"""MediumBOOM fixed recipe: riscv-dv until plateau, then a directed suite.

This is the industrial default the agent has to beat on the curve.
"""

from __future__ import annotations

from .engines import Allocation
from .meter import MeterSnapshot
from .yield_table import EngineYieldNode


def baseline_plan(snap: MeterSnapshot, yields: EngineYieldNode, remaining_hours: float) -> Allocation:
    dv = yields.rows["riscv_dv"]
    stalled = dv.batches >= 4 and dv.coverage_per_hour < 3.5
    # Industrial schedule: after a fixed CRV count, dump the directed suite
    # (including leftover SVA / unreachables). That is the hours sink.
    scheduled_directed = dv.batches >= 8
    late = snap.hit_frac >= 0.78 or stalled or scheduled_directed
    if not late:
        return Allocation(
            "riscv_dv",
            min(2.0, remaining_hours),
            reason="baseline: default-weight riscv-dv scale-out",
        )
    # Directed suite with no formal retirement and no targeting intelligence
    # beyond "whatever is still open" — including unreachable points.
    targets = [u["pid"] for u in snap.uncovered[:10]]
    return Allocation(
        "directed",
        min(1.2, remaining_hours),
        target_pids=targets,
        reason="baseline: fixed directed suite after CRV plateau",
    )
