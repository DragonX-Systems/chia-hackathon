"""Legacy MediumBOOM coverage-vs-hours experiment loop."""

from __future__ import annotations

import json
import os
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from .baseline import baseline_plan
from .baselines_b123 import B123_ARMS, b123_manifest, get_b123_plan
from .chia_dispatch import engine_via_chia, merge_via_chia, snapshot_via_chia
from .coverage_model import FROZEN_SEED, dump_model, freeze_model
from .engines import Allocation, KnobState, simulate_batch
from .enhancement import (
    ENHANCEMENT_ARM,
    ENHANCEMENT_MODES,
    enhancement_manifest,
    make_enhancement_plan,
)
from .meter import CoverageMeterNode, MeterSnapshot
from .oracle_policy import ORACLE_NAMES, dump_catalog, get_oracle, start_picks
from .planner import LlmAccounting
from .yield_table import EngineYieldNode


@dataclass
class TracePoint:
    hours: float
    hit_frac: float
    closure_frac: float
    action: str
    hits: int
    retired: int
    reason: str
    notes: str = ""


@dataclass
class ArmResult:
    arm: str
    seed: int
    budget_hours: float
    trace: list[TracePoint]
    policy: list[dict]
    final_hit: float
    final_closure: float
    llm: dict = field(default_factory=dict)


def _hours_to(trace: list[TracePoint], field: str, target: float) -> float | None:
    for p in trace:
        if getattr(p, field) >= target:
            return p.hours
    return None


def run_arm(
    arm: str,
    seed: int,
    budget_hours: float,
    frozen_path: Path,
    planner: Callable[[MeterSnapshot, EngineYieldNode, float], Allocation],
    batch_fn: Callable | None = None,
    work_dir: Path | None = None,
) -> ArmResult:
    rng = random.Random(seed)
    meter = CoverageMeterNode(store_path=frozen_path)
    yields = EngineYieldNode()
    knobs = KnobState()
    llm = LlmAccounting()
    run_batch = batch_fn or simulate_batch
    trace: list[TracePoint] = [
        TracePoint(0.0, 0.0, 0.0, "init", 0, 0, "start", ""),
    ]
    policy: list[dict] = []

    rounds = 0
    while meter.hours < budget_hours - 1e-9 and rounds < 80:
        rounds += 1
        snap = snapshot_via_chia(meter)
        remaining = budget_hours - meter.hours
        alloc = planner(snap, yields, remaining)
        alloc.hour_budget = min(alloc.hour_budget, remaining)
        if alloc.hour_budget <= 0 or len(snap.uncovered) == 0:
            break

        work = (work_dir / f"{arm}_{seed}") if work_dir is not None else None
        result, knobs = engine_via_chia(
            alloc.action,
            run_batch,
            meter.model,
            meter.covered,
            meter.retired,
            alloc,
            knobs,
            rng,
            work,
        )
        before_c = set(meter.covered)
        before_r = set(meter.retired)
        merge_via_chia(meter, result.hits, result.retired, result.hours)
        new_hits = len(meter.covered - before_c)
        new_ret = len(meter.retired - before_r)
        yields.record(result.action, result.hours, new_hits, new_ret)

        snap2 = meter.snapshot()
        reason = alloc.reason
        trace.append(
            TracePoint(
                hours=snap2.hours,
                hit_frac=snap2.hit_frac,
                closure_frac=snap2.closure_frac,
                action=result.action,
                hits=new_hits,
                retired=new_ret,
                reason=reason,
                notes=result.notes,
            )
        )
        policy.append(
            {
                "hours": round(snap2.hours, 4),
                "closure": round(snap2.closure_frac, 4),
                "action": result.action,
                "reason": reason,
                "targets": alloc.target_pids[:8],
            }
        )

    final = meter.snapshot()
    return ArmResult(
        arm=arm,
        seed=seed,
        budget_hours=budget_hours,
        trace=trace,
        policy=policy,
        final_hit=final.hit_frac,
        final_closure=final.closure_frac,
        llm={"calls": llm.calls, "tokens": llm.tokens, "wall_s": llm.wall_s, "notes": llm.notes},
    )


