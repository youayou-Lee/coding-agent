"""coding_agent v0.1: TerminalBackend abstraction.

LocalBackend: subprocess in a workdir — fast, cheap, for development.
TmuxBackend: docker exec inside terminal-bench's sandbox — for evaluation.
The agent never knows which backend it talks to.

失败上报约定（v0.5 #4，两个 backend 均兑现）：成功返回字符串，失败抛
ToolExecutionError。exit_code / stderr 原样带上，供 classify_error 的
三层信号（异常类型 / exit_code / 消息模式）使用。
"""

import subprocess
from pathlib import Path
from typing import Protocol


class ToolExecutionError(Exception):
    """工具执行失败的结构化异常。

    携带三层线索：exit_code（124/126/127 有专属语义）、stderr（消息模式层用）、
    原生异常消息（异常类型层用）。区别于 returncode 非零：后者是"进程跑完、
    报告失败"，本异常是"失败要上报给 agent 层"的信号通道。
    """

    def __init__(
        self,
        message: str,
        *,
        exit_code: int | None = None,
        stderr: str = "",
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.stderr = stderr


class TerminalBackend(Protocol):
    def run(self, command: str, *, timeout: int = 60) -> str: ...


class LocalBackend:
    """Run commands in a fixed workdir (NOT a sandbox — dev only)."""

    def __init__(self, workdir: Path) -> None:
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)

    def run(self, command: str, *, timeout: int = 60) -> str:
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=self.workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            # 超时：进程被杀，exit_code 用 124（timeout 专属码）喂给分类器。
            # 注意（审核 C2 修复）：capture_output=True 下超时时
            # TimeoutExpired.stdout/stderr 恒为 None（Python 行为），
            # 部分输出不可得，stderr 如实留空——不写误导性的 bytes 分支。
            raise ToolExecutionError(
                f"命令超时（>{timeout}s）",
                exit_code=124,
                stderr="",
            ) from exc

        if proc.returncode != 0:
            # 退出码非零：命令跑完但失败，抛结构化异常（permanent/transient 交给分类器）
            raise ToolExecutionError(
                f"命令退出码 {proc.returncode}",
                exit_code=proc.returncode,
                stderr=proc.stderr or "",
            )

        out = (proc.stdout or "") + (proc.stderr or "")
        return out.strip() or f"(exit code {proc.returncode}, no output)"


import base64


def wrap_multiline(command: str) -> tuple[str, bool]:
    """多行命令 base64 单行化（bug#1 修复，2026-08-31 解剖确认）。

    .. deprecated:: v0.5 (Issue #12 方案 D)
        TmuxBackend 已改走 container.exec_run（bash -c 多行天然合法），
        本函数在生产路径不再被调用，仅保留供历史 trace 复现。

    机制：TB send-keys 把含换行的字符串逐行敲入，第一行立刻执行，
    shell 进入 PS2 续行状态，完成信号 "; tmux wait -S done" 被当作
    heredoc 正文吸进文件 → 完成通知永不到达 → 死等到超时。

    解法：整段命令 base64 编码后作为单行发送，永不触发续行。
    返回 (实际要发送的命令, 是否包装过)。纯函数，可独立测试。
    """
    if "\n" not in command:
        return command, False
    b64 = base64.b64encode(command.encode("utf-8")).decode("ascii")
    return f"echo {b64} | base64 -d | bash", True


