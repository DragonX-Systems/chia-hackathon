"""Run actual decoder reachability/proof jobs; no full-core pruning is authorized."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(path)


def classify(mode: str, native: str, returncode: int) -> str:
    native = native.split()[0] if native.split() else "ERROR"
    if native == "TIMEOUT":
        return "TIMEOUT"
    if native == "PASS" and returncode == 0:
        return "PROVEN_UNREACHABLE" if mode == "prove" else "COVER_REACHED"
    if native == "FAIL" and returncode == 2:
        return "COUNTEREXAMPLE" if mode == "prove" else "BOUNDED_UNREACHED"
    if native == "UNKNOWN":
        return "UNKNOWN"
    return "ERROR"


def run_simulation(out: Path, timeout: int) -> dict:
    """Check shared predicates on real decoder vectors, outside space-containing paths."""
    commands = []
    start = time.monotonic()
    status = "PASS"
    with tempfile.TemporaryDirectory(prefix="ibex-decoder-", dir="/tmp") as staging:
        stage = Path(staging)
        for source in (out / "inputs").iterdir():
            shutil.copy2(source, stage / source.name)
        fixture = HERE / "tests/decoder_smoke.sv"
        shutil.copy2(fixture, stage / fixture.name)
        build = ["verilator", "--binary", "--timing", "--assert", "-Wno-fatal",
                 "-Wno-PINMISSING", "-DSYNTHESIS", "-DCHECK_COVER", "-DBIN_INDEX=0",
                 "-I.", "--top-module", "decoder_smoke", "--Mdir", "obj_dir",
                 "ibex_pkg.sv", "ibex_cheriot_pkg.sv", "ibex_decoder.sv",
                 "decoder_events.sv", "decoder_formal.sv", "decoder_smoke.sv"]
        for label, command in (("build", build), ("run", ["./obj_dir/Vdecoder_smoke"])):
            commands.append(command)
            with (out / f"simulation-{label}.log").open("w") as log:
                process = subprocess.Popen(command, cwd=stage, stdout=log,
                                           stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    if process.wait(timeout=timeout) != 0:
                        status = "ERROR"
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    status = "TIMEOUT"
            if status != "PASS":
                break
    if status == "PASS" and "PASS: five actual decoder events and illegal gating" not in (
        out / "simulation-run.log"
    ).read_text():
        status = "ERROR"
    result = {"status": status, "scope": "local_decoder_only", "commands": commands,
              "fixture_sha256": digest(fixture), "elapsed_seconds": time.monotonic() - start}
    write_json(out / "simulation.json", result)
    return result


def run_job(directory: Path, index: int, mode: str, timeout: int) -> dict:
    job = directory / mode
    job.mkdir()
    define = "-D CHECK_COVER" if mode == "cover" else ""
    # Source snapshots and relative names avoid shell quoting and path-space issues.
    recipe = f"""[options]
mode {mode}
depth 2
timeout {timeout}

[engines]
smtbmc z3

[script]
read_slang -I. -D SYNTHESIS -D BIN_INDEX={index} {define} ibex_pkg.sv ibex_cheriot_pkg.sv ibex_decoder.sv decoder_events.sv decoder_formal.sv --top decoder_formal
prep -top decoder_formal