def _backend_batch(backend: str, out_dir: Path, frozen_path: Path):
    batch_fn: Callable | None = None
    work: Path | None = None
    if backend == "real":
        from .real_backend import freeze_isa_model, simulate_real_batch

        dump_model(freeze_isa_model(), frozen_path)
        batch_fn = simulate_real_batch
        work = out_dir / "work"
    elif backend == "sby":
        from .sby_backend import freeze_sby_model, simulate_sby_batch

        dump_model(freeze_sby_model(), frozen_path)
        batch_fn = simulate_sby_batch
        work = out_dir / "work"
    elif backend == "verilator":
        from .verilator_backend import freeze_verilator_model, simulate_verilator_batch

        dump_model(freeze_verilator_model(out_dir / "work" / "freeze"), frozen_path)
        batch_fn = simulate_verilator_batch
        work = out_dir / "work"
    elif backend == "boom":
        from .boom_backend import freeze_boom_model, simulate_boom_batch

        # Default yardstick is SoC-scoped CRV bins + LSU SVA. Do not auto-build
        # the driven LSU coverage TB (that is opt-in via BOOM_USE_LSU_COV=1).
        dump_model(freeze_boom_model(out_dir / "work" / "freeze"), frozen_path)
        batch_fn = simulate_boom_batch
        work = out_dir / "work"
    elif backend == "leftover":
        from .leftover_backend import freeze_leftover_rich, simulate_leftover_batch

        dump_model(freeze_leftover_rich(), frozen_path)
        batch_fn = simulate_leftover_batch
        work = out_dir / "work"
    elif not frozen_path.exists():
        dump_model(freeze_model(FROZEN_SEED), frozen_path)
    return batch_fn, work


