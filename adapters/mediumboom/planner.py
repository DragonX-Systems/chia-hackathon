"""Legacy MediumBOOM verification strategy planner.

Chooses the next (action, target, hour budget) from uncovered identity
plus measured coverage-per-hour. No difficulty labels, no HMAC, no
write access to the meter.

Default policy is a stage machine driven by last-batch coverage-per-hour.
Set COVERAGE_PLANNER=llm to ask an LLM; tokens are recorded separately and
never charged to the sim-CPU budget.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .engines import Allocation
from .meter import MeterSnapshot
from .yield_table import ACTIONS, Action, EngineYieldNode


@dataclass
class LlmAccounting:
    calls: int = 0
    tokens: int = 0
    wall_s: float = 0.0
    notes: list[str] = field(default_factory=list)


def _unit_counts(uncovered: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for u in uncovered:
        counts[u["unit"]] = counts.get(u["unit"], 0) + 1
    return counts


def _named_targets(uncovered: list[dict], action: Action) -> list[str]:
    units = _unit_counts(uncovered)
    hot_unit = max(units, key=units.get) if units else None  # type: ignore[arg-type]
    pool = list(uncovered)
    if action == "formal":
        sva = [u for u in pool if u["kind"] == "sva"]
        rest = [u for u in pool if u["kind"] != "sva"]
        stride = max(1, len(rest) // 10)
        striped = rest[::stride]
        ordered = sva + striped
        return [u["pid"] for u in ordered[:18]]
    pool.sort(key=lambda u: (0 if u["unit"] == hot_unit else 1, u["kind"] != "sva", u["pid"]))
    return [u["pid"] for u in pool[:14]]


def heuristic_plan(snap: MeterSnapshot, yields: EngineYieldNode, remaining_hours: float) -> Allocation:
    """Stage machine + last-batch yield collapse. UCB only breaks ties inside a stage."""
    closure = snap.closure_frac
    uncovered = snap.uncovered
    n_open = len(uncovered)
    if n_open == 0 or remaining_hours <= 0:
        return Allocation("formal", 0.1, reason="nothing left")

    rows = yields.rows
    torture, dv, tune = rows["torture"], rows["riscv_dv"], rows["constraint_tuning"]
    directed, formal = rows["directed"], rows["formal"]

    def _alloc(action: Action, hours: float, reason: str) -> Allocation:
        hours = min(hours, remaining_hours)
        targets = _named_targets(uncovered, action) if action in ("directed", "formal") else []
        return Allocation(action, hours, target_pids=targets, reason=reason)

    # --- last mile: retire unreachable, snipe the rest ---
    sva_only = bool(uncovered) and all(u["kind"] == "sva" for u in uncovered)
    last_mile = (
        closure >= 0.80
        or directed.batches > 0
        or formal.batches > 0
        or (dv.batches >= 4 and dv.last_cph < 3.5 and closure >= 0.68)
        # Frozen SVA list (SBY backend): leftover holes are properties, not line bins.
        or (sva_only and (torture.batches + dv.batches) >= 1)
        # Real Verilator: always-on lines inflate early closure, then CRV dries up.
        or (dv.batches >= 2 and dv.last_cph < 2.0 and (torture.batches >= 1 or closure >= 0.45))
    )
    if last_mile:
        sva_open = any(u["kind"] == "sva" for u in uncovered)
        formal_dry = formal.batches >= 1 and (formal.last_hits + formal.last_retired) == 0
        if sva_open and not formal_dry:
            return _alloc("formal", 1.2, f"last-mile formal retire/prove open={n_open}")
        return _alloc("directed", 1.2, f"last-mile directed sniper open={n_open}")

    # --- mid: CRV, with cheap retune when last batch dried up ---
    if torture.hours >= 4.0 or closure >= 0.50:
        need_tune = (
            dv.batches >= 2
            and dv.last_cph < 3.0
            and tune.batches < max(1, dv.batches // 4)
        )
        if need_tune:
            return _alloc("constraint_tuning", 0.08, "CRV last-batch yield collapsed — retune")
        return _alloc("riscv_dv", 1.8, f"mid-range CRV closure={closure:.3f}")

    # --- early: carpet bomb, but abandon torture the moment it saturates ---
    if torture.batches == 0:
        return _alloc("torture", 2.0, "early carpet-bomb")
    if torture.last_cph >= 12.0 and torture.hours < 8.0:
        return _alloc("torture", 2.0, f"torture still yielding last_cph={torture.last_cph:.1f}")
    return _alloc("riscv_dv", 1.8, f"leave torture last_cph={torture.last_cph:.1f}")


def llm_plan(snap: MeterSnapshot, yields: EngineYieldNode, remaining_hours: float, acct: LlmAccounting) -> Allocation:
    """Optional LLM planner. Falls back to heuristic if no key / library."""
    fallback = heuristic_plan(snap, yields, remaining_hours)
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or os.environ.get("COVERAGE_PLANNER", "heuristic") != "llm":
        return fallback

    prompt = {
        "uncovered_n": len(snap.uncovered),
        "uncovered_units": _unit_counts(snap.uncovered),
        "closure_frac": snap.closure_frac,
        "hit_frac": snap.hit_frac,
        "yields": yields.as_planner_view(),
        "remaining_hours": remaining_hours,
        "actions": list(ACTIONS),
        "schema": {"action": "one of actions", "hour_budget": "float", "target_pids": "optional list", "reason": "str"},
    }
    try:
        import time

        t0 = time.time()
        # Kept optional and tiny — tokens must not enter the compute budget.
        from openai import OpenAI  # type: ignore

        client = OpenAI()
        resp = client.chat.completions.create(
            model=os.environ.get("COVERAGE_PLANNER_MODEL", "gpt-4o-mini"),
            messages=[
                {
                    "role": "system",
                    "content": "You allocate the next verification compute block. Reply JSON only.",
                },
                {"role": "user", "content": json.dumps(prompt)},
            ],
            max_tokens=300,
        )
        acct.calls += 1
        acct.wall_s += time.time() - t0
        usage = getattr(resp, "usage", None)
        if usage:
            acct.tokens += int(getattr(usage, "total_tokens", 0) or 0)
        text = resp.choices[0].message.content or "{}"
        data = json.loads(text[text.find("{") : text.rfind("}") + 1])
        action = data.get("action", fallback.action)
        if action not in ACTIONS:
            action = fallback.action
        budget = float(data.get("hour_budget", fallback.hour_budget))
        targets = list(data.get("target_pids") or fallback.target_pids)
        return Allocation(action, min(budget, remaining_hours), targets, reason=str(data.get("reason", "llm")))
    except Exception as exc:  # noqa: BLE001 — planner must not abort the loop
        acct.notes.append(str(exc))
        return fallback


def plan(snap: MeterSnapshot, yields: EngineYieldNode, remaining_hours: float, acct: LlmAccounting) -> Allocation:
    name = os.environ.get("COVERAGE_PLANNER", "sva_first")
    if name == "llm":
        return llm_plan(snap, yields, remaining_hours, acct)
    if name == "heuristic":
        return heuristic_plan(snap, yields, remaining_hours)
    from .oracle_policy import get_oracle

    if name in ("sva_first", "sim_max", "cph_greedy", "stage_clean"):
        return get_oracle(name).plan(snap, yields, remaining_hours)
    return get_oracle("sva_first").plan(snap, yields, remaining_hours)
