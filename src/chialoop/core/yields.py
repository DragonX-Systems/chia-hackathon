"""Per-node coverage yield used by adaptive scheduling."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterable

from .types import JobResult, JobSpec, MergeOutcome


@dataclass(frozen=True)
class YieldSample:
    compute_hours: float
    new_sound_points: int


@dataclass
class NodeStats:
    recent_window: int
    completed_jobs: int = 0
    total_compute_hours: float = 0.0
    total_new_sound_points: int = 0
    consecutive_zero_yield_jobs: int = 0
    recent: deque[YieldSample] = field(default_factory=deque)

    def record(self, sample: YieldSample) -> None:
        self.completed_jobs += 1
        self.total_compute_hours += sample.compute_hours
        self.total_new_sound_points += sample.new_sound_points
        self.consecutive_zero_yield_jobs = (
            self.consecutive_zero_yield_jobs + 1
            if sample.new_sound_points == 0
            else 0
        )
        self.recent.append(sample)
        while len(self.recent) > self.recent_window:
            self.recent.popleft()

    @property
    def recent_bins(self) -> int:
        return sum(sample.new_sound_points for sample in self.recent)

    @property
    def recent_yield(self) -> float:
        hours = sum(sample.compute_hours for sample in self.recent)
        if hours <= 0:
            return 0.0
        return sum(sample.new_sound_points for sample in self.recent) / hours

    @property
    def historical_yield(self) -> float:
        if self.total_compute_hours <= 0:
            return 0.0
        return self.total_new_sound_points / self.total_compute_hours

    def to_dict(self) -> dict:
        return {
            "completed_jobs": self.completed_jobs,
            "total_compute_hours": self.total_compute_hours,
            "total_new_sound_points": self.total_new_sound_points,
            "consecutive_zero_yield_jobs": self.consecutive_zero_yield_jobs,
            "recent_bins": self.recent_bins,
            "recent_yield": self.recent_yield,
            "historical_yield": self.historical_yield,
            "recent": [asdict(sample) for sample in self.recent],
        }


class YieldTable:
    def __init__(self, nodes: Iterable[str], recent_window: int = 10,
                 node_for: Callable[[JobSpec], str] | None = None) -> None:
        if recent_window <= 0:
            raise ValueError("recent_window must be positive")
        self.recent_window = recent_window
        self.node_for = node_for or (lambda job: job.node_id or f"{job.engine.value}-1")
        self._rows = {node: NodeStats(recent_window=recent_window) for node in nodes}

    def row(self, node: str) -> NodeStats:
        if node not in self._rows:
            self._rows[node] = NodeStats(recent_window=self.recent_window)
        return self._rows[node]

    def record(self, job: JobSpec, result: JobResult, merge: MergeOutcome) -> None:
        self.row(self.node_for(job)).record(
            YieldSample(result.compute_hours, merge.new_sound_points if merge.accepted else 0)
        )

    def to_dict(self) -> dict[str, dict]:
        return {node: row.to_dict() for node, row in sorted(self._rows.items())}
