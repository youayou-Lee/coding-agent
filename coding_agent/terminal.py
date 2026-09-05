"""coding_agent v0.1: TerminalBackend abstraction.

LocalBackend: subprocess in a workdir — fast, cheap, for development.
TmuxBackend: terminal-bench's TmuxSession inside the Docker sandbox —
for evaluation. The agent never knows which backend it talks to.

失败上报约定（v0.5 #4）：成功返回字符串，失败抛 ToolExecutionError。
exit_code / stderr 原样带上，供 classify_error 的三层信号（异常类型 /
exit_code / 消息模式）使用——不再把错误煮成一锅字符串。
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
            # 超时：进程被杀，exit_code 用 124（timeout 专属码）喂给分类器
            stderr = ""
            if isinstance(exc.stderr, bytes):
                stderr = exc.stderr.decode("utf-8", errors="replace")
            elif exc.stderr:
                stderr = exc.stderr
            raise ToolExecutionError(
                f"命令超时（>{timeout}s）",
                exit_code=124,
                stderr=stderr,
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
    """Adapter over terminal-bench's TmuxSession (Docker sandbox)."""

    def __init__(self, session, *, logger=None) -> None:
        self._session = session
        self._logger = logger

    def run(self, command: str, *, timeout: int = 60) -> str:
        wrapped, was_wrapped = wrap_multiline(command)
        if was_wrapped and self._logger is not None:
            self._logger.event("multi_line_wrapped", original_preview=command[:200])
        self._session.send_keys(
            keys=[wrapped, "Enter"],
            block=True,
            max_timeout_sec=timeout,
        )
        try:
            return self._session.get_incremental_output()
        except Exception as exc:  # pragma: no cover
            return f"(tmux read error: {exc})"
