"""Analyze the prospectively frozen 0.25-hour MediumBOOM holdout."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path

from summarize_mediumboom_open_toggle_replicates import (
    arm_result,
    auc,
    bootstrap_mean_ci,
    exact_sign_p,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STUDY = ROOT / "artifacts_mediumboom/kapil_qualification/open_toggle_1000_execution_study"
FIRST_HOLDOUT = 26
LAST_HOLDOUT = 50
FIRST_CORRECTION = 31
LAST_CORRECTION = 50
FIRST_CAPPED_CONCURRENCY = 36


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def checkpoint(trace: list[dict], horizon: float, target_count: int) -> tuple[int, int]:
    """Return accepted bins and completed jobs known by a compute-hour landmark."""
    if not trace or trace[0]["compute_hours"] != 0:
        raise ValueError("coverage trace lacks zero-time baseline")
    last_time = -1.0
    last_bins = -1
    at_horizon = (0, 0)
    for index, row in enumerate(trace):
        when = float(row["compute_hours"])
        exact_bins = float(row["sound_closure"]) * target_count
        bins = round(exact_bins)
        if abs(exact_bins - bins) > 1e-6:
            raise ValueError(f"closure does not represent integral bin count: {exact_bins}")
        if when < last_time or bins < last_bins:
            raise ValueError("coverage trace is not monotone")
        if index and row.get("job_id") is None:
            raise ValueError("non-baseline trace point lacks a completed job")
        if when <= horizon:
            at_horizon = (bins, index)
        last_time, last_bins = when, bins
    return at_horizon


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", type=Path, default=DEFAULT_STUDY)
    parser.add_argument("--cohort", choices=("original", "correction", "capped_concurrency"),
                        default="original")
    args = parser.parse_args()
    study = args.study.resolve()
    if args.cohort == "capped_concurrency":
        protocol_path = study / "host_contention_amendment.json"
        trial_ids = [f"trial_{i:02d}" for i in range(FIRST_CAPPED_CONCURRENCY, LAST_CORRECTION + 1)]
        output = study / "capped_concurrency_exploratory_results.json"
        measurement_protocol = json.loads((study / "binding_budget_holdout_preregistration.json").read_text())
    elif args.cohort == "correction":
        protocol_path = study / "orphan_incident_correction_preregistration.json"
        trial_ids = [f"trial_{i:02d}" for i in range(FIRST_CORRECTION, LAST_CORRECTION + 1)]
        output = study / "orphan_incident_correction_results.json"
    else:
        protocol_path = study / "binding_budget_holdout_preregistration.json"
        trial_ids = [f"trial_{i:02d}" for i in range(FIRST_HOLDOUT, LAST_HOLDOUT + 1)]
        output = study / "binding_budget_holdout_results.json"
    if args.cohort != "capped_concurrency":
        measurement_protocol = json.loads(protocol_path.read_text())
    protocol = json.loads(protocol_path.read_text())
    parent = study / "preregistration.json"
    if sha256(parent) != measurement_protocol["parent_study_preregistration_sha256"]:
        raise SystemExit("parent study preregistration changed after holdout freeze")
    frozen = json.loads(parent.read_text())
    target = int(measurement_protocol["frozen_residual_open_point_count"])
    horizon = float(measurement_protocol["primary_horizon_compute_hours_per_arm"])
    if args.cohort == "capped_concurrency":
        if protocol["fresh_untouched_cohort"] != "trial_36 through trial_50" or protocol["fresh_pair_count"] != len(trial_ids):
            raise SystemExit("host-contention amendment does not match exploratory cohort")
    if target != frozen["frozen_residual_open_point_count"]:
        raise SystemExit("holdout and parent toggle registries differ")
    if not set(trial_ids).issubset({row["trial_id"] for row in frozen["trials"]}):
        raise SystemExit("a holdout trial is missing from the frozen parent study")

    rows = []
    seen_input_elfs = set()
    for trial_id in trial_ids:
        trial = study / "inputs" / trial_id
        frozen_inputs = {
            job["job_id"]: job["elf_sha256"]
            for job in json.loads((trial / "preregistration.json").read_text())["input_hashes"]
        }
        if len(frozen_inputs) != 10:
            raise SystemExit(f"frozen ten-job input catalog is incomplete: {trial_id}")
        for elf_hash in frozen_inputs.values():
            if elf_hash in seen_input_elfs:
                raise SystemExit(f"input ELF repeated between holdout pairs: {trial_id}")
            seen_input_elfs.add(elf_hash)
        arms = {}
        for arm in ("static_hybrid", "adaptive"):
            if not (trial / arm / "summary.json").is_file():
                raise SystemExit(f"holdout pair incomplete: {trial_id}/{arm}")
            result = arm_result(trial, arm, target, horizon)
            if {job["job_id"] for job in result["jobs"]} != set(frozen_inputs):
                raise SystemExit(f"executed jobs differ from frozen input catalog: {trial_id}/{arm}")
            for job in result["jobs"]:
                if job["status"] == "success":
                    if (job["input_elf_sha256"] != frozen_inputs[job["job_id"]]
                            or not job["evidence_sha256"] or not job["coverage_dat_sha256"]
                            or not job["coverage_database_verified"] or not job["sim_log_sha256"]):
                        raise SystemExit(f"missing or mismatched native evidence: {trial_id}/{arm}/{job['job_id']}")
                elif job["status"] == "timeout":
                    if job["new_target_bins"] != 0 or "timeout" not in job["error_summary"].lower():
                        raise SystemExit(f"invalid timeout evidence: {trial_id}/{arm}/{job['job_id']}")
                else:
                    raise SystemExit(f"unexpected job status: {trial_id}/{arm}/{job['job_id']}: {job['status']}")
            bins, jobs = checkpoint(result["coverage_trace"], horizon, target)
            engine_mix = {}
            for event in result["coverage_trace"][1:jobs + 1]:
                engine = event["engine"]
                engine_mix[engine] = engine_mix.get(engine, 0) + 1
            if result["completed_jobs"] != 10:
                raise SystemExit(f"missing catalog job: {trial_id}/{arm}")
            if result["compute_hours"] <= horizon:
                raise SystemExit(f"binding budget did not precede catalog exhaustion: {trial_id}/{arm}")
            if result["new_target_bins_at_budget"] != round(
                result["coverage_trace"][-1]["sound_closure"] * target
            ):
                raise SystemExit(f"event evidence and summary trace disagree: {trial_id}/{arm}")
            arms[arm] = {
                "bins_by_horizon": bins,
                "completed_jobs_by_horizon": jobs,
                "completed_engine_mix_by_horizon": engine_mix,
                "time_averaged_bins_by_horizon": auc(result["coverage_trace"], horizon) * target,
                "full_catalog_bins": result["new_target_bins_at_budget"],
                "full_catalog_compute_hours": result["compute_hours"],
                "successful_jobs": result["successful_jobs"],
                "status_counts": result["failed_job_statuses"],
                "campaign_fingerprint": result["campaign_fingerprint"],
                "dispatch_order": result["dispatch_order"],
            }
        if arms["static_hybrid"]["campaign_fingerprint"] != arms["adaptive"]["campaign_fingerprint"]:
            raise SystemExit(f"paired campaign fingerprints differ: {trial_id}")
        rows.append({
            "trial_id": trial_id,
            **arms,
            "adaptive_minus_static_bins": (
                arms["adaptive"]["bins_by_horizon"] - arms["static_hybrid"]["bins_by_horizon"]
            ),
            "adaptive_minus_static_time_averaged_bins": (
                arms["adaptive"]["time_averaged_bins_by_horizon"]
                - arms["static_hybrid"]["time_averaged_bins_by_horizon"]
            ),
        })

    differences = [row["adaptive_minus_static_bins"] for row in rows]
    interval = bootstrap_mean_ci(differences)
    p_value = exact_sign_p(differences)
    mean_difference = statistics.mean(differences)
    confirmatory_direction = None
    if args.cohort != "capped_concurrency" and interval[0] > 0 and p_value < 0.05:
        confirmatory_direction = "adaptive_higher"
    elif args.cohort != "capped_concurrency" and interval[1] < 0 and p_value < 0.05:
        confirmatory_direction = "adaptive_lower"
    auc_differences = [row["adaptive_minus_static_time_averaged_bins"] for row in rows]
    result = {
        "protocol": protocol_path.name,
        "protocol_sha256": sha256(protocol_path),
        "cohort": args.cohort,
        "analysis_status": ("exploratory_capped_concurrency_subset" if args.cohort == "capped_concurrency"
                            else "preregistered"),
        "parent_preregistration_sha256": sha256(parent),
        "paired_trials": len(rows),
        "horizon_compute_hours_per_arm": horizon,
        "frozen_residual_toggle_bins": target,
        "primary": {
            "static_mean_bins": statistics.mean(row["static_hybrid"]["bins_by_horizon"] for row in rows),
            "adaptive_mean_bins": statistics.mean(row["adaptive"]["bins_by_horizon"] for row in rows),
            "adaptive_minus_static_mean_bins": mean_difference,
            "adaptive_minus_static_median_bins": statistics.median(differences),
            "mean_difference_pair_bootstrap_95_percent_ci": interval,
            "two_sided_exact_sign_test_p": p_value,
            "confirmatory_direction_under_frozen_rule": confirmatory_direction,
            "wins_ties_losses": {
                "adaptive_higher": sum(value > 0 for value in differences),
                "equal": sum(value == 0 for value in differences),
                "adaptive_lower": sum(value < 0 for value in differences),
            },
        },
        "secondary_time_averaged_bins": {
            "adaptive_minus_static_mean": statistics.mean(auc_differences),
            "mean_difference_pair_bootstrap_95_percent_ci": bootstrap_mean_ci(auc_differences),
            "two_sided_exact_sign_test_p": exact_sign_p(auc_differences),
        },
        "trials": rows,
    }
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(output), "paired_trials": len(rows), **result["primary"]}, indent=2))


if __name__ == "__main__":
    main()
