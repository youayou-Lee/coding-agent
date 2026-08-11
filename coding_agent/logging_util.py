"""coding_agent v0.1: RunLogger — the observability backbone.

Why a logging system matters here (user requirement): when a coding agent
fails, you must be able to answer — did the model loop forever? which
command errored? did the model emit garbage? how many tokens did it burn?

Every event lands in one JSONL file; replay() lets you inspect afterwards.
"""

import json
import time
from pathlib import Path
from typing import Any


class RunLogger:
    def __init__(self, log_dir: Path) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.log_dir / "events.jsonl"
        self._events: list[dict[str, Any]] = []
        self._llm_seq = 0

    def event(self, event: str, **data: Any) -> None:
        row = {"ts": round(time.time(), 3), "event": event, **data}
        self._events.append(row)
        with open(self.events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def llm_call(self, messages, response_text: str = "", *, step: int | None = None) -> None:
        self._llm_seq += 1
        data = {"llm_seq": self._llm_seq, "prompt_preview": str(messages)[:400]}
        if step is not None:
            data["step"] = step
        self.event("llm_call", response_preview=str(response_text)[:400], **data)

    def llm_response(self, response_text: str, **extra) -> None:
        self.event("llm_response", response_preview=str(response_text)[:400], **extra)

    def tool_call(self, tool: str, args: dict, result: str, *, ok: bool, step: int) -> None:
        self.event(
            "tool_call", step=step, tool=tool, args=args,
            ok=ok, result_preview=str(result)[:500],
        )

    def step_event(self, step: int, note: str) -> None:
        self.event("step", step=step, note=note)

    def final(self, status: str, **data: Any) -> None:
        self.event("final", status=status, **data)

    def replay(self) -> list[dict[str, Any]]:
        return list(self._events)

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for e in self._events:
            counts[e["event"]] = counts.get(e["event"], 0) + 1
        return json.dumps(counts, ensure_ascii=False, sort_keys=True)
