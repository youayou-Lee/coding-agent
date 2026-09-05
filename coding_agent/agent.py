"""coding_agent v0.1: the coding agent itself — Agno + terminal/file tools + logging.

Tools: send_command (shell), read_file, write_file, list_files.
Every call is logged to RunLogger; the run loop is Agno's (tool_call_limit
is the hard boundary, failure feedback flows back automatically).

v0.5 #4：send_command 接住 ToolExecutionError → 体检（classify_error）→ 路由（方案 A）：
  transient（会自己好）→ 工具内部自动重试，模型无感知；
  permanent（硬伤）   → 结构化"换路建议"立即回给模型；
  semantic  （跑通但结果可疑）→ 原样透传交模型判断。
"""

import time
from pathlib import Path

from agno.agent import Agent
from agno.tools.decorator import tool

from coding_agent.agno_compat import OpenAICompatChat, ProviderChat
from coding_agent.config import LLM_MODEL
from coding_agent.errors import ErrorKind, ToolErrorType, classify_error
from coding_agent.logging_util import RunLogger
from coding_agent.provider import ProviderChain, load_providers
from coding_agent.terminal import LocalBackend, TerminalBackend, ToolExecutionError


SYSTEM_INSTRUCTIONS = [
    "你是编程 Agent，在给定的工作目录里完成用户的开发任务。",
    "工作方法：先探索（list_files / 读关键文件）→ 理解现状 → 制定方案 → 实现 → 运行命令验证。",
    "命令失败时，阅读错误输出并修复（测试失败信息会回流给你）。",
    "不要假设文件内容，先读再改。",
    "任务完成后，用一条消息总结你做了什么、验证结果如何。",
]

# transient 重试参数：封顶重试次数与指数退避基数（真正"预算"在 #5）
RETRY_MAX = 2
RETRY_BACKOFF_BASE = 1.0  # 1s → 2s


def _format_changepath(kind: ErrorKind, err: ToolExecutionError) -> str:
    """permanent 错误的结构化回馈：告诉模型这是硬伤 + 给出换路建议。"""
    return (
        f"[命令失败，永久性错误] {err}\n"
        f"分类：{kind.rule}（重试无效，请换方法）\n"
        f"stderr: {err.stderr[:500] if err.stderr else '(无)'}"
    )


def _retry_with_backoff(
    backend: TerminalBackend,
    command: str,
    err: ToolExecutionError,
    logger: RunLogger,
    kind: ErrorKind,
) -> str:
    """transient 错误：工具内部自动重试（指数退避），模型无感知。

    每次重试都记 tool_retry 事件，trace 可回放（#6 验收）。
    返回：重试成功的结果，或重试耗尽后的结构化失败信息。
    """
    logger.event(
        "tool_retry",
        command_preview=command[:200],
        rule=kind.rule,
        retry_max=RETRY_MAX,
        detail=str(err)[:200],
    )
    for i in range(RETRY_MAX):
        delay = RETRY_BACKOFF_BASE * (2**i)
        time.sleep(delay)
        try:
            result = backend.run(command)
            logger.event("tool_retry_ok", attempt=i + 1, command_preview=command[:200])
            return result
        except ToolExecutionError as retry_err:
            rekind = classify_error(
                retry_err, exit_code=retry_err.exit_code, stderr=retry_err.stderr
            )
            logger.event(
                "tool_retry_failed",
                attempt=i + 1,
                rule=rekind.rule,
                detail=str(retry_err)[:200],
            )
            err = retry_err
            kind = rekind
    # 重试耗尽：升级为结构化失败回馈（不再透传裸错误）
    logger.event("tool_retry_exhausted", retry_max=RETRY_MAX, rule=kind.rule)
    return _format_changepath(kind, err)


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
        try:
            result = backend.run(command)
            logger.tool_call(
                "send_command", {"command": command}, result, ok=True, step=0
            )
            return result
        except ToolExecutionError as err:
            kind = classify_error(err, exit_code=err.exit_code, stderr=err.stderr)
            if kind.type is ToolErrorType.TRANSIENT:
                result = _retry_with_backoff(backend, command, err, logger, kind)
                logger.tool_call(
                    "send_command", {"command": command}, result,
                    ok=not result.startswith("[命令失败"), step=0,
                    error_kind=kind.type.value, rule=kind.rule,
                )
                return result
            if kind.type is ToolErrorType.PERMANENT:
                result = _format_changepath(kind, err)
                logger.tool_call(
                    "send_command", {"command": command}, result, ok=False, step=0,
                    error_kind=kind.type.value, rule=kind.rule,
                )
                return result
            # semantic：原样透传（模型才有能力判断结果对不对）
            logger.tool_call(
                "send_command", {"command": command}, str(err), ok=False, step=0,
                error_kind=kind.type.value, rule=kind.rule,
            )
            return str(err)

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

    providers = load_providers()
    if len(providers) == 1:
        model = OpenAICompatChat(
            id=LLM_MODEL,
            logger=logger,
        )
    else:
        # 多 provider：链式故障切换（重试 + failover）
        model = ProviderChat(ProviderChain(providers), logger=logger)

    return Agent(
        name="编程Agent",
        model=model,
        tools=[send_command, list_files, read_file, write_file],
        instructions=SYSTEM_INSTRUCTIONS,
        tool_call_limit=max_steps,
        debug_mode=debug,
    )
