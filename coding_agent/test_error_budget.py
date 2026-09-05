"""test_error_budget.py — ErrorBudget 的 L1 单测 + L2 集成场景（Issue #5 验收）。

L1：计数/阈值/清零/升级判定/参数校验
L2：send_command 路由中升级跳过重试、成功销账重置
"""

import tempfile
import unittest
from pathlib import Path

from coding_agent.agent import make_coding_agent
from coding_agent.error_budget import ErrorBudget
from coding_agent.errors import ErrorKind, ToolErrorType
from coding_agent.logging_util import RunLogger
from coding_agent.terminal import ToolExecutionError


def _kind(rule="msg:lock"):
    return ErrorKind(ToolErrorType.TRANSIENT, rule, "")


class ErrorBudgetL1Test(unittest.TestCase):
    def test_counts_increment(self):
        b = ErrorBudget()
        self.assertEqual(b.record(_kind()), 1)
        self.assertEqual(b.record(_kind()), 2)
        self.assertEqual(b.count(_kind()), 2)

    def test_threshold_upgrade(self):
        b = ErrorBudget(threshold=3)
        for _ in range(2):
            b.record(_kind())
        self.assertFalse(b.exhausted(_kind()))  # 未到阈值
        b.record(_kind())
        self.assertTrue(b.exhausted(_kind()))   # 达到阈值

    def test_clear_resets(self):
        b = ErrorBudget(threshold=2)
        b.record(_kind())
        b.record(_kind())
        self.assertTrue(b.exhausted(_kind()))
        b.clear(_kind())  # 成功销账
        self.assertFalse(b.exhausted(_kind()))
        self.assertEqual(b.count(_kind()), 0)

    def test_different_rules_independent(self):
        b = ErrorBudget(threshold=2)
        b.record(_kind("msg:lock"))
        b.record(_kind("msg:timeout"))
        self.assertFalse(b.exhausted(_kind("msg:lock")))
        self.assertEqual(b.count(_kind("msg:timeout")), 1)

    def test_different_types_independent(self):
        # 同 rule 不同 error_type 分开计数（transient vs permanent 是两本账）
        b = ErrorBudget(threshold=1)
        t = ErrorKind(ToolErrorType.TRANSIENT, "msg:not-found", "")
        pm = ErrorKind(ToolErrorType.PERMANENT, "msg:not-found", "")
        b.record(t)
        self.assertTrue(b.exhausted(t))
        self.assertFalse(b.exhausted(pm))

    def test_threshold_validation(self):
        with self.assertRaises(ValueError):
            ErrorBudget(threshold=0)


class FakeContainerLikeBackend:
    """按脚本吐结果；耗尽后重复最后一项（保持测试意图的状态）。"""

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0
        self._last = results[0] if results else None

    def run(self, command: str, *, timeout: int = 60) -> str:
        self.calls += 1
        if self.results:
            item = self.results.pop(0)
        elif isinstance(self._last, Exception):
            raise self._last
        else:
            item = self._last
        if isinstance(item, Exception):
            self._last = item
            raise item
        self._last = item
        return item


def _get_send_command(backend, logger, **kw):
    agent = make_coding_agent(backend, logger, max_steps=3, **kw)
    for t in agent.tools:
        if t.name == "send_command":
            return t.entrypoint if hasattr(t, "entrypoint") else t.function
    raise AssertionError("send_command not found")


class ErrorBudgetL2Test(unittest.TestCase):
    """验收：升级后跳过自动重试直接回模型；成功销账重置。"""

    def test_budget_exhausted_skips_retry(self):
        # 同一 transient 错误跨 3 次调用：第 1/2 次照常重试，第 3 次升级跳过
        backend = FakeContainerLikeBackend([
            ToolExecutionError("database is locked", exit_code=1, stderr="locked"),
        ])
        with tempfile.TemporaryDirectory() as tmp:
            logger = RunLogger(Path(tmp))
            send = _get_send_command(backend, logger, budget_threshold=3)
            send("q1")  # 第 1 次：重试 2 次（1+2=3 calls）
            calls_after_first = backend.calls
            send("q2")  # 第 2 次：同样照常重试
            self.assertEqual(backend.calls - calls_after_first, 3)
            calls_after_second = backend.calls
            result = send("q3")  # 第 3 次：预算耗尽，跳过重试
            self.assertEqual(backend.calls - calls_after_second, 1)  # 不再重试！
            self.assertIn("预算耗尽", result)
            events = logger.replay()
            self.assertIn("error_budget_exhausted", [e["event"] for e in events])
            marker = [e for e in events if e["event"] == "error_budget_exhausted"][0]
            self.assertEqual(marker["count"], 3)
            self.assertEqual(marker["threshold"], 3)

    def test_success_clears_budget(self):
        # 2 次失败调用（各消耗 1+2 重试=3 项）→ 成功 → 再 2 次失败调用才升级（销账生效）
        err = ToolExecutionError("database is locked", exit_code=1, stderr="locked")
        backend = FakeContainerLikeBackend(
            [err] * 6 + ["recovered"] + [err] * 7  # q1:3 + q2:3 + q3:1 + q4:3 + q5:3 + q6:1
        )
        with tempfile.TemporaryDirectory() as tmp:
            logger = RunLogger(Path(tmp))
            send = _get_send_command(backend, logger, budget_threshold=3)
            send("q1")  # 失败重试耗尽
            send("q2")  # 失败重试耗尽（计数=2，未升级）
            r3 = send("q3")  # 成功 → 销账
            self.assertEqual(r3, "recovered")
            calls_before = backend.calls
            send("q4")  # 重试照常（计数从 0 重新开始）
            self.assertEqual(backend.calls - calls_before, 3)
            send("q5")
            self.assertEqual(backend.calls - calls_before - 3, 3)
            r6 = send("q6")  # 第 3 次失败 → 升级
            self.assertIn("预算耗尽", r6)


if __name__ == "__main__":
    unittest.main()
