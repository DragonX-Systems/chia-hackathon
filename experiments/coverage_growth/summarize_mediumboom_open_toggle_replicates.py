"""Validate and compare the preregistered paired fresh-input toggle replicates."""
from __future__ import annotations

import hashlib
import argparse
import gzip
import json
import math
import random
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STUDY = ROOT / "artifacts_mediumboom/kapil_qualification/open_toggle_multi_seed_study"
DEFAULT_RAW = Path("/private/tmp/mediumboom-open-toggle-multiseed-20260924")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def auc(trace: list[dict], horizon: float) -> float:
    points = sorted((float(row["compute_hours"]), float(row["sound_closure"])) for row in trace)
    area = 0.0
    x_prev, y_prev = 0.0, 0.0
    for x, y in points[1:]:
        if x >= horizon:
            area += max(0.0, horizon - x_prev) * y_prev
            return area / horizon
        area += max(0.0, x - x_prev) * y_prev
        x_prev, y_prev = x, y
    area += max(0.0, horizon - x_prev) * y_prev
    return area / horizon


def arm_result(trial: Path, arm: str, target_count: int, horizon: float) -> dict:
    out = trial / arm
    summary = json.loads((out / "summary.json").read_text())
    event_files = [out / "events.jsonl"] + sorted((out / "segments").glob("*/events.jsonl"))
    events = [json.loads(line) for path in event_files if path.is_file()
              for line in path.read_text().splitlines()]
    dispatch = {e["job_id"]: e for e in events if e["event"] == "job_dispatched"}
    completed = {e["job_id"]: e for e in events if e["event"] == "job_completed"}
    order = sorted(dispatch, key=lambda job: dispatch[job]["timestamp"])
    closed: set[str] = set()
    jobs = []
    for job_id in order:
        event = completed.get(job_id)
        if not event:
            jobs.append({"job_id": job_id, "status": "not_completed"})
            continue
        result = event["result"]
        hits = set(result.get("hit_point_ids", []))
        delta = sorted(hits - closed)
        closed.update(hits)
        evidence = {}
        compressed_db = None
        sim_log = None
        for path_text in result.get("artifact_paths", []):
            path = Path(path_text)
            if path.name == "evidence.json" and path.is_file():
                evidence = json.loads(path.read_text())
            elif path.name == "coverage.dat.gz" and path.is_file():
                compressed_db = path
            elif path.name == "sim.log" and path.is_file():
                sim_log = path
        if evidence:
            if evidence.get("job_id") != job_id:
                raise SystemExit(f"evidence job ID mismatch: {job_id}")
            if sorted(evidence.get("monitor_toggle_bins_hit_ids", [])) != sorted(hits):
                raise SystemExit(f"evidence toggle hits differ from event journal: {job_id}")
            if evidence.get("target_toggle_bins_hit") != len(hits):
                raise SystemExit(f"evidence toggle count differs from event journal: {job_id}")
        coverage_database_verified = False
        if compressed_db is not None:
            digest = hashlib.sha256()
            with gzip.open(compressed_db, "rb") as stream:
                for block in iter(lambda: stream.read(1 << 20), b""):
                    digest.update(block)
            if digest.hexdigest() != evidence.get("coverage_dat_sha256"):
                raise SystemExit(f"compressed coverage database hash mismatch: {compressed_db}")
            coverage_database_verified = True
        jobs.append({"job_id": job_id, "status": result["status"],
                     "engine": result["engine"], "new_target_bins": len(delta),
                     "error_summary": result.get("error_summary", ""),
                     "dispatch_reason": dispatch[job_id]["reason"],
                     "compute_hours": result["compute_hours"],
                     "input_elf_sha256": evidence.get("input_elf_sha256"),
                     "coverage_dat_sha256": evidence.get("coverage_dat_sha256"),
                     "coverage_database_verified": coverage_database_verified,
                     "sim_log_sha256": sha256(sim_log) if sim_log is not None else None,
                     "evidence_sha256": sha256(Path(next(
                         p for p in result.get("artifact_paths", []) if p.endswith("evidence.json"))))
                         if any(p.endswith("evidence.json") and Path(p).is_file()
                                for p in result.get("artifact_paths", [])) else None})
    return {"campaign_fingerprint": summary["campaign_fingerprint"],
            "completed_jobs": summary["completed_jobs"],
            "successful_jobs": summary["status_counts"].get("success", 0),
            "failed_job_statuses": summary["status_counts"],
            "stop_reason": summary["stop_reason"],
            "reached_95_percent": summary["reached_target"],
            "compute95_hours": summary["compute95"],
            "compute_hours": summary["total_compute_hours"],
            "wall_minutes": summary["wall_time_hours"] * 60,
            "new_target_bins_at_budget": len(closed),
            "target_bins": target_count,
            "closure_at_budget": len(closed) / target_count,
            "auc_closure_fraction": auc(summary["coverage_trace"], horizon),
            "coverage_trace": summary["coverage_trace"],
            "dispatch_order": order,
            "dispatch_reasons": [dispatch[job]["reason"] for job in order],
            "jobs": jobs}


