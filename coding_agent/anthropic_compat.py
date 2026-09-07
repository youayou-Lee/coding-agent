"""agno_compat 中的 Anthropic 路径（v0.6 #19 附加）：GLM Anthropic 兼容端点适配。

背景（2026-09-07）：新 GLM key 的资源包绑定在 Anthropic 兼容端点
（/api/anthropic），OpenAI 兼容端点 429 余额不足。agno 自带 Claude 模型类，
本适配器补齐项目标准行为：logger 挂接 + plan_provider 钩子 + 代理隔离。

注意：anthropic SDK 基于 httpx2，socks 代理变量（ALL_PROXY 等）会破坏它——
tb_run.sh 与 cli 入口需 unset 代理变量（与 LiteLLM 同样处理）。
"""

import os

from agno.models.anthropic import Claude

from coding_agent.logging_util import RunLogger


class AnthropicCompatChat(Claude):
    """Claude 协议 + 项目标准行为（logger/plan_provider/代理隔离）。"""

    def __init__(self, *args, **kwargs):
        kwargs.pop("plan_provider", None)  # 兼容签名（Claude 基类不接受未知参数）
        kwargs.pop("logger", None)  # Claude 基类不接受 logger，改为属性挂接
        self._logger = None  # 在 super().__init__ 后挂
        super().__init__(*args, **kwargs)
        self._logger = None

    def set_logger(self, logger: RunLogger | None) -> None:
        self._logger = logger

    def set_plan_provider(self, provider) -> None:
        self._plan_provider = provider

    def response(self, messages, **kwargs):
        # 计划注入（与 OpenAI 路径同构：副本追加，不污染历史）
        if getattr(self, "_plan_provider", None) is not None:
            from coding_agent.plan_injection import inject_plan

            messages = inject_plan(list(messages), self._plan_provider())
        logger = getattr(self, "_logger", None)
        if logger is not None:
            logger.llm_call(messages, "(pending)")
        resp = super().response(messages, **kwargs)
        if logger is not None:
            logger.llm_response(str(getattr(resp, "content", ""))[:500])
        return resp

    def __deepcopy__(self, memo):
        import copy as _copy

        new = self.__class__.__new__(self.__class__)
        memo[id(self)] = new
        for key, value in self.__dict__.items():
            if key in ("_client", "_async_client", "client", "async_client"):
                continue
            if key == "_logger":
                setattr(new, key, value)
                continue
            try:
                setattr(new, key, _copy.deepcopy(value, memo))
            except Exception:
                setattr(new, key, value)
        return new
