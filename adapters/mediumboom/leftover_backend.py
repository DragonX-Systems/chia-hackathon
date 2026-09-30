"""MediumBOOM leftover-rich frozen yardstick with a wall-hour cost model.

CRV saturates well below 90% of what directed+formal can eventually reach:
easy/medium are a minority; hard + unreachable dominate. Hours charged to
the meter are a wall model, not planner ``hour_budget``.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict
from pathlib import Path
from .coverage_model import (
    Bucket,
    CoverPoint,
    FrozenModel,
    Kind,
    Unit,
    _hmac,
    _share_counts,
)
from .engines import Allocation, BatchResult, KnobState, simulate_batch

LEFTOVER_SEED = 20260910
LEFTOVER_N = 5000

BUCKET_SHARES = {
    "easy": 0.22,
    "medium": 0.18,
    "hard": 0.32,
    "unreachable": 0.28,
}
KIND_SHARES = {"line": 0.40, "toggle": 0.25, "sva": 0.35}
UNIT_SHARES = {"lsu": 0.40, "bpred": 0.30, "issue_q": 0.30}

WALL_HOURS = {
    "torture": 0.40,
    "riscv_dv": 0.80,
    "constraint_tuning": 0.02,
    "directed": 1.20,
}


def freeze_leftover_rich(seed: int = LEFTOVER_SEED, n: int = LEFTOVER_N) -> FrozenModel:
    buckets = _share_counts(n, BUCKET_SHARES)
    kinds = _share_counts(n, KIND_SHARES)
    units = _share_counts(n, UNIT_SHARES)

    kind_tape: list[Kind] = []
    for k, c in kinds.items():
        kind_tape.extend([k] * c)  # type: ignore[arg-type]
    unit_tape: list[Unit] = []
    for u, c in units.items():
        unit_tape.extend([u] * c)  # type: ignore[arg-type]
    bucket_tape: list[Bucket] = []
    for b, c in buckets.items():
        bucket_tape.extend([b] * c)  # type: ignore[arg-type]

    points: list[CoverPoint] = []
    for i in range(n):
        kind = kind_tape[(i * 7) % n]
        unit = unit_tape[(i * 11) % n]
        bucket = bucket_tape[(i * 13) % n]
        pid = f"cp_{i:05d}"
        name = f"{unit}.{kind}.{bucket}.{i:04d}"
        points.append(CoverPoint(pid=pid, kind=kind, unit=unit, bucket=bucket, name=name))

    blob = json.dumps([asdict(p) for p in points], sort_keys=True)
    return FrozenModel(seed=seed, points=points, hmac=_hmac(blob + str(seed)))


def _wall_hours(alloc: Allocation) -> float:
    if alloc.action == "formal":
        return 0.30
    if alloc.action == "directed":
        return WALL_HOURS["directed"]
    if alloc.action == "constraint_tuning":
        return WALL_HOURS["constraint_tuning"]
    if alloc.action == "riscv_dv":
        # Hits in simulate_batch scale with hour_budget; wall must scale too
        # or a 2.0 h request would get more hits for the same 0.80 h bill.
        return 0.40 * max(float(alloc.hour_budget), 0.01)
    if alloc.action == "torture":
        return (0.40 / 1.5) * max(float(alloc.hour_budget), 0.01)
    return max(1e-6, float(alloc.hour_budget))


def simulate_leftover_batch(
    model: FrozenModel,
    covered: set[str],
    retired: set[str],
    alloc: Allocation,
    knobs: KnobState,
    rng: random.Random,
    work_dir: Path | None = None,
) -> tuple[BatchResult, KnobState]:
    del work_dir  # hits/retired come from the yield model, not a DUT
    result, knobs = simulate_batch(model, covered, retired, alloc, knobs, rng)
    wall = max(1e-6, _wall_hours(alloc))
    notes = f"{result.notes}; leftover-rich wall_h={wall:.4f}"
    return (
        BatchResult(result.action, wall, result.hits, result.retired, notes),
        knobs,
    )
