"""Run up to five paired trials with bounded host concurrency and safe recovery."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STUDY = ROOT / "artifacts_mediumboom/kapil_qualification/open_toggle_multi_seed_study"
RUNNER = ROOT / "experiments/coverage_growth/run_mediumboom_open_toggle_arm.py"
COMPRESSOR = ROOT / "experiments/coverage_growth/compress_toggle_campaign_artifacts.py"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=int, required=True, help="zero-based batch of five preregistered trials")
    parser.add_argument("--study", type=Path, default=STUDY)
    parser.add_argument("--raw", type=Path, default=Path("/private/tmp/mediumboom-open-toggle-multiseed-20260924"))
    parser.add_argument("--compress-databases", action="store_true",
                        help="losslessly gzip parsed coverage databases after a successful batch")
    parser.add_argument("--max-active-pairs", type=int, default=2,
                        help="paired trials to execute concurrently (default: 2)")
    args = parser.parse_args()
    if args.max_active_pairs < 1:
        raise SystemExit("--max-active-pairs must be positive")
    prereg = json.loads((args.study / "preregistration.json").read_text())
    trials = prereg["trials"][args.batch * 5:(args.batch + 1) * 5]
    if not trials:
        raise SystemExit(f"no trials in batch {args.batch}")
    resume_outputs: dict[tuple[str, str], bool] = {}
    completed_outputs: set[tuple[str, str]] = set()
    for row in trials:
        for arm in ("static_hybrid", "adaptive"):
            output = args.study / "inputs" / row["trial_id"] / arm
            if output.exists():
                if (output / "summary.json").is_file():
                    summary = json.loads((output / "summary.json").read_text())
                    if summary.get("trial_id") != row["trial_id"] or summary.get("arm") != arm:
                        raise SystemExit(f"completed arm identity mismatch: {output}")
                    completed_outputs.add((row["trial_id"], arm))
                    continue
                if not (output / "events.jsonl").is_file():
                    raise SystemExit(f"cannot resume arm without its event journal: {output}")
                resume_outputs[(row["trial_id"], arm)] = True
            else:
                resume_outputs[(row["trial_id"], arm)] = False
    logs = args.raw / "launcher_logs"
    logs.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    procs = []
    handles = []
    statuses = []
    for offset in range(0, len(trials), args.max_active_pairs):
        wave = []
        for row in trials[offset:offset + args.max_active_pairs]:
            for arm in ("static_hybrid", "adaptive"):
                if (row["trial_id"], arm) in completed_outputs:
                    statuses.append({"trial_id": row["trial_id"], "arm": arm,
                                     "exit_code": 0, "reused_completed_output": True, "log": None})
                    continue
                log = logs / f"{row['trial_id']}_{arm}.log"
                handle = log.open("w")
                handles.append(handle)
                cmd = [sys.executable, str(RUNNER), "--trial", row["trial_id"], "--arm", arm,
                       "--study", str(args.study.resolve()), "--raw", str(args.raw.resolve())]
                if resume_outputs[(row["trial_id"], arm)]:
                    cmd.extend(["--resume-from", str((args.study / "inputs" / row["trial_id"] / arm).resolve())])
                wave.append({"trial_id": row["trial_id"], "arm": arm, "command": cmd,
                             "log": str(log), "process": subprocess.Popen(
                                 cmd, cwd=ROOT, env=os.environ.copy(), stdout=handle,
                                 stderr=subprocess.STDOUT)})
        for entry in wave:
            code = entry["process"].wait()
            statuses.append({"trial_id": entry["trial_id"], "arm": entry["arm"], "exit_code": code,
                             "log": entry["log"]})
        procs.extend(wave)
    for handle in handles:
        handle.close()
    compression = None
    if all(item["exit_code"] == 0 for item in statuses) and args.compress_databases:
        compressed = subprocess.run([sys.executable, str(COMPRESSOR),
                                     "--raw", str(args.raw.resolve()),
                                     "--study", str(args.study.resolve())],
                                    cwd=ROOT, capture_output=True, text=True, check=False)
        compression = {"exit_code": compressed.returncode, "stdout": compressed.stdout,
                       "stderr": compressed.stderr}
        if compressed.returncode:
            raise SystemExit(f"lossless artifact compression failed: {compressed.stderr}")
    record = {"batch": args.batch, "started_utc": started,
              "finished_utc": datetime.now(timezone.utc).isoformat(),
              "statuses": statuses, "compression": compression,
              "success": all(item["exit_code"] == 0 for item in statuses)}
    record_path = args.raw / f"batch_{args.batch:02d}_launcher.json"
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps(record, indent=2))
    if not record["success"]:
        raise SystemExit("one or more arms failed; no automatic rerun will be attempted")


if __name__ == "__main__":
    main()
