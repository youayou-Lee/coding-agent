"""test_provider.py — ProviderChain 的确定性测试（不碰真实 API）。

用假 attempt 函数模拟：瞬时故障重试成功 / 重试耗尽切换 / 配置错误直接切换 /
全链挂掉抛最后异常。sleeper 注入，测试不等真实时间。
"""

import unittest

import httpx
import openai

from coding_agent.provider import ProviderChain, ProviderConfig, load_providers



def api_error(status: int) -> openai.APIStatusError:
    """构造真实的 APIStatusError（status_code 从 httpx.Response 读取）。"""
    req = httpx.Request("POST", "https://x.test")
    resp = httpx.Response(status, request=req)
    return openai.APIStatusError(f"err-{status}", response=resp, body=None)

def make_chain(providers=None, **kw):
    providers = providers or [
        ProviderConfig("glm", "https://glm.test", "k1", "m1"),
        ProviderConfig("deepseek", "https://ds.test", "k2", "m2"),
    ]
    return ProviderChain(providers, sleeper=lambda s: None, **kw)


class ProviderChainTest(unittest.TestCase):
    def test_first_try_success(self):
        chain = make_chain()
        result = chain.execute(lambda p: f"ok-{p.name}")
        self.assertEqual(result, "ok-glm")

    def test_transient_then_retry_succeeds(self):
        chain = make_chain()
        calls = []

        def attempt(p):
            calls.append(p.name)
            if len(calls) == 1:
                raise api_error(500)
            return f"ok-{p.name}"

        events = []
        self.assertEqual(chain.execute(attempt, on_event=lambda e, d: events.append(e)), "ok-glm")
        self.assertEqual(calls, ["glm", "glm"])  # 同一 provider 重试
        self.assertIn("provider_retry", events)

    def test_retries_exhausted_then_failover(self):
        chain = make_chain()
        calls = []

        def attempt(p):
            calls.append(p.name)
            raise api_error(500)

        with self.assertRaises(openai.APIStatusError):
            chain.execute(attempt)
        # glm: 1+2 次重试 = 3 次；然后 deepseek: 3 次
        self.assertEqual(calls, ["glm"] * 3 + ["deepseek"] * 3)

    def test_config_error_switches_immediately(self):
        chain = make_chain()
        calls = []

        def attempt(p):
            calls.append(p.name)
            raise api_error(401)

        with self.assertRaises(openai.APIStatusError):
            chain.execute(attempt)
        self.assertEqual(calls, ["glm", "deepseek"])  # 401 不重试，直接切

    def test_chain_exhausted_raises_last(self):
        chain = make_chain()  # 只有 2 个 provider

        def attempt(p):
            raise api_error(503)

        with self.assertRaises(openai.APIStatusError):
            chain.execute(attempt)
        self.assertTrue(chain.exhausted)

    def test_connection_error_is_transient(self):
        chain = make_chain()
        calls = []

        def attempt(p):
            calls.append(p.name)
            if len(calls) < 2:
                raise openai.APIConnectionError(request=None)
            return "ok"

        self.assertEqual(chain.execute(attempt), "ok")
        self.assertEqual(calls, ["glm", "glm"])


