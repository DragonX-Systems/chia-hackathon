"""Frozen MediumBOOM-shaped coverage universe.

The yardstick is generated once from FROZEN_SEED, written to a meter-owned
file, and never edited by the planner. Point IDs mimic Verilator line/toggle
plus hand-written SVA covers over LSU, branch predictor, and issue queues.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Literal

Kind = Literal["line", "toggle", "sva"]
Unit = Literal["lsu", "bpred", "issue_q"]
Bucket = Literal["easy", "medium", "hard", "unreachable"]

FROZEN_SEED = 20260901
N_POINTS = 500

# Mix matches the proposal: random saturates, a mid-band needs CRV, a
# last-mile needs directed/formal, and a real unreachable tail exists.
BUCKET_SHARES = {
    "easy": 0.52,
    "medium": 0.26,
    "hard": 0.14,
    "unreachable": 0.08,
}
KIND_SHARES = {"line": 0.50, "toggle": 0.30, "sva": 0.20}
UNIT_SHARES = {"lsu": 0.40, "bpred": 0.30, "issue_q": 0.30}


@dataclass(frozen=True)
class CoverPoint:
    pid: str
    kind: Kind
    unit: Unit
    bucket: Bucket
    name: str


@dataclass
class FrozenModel:
    seed: int
    points: list[CoverPoint]
    hmac: str

    def by_id(self) -> dict[str, CoverPoint]:
        return {p.pid: p for p in self.points}


def _hmac(payload: str) -> str:
    return hashlib.sha256(f"meter-only|{payload}".encode()).hexdigest()


def _share_counts(n: int, shares: dict[str, float]) -> dict[str, int]:
    keys = list(shares)
    raw = [shares[k] * n for k in keys]
    counts = [int(x) for x in raw]
    counts[-1] = n - sum(counts[:-1])
    return dict(zip(keys, counts))


def freeze_model(seed: int = FROZEN_SEED, n: int = N_POINTS) -> FrozenModel:
    """Deterministic universe. Called once before any arm runs."""
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

    # Stripe so units/kinds mix rather than forming contiguous blocks.
    points: list[CoverPoint] = []
    for i in range(n):
        kind = kind_tape[(i * 7) % n]
        unit = unit_tape[(i * 11) % n]
        bucket = bucket_tape[(i * 13) % n]
        pid = f"cp_{i:04d}"
        name = f"{unit}.{kind}.{bucket}.{i:04d}"
        points.append(CoverPoint(pid=pid, kind=kind, unit=unit, bucket=bucket, name=name))

    blob = json.dumps([asdict(p) for p in points], sort_keys=True)
    return FrozenModel(seed=seed, points=points, hmac=_hmac(blob + str(seed)))


def dump_model(model: FrozenModel, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "seed": model.seed,
        "hmac": model.hmac,
        "points": [asdict(p) for p in model.points],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")


def load_model(path: Path) -> FrozenModel:
    data = json.loads(path.read_text())
    points = [CoverPoint(**p) for p in data["points"]]
    blob = json.dumps([asdict(p) for p in points], sort_keys=True)
    hmac = _hmac(blob + str(data["seed"]))
    if hmac != data["hmac"]:
        raise ValueError("Frozen coverage model HMAC mismatch — yardstick was altered")
    return FrozenModel(seed=data["seed"], points=points, hmac=hmac)


def uncovered_view(points: Iterable[CoverPoint], covered: set[str], retired: set[str]) -> list[dict]:
    """Planner-visible slice: identity only, no reachability / difficulty."""
    hidden = covered | retired
    return [
        {"pid": p.pid, "kind": p.kind, "unit": p.unit, "name": p.name}
        for p in points
        if p.pid not in hidden
    ]
