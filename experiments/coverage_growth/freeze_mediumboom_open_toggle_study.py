"""Freeze a fresh MediumBOOM CRV campaign against previously open monitor toggles."""
from __future__ import annotations

import hashlib
import json
import random
import subprocess
from pathlib import Path

from chialoop.core.catalog import JobCatalog
from chialoop.core.config import CampaignSpec
from chialoop.core.types import CoveragePoint, Engine, JobSpec, ResourceCapacity

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts_mediumboom/kapil_qualification/open_toggle_study"
REFERENCE = Path("/private/tmp/mediumboom-instrumented-five-20260924/directed_0000/coverage.dat")
PRIOR_ROOTS = tuple(Path(p) for p in (
    "/private/tmp/mediumboom-instrumented-five-20260924",
    "/private/tmp/mediumboom-paired-pilot-20260924",
    "/private/tmp/mediumboom-fp-smoke",
    "/private/tmp/mediumboom-rerun-20260924",
    "/private/tmp/online-pilot-smoke-b1",
    "/private/tmp/kapil-e602-run-directed",
))
INPUTS = OUT / "inputs"
SIM = Path("/private/tmp/chipyard-kapil-e602/sims/verilator/simulator-chipyard.harness-MediumBoomV3Config")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fields(line: str) -> dict[str, str]:
    metadata = line.split("' ", 1)[0].split("'", 1)[1]
    return {key: value for field in metadata.split("\x01") if "\x02" in field
            for key, value in (field.split("\x02", 1),)}


def toggle_id(record: dict[str, str]) -> str:
    module = record["page"].removeprefix("v_toggle/")
    identity = "\x1f".join((module, record.get("h", ""), record["o"]))
    return "toggle:" + hashlib.sha256(identity.encode()).hexdigest()


def partition(module: str) -> str:
    if module in {"BoomFrontend", "FetchTargetQueue"}:
        return "p_frontend"
    if module in {"BoomNonBlockingDCache", "LSU"}:
        return "p_memory_lsu"
    if module == "NBDTLB":
        return "p_tlb_formal"
    if module.startswith(("RenameBusyTable", "RenameFreeList", "RenameMapTable")) or module == "Rob":
        return "p_rename_rob"
    return "p_issue_forward"


