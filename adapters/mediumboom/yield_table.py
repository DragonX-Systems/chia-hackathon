"""Legacy per-action coverage-per-hour tracking."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Action = Literal["torture", "riscv_dv", "constraint_tuning", "directed", "formal"]
ACTIONS: tuple[Action, ...] = (
    "torture",
    "riscv_dv",
    "constraint_tuning",
    "directed",
    "formal",
)


@dataclass
class YieldRow:
    action: Action
    hours: float = 0.0
    hits: int = 0
    retired: int = 0
    batches: int = 0
    last_hits: int = 0
    last_retired: int = 0
    last_hours: float = 0.0

    @property
    def coverage_per_hour(self) -> float:
        if self.hours <= 0:
            return 0.0
        return (self.hits + self.retired) / self.hours

    @property
    def last_cph(self) -> float:
        if self.last_hours <= 0:
            return 0.0
        return (self.last_hits + self.last_retired) / self.last_hours


@dataclass
class EngineYieldNode:
    rows: dict[str, YieldRow] = field(default_factory=dict)
    dv_peak_cph: float = 0.0

    def __post_init__(self) -> None:
        for a in ACTIONS:
            self.rows.setdefault(a, YieldRow(action=a))  # type: ignore[arg-type]

    def record(self, action: Action, hours: float, hits: int, retired: int) -> None:
        row = self.rows[action]
        row.hours += hours
        row.hits += hits
        row.retired += retired
        row.batches += 1
        row.last_hits = hits
        row.last_retired = retired
        row.last_hours = hours
        if action == "riscv_dv" and row.last_cph > self.dv_peak_cph:
            self.dv_peak_cph = row.last_cph

    def as_planner_view(self) -> list[dict]:
        return [
            {
                "action": r.action,
                "hours": round(r.hours, 4),
                "hits": r.hits,
                "retired": r.retired,
                "batches": r.batches,
                "coverage_per_hour": round(r.coverage_per_hour, 4),
                "last_cph": round(r.last_cph, 4),
            }
            for r in self.rows.values()
        ]
