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
    def test_fallback_single_provider(self):
        import os
        old = {k: os.environ.get(k) for k in ("LLM_PROVIDERS", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL")}
        try:
            os.environ.pop("LLM_PROVIDERS", None)
            os.environ["LLM_API_KEY"] = "k"
            os.environ["LLM_BASE_URL"] = "https://x.test"
            os.environ["LLM_MODEL"] = "m"
            ps = load_providers()
            self.assertEqual(len(ps), 1)
            self.assertEqual(ps[0].name, "default")
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_multi_provider_chain(self):
        import os
        env = {
            "LLM_PROVIDERS": "glm,deepseek",
            "GLM_API_KEY": "k1", "GLM_BASE_URL": "https://glm.test", "GLM_MODEL": "glm-5.3-flash",
            "DEEPSEEK_API_KEY": "k2", "DEEPSEEK_BASE_URL": "https://api.deepseek.com", "DEEPSEEK_MODEL": "deepseek-chat",
        }
        old = {k: os.environ.get(k) for k in env}
        try:
            os.environ.update(env)
            os.environ.pop("GLM_THINKING", None)  # 隔离宿主 .env 的 GLM_THINKING=low
            ps = load_providers()
            self.assertEqual([p.name for p in ps], ["glm", "deepseek"])
            self.assertEqual(ps[0].thinking, None)  # 未设 GLM_THINKING
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
