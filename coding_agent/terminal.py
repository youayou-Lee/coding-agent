"""coding_agent v0.1: TerminalBackend abstraction.

LocalBackend: subprocess in a workdir — fast, cheap, for development.
TmuxBackend: terminal-bench's TmuxSession inside the Docker sandbox —
for evaluation. The agent never knows which backend it talks to.
"""

import subprocess
from pathlib import Path
from typing import Protocol


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
            out = (proc.stdout or "") + (proc.stderr or "")
            return out.strip() or f"(exit code {proc.returncode}, no output)"
        except subprocess.TimeoutExpired:
            return f"(timeout after {timeout}s)"
        except Exception as exc:  # pragma: no cover
            return f"(error: {exc})"


class TmuxBackend:
    """Adapter over terminal-bench's TmuxSession (Docker sandbox)."""

    def __init__(self, session) -> None:
        self._session = session

    def run(self, command: str, *, timeout: int = 60) -> str:
        self._session.send_keys(
            keys=[command, "Enter"],
            block=True,
            max_timeout_sec=timeout,
        )
        try:
            return self._session.get_incremental_output()
        except Exception as exc:  # pragma: no cover
            return f"(tmux read error: {exc})"
