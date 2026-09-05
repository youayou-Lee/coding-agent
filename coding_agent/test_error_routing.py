"""test_error_routing.py — #4 策略路由的 L2 组件测试（fake backend，零 API 成本）。

验证 send_command 接住 ToolExecutionError 后按分类路由：
  transient → 工具内部自动重试（fake backend 第 N 次才成功 / 永远失败）
  permanent → 立即结构化换路建议（不重试）
  semantic  → 原样透传

用 FakeBackend 控制每次 backend.run 的行为序列，不碰真 shell / LLM。
"""

import tempfile
import unittest
from pathlib import Path

from coding_agent.agent import make_coding_agent
from coding_agent.logging_util import RunLogger
from coding_agent.terminal import ToolExecutionError


class FakeBackend:
    """按脚本吐结果：results 是队列，每次 run 弹出一个；支持 raise ToolExecutionError。"""

    def __init__(self, results) -> None:
        self.results = list(results)
        self.calls = 0

    def run(self, command: str, *, timeout: int = 60) -> str:
        self.calls += 1
        item = self.results.pop(0) if self.results else self.results[-1] if self.results else "(empty)"
        if isinstance(item, Exception):
            raise item
        return item


def _get_send_command(backend, logger):
    """从 make_coding_agent 生成的 Agent 里取出 send_command 工具函数。"""
    agent = make_coding_agent(backend, logger, max_steps=3)
    for t in agent.tools:
        if t.name == "send_command":
            return t.entrypoint if hasattr(t, "entrypoint") else t.function
    raise AssertionError("send_command 工具未找到")


class TransientRetryTest(unittest.TestCase):
    def test_transient_succeeds_on_retry(self):
        # fake: 第一次抛 database locked（transient），第二次成功
        backend = FakeBackend([
            ToolExecutionError("database is locked", exit_code=1, stderr="locked"),
            "SELECT count=42",
        ])
        with tempfile.TemporaryDirectory() as tmp:
            logger = RunLogger(Path(tmp))
            send = _get_send_command(backend, logger)
            result = send("sqlite3 query")
        self.assertIn("42", result)
        self.assertEqual(backend.calls, 2)  # 重试了一次
        events = [e["event"] for e in logger.replay()]
        self.assertIn("tool_retry", events)
        self.assertIn("tool_retry_ok", events)
        # 最终 tool_call 应标记 ok=True
        final = [e for e in logger.replay() if e["event"] == "tool_call"][-1]
        self.assertTrue(final["ok"])

    def test_transient_exhausts_retries_then_reports(self):
        # fake: 永远 database locked（transient）→ 重试耗尽 → 结构化失败
        backend = FakeBackend([
            ToolExecutionError("database is locked", exit_code=1, stderr="locked")
        ] * 5)
        with tempfile.TemporaryDirectory() as tmp:
            logger = RunLogger(Path(tmp))
            send = _get_send_command(backend, logger)
            result = send("sqlite3 query")
        # RETRY_MAX=2 → 总调用 = 第一次 + 2 次重试 = 3
        self.assertEqual(backend.calls, 3)
        self.assertIn("[命令失败", result)
        events = [e["event"] for e in logger.replay()]
        self.assertIn("tool_retry_exhausted", events)

    def test_transient_exhaust_keeps_original_rule(self):
        # C1 回归：重试耗尽后 trace 的 rule 应保持外层 transient 判定，不降级为 fallback
        backend = FakeBackend([
            ToolExecutionError("database is locked", exit_code=1, stderr="locked")
        ] * 5)
        with tempfile.TemporaryDirectory() as tmp:
            logger = RunLogger(Path(tmp))
            send = _get_send_command(backend, logger)
            send("sqlite3 query")
        exhausted = [e for e in logger.replay() if e["event"] == "tool_retry_exhausted"][0]
        self.assertEqual(exhausted["rule"], "msg:lock")  # 保持 transient 判定
        self.assertNotEqual(exhausted["rule"], "fallback:unknown")


class PermanentTest(unittest.TestCase):
    def test_permanent_returns_changepath_without_retry(self):
        backend = FakeBackend([
            ToolExecutionError("No such file", exit_code=2, stderr="cat: x: No such file")
        ])
        with tempfile.TemporaryDirectory() as tmp:
            logger = RunLogger(Path(tmp))
            send = _get_send_command(backend, logger)
            result = send("cat x.yaml")
        self.assertEqual(backend.calls, 1)  # permanent 不重试
        self.assertIn("[命令失败，永久性错误]", result)
        self.assertIn("重试无效", result)  # 换路建议
        final = [e for e in logger.replay() if e["event"] == "tool_call"][-1]
        self.assertFalse(final["ok"])
        self.assertEqual(final["error_kind"], "permanent")


class SemanticTest(unittest.TestCase):
    def test_semantic_passthrough(self):
        # 未知错误 → semantic → 透传交模型判断（方案 A 兜底）
        backend = FakeBackend([
            ToolExecutionError("md5 mismatch: expected aaa got bbb", exit_code=3, stderr="")
        ])
        with tempfile.TemporaryDirectory() as tmp:
            logger = RunLogger(Path(tmp))
            send = _get_send_command(backend, logger)
            result = send("run something")
        self.assertEqual(backend.calls, 1)  # 不重试
        self.assertIn("md5 mismatch", result)
        final = [e for e in logger.replay() if e["event"] == "tool_call"][-1]
        self.assertFalse(final["ok"])
        self.assertEqual(final["error_kind"], "semantic")

    def test_semantic_passthrough_includes_stderr(self):
        # I2 回归：semantic 透传必须含 stderr，否则模型信息量不足无法判断
        backend = FakeBackend([
            ToolExecutionError("命令退出码 3", exit_code=3, stderr="assertion failed: 1 != 2")
        ])
        with tempfile.TemporaryDirectory() as tmp:
            logger = RunLogger(Path(tmp))
            send = _get_send_command(backend, logger)
            result = send("pytest tests/")
        self.assertIn("assertion failed", result)  # stderr 内容透传给了模型


if __name__ == "__main__":
    unittest.main()
