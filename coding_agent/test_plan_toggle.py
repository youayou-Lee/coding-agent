"""test_plan_toggle.py — CODING_AGENT_DISABLE_PLAN_TOOLS 对照开关（Issue #27）。

对照实验的生命线：两组除被测变量外零差异。开关必须同时摘除
1) plan/todo/revise_plan 三工具
2) "必须制定计划"的系统指令行（否则模型被告知要计划却无工具可用）
3) plan_provider 注入接线（plan_state 恒 None，注入层无计划可注）
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coding_agent.agent import PLAN_INSTRUCTION, SYSTEM_INSTRUCTIONS, make_coding_agent
from coding_agent.logging_util import RunLogger
from coding_agent.test_model_loop import _NoOpBackend

PLAN_TOOL_NAMES = {"plan", "todo", "revise_plan"}


def _tool_names(agent):
    return {t.name for t in agent.tools}


class PlanToggleTests(unittest.TestCase):
    def _make(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        agent = make_coding_agent(
            _NoOpBackend(), RunLogger(Path(tmp.name)), workdir=Path(tmp.name)
        )
        return agent

    def test_default_has_plan_tools_and_instruction(self):
        agent = self._make()
        self.assertTrue(PLAN_TOOL_NAMES <= _tool_names(agent))
        joined = "\n".join(agent.instructions)
        self.assertIn(PLAN_INSTRUCTION, joined)

    def test_disabled_leaves_no_plan_provider_wiring(self):
        # 验收 #27：接线必须同步断开——注入层拿到的 plan_provider 为 None，
        # anthropic 分支的 set_plan_provider 不被调用
        # #25 配置化后协议来自 provider 声明：patch agent.load_providers 控制分支
        from coding_agent.provider import ProviderConfig

        def _fake_providers(protocol):
            return [ProviderConfig("fake", "https://x.test", "k", "m", protocol=protocol)]

        # Agno Agent 校验 model 必须是 Model 实例——两个分支都用真实 fake Model，
        # compat 层的 set_logger/set_plan_provider 用 Mock 补齐并留断言口
        from coding_agent.test_plan_loop import ScriptedChatModel

        def _fake_model():
            m = ScriptedChatModel([])
            m.set_logger = mock.Mock()
            m.set_plan_provider = mock.Mock()
            return m

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"CODING_AGENT_DISABLE_PLAN_TOOLS": "1"}):
                with mock.patch("coding_agent.agent.OpenAICompatChat", return_value=_fake_model()) as fake_chat, \
                     mock.patch("coding_agent.agent.load_providers", return_value=_fake_providers("openai")):
                    make_coding_agent(_NoOpBackend(), RunLogger(Path(tmp)), workdir=Path(tmp))
            _, kwargs = fake_chat.call_args
            self.assertIsNone(kwargs.get("plan_provider"))
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"CODING_AGENT_DISABLE_PLAN_TOOLS": "1"}):
                fake_model = _fake_model()
                # agent.py 在分支内局部 import——须 patch 源模块才能生效
                with mock.patch(
                    "coding_agent.anthropic_compat.AnthropicCompatChat", return_value=fake_model
                ), mock.patch(
                    "coding_agent.agent.load_providers", return_value=_fake_providers("anthropic")
                ):
                    make_coding_agent(_NoOpBackend(), RunLogger(Path(tmp)), workdir=Path(tmp))
            fake_model.set_plan_provider.assert_not_called()

    def test_anthropic_multi_provider_raises(self):
        # #25 防污染闸门：anthropic 路径不支持多 provider——显式报错而非静默取第一个
        from coding_agent.provider import ProviderConfig
        with tempfile.TemporaryDirectory() as tmp:
            two_anthropic = [
                ProviderConfig("glm1", "https://x.test", "k", "m", protocol="anthropic"),
                ProviderConfig("glm2", "https://y.test", "k", "m", protocol="anthropic"),
            ]
            with mock.patch("coding_agent.agent.load_providers", return_value=two_anthropic):
                with self.assertRaises(ValueError) as ctx:
                    make_coding_agent(_NoOpBackend(), RunLogger(Path(tmp)), workdir=Path(tmp))
            self.assertIn("anthropic", str(ctx.exception))

    def test_disabled_plan_ref_stays_empty(self):
        # 禁用组观测语义固定：无工具能创建计划 → plan_ref 恒为空列表（区别于 [None]）
        with mock.patch.dict(os.environ, {"CODING_AGENT_DISABLE_PLAN_TOOLS": "1"}):
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            plan_ref: list = []
            make_coding_agent(
                _NoOpBackend(), RunLogger(Path(tmp.name)), workdir=Path(tmp.name), plan_ref=plan_ref
            )
        self.assertEqual(plan_ref, [])

    def test_disabled_removes_tools_and_instruction(self):
        with mock.patch.dict(os.environ, {"CODING_AGENT_DISABLE_PLAN_TOOLS": "1"}):
            agent = self._make()
        self.assertFalse(PLAN_TOOL_NAMES & _tool_names(agent))
        # 其余工具必须原样保留（对照组只能少 Plan，不能少别的）
        self.assertEqual(_tool_names(agent), {"send_command", "list_files", "read_file", "write_file"})
        joined = "\n".join(agent.instructions)
        self.assertNotIn("revise_plan", joined)
        self.assertNotIn(PLAN_INSTRUCTION, joined)
        # 基础指令不受影响
        for line in SYSTEM_INSTRUCTIONS:
            self.assertIn(line, joined)

    def test_disabled_accepts_truthy_variants_and_default_off(self):
        for value in ("true", "YES", " 1 "):
            with mock.patch.dict(os.environ, {"CODING_AGENT_DISABLE_PLAN_TOOLS": value}):
                agent = self._make()
            self.assertFalse(PLAN_TOOL_NAMES & _tool_names(agent), value)
        with mock.patch.dict(os.environ, {"CODING_AGENT_DISABLE_PLAN_TOOLS": "0"}):
            agent = self._make()
        self.assertTrue(PLAN_TOOL_NAMES <= _tool_names(agent))


if __name__ == "__main__":
    unittest.main()
