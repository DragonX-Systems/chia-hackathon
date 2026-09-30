"""Legacy MediumBOOM enhancement pipeline (not catalog oracles-20260907).

Vanilla baseline stays riscv-dv then untargeted directed (including SVA).

``enh_v1`` (id enh-20260909):
  1. Formal prune leftover SVA after CRV hit_frac >= 0.40 (and dv.batches >= 8),
     up to 3 batches; Unreached retire planted unreachable SVA. Not billed
     ahead of the 40% hit bar.
  2. Directed last-mile on non-SVA leftovers only.

Enh 1 (OpenEvolve / genome retune) was removed — it never fired useful
``constraint_tuning`` batches on judged runs.
"""

from __future__ import annotations

from .engines import Allocation
from .formal_prune import directed_targets, formal_targets
from .meter import MeterSnapshot
from .prune import past_crv_80, split_leftovers
from .yield_table import EngineYieldNode

ENHANCEMENT_ID = "enh-20260909"
ENHANCEMENT_ARM = "enh_v1"
# Ablation arms: each enhancement alone, then both.
ENHANCEMENT_MODES = {
    "enh_formal": "formal",
    "enh_directed": "directed",
    "enh_v1": "both",
}


def make_enhancement_plan(mode: str = "both"):
    """``mode``: formal | directed | both."""

    def plan(snap: MeterSnapshot, yields: EngineYieldNode, remaining: float) -> Allocation:
        return enhancement_plan(snap, yields, remaining, mode=mode)

    return plan


def enhancement_plan(
    snap: MeterSnapshot,
    yields: EngineYieldNode,
    remaining: float,
    mode: str = "both",
) -> Allocation:
    use_formal = mode in ("both", "formal")
    use_directed = mode in ("both", "directed")
    tag = {"formal": "enh_formal", "directed": "enh_directed", "both": "enh_v1"}.get(
        mode, "enh_v1"
    )
    split = split_leftovers(snap)
    if remaining <= 0 or split.n_open == 0:
        return Allocation("formal", 0.0, reason=f"{tag}: closed or no budget")

    rows = yields.rows
    dv = rows["riscv_dv"]
    formal, directed = rows["formal"], rows["directed"]
    formal_dry = formal.batches >= 1 and (formal.last_hits + formal.last_retired) == 0
    crv_dead = dv.batches >= 2 and dv.last_cph < 2.0
    crv_stall = dv.batches >= 4 and dv.coverage_per_hour < 3.5
    peak = yields.dv_peak_cph
    last_mile = (
        use_directed
        and bool(split.crv)
        and snap.hit_frac >= 0.40
        and (
            past_crv_80(split)
            or crv_dead
            or crv_stall
            or (dv.batches >= 12 and peak > 0.0 and dv.last_cph < 0.50 * peak)
            or dv.batches >= 16
        )
    )

    def A(action, hours, reason, targets=None, parameters=None) -> Allocation:
        return Allocation(
            action,
            min(hours, remaining),
            target_pids=list(targets or []),
            parameters=dict(parameters or {}),
            reason=reason,
        )

    # Enh 3: last-mile hits on non-SVA. One formal prune after the first directed
    # when this arm is allowed to use formal.
    if last_mile:
        if (
            use_formal
            and split.formal
            and directed.batches >= 1
            and formal.batches == 0
            and (not formal_dry)
        ):
            return A(
                "formal",
                0.15,
                f"{tag}: formal prune SVA={split.n_formal} crv_closed={split.crv_closed_frac:.2f}",
                formal_targets(split),
            )
        return A(
            "directed",
            1.2,
            f"{tag}: directed last-mile CRV leftover={split.n_crv}",
            directed_targets(split),
        )

    # Enh 2: SVA-only leftover (no CRV) → formal, never directed on SVA.
    if use_formal and split.formal and split.n_crv == 0:
        if (not formal_dry) and formal.batches < 3:
            return A(
                "formal",
                0.15,
                f"{tag}: formal prune SVA={split.n_formal} crv_closed={split.crv_closed_frac:.2f}",
                formal_targets(split),
            )
        if split.n_crv == 0:
            return A("formal", 0.05, f"{tag}: leftover SVA after formal — stop recarpet")

    # Up to 3 formal prunes after CRV has already cleared the 40% hit bar so
    # hours-to-40% is unchanged; leftover SVA leave the CRV set. Stop if dry.
    if (
        use_formal
        and split.formal
        and formal.batches < 3
        and (not formal_dry)
        and dv.batches >= 8
        and snap.hit_frac >= 0.40
        and not last_mile
    ):
        return A(
            "formal",
            1.2,
            f"{tag}: formal prune SVA={split.n_formal} after CRV hit={snap.hit_frac:.2f}",
            formal_targets(split),
        )

    return A("riscv_dv", 2.0, f"{tag}: constrained-random closure={snap.closure_frac:.3f}")


def enhancement_manifest() -> dict:
    return {
        "id": ENHANCEMENT_ID,
        "arm": ENHANCEMENT_ARM,
        "catalog_oracles_untouched": "oracles-20260907",
        "baseline": "vanilla CRV (riscv_dv then directed) — unchanged",
        "fitness": "coverage gained per compute-hour (yield table last_cph)",
        "chia": [
            "dynamic graph: crv | formal | directed | artifact_writer",
            "cache: DUT Verilator library stamp; formal Unreached per pid",
            "Enh 1 genome/OpenEvolve removed — never moved judged curves",
        ],
        "roi": "MediumBOOM 51-point SoC+LSU-SVA yardstick (poster); leftover-rich lab only",
        "ablation_arms": list(ENHANCEMENT_MODES),
    }
