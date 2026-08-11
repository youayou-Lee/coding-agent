"""agno_compat: DeepSeekChat — the DeepSeek adapter for Agno.

Extracted from the learning lab (course/a5_framework_lab/agno_agent.py) so
this project is self-contained. It folds in every compatibility fix found
the hard way:

- role_map: Agno maps system->developer for the new OpenAI API; DeepSeek
  only accepts classic roles.
- trust_env=False: isolates the environment proxy (socks://127.0.0.1:7890
  breaks httpx's default trust_env=True).
- api_key: defaults to DEEPSEEK_API_KEY (Agno/OpenAI only reads
  OPENAI_API_KEY otherwise).
- __deepcopy__: httpx.Client cannot be deep-copied (Agno's MemoryManager
  deepcopies the model and the client silently vanishes -> trust_env=True
  -> proxy error). Rebuild a clean client and share the logger.
- logger: optional RunLogger; wraps response() so every LLM round-trip is
  logged (Agno's non-streaming path runs the tool loop inside response()).
"""

import os

import httpx
from agno.models.openai import OpenAIChat


class DeepSeekChat(OpenAIChat):
    default_role_map = {
        "system": "system",
        "user": "user",
        "assistant": "assistant",
        "tool": "tool",
        "model": "assistant",
    }

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("http_client", httpx.Client(trust_env=False))
        kwargs.setdefault("api_key", os.environ.get("DEEPSEEK_API_KEY"))
        self._logger = kwargs.pop("logger", None)
        super().__init__(*args, **kwargs)

    def response(self, messages, **kwargs):
        logger = getattr(self, "_logger", None)
        if logger is not None:
            logger.llm_call(messages, "(pending)")
        resp = super().response(messages, **kwargs)
        if logger is not None:
            content = getattr(resp, "content", None) or ""
            logger.llm_response(str(content)[:500])
        return resp

    def __deepcopy__(self, memo):
        import copy as _copy

        new = self.__class__.__new__(self.__class__)
        memo[id(self)] = new
        for key, value in self.__dict__.items():
            if key == "http_client":
                continue
            if key == "_logger":
                setattr(new, key, value)  # share the logger, don't duplicate it
                continue
            try:
                setattr(new, key, _copy.deepcopy(value, memo))
            except Exception:
                setattr(new, key, value)
        new.http_client = httpx.Client(trust_env=False)
        return new
