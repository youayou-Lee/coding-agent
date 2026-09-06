"""plan.py — 计划-执行分离的数据结构（v0.6 Issue #19，设计 v2）。

增量假设（先生量化验收框架）：纯 ReAct 基线 X → +Plan 目标 X+15pct → +Todo 目标 X+25pct。
设计必须用 TB 数据证明自身价值，达不到即回滚。

根源共识（先生，2026-09-07）：ReAct 三失败（方向漂移/收尾遗漏/重复踩坑）同源于
LLM 注意力非均匀（长上下文首尾偏好）。Plan/Todo 的本质 = 把关键信息结构化后
放到上下文最新端，并通过工具交互形成频繁提醒——工程手段部分缓解，不能根治。

设计定稿（先生拍板）：
- 分叉 1 = A：每步全量注入（context 1M 起步，省 token 无意义；
  保住"后期发现早期问题"的纠错机会）
- 分叉 2 = A：模型主动调 todo 勾销（"步骤是否完成"不可枚举，程序判定是伪命题）
- 分叉 3 = 软约束：完整修订权限 + 修订历史摘要随计划注入（Trace 施压影响决策）
"""

from __future__ import annotations

from dataclasses import dataclass, field

VALID_STATUS = ("pending", "done", "blocked")


@dataclass
class PlanStep:
    """计划步骤：id + 描述 + 状态机。"""

    id: int
    description: str
    status: str = "pending"

    def mark(self, status: str) -> None:
        if status not in VALID_STATUS:
            raise ValueError(f"非法状态 {status!r}，合法值：{VALID_STATUS}")
        self.status = status


@dataclass
class Plan:
    """计划：步骤列表 + 修订计数 + 修订历史（软约束载体）。

    修订历史记录每次 revise 的原因/内容摘要，随计划注入模型上下文——
    让模型"看得见"自己改了多少次计划，形成影响决策的软压力（分叉 3）。
    """

    steps: list[PlanStep] = field(default_factory=list)
    revision: int = 0
    revision_history: list[str] = field(default_factory=list)

    # --- 构造 ---
    @classmethod
    def _coerce_description(cls, item) -> str:
        """容错归一化：模型可能把步骤传成 dict（如 {'info': ..., 'step': 1}）而非 str。

        实测（TB 80 题基线 2026-09-07）：GLM 5.3-flash 会输出结构化 dict 列表，
        pydantic 严格校验直接拒绝 → 整个 agent run 崩溃。描述里的信息是真实的，
        只是形态不对——提取常见键合并保留，比报错更符合工具的容错职责。
        """
        if isinstance(item, str):
            return item.strip()
        if isinstance(item, dict):
            # 常见键优先：info/description/step/task/desc/name/content
            for key in ("info", "description", "desc", "task", "name", "content", "title"):
                if key in item and isinstance(item[key], str) and item[key].strip():
                    prefix = f"[step {item['step']}] " if "step" in item and item["step"] not in (None, "") else ""
                    return f"{prefix}{item[key].strip()}"
            # 兜底：所有标量值拼接
            parts = [f"{k}: {v}" for k, v in item.items() if isinstance(v, (str, int, float))]
            return " | ".join(parts) if parts else str(item)
        return str(item).strip()

    @classmethod
    def from_descriptions(cls, descriptions: list[str]) -> Plan:
        coerced = [cls._coerce_description(d) for d in descriptions]
        return cls(steps=[PlanStep(id=i, description=d) for i, d in enumerate(coerced, 1)])

    # --- Todo 勾销 ---
    def mark_step(self, step_id: int, status: str) -> PlanStep:
        for s in self.steps:
            if s.id == step_id:
                s.mark(status)
                return s
        raise KeyError(f"步骤 {step_id} 不存在（共 {len(self.steps)} 步）")

    def mark_by_description(self, substring: str, status: str) -> PlanStep | None:
        """按描述子串匹配勾销（模型引用计划项时的容错匹配）。"""
        for s in self.steps:
            if substring in s.description:
                s.mark(status)
                return s
        return None

    # --- 状态查询 ---
    @property
    def pending_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.status == "pending"]

    @property
    def progress(self) -> str:
        """进度摘要：'2/5 done, 1 blocked'。"""
        done = sum(1 for s in self.steps if s.status == "done")
        blocked = sum(1 for s in self.steps if s.status == "blocked")
        return f"{done}/{len(self.steps)} done" + (f", {blocked} blocked" if blocked else "")

    # --- 修订（分叉 3：完整权限 + 历史留痕软约束） ---
    def revise(self, new_descriptions: list[str], reason: str = "") -> None:
        """整体修订：替换全部步骤，revision +1，原因进修订历史。

        reason 由模型提供（为什么改计划）——修订历史随计划注入上下文，
        频繁无理由修订会在上下文中可见，形成软压力。
        """
        coerced = [self._coerce_description(d) for d in new_descriptions]
        self.steps = [PlanStep(id=i, description=d) for i, d in enumerate(coerced, 1)]
        self.revision += 1
        self.revision_history.append(f"rev{self.revision}: {reason or '(未说明原因)'}")

    # --- 上下文注入（分叉 1：每步全量 + 修订历史） ---
    def render(self) -> str:
        """渲染为模型可读的计划文本：全量步骤 + 进度 + 修订历史。

        每步注入（分叉 1 = A）。修订历史是软约束的一部分——模型每次
        决策都能看到"我已经改过几次计划"。
        """
        lines = [f"[计划 rev{self.revision} | {self.progress}]"]
        for s in self.steps:
            mark = {"pending": " ", "done": "x", "blocked": "!"}[s.status]
            lines.append(f"  [{mark}] #{s.id} {s.description}")
        if self.revision_history:
            lines.append(f"  修订历史（共 {self.revision} 次，频繁修订请反思计划质量）：")
            for h in self.revision_history:
                lines.append(f"    - {h}")
        return "\n".join(lines)

    # --- 持久化 ---
    def to_dict(self) -> dict:
        return {
            "revision": self.revision,
            "revision_history": list(self.revision_history),
            "steps": [{"id": s.id, "description": s.description, "status": s.status} for s in self.steps],
        }

    @classmethod
    def from_dict(cls, d: dict) -> Plan:
        steps = [PlanStep(id=s["id"], description=s["description"], status=s["status"]) for s in d["steps"]]
        return cls(steps=steps, revision=d.get("revision", 0), revision_history=list(d.get("revision_history", [])))
