"""Run all frozen paired toggle-study batches, compressing verified databases per batch."""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STUDY = ROOT / "artifacts_mediumboom/kapil_qualification/open_toggle_multi_seed_study"
DEFAULT_RAW = Path("/private/tmp/mediumboom-open-toggle-multiseed-20260924")
BATCH_RUNNER = ROOT / "experiments/coverage_growth/run_mediumboom_open_toggle_batch.py"
PAIRS_PER_BATCH = 5


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", type=Path, default=DEFAULT_STUDY)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW)
    parser.add_argument("--start-batch", type=int, default=0)
    args = parser.parse_args()
    prereg = json.loads((args.study / "preregistration.json").read_text())
    trials = prereg["trials"]
    batches = math.ceil(len(trials) / PAIRS_PER_BATCH)
    env = os.environ.copy()
    source_path = str(ROOT / "src")
    env["PYTHONPATH"] = source_path + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    for batch in range(args.start_batch, batches):
        cmd = [sys.executable, str(BATCH_RUNNER), "--batch", str(batch),
               "--study", str(args.study.resolve()), "--raw", str(args.raw.resolve()),
               "--compress-databases"]
        print(json.dumps({"starting_batch": batch, "of": batches, "command": cmd}), flush=True)
        result = subprocess.run(cmd, cwd=ROOT, env=env, check=False)
        if result.returncode:
            raise SystemExit(f"batch {batch} failed with exit code {result.returncode}; no automatic rerun")


if __name__ == "__main__":
    main()
