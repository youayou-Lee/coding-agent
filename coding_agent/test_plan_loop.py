"""test_plan_loop.py — Plan/Todo 在 Agno 循环中的集成验证（Issue #19 设计 v2）。

ScriptedChatModel 继承 Agno Model 基类（覆写 invoke，基类工具循环原样运行），
四个决定性信号：
1. plan 工具建立计划 + trace 事件
2. 计划全文注入模型上下文（分叉 1 = A：每步全量）
3. todo 勾销联动（进度随轮次更新）
4. revise 修订历史注入（分叉 3 软约束：模型看见自己的修订次数）
"""

import json
import tempfile
import unittest
from pathlib import Path

from agno.models.base import Model

from coding_agent.agent import make_coding_agent
from coding_agent.logging_util import RunLogger
from coding_agent.test_model_loop import _NoOpBackend


class ScriptedChatModel(Model):
    """继承 Model 基类，覆写 invoke；基类 response() 的工具循环原样运行。"""

    def __init__(self, script, *, id="fake-model"):
        super().__init__(id=id)
        self.provider = "fake"
        self.script = list(script)
        self.seen_messages: list = []

    def _parse_provider_response(self, response, response_format=None):
        return response

    def _parse_provider_response_delta(self, response):
        return response

    async def ainvoke(self, messages, assistant_message, **kwargs):
        return self.invoke(messages, assistant_message, **kwargs)

    async def ainvoke_stream(self, messages, assistant_message, **kwargs):
        yield self.invoke(messages, assistant_message, **kwargs)
        return

    def invoke_stream(self, messages, assistant_message, **kwargs):
        yield self.invoke(messages, assistant_message, **kwargs)

    def invoke(self, messages, assistant_message, **kwargs):
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

    def round_text(self, round_no: int) -> str:
        """第 round_no 轮模型收到的全部消息文本（验证注入）。"""
        return "\n".join(str(getattr(m, "content", "")) for m in self.seen_messages[round_no - 1])


def _run(script, max_steps=8):
    with tempfile.TemporaryDirectory() as tmp:
        logger = RunLogger(Path(tmp))
        agent = make_coding_agent(_NoOpBackend(), logger, max_steps=max_steps, workdir=Path(tmp))
        model = ScriptedChatModel(script)
        agent.model = model
        agent.run("多步任务：探索、写脚本、跑测试", stream=False)
        return model, logger


class PlanLoopTest(unittest.TestCase):
    def test_plan_created_and_injected_to_model(self):
        """信号 1+2：plan 建立计划；后续轮次模型上下文含计划全文（分叉 1=A）。"""
        script = [
            {"tool": "plan", "args": {"steps": ["探索目录", "写脚本", "跑测试"]}},
            {"tool": "send_command", "args": {"command": "ls"}},
            {"content": "按计划执行完毕"},
        ]
        model, logger = _run(script)
        events = [e["event"] for e in logger.replay()]
        self.assertIn("plan_created", events)
        round2 = model.round_text(2)
        self.assertIn("探索目录", round2)
        self.assertIn("0/3 done", round2)
        self.assertIn("rev0", round2)

    def test_todo_marks_and_progress_updates(self):
        """信号 3：勾销联动——第 3 轮模型看到的进度反映 todo 更新。"""
        script = [
            {"tool": "plan", "args": {"steps": ["探索目录", "写脚本", "跑测试"]}},
            {"tool": "todo", "args": {"step_id": 1, "status": "done"}},
            {"tool": "send_command", "args": {"command": "ls"}},
            {"content": "继续"},
        ]
        model, logger = _run(script)
        events = [e["event"] for e in logger.replay()]
        self.assertIn("todo_marked", events)
        round3 = model.round_text(3)
        self.assertIn("1/3 done", round3)
        self.assertIn("[x] #1", round3)

    def test_revise_history_injected_as_soft_constraint(self):
        """信号 4（分叉 3 软约束）：修订原因随计划注入模型上下文。"""
        script = [
            {"tool": "plan", "args": {"steps": ["旧方向"]}},
            {"tool": "revise_plan", "args": {"steps": ["新方向 A", "新方向 B"], "reason": "原步骤前提不成立"}},
            {"content": "按新计划执行"},
        ]
        model, logger = _run(script)
        events = [e["event"] for e in logger.replay()]
        self.assertIn("plan_revised", events)
        rev_event = [e for e in logger.replay() if e["event"] == "plan_revised"][0]
        self.assertEqual(rev_event["revision"], 1)
        self.assertEqual(rev_event["reason"], "原步骤前提不成立")
        round3 = model.round_text(3)
        self.assertIn("rev1", round3)
        self.assertIn("新方向 A", round3)
        self.assertIn("0/2 done", round3)  # 当前快照 = 新方向
        self.assertIn("原步骤前提不成立", round3)  # 软约束：修订原因可见
        self.assertIn("频繁修订请反思计划质量", round3)

    def test_todo_without_plan_returns_error(self):
        script = [
            {"tool": "todo", "args": {"step_id": 1, "status": "done"}},
            {"content": "好"},
        ]
        model, logger = _run(script)
        round2 = model.round_text(2)
        self.assertIn("尚未制定计划", round2)


if __name__ == "__main__":
    unittest.main()
