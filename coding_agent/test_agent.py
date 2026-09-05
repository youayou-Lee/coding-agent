import tempfile
import unittest
from pathlib import Path

from coding_agent.logging_util import RunLogger
from coding_agent.terminal import LocalBackend, ToolExecutionError


class LocalBackendTest(unittest.TestCase):
    def test_runs_command_in_workdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            backend = LocalBackend(Path(tmp))
            out = backend.run("pwd && echo hello")
            self.assertIn("hello", out)

    def test_nonzero_exit_raises_tool_execution_error(self) -> None:
        # v0.5 #4 新契约：退出码非零不再煮字符串，而是抛结构化异常
        with tempfile.TemporaryDirectory() as tmp:
            backend = LocalBackend(Path(tmp))
            with self.assertRaises(ToolExecutionError) as ctx:
                backend.run("ls /definitely_not_exist_7f3a")
            self.assertIsNotNone(ctx.exception.exit_code)
            self.assertFalse(ctx.exception.exit_code == 0)
            self.assertTrue(
                "definitely_not_exist" in ctx.exception.stderr
                or "没有那个文件" in ctx.exception.stderr
            )


class RunLoggerTest(unittest.TestCase):
    def test_events_append_and_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger = RunLogger(Path(tmp))
            logger.tool_call("send_command", {"command": "ls"}, "out", ok=True, step=1)
            logger.llm_call([{"role": "user", "content": "hi"}], "resp", step=1)
            logger.final("finished")
            rows = logger.replay()
            self.assertEqual([r["event"] for r in rows], ["tool_call", "llm_call", "final"])
            self.assertIn("tool_call", logger.summary())

    def test_replay_file_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logger = RunLogger(Path(tmp))
            logger.event("task_start")
            content = (Path(tmp) / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn("task_start", content)


if __name__ == "__main__":
    unittest.main()
