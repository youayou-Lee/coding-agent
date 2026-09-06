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
from coding_agent.error_budget import ErrorBudget
from coding_agent.errors import ErrorKind, ToolErrorType, classify_error
from coding_agent.logging_util import RunLogger
from coding_agent.plan import Plan
from coding_agent.provider import ProviderChain, load_providers
from coding_agent.terminal import LocalBackend, TerminalBackend, ToolExecutionError


SYSTEM_INSTRUCTIONS = [
    "你是编程 Agent，在给定的工作目录里完成用户的开发任务。",
    "工作方法：先探索（list_files / 读关键文件）→ 理解现状 → 制定方案 → 实现 → 运行命令验证。",
    "命令失败时，阅读错误输出并修复（测试失败信息会回流给你）。",
    "不要假设文件内容，先读再改。",
    "任务完成后，用一条消息总结你做了什么、验证结果如何。",
    "多步任务（≥3 个动作）必须先制定计划：调用 plan 工具提交步骤清单，之后严格按计划执行，完成一步用 todo 勾销一步（status=done）。执行中发现计划有误时调用 revise_plan 修订（须说明原因），不要默默偏离。每次决策时你都能看到完整计划与修订历史——频繁修订会 visible 在你的上下文里，请先想清楚再动手。",
]

# transient 重试参数：封顶重试次数与指数退避基数（真正"预算"在 #5）
RETRY_MAX = 2
RETRY_BACKOFF_BASE = 1.0  # 1s → 2s


def _format_changepath(kind: ErrorKind, err: ToolExecutionError, *, budget_note: str = "") -> str:
    """permanent 错误的结构化回馈：告诉模型这是硬伤 + 给出换路建议。"""
    stderr = (err.stderr or "").strip()
    return (
        f"[命令失败，永久性错误] {err}\n"
        f"分类：{kind.rule}（重试无效，请换方法）{budget_note}\n"
        f"stderr: {stderr[:500] if stderr else '(无)'}"
    )