[files]
../../inputs/ibex_pkg.sv
../../inputs/ibex_cheriot_pkg.sv
../../inputs/ibex_decoder.sv
../../inputs/prim_assert.sv
../../inputs/prim_assert_dummy_macros.svh
../../inputs/prim_assert_sec_cm.svh
../../inputs/prim_flop_macros.sv
../../inputs/decoder_events.sv
../../inputs/decoder_formal.sv
"""
    (job / "job.sby").write_text(recipe)
    command = ["sby", "-d", "work", "job.sby"]
    start = time.monotonic()
    with (job / "runner.log").open("w") as log:
        process = subprocess.Popen(command, cwd=job, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            returncode = process.wait(timeout=timeout + 30)
            status_path = job / "work" / "status"
            native = status_path.read_text().strip() if status_path.exists() else "ERROR"
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            returncode, native = -1, "TIMEOUT"
    payload = {"mode": mode, "status": classify(mode, native, returncode),
               "native_status": native, "returncode": returncode,
               "elapsed_seconds": time.monotonic() - start, "command": command,
               "recipe_sha256": digest(job / "job.sby"),
               "work_directory": str(job / "work")}
    write_json(job / "result.json", payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ibex-root", type=Path, required=True)
    parser.add_argument("--bin", default="all", help="Exact decoder bin ID, or all")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--simulate", action="store_true",
                        help="Also check singleton vectors using the actual decoder in Verilator")
    args = parser.parse_args()
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    catalog_path = HERE / "formal/decoder_bins.json"
    catalog = json.loads(catalog_path.read_text())
    selected = [b for b in catalog["bins"] if args.bin in ("all", b["id"])]
    if not selected:
        parser.error("Unknown decoder bin ID")
    required = ["sby", "yosys", "yosys-smtbmc", "z3"]
    if args.simulate:
        required.append("verilator")
    for tool in required:
        if not shutil.which(tool):
            parser.error(f"Required tool missing: {tool}")
    root = args.ibex_root.resolve()
    commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                                     text=True).strip()
    if commit != catalog["ibex_commit"]:
        parser.error(f"Expected Ibex revision {catalog['ibex_commit']}; found {commit}")
    dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain",
                                     "--untracked-files=no"], text=True)
    if dirty:
        parser.error("Ibex tracked sources must be unchanged for this qualification")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    out = (args.out or REPO / "artifacts/ibex" / f"formal-{stamp}").resolve()
    out.mkdir(parents=True, exist_ok=False)
    inputs = out / "inputs"
    inputs.mkdir()
    sources = [root / "rtl" / name for name in
               ("ibex_pkg.sv", "ibex_cheriot_pkg.sv", "ibex_decoder.sv")]
    sources += [root / "vendor/lowrisc_ip/ip/prim/rtl" / name for name in
                ("prim_assert.sv", "prim_assert_dummy_macros.svh", "prim_assert_sec_cm.svh",
                 "prim_flop_macros.sv")]
    sources += [HERE / "coverage/decoder_events.sv", HERE / "formal/decoder_formal.sv"]
    for source in sources:
        shutil.copy2(source, inputs / source.name)
    identity = {"ibex_commit": commit, "scope": catalog["scope"],
                "catalog_sha256": digest(catalog_path),
                "input_sha256": {p.name: digest(p) for p in inputs.iterdir()},
                "core_pruning_authorized": False,
                "assumptions": "No assume statements. Duplicated instruction inputs tied; "
                "RV32I/MFast, B disabled, RV32E=0, BranchTargetALU=0, CHERIoT off; "
                "valid=1. Other decoder data inputs unrestricted. Local combinational scope.",
                "upstream_assertions": "SYNTHESIS disables upstream assertion macros; "
                "the explicit project assertion/cover in decoder_formal.sv remains active."}
    for tool, options in (("sby", ["--version"]), ("yosys", ["-V"]), ("z3", ["--version"])):
        identity[tool] = subprocess.check_output([tool, *options], text=True).strip()
    write_json(out / "identity.json", identity)
    shutil.copy2(catalog_path, out / "catalog.json")
    rows = []
    for item in selected:
        directory = out / item["id"]
        directory.mkdir()
        row = {"id": item["id"], "bit": item["bit"]}
        for mode in ("cover", "prove"):
            row[mode] = run_job(directory, item["bit"], mode, args.timeout)
            print(f"{item['id']} {mode}: {row[mode]['status']}", flush=True)
        if row["cover"]["status"] == "COVER_REACHED" and row["prove"]["status"] == "PROVEN_UNREACHABLE":
            row["conflict"] = True
        rows.append(row)
    failed = any(r.get("conflict") or any(r[m]["status"] in ("ERROR", "TIMEOUT", "UNKNOWN")
                                         for m in ("cover", "prove")) for r in rows)
    summary = {"scope": "local_decoder_only", "core_pruning_authorized": False,
               "status": "INCOMPLETE" if failed else "COMPLETED", "results": rows}
    if args.simulate:
        summary["simulation"] = run_simulation(out, max(120, args.timeout))
        failed = failed or summary["simulation"]["status"] != "PASS"
        summary["status"] = "INCOMPLETE" if failed else "COMPLETED"
    write_json(out / "summary.json", summary)
    print(out)
    return int(failed)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Qualification error: {exc}", file=sys.stderr)
        sys.exit(1)
