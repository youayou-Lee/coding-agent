"""provider.py — LLM Provider 部件：多 provider 链、重试、故障切换。

设计（v0.4，由真实故障驱动——GLM 偶发 500 无重试导致整个 run 终止）：

  Agent 请求 → [ProviderChain]
                 ① 瞬时故障（429/5xx/超时/连接错误）→ 指数退避重试（1s→2s）
                 ② 持续故障（重试耗尽）或配置错误（401/403）→ 切下一个 provider
                 ③ 链耗尽 → 原样抛出最后一个异常（不静默、不伪造）

provider 列表来自 .env：
  LLM_PROVIDERS=glm,deepseek
  GLM_API_KEY=***  GLM_BASE_URL=...  GLM_MODEL=...  GLM_THINKING=low
  DEEPSEEK_API_KEY=***  DEEPSEEK_BASE_URL=...  DEEPSEEK_MODEL=...
未设 LLM_PROVIDERS 时，回退到单 provider（LLM_API_KEY/LLM_BASE_URL/LLM_MODEL）。

"生病记忆"只在本次 run 内：切走后本 run 后续请求直接用新 provider，
下次 run 从头开始（状态复杂度换可靠性，先简单后聪明）。
"""

import os
import time
from dataclasses import dataclass

import openai

from coding_agent import config as _config  # 确保 .env 已加载（import 副作用）

# 这些状态码重试有意义；401/403/404 是配置或权限错误，重试纯浪费时间
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    model: str
    thinking: str | None = None  # GLM thinking 档位；DeepSeek 等无此概念


def load_providers() -> list[ProviderConfig]:
    """从环境变量构建 provider 链。保证至少返回一个。"""
    raw = os.environ.get("LLM_PROVIDERS", "").strip()
    providers: list[ProviderConfig] = []
    if raw:
        for name in [p.strip().lower() for p in raw.split(",") if p.strip()]:
            key = os.environ.get(f"{name.upper()}_API_KEY", "")
            base = os.environ.get(f"{name.upper()}_BASE_URL", "")
            model = os.environ.get(f"{name.upper()}_MODEL", "")
            if not (key and base and model):
                continue  # 配置不全的 provider 跳过，不进链
            providers.append(
                ProviderConfig(
                    name=name,
                    base_url=base,
                    api_key=key,
                    model=model,
                    thinking=os.environ.get(f"{name.upper()}_THINKING") or None,
                )
            )
    if not providers:  # 回退：单 provider（旧 .env 格式）
        providers.append(
            ProviderConfig(
                name="default",
                base_url=os.environ.get("LLM_BASE_URL", ""),
                api_key=os.environ.get("LLM_API_KEY", ""),
                model=os.environ.get("LLM_MODEL", ""),
                thinking=os.environ.get("LLM_THINKING") or None,
            )
        )
    return providers


def is_transient(exc: BaseException) -> bool:
    """瞬时故障 → 值得对同一个 provider 重试。"""
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code in RETRYABLE_STATUS
    return False


class ProviderChain:
    """run 内的 provider 游标 + 重试参数。可独立单测。"""

    def __init__(
        self,
        providers: list[ProviderConfig],
        *,
        max_retries: int = 2,
        backoff_base: float = 1.0,
        sleeper=time.sleep,
    ) -> None:
        if not providers:
            raise ValueError("ProviderChain 需要至少一个 provider")
        self.providers = providers
        self.index = 0
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._sleep = sleeper  # 注入以便测试不等真实时间

    @property
    def current(self) -> ProviderConfig:
        return self.providers[self.index]

    @property
    def exhausted(self) -> bool:
        return self.index >= len(self.providers)

    def next_provider(self) -> bool:
        """切到下一个 provider。返回 False 表示链已耗尽。"""
        if self.index + 1 >= len(self.providers):
            self.index = len(self.providers)  # 标记耗尽
            return False
        self.index += 1
        return True

    def retry_delays(self) -> list[float]:
        """重试等待序列：1s → 2s → 4s ...（max_retries 个）。"""
        return [self.backoff_base * (2**i) for i in range(self.max_retries)]

    def execute(self, attempt, *, on_event=None):
        """带重试+切换的执行入口。

        attempt: 无参函数，执行一次真实的 API 调用，返回结果或抛异常。
        on_event: 可选回调 (event: str, detail: str)，用于接 RunLogger。

        语义：
          - 瞬时异常 → 退避后重试，重试耗尽 → 切下一个 provider
          - 非瞬时异常（401/403/参数错）→ 立即切下一个 provider
          - 链耗尽 → 抛出最后一个异常
        """
        last_exc: BaseException | None = None
        while not self.exhausted:
            retries_left = list(self.retry_delays())
            while True:
                try:
                    return attempt(self.current)
                except Exception as exc:
                    last_exc = exc
                    if is_transient(exc) and retries_left:
                        delay = retries_left.pop(0)
                        if on_event:
                            on_event(
                                "provider_retry",
                                f"provider={self.current.name} 瞬时故障，{delay}s 后重试: {exc}",
                            )
                        self._sleep(delay)
                        continue  # 同一 provider 再试
                    # 非瞬时，或重试耗尽 → 换 provider
                    if on_event:
                        on_event(
                            "provider_failover",
                            f"provider={self.current.name} 放弃: {exc}",
                        )
                    break
            if not self.next_provider():
                break
        assert last_exc is not None
        raise last_exc
