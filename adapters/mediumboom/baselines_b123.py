"""MediumBOOM arms: B1 CRV-only, B2 static hybrid, B3 adaptive CHIA.

These are distinct from the legacy ``baseline`` / ``enh_formal`` / ``enh_directed``
ablation arms. B3 must be compared to B2 (same engines), not only to B1.
"""

from __future__ import annotations

import os

from .engines import Allocation
from .formal_prune import directed_targets, formal_targets
from .meter import MeterSnapshot
from .prune import split_leftovers
from .yield_table import EngineYieldNode

B123_ARMS = ("b1", "b2", "b3")

# Frozen B2 schedule (outcome-independent). Override via env for calibration.
def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def b2_crv_batches() -> int:
    return _int_env("B2_CRV_BATCHES", 8)


def b2_directed_batches() -> int:
    return _int_env("B2_DIRECTED_BATCHES", 4)


def b2_formal_batches() -> int:
    return _int_env("B2_FORMAL_BATCHES", 6)


def b1_crv_only_plan(
    snap: MeterSnapshot, yields: EngineYieldNode, remaining: float
) -> Allocation:
    """B1: constrained-random only. Never directed or formal."""
    if remaining <= 0 or not snap.uncovered:
        return Allocation("riscv_dv", 0.0, reason="b1: closed or no budget")
    return Allocation(
        "riscv_dv",
        min(2.0, remaining),
        reason="b1: CRV-only (no directed, no formal)",
    )


def b2_static_hybrid_plan(
    snap: MeterSnapshot, yields: EngineYieldNode, remaining: float
) -> Allocation:
    """B2: fixed CRV → directed → formal. Batch counts only; no yield redirect."""
    if remaining <= 0 or not snap.uncovered:
        return Allocation("riscv_dv", 0.0, reason="b2: closed or no budget")

    dv = yields.rows["riscv_dv"]
    directed = yields.rows["directed"]
    formal = yields.rows["formal"]
    split = split_leftovers(snap)
    n_crv = b2_crv_batches()
    n_dir = b2_directed_batches()
    n_form = b2_formal_batches()

    if dv.batches < n_crv:
        return Allocation(
            "riscv_dv",
            min(2.0, remaining),
            reason=f"b2: phase CRV {dv.batches}/{n_crv}",
        )

    if directed.batches < n_dir and split.n_crv > 0:
        return Allocation(
            "directed",
            min(1.2, remaining),
            target_pids=directed_targets(split) or [u["pid"] for u in snap.uncovered[:10]],
            reason=f"b2: phase directed {directed.batches}/{n_dir}",
        )

    if formal.batches < n_form and split.n_formal > 0:
        return Allocation(
            "formal",
            min(0.30, remaining),
            target_pids=formal_targets(split),
            reason=f"b2: phase formal {formal.batches}/{n_form}",
        )

    # Schedule exhausted but points remain: keep CRV (still no adaptive redirect).
    return Allocation(
        "riscv_dv",
        min(2.0, remaining),
        reason="b2: schedule exhausted; residual CRV",
    )


def b3_adaptive_plan(
    snap: MeterSnapshot, yields: EngineYieldNode, remaining: float
) -> Allocation:
    """B3: same engines as B2; next job from open debt + measured yield."""
    if remaining <= 0 or not snap.uncovered:
        return Allocation("riscv_dv", 0.0, reason="b3: closed or no budget")

    split = split_leftovers(snap)
    dv = yields.rows["riscv_dv"]
    directed = yields.rows["directed"]
    formal = yields.rows["formal"]
    formal_dry = formal.batches >= 1 and (formal.last_hits + formal.last_retired) == 0
    crv_stalled = dv.batches >= 4 and (
        dv.coverage_per_hour < 3.5 or (dv.last_cph < 0.5 * max(yields.dv_peak_cph, 1e-9))
    )

    # Prefer formal on leftover SVA when the formal arm is not dry.
    if split.n_formal > 0 and not formal_dry and formal.batches < 8:
        # After some CRV, or when only SVA remains.
        if dv.batches >= 2 or split.n_crv == 0:
            return Allocation(
                "formal",
                min(0.30, remaining),
                target_pids=formal_targets(split),
                reason=(
                    f"b3: adaptive formal SVA={split.n_formal} "
                    f"cph={formal.coverage_per_hour:.2f}"
                ),
            )

    # Directed last-mile on non-SVA when CRV stalls or CRV debt remains late.
    if split.n_crv > 0 and (crv_stalled or dv.batches >= 6 or snap.hit_frac >= 0.40):
        if directed.batches < 10:
            return Allocation(
                "directed",
                min(1.2, remaining),
                target_pids=directed_targets(split),
                reason=(
                    f"b3: adaptive directed leftover={split.n_crv} "
                    f"cph={directed.coverage_per_hour:.2f}"
                ),
            )

    return Allocation(
        "riscv_dv",
        min(2.0, remaining),
        reason=f"b3: adaptive CRV closure={snap.closure_frac:.3f}",
    )


PLANS = {
    "b1": b1_crv_only_plan,
    "b2": b2_static_hybrid_plan,
    "b3": b3_adaptive_plan,
}


def get_b123_plan(arm: str):
    try:
        return PLANS[arm]
    except KeyError as exc:
        raise KeyError(f"unknown B1/B2/B3 arm: {arm}") from exc


def b123_manifest() -> dict:
    return {
        "id": "b123-20260922",
        "arms": list(B123_ARMS),
        "b1": "CRV only (riscv_dv)",
        "b2": {
            "schedule": "CRV → directed → formal",
            "crv_batches": b2_crv_batches(),
            "directed_batches": b2_directed_batches(),
            "formal_batches": b2_formal_batches(),
            "adaptive": False,
        },
        "b3": "adaptive yield/debt on same engines as b2",
        "win_condition": "b3 vs b2 on coverage per compute-hour / hours-to-target",
        "not_a_win": "beating b1 alone (withholds directed+formal)",
    }
