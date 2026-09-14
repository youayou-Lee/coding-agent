"""provider.py — LLM Provider 部件：多 provider 链、重试、故障切换。

设计（v0.4，由真实故障驱动——GLM 偶发 500 无重试导致整个 run 终止）：

  Agent 请求 → [ProviderChain]
                 ① 瞬时故障（429/5xx/超时/连接错误）→ 指数退避重试（1s→2s）
                 ② 持续故障（重试耗尽）或配置错误（401/403）→ 切下一个 provider
                 ③ 链耗尽 → 原样抛出最后一个异常（不静默、不伪造）

provider 声明在仓库根 providers.yaml（git 可审，密钥经 api_key_env 指向
.env/shell 环境变量，key 本体不进 git）。校验显式失败原则（#25）：
  - yaml 缺失 / 启用 provider 缺 key / 启用链内 protocol 混用 → 启动即报错
  - enabled: false → 不进链（显式禁用，保留声明）
  - 旧 LLM_PROVIDERS 环境变量已废弃（#25 分叉 3 = 一刀切）

"生病记忆"只在本次 run 内：切走后本 run 后续请求直接用新 provider，
下次 run 从头开始（状态复杂度换可靠性，先简单后聪明）。
"""

import os
import time
from dataclasses import dataclass
from pathlib import Path

import openai
import yaml

from coding_agent import config as _config  # 确保 .env 已加载（import 副作用）
from coding_agent.config import PROJECT_ROOT

# 这些状态码重试有意义；401/403/404 是配置或权限错误，重试纯浪费时间
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}

VALID_PROTOCOLS = {"openai", "anthropic"}
VALID_FAILOVER = {"alert", "strict"}


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    model: str
    thinking: str | None = None  # GLM thinking 档位；DeepSeek 等无此概念
    protocol: str = "openai"  # anthropic = Anthropic 兼容端点（协议随 provider，#25）
    on_failover: str = "alert"  # strict = 重试耗尽即抛错；alert = 切换并留痕


def load_providers() -> list[ProviderConfig]:
    """从 providers.yaml 构建启用中的 provider 链。校验失败显式报错，不静默降级。"""
    path = Path(os.environ.get("PROVIDERS_CONFIG") or PROJECT_ROOT / "providers.yaml")
    if not path.is_file():
        raise FileNotFoundError(
            f"provider 配置不存在: {path}（用 PROVIDERS_CONFIG 指定，或在仓库根放 providers.yaml）"
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("providers"), list):
        raise ValueError(f"{path}: 结构须为 dict 且含 providers 列表（顶层当前: {type(data).__name__}）")
    raw_list = data["providers"]
    if not raw_list:
        raise ValueError(f"{path}: providers 列表为空——至少声明一个 provider")

    enabled: list[ProviderConfig] = []
    seen_names: set[str] = set()
    for i, raw in enumerate(raw_list):
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: providers[{i}] 不是映射（got {type(raw).__name__}）")
        name = (raw.get("name") or "").strip()
        if not name:
            raise ValueError(f"{path}: providers[{i}] 缺 name")
        if name in seen_names:
            # 重名会让 ProviderChat 的 client 缓存（按 name 键控）在 failover 时
            # 复用前一个 provider 的 client——静默错配，必须启动即拦
            raise ValueError(f"{path}: provider name 重复: {name}")
        seen_names.add(name)
        protocol = (raw.get("protocol") or "openai").strip().lower()
        if protocol not in VALID_PROTOCOLS:
            raise ValueError(
                f"{path}: provider {name} protocol={protocol!r}，须为 {sorted(VALID_PROTOCOLS)}"
            )
        on_failover = (raw.get("on_failover") or "alert").strip().lower()
        if on_failover not in VALID_FAILOVER:
            raise ValueError(
                f"{path}: provider {name} on_failover={on_failover!r}，须为 {sorted(VALID_FAILOVER)}"
            )
        enabled_flag = raw.get("enabled", True)
        if not isinstance(enabled_flag, bool):
            # enabled: "false"（带引号）是 truthy——会被当启用，报错位置还让人困惑
            raise ValueError(f"{path}: provider {name} enabled 须为布尔（got {enabled_flag!r}）")
        if not enabled_flag:
            continue  # 显式禁用：不进链，但声明保留（实验口径记录）
        key_env = (raw.get("api_key_env") or "").strip()
        if not key_env:
            raise ValueError(f"{path}: provider {name} 缺 api_key_env（key 来源必须显式声明）")
        api_key = os.environ.get(key_env, "")
        if not api_key:
            raise ValueError(
                f"{path}: provider {name} 声明 api_key_env={key_env} 但该变量未设置"
                f"（.env 或 shell 里补上；缺 key 直接报错，不静默跳过）"
            )
        base = (raw.get("base_url") or "").strip()
        model = (raw.get("model") or "").strip()
        if not (base and model):
            raise ValueError(f"{path}: provider {name} 缺 base_url/model")
        enabled.append(
            ProviderConfig(
                name=name,
                base_url=base,
                api_key=api_key,
                model=model,
                thinking=raw.get("thinking") or None,
                protocol=protocol,
                on_failover=on_failover,
            )
        )

    if not enabled:
        raise ValueError(f"{path}: 没有任何 enabled: true 的 provider")
    protocols = {p.protocol for p in enabled}
    if len(protocols) > 1:
        raise ValueError(
            f"{path}: 启用链内协议混用 {sorted(protocols)}——跨协议 failover 不支持"
            f"（#25：协议异构不通），enabled 请只保留同一协议的 provider"
        )
    return enabled


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
          - 瞬时异常 → 退避后重试，重试耗尽 → 按当前 provider 的 on_failover 策略
          - 非瞬时异常（401/403/参数错）→ 同样进入 failover 策略判定
          - alert = 留 provider_failover 事件后切下一个 provider
          - strict = 不切链，立即抛出异常（实验模式：不是指定模型就别跑，#25）
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
                    # 非瞬时，或重试耗尽 → failover 策略判定
                    if self.current.on_failover == "strict":
                        # 实验模式：宁可终止也不静默换模型（污染数据比中断代价高）
                        if on_event:
                            on_event(
                                "provider_strict_stop",
                                f"provider={self.current.name} on_failover=strict，终止不切链: {exc}",
                            )
                        raise last_exc
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