def _retry_with_backoff(
    backend: TerminalBackend,
    command: str,
    err: ToolExecutionError,
    logger: RunLogger,
    kind: ErrorKind,
) -> tuple[bool, str]:
    """transient 错误：工具内部自动重试（指数退避），模型无感知。

    每次重试都记 tool_retry 事件，trace 可回放（#6 验收）。
    返回 (ok, result)。

    注意（审核 C1 修复）：进入重试循环即已定性 transient，重试只关心
    "这次是否又失败"，不再对重试错误二次三分类——否则 ToolExecutionError
    不在异常类型表里，重试阶段会退化成只靠 exit_code/stderr 的信号，
    耗尽后把主导 kind 覆盖成 fallback，导致 trace 依据失真。
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
            return True, result
        except ToolExecutionError as retry_err:
            logger.event(
                "tool_retry_failed",
                attempt=i + 1,
                detail=str(retry_err)[:200],
                stderr=(retry_err.stderr or "")[:200],
            )
            err = retry_err
    # 重试耗尽：结构化失败回馈（不再透传裸错误）。kind 保持外层 transient 判定
    # 不失真，但文案需如实说明是"重试未愈"而非"永久性错误"。
    logger.event("tool_retry_exhausted", retry_max=RETRY_MAX, rule=kind.rule)
    stderr = (err.stderr or "").strip()
    return False, (
        f"[命令失败，transient 重试耗尽] {err}\n"
        f"分类：{kind.rule}（重试 {RETRY_MAX} 次未愈，建议换方法）\n"
        f"stderr: {stderr[:500] if stderr else '(无)'}"
    )


def make_coding_agent(
    backend: TerminalBackend,
    logger: RunLogger,
    *,
    max_steps: int = 20,
    workdir: Path | None = None,
    debug: bool = False,
    budget_threshold: int = 3,
    plan_ref: list | None = None,  # 测试观察口：外部可变容器 [Plan|None]，与闭包 plan_state 同步
) -> Agent:
    workdir = workdir or Path.cwd()
    budget = ErrorBudget(threshold=budget_threshold)  # 跨调用错误预算（#5）
    plan_state: Plan | None = None  # v0.6 计划-执行分离（#19）：None=尚未制定

    def _sync_plan_ref():
        # plan_ref[0] 与闭包 plan_state 保持同对象（测试观察 + fake 注入源）
        if plan_ref is not None:
            plan_ref.clear()
            plan_ref.append(plan_state)

    @tool
    def plan(steps: list[str]) -> str:
        """多步任务开始时提交计划：steps 为按执行顺序排列的步骤描述列表。"""
        nonlocal plan_state
        if plan_state is not None:
            # I2 修复：静默重建会清零 revision 计数，软约束可被绕过——拒绝并引导走 revise
            return (
                "(错误: 计划已存在。要修改计划请调用 revise_plan（保留修订历史）；"
                "确实要推倒重来则先说明现有计划的根本性问题再调用 revise_plan)"
            )
        plan_state = Plan.from_descriptions(steps)
        _sync_plan_ref()
        logger.event("plan_created", steps=len(steps), revision=plan_state.revision)
        return f"计划已建立（{len(steps)} 步）。全文已注入你的上下文（[当前计划] 段），请按步骤执行。"

    @tool
    def todo(step_id: int, status: str) -> str:
        """勾销计划步骤：step_id 为计划中的步骤编号，status 取 done/blocked/pending。"""
        if plan_state is None:
            return "(错误: 尚未制定计划，先调用 plan)"
        try:
            step = plan_state.mark_step(step_id, status)
        except (KeyError, ValueError) as exc:
            return f"(错误: {exc})"
        logger.event("todo_marked", step_id=step_id, status=status, progress=plan_state.progress)
        return f"已更新：#{step_id} {step.description} → {status}（进度 {plan_state.progress}，最新计划见 [当前计划] 段）"

    @tool
    def revise_plan(steps: list[str], reason: str = "") -> str:
        """修订计划：执行中发现原计划有误时提交新的完整步骤列表。必须说明 reason（修订历史会注入你的上下文，频繁无因修订可见）。"""
        if plan_state is None:
            return "(错误: 尚未制定计划，先调用 plan)"
        if not reason.strip():
            return "(错误: revise_plan 必须说明 reason——为什么原计划有误。修订历史会注入你的上下文，无因修订可见且影响信任)"
        plan_state.revise(steps, reason=reason)
        _sync_plan_ref()
        logger.event(
            "plan_revised",
            revision=plan_state.revision,
            steps=len(steps),
            reason=reason[:200] or "(未说明)",
        )
        return f"计划已修订至 rev{plan_state.revision}（原因已记录，最新计划见 [当前计划] 段）"

    @tool
    def send_command(command: str) -> str:
        """在项目终端里执行一条 shell 命令并返回完整输出。参数 command: 要执行的命令。"""
        try:
            result = backend.run(command)
            # 成功 = 环境恢复信号：销账（清空全部 transient 计数）
            # 注：命令成功不代表所有环境问题消失，但作为销账信号足够简单可靠——
            # 若错误真的还在，下一次失败会重新计数，最多多花一轮重试
            budget.clear_all_transient()
            logger.tool_call(
                "send_command", {"command": command}, result, ok=True, step=0
            )
            return result
        except ToolExecutionError as err:
            kind = classify_error(err, exit_code=err.exit_code, stderr=err.stderr)
            if kind.type is ToolErrorType.TRANSIENT:
                # #5 错误预算：同类错误跨调用计数；超阈值升级为 permanent 处理
                # （跳过自动重试，直接结构化回模型，防止 20 步预算烧在同一堵墙上）
                count = budget.record(kind)
                if budget.exhausted(kind):
                    logger.event(
                        "error_budget_exhausted",
                        rule=kind.rule,
                        count=count,
                        threshold=budget.threshold,
                        detail=str(err)[:200],
                    )
                    result = _format_changepath(
                        kind, err,
                        budget_note=f"【预算耗尽】同类错误已发生 {count} 次，本次跳过自动重试",
                    )
                    logger.tool_call(
                        "send_command", {"command": command}, result,
                        ok=False, step=0,
                        error_kind=kind.type.value, rule=kind.rule,
                        budget_exhausted=True,
                    )
                    return result
                ok, result = _retry_with_backoff(backend, command, err, logger, kind)
                if ok:
                    budget.clear(kind)  # 成功 = 环境恢复信号，销账
                logger.tool_call(
                    "send_command", {"command": command}, result,
                    ok=ok, step=0,
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
            # semantic：透传给模型判断，但必须含 stderr（审核 I2 修复）——
            # 否则模型只拿到"命令退出码 N"的异常外壳，没有足够信息判断结果对不对
            passthrough = f"{err}\nstderr: {(err.stderr or '(无)')[:500]}"
            logger.tool_call(
                "send_command", {"command": command}, passthrough, ok=False, step=0,
                error_kind=kind.type.value, rule=kind.rule,
            )
            return passthrough

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

    def _plan_provider():
        return plan_state  # 闭包读取最新计划状态（每步注入源）

    providers = load_providers()
    if len(providers) == 1:
        model = OpenAICompatChat(
            id=LLM_MODEL,
            logger=logger,
            plan_provider=_plan_provider,
        )
    else:
        # 多 provider：链式故障切换（重试 + failover）
        model = ProviderChat(ProviderChain(providers), logger=logger, plan_provider=_plan_provider)

    return Agent(
        name="编程Agent",
        model=model,
        tools=[plan, todo, revise_plan, send_command, list_files, read_file, write_file],
        instructions=SYSTEM_INSTRUCTIONS,
        tool_call_limit=max_steps,
        debug_mode=debug,
    )
