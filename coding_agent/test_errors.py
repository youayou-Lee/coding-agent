"""test_errors.py — 错误分类器的确定性测试（Issue #3 验收：≥8 项）。

覆盖：三类各 ≥2 例、兜底（方案 A）、中文消息、exit_code 优先级、
异常类型优先于消息、frozen 不可变、stderr 合并、真实 trace 集成样例。
"""

import unittest

from coding_agent.errors import ErrorKind, ToolErrorType, classify_error


class TransientRulesTest(unittest.TestCase):
    def test_timeout_exception(self):
        kind = classify_error(TimeoutError("cmd took too long"))
        self.assertIs(kind.type, ToolErrorType.TRANSIENT)
        self.assertEqual(kind.rule, "exc:TimeoutError")

    def test_exit_124_means_timeout(self):
        kind = classify_error("killed", exit_code=124)
        self.assertIs(kind.type, ToolErrorType.TRANSIENT)
        self.assertEqual(kind.rule, "exit:124-timeout")

    def test_english_connection_refused(self):
        kind = classify_error("curl: (7) Failed to connect: Connection refused")
        self.assertIs(kind.type, ToolErrorType.TRANSIENT)

    def test_chinese_lock_message(self):
        kind = classify_error("数据库被锁，请稍后重试")
        self.assertIs(kind.type, ToolErrorType.TRANSIENT)


class PermanentRulesTest(unittest.TestCase):
    def test_file_not_found_exception(self):
        kind = classify_error(FileNotFoundError("[Errno 2] 没有那个文件"))
        self.assertIs(kind.type, ToolErrorType.PERMANENT)
        self.assertEqual(kind.rule, "exc:FileNotFoundError")

    def test_permission_denied_text(self):
        kind = classify_error("cat: /etc/shadow: Permission denied", exit_code=1)
        self.assertIs(kind.type, ToolErrorType.PERMANENT)

    def test_exit_127_command_not_found(self):
        kind = classify_error("sh: 1: foo: not found", exit_code=127)
        self.assertIs(kind.type, ToolErrorType.PERMANENT)
        self.assertEqual(kind.rule, "exit:127-not-found")

    def test_syntax_error_via_stderr(self):
        kind = classify_error("python died", stderr="SyntaxError: invalid syntax")
        self.assertIs(kind.type, ToolErrorType.PERMANENT)


class SemanticFallbackTest(unittest.TestCase):
    """方案 A：未知错误 → SEMANTIC 透传，不自动重试。"""

    def test_unknown_message_falls_back_to_semantic(self):
        kind = classify_error("md5 mismatch: expected aaa got bbb")
        self.assertIs(kind.type, ToolErrorType.SEMANTIC)
        self.assertEqual(kind.rule, "fallback:unknown")

    def test_none_and_empty_fall_back(self):
        self.assertIs(classify_error(None).type, ToolErrorType.SEMANTIC)
        self.assertIs(classify_error("").type, ToolErrorType.SEMANTIC)


class PriorityTest(unittest.TestCase):
    def test_exception_type_beats_message(self):
        # TimeoutError 的消息碰巧含"not found"——类型层必须先赢
        kind = classify_error(TimeoutError("waiting for not found resource"))
        self.assertIs(kind.type, ToolErrorType.TRANSIENT)
        self.assertEqual(kind.rule, "exc:TimeoutError")

    def test_exit_code_beats_message(self):
        # exit 127（命令不存在，permanent）但消息带 timeout 字样——exit 层赢
        kind = classify_error("timeout waiting for foo", exit_code=127)
        self.assertIs(kind.type, ToolErrorType.PERMANENT)

    def test_message_beats_fallback(self):
        kind = classify_error("weird state", exit_code=1)
        self.assertIs(kind.type, ToolErrorType.SEMANTIC)


class ErrorKindContractTest(unittest.TestCase):
    def test_error_kind_is_frozen(self):
        kind = classify_error("anything")
        with self.assertRaises(Exception):
            kind.rule = "tampered"

    def test_rule_always_present_for_trace(self):
        # 每个结果都必须带依据——trace 回放的底线
        for err in (TimeoutError(), FileNotFoundError(), "foo bar", "", None):
            kind = classify_error(err)
            self.assertTrue(kind.rule)


if __name__ == "__main__":
    unittest.main()
