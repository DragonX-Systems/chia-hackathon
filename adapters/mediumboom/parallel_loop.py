"""Legacy MediumBOOM parallel multi-seat arm runner.

Admits up to ``n_sim`` concurrent simulation-class jobs (torture / riscv_dv /
directed / constraint_tuning) and ``n_formal`` concurrent formal jobs. Completions
drive meter merge and the next admission. Planners stay the same as serial B1/B2/B3.
"""

from __future__ import annotations

import os
import random
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .chia_dispatch import engine_via_chia, merge_via_chia, snapshot_via_chia
from .engines import Allocation, KnobState, simulate_batch
from .loop import ArmResult, TracePoint
from .meter import CoverageMeterNode
from .planner import LlmAccounting
from .yield_table import EngineYieldNode

SIM_ACTIONS = frozenset({"torture", "riscv_dv", "directed", "constraint_tuning"})
FORMAL_ACTIONS = frozenset({"formal"})


def _seat_kind(action: str) -> str:
    if action in FORMAL_ACTIONS:
        return "formal"
    return "sim"


@dataclass
class _InFlight:
    fut: Future
    alloc: Allocation
    seat: str
    submitted_wall: float


@dataclass
class ParallelStats:
    max_in_flight: int = 0
    peak_sim: int = 0
    peak_formal: int = 0
    admissions: int = 0
    wall_s: float = 0.0
    events: list[dict] = field(default_factory=list)


