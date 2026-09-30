"""Render compact results from one completed, real MediumBOOM paired pilot."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PILOT = ROOT / "artifacts_mediumboom/kapil_qualification/online_pilot_20260924_v2"
RAW = Path("/private/tmp/mediumboom-paired-pilot-20260924/pilot02/pilot02")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    arms = {}
    for arm in ("static_hybrid", "adaptive"):
        out = PILOT / arm
        summary = json.loads((out / "summary.json").read_text())
        events = [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()]
        order = [e for e in events if e["event"] == "job_dispatched"]
        completed = {e["job_id"]: e["result"] for e in events if e["event"] == "job_completed"}
        job_summaries = {}
        for job in summary["coverage_trace"]:
            if job["job_id"] is None:
                continue
            jid = job["job_id"]
            work = RAW / arm / jid
            summary_file = work / "coverage_summary.json"
            cov = work / "coverage.dat"
            log = work / "sim.log"
            formal_log = work / "sby/logfile.txt"
            formal_status = work / "sby/status"
            if summary_file.exists():
                native = json.loads(summary_file.read_text())
                hit_ids = native["families"]["scenario"]["hit_family_ids"]
                input_hash = native.get("input_sha256")
                runtime_seconds = native.get("elapsed_seconds")
            else:
                hit_ids = completed.get(jid, {}).get("hit_point_ids", [])
                input_hash = None
                runtime_seconds = None
            job_summaries[jid] = {
                "scenario_family_hits": hit_ids,
                "input_sha256": input_hash,
                "runtime_seconds": runtime_seconds,
                "coverage_dat_sha256": sha256(cov) if cov.exists() else None,
                "sim_log_sha256": sha256(log) if log.exists() else None,
                "formal_log_sha256": sha256(formal_log) if formal_log.exists() else None,
                "formal_status_sha256": sha256(formal_status) if formal_status.exists() else None,
            }
        arms[arm] = {
            "summary": summary,
            "dispatch_order": [{"job_id": e["job_id"], "engine": e["engine"], "reason": e["reason"]} for e in order],
            "yield_based_dispatches": sum("recent yield=" in e["reason"] for e in order),
            "jobs": job_summaries,
        }
    result = {
        "study": "post-pilot exploratory follow-up pair; not independent or confirmatory and not the registered multi-trial study",
        "metric": "Kapil native semantic cover-family IDs: 66 points from verification/boom/catalog.json; no legacy 51-point proxy, activation-counter or toggle-bin credit",
        "arms": arms,
        "interpretation": "The 95% target was not attained if both arm summaries say false; compute-to-95 is therefore undefined. This follow-up was designed after outcomes from the first pilot were observed, reuses the same inputs, and uses local runtime; it is post-pilot method development, not independent or confirmatory evidence. Interpret closure and dispatch trace as exploratory measurements only.",
    }
    (PILOT / "results.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    rows = []
    for arm, label in (("static_hybrid", "Static-Hybrid"), ("adaptive", "Adaptive")):
        s = arms[arm]["summary"]
        rows.append(
            f"| {label} | {s['completed_jobs']}/{s['dispatched_jobs']} | "
            f"{s['final_hit_points']}/66 ({100*s['final_sound_closure']:.2f}%) | "
            f"{s['total_compute_hours']:.4f} | {s['wall_time_hours']*60:.2f} | "
            f"{'yes' if s['reached_target'] else 'no'} | {s['stop_reason']} |"
        )
    order_lines = []
    for arm, label in (("static_hybrid", "Static-Hybrid"), ("adaptive", "Adaptive")):
        sequence = " -> ".join(x["job_id"] for x in arms[arm]["dispatch_order"])
        order_lines.append(f"- {label}: `{sequence}`")
    readme = """# MediumBOOM paired scheduler deadline pilot (2026-09-24)

This is a real, single-pair follow-up pilot using the controller's `static_hybrid` and
`adaptive` policies, local asynchronous execution, the assertion-enabled
MediumBoomV3Config Verilator binary, native Verilator `v_user` counters, and a
bounded DTLB SBY cover query. It is **not** a replication of the registered
multi-trial 95%-closure study. Only the 66 semantic cover families in Kapil's
`verification/boom/catalog.json` count; the 51-point legacy proxy, activation
counters, and toggle bins do not contribute to this endpoint.

## Measured arm outcomes

| Arm | Successful jobs | Sound cover families | Compute-slot hours | Wall minutes | Reached 95%? | Stop reason |
|---|---:|---:|---:|---:|---|---|
""" + "\n".join(rows) + """

The same six hash-frozen jobs and 0.5 compute-hour ceiling were offered to each
arm. The five mapped DTLB cover goals reached in the formal job are
`tlb.full_fence_late_refill`, `tlb.sfence_ptw_accept`,
`tlb.sfence_refill_collision`, `tlb.invalidated_walk_returns`, and
`tlb.superpage_refill`; each is counted by exact catalog identity. The 95%
endpoint is undefined if not attained; no
compute-to-target value or percentage reduction is imputed. These are bounded
cover witnesses, not assertion proofs. Per-job ELF, coverage database, and log hashes are in
`results.json`; raw coverage databases/logs remain outside Git under
`/private/tmp/mediumboom-paired-pilot-20260924/pilot02/pilot02/`.

## Observed dispatch order

""" + "\n".join(order_lines) + """

This follow-up has one matched pair, a finite six-job roster, one local host,
and no independent seed-level replications. Three CRV inputs are paired
with two directed inputs and one deterministic formal query. The b3 CRV and
both directed ELF inputs were also present in earlier native characterization;
their re-execution is reproducibility evidence, not held-out evaluation. The
follow-up was designed after first-pilot outcomes had been observed and reuses
the same inputs, so it is not independent or confirmatory. Repeated engine
lanes now permit measured-yield choices, but this small roster still does not
establish general policy superiority. Report this as post-pilot method
development, not completion of the registered primary study.
"""
    (PILOT / "README.md").write_text(readme)
    print(json.dumps({arm: {**{k: v for k, v in val["summary"].items() if k in ("completed_jobs", "dispatched_jobs", "final_hit_points", "final_sound_closure", "total_compute_hours", "wall_time_hours", "reached_target", "stop_reason")}, "yield_based_dispatches": val["yield_based_dispatches"]} for arm, val in arms.items()}, indent=2))


if __name__ == "__main__":
    main()
