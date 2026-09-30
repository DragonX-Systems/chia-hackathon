"""Run one frozen arm of one fresh-input open-toggle replicate."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from chialoop.core.catalog import JobCatalog
from chialoop.core.config import CampaignSpec
from chialoop.core.types import CoveragePoint
from chialoop.orchestration.driver import CampaignRunner
from chialoop.orchestration.policies import AdaptivePolicy, StaticHybridPolicy
from chialoop.orchestration.runtime import LocalAsyncRuntime

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STUDY = ROOT / "artifacts_mediumboom/kapil_qualification/open_toggle_multi_seed_study"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trial", required=True)
    parser.add_argument("--arm", choices=("static_hybrid", "adaptive"), required=True)
    parser.add_argument("--study", type=Path, default=DEFAULT_STUDY)
    parser.add_argument("--raw", type=Path, default=Path("/private/tmp/mediumboom-open-toggle-multiseed-20260924"))
    parser.add_argument("--resume-from", type=Path,
                        help="resume an interrupted arm from its validated event journal")
    args = parser.parse_args()
    os.environ["CHIALOOP_OPEN_TOGGLE_STUDY"] = str(args.study.resolve())
    os.environ["CHIALOOP_TRIAL"] = args.trial
    os.environ["CHIALOOP_ARM"] = args.arm
    os.environ["MEDIUMBOOM_OPEN_TOGGLE_RAW"] = str(args.raw.resolve())

    trial = args.study / "inputs" / args.trial
    out = trial / args.arm
    resume_events = []
    output_dir = out
    if args.resume_from:
        resume_root = args.resume_from.resolve()
        if resume_root != out.resolve() or not (resume_root / "events.jsonl").is_file():
            raise SystemExit("--resume-from must identify this arm's existing event journal")
        if (resume_root / "summary.json").exists():
            raise SystemExit(f"refusing to resume a completed arm: {resume_root}")
        event_files = [resume_root / "events.jsonl"] + sorted(
            (resume_root / "segments").glob("resume_*/events.jsonl")
        )
        resume_events = [
            json.loads(line) for path in event_files for line in path.read_text().splitlines()
        ]
        completed = {e["job_id"] for e in resume_events if e.get("event") == "job_completed"}
        pending = {e["job_id"] for e in resume_events if e.get("event") == "job_dispatched"} - completed
        for job_id in pending:
            work = args.raw.resolve() / args.trial / args.arm / job_id
            if work.exists() and any(work.iterdir()):
                raise SystemExit(f"cannot safely resume partially materialized job {job_id}: {work}")
        segment_root = resume_root / "segments"
        segment_root.mkdir(parents=True, exist_ok=True)
        segment_id = 1
        while (segment_root / f"resume_{segment_id:02d}").exists():
            segment_id += 1
        output_dir = segment_root / f"resume_{segment_id:02d}"
    elif out.exists():
        raise SystemExit(f"refusing to rerun/overwrite measured arm output: {out}")
    spec = CampaignSpec.read_json(trial / "campaign.json")
    catalog = JobCatalog.read_jsonl(trial / "job_catalog.jsonl")
    points = [CoveragePoint(**row) for row in json.loads((args.study / "coverage_points.json").read_text())["points"]]
    policy = StaticHybridPolicy(catalog) if args.arm == "static_hybrid" else AdaptivePolicy()
    from mediumboom_open_toggle_executor import execute

    runtime = LocalAsyncRuntime(max_workers=spec.resources.compute)
    try:
        summary = CampaignRunner(
            spec=spec, catalog=catalog, coverage_points=points, policy=policy,
            runtime=runtime, executor=execute, trial_id=args.trial,
            output_dir=output_dir, resume_events=resume_events,
        ).run()
    finally:
        runtime.close()
    payload = summary.to_dict()
    if args.resume_from:
        payload["resumed_from"] = str(args.resume_from.resolve())
        (out / "summary.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