def bootstrap_mean_ci(values: list[float], samples: int = 20000) -> list[float]:
    rng = random.Random(20260924)
    means = [statistics.mean(rng.choices(values, k=len(values))) for _ in range(samples)]
    means.sort()
    return [means[int(0.025 * samples)], means[min(samples - 1, int(0.975 * samples))]]


def exact_sign_p(values: list[float]) -> float:
    nonzero = [value for value in values if value != 0]
    n = len(nonzero)
    if n == 0:
        return 1.0
    positive = sum(value > 0 for value in nonzero)
    tail = sum(math.comb(n, k) for k in range(min(positive, n - positive) + 1))
    return min(1.0, 2 * tail / (2 ** n))


def log_projection(trace: list[dict], target: float = 0.95) -> dict:
    """Fit closure=a+b*ln(compute-hours); model output, never measured Compute95."""
    by_time: dict[float, float] = {}
    for row in trace:
        x = float(row["compute_hours"])
        if x > 0:
            by_time[x] = float(row["sound_closure"])
    points = sorted(by_time.items())
    if len(points) < 3:
        return {"status": "insufficient_measured_points", "n": len(points)}
    xs, ys = [math.log(x) for x, _ in points], [y for _, y in points]
    xb, yb = statistics.mean(xs), statistics.mean(ys)
    sxx = sum((x - xb) ** 2 for x in xs)
    if sxx <= 0:
        return {"status": "degenerate_log_time", "n": len(points)}
    slope = sum((x - xb) * (y - yb) for x, y in zip(xs, ys)) / sxx
    intercept = yb - slope * xb
    sst = sum((y - yb) ** 2 for y in ys)
    sse = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    log_t = (target - intercept) / slope if slope > 0 else None
    t95 = math.exp(log_t) if log_t is not None and log_t < 709 else None
    max_time = max(x for x, _ in points)
    return {"status": "log_linear_extrapolation_only" if slope > 0 else "nonpositive_fitted_slope",
            "n": len(points), "intercept": intercept, "slope_per_ln_hour": slope,
            "r_squared": None if sst == 0 else 1 - sse / sst,
            "measured_time_hours": [min(x for x, _ in points), max_time],
            "measured_closure": [min(ys), max(ys)], "projected_log_compute95_hours": log_t,
            "projected_compute95_hours": t95,
            "extrapolation_factor_beyond_max_observation": math.exp(log_t) / max_time if t95 is not None else None}


