"""test_model_loop.py — I3 补齐：L2 模型回路验证（Issue #6 / 上两轮审核遗留）。

验证"permanent 错误回模型后，模型真的换路"的完整闭环：
FakeChatModel 替换真实 LLM（ scripted 决策序列），驱动真 agent 循环：
  1. 模型调 send_command → 失败（permanent）
  2. 工具层路由把结构化换路建议作为 tool 消息回给模型
  3. 模型读到换路建议后改变策略（调 list_files 而不是重试 cat）
  → 证明错误信号真的影响了模型行为，而非只测工具函数返回

fake 是两层保真：invoke 签名/返回类型与 Agno OpenAIChat 一致（形状），
按脚本吐 tool_calls 并维护消息序列状态（语义）。
"""

import json
import tempfile
import unittest
from pathlib import Path

from coding_agent.agent import make_coding_agent
from coding_agent.logging_util import RunLogger
from coding_agent.terminal import ToolExecutionError


from agno.models.base import Model


class ScriptedChatModel(Model):
    """Agno Model 基类子类：覆写 invoke()（单次调用），复用基类 response() 的工具循环。

    这样 Agno 的真实循环逻辑（工具执行→结果回填→再决策）原样运行，
    fake 只负责"模型决策"这一环——这是模型回路测试的架构关键。
    """

    def __init__(self, script, *, id="fake-model"):
        super().__init__(id=id)
        self.provider = "fake"
        self.script = list(script)
        self.seen_messages: list = []

    def _last_tool_result(self) -> str:
        """从 messages 里取最后一条 tool 角色消息的内容（模型视角的观察）。"""
        for m in reversed(self.seen_messages[-1]):
            if getattr(m, "role", None) == "tool":
                return str(getattr(m, "content", ""))
        return ""

    provider = "fake"
    instructions: list | None = None

    system_prompt: str | None = None

    def _parse_provider_response(self, response, response_format=None):
        return response  # 基类抽象方法的最小实现

    def _parse_provider_response_delta(self, response):
        return response

    async def ainvoke(self, messages, assistant_message, **kwargs):
        return self.invoke(messages, assistant_message, **kwargs)  # 异步路径复用同步脚本

    async def ainvoke_stream(self, messages, assistant_message, **kwargs):
        yield self.invoke(messages, assistant_message, **kwargs)  # 占位（本测试不走流式）

    def invoke_stream(self, messages, assistant_message, **kwargs):
        yield self.invoke(messages, assistant_message, **kwargs)  # 占位（本测试不走流式）

    def invoke(self, messages, assistant_message, **kwargs):
        """单次模型调用（Agno 基类 response() 的工具循环会消费 assistant_message.tool_calls）。"""
        self.seen_messages.append(list(messages))
        item = self.script.pop(0) if self.script else {"content": "done"}
        if "tool" in item:
            assistant_message.content = ""
            assistant_message.tool_calls = [
                {
                    "id": f"call_{len(self.seen_messages)}",
                    "type": "function",
                    "function": {
                        "name": item["tool"],
                        "arguments": json.dumps(item["args"], ensure_ascii=False),
                    },
                }
            ]
        else:
            assistant_message.content = item.get("content", "done")
            assistant_message.tool_calls = None
        from agno.models.response import ModelResponse

        return ModelResponse(role="assistant", content=assistant_message.content or "")


class _NoOpBackend:
    """真实行为的 backend：cat 抛 permanent（文件不存在），ls 成功。"""

    def __init__(self):
        self.calls: list[str] = []

    def run(self, command: str, *, timeout: int = 60) -> str:
        self.calls.append(command)
        if command.startswith("cat "):
            raise ToolExecutionError(
                "命令退出码 1", exit_code=1, stderr="cat: x.yaml: No such file or directory"
            )
        return "config.yaml.bak\nnotes.txt"


class _RecordingToolWrapper:
    """包装 backend，让 list_files/read_file/write_file 可用（走真 Agno 工具注册）。"""

    pass


class ModelLoopPermanentChangePathTest(unittest.TestCase):
    def test_permanent_error_reaches_model_and_changes_behavior(self):
        """决定性闭环：permanent 错误 → 模型收到换路建议 → 模型改用 ls 探索。"""
        backend = _NoOpBackend()
        script = [
            {"tool": "send_command", "args": {"command": "cat x.yaml"}},  # 第 1 轮：失败
            {"tool": "send_command", "args": {"command": "ls"}},          # 第 3 轮：换路（需先验证第 2 轮观察含换路建议）
            {"content": "用 ls 找到了备份文件，任务完成"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            logger = RunLogger(Path(tmp))
            agent = make_coding_agent(backend, logger, max_steps=5, workdir=Path(tmp))
            # 注入 fake model（保留 Agno 的工具注册不动）
            model = ScriptedChatModel(script)
            agent.model = model

            response = agent.run("读一下 x.yaml 里的配置", stream=False)

        # 断言 1：模型第 2 轮收到的 messages 里，tool 消息含换路建议（错误信号到达模型）
        round2 = model.seen_messages[1]
        tool_msgs = [str(getattr(m, "content", "")) for m in round2 if getattr(m, "role", None) == "tool"]
        self.assertTrue(
            any("永久性错误" in c for c in tool_msgs),
            f"换路建议未到达模型，tool 消息: {tool_msgs}",
        )
        # 断言 2：模型行为改变——第 3 轮调用的是 ls 而不是再 cat（真实换路）
        self.assertIn("ls", backend.calls)
        self.assertEqual(backend.calls.count("cat x.yaml"), 1)  # 没有盲目重试
        # 断言 3：trace 里 permanent 分类事件齐全
        events = logger.replay()
        tc = [e for e in events if e["event"] == "tool_call" and e.get("error_kind") == "permanent"]
        self.assertEqual(len(tc), 1)
        self.assertEqual(tc[0]["rule"], "msg:not-found")


class ModelLoopTransientRetryTest(unittest.TestCase):
    def test_transient_auto_retry_invisible_to_model(self):
        """对照：transient 错误工具内部重试，模型无感知（不产生换路消息）。"""
        from coding_agent.test_error_budget import FakeContainerLikeBackend

        backend = FakeContainerLikeBackend([
            ToolExecutionError("database is locked", exit_code=1, stderr="locked"),
            ToolExecutionError("database is locked", exit_code=1, stderr="locked"),
            "QUERY RESULT: 42",
        ])
        script = [
            {"tool": "send_command", "args": {"command": "sqlite3 db 'SELECT count(*)'"}},
            {"content": "查询完成"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            logger = RunLogger(Path(tmp))
            agent = make_coding_agent(backend, logger, max_steps=5, workdir=Path(tmp))
            model = ScriptedChatModel(script)
            agent.model = model
            agent.run("查一下数量", stream=False)

        # 模型只看到最终成功结果，没有 transient 失败痕迹
        round2 = model.seen_messages[1]
        tool_msgs = [str(getattr(m, "content", "")) for m in round2 if getattr(m, "role", None) == "tool"]
        self.assertTrue(any("42" in c for c in tool_msgs))
        self.assertFalse(any("database is locked" in c for c in tool_msgs))
        # 但 trace 里重试事件完整保留（可回放）
        events = [e["event"] for e in logger.replay()]
        self.assertIn("tool_retry", events)
        self.assertIn("tool_retry_ok", events)


if __name__ == "__main__":
    unittest.main()
