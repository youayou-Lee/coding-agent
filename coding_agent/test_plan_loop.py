"""test_plan_loop.py — Plan/Todo 在 Agno 循环中的集成验证（Issue #19 L2）。

复用 test_model_loop 的 ScriptedChatModel 架构（继承 Agno Model，覆写 invoke，
基类工具循环原样运行），验证三个决定性信号：
1. 模型调 plan 工具 → 计划建立 + trace 事件
2. 后续轮次模型收到的 messages 含计划全文（注入生效——计划对模型"可见"）
3. todo 勾销联动（进度摘要随轮次更新）+ revise 修订事件
"""

import json
import tempfile
import unittest
from pathlib import Path

from coding_agent.agent import make_coding_agent
from coding_agent.logging_util import RunLogger
from coding_agent.test_model_loop import _NoOpBackend


from agno.models.base import Model


class ScriptedChatModel(Model):
    """继承 Agno Model 基类（与 test_model_loop 相同架构）：覆写 invoke，
    基类 response() 的工具循环原样运行——plan/todo 的注入与勾销由真实循环验证。"""

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

    def invoke_stream(self, messages, assistant_message, **kwargs):
        yield self.invoke(messages, assistant_message, **kwargs)

    def _mk_resp(self, item):
        from agno.models.response import ModelResponse

        resp = ModelResponse(role="assistant")
        if "tool" in item:
            resp.content = ""
            resp.tool_calls = [
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
            resp.content = item.get("content", "done")
            resp.tool_calls = None
        return resp

    def invoke(self, messages, assistant_message, **kwargs):
        self.seen_messages.append(list(messages))
        item = self.script.pop(0) if self.script else {"content": "done"}
        return self._mk_resp(item)

    def get_last_user_visible_text(self, round_no: int) -> str:
        """取第 round_no 轮模型收到的全部消息文本（验证注入）。"""
        return "\n".join(str(getattr(m, "content", "")) for m in self.seen_messages[round_no - 1])

    # Agno 序列化/钩子最小集
    def to_dict(self):
        return {"id": self.id, "provider": "fake"}


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
        """决定性信号 1+2：plan 工具建立计划；后续轮次模型上下文含计划全文。"""
        script = [
            {"tool": "plan", "args": {"steps": ["探索目录", "写脚本", "跑测试"]}},
            {"tool": "send_command", "args": {"command": "ls"}},
            {"content": "按计划执行完毕"},
        ]
        model, logger = _run(script)

        # trace 事件
        events = [e["event"] for e in logger.replay()]
        self.assertIn("plan_created", events)

        # 注入验证：第 2 轮模型收到的消息里应含计划文本
        round2_text = model.get_last_user_visible_text(2)
        self.assertIn("探索目录", round2_text)
        self.assertIn("0/3 done", round2_text)
        self.assertIn("rev0", round2_text)

    def test_todo_marks_and_progress_updates(self):
        """决定性信号 3：勾销联动——第 3 轮模型看到的进度应反映 todo 更新。"""
        script = [
            {"tool": "plan", "args": {"steps": ["探索目录", "写脚本", "跑测试"]}},
            {"tool": "todo", "args": {"step_id": 1, "status": "done"}},
            {"tool": "send_command", "args": {"command": "ls"}},
            {"content": "继续"},
        ]
        model, logger = _run(script)

        events = [e["event"] for e in logger.replay()]
        self.assertIn("todo_marked", events)
        # 第 3 轮注入：进度已更新为 1/3
        round3_text = model.get_last_user_visible_text(3)
        self.assertIn("1/3 done", round3_text)
        self.assertIn("[x] #1", round3_text)

    def test_revise_plan_increments_revision(self):
        script = [
            {"tool": "plan", "args": {"steps": ["旧方向"]}},
            {"tool": "revise_plan", "args": {"steps": ["新方向 A", "新方向 B"]}},
            {"content": "按新计划执行"},
        ]
        model, logger = _run(script)
        events = [e["event"] for e in logger.replay()]
        self.assertIn("plan_revised", events)
        # 第 3 轮注入：rev1 + 新步骤
        round3_text = model.get_last_user_visible_text(3)
        self.assertIn("rev1", round3_text)
        self.assertIn("新方向 A", round3_text)
        # 当前计划快照 = 新方向（旧方向只存在于历史轮次的工具返回里，快照已替换）
        self.assertIn("0/2 done", round3_text)

    def test_todo_without_plan_returns_error(self):
        script = [
            {"tool": "todo", "args": {"step_id": 1, "status": "done"}},
            {"content": "好"},
        ]
        model, logger = _run(script)
        # 模型第 2 轮应收到错误提示（引导先 plan）
        round2_text = model.get_last_user_visible_text(2)
        self.assertIn("尚未制定计划", round2_text)


if __name__ == "__main__":
    unittest.main()
