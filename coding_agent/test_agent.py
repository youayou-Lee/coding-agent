import tempfile
import unittest
from pathlib import Path

from coding_agent.logging_util import RunLogger
from coding_agent.terminal import LocalBackend


class LocalBackendTest(unittest.TestCase):
    def test_runs_command_in_workdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            backend = LocalBackend(Path(tmp))
            out = backend.run("pwd && echo hello")
            self.assertIn("hello", out)

    def test_captures_error_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            backend = LocalBackend(Path(tmp))
            out = backend.run("ls /definitely_not_exist_7f3a")
            self.assertTrue("definitely_not_exist" in out or "没有那个文件" in out)


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