def run_arm_parallel(
    arm: str,
    seed: int,
    budget_hours: float,
    frozen_path: Path,
    planner: Callable,
    batch_fn: Callable | None = None,
    work_dir: Path | None = None,
    n_sim: int | None = None,
    n_formal: int | None = None,
) -> tuple[ArmResult, ParallelStats]:
    n_sim = int(n_sim if n_sim is not None else os.environ.get("COVERAGE_N_SIM", "4"))
    n_formal = int(
        n_formal if n_formal is not None else os.environ.get("COVERAGE_N_FORMAL", "1")
    )
    n_sim = max(1, n_sim)
    n_formal = max(0, n_formal)

    rng = random.Random(seed)
    meter = CoverageMeterNode(store_path=frozen_path)
    yields = EngineYieldNode()
    knobs = KnobState()
    llm = LlmAccounting()
    run_batch = batch_fn or simulate_batch
    stats = ParallelStats()
    t0 = time.perf_counter()

    trace: list[TracePoint] = [
        TracePoint(0.0, 0.0, 0.0, "init", 0, 0, "start", ""),
    ]
    policy: list[dict] = []
    in_flight: list[_InFlight] = []
    free_sim, free_formal = n_sim, n_formal
    rounds = 0

    def _can_admit(action: str) -> bool:
        seat = _seat_kind(action)
        if seat == "formal":
            return free_formal > 0
        return free_sim > 0

    def _reserve(action: str) -> str:
        nonlocal free_sim, free_formal
        seat = _seat_kind(action)
        if seat == "formal":
            free_formal -= 1
        else:
            free_sim -= 1
        return seat

    def _release(seat: str) -> None:
        nonlocal free_sim, free_formal
        if seat == "formal":
            free_formal += 1
        else:
            free_sim += 1

    with ThreadPoolExecutor(max_workers=max(1, n_sim + n_formal)) as pool:
        while meter.hours < budget_hours - 1e-9 and rounds < 200:
            rounds += 1
            # Admit while seats free and budget remains.
            while True:
                snap = snapshot_via_chia(meter)
                remaining = budget_hours - meter.hours
                # Reserve compute for in-flight optimistic charge? Charge on merge only.
                if remaining <= 0 or len(snap.uncovered) == 0:
                    break
                alloc = planner(snap, yields, remaining)
                alloc.hour_budget = min(alloc.hour_budget, remaining)
                if alloc.hour_budget <= 0:
                    break
                if not _can_admit(alloc.action):
                    break
                seat = _reserve(alloc.action)
                work = (work_dir / f"{arm}_{seed}") if work_dir is not None else None
                # Capture covered/retired at submit time; merge is serial on main.
                covered = set(meter.covered)
                retired = set(meter.retired)
                model = meter.model
                knobs_snap = knobs

                def _job(
                    a=alloc,
                    c=covered,
                    r=retired,
                    m=model,
                    k=knobs_snap,
                    w=work,
                    rng_local=random.Random(rng.randint(0, 2**31 - 1)),
                ):
                    return engine_via_chia(
                        a.action, run_batch, m, c, r, a, k, rng_local, w
                    )

                fut = pool.submit(_job)
                in_flight.append(
                    _InFlight(fut=fut, alloc=alloc, seat=seat, submitted_wall=time.perf_counter())
                )
                stats.admissions += 1
                stats.max_in_flight = max(stats.max_in_flight, len(in_flight))
                n_sim_live = sum(1 for x in in_flight if x.seat == "sim")
                n_form_live = sum(1 for x in in_flight if x.seat == "formal")
                stats.peak_sim = max(stats.peak_sim, n_sim_live)
                stats.peak_formal = max(stats.peak_formal, n_form_live)
                stats.events.append(
                    {
                        "event": "admit",
                        "action": alloc.action,
                        "seat": seat,
                        "in_flight": len(in_flight),
                        "hours": meter.hours,
                    }
                )

            if not in_flight:
                break

            done, _ = wait([x.fut for x in in_flight], return_when=FIRST_COMPLETED)
            still: list[_InFlight] = []
            for item in in_flight:
                if item.fut not in done:
                    still.append(item)
                    continue
                result, knobs = item.fut.result()
                _release(item.seat)
                before_c = set(meter.covered)
                before_r = set(meter.retired)
                merge_via_chia(meter, result.hits, result.retired, result.hours)
                new_hits = len(meter.covered - before_c)
                new_ret = len(meter.retired - before_r)
                yields.record(result.action, result.hours, new_hits, new_ret)
                snap2 = meter.snapshot()
                trace.append(
                    TracePoint(
                        hours=snap2.hours,
                        hit_frac=snap2.hit_frac,
                        closure_frac=snap2.closure_frac,
                        action=result.action,
                        hits=new_hits,
                        retired=new_ret,
                        reason=item.alloc.reason,
                        notes=f"parallel seat={item.seat} | {result.notes}",
                    )
                )
                policy.append(
                    {
                        "hours": round(snap2.hours, 4),
                        "closure": round(snap2.closure_frac, 4),
                        "action": result.action,
                        "reason": item.alloc.reason,
                        "targets": item.alloc.target_pids[:8],
                        "seat": item.seat,
                        "in_flight_after": len(still),
                    }
                )
                stats.events.append(
                    {
                        "event": "complete",
                        "action": result.action,
                        "seat": item.seat,
                        "hits": new_hits,
                        "retired": new_ret,
                        "hours": snap2.hours,
                    }
                )
            in_flight = still

            # Stop admitting once budget exhausted; drain in-flight.
            if meter.hours >= budget_hours - 1e-9:
                while in_flight:
                    done, _ = wait([x.fut for x in in_flight], return_when=FIRST_COMPLETED)
                    still = []
                    for item in in_flight:
                        if item.fut not in done:
                            still.append(item)
                            continue
                        result, knobs = item.fut.result()
                        _release(item.seat)
                        before_c = set(meter.covered)
                        before_r = set(meter.retired)
                        # Do not charge past budget: clamp hours.
                        charge = min(result.hours, max(0.0, budget_hours - meter.hours))
                        merge_via_chia(meter, result.hits, result.retired, charge)
                        new_hits = len(meter.covered - before_c)
                        new_ret = len(meter.retired - before_r)
                        yields.record(result.action, charge, new_hits, new_ret)
                        snap2 = meter.snapshot()
                        trace.append(
                            TracePoint(
                                hours=snap2.hours,
                                hit_frac=snap2.hit_frac,
                                closure_frac=snap2.closure_frac,
                                action=result.action,
                                hits=new_hits,
                                retired=new_ret,
                                reason=item.alloc.reason,
                                notes=f"parallel drain seat={item.seat} | {result.notes}",
                            )
                        )
                    in_flight = still
                break

    stats.wall_s = time.perf_counter() - t0
    final = meter.snapshot()
    arm_result = ArmResult(
        arm=arm,
        seed=seed,
        budget_hours=budget_hours,
        trace=trace,
        policy=policy,
        final_hit=final.hit_frac,
        final_closure=final.closure_frac,
        llm={
            "calls": llm.calls,
            "tokens": llm.tokens,
            "wall_s": llm.wall_s,
            "notes": llm.notes,
            "parallel": {
                "n_sim": n_sim,
                "n_formal": n_formal,
                "max_in_flight": stats.max_in_flight,
                "peak_sim": stats.peak_sim,
                "peak_formal": stats.peak_formal,
                "admissions": stats.admissions,
                "wall_s": stats.wall_s,
            },
        },
    )
    return arm_result, stats