def run_experiment(
    out_dir: Path,
    budget_hours: float = 40.0,
    seeds: tuple[int, ...] = (1, 2, 3),
    backend: str = "fake",
    oracles: tuple[str, ...] | None = None,
    enhancement: bool = False,
    arms: tuple[str, ...] | None = None,
    parallel: bool = False,
    n_sim: int = 4,
    n_formal: int = 1,
) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    b123_mode = bool(arms)
    if b123_mode:
        unknown = [a for a in arms if a not in B123_ARMS]
        if unknown:
            raise ValueError(f"unknown arms {unknown}; expected subset of {B123_ARMS}")
        names: tuple[str, ...] = ()
        enhancement = False
    else:
        names = ORACLE_NAMES if oracles is None else oracles
    dump_catalog(out_dir / "oracle_catalog.json")
    frozen_path = out_dir / "frozen_coverage_model.json"
    batch_fn, work = _backend_batch(backend, out_dir, frozen_path)

    # Pre-register step-1 picks on the frozen yardstick (empty yields).
    from .coverage_model import load_model
    from .meter import uncovered_view

    model = load_model(frozen_path)
    empty_uncovered = uncovered_view(model.points, set(), set())
    start_snap = MeterSnapshot(
        hours=0.0,
        n_total=len(model.points),
        n_covered=0,
        n_retired=0,
        hit_frac=0.0,
        closure_frac=0.0,
        uncovered=empty_uncovered,
    )
    opening = start_picks(start_snap, budget_hours)
    (out_dir / "oracle_step1.json").write_text(json.dumps(opening, indent=2) + "\n")

    results: list[ArmResult] = []
    parallel_stats: list[dict] = []

    def _run_one(arm_name: str, seed: int, planner) -> None:
        if parallel:
            from .parallel_loop import run_arm_parallel

            arm_res, stats = run_arm_parallel(
                arm_name,
                seed,
                budget_hours,
                frozen_path,
                planner,
                batch_fn,
                work,
                n_sim=n_sim,
                n_formal=n_formal,
            )
            results.append(arm_res)
            parallel_stats.append(
                {
                    "arm": arm_name,
                    "seed": seed,
                    "max_in_flight": stats.max_in_flight,
                    "peak_sim": stats.peak_sim,
                    "peak_formal": stats.peak_formal,
                    "admissions": stats.admissions,
                    "wall_s": stats.wall_s,
                }
            )
        else:
            results.append(
                run_arm(arm_name, seed, budget_hours, frozen_path, planner, batch_fn, work)
            )

    for seed in seeds:
        if b123_mode:
            for arm_name in arms:
                _run_one(arm_name, seed, get_b123_plan(arm_name))
            continue
        results.append(
            run_arm("baseline", seed, budget_hours, frozen_path, baseline_plan, batch_fn, work)
        )
        for name in names:
            spec = get_oracle(name)
            results.append(
                run_arm(name, seed, budget_hours, frozen_path, spec.plan, batch_fn, work)
            )
        if enhancement:
            # The real MediumBOOM evaluation needs the two enhancements as
            # separate ablations against its CRV baseline.  Keep enh_v1 for
            # the synthetic leftover-rich combined-arm study only.
            if backend == "boom":
                modes = {
                    "enh_formal": ENHANCEMENT_MODES["enh_formal"],
                    "enh_directed": ENHANCEMENT_MODES["enh_directed"],
                }
            elif backend == "leftover":
                modes = ENHANCEMENT_MODES
            else:
                modes = {ENHANCEMENT_ARM: "both"}
            for arm_name, mode in modes.items():
                results.append(
                    run_arm(
                        arm_name,
                        seed,
                        budget_hours,
                        frozen_path,
                        make_enhancement_plan(mode),
                        batch_fn,
                        work,
                    )
                )

    if b123_mode:
        chart_names = tuple(arms)
        ref_arm = "b2" if "b2" in arms else arms[0]
        payload = summarize(results, budget_hours, chart_names, ref_arm=ref_arm)
        payload["feature1_arms"] = list(arms)
        payload["b123"] = b123_manifest()
        (out_dir / "b123.json").write_text(json.dumps(payload["b123"], indent=2) + "\n")
    else:
        if enhancement:
            if backend == "boom":
                extra = ("enh_formal", "enh_directed")
            elif backend == "leftover":
                extra = tuple(ENHANCEMENT_MODES)
            else:
                extra = (ENHANCEMENT_ARM,)
        else:
            extra = ()
        chart_names = names + extra
        payload = summarize(results, budget_hours, chart_names)
    payload["backend"] = backend
    payload["oracles"] = list(names)
    payload["oracle_step1"] = opening
    payload["parallel"] = bool(parallel)
    if parallel:
        payload["parallel_pool"] = {"n_sim": n_sim, "n_formal": n_formal}
        payload["parallel_stats"] = parallel_stats
        (out_dir / "parallel_stats.json").write_text(
            json.dumps(parallel_stats, indent=2) + "\n"
        )
    if enhancement and not b123_mode:
        payload["enhancement"] = enhancement_manifest()
        (out_dir / "enhancement.json").write_text(
            json.dumps(payload["enhancement"], indent=2) + "\n"
        )
    if backend == "verilator":
        payload["dut"] = "ultraembedded RV32IM TCM core (not generated MediumBOOM)"
        payload["clock"] = "batch wall seconds charged as hour units (CHIA viz-profile on a cluster)"
    if backend == "boom":
        from .boom_backend import find_mediumboom_sim

        has_sim = find_mediumboom_sim() is not None
        payload["dut"] = (
            "Chipyard MediumBoomV3Config: SymbiYosys on generated LSU.sv (formal) + "
            "Verilator SoC for torture/riscv_dv/directed (CRV)"
        )
        payload["clock"] = (
            "formal + SoC Verilator wall seconds / 3600 (no 0.05 h floor)"
        )
        payload["crv"] = bool(has_sim)
        payload["honesty"] = (
            "Split yardstick: Formal = SymbiYosys cover BMC on named LSU "
            "SystemVerilog Assertions only (full ChipTop BMC infeasible here). "
            "CRV = UCB riscv-torture on SoC +loadmem PASS for torture/riscv_dv/"
            "directed when the suite exists; hits = SoC-scoped opcode/class/run "
            "bins (seed 20260914). SoC binary has VM_COVERAGE=0 — not Verilator "
            "line dumps, not driven-LSU Vlsu_cov_tb bins. Set BOOM_USE_LSU_COV=1 "
            "only for an explicit LSU-slice experiment. Mini assembler is fallback "
            "only. Formal with no open LSU SVA diverts to directed torture."
            if has_sim
            else "MediumBOOM simulator missing — CRV batches charge hours with 0 hits."
        )
    if backend == "leftover":
        payload["dut"] = (
            "leftover-rich frozen CoverPoint list (n=5000, seed=20260910); "
            "not a MediumBOOM SoC and not a Ray cluster"
        )
        payload["clock"] = (
            "wall-hour MODEL: riscv_dv 0.40×hour_budget (2.0 h request → 0.80 h), "
            "torture 0.40/1.5×hour_budget, constraint_tuning 0.02, "
            "directed 1.20 (full suite), formal 0.30 per solver batch"
        )
        payload["n_points"] = 5000
        payload["yardstick_seed"] = 20260910
        payload["honesty"] = (
            "leftover-rich wall-hour MODEL, not MediumBOOM 38-point SoC eval, not Ray cluster. "
            "Hits/retired still come from engines.simulate_batch; hours are replaced by the wall model."
        )
    if backend in ("verilator", "boom"):
        payload["profile"] = [
            {
                "arm": r.arm,
                "seed": r.seed,
                "steps": [
                    {"hours": p.hours, "action": p.action, "notes": p.notes}
                    for p in r.trace
                    if p.action != "init"
                ],
            }
            for r in results
        ]
        (out_dir / "chia_profile.json").write_text(json.dumps(payload["profile"], indent=2) + "\n")
    (out_dir / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    (out_dir / "compare.json").write_text(json.dumps(payload["compare"], indent=2) + "\n")
    return payload


def summarize(
    results: list[ArmResult],
    budget_hours: float,
    oracle_names: tuple[str, ...],
    ref_arm: str | None = None,
) -> dict:
    # Legacy path prefixes industrial ``baseline``. Feature-1 path passes b1/b2/b3
    # as oracle_names with ref_arm=b2 (primary control for CHIA ROI).
    if ref_arm is not None:
        names = tuple(oracle_names)
        reference = ref_arm if ref_arm in names else (names[0] if names else "baseline")
    else:
        names = ("baseline",) + tuple(oracle_names)
        reference = "baseline"
    by_arm: dict[str, list[ArmResult]] = {n: [] for n in names}
    for r in results:
        by_arm.setdefault(r.arm, []).append(r)

    def mean_final(arm: str, field: str) -> float:
        xs = [getattr(r, field) for r in by_arm.get(arm, [])]
        return sum(xs) / max(1, len(xs))

    ref_final = mean_final(reference, "final_hit")
    headlines: dict[str, dict] = {}
    for arm in names:
        ratios = {}
        for pct in (0.90, 0.95, 0.99):
            target = pct * ref_final
            hours = [_hours_to(r.trace, "hit_frac", target) for r in by_arm.get(arm, [])]
            finite = [h for h in hours if h is not None]
            ratios[f"{int(pct * 100)}pct_of_{reference}_final"] = {
                "target_hit_frac": target,
                "hours_mean": (sum(finite) / len(finite)) if finite else None,
                "reached_seeds": len(finite),
            }
            # Keep legacy key for chart code that still reads 90pct_of_baseline_final.
            if reference == "baseline":
                pass
            else:
                ratios[f"{int(pct * 100)}pct_of_baseline_final"] = ratios[
                    f"{int(pct * 100)}pct_of_{reference}_final"
                ]
        if reference == "baseline":
            pass
        headlines[arm] = ratios

    base_key = f"90pct_of_{reference}_final"
    # Charts also look up the legacy key.
    legacy_key = "90pct_of_baseline_final"
    for arm in names:
        if legacy_key not in headlines[arm] and base_key in headlines[arm]:
            headlines[arm][legacy_key] = headlines[arm][base_key]

    base_h = headlines.get(reference, {}).get(legacy_key, {}).get("hours_mean")
    speedups = {}
    for name in names:
        if name == reference:
            continue
        agent_h = headlines[name][legacy_key]["hours_mean"]
        speedups[name] = (base_h / agent_h) if agent_h and base_h else None

    compare = {
        "mean_final_hit": {arm: mean_final(arm, "final_hit") for arm in names},
        "mean_final_closure": {arm: mean_final(arm, "final_closure") for arm in names},
        "speedup_hours_to_90pct_of_baseline_final": speedups,
        "reference_arm": reference,
        "step1_actions": {},
    }

    n_seeds = len(by_arm.get(reference, [])) or (
        len(by_arm.get(names[0], [])) if names else 0
    )
    return {
        "budget_hours": budget_hours,
        "n_seeds": n_seeds,
        "mean_final_hit": compare["mean_final_hit"],
        "mean_final_closure": compare["mean_final_closure"],
        "hours_to_fraction_of_baseline_final": headlines,
        "speedup_hours_to_90pct_of_baseline_final": speedups,
        "reference_arm": reference,
        "compare": compare,
        "arms": [
            {
                "arm": r.arm,
                "seed": r.seed,
                "final_hit": r.final_hit,
                "final_closure": r.final_closure,
                "trace": [asdict(p) for p in r.trace],
                "policy": r.policy,
                "llm": r.llm,
            }
            for r in results
        ],
    }
