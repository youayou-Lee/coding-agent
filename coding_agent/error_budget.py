"""errors.py 追加：ErrorBudget —— 跨调用的错误预算台账（v0.5 Issue #5）。

问题：#4 的重试是单次调用内的（RETRY_MAX=2，每次 send_command 独立计数）。
跨调用无记忆：同一错误在一次任务的 20 步里反复撞 → 每次都白烧重试步数。

设计（事实与决策分离的延续）：
- 台账只回答"这个错误第几次出现了"（事实层，纯计数）
- 升级决策由调用方做：exhausted(rule) 为 True → 跳过重试直接回模型（决策层）
- 成功销账：同一 rule 的命令成功执行 = 环境恢复信号 → 计数清零
"""

from collections import defaultdict

from coding_agent.errors import ErrorKind, ToolErrorType


class ErrorBudget:
    """按 (error_type, rule) 计次的错误预算。可独立单测，不碰 LLM/shell。"""

    def __init__(self, *, threshold: int = 3) -> None:
        if threshold < 1:
            raise ValueError("threshold 必须 >= 1")
        self.threshold = threshold
        self._counts: dict[tuple[str, str], int] = defaultdict(int)

    @staticmethod
    def _key(kind: ErrorKind) -> tuple[str, str]:
        return (kind.type.value, kind.rule)

    def record(self, kind: ErrorKind) -> int:
        """记录一次错误发生，返回当前计数。"""
        self._counts[self._key(kind)] += 1
        return self._counts[self._key(kind)]

    def exhausted(self, kind: ErrorKind) -> bool:
        """该错误是否已超预算（达到阈值即升级为 permanent 处理）。"""
        return self._counts[self._key(kind)] >= self.threshold

    def clear(self, kind: ErrorKind) -> None:
        """销账：同 rule 命令成功执行 = 环境恢复信号。"""
        self._counts.pop(self._key(kind), None)

    def clear_all_transient(self) -> None:
        """全量销账：任意命令成功执行 = 环境整体恢复的强信号。

        只清 transient（permanent 与成功无关，不应被误清）。
        """
        for key in [k for k in self._counts if k[0] == ToolErrorType.TRANSIENT.value]:
            self._counts.pop(key, None)

    def count(self, kind: ErrorKind) -> int:
        return self._counts[self._key(kind)]
