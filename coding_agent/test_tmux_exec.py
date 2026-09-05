"""test_tmux_exec.py — TmuxBackend 方案 D（docker exec）的 L2 组件测试。

FakeContainer 模拟 docker exec_run：按脚本返回 (exit_code, (stdout, stderr))，
验证 TmuxBackend.run 的契约与 LocalBackend 完全对齐（成功返回/失败抛 ToolExecutionError）
以及 cwd 持久性、124 超时映射、标记剥离。不碰真 Docker。
"""

import unittest

from coding_agent.terminal import ToolExecutionError, TmuxBackend


class FakeContainer:
    """exec_run 按 script 弹出结果；记录每次调用收到的完整命令。"""

    def __init__(self, script=None, *, behave=None):
        self.script = list(script or [])
        self.calls: list[str] = []
        self.behave = behave  # 高级：函数(cmd_str) -> (exit_code, (out, err))

    def exec_run(self, cmd, workdir=None, demux=True):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        self.calls.append(cmd_str)
        if self.behave is not None:
            return self.behave(cmd_str)  # 必须返回 _ExecResult（与真实 docker SDK 形状一致）
        code, out = self.script.pop(0) if self.script else (0, (b"", b""))
        return _ExecResult(code, out)


class _ExecResult:
    """模拟 docker ExecResult（namedtuple 风格：exit_code + output）。"""

    def __init__(self, exit_code, output):
        self.exit_code = exit_code
        self.output = output


def _make_backend(script=None, *, behave=None):
    container = FakeContainer(script, behave=behave)
    backend = TmuxBackend.__new__(TmuxBackend)  # 跳过 __init__（需要真 TmuxSession）
    backend._container = container
    backend._session = None
    backend._logger = None
    backend._cwd = None
    return backend, container


def _ok(stdout: bytes, stderr: bytes = b""):
    return _ExecResult(0, (stdout, stderr))


def _fail(code: int, stdout: bytes = b"", stderr: bytes = b""):
    return _ExecResult(code, (stdout, stderr))


M = TmuxBackend._EXIT_MARKER


class ContractAlignmentTest(unittest.TestCase):
    """验收：与 LocalBackend 契约完全一致——成功返回字符串，失败抛 ToolExecutionError。"""

    def test_success_returns_clean_output_without_marker(self):
        backend, container = _make_backend()
        result = backend.run("ls")
        self.assertNotIn(M, result)  # 标记行剥掉，模型不可见
        # 内部命令带 timeout 包裹与退出码标记
        self.assertIn("timeout 60s bash -c", container.calls[0])
        self.assertIn(M, container.calls[0])

    def test_nonzero_exit_raises_tool_execution_error(self):
        backend, _ = _make_backend(
            behave=lambda cmd: _fail(2, b"", b"cat: x: No such file or directory")
        )
        with self.assertRaises(ToolExecutionError) as ctx:
            backend.run("cat x")
        self.assertEqual(ctx.exception.exit_code, 2)
        self.assertIn("No such file", ctx.exception.stderr)

    def test_timeout_exit_124_maps_to_transient(self):
        # 容器内 timeout 杀 bash：bash 自身 exit 124，无标记行
        backend, _ = _make_backend(behave=lambda cmd: _ExecResult(124, (b"partial", b"")))
        with self.assertRaises(ToolExecutionError) as ctx:
            backend.run("sleep infinity")
        self.assertEqual(ctx.exception.exit_code, 124)

    def test_docker_layer_failure_raises(self):
        backend, _ = _make_backend()

        def boom(cmd):
            raise RuntimeError("container stopped")

        backend._container.exec_run = boom
        with self.assertRaises(ToolExecutionError):
            backend.run("ls")

    def test_multiline_command_natively_supported(self):
        # bash -c 下多行命令天然合法，不再需要 base64 单行化
        backend, container = _make_backend(
            behave=lambda cmd: _ok(b"done\n" + M.encode() + b"0\n")
        )
        result = backend.run("cat <<'EOF'\nhello\nEOF")
        self.assertIn("done", result)
        self.assertNotIn(M, result)


class CwdPersistenceTest(unittest.TestCase):
    """验收：cd 跨命令持久（对齐 tmux 的 shell 持续存活语义）。"""

    def test_cwd_tracked_after_cd(self):
        backend, _ = _make_backend(behave=lambda cmd: _ok(M.encode() + b"0\n"))
        backend.run("cd /app/src")
        self.assertEqual(backend._cwd, "/app/src")
        backend.run("make")
        # 第二条命令应显式 cd 回 /app/src
        self.assertIn("cd /app/src", backend._container.calls[1])

    def test_cwd_relative_resolution(self):
        backend, _ = _make_backend(behave=lambda cmd: _ok(M.encode() + b"0\n"))
        backend._cwd = "/app"
        backend.run("cd src")
        self.assertEqual(backend._cwd, "/app/src")

    def test_cd_failure_raises_permanent(self):
        # cd 目标不存在 → cd 失败 → 无标记行 + 非 124 → permanent 语义
        backend, _ = _make_backend(behave=lambda cmd: _fail(1, b""))
        backend._cwd = "/nonexistent_xyz"
        with self.assertRaises(ToolExecutionError) as ctx:
            backend.run("ls")
        self.assertIn("cd", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
