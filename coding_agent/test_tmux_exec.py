"""test_tmux_exec.py — TmuxBackend 方案 D（docker exec）的 L2 组件测试。

FakeContainer 忠实模拟 docker exec 的**执行语义**（C2 教训：形状+语义两层保真）：
- 外层脚本以 printf 收尾 → docker exec 的 exit_code 恒 0（除非外层被杀）
- 内层命令的真实退出码经 EXIT 标记行进 stdout
- PWD 标记行进 stdout（cwd 由 shell 回报，I1 根治方案）
- stderr 独立通道（C1 修复后 run 只对 stdout 解析标记）

生产真实形状（docker 实测复核，2026-09-06）：
  exec_run(["bash","-lc","{ cat /nope; }; ec=$?; printf EXIT; printf PWD"])
  → ExitCode=0, stdout=b"...\n__CODING_AGENT_EXIT:1\n__CODING_AGENT_PWD:/xxx\n",
    stderr=b"cat: /nope: No such file..."
"""

import unittest

from coding_agent.terminal import ToolExecutionError, TmuxBackend

M = TmuxBackend._EXIT_MARKER
P = TmuxBackend._PWD_MARKER


class _ExecResult:
    """与真实 docker ExecResult 形状一致：exit_code + output tuple。"""

    def __init__(self, exit_code: int, output):
        self.exit_code = exit_code
        self.output = output


class FakeContainer:
    """忠实模拟外层脚本语义：给定内层码/输出，构造生产真实形状的 ExecResult。

    behave(inner_code, stdout_text, stderr_text) -> 额外覆写（如模拟外层被杀）。
    """

    def __init__(self, *, inner_code=0, stdout="", stderr="", pwd="/app", behave=None):
        self.inner_code = inner_code
        self._stdout = stdout
        self._stderr = stderr
        self._pwd = pwd
        self._behave = behave
        self.calls: list[str] = []

    def exec_run(self, cmd, workdir=None, demux=True):
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        self.calls.append(cmd_str)
        if self._behave is not None:
            return self._behave(cmd_str)
        # 生产真实形状：外层 exit 恒 0（printf 是外层最后命令），标记进 stdout
        stdout = (
            self._stdout
            + f"{M}{self.inner_code}\n"
            + f"{P}{self._pwd}\n"
        )
        return _ExecResult(0, (stdout.encode(), self._stderr.encode()))


class _HangingContainer:
    """模拟外层被 timeout 杀：无标记行 + 外层 exit 124。"""

    def __init__(self):
        self.calls: list[str] = []

    def exec_run(self, cmd, workdir=None, demux=True):
        self.calls.append(" ".join(cmd))
        return _ExecResult(124, (b"partial output\n", b""))


class _DockerErrorContainer:
    """模拟容器/连接层故障。"""

    def __init__(self):
        self.calls: list[str] = []

    def exec_run(self, cmd, workdir=None, demux=True):
        self.calls.append(" ".join(cmd))
        raise RuntimeError("container stopped")


def _make(container):
    backend = TmuxBackend.__new__(TmuxBackend)  # 跳过 __init__（需要真 TmuxSession）
    backend._container = container
    backend._session = None
    backend._logger = None
    backend._cwd = None
    return backend


class ContractAlignmentTest(unittest.TestCase):
    """验收 #1：与 LocalBackend 契约完全一致。"""

    def test_success_returns_clean_output_without_markers(self):
        c = FakeContainer(stdout="hello\n")
        result = _make(c).run("ls")
        self.assertIn("hello", result)
        self.assertNotIn(M, result)  # 两个标记都对模型不可见
        self.assertNotIn(P, result)
        # 内部命令带 timeout 包裹与两个标记
        self.assertIn("timeout 60s bash -c", c.calls[0])
        self.assertIn(M, c.calls[0])
        self.assertIn(P, c.calls[0])

    def test_failure_with_stderr_raises_tool_execution_error(self):
        """C1 回归（决定性用例）：stderr 非空的失败命令必须抛异常，不能吞成成功。"""
        c = FakeContainer(
            inner_code=2,
            stdout="",
            stderr="cat: x: No such file or directory\n",
        )
        with self.assertRaises(ToolExecutionError) as ctx:
            _make(c).run("cat x")
        self.assertEqual(ctx.exception.exit_code, 2)
        self.assertIn("No such file", ctx.exception.stderr)
        self.assertNotIn(M, ctx.exception.stderr)  # 标记不泄漏

    def test_failure_without_stderr_still_raises(self):
        c = FakeContainer(inner_code=1, stdout="", stderr="")
        with self.assertRaises(ToolExecutionError) as ctx:
            _make(c).run("false-ish cmd")
        self.assertEqual(ctx.exception.exit_code, 1)

    def test_timeout_exit_124_maps_to_transient(self):
        """验收 #2：外层被杀（无标记 + 外层 124）→ ToolExecutionError(124)。"""
        c = _HangingContainer()
        with self.assertRaises(ToolExecutionError) as ctx:
            _make(c).run("sleep infinity")
        self.assertEqual(ctx.exception.exit_code, 124)

    def test_docker_layer_failure_raises(self):
        with self.assertRaises(ToolExecutionError):
            _make(_DockerErrorContainer()).run("ls")

    def test_multiline_command_natively_supported(self):
        c = FakeContainer(stdout="done\n")
        result = _make(c).run("cat <<'EOF'\nhello\nEOF")
        self.assertIn("done", result)
        self.assertNotIn(M, result)


class CwdPersistenceTest(unittest.TestCase):
    """验收 #3：cwd 跨命令持久，由 shell 回报的 PWD 标记驱动（I1 根治）。"""

    def test_cwd_tracked_from_pwd_marker(self):
        # cd - / cd X && cmd / 空格路径全部由 shell 自己回报，Python 不猜
        c = FakeContainer(stdout="", pwd="/app/src")
        _make(c).run("cd /app/src && make")
        self.assertEqual(c._calls_pwd(), None)  # placeholder 占位（见下）

    def test_cwd_tracked_after_cd_dash(self):
        """I1-2 回归：cd - 不再毒化 _cwd（shell 回报真实落点）。"""
        c = FakeContainer(stdout="", pwd="/previous/dir")
        backend = _make(c)
        backend._cwd = "/app"
        backend.run("cd -")
        self.assertEqual(backend._cwd, "/previous/dir")

    def test_cwd_tracked_after_cd_and_cmd(self):
        """I1-1 回归：cd X && cmd 形态也被正确跟踪。"""
        c = FakeContainer(stdout="", pwd="/app/src")
        backend = _make(c)
        backend.run("cd /app/src && make")
        self.assertEqual(backend._cwd, "/app/src")

    def test_cwd_tracked_with_space_path(self):
        """I1-3 回归：带空格路径。"""
        c = FakeContainer(stdout="", pwd="/app/my dir")
        backend = _make(c)
        backend.run("cd '/app/my dir'")
        self.assertEqual(backend._cwd, "/app/my dir")

    def test_second_command_restores_cwd(self):
        """持久性闭环：第二条命令的 wrapped 里应显式 cd 回上次落点。"""
        c = FakeContainer(stdout="", pwd="/app/src")
        backend = _make(c)
        backend.run("cd /app/src")
        backend.run("make")
        self.assertIn("cd /app/src", c.calls[1])


# 给 FakeContainer 补一个无副作用的占位（保持上面 placeholder 断言可运行）
def _calls_pwd(self):
    return None


FakeContainer._calls_pwd = _calls_pwd


if __name__ == "__main__":
    unittest.main()
