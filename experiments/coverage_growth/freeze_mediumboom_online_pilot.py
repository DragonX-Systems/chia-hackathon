"""Freeze hash-pinned campaign inputs for the 2026-09-24 MediumBOOM pilot."""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from chialoop.core.types import CoveragePoint, Engine, JobSpec
from chialoop.core.catalog import JobCatalog
from chialoop.core.config import CampaignSpec

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts_mediumboom/kapil_qualification/online_pilot_20260924_v2"
WORK = ROOT / "runs/mediumboom/kaps_sep22-b123-smoke/work"
ELFS = {
    "crv_b1": WORK / "b1_1/riscv_dv/t.elf",
    "crv_b2": WORK / "b2_1/riscv_dv/t.elf",
    "crv_b3": WORK / "b3_1/riscv_dv/t.elf",
    "directed_0000": WORK / "b3_1/directed_0000/directed/t.elf",
    "directed_0001": WORK / "b3_1/directed_0001/directed/t.elf",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    properties = json.loads((ROOT / "verification/boom/catalog.json").read_text())["properties"]
    families = sorted(p["id"] for p in properties if p["kind"] == "cover")
    def partition(family: str) -> str:
        prefix = family.split(".", 1)[0]
        group = {
            "fetch": "frontend", "ftq": "frontend",
            "free": "rename_rob", "map": "rename_rob", "rob": "rename_rob",
            "issue": "issue_forward", "forward": "issue_forward",
            "busy": "memory_lsu", "dcache": "memory_lsu", "lsu": "memory_lsu", "tlb": "tlb_formal",
        }.get(prefix, "misc")
        return f"p_{group}"
    points = [CoveragePoint(point_id=f, partition_id=partition(f)).__dict__ for f in families]
    (OUT / "coverage_points.json").write_text(json.dumps({"points": points}, indent=2, sort_keys=True) + "\n")

    root_sha = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    chipyard = Path("/private/tmp/chipyard-kapil-e602")
    chip_sha = subprocess.check_output(["git", "-C", str(chipyard), "rev-parse", "HEAD"], text=True).strip()
    sim = chipyard / "sims/verilator/simulator-chipyard.harness-MediumBoomV3Config"
    executor_file = ROOT / "experiments/coverage_growth/mediumboom_native_executor.py"
    freeze_file = Path(__file__).resolve()
    artifact_version = f"mediumboom:{root_sha}:sim:{sha256(sim)}:executor:{sha256(executor_file)}"
    assumption_version = hashlib.sha256((ROOT / "artifacts_mediumboom/kapil_qualification/formal_dtlb/NBDTLB_cover.sby").read_bytes()).hexdigest()
    spec = CampaignSpec(campaign_id="mediumboom-kapil-native-paired-pilot-20260924",
                        artifact_version=artifact_version, assumption_version=assumption_version,
                        target_closure=0.95, budget_compute_hours=0.5,
                        resources=__import__("chialoop.core.types", fromlist=["ResourceCapacity"]).ResourceCapacity(compute=2, sim_license=1, formal_license=1),
                        recent_window=10, n1=1)
    spec.write_json(OUT / "campaign.json")
    all_ids = tuple(families)
    jobs = []
    for job_id, engine, part, sequence, elf_key in (
        ("crv_b1", Engine.CRV, "p_frontend", 0, "crv_b1"),
        ("crv_b2", Engine.CRV, "p_frontend", 1, "crv_b2"),
        ("crv_b3", Engine.CRV, "p_frontend", 2, "crv_b3"),
        ("directed_0000", Engine.DIRECTED, "p_issue_forward", 0, "directed_0000"),
        ("directed_0001", Engine.DIRECTED, "p_issue_forward", 1, "directed_0001"),
    ):
        path = ELFS[elf_key].resolve()
        jobs.append(JobSpec(job_id=job_id, engine=engine, partition_id=part,
                            sequence_in_lane=sequence, target_point_ids=all_ids,
                            seed_or_query_id=f"sha256:{sha256(path)}", timeout_seconds=300,
                            artifact_version=artifact_version,
                            payload={"input_elf": str(path), "sha256": sha256(path), "work_type": elf_key}))
    jobs.append(JobSpec(job_id="formal_dtlb_cover", engine=Engine.FORMAL,
                        partition_id="p_tlb_formal", sequence_in_lane=0,
                        target_point_ids=("tlb.full_fence_late_refill", "tlb.sfence_ptw_accept",
                                          "tlb.sfence_refill_collision", "tlb.invalidated_walk_returns",
                                          "tlb.superpage_refill"),
                        seed_or_query_id="NBDTLB_cover.sby:depth12",
                        timeout_seconds=120, artifact_version=artifact_version,
                        assumption_version=assumption_version,
                        payload={"sby_file": "NBDTLB_cover.sby", "required_goal": "cover__boom_tlb_full_fence_late_refill"}))
    JobCatalog(jobs).write_jsonl(OUT / "job_catalog.jsonl")
    manifest = {
        "status": "exploratory_followup_frozen_after_initial_pilot; outcomes_have_been_observed",
        "created_utc": "2026-09-24",
        "scope": "Kapil catalog's 66 semantic cover-property families only; no 51-point legacy proxy; no activation counters or toggle bins in the endpoint",
        "primary_endpoint": "attainment of 95% of the frozen 66 family IDs; report compute-hours only if attained",
        "secondary_pilot_endpoint": "final sound unique-family hits and compute-hours at catalog exhaustion under a 0.5-hour ceiling; unsuccessful/failed jobs remain reported",
        "arms": {"static_hybrid": "core StaticHybridPolicy frozen 4:1:1 CRV:directed:formal manifest over a finite 3:2:1 roster", "adaptive": "core AdaptivePolicy, probe each node then rank incremental bins per compute-hour from its last 10 completed runs"},
        "campaign_commit": root_sha,
        "chipyard_commit": chip_sha,
        "simulator_sha256": sha256(sim),
        "executor_sha256": sha256(executor_file),
        "freeze_script_sha256": sha256(freeze_file),
        "catalog_sha256": sha256(ROOT / "verification/boom/catalog.json"),
        "input_elf_sha256": {name: sha256(path) for name, path in ELFS.items()},
        "prior_measurement_disclosure": "b1/b2 ELFs and the first pilot arm outputs have already been observed; b3 riscv_dv and directed_0000/0001 were present in the earlier characterization. This follow-up is post-pilot method development and is not independent or confirmatory.",
        "formal_evidence": "credit only the five exact Kapil scenario-cover IDs mapped to reached SBY goals: tlb.full_fence_late_refill, tlb.sfence_ptw_accept, tlb.sfence_refill_collision, tlb.invalidated_walk_returns, tlb.superpage_refill; bounded reachability, not assertion proof",
        "lane_grouping": "three CRV jobs share p_frontend and two directed jobs share p_issue_forward to enable repeated policy choices within lanes",
        "coverage_evidence": "simulator must exit 0, emit Verilog $finish and coverage.dat; parse with verification/boom/summarize_verilator_coverage.py; only native Kapil scenario-family counters count",
    }
    (OUT / "preregistration.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"campaign": str(OUT), "points": len(points), "jobs": len(jobs), "hashes": manifest["input_elf_sha256"]}, indent=2))


if __name__ == "__main__":
    main()