class TmuxBackend:
    """在 terminal-bench 的 Docker 沙箱容器内执行命令（评测用）。

    v0.5 方案 D（Issue #12，先生拍板）：不再用 tmux 敲键盘模拟人类操作，
    改走 session.container.exec_run()（docker SDK）——
    - 真实 exit_code（结构化，与 LocalBackend 契约完全对齐）
    - stdout/stderr 分离
    - 多行命令天然合法（bash -c），v0.4 的 base64 单行化补丁退役
    - 容器内 timeout 包裹 → 超时 exit 124，与分类器规则天然对接

    TB 评分不受影响：harness 在任务目录自己跑 pytest + parser，
    与 agent 用什么通道执行命令无关；回放证据依赖 events.jsonl trace。

    cwd 语义：exec 每次新进程，本 backend 维护 _cwd 状态，
    每条命令显式 cd 恢复（对齐 tmux 的“shell 持续存活”语义）。
    """

    _EXIT_MARKER = "__CODING_AGENT_EXIT:"
    _PWD_MARKER = "__CODING_AGENT_PWD:"

    def __init__(self, session, *, logger=None) -> None:
        self._session = session
        self._container = session.container
        self._logger = logger
        self._cwd: str | None = None  # None = 容器默认目录（首次执行时探测）

    def _build_wrapped(self, command: str, timeout: int) -> str:
        """把用户命令包进：cd 恢复 + timeout 包裹 + 退出码标记行 + PWD 标记行。"""
        import shlex

        cd_part = f"cd {shlex.quote(self._cwd)} && " if self._cwd else ""
        inner = f"{cd_part}{{ {command}; }}"
        return (
            f"timeout {timeout}s bash -c {shlex.quote(inner)}; "
            f"ec=$?; printf '%s%s\\n' '{self._EXIT_MARKER}' \"$ec\"; "
            f"printf '%s%s\\n' '{self._PWD_MARKER}' \"$PWD\""
        )

    @staticmethod
    def _split_tail_markers(stdout: str) -> tuple[str, int | None, str | None]:
        """从 stdout 末尾剥掉退出码 + PWD 两个标记行。

        返回 (净输出, 退出码, 真实 cwd)。末尾顺序固定：EXIT 标记在前、PWD 在后；
        逐个从末尾弹出，缺失则对应值为 None（调用方决定如何处理缺失）。
        """
        lines = stdout.rstrip("\n").split("\n")
        pwd: str | None = None
        exit_code: int | None = None

        if lines and lines[-1].startswith(TmuxBackend._PWD_MARKER):
            pwd = lines.pop().removeprefix(TmuxBackend._PWD_MARKER).strip() or None
        if lines and lines[-1].startswith(TmuxBackend._EXIT_MARKER):
            raw = lines.pop().removeprefix(TmuxBackend._EXIT_MARKER).strip()
            try:
                exit_code = int(raw)
            except ValueError:
                exit_code = None
        # 清除输出中任何疑似标记的行（防命令故意 echo 标记污染解析）
        lines = [
            ln
            for ln in lines
            if not ln.startswith(TmuxBackend._EXIT_MARKER)
            and not ln.startswith(TmuxBackend._PWD_MARKER)
        ]
        return "\n".join(lines).strip(), exit_code, pwd

    def run(self, command: str, *, timeout: int = 60) -> str:
        wrapped = self._build_wrapped(command, timeout)
        try:
            result = self._container.exec_run(
                ["bash", "-lc", wrapped], workdir=None, demux=True
            )
        except Exception as exc:  # 容器/连接层故障（非命令失败）
            raise ToolExecutionError(
                f"docker exec 故障: {exc}", exit_code=None, stderr=str(exc)
            ) from exc

        stdout = (result.output[0] or b"").decode("utf-8", errors="replace") if result.output else ""
        stderr = (result.output[1] or b"").decode("utf-8", errors="replace") if result.output else ""
        # C1 修复：标记解析只对 stdout。stderr 是独立的证据通道，永不混入——
        # 否则 stderr 非空时末行不是标记，失败命令会被误判为“标记丢失”而吞成成功。
        net, exit_code, real_cwd = self._split_tail_markers(stdout)

        if exit_code is None:
            # stdout 无标记行（docker 实测：printf 是外层最后命令，外层 exit 恒 0，
            # 标记必然进 stdout 末行；缺标记 = cd 失败/timeout 杀 bash/异常截断）。
            # 此时唯一的结构化事实是外层退出码。
            code = result.exit_code
            if code == 124:
                raise ToolExecutionError(
                    f"命令超时（>{timeout}s）", exit_code=124, stderr=net or stderr
                )
            if code == 0:
                # 标记丢失但外层退出码 0：如实按成功处理（不应制造假错误）
                return net or "(exit code 0, no output)"
            raise ToolExecutionError(
                f"cd 到 {self._cwd} 失败或标记丢失（exit {code}）",
                exit_code=code,
                stderr=net or stderr,
            )

        if exit_code == 124:
            raise ToolExecutionError(f"命令超时（>{timeout}s）", exit_code=124, stderr=net)
        if exit_code != 0:
            raise ToolExecutionError(
                f"命令退出码 {exit_code}", exit_code=exit_code, stderr=stderr or net
            )

        # 成功：跟踪 cd 语义。I1 根治：PWD 标记由 shell 回报真实 cwd，
        # cd - / cd X && cmd / 空格路径全部正确（废除 Python 正则猜）。
        if real_cwd and real_cwd.startswith("/"):
            self._cwd = real_cwd
        return net or "(exit code 0, no output)"