class LoadProvidersTest(unittest.TestCase):
    """providers.yaml 配置化（#25）：校验显式失败、禁用跳过、协议一致性。"""

    def _write_yaml(self, content: str) -> str:
        import os
        import tempfile
        from pathlib import Path

        fd, path = tempfile.mkstemp(suffix=".yaml")
        with os.fdopen(fd, "w") as f:
            f.write(content)
        self.addCleanup(lambda: os.unlink(path))
        return path

    def _with_config(self, yaml_content: str, extra_env: dict | None = None):
        """在临时 providers.yaml 下调用 load_providers，返回结果或异常。"""
        import os
        from unittest import mock

        path = self._write_yaml(yaml_content)
        env = {"PROVIDERS_CONFIG": path, **(extra_env or {})}
        with mock.patch.dict(os.environ, env):
            return load_providers()

    def test_load_enabled_skips_disabled(self):
        ps = self._with_config(
            """
providers:
  - name: glm
    protocol: anthropic
    api_key_env: TEST_GLM_KEY
    base_url: https://glm.test
    model: glm-5.3-flash
    enabled: true
    on_failover: strict
  - name: deepseek
    protocol: openai
    api_key_env: TEST_DS_KEY
    base_url: https://ds.test
    model: deepseek-chat
    enabled: false
""",
            {"TEST_GLM_KEY": "k1"},
        )
        self.assertEqual([p.name for p in ps], ["glm"])
        self.assertEqual(ps[0].protocol, "anthropic")
        self.assertEqual(ps[0].on_failover, "strict")

    def test_missing_yaml_raises(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"PROVIDERS_CONFIG": "/nonexistent/providers.yaml"}):
            with self.assertRaises(FileNotFoundError):
                load_providers()

    def test_enabled_provider_missing_key_raises(self):
        # 静默跳过的反模式废除：启用 provider 缺 key 必须响亮失败
        with self.assertRaises(ValueError) as ctx:
            self._with_config(
                """
providers:
  - name: glm
    api_key_env: TEST_ABSENT_KEY
    base_url: https://glm.test
    model: m
""",
                {"TEST_ABSENT_KEY": ""},  # 显式置空，隔离宿主环境
            )
        self.assertIn("TEST_ABSENT_KEY", str(ctx.exception))

    def test_missing_api_key_env_field_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self._with_config(
                """
providers:
  - name: glm
    base_url: https://glm.test
    model: m
"""
            )
        self.assertIn("api_key_env", str(ctx.exception))

    def test_mixed_protocol_chain_raises(self):
        # #25 分叉：协议异构不通——混协议启动即报错
        with self.assertRaises(ValueError) as ctx:
            self._with_config(
                """
providers:
  - name: glm
    protocol: anthropic
    api_key_env: TEST_GLM_KEY
    base_url: https://glm.test
    model: m
  - name: ds
    protocol: openai
    api_key_env: TEST_DS_KEY
    base_url: https://ds.test
    model: m2
""",
                {"TEST_GLM_KEY": "k1", "TEST_DS_KEY": "k2"},
            )
        self.assertIn("协议混用", str(ctx.exception))

    def test_all_disabled_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self._with_config(
                """
providers:
  - name: glm
    api_key_env: TEST_GLM_KEY
    base_url: https://glm.test
    model: m
    enabled: false
"""
            )
        self.assertIn("enabled", str(ctx.exception))

    def test_invalid_protocol_raises(self):
        with self.assertRaises(ValueError):
            self._with_config(
                """
providers:
  - name: glm
    protocol: grpc
    api_key_env: TEST_GLM_KEY
    base_url: https://glm.test
    model: m
""",
                {"TEST_GLM_KEY": "k1"},
            )

    def test_duplicate_name_raises(self):
        # 重名会让 ProviderChat 的 client 缓存（按 name 键控）在 failover 时
        # 静默复用前一个 provider 的 client（旧 key/base_url）——启动即拦
        with self.assertRaises(ValueError) as ctx:
            self._with_config(
                """
providers:
  - name: glm
    api_key_env: TEST_GLM_KEY
    base_url: https://glm.test
    model: m1
  - name: glm
    api_key_env: TEST_GLM_KEY2
    base_url: https://glm2.test
    model: m2
""",
                {"TEST_GLM_KEY": "k1", "TEST_GLM_KEY2": "k2"},
            )
        self.assertIn("重复", str(ctx.exception))

    def test_enabled_non_bool_raises(self):
        # enabled: "false" 是 truthy——不校验会被当启用，报错位置让人困惑
        with self.assertRaises(ValueError) as ctx:
            self._with_config(
                """
providers:
  - name: glm
    api_key_env: TEST_GLM_KEY
    base_url: https://glm.test
    model: m
    enabled: "false"
""",
                {"TEST_GLM_KEY": "k1"},
            )
        self.assertIn("布尔", str(ctx.exception))

    def test_non_dict_structure_raises(self):
        with self.assertRaises(ValueError):
            self._with_config("- just\n- a list\n")


class StrictFailoverTest(unittest.TestCase):
    """on_failover=strict：重试耗尽不切链，立即抛错（实验模式宁断不混，#25）。"""

    def test_strict_raises_instead_of_switching(self):
        chain = make_chain([
            ProviderConfig("glm", "https://glm.test", "k1", "m1", on_failover="strict"),
            ProviderConfig("deepseek", "https://ds.test", "k2", "m2"),
        ])
        calls = []
        events = []

        def attempt(p):
            calls.append(p.name)
            raise api_error(500)

        with self.assertRaises(openai.APIStatusError):
            chain.execute(attempt, on_event=lambda e, d: events.append(e))
        self.assertEqual(calls, ["glm"] * 3)  # 只重试 glm，从未碰 deepseek
        self.assertIn("provider_strict_stop", events)
        self.assertNotIn("provider_failover", events)
        self.assertFalse(chain.exhausted)  # 游标没动过

    def test_alert_provider_still_switches(self):
        chain = make_chain([
            ProviderConfig("glm", "https://glm.test", "k1", "m1", on_failover="alert"),
            ProviderConfig("deepseek", "https://ds.test", "k2", "m2"),
        ])
        calls = []

        def attempt(p):
            calls.append(p.name)
            raise api_error(500)

        with self.assertRaises(openai.APIStatusError):
            chain.execute(attempt)
        self.assertEqual(calls, ["glm"] * 3 + ["deepseek"] * 3)


if __name__ == "__main__":
    unittest.main()