def projection_summary(trials: list[dict], arm: str) -> dict:
    fits = [log_projection(row[arm]["coverage_trace"]) for row in trials]
    values = [f["projected_compute95_hours"] for f in fits if f.get("projected_compute95_hours") is not None]
    rng = random.Random(950024)
    boot = sorted(statistics.median(rng.choices(values, k=len(values))) for _ in range(10000)) if values else []
    return {"label": "EXTRAPOLATED MODEL OUTPUT; NOT MEASURED COMPUTE95",
            "model": "per-trial OLS: closure fraction = a + b*ln(compute-hours), solved at 0.95",
            "positive_slope_trials": len(values), "total_trials": len(fits),
            "median_projected_compute95_hours": statistics.median(values) if values else None,
            "bootstrap_95_percent_interval_hours": [boot[250], boot[9749]] if len(boot) == 10000 else None,
            "per_trial": fits,
            "warning": "Exploratory extrapolation from short fixed-budget traces; not observed attainment and not evidence of measured savings."}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", type=Path, default=DEFAULT_STUDY)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    args = parser.parse_args()
    study = args.study.resolve()
    raw = args.raw.resolve()
    prereg = json.loads((study / "preregistration.json").read_text())
    target_count = prereg["frozen_residual_open_point_count"]
    horizon = float(prereg["budget_compute_hours_per_arm"])
    trials = []
    seen_inputs: set[str] = set()
    for row in prereg["trials"]:
        trial_dir = study / "inputs" / row["trial_id"]
        if not all((trial_dir / arm / "summary.json").is_file()
                   for arm in ("static_hybrid", "adaptive")):
            raise SystemExit(f"paired trial incomplete: {row['trial_id']}")
        frozen = json.loads((trial_dir / "preregistration.json").read_text())
        for job in frozen["input_hashes"]:
            if job["elf_sha256"] in seen_inputs:
                raise SystemExit(f"input ELF reused across trials: {row['trial_id']}/{job['job_id']}")
            seen_inputs.add(job["elf_sha256"])
        static = arm_result(trial_dir, "static_hybrid", target_count, horizon)
        adaptive = arm_result(trial_dir, "adaptive", target_count, horizon)
        fingerprints_match = static["campaign_fingerprint"] == adaptive["campaign_fingerprint"]
        if not fingerprints_match:
            raise SystemExit(f"paired campaign fingerprints differ: {row['trial_id']}")
        trials.append({"trial_id": row["trial_id"], "base_seed": row["base_seed"],
                       "paired_fingerprint_match": fingerprints_match,
                       "static_hybrid": static, "adaptive": adaptive,
                       "adaptive_minus_static_bins": adaptive["new_target_bins_at_budget"] - static["new_target_bins_at_budget"],
                       "adaptive_minus_static_auc": adaptive["auc_closure_fraction"] - static["auc_closure_fraction"]})
    bin_diffs = [row["adaptive_minus_static_bins"] for row in trials]
    auc_diffs = [row["adaptive_minus_static_auc"] for row in trials]
    log_models = {arm: projection_summary(trials, arm)
                  for arm in ("static_hybrid", "adaptive")}
    summary = {
        "study": "preregistered repeated paired seeded MediumBOOM open-toggle comparison",
        "endpoint": prereg["primary_endpoint"],
        "target_bins": target_count,
        "budget_compute_hours": horizon,
        "paired_trials": len(trials),
        "all_catalog_fingerprints_match_within_pair": all(row["paired_fingerprint_match"] for row in trials),
        "adaptive_minus_static_bins": {
            "per_trial": bin_diffs, "mean": statistics.mean(bin_diffs),
            "median": statistics.median(bin_diffs), "bootstrap_95_percent_ci": bootstrap_mean_ci(bin_diffs),
            "two_sided_exact_sign_test_p": exact_sign_p(bin_diffs),
        },
        "adaptive_minus_static_auc_closure_fraction": {
            "per_trial": auc_diffs, "mean": statistics.mean(auc_diffs),
            "median": statistics.median(auc_diffs), "bootstrap_95_percent_ci": bootstrap_mean_ci(auc_diffs),
            "two_sided_exact_sign_test_p": exact_sign_p(auc_diffs),
        },
        "logarithmic_compute95_extrapolation": log_models,
        "trials": trials,
        "interpretation": "Treat confidence intervals and exact sign test as exploratory with this sample size. Any budget-capped arm that does not attain 95% has undefined Compute95; policy comparison uses predeclared fixed-budget coverage and area under the within-budget closure curve.",
        "limitations": [
            "Local single-host Verilator only; this is not a CHIA/Ray cluster evaluation.",
            "Each replicate is an independently seeded fresh workload corpus; only ELF hashes are repeated across paired arms by design.",
            "Replicates share a fixed post-pilot baseline to preserve paired comparability; duplicate bin hits across different replicates are not pooled as unique system closure.",
            "Signal toggles measure structural activity, not source-line coverage or functional correctness.",
        ],
    }
    (study / "results.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    readme = [
        "# Repeated paired MediumBOOM open-toggle study",
        "",
        f"The primary endpoint is new native Verilator signal-toggle bins at a preregistered {horizon:g} compute-hour per-arm budget, divided by the common residual registry. The target is the 12,146 open bins from the first pilot minus any bins that pilot actually hit; legacy 51-point and 66-family counts are excluded.",
        "",
        f"Frozen residual denominator: **{target_count:,} points**. Completed matched pairs: **{len(trials)}**. Each trial has eight seeded CRV workloads and two seeded directed workloads (4:1 roster), with identical input ELF hashes within its two policy arms and distinct inputs across trials.",
        "",
        f"Both arms use a fixed {horizon:g} compute-hour horizon and 95% closure as a secondary threshold. If 95% is not reached, measured Compute95 is undefined; fixed-budget closure and within-budget area under the closure curve are reported instead. The paired confidence interval/sign test are exploratory, not a superiority claim by themselves.",
        "",
        "## Per-trial outcomes",
        "",
        "| Trial | Static bins / target | Adaptive bins / target | Adaptive − Static | Completed jobs (successful), S/A | Static / Adaptive 95%? |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in trials:
        s, a = row["static_hybrid"], row["adaptive"]
        readme.append(f"| {row['trial_id']} | {s['new_target_bins_at_budget']:,}/{target_count:,} | {a['new_target_bins_at_budget']:,}/{target_count:,} | {row['adaptive_minus_static_bins']:+,} | {s['completed_jobs']} ({s['successful_jobs']}) / {a['completed_jobs']} ({a['successful_jobs']}) | {'yes' if s['reached_95_percent'] else 'no'} / {'yes' if a['reached_95_percent'] else 'no'} |")
    readme.extend(["", "## Aggregate paired endpoint", "",
                   f"Mean Adaptive − Static closure: {summary['adaptive_minus_static_bins']['mean']:.2f} bins; bootstrap 95% interval {summary['adaptive_minus_static_bins']['bootstrap_95_percent_ci']}; exact sign-test p={summary['adaptive_minus_static_bins']['two_sided_exact_sign_test_p']:.4g}.",
                   f"Mean Adaptive − Static normalized closure AUC: {summary['adaptive_minus_static_auc_closure_fraction']['mean']:.6f}; bootstrap 95% interval {summary['adaptive_minus_static_auc_closure_fraction']['bootstrap_95_percent_ci']}; exact sign-test p={summary['adaptive_minus_static_auc_closure_fraction']['two_sided_exact_sign_test_p']:.4g}.",
                   "", "## Exploratory log-model projection (not observed Compute95)",
                   "Each trial fits closure fraction = a + b ln(compute-hours) by ordinary least squares and solves at 95%. These values are explicitly extrapolated, not measured attainment. The short fixed-budget horizon makes them highly model-dependent; inspect per-trial R-squared and extrapolation factors before citing.",
                   "", "| Arm | Positive-slope fits | Median projected Compute95 (hours) | Trial-bootstrap 95% interval |",
                   "|---|---:|---:|---:|"])
    for arm, label in (("static_hybrid", "Static-Hybrid"), ("adaptive", "Adaptive")):
        model = log_models[arm]
        interval = model["bootstrap_95_percent_interval_hours"]
        interval_text = f"{interval[0]:.3g}–{interval[1]:.3g}" if interval else "unavailable"
        estimate = model["median_projected_compute95_hours"]
        estimate_text = f"{estimate:.3g}" if estimate is not None else "not estimable"
        readme.append(f"| {label} | {model['positive_slope_trials']}/{model['total_trials']} | {estimate_text} | {interval_text} |")
    readme.extend(["", f"All arm summaries, dispatch reasons, per-job coverage database hashes, seeded source/ELF inputs, and the frozen preregistration are retained beside this file. Raw simulator databases/logs are under {raw}.", ""])
    (study / "README.md").write_text("\n".join(readme))
    print(json.dumps({"paired_trials": len(trials), "endpoint": summary["adaptive_minus_static_bins"],
                      "auc": summary["adaptive_minus_static_auc_closure_fraction"]}, indent=2))


if __name__ == "__main__":
    main()
