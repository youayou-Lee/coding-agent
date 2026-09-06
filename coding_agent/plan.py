"""plan.py — 计划-执行分离的数据结构（v0.6 Issue #19）。

增量假设（先生量化验收框架）：纯 ReAct 基线 X → +Plan 目标 X+15pct → +Todo 目标 X+25pct。
设计必须用 TB 数据证明自身价值，达不到即回滚。

Plan = 步骤序列 + 状态机 + 修订计数。
- Plan 阶段：任务开始产出步骤序列（解决"方向漂移/收尾遗漏"）
- Todo 阶段：每项有 pending/done/blocked 状态，执行中勾销 + 可修订（revision 进 trace）
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
        """状态迁移（合法迁移由调用方保证；这里只验证值合法）。"""
        if status not in VALID_STATUS:
            raise ValueError(f"非法状态 {status!r}，合法值：{VALID_STATUS}")
        self.status = status


@dataclass
class Plan:
    """计划：步骤列表 + 修订计数。序列化/反序列化支持 TaskState 持久化。"""

    steps: list[PlanStep] = field(default_factory=list)
    revision: int = 0

    # --- 构造 ---
    @classmethod
    def from_descriptions(cls, descriptions: list[str]) -> Plan:
        return cls(steps=[PlanStep(id=i, description=d) for i, d in enumerate(descriptions, 1)])

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
        """进度摘要（注入模型上下文的紧凑形式）：'2/5 done, 1 blocked'。"""
        done = sum(1 for s in self.steps if s.status == "done")
        blocked = sum(1 for s in self.steps if s.status == "blocked")
        return f"{done}/{len(self.steps)} done" + (f", {blocked} blocked" if blocked else "")

    # --- 修订 ---
    def revise(self, new_descriptions: list[str]) -> None:
        """整体修订：替换全部步骤（保留未完成语义由调用方决定），revision +1 进 trace。"""
        self.steps = [PlanStep(id=i, description=d) for i, d in enumerate(new_descriptions, 1)]
        self.revision += 1

    # --- 上下文注入 ---
    def render(self, *, only_pending: bool = False) -> str:
        """渲染为模型可读的计划文本。

        only_pending=True 时只列未完成项（上下文预算保护）。
        """
        steps = self.pending_steps if only_pending else self.steps
        lines = [f"[计划 rev{self.revision} | {self.progress}]"]
        for s in steps:
            mark = {"pending": " ", "done": "x", "blocked": "!"}[s.status]
            lines.append(f"  [{mark}] #{s.id} {s.description}")
        return "\n".join(lines)

    # --- 持久化 ---
    def to_dict(self) -> dict:
        return {
            "revision": self.revision,
            "steps": [{"id": s.id, "description": s.description, "status": s.status} for s in self.steps],
        }

    @classmethod
    def from_dict(cls, d: dict) -> Plan:
        steps = [PlanStep(id=s["id"], description=s["description"], status=s["status"]) for s in d["steps"]]
        return cls(steps=steps, revision=d.get("revision", 0))
