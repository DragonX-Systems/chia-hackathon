"""Summarize the completed fresh-ELF paired open-toggle campaign."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "artifacts_mediumboom/kapil_qualification/open_toggle_study"
RAW = Path("/private/tmp/mediumboom-open-toggle-20260924/toggle-pilot")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    frozen = json.loads((STUDY / "preregistration.json").read_text())
    open_count = frozen["open_target_points"]
    arms = {}
    for arm in ("static_hybrid", "adaptive"):
        out = STUDY / arm
        summary_path = out / "summary.json"
        if not summary_path.is_file():
            raise SystemExit(f"arm has not completed: {arm}")
        summary = json.loads(summary_path.read_text())
        events = [json.loads(line) for line in (out / "events.jsonl").read_text().splitlines()]
        dispatches = {e["job_id"]: e for e in events if e["event"] == "job_dispatched"}
        completed = {e["job_id"]: e for e in events if e["event"] == "job_completed"}
        order = sorted(dispatches, key=lambda jid: dispatches[jid]["sequence"])
        closed: set[str] = set()
        jobs = []
        for job_id in order:
            event = completed.get(job_id)
            if not event:
                jobs.append({"job_id": job_id, "status": "not_completed"})
                continue
            hit_ids = set(event["result"].get("hit_point_ids", []))
            new_ids = sorted(hit_ids - closed)
            closed.update(hit_ids)
            evidence_path = RAW / arm / job_id / "evidence.json"
            coverage_path = RAW / arm / job_id / "coverage.dat"
            jobs.append({
                "job_id": job_id,
                "engine": event["result"]["engine"],
                "status": event["result"]["status"],
                "dispatch_reason": dispatches[job_id]["reason"],
                "compute_hours": event["result"]["compute_hours"],
                "new_open_toggle_bins": len(new_ids),
                "hit_point_ids": sorted(hit_ids),
                "input_elf_sha256": json.loads(evidence_path.read_text()).get("input_elf_sha256")
                    if evidence_path.is_file() else None,
                "coverage_dat_sha256": sha256(coverage_path) if coverage_path.is_file() else None,
                "evidence_sha256": sha256(evidence_path) if evidence_path.is_file() else None,
            })
        arms[arm] = {
            "summary": summary,
            "dispatch_order": order,
            "dispatch_reasons": [dispatches[jid]["reason"] for jid in order],
            "successful_jobs": summary["completed_jobs"],
            "new_open_toggle_bins": len(closed),
            "open_target_points": open_count,
            "open_set_closure": len(closed) / open_count,
            "jobs": jobs,
        }

    stat = arms["static_hybrid"]["summary"]
    adapt = arms["adaptive"]["summary"]
    comparison = {
        "paired_catalog_fingerprint_matches": stat["campaign_fingerprint"] == adapt["campaign_fingerprint"],
        "static_catalog_exhausted": stat["stop_reason"] == "catalog_exhausted",
        "adaptive_catalog_exhausted": adapt["stop_reason"] == "catalog_exhausted",
        "target_reached_by_both": bool(stat["reached_target"] and adapt["reached_target"]),
        "compute_to_95_hours": None,
        "static_minus_adaptive_new_bins": arms["static_hybrid"]["new_open_toggle_bins"] - arms["adaptive"]["new_open_toggle_bins"],
        "compute_hour_difference_adaptive_minus_static": adapt["total_compute_hours"] - stat["total_compute_hours"],
        "interpretation": "single local matched pair with a fixed finite catalog; if either arm misses 95%, Compute95 is undefined. Final union should be equal when both exhaust the same deterministic inputs; runtime and order are descriptive only, not proof of policy superiority.",
    }
    if not comparison["paired_catalog_fingerprint_matches"]:
        raise SystemExit("paired arm campaign fingerprints differ")
    result = {
        "study": "fresh-input, single-pair local open-toggle pilot; exploratory, not confirmatory",
        "endpoint": "new-to-study Verilator signal-toggle bins from the 15 BOOM generated modules with named v_user monitors",
        "universe": {
            "all_monitor_toggle_bins": frozen["point_universe_before_exclusion"],
            "previously_hit_excluded": frozen["previously_hit_points_excluded"],
            "open_target_bins": open_count,
            "prior_valid_databases_scanned": frozen["previous_run_database_count"],
        },
        "arms": arms,
        "comparison": comparison,
        "limitations": [
            "Only one pair was run; no seed-level inferential statistics or generalization claim.",
            "Five fresh programs from a frozen UCB riscv-torture generator/config were shared by both arms; this is a finite exploratory catalog.",
            "Local asynchronous execution was used because CHIA/Ray is not installed in this checkout.",
            "Native toggle activity is structural signal activity, not source-line coverage or functional/property coverage.",
            "All monitor-toggle points hit in 33 prior valid coverage databases were excluded before this campaign; no legacy 51-point or 66-family proxy contributes to the primary endpoint.",
        ],
    }
    (STUDY / "results.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    rows = []
    for arm, label in (("static_hybrid", "Static-Hybrid"), ("adaptive", "Adaptive")):
        a = arms[arm]
        s = a["summary"]
        rows.append(f"| {label} | {a['successful_jobs']}/5 | {a['new_open_toggle_bins']:,}/{open_count:,} ({100*a['open_set_closure']:.2f}%) | {s['total_compute_hours']:.4f} | {s['wall_time_hours']*60:.2f} | {'yes' if s['reached_target'] else 'no'} | {s['stop_reason']} |")
    order_lines = []
    for arm, label in (("static_hybrid", "Static-Hybrid"), ("adaptive", "Adaptive")):
        a = arms[arm]
        seq = " -> ".join(a["dispatch_order"])
        order_lines.append(f"- {label}: `{seq}`")
        for job in a["jobs"]:
            order_lines.append(f"  - {job['job_id']}: {job.get('new_open_toggle_bins', 0)} newly closed open bins; {job.get('dispatch_reason', 'not dispatched')}")
    readme = f"""# Fresh MediumBOOM open-toggle paired pilot (2026-09-24)

