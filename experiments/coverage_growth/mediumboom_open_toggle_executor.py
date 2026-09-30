"""Run fresh CRV/directed MediumBOOM ELF jobs and score open monitor toggles."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import tempfile
from pathlib import Path

from adapters.mediumboom.crv_profiles import signature_sha256

from chialoop.core.types import EngineEvidence, JobStatus

ROOT = Path(__file__).resolve().parents[2]
STUDY = Path(os.environ.get(
    "CHIALOOP_OPEN_TOGGLE_STUDY",
    ROOT / "artifacts_mediumboom/kapil_qualification/open_toggle_study",
))
CHIPYARD = Path(os.environ.get("CHIPYARD_ROOT", "/private/tmp/chipyard-kapil-e602"))
SIM = Path(os.environ.get("MEDIUMBOOM_SIM", CHIPYARD / "sims/verilator/simulator-chipyard.harness-MediumBoomV3Config"))
RAW = Path(os.environ.get("MEDIUMBOOM_OPEN_TOGGLE_RAW", "/private/tmp/mediumboom-open-toggle-20260924"))



def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fields(line: str) -> dict[str, str]:
    metadata = line.split("' ", 1)[0].split("'", 1)[1]
    return {key: value for field in metadata.split("\x01") if "\x02" in field
            for key, value in (field.split("\x02", 1),)}


def _point_id(record: dict[str, str]) -> str:
    module = record["page"].removeprefix("v_toggle/")
    identity = "\x1f".join((module, record.get("h", ""), record["o"]))
    return "toggle:" + hashlib.sha256(identity.encode()).hexdigest()


def execute(job):
    payload = dict(job.payload)
    arm = os.environ.get("CHIALOOP_ARM", "unknown")
    trial = os.environ.get("CHIALOOP_TRIAL", "toggle-pilot")
    root = Path(payload.get("execution_root", RAW / trial / arm)).resolve()
    root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="attempt-", dir=root))
    simulator = Path(payload.get("simulator", SIM)).resolve()
    chipyard = Path(payload.get("chipyard_root", CHIPYARD)).resolve()
    monitor = Path(payload.get("monitor_modules", STUDY / "monitor_modules.json"))
    if not simulator.is_file():
        return EngineEvidence(status=JobStatus.ERROR, error_summary=f"missing simulator: {simulator}")
    simulator_hash = _sha256(simulator)
    if payload.get("simulator_sha256", simulator_hash) != simulator_hash:
        return EngineEvidence(status=JobStatus.ERROR, error_summary="simulator hash differs from catalog")
    if not monitor.is_file() or payload.get("monitor_modules_sha256", _sha256(monitor)) != _sha256(monitor):
        return EngineEvidence(status=JobStatus.ERROR, error_summary="monitor registry missing or hash-changed")
    monitor_modules = set(json.loads(monitor.read_text()))
    expected_signature = payload.get("expected_signature_sha256")
    if payload.get("crv_profile") and not expected_signature:
        return EngineEvidence(status=JobStatus.ERROR, error_summary="profiled CRV job lacks Spike signature")
    elf = Path(payload["input_elf"]).resolve()
    if not elf.is_file() or _sha256(elf) != payload["sha256"]:
        return EngineEvidence(status=JobStatus.ERROR,
                              error_summary=f"missing or hash-changed ELF: {elf}")
    coverage = work / "coverage.dat"
    dram_ini = chipyard / "generators/testchipip/src/main/resources/dramsim2_ini"
    cmd = [str(simulator), "+permissive", f"+signature={work / 'actual.signature'}",
           "+dramsim", f"+dramsim_ini_dir={dram_ini}",
           f"+loadmem={elf}", "+max-cycles=10000000", "+verbose", "+permissive-off", str(elf)]
    env = os.environ.copy()
    env.update({"CHIPYARD_ROOT": str(chipyard), "VM_COVERAGE": "1",
                "COVERAGE_FILE": str(coverage), "RISCV": env.get("RISCV", "/tmp/cy-riscv")})
    dram = str(chipyard / "tools/DRAMSim2")
    env["DYLD_LIBRARY_PATH"] = f"{env['RISCV']}/lib:/opt/homebrew/lib:{dram}:" + env.get("DYLD_LIBRARY_PATH", "")
    env["LIBRARY_PATH"] = f"{env['RISCV']}/lib:/opt/homebrew/lib:" + env.get("LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = f"{env['RISCV']}/lib:{dram}:" + env.get("LD_LIBRARY_PATH", "")
    started = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True,
                              timeout=job.timeout_seconds, check=False)
    except subprocess.TimeoutExpired as exc:
        def decoded(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")
        (work / "sim.log").write_text(decoded(exc.stdout) + decoded(exc.stderr))
        return EngineEvidence(status=JobStatus.TIMEOUT,
                              artifact_paths=(str(work / "sim.log"),),
                              notes=f"timed out after {job.timeout_seconds}s")
    except OSError as exc:
        return EngineEvidence(status=JobStatus.ERROR, error_summary=f"simulator launch failed: {exc}")
    elapsed = time.monotonic() - started
    log = (proc.stdout or "") + (proc.stderr or "")
    log_path = work / "sim.log"
    log_path.write_text(log)
    passed = (proc.returncode == 0 and "Verilog $finish" in log
              and not any(term in log for term in ("*** FAILED ***", "Assertion failed", "%Error:")))
    if not passed or not coverage.is_file() or coverage.stat().st_size == 0:
        return EngineEvidence(status=JobStatus.ERROR, artifact_paths=(str(log_path),),
                              error_summary=f"simulator rc={proc.returncode}, coverage={coverage.exists()}, finish={'Verilog $finish' in log}")

    actual_signature = None
    if expected_signature:
        try:
            actual_signature = signature_sha256(work / "actual.signature")
        except (OSError, ValueError) as exc:
            return EngineEvidence(status=JobStatus.ERROR, artifact_paths=(str(log_path),),
                                  error_summary=f"invalid DUT signature: {exc}")
        if actual_signature != expected_signature:
            return EngineEvidence(status=JobStatus.ERROR, artifact_paths=(str(log_path), str(work / "actual.signature")),
                                  error_summary="DUT signature differs from Spike; coverage not credited")

    target_ids = set(job.target_point_ids)
    hits: set[str] = set()
    for line in coverage.open(errors="replace"):
        if "page\x02v_toggle/" not in line or line.rsplit("' ", 1)[1].strip() == "0":
            continue
        record = _fields(line)
        if record["page"].split("/", 1)[1] not in monitor_modules:
            continue
        point = _point_id(record)
        if point in target_ids:
            hits.add(point)
    evidence_path = work / "evidence.json"
    evidence_path.write_text(json.dumps({
        "job_id": job.job_id, "input_elf_sha256": _sha256(elf),
        "assembly_sha256": payload.get("assembly_sha256"),
        "coverage_dat_sha256": _sha256(coverage), "simulator_sha256": simulator_hash,
        "expected_signature_sha256": expected_signature,
        "actual_signature_sha256": actual_signature,
        "runtime_seconds": elapsed, "target_toggle_bins_hit": len(hits),
        "monitor_toggle_bins_hit_ids": sorted(hits),
        "nonzero_non_target_monitor_bins_not_credited": True,
    }, indent=2, sort_keys=True) + "\n")
    return EngineEvidence(status=JobStatus.SUCCESS, hit_point_ids=tuple(sorted(hits)),
                          artifact_version=job.artifact_version,
                          artifact_paths=(str(log_path), str(coverage), str(evidence_path))
                          + ((str(work / "actual.signature"),) if expected_signature else ()),
                          tool_versions={"simulator_sha256": simulator_hash},
                          notes=f"native BOOM monitor signal-toggle bins only; {len(hits)} new-to-study targets hit; elapsed={elapsed:.3f}s")
