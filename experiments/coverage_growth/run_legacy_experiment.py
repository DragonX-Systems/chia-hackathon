#!/usr/bin/env python3
"""Run the legacy MediumBOOM coverage-growth experiment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from adapters.mediumboom.baselines_b123 import B123_ARMS  # noqa: E402
from adapters.mediumboom.loop import run_experiment  # noqa: E402
from adapters.mediumboom.report import policy_brief, render_all_charts  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--budget-hours", type=float, default=40.0)
    p.add_argument("--out", type=Path, default=ROOT / "runs" / "legacy" / "batch")
    p.add_argument(
        "--backend",
        choices=("fake", "real", "sby", "verilator", "boom", "leftover"),
        default="fake",
    )
    p.add_argument("--seeds", default="1,2,3")
    p.add_argument(
        "--oracles",
        default="all",
        help="Comma-separated oracle names, or 'all' (sva_first,sim_max,cph_greedy,stage_clean). "
        "Ignored when --arms is set.",
    )
    p.add_argument(
        "--enhancement",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Also run enh_v1 / enh_formal / enh_directed (legacy). Ignored when --arms is set.",
    )
    p.add_argument(
        "--arms",
        default="",
        help="Feature-1 arms: comma-separated subset of b1,b2,b3. "
        "When set, skips legacy baseline/oracles/enhancement.",
    )
    p.add_argument(
        "--parallel",
        action="store_true",
        help="Feature-2: admit up to N_sim + N_formal concurrent jobs.",
    )
    p.add_argument("--n-sim", type=int, default=4, help="Parallel sim seats (default 4).")
    p.add_argument("--n-formal", type=int, default=1, help="Parallel formal seats (default 1).")
    args = p.parse_args()
    seeds = tuple(int(s) for s in args.seeds.split(",") if s.strip())
    arms_raw = args.arms.strip()
    arms = tuple(s.strip() for s in arms_raw.split(",") if s.strip()) if arms_raw else None
    if arms:
        bad = [a for a in arms if a not in B123_ARMS]
        if bad:
            p.error(f"unknown --arms {bad}; choose from {B123_ARMS}")
        oracles: tuple[str, ...] | None = ()
        enhancement = False
    else:
        oracle_raw = args.oracles.strip()
        if oracle_raw == "all":
            oracles = None
        elif oracle_raw in ("none", "off", ""):
            oracles = ()
        else:
            oracles = tuple(s.strip() for s in args.oracles.split(",") if s.strip())
        enhancement = args.enhancement
    payload = run_experiment(
        args.out,
        budget_hours=args.budget_hours,
        seeds=seeds,
        backend=args.backend,
        oracles=oracles,
        enhancement=enhancement,
        arms=arms,
        parallel=args.parallel,
        n_sim=args.n_sim,
        n_formal=args.n_formal,
    )
    brief = policy_brief(payload)
    payload["learned_policy"] = brief
    (args.out / "results.json").write_text(json.dumps(payload, indent=2) + "\n")
    render_all_charts(payload, args.out, brief)
    (args.out / "policy.json").write_text(json.dumps(brief, indent=2) + "\n")
    if args.backend == "leftover" and "enh_v1" in payload.get("mean_final_hit", {}):
        from adapters.mediumboom.roi import write_roi

        rec = write_roi(payload, args.out)
        print("ROI hours-to-40% ratio", rec["hours_to_40pct_hit"].get("ratio_over_baseline"))
    print(f"wrote {args.out}")
    print("step-1 picks:", json.dumps(payload.get("oracle_step1", {}), indent=2))
    print("mean hit    ", json.dumps(payload["mean_final_hit"], indent=2))
    print("mean closure", json.dumps(payload["mean_final_closure"], indent=2))
    print(
        "speedup to 90% of reference-final:",
        json.dumps(payload["speedup_hours_to_90pct_of_baseline_final"], indent=2),
    )
    if payload.get("parallel_stats"):
        print("parallel stats:", json.dumps(payload["parallel_stats"], indent=2))


if __name__ == "__main__":
    main()
