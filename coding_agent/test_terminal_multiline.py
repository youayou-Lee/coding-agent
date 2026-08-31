"""test_terminal_multiline.py — wrap_multiline 的确定性测试（bug#1 回归集）。"""

import base64
import unittest

from coding_agent.terminal import wrap_multiline


class WrapMultilineTest(unittest.TestCase):
    def test_single_line_untouched(self):
        wrapped, was = wrap_multiline("ls -la /app")
        self.assertFalse(was)
        self.assertEqual(wrapped, "ls -la /app")

    def test_multiline_gets_wrapped(self):
        cmd = "cat > f.txt << 'X'\nhello\nX"
        wrapped, was = wrap_multiline(cmd)
        self.assertTrue(was)
        self.assertNotIn("\n", wrapped)  # 单行保证——bug#1 的核心不变量
        self.assertTrue(wrapped.startswith("echo "))
        self.assertIn("| base64 -d | bash", wrapped)

    def test_roundtrip_preserves_command(self):
        cmd = "cat > f.txt << 'X'\nline1\nline2  with spaces\nX\necho DONE"
        wrapped, was = wrap_multiline(cmd)
        self.assertTrue(was)
        # 解码后必须逐字节还原原命令
        b64 = wrapped.removeprefix("echo ").removesuffix(" | base64 -d | bash")
        self.assertEqual(base64.b64decode(b64).decode("utf-8"), cmd)

    def test_trailing_newline_counts_as_multiline(self):
        # 尾部换行同样会触发 TB 的提前执行，必须包装
        wrapped, was = wrap_multiline("echo hi\n")
        self.assertTrue(was)

    def test_unicode_survives_roundtrip(self):
        cmd = "echo '中文内容测试'\necho done"
        wrapped, _ = wrap_multiline(cmd)
        b64 = wrapped.removeprefix("echo ").removesuffix(" | base64 -d | bash")
        self.assertEqual(base64.b64decode(b64).decode("utf-8"), cmd)


if __name__ == "__main__":
    unittest.main()
