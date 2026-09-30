"""Freeze seeded paired CRV/directed corpora on the residual open-toggle set."""
from __future__ import annotations

import hashlib
import json
import argparse
from datetime import datetime, timezone
import subprocess
from pathlib import Path

from chialoop.core.catalog import JobCatalog
from chialoop.core.config import CampaignSpec
from chialoop.core.types import CoveragePoint, Engine, JobSpec, ResourceCapacity

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "artifacts_mediumboom/kapil_qualification/open_toggle_multi_seed_study"
PILOT = ROOT / "artifacts_mediumboom/kapil_qualification/open_toggle_study"
CORPORA = STUDY / "inputs"
SIM = Path("/private/tmp/chipyard-kapil-e602/sims/verilator/simulator-chipyard.harness-MediumBoomV3Config")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", type=Path, default=STUDY)
    parser.add_argument("--budget-compute-hours", type=float, default=0.28)
    parser.add_argument("--exclude-input-manifest", action="append", type=Path, default=[])
    args = parser.parse_args()
    study = args.study.resolve()
    corpora = study / "inputs"
    generator_manifest = json.loads((corpora / "generator_manifest.json").read_text())
    pilot_results = json.loads((PILOT / "results.json").read_text())
    monitor_modules = json.loads((PILOT / "monitor_modules.json").read_text())
    prior_ids = {point for arm in pilot_results["arms"].values()
                 for job in arm["jobs"] for point in job["hit_point_ids"]}
    before = json.loads((PILOT / "coverage_points.json").read_text())
    all_open = {row["point_id"]: row["partition_id"] for row in before["points"]}
    remaining = sorted(set(all_open) - prior_ids)
    if not remaining:
        raise SystemExit("no residual target bins after excluding the completed pilot")
    if not SIM.is_file():
        raise SystemExit(f"MediumBOOM simulator is missing: {SIM}")
    root_sha = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    sim_sha = sha256(SIM)
    artifact_version = f"mediumboom:{root_sha}:sim:{sim_sha}:seeded-open-toggle-v2"

    seen_elfs = {entry["input_elf_sha256"] for arm in pilot_results["arms"].values()
                 for entry in arm["jobs"] if entry.get("input_elf_sha256")}
    for manifest_path in args.exclude_input_manifest:
        old_manifest = json.loads(manifest_path.read_text())
        seen_elfs.update(job["elf_sha256"] for trial in old_manifest["replicates"]
                         for job in trial["jobs"])
    input_manifests = []
    trial_dirs = sorted(path for path in corpora.iterdir() if path.is_dir() and path.name.startswith("trial_"))
    if len(trial_dirs) != generator_manifest["replicate_count"]:
        raise SystemExit("replicate input directory count does not match generator manifest")
    study.mkdir(parents=True, exist_ok=True)
    shared_points_path = study / "coverage_points.json"
    shared_points_path.write_text(json.dumps(
        {"points": [{"point_id": point, "partition_id": all_open[point]} for point in remaining]},
        separators=(",", ":")) + "\n")
    monitor_modules_path = study / "monitor_modules.json"
    monitor_modules_path.write_text(json.dumps(monitor_modules, indent=2) + "\n")
    prereg = {
        "status": "frozen_before_any_multi_seed_campaign_outcome_collection",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "objective": "paired online Static-Hybrid versus Adaptive policy comparison on fresh, seeded MediumBOOM CRV/directed workloads",
        "primary_endpoint": f"native Verilator signal-toggle bins newly hit at the fixed {args.budget_compute_hours} compute-hour horizon, divided by the common frozen residual open set; report paired workload-level differences and uncertainty",
        "secondary_endpoint": "compute-hours to 95% residual-set closure only when attained; otherwise report not attained",
        "point_universe_before_pilot": len(all_open),
        "pilot_hit_point_ids_excluded_before_this_study": sorted(prior_ids),
        "pilot_hit_point_count_excluded": len(prior_ids),
        "pilot_results_sha256": sha256(PILOT / "results.json"),
        "pilot_preregistration_sha256": sha256(PILOT / "preregistration.json"),
        "pilot_open_registry_sha256": sha256(PILOT / "coverage_points.json"),
        "monitor_modules_sha256": sha256(monitor_modules_path),
        "frozen_residual_open_point_count": len(remaining),
        "scope": "native positive toggle counts for exact registered signal-bit IDs in the BOOM monitor-module subset; source-line and semantic-family counts are not included",
        "cross_replicate_rule": "Each replicate starts from the same frozen post-pilot residual baseline so paired workload corpora are comparable. Workload inputs are unique across replicates; naturally overlapping hits across distinct workloads are retained as per-replicate outcomes and are not pooled as unique chip-wide closure.",
        "paired_rule": "Within each trial, Static-Hybrid and Adaptive receive the byte-identical catalog and point registry. Adaptive decisions may use only completed measured results. One simulator license enforces one in-flight simulation per arm.",
        "catalog_per_trial": {"crv": 8, "directed": 2, "static_ratio": "4:1"},
        "budget_compute_hours_per_arm": args.budget_compute_hours,
        "sample_size_amendment": "This is a separately preregistered 50-pair, 1,000-simulation-execution study; distinct seeds from the initial 10-pair 0.28-hour study are excluded. The per-arm budget is expanded to permit all ten catalog jobs to run, and results are not pooled with the initial study.",
        "target_closure": 0.95,
        # Keep one formal slot available in the declared host capacity. This
        # toggle-only catalog contains no formal jobs, so the slot is idle;
        # formal evidence is not mixed into the CRV/directed comparison.
        "resources": {"compute": 2, "sim_license": 1, "formal_license": 1},
        "run_plan": "Run each paired corpus with its two policy arms concurrently; batch no more than five pairs (ten simulator campaigns) at once on the 10-core host.",
        "generator": generator_manifest,
        "campaign_commit": root_sha,
        "simulator_sha256": sim_sha,
        "trials": [],
    }
    for trial_dir in trial_dirs:
        trial_id = trial_dir.name
        trial_info = next(x for x in generator_manifest["replicates"] if x["trial"] == trial_id)
        points_path = shared_points_path
        jobs = []
        input_rows = []
        for lane, count, engine, partition in (
            ("crv", 8, Engine.CRV, "p_frontend"),
            ("directed", 2, Engine.DIRECTED, "p_issue_forward"),
        ):
            for index in range(count):
                job_id = f"{lane}_{index:02d}"
                elf = trial_dir / f"{job_id}.elf"
                assembly = trial_dir / f"{job_id}.S"
                stats = trial_dir / f"{job_id}.stats"
                if not all(path.is_file() for path in (elf, assembly, stats)):
                    raise SystemExit(f"incomplete fresh input {trial_id}/{job_id}")
                elf_hash = sha256(elf)
                if elf_hash in seen_elfs:
                    raise SystemExit(f"input ELF repeats prior measured input: {trial_id}/{job_id}")
                seen_elfs.add(elf_hash)
                details = next(x for x in trial_info["jobs"] if x["job_id"] == job_id)
                if elf_hash != details["elf_sha256"] or sha256(assembly) != details["assembly_sha256"]:
                    raise SystemExit(f"input hash mismatch against generator freeze: {trial_id}/{job_id}")
                input_row = {"job_id": job_id, "seed": details["seed"],
                             "assembly_sha256": sha256(assembly), "elf_sha256": elf_hash,
                             "stats_sha256": sha256(stats)}
                input_rows.append(input_row)
                jobs.append(JobSpec(
                    job_id=job_id, engine=engine, partition_id=partition,
                    sequence_in_lane=index, target_point_ids=tuple(remaining),
                    seed_or_query_id=f"elf-sha256:{elf_hash}", timeout_seconds=300,
                    artifact_version=artifact_version,
                    payload={"input_elf": str(elf.resolve()), "sha256": elf_hash,
                             "assembly_sha256": sha256(assembly), "work_type": lane},
                ))
        catalog = JobCatalog(jobs, shared_target_file=shared_points_path)
        catalog.write_jsonl(trial_dir / "job_catalog.jsonl")
        spec = CampaignSpec(
            campaign_id=f"mediumboom-toggle-{trial_id}-paired-20260924",
            artifact_version=artifact_version, assumption_version="not-applicable-no-formal-jobs",
            target_closure=0.95, budget_compute_hours=args.budget_compute_hours,
            resources=ResourceCapacity(compute=2, sim_license=1, formal_license=1),
            recent_window=10, n1=1,
        )
        spec.write_json(trial_dir / "campaign.json")
        trial_prereg = {
            "status": "frozen_before_trial_outcome_collection",
            "trial_id": trial_id,
            "base_seed": trial_info["base_seed"],
            "campaign_commit": root_sha,
            "artifact_version": artifact_version,
            "campaign_fingerprint_inputs": {
                "campaign_sha256": sha256(trial_dir / "campaign.json"),
                "catalog_sha256": sha256(trial_dir / "job_catalog.jsonl"),
                "coverage_points_sha256": sha256(points_path),
            },
            "input_hashes": input_rows,
            "policy_pair": ["static_hybrid", "adaptive"],
            "primary_endpoint": f"accepted unique target bins divided by the common residual registry at the {args.budget_compute_hours} compute-hour budget; report both bins and fraction",
            "adaptive_information_rule": "policy observes only accepted results from already completed jobs; no corpus outcome is visible before execution",
        }
        (trial_dir / "preregistration.json").write_text(json.dumps(trial_prereg, indent=2, sort_keys=True) + "\n")
        prereg["trials"].append({"trial_id": trial_id,
                                 "base_seed": trial_info["base_seed"],
                                 "jobs": input_rows,
                                 "catalog_sha256": sha256(trial_dir / "job_catalog.jsonl"),
                                 "coverage_points_sha256": sha256(points_path)})
    (study / "preregistration.json").write_text(json.dumps(prereg, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"trials": len(trial_dirs), "prior_pilot_points_excluded": len(prior_ids),
                      "residual_open_points": len(remaining),
                      "budget_compute_hours_per_arm": args.budget_compute_hours,
                      "frozen": str(study / "preregistration.json")}, indent=2))


if __name__ == "__main__":
    main()
