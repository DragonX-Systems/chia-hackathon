"""Legacy cluster-shaped MediumBOOM coverage-growth driver.

Same idea as the vendored `chia/examples/memcpy/memcpy_loop.py` and
`chia/examples/common/verilator.py`:

    plan (driver)  →  chia_remote onto the worker that has that engine
                   →  get() coverage deltas
                   →  merge on the isolated meter
                   →  repeat until budget

Local (no ``chia up``)::

    conda activate chia_env
    python experiments/coverage_growth/cluster_driver.py

Cluster::

    export THIS_MACHINE=$(hostname)
    chia up experiments/coverage_growth/cluster.yaml
    chia job submit -- python experiments/coverage_growth/cluster_driver.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import ray
from chia.base.ChiaFunction import get

# Add the repository packages when this file is launched directly.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
for path in (_ROOT / "src", _ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from adapters.mediumboom.baseline import baseline_plan
from adapters.mediumboom.coverage_model import dump_model, freeze_model
from adapters.mediumboom.engines import KnobState
from adapters.mediumboom.enhancement import ENHANCEMENT_MODES, make_enhancement_plan
from adapters.mediumboom.meter import CoverageMeterNode
from adapters.mediumboom.planner import LlmAccounting, plan as agent_plan
from adapters.mediumboom.report import policy_brief, render_svg
from adapters.mediumboom.yield_table import EngineYieldNode

from experiments.coverage_growth.cluster_nodes import DISPATCH

LOCAL_RESOURCES = {
    "torture": 8,
    "riscv_dv": 10,
    "verilator_run": 10,
    "formal": 4,
    "coverage_meter": 1,
}


def _ensure_ray() -> None:
    if ray.is_initialized():
        return
    # Local demo: advertise the same names the cluster YAML would.
    # On a real cluster the workers already advertise these; extra is ignored.
    ray.init(ignore_reinit_error=True, include_dashboard=False, resources=LOCAL_RESOURCES)


def _dispatch(alloc, model, covered, retired, knobs, seed):
    fn = DISPATCH[alloc.action]
    return get(fn.chia_remote(model, list(covered), list(retired), alloc, knobs, seed))


def run_arm(arm: str, seed: int, budget: float, frozen: Path, planner=None) -> dict:
    meter = CoverageMeterNode(store_path=frozen)
    yields = EngineYieldNode()
    knobs = KnobState()
    llm = LlmAccounting()
    policy = []
    trace = [{"hours": 0.0, "hit_frac": 0.0, "closure_frac": 0.0, "action": "init"}]
    rounds = 0
    while meter.hours < budget and rounds < 40:
        rounds += 1
        snap = meter.snapshot()
        remaining = budget - meter.hours
        if not snap.uncovered or remaining <= 0:
            break
        if planner is not None:
            alloc = planner(snap, yields, remaining)
        elif arm == "agent":
            alloc = agent_plan(snap, yields, remaining, llm)
        else:
            alloc = baseline_plan(snap, yields, remaining)
        alloc.hour_budget = min(alloc.hour_budget, remaining)
        print(f"  [{arm}] t={meter.hours:.2f} closure={snap.closure_frac:.3f} -> {alloc.action} ({alloc.reason})", flush=True)
        result, knobs = _dispatch(alloc, meter.model, meter.covered, meter.retired, knobs, seed + rounds)
        before_c, before_r = set(meter.covered), set(meter.retired)
        meter.merge_batch(result.hits, result.retired, result.hours)
        yields.record(result.action, result.hours, len(meter.covered - before_c), len(meter.retired - before_r))
        snap2 = meter.snapshot()
        trace.append(
            {
                "hours": snap2.hours,
                "hit_frac": snap2.hit_frac,
                "closure_frac": snap2.closure_frac,
                "action": result.action,
            }
        )
        policy.append({"hours": snap2.hours, "closure": snap2.closure_frac, "action": result.action, "reason": alloc.reason})
    snap = meter.snapshot()
    return {
        "arm": arm,
        "seed": seed,
        "final_hit": snap.hit_frac,
        "final_closure": snap.closure_frac,
        "trace": trace,
        "policy": policy,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--budget-hours", type=float, default=24.0)
    p.add_argument("--seeds", default="1")
    p.add_argument("--out", type=Path, default=_HERE.parents[1] / "runs" / "legacy" / "cluster")
    p.add_argument("--backend", choices=("fake", "verilator", "leftover"), default=os.environ.get("COVERAGE_BACKEND", "fake"))
    p.add_argument("--enhancement", action="store_true", help="Run Enh 2/3 via chia_remote (leftover or fake)")
    args = p.parse_args()
    seeds = tuple(int(s) for s in args.seeds.split(",") if s.strip())
    args.out.mkdir(parents=True, exist_ok=True)
    os.environ["COVERAGE_BACKEND"] = args.backend
    os.environ.setdefault("COVERAGE_WORK", str(args.out / "work"))
    frozen = args.out / "frozen_coverage_model.json"
    if args.backend == "verilator":
        from adapters.mediumboom.verilator_backend import freeze_verilator_model

        dump_model(freeze_verilator_model(args.out / "work" / "freeze"), frozen)
    elif args.backend == "leftover":
        from adapters.mediumboom.leftover_backend import freeze_leftover_rich

        dump_model(freeze_leftover_rich(), frozen)
    else:
        dump_model(freeze_model(), frozen)

    _ensure_ray()
    arms = []
    for seed in seeds:
        print(f"=== baseline seed={seed} ===", flush=True)
        arms.append(run_arm("baseline", seed, args.budget_hours, frozen))
        if args.enhancement:
            for arm_name, mode in ENHANCEMENT_MODES.items():
                print(f"=== {arm_name} seed={seed} ===", flush=True)
                arms.append(
                    run_arm(
                        arm_name,
                        seed,
                        args.budget_hours,
                        frozen,
                        planner=make_enhancement_plan(mode),
                    )
                )
        else:
            print(f"=== agent seed={seed} ===", flush=True)
            arms.append(run_arm("agent", seed, args.budget_hours, frozen))

    payload = {
        "budget_hours": args.budget_hours,
        "n_seeds": len(seeds),
        "backend": f"chia-{args.backend}",
        "clock": "ray_task_wall (chia viz-profile on a cluster)",
        "mean_final_hit": {
            n: sum(a["final_hit"] for a in arms if a["arm"] == n)
            / max(1, sum(1 for a in arms if a["arm"] == n))
            for n in {a["arm"] for a in arms}
        },
        "mean_final_closure": {
            n: sum(a["final_closure"] for a in arms if a["arm"] == n)
            / max(1, sum(1 for a in arms if a["arm"] == n))
            for n in {a["arm"] for a in arms}
        },
        "hours_to_fraction_of_baseline_final": {},
        "speedup_hours_to_90pct_of_baseline_final": None,
        "arms": arms,
    }
    payload["learned_policy"] = policy_brief(payload)
    (args.out / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    render_svg(payload, args.out / "coverage_vs_hours.svg")
    print(json.dumps({"hit": payload["mean_final_hit"], "closure": payload["mean_final_closure"], "policy": payload["learned_policy"]}, indent=2))
    if os.environ.get("CHIA_KEEP_RAY") != "1":
        ray.shutdown()


if __name__ == "__main__":
    main()
