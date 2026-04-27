"""Cross-run memory — persists danger flags and diary across deaths."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunRecord:
    run_no: int
    steps: int
    outcome: str  # "died" | "cleared" | "timeout"
    diary: str


@dataclass
class MemoryStore:
    danger_flags: list[str] = field(default_factory=list)
    safe_actions: list[str] = field(default_factory=list)
    runs: list[RunRecord] = field(default_factory=list)
    _max_diary: int = 3

    def add_danger(self, hint: str) -> None:
        if hint and hint not in self.danger_flags:
            self.danger_flags.append(hint)

    def add_safe(self, action_desc: str) -> None:
        if action_desc and action_desc not in self.safe_actions:
            self.safe_actions.append(action_desc)

    def add_run(self, record: RunRecord) -> None:
        self.runs.append(record)
        if len(self.runs) > self._max_diary:
            self.runs = self.runs[-self._max_diary :]

    def to_prompt(self) -> str:
        if not self.danger_flags and not self.runs:
            return ""
        parts: list[str] = ["【前の挑戦から引き継いだ記憶 — 必ず従え】"]
        if self.danger_flags:
            parts.append("⚠ 死亡確認済み禁止事項（これを破ると即死する）:")
            for f in self.danger_flags:
                parts.append(f"  !! {f}")
        if self.safe_actions:
            parts.append("確認済みの安全行動:")
            for s in self.safe_actions:
                parts.append(f"  - {s}")
        if self.runs:
            parts.append("過去の挑戦:")
            for r in self.runs:
                parts.append(f"  Run{r.run_no} ({r.outcome}, {r.steps}ターン): {r.diary}")
        return "\n".join(parts)
