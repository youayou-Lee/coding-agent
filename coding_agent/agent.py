"""coding_agent v0.1: the coding agent itself — Agno + terminal/file tools + logging.

Tools: send_command (shell), read_file, write_file, list_files.
Every call is logged to RunLogger; the run loop is Agno's (tool_call_limit
is the hard boundary, failure feedback flows back automatically).
"""

from pathlib import Path

from agno.agent import Agent
from agno.tools.decorator import tool

from coding_agent.agno_compat import OpenAICompatChat
from coding_agent.config import LLM_MODEL
from coding_agent.logging_util import RunLogger
from coding_agent.terminal import LocalBackend, TerminalBackend


SYSTEM_INSTRUCTIONS = [
    "你是编程 Agent，在给定的工作目录里完成用户的开发任务。",
    "工作方法：先探索（list_files / 读关键文件）→ 理解现状 → 制定方案 → 实现 → 运行命令验证。",
    "命令失败时，阅读错误输出并修复（测试失败信息会回流给你）。",
    "不要假设文件内容，先读再改。",
    "任务完成后，用一条消息总结你做了什么、验证结果如何。",
]


def make_coding_agent(
    backend: TerminalBackend,
    logger: RunLogger,
    *,
    max_steps: int = 20,
    workdir: Path | None = None,
    debug: bool = False,
) -> Agent:
    workdir = workdir or Path.cwd()

    @tool
    def send_command(command: str) -> str:
        """在项目终端里执行一条 shell 命令并返回完整输出。参数 command: 要执行的命令。"""
        result = backend.run(command)
        logger.tool_call("send_command", {"command": command}, result, ok=True, step=0)
        return result

    @tool
    def list_files(path: str = ".") -> str:
        """列出目录内容（含文件大小和修改时间）。参数 path: 目录路径。"""
        try:
            target = (workdir / path).resolve()
            if not str(target).startswith(str(workdir.resolve())):
                return "(拒绝：路径超出工作目录)"
            entries = []
            for p in sorted(target.iterdir()):
                kind = "DIR " if p.is_dir() else "FILE"
                entries.append(f"{kind} {p.name} ({p.stat().st_size}B)")
            result = "\n".join(entries) or "(空目录)"
        except Exception as exc:
            result = f"(错误: {exc})"
        logger.tool_call("list_files", {"path": path}, result, ok=not result.startswith("(错误"), step=0)
        return result

    @tool
    def read_file(path: str) -> str:
        """读取一个文本文件的完整内容。参数 path: 相对于工作目录的文件路径。"""
        try:
            target = (workdir / path).resolve()
            if not str(target).startswith(str(workdir.resolve())):
                return "(拒绝：路径超出工作目录)"
            content = target.read_text(encoding="utf-8")
            result = content if len(content) <= 6000 else content[:6000] + "\n...(截断)"
        except Exception as exc:
            result = f"(错误: {exc})"
        logger.tool_call("read_file", {"path": path}, result, ok=not result.startswith("(错误"), step=0)
        return result

    @tool
    def write_file(path: str, content: str) -> str:
        """写入/覆盖一个文本文件。参数 path: 相对路径, content: 完整内容。"""
        try:
            target = (workdir / path).resolve()
            if not str(target).startswith(str(workdir.resolve())):
                return "(拒绝：路径超出工作目录)"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            result = f"已写入 {target.relative_to(workdir)} ({len(content)} chars)"
        except Exception as exc:
            result = f"(错误: {exc})"
        logger.tool_call("write_file", {"path": path}, result, ok=not result.startswith("(错误"), step=0)
        return result

    return Agent(
        name="编程Agent",
        model=OpenAICompatChat(
            id=LLM_MODEL,
            logger=logger,
        ),
        tools=[send_command, list_files, read_file, write_file],
        instructions=SYSTEM_INSTRUCTIONS,
        tool_call_limit=max_steps,
        debug_mode=debug,
    )
