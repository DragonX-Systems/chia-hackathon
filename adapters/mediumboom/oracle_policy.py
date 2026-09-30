"""Named MediumBOOM coverage oracles used by the legacy experiment.

Each oracle is a frozen schedule plus the physics it assumes. After a run we
compare oracles to each other and to the industrial baseline — we do not
edit an oracle to match the curve. If an assumption was wrong, add a new id.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Iterable

from .engines import Allocation
from .meter import MeterSnapshot
from .yield_table import Action, EngineYieldNode

OracleFn = Callable[[MeterSnapshot, EngineYieldNode, float], Allocation]


def _sva_other(uncovered: list[dict]) -> tuple[list[dict], list[dict], bool]:
    sva = [u for u in uncovered if u["kind"] == "sva"]
    other = [u for u in uncovered if u["kind"] != "sva"]
    return sva, other, bool(uncovered) and not other


def _alloc(
    action: Action,
    hours: float,
    remaining: float,
    sva_open: list[dict],
    other_open: list[dict],
    reason: str,
) -> Allocation:
    hours = min(hours, remaining)
    if action == "formal":
        targets = [u["pid"] for u in sva_open[:12]]
    elif action == "directed":
        pool = other_open or sva_open
        targets = [u["pid"] for u in pool[:8]]
    else:
        targets = []
    return Allocation(action, hours, target_pids=targets, reason=reason)


def _done(snap: MeterSnapshot, remaining: float) -> Allocation | None:
    if not snap.uncovered or remaining <= 0:
        return Allocation("formal", 0.05, reason="oracle: nothing left")
    return None


def oracle_sva_first(snap: MeterSnapshot, yields: EngineYieldNode, remaining: float) -> Allocation:
    """Retire-first. Formal owns SystemVerilog Assertion leftovers."""
    stop = _done(snap, remaining)
    if stop:
        return stop
    rows = yields.rows
    torture, dv = rows["torture"], rows["riscv_dv"]
    formal = rows["formal"]
    sva, other, sva_only = _sva_other(snap.uncovered)
    n_open = len(snap.uncovered)
    formal_dry = formal.batches >= 1 and (formal.last_hits + formal.last_retired) == 0
    formal_timeoutish = formal.batches >= 1 and formal.last_hours >= 0.9 and formal.last_cph == 0.0

    def A(action: Action, hours: float, reason: str) -> Allocation:
        return _alloc(action, hours, remaining, sva, other, reason)

    if sva_only:
        if (not formal_dry) and (not formal_timeoutish) and formal.batches < 2:
            return A("formal", 0.15, "sva_first: SVA-only — BMC/induction")
        return A("formal", 0.05, "sva_first: leftover SVA after formal — stop recarpet")

    crv_dead = dv.batches >= 2 and dv.last_cph < 2.0
    last_mile = snap.closure_frac >= 0.80 or (crv_dead and torture.batches >= 1)
    if last_mile:
        if sva and not formal_dry and not formal_timeoutish and formal.batches < 2:
            return A("formal", 0.15, f"sva_first: last-mile formal SVA={len(sva)}")
        return A("directed", 0.8, f"sva_first: last-mile directed open={n_open}")

    if torture.batches >= 1 and (torture.last_cph < 12.0 or torture.hours >= 2.0 or snap.closure_frac >= 0.45):
        if dv.batches >= 2 and dv.last_cph < 3.0 and rows["constraint_tuning"].batches < max(1, dv.batches // 4):
            return A("constraint_tuning", 0.08, "sva_first: CRV collapsed — retune")
        return A("riscv_dv", 1.5, f"sva_first: mid CRV closure={snap.closure_frac:.3f}")
    if torture.batches == 0:
        return A("torture", 1.5, "sva_first: one early carpet-bomb")
    if torture.last_cph >= 12.0 and torture.hours < 3.0:
        return A("torture", 1.5, f"sva_first: torture still yielding last_cph={torture.last_cph:.1f}")
    return A("riscv_dv", 1.5, f"sva_first: leave torture last_cph={torture.last_cph:.1f}")


def oracle_sim_max(snap: MeterSnapshot, yields: EngineYieldNode, remaining: float) -> Allocation:
    """Formal-last. Assume Bounded Model Checking may be huge — spend sim first."""
    stop = _done(snap, remaining)
    if stop:
        return stop
    rows = yields.rows
    torture, dv, tune = rows["torture"], rows["riscv_dv"], rows["constraint_tuning"]
    directed, formal = rows["directed"], rows["formal"]
    sva, other, _sva_only = _sva_other(snap.uncovered)

    def A(action: Action, hours: float, reason: str) -> Allocation:
        return _alloc(action, hours, remaining, sva, other, reason)

    sim_dead = (
        torture.batches >= 1
        and dv.batches >= 2
        and dv.last_cph < 2.0
        and ((directed.batches >= 1 and directed.last_cph < 1.0) or directed.batches >= 2)
    )
    formal_dry = formal.batches >= 1 and (formal.last_hits + formal.last_retired) == 0
    if sim_dead and sva and not formal_dry and formal.batches < 2:
        return A("formal", 0.15, "sim_max: sim engines dead — now formal")

    if torture.batches == 0:
        return A("torture", 2.0, "sim_max: wring torture first")
    if torture.last_cph >= 10.0 and torture.hours < 6.0:
        return A("torture", 2.0, f"sim_max: torture last_cph={torture.last_cph:.1f}")
    if dv.batches == 0 or (dv.last_cph >= 3.0 and dv.hours < 12.0):
        if dv.batches >= 2 and dv.last_cph < 3.0 and tune.batches < max(1, dv.batches // 4):
            return A("constraint_tuning", 0.08, "sim_max: retune CRV")
        return A("riscv_dv", 1.8, f"sim_max: CRV closure={snap.closure_frac:.3f}")
    if dv.last_cph < 3.0 and tune.batches < max(1, dv.batches // 4):
        return A("constraint_tuning", 0.08, "sim_max: CRV collapsed — retune")
    if other or (sva and formal_dry):
        return A("directed", 1.0, "sim_max: directed before paying formal")
    if sva and not formal_dry and formal.batches < 2:
        return A("formal", 0.15, "sim_max: only SVA left")
    return A("directed", 0.5, "sim_max: tail")


def oracle_cph_greedy(snap: MeterSnapshot, yields: EngineYieldNode, remaining: float) -> Allocation:
    """Last-batch coverage-per-hour bandit. No stages. Explore each engine once."""
    stop = _done(snap, remaining)
    if stop:
        return stop
    rows = yields.rows
    sva, other, _ = _sva_other(snap.uncovered)

    def A(action: Action, hours: float, reason: str) -> Allocation:
        return _alloc(action, hours, remaining, sva, other, reason)

    applicable: list[tuple[Action, float]] = []
    if rows["torture"].batches == 0:
        return A("torture", 1.5, "cph_greedy: explore torture")
    if rows["riscv_dv"].batches == 0:
        return A("riscv_dv", 1.5, "cph_greedy: explore CRV")
    if sva and rows["formal"].batches == 0:
        return A("formal", 0.15, "cph_greedy: explore formal (SVA open)")
    if rows["directed"].batches == 0:
        return A("directed", 0.8, "cph_greedy: explore directed")

    if (
        rows["riscv_dv"].batches >= 2
        and rows["riscv_dv"].last_cph < 3.0
        and rows["constraint_tuning"].batches < max(1, rows["riscv_dv"].batches // 4)
    ):
        return A("constraint_tuning", 0.08, "cph_greedy: CRV last-batch collapsed — retune")

    candidates: list[tuple[float, Action, float]] = []
    for action, hours in (
        ("torture", 1.5),
        ("riscv_dv", 1.5),
        ("directed", 0.8),
    ):
        row = rows[action]
        if row.batches == 0:
            continue
        if action == "torture" and row.last_cph < 8.0:
            continue
        candidates.append((row.last_cph, action, hours))
    if sva:
        f = rows["formal"]
        dry = f.batches >= 1 and (f.last_hits + f.last_retired) == 0
        if not dry and f.batches < 3:
            # Untried already returned. Tried: use last_cph, inf if last batch scored.
            score = f.last_cph if f.batches else math.inf
            candidates.append((score, "formal", 0.15))

    if not candidates:
        if sva:
            return A("formal", 0.15, "cph_greedy: fallback formal")
        return A("directed", 0.5, "cph_greedy: fallback directed")
    candidates.sort(reverse=True)
    _score, action, hours = candidates[0]
    return A(action, hours, f"cph_greedy: max last_cph={_score:.2f} → {action}")


def oracle_stage_clean(snap: MeterSnapshot, yields: EngineYieldNode, remaining: float) -> Allocation:
    """Industrial stages without the sticky last-mile latch."""
    stop = _done(snap, remaining)
    if stop:
        return stop
    rows = yields.rows
    torture, dv, tune = rows["torture"], rows["riscv_dv"], rows["constraint_tuning"]
    formal = rows["formal"]
    sva, other, _ = _sva_other(snap.uncovered)
    n_open = len(snap.uncovered)
    formal_dry = formal.batches >= 1 and (formal.last_hits + formal.last_retired) == 0

    def A(action: Action, hours: float, reason: str) -> Allocation:
        return _alloc(action, hours, remaining, sva, other, reason)

    last_mile = snap.closure_frac >= 0.80 or (
        dv.batches >= 2 and dv.last_cph < 2.0 and torture.batches >= 1
    )
    if last_mile:
        if sva and not formal_dry and formal.batches < 2:
            return A("formal", 0.15, f"stage_clean: last-mile formal SVA={len(sva)}")
        return A("directed", 0.8, f"stage_clean: last-mile directed open={n_open}")

    if torture.hours >= 4.0 or snap.closure_frac >= 0.50:
        if dv.batches >= 2 and dv.last_cph < 3.0 and tune.batches < max(1, dv.batches // 4):
            return A("constraint_tuning", 0.08, "stage_clean: CRV collapsed — retune")
        return A("riscv_dv", 1.8, f"stage_clean: mid CRV closure={snap.closure_frac:.3f}")
    if torture.batches == 0:
        return A("torture", 2.0, "stage_clean: early carpet-bomb")
    if torture.last_cph >= 12.0 and torture.hours < 8.0:
        return A("torture", 2.0, f"stage_clean: torture last_cph={torture.last_cph:.1f}")
    return A("riscv_dv", 1.8, f"stage_clean: leave torture last_cph={torture.last_cph:.1f}")


@dataclass(frozen=True)
class OracleSpec:
    name: str
    title: str
    assumption: str
    thinks: str
    will_fail_if: str
    plan: OracleFn


ORACLES: dict[str, OracleSpec] = {
    "sva_first": OracleSpec(
        name="sva_first",
        title="Retire-first",
        assumption="SystemVerilog Assertion leftovers can only close via formal. Sim engines cannot retire. Formal request must stay small so wall seconds are the cost.",
        thinks="If the yardstick is SVA-only, skip torture. On a mixed list, one carpet-bomb, then constrained-random, then formal for SVA and directed for the rest. Last-mile is a yield test, not a latch.",
        will_fail_if="Formal on generated MediumBOOM is so slow that even 0.15h floors waste the budget, or sim engines actually hit the named SVA covers.",
        plan=oracle_sva_first,
    ),
    "sim_max": OracleSpec(
        name="sim_max",
        title="Formal-last",
        assumption="Bounded Model Checking wall time on MediumBOOM is unknown and possibly huge. Hits from torture / constrained-random / directed are cheap relative to a solver timeout.",
        thinks="Wring sim engines until their last-batch coverage-per-hour dies, then pay formal. Directed before formal even on leftover SVA, except when only SVA remain.",
        will_fail_if="The leftover is mostly unreachable SVA — directed cannot hit them, and we burn hours before retiring.",
        plan=oracle_sim_max,
    ),
    "cph_greedy": OracleSpec(
        name="cph_greedy",
        title="Coverage-per-hour bandit",
        assumption="Last-batch coverage-per-hour is a sufficient statistic. Stages are a prior we should not freeze. Explore each applicable engine once, then pick the highest last_cph.",
        thinks="This is the CHIA claim: sequencing is a bandit, not a chat. Formal is only in the candidate set while SVA remain and the last formal batch was not dry.",
        will_fail_if="Last-batch noise (one lucky directed hit) keeps us on a dead engine, or explore-formal on a mixed list spends solver time too early.",
        plan=oracle_cph_greedy,
    ),
    "stage_clean": OracleSpec(
        name="stage_clean",
        title="Industrial stages, no latch",
        assumption="The usual verification flow (carpet → constrained-random → last-mile) is right if last-mile cannot latch just because we already touched formal or directed.",
        thinks="Same stages as the old heuristic, but last-mile only when closure ≥ 80% or constrained-random is dead after torture. Formal 0.15h, at most twice, SVA-only.",
        will_fail_if="Always-on line bins inflate early closure and we enter last-mile before constrained-random has done its job (the Verilator TCM failure mode).",
        plan=oracle_stage_clean,
    ),
}

ORACLE_NAMES: tuple[str, ...] = tuple(ORACLES)
CATALOG_ID = "oracles-20260907"


def get_oracle(name: str) -> OracleSpec:
    if name not in ORACLES:
        raise KeyError(f"unknown oracle {name!r}; choose from {list(ORACLES)}")
    return ORACLES[name]


def catalog() -> dict:
    return {
        "catalog_id": CATALOG_ID,
        "written_before_run": True,
        "oracles": [
            {
                "name": spec.name,
                "title": spec.title,
                "assumption": spec.assumption,
                "thinks": spec.thinks,
                "will_fail_if": spec.will_fail_if,
            }
            for spec in ORACLES.values()
        ],
        "compare": "Run every oracle vs the industrial baseline on the same frozen yardstick and seeds. Audit step-1 picks and hours-to-90% of baseline-final. Do not edit an oracle after seeing the curve.",
    }


def start_picks(snap: MeterSnapshot, remaining: float) -> dict:
    """What each oracle would do on this snapshot (usually step 0)."""
    y = EngineYieldNode()
    picks = {name: spec.plan(snap, y, remaining).action for name, spec in ORACLES.items()}
    return {"catalog_id": CATALOG_ID, "n_open": len(snap.uncovered), "picks": picks}


def dump_catalog(path) -> None:
    import json
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(catalog(), indent=2) + "\n")


# Back-compat aliases used by older tests / docs.
ORACLE_ID = "sva_first"
oracle_plan = oracle_sva_first


def manifesto() -> dict:
    return catalog()


def predicted_boom_disagreements() -> list[dict]:
    return [
        {
            "when": "step 1, empty yields, 6 SVA open",
            "sva_first": "formal",
            "sim_max": "torture",
            "cph_greedy": "torture",
            "stage_clean": "torture",
            "why": "Only sva_first treats an SVA-only list as 'skip the carpet'. That is the disagreement we want to measure.",
        }
    ]


def audit_step(snap: MeterSnapshot, yields: EngineYieldNode, remaining_hours: float) -> dict:
    """Cross-oracle picks on the same snapshot (not vs the old heuristic)."""
    picks = {}
    for name, spec in ORACLES.items():
        alloc = spec.plan(snap, yields, remaining_hours)
        picks[name] = {"action": alloc.action, "hours": alloc.hour_budget}
    actions = {p["action"] for p in picks.values()}
    return {
        "catalog_id": CATALOG_ID,
        "agree": len(actions) == 1,
        "picks": picks,
        "closure": snap.closure_frac,
        "n_open": len(snap.uncovered),
        "sva_open": sum(1 for u in snap.uncovered if u["kind"] == "sva"),
    }


def audit_trace(rows: Iterable[dict]) -> dict:
    rows = list(rows)
    n = len(rows)
    agrees = sum(1 for r in rows if r.get("agree"))
    return {
        "catalog_id": CATALOG_ID,
        "n_steps": n,
        "all_oracles_agree": agrees / n if n else 1.0,
        "n_disagree": n - agrees,
    }
