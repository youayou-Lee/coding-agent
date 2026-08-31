"""agno_compat: OpenAICompatChat — OpenAI-compatible chat adapter for Agno.

Extracted from the learning lab (course/a5_framework_lab/agno_agent.py) so
this project is self-contained. It folds in every compatibility fix found
the hard way:

- role_map: Agno maps system->developer for the new OpenAI API; DeepSeek/GLM
  only accept classic roles.
- trust_env=False: isolates the environment proxy (socks://127.0.0.1:7890
  breaks httpx's default trust_env=True).
- api_key/base_url/model: defaults read from coding_agent.config (.env).
- __deepcopy__: httpx.Client cannot be deep-copied (Agno's MemoryManager
  deepcopies the model and the client silently vanishes -> trust_env=True
  -> proxy error). Rebuild a clean client and share the logger.
- logger: optional RunLogger; wraps response() so every LLM round-trip is
  logged (Agno's non-streaming path runs the tool loop inside response()).
"""

import httpx
from agno.models.base import Model

from coding_agent import config as _cfg
from agno.models.openai import OpenAIChat


class OpenAICompatChat(OpenAIChat):
    default_role_map = {
        "system": "system",
        "user": "user",
        "assistant": "assistant",
        "tool": "tool",
        "model": "assistant",
    }

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("http_client", httpx.Client(trust_env=False))
        kwargs.setdefault("api_key", _cfg.LLM_API_KEY)
        kwargs.setdefault("base_url", _cfg.LLM_BASE_URL)
        kwargs.setdefault("id", _cfg.LLM_MODEL)
        thinking = _cfg.thinking_extra_body()
        if thinking:
            kwargs.setdefault("extra_body", thinking)
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


DeepSeekChat = OpenAICompatChat  # backward-compat alias


class ProviderChat(OpenAICompatChat):
    """多 provider 故障切换包装器。

    设计：继承 OpenAICompatChat（复用全部协议实现），只覆写两个单点：
      - get_client(): 返回链当前 provider 的 OpenAI client
      - get_request_params(): 同步当前 provider 的 model / thinking 参数

    Agno 的 invoke() 每次调用 get_client() 发请求 → 链的游标决定实际谁接单。
    故障切换 = chain.execute() 内部移动游标，下次请求自动用新 provider。

    覆写 invoke()（非流式路径）把调用包进 ProviderChain：
      - 瞬时故障（500/429/超时）→ 同 provider 退避重试
      - 持续/配置故障 → 切下一个 provider（本次 run 内记住）
      - 全挂 → 原样抛最后一个异常
    """

    def __init__(self, chain, *, logger=None):
        head = chain.current
        super().__init__(
            id=head.model,
            base_url=head.base_url,
            api_key=head.api_key,
            extra_body=(
                {"thinking": {"type": "enabled", "thinking_budget": head.thinking}}
                if head.thinking
                else None
            ),
        )
        self._chain = chain
        self._logger = logger
        # 同 provider 复用已建 client（Agno get_client 本身有缓存，
        # 但换 provider 后 self.client 缓存的是旧家的，必须失效）
        self._client_cache: dict[str, OpenAICompatChat] = {}

    def _active_client(self):
        cfg = self._chain.current
        if cfg.name not in self._client_cache:
            kwargs = {
                "id": cfg.model,
                "base_url": cfg.base_url,
                "api_key": cfg.api_key,
            }
            if cfg.thinking:
                kwargs["extra_body"] = {"thinking": {"type": "enabled", "thinking_budget": cfg.thinking}}
            self._client_cache[cfg.name] = OpenAICompatChat(**kwargs)
        return self._client_cache[cfg.name]

    def get_client(self):
        return self._active_client().get_client()

    def invoke(self, messages, assistant_message, **kwargs):
        cfg = self._chain.current

        def attempt(active_cfg):
            if self._logger is not None:
                self._logger.event("provider_attempt", provider=active_cfg.name, model=active_cfg.model)
            # Agno invoke 里 model=self.id 是闭包读取【执行时】的 self.id，
            # 临时指向当前 provider 的 model，请求结束后还原（避免残留状态）
            original_id = self.id
            try:
                self.id = active_cfg.model
                return super(ProviderChat, self).invoke(messages, assistant_message, **kwargs)
            finally:
                self.id = original_id

        return self._chain.execute(attempt, on_event=(
            (lambda e, d: self._logger.event(e, detail=d)) if self._logger is not None else None
        ))

    def __deepcopy__(self, memo):
        # 链状态（游标）与 client 缓存不可深拷贝：共享引用即可
        # （与 OpenAICompatChat.__deepcopy__ 同思路）
        import copy as _copy

        new = self.__class__.__new__(self.__class__)
        memo[id(self)] = new
        for key, value in self.__dict__.items():
            if key in ("_chain", "_client_cache", "_logger"):
                setattr(new, key, value)  # 共享
                continue
            try:
                setattr(new, key, _copy.deepcopy(value, memo))
            except Exception:
                setattr(new, key, value)
        return new
