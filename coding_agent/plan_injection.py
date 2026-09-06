"""plan_injection.py — 每步计划注入（v0.6 #19 C1 修复）。

设计（分叉 1=A）：计划全文必须每步出现在模型上下文的**最新端**。
历史残留会让计划随轮次增加沉向最老端（新近度 75%→30%，实测），恰是
注意力失效区——注入机制就是为此存在的。

实现：不做 Agno 内部钩子（过重），在 invoke 入口拼一条临时 system Message
（副本，不污染历史）。ProviderChat（生产）与 ScriptedChatModel（测试）共用。
"""

from agno.models.message import Message

from coding_agent.plan import Plan

_PLAN_TAG = "[当前计划——每步更新，以此为准]"


def build_plan_message(plan: Plan | None) -> Message | None:
    """构造计划注入消息。plan 为 None（尚未制定）返回 None。"""
    if plan is None:
        return None
    return Message(role="system", content=f"{_PLAN_TAG}\n{plan.render()}")


def inject_plan(messages: list, plan: Plan | None) -> list:
    """返回追加了计划消息的 messages **副本**（不修改原列表=不污染历史）。"""
    plan_msg = build_plan_message(plan)
    if plan_msg is None:
        return messages
    return [*messages, plan_msg]