def main() -> None:
    monitor_modules: set[str] = set()
    for line in REFERENCE.open(errors="replace"):
        if "page\x02v_user/" in line:
            monitor_modules.add(fields(line)["page"].split("/", 1)[1])

    universe: dict[str, dict[str, str]] = {}
    for line in REFERENCE.open(errors="replace"):
        if "page\x02v_toggle/" not in line:
            continue
        record = fields(line)
        module = record["page"].split("/", 1)[1]
        if module in monitor_modules:
            universe[toggle_id(record)] = {"module": module, "signal_bit": record["o"],
                                           "hierarchy": record.get("h", "")}

    prior_files = sorted({p for root in PRIOR_ROOTS if root.exists()
                          for p in root.rglob("coverage.dat") if "verbose" not in p.parts})
    previously_hit: set[str] = set()
    prior_hashes = []
    for path in prior_files:
        prior_hashes.append({"path": str(path), "sha256": sha256(path)})
        for line in path.open(errors="replace"):
            if "page\x02v_toggle/" not in line or line.rsplit("' ", 1)[1].strip() == "0":
                continue
            record = fields(line)
            if record["page"].split("/", 1)[1] in monitor_modules:
                previously_hit.add(toggle_id(record))

    open_points = sorted(set(universe) - previously_hit)
    if not open_points:
        raise RuntimeError("all monitored toggle bins already have prior hit evidence")
    OUT.mkdir(parents=True, exist_ok=True)
    points = [CoveragePoint(point_id=point_id,
                            partition_id=partition(universe[point_id]["module"])).__dict__
              for point_id in open_points]
    (OUT / "coverage_points.json").write_text(
        json.dumps({"points": points}, separators=(",", ":")) + "\n")
    (OUT / "monitor_modules.json").write_text(
        json.dumps(sorted(monitor_modules), indent=2) + "\n")

    root_sha = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    torture_root = Path("/private/tmp/riscv-torture-source-20260924")
    torture_sha = subprocess.check_output(
        ["git", "-C", str(torture_root), "rev-parse", "HEAD"], text=True).strip()
    sim_sha = sha256(SIM)
    artifact_version = f"mediumboom:{root_sha}:sim:{sim_sha}:open-monitor-toggle-v1"
    catalog_jobs = []
    for engine, prefix, count, work_type in (
        (Engine.CRV, "crv", 3, "crv"),
        (Engine.DIRECTED, "directed", 2, "directed"),
    ):
        for index in range(count):
            job_id = f"{prefix}_{index:02d}"
            elf = INPUTS / f"{job_id}.elf"
            catalog_jobs.append(JobSpec(
                job_id=job_id, engine=engine,
                partition_id=("p_frontend" if engine is Engine.CRV else "p_issue_forward"),
                sequence_in_lane=index, target_point_ids=tuple(open_points),
                seed_or_query_id=f"elf-sha256:{sha256(elf)}", timeout_seconds=300,
                artifact_version=artifact_version,
                payload={"input_elf": str(elf.resolve()), "sha256": sha256(elf),
                         "assembly_sha256": sha256(INPUTS / f"{job_id}.S"),
                         "work_type": work_type},
            ))
    catalog = JobCatalog(catalog_jobs)
    catalog.write_jsonl(OUT / "job_catalog.jsonl")
    spec = CampaignSpec(
        campaign_id="mediumboom-open-monitor-toggle-paired-20260924",
        artifact_version=artifact_version, assumption_version="not-applicable-no-formal-jobs",
        target_closure=0.95, budget_compute_hours=0.5,
        resources=ResourceCapacity(compute=2, sim_license=1, formal_license=0),
        recent_window=10, n1=1,
    )
    spec.write_json(OUT / "campaign.json")
    manifest = {
        "status": "frozen_before_new_campaign_outcome_collection",
        "scope": "previously-unhit signal-toggle bins in the 15 generated BOOM modules containing named v_user monitors; no 66-point proxy and no activity bins already hit by any prior valid database",
        "toggle_point_model": "one point per Verilator v_toggle signal bit, identified by generated module + hierarchy + signal-bit name; a positive native v_toggle count closes the point",
        "point_universe_before_exclusion": len(universe),
        "previously_hit_points_excluded": len(set(universe) & previously_hit),
        "open_target_points": len(open_points),
        "previous_run_database_count": len(prior_files),
        "previous_run_databases": prior_hashes,
        "reference_database_sha256": sha256(REFERENCE),
        "simulator_sha256": sim_sha,
        "riscv_torture_commit": torture_sha,
        "balanced_config_sha256": sha256(ROOT / "adapters/mediumboom/stimulus/mediumboom.config"),
        "directed_mix_config_sha256": sha256(ROOT / "adapters/mediumboom/stimulus/mediumboom_directed.config"),
        "campaign_commit": root_sha,
        "candidate_jobs": [{"job_id": job.job_id, "engine": job.engine.value,
                            "sha256": job.payload["sha256"],
                            "assembly_sha256": job.payload["assembly_sha256"]}
                           for job in catalog.jobs],
        "primary_endpoint": "fraction of the 12,146 (or exact frozen count above) previously unhit monitor-toggle bins reached by fresh CRV/directed ELF jobs; 95% of this remaining-open set is the target; if not attained, compare closure at catalog exhaustion and report compute-hours only as cost",
        "prior_result_exclusion": "Coverage databases from the earlier characterization, pilots, and related MediumBOOM toggle runs are scanned before freezing. A monitor-toggle point hit in any valid prior database is excluded from the target registry and cannot be credited again.",
        "arm_policy": "identical fresh CRV and directed ELF catalog, same local async executor/resources; Static-Hybrid follows the core frozen 4:1 CRV:directed manifest; Adaptive probes both nodes and ranks incremental toggle bins per compute-hour from each node's last 10 completed runs",
        "run_order": random.Random(20260924).choice(["static_hybrid", "adaptive"]),
        "limitation": "single local pair with reused generator configuration is exploratory method development, not the preregistered multi-seed confirmatory evaluation",
    }
    (OUT / "preregistration.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"point_universe": len(universe),
                      "prior_hits_excluded": len(set(universe) & previously_hit),
                      "open_target_points": len(open_points), "prior_db_count": len(prior_files),
                      "jobs": len(catalog.jobs), "manifest": str(OUT / "preregistration.json")}, indent=2))


if __name__ == "__main__":
    main()
