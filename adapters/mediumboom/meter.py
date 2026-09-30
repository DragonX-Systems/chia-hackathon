"""Legacy MediumBOOM CoverageMeterNode with an isolated frozen collection.

The planner may query a filtered uncovered view. It cannot write cover
points, change HMAC, or mark difficulty. Hits and formal retirements enter
only through ``merge_batch``, which re-checks the frozen HMAC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from chialoop.compat import ChiaFunction
from .coverage_model import FrozenModel, dump_model, freeze_model, load_model, uncovered_view


@dataclass
class MeterSnapshot:
    hours: float
    n_total: int
    n_covered: int
    n_retired: int
    hit_frac: float
    closure_frac: float
    uncovered: list[dict]


@dataclass
class CoverageMeterNode:
    store_path: Path
    model: FrozenModel = field(init=False)
    covered: set[str] = field(default_factory=set)
    retired: set[str] = field(default_factory=set)
    hours: float = 0.0

    def __post_init__(self) -> None:
        self.store_path = Path(self.store_path)
        if self.store_path.exists():
            self.model = load_model(self.store_path)
        else:
            self.model = freeze_model()
            dump_model(self.model, self.store_path)

    def _assert_frozen(self) -> None:
        on_disk = load_model(self.store_path)
        if on_disk.hmac != self.model.hmac:
            raise ValueError("Yardstick HMAC changed under the meter")

    @ChiaFunction(resources={"coverage_meter": 1})
    def snapshot(self) -> MeterSnapshot:
        self._assert_frozen()
        n = len(self.model.points)
        n_cov = len(self.covered)
        n_ret = len(self.retired)
        return MeterSnapshot(
            hours=self.hours,
            n_total=n,
            n_covered=n_cov,
            n_retired=n_ret,
            hit_frac=n_cov / n,
            closure_frac=(n_cov + n_ret) / n,
            uncovered=uncovered_view(self.model.points, self.covered, self.retired),
        )

    @ChiaFunction(resources={"coverage_meter": 1})
    def merge_batch(self, hits: list[str], retired: list[str], hours: float) -> MeterSnapshot:
        """Only legal mutation path. Unknown IDs are dropped."""
        self._assert_frozen()
        known = {p.pid for p in self.model.points}
        by_id = self.model.by_id()
        for pid in hits:
            if pid in known and by_id[pid].bucket != "unreachable":
                if pid not in self.retired:
                    self.covered.add(pid)
        for pid in retired:
            if pid in known and by_id[pid].bucket == "unreachable":
                self.covered.discard(pid)
                self.retired.add(pid)
        self.hours += hours
        return self.snapshot()
