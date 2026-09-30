"""MediumBOOM verification actions with distinct cost/yield profiles.

These are *yield models* of the existing CHIA nodes (riscv-tests torture,
riscv-dv, Verilator) plus the new formal node. A cluster run replaces
``simulate_batch`` with real node dispatch; the planner and accounting
stay identical.

Profiles (proposal table):
  torture            cheap carpet-bomb, saturates early
  riscv_dv           constrained-random, strong mid-range
  constraint_tuning  almost free, re-aims a stalled engine
  directed           expensive sniper at named uncovered points
  formal             prove-hit or retire unreachable
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any

from .coverage_model import CoverPoint, FrozenModel
from .yield_table import Action


@dataclass
class Allocation:
    action: Action
    hour_budget: float
    target_pids: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass
class BatchResult:
    action: Action
    hours: float
    hits: list[str]
    retired: list[str]
    notes: str = ""


@dataclass
class KnobState:
    """Constraint / test-class weights the tuner writes and CRV reads."""

    unit_boost: dict[str, float] = field(default_factory=lambda: {"lsu": 1.0, "bpred": 1.0, "issue_q": 1.0})
    freshness: float = 0.0


def _open_points(model: FrozenModel, covered: set[str], retired: set[str]) -> list[CoverPoint]:
    hidden = covered | retired
    return [p for p in model.points if p.pid not in hidden]


def _sample_hits(rng: random.Random, candidates: list[CoverPoint], p: float, cap: int) -> list[str]:
    if not candidates or p <= 0 or cap <= 0:
        return []
    # Poisson-binomial approximation: take expected count then shuffle.
    expected = min(cap, max(0, rng.gauss(len(candidates) * p, math.sqrt(max(len(candidates) * p * (1 - p), 1e-6)))))
    k = int(max(0, min(len(candidates), round(expected))))
    if k == 0:
        return []
    return [p.pid for p in rng.sample(candidates, k)]


def simulate_batch(
    model: FrozenModel,
    covered: set[str],
    retired: set[str],
    alloc: Allocation,
    knobs: KnobState,
    rng: random.Random,
) -> tuple[BatchResult, KnobState]:
    hours = max(0.01, float(alloc.hour_budget))
    open_pts = _open_points(model, covered, retired)
    by_bucket: dict[str, list[CoverPoint]] = {"easy": [], "medium": [], "hard": [], "unreachable": []}
    for p in open_pts:
        by_bucket[p.bucket].append(p)

    if alloc.action == "constraint_tuning":
        units_present = {p.unit for p in open_pts}
        for u in knobs.unit_boost:
            knobs.unit_boost[u] = 1.55 if u in units_present else 0.85
        # Extra boost toward the most uncovered unit.
        counts: dict[str, int] = {}
        for p in open_pts:
            counts[p.unit] = counts.get(p.unit, 0) + 1
        if counts:
            hot = max(counts, key=counts.get)  # type: ignore[arg-type]
            knobs.unit_boost[hot] = 2.1
        knobs.freshness = 1.0
        return BatchResult("constraint_tuning", hours, [], [], "reweighted CRV knobs"), knobs

    hits: list[str] = []
    retired_ids: list[str] = []
    notes = ""

    if alloc.action == "torture":
        sat = 1.0 - math.exp(-3.2 * (1.0 - len(by_bucket["easy"]) / max(1, sum(1 for p in model.points if p.bucket == "easy"))))
        p_easy = 0.42 * (1.0 - 0.85 * sat)
        p_med = 0.035 * (1.0 - 0.5 * sat)
        p_hard = 0.003
        cap = int(hours * 220)
        hits += _sample_hits(rng, by_bucket["easy"], p_easy, cap)
        hits += _sample_hits(rng, by_bucket["medium"], p_med, max(4, cap // 8))
        hits += _sample_hits(rng, by_bucket["hard"], p_hard, max(1, cap // 40))
        notes = "riscv-tests torture carpet bomb"

    elif alloc.action == "riscv_dv":
        boost = 0.35 * knobs.freshness + 0.15 * (max(knobs.unit_boost.values()) - 1.0)
        p_easy = 0.22 + 0.05 * boost
        p_med = 0.20 + 0.18 * boost
        p_hard = 0.018 + 0.02 * boost
        # Prefer the boosted unit.
        def _weighted(bucket: str) -> list[CoverPoint]:
            pts = by_bucket[bucket]
            if not pts:
                return pts
            hot = [p for p in pts if knobs.unit_boost.get(p.unit, 1.0) >= 1.4]
            return hot if hot and knobs.freshness > 0 else pts

        cap = int(hours * 90)
        hits += _sample_hits(rng, _weighted("easy"), p_easy, cap)
        hits += _sample_hits(rng, _weighted("medium"), p_med, cap)
        hits += _sample_hits(rng, _weighted("hard"), p_hard, max(2, cap // 6))
        knobs.freshness *= 0.45
        notes = "riscv-dv constrained-random"

    elif alloc.action == "directed":
        wanted = alloc.target_pids or [p["pid"] for p in []]
        if not wanted:
            # If the caller forgot targets, snipe remaining hard/medium.
            wanted = [p.pid for p in (by_bucket["hard"] + by_bucket["medium"])[:8]]
        by_id = model.by_id()
        cost_each = 0.12
        max_n = max(1, int(hours / cost_each))
        aimed = wanted[:max_n]
        for pid in aimed:
            pt = by_id.get(pid)
            if pt is None or pid in covered or pid in retired:
                continue
            if pt.bucket == "unreachable":
                continue  # directed cannot invent a hit
            p_hit = {"easy": 0.95, "medium": 0.88, "hard": 0.72}[pt.bucket]
            if rng.random() < p_hit:
                hits.append(pid)
        hours = max(hours, len(aimed) * cost_each)
        notes = f"directed assembly at {len(aimed)} named points"

    elif alloc.action == "formal":
        by_id = model.by_id()
        wanted = alloc.target_pids
        if not wanted:
            # Prefer SVA, then remaining open points.
            sva = [p.pid for p in open_pts if p.kind == "sva"]
            wanted = sva[:12] or [p.pid for p in open_pts[:12]]
        cost_each = 0.10
        max_n = max(1, int(hours / cost_each))
        aimed = wanted[:max_n]
        for pid in aimed:
            pt = by_id.get(pid)
            if pt is None or pid in covered or pid in retired:
                continue
            if pt.bucket == "unreachable":
                if rng.random() < 0.78:
                    retired_ids.append(pid)
            else:
                p_hit = {"easy": 0.90, "medium": 0.55, "hard": 0.38}[pt.bucket]
                if rng.random() < p_hit:
                    hits.append(pid)
        hours = max(hours, len(aimed) * cost_each)
        notes = f"SymbiYosys BMC/induction on {len(aimed)} properties"
    else:
        raise ValueError(f"unknown action {alloc.action}")

    # Dedup while preserving order.
    seen: set[str] = set()
    uniq_hits = []
    for h in hits:
        if h not in seen:
            seen.add(h)
            uniq_hits.append(h)

    return BatchResult(alloc.action, hours, uniq_hits, retired_ids, notes), knobs