## Scope and exclusions

The primary denominator is **{frozen['open_target_points']:,} previously-unhit signal-toggle bins** in the 15 generated BOOM modules containing named `v_user` monitors. The complete subset contains {frozen['point_universe_before_exclusion']:,} distinct signal-bit bins. Before freezing this campaign, {frozen['previously_hit_points_excluded']:,} bins hit in **{frozen['previous_run_database_count']} prior valid coverage databases** were removed from the target registry. Only a positive native Verilator toggle count on one of the remaining registered point IDs earns new closure. Source-line coverage was not collected. Neither the old 51-point proxy nor Kapil's 66 semantic-family registry contributes to this endpoint.

Fresh UCB `riscv-torture` inputs were generated at pinned generator commit `{frozen['riscv_torture_commit']}` and shared unchanged by both policies: three balanced CRV workloads and two directed-mix workloads. Their assembly, ELF, and generator stats are included under `inputs/`; input and tool hashes are in `preregistration.json` and `results.json`. The native toggle point IDs, five planning partitions, 0.5 compute-hour cap, paired policies, and static-first run order were frozen before either arm's new coverage results were collected.

## Measured outcomes

| Arm | Successful jobs | New open-toggle bins | Compute-slot hours | Wall minutes | Reached 95% of open set? | Stop reason |
|---|---:|---:|---:|---:|---|---|
{chr(10).join(rows)}

## Observed order and per-job incremental closure

{chr(10).join(order_lines)}

## Interpretation and limits

This is one exploratory matched pair on one local host, not the registered multi-seed confirmatory study. Compute-to-95 is undefined unless the target is reached; no target reduction is imputed. If both arms exhaust the same deterministic five-job roster, they have the same maximum reachable union by construction, so differences in dispatch order and runtime are descriptive—not evidence of adaptive-policy superiority. Adaptive's decision trace uses only measured online yield; it is not the earlier full-information hindsight replay.

The simulator was run locally through the shared ChiaLoop asynchronous controller because CHIA/Ray is not installed in this checkout. Each job exited successfully, emitted `$finish`, and produced a native Verilator coverage database. The raw `.dat` and simulator logs remain outside Git at `{RAW}/<arm>/<job>/`; per-job coverage database and evidence hashes are recorded in `results.json`.
"""
    (STUDY / "README.md").write_text(readme)
    print(json.dumps({"static": {"new_bins": arms["static_hybrid"]["new_open_toggle_bins"], "compute_hours": stat["total_compute_hours"], "reached_target": stat["reached_target"]}, "adaptive": {"new_bins": arms["adaptive"]["new_open_toggle_bins"], "compute_hours": adapt["total_compute_hours"], "reached_target": adapt["reached_target"]}, "comparison": comparison}, indent=2))


if __name__ == "__main__":
    main()
