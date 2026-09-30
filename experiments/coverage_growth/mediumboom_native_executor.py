"""Evidence-validating executor for the bounded MediumBOOM ChiaLoop pilot."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

from chialoop.core.types import Engine, EngineEvidence, JobStatus, ProofStatus

ROOT = Path(__file__).resolve().parents[2]
CHIPYARD = Path(os.environ.get("CHIPYARD_ROOT", "/private/tmp/chipyard-kapil-e602"))
SIM = Path(os.environ.get("MEDIUMBOOM_SIM", CHIPYARD / "sims/verilator/simulator-chipyard.harness-MediumBoomV3Config"))
ARTIFACT_ROOT = Path(os.environ.get("MEDIUMBOOM_PILOT_RAW", "/private/tmp/mediumboom-paired-pilot-20260924"))
CATALOG = ROOT / "verification/boom/catalog.json"
FORMAL_DIR = ROOT / "artifacts_mediumboom/kapil_qualification/formal_dtlb"
FORMAL_GOALS = {
    "cover__boom_tlb_full_fence_late_refill": "tlb.full_fence_late_refill",
    "cover__boom_tlb_sfence_ptw_accept": "tlb.sfence_ptw_accept",
    "cover__boom_tlb_sfence_refill_collision": "tlb.sfence_refill_collision",
    "cover__boom_tlb_invalidated_walk_returns": "tlb.invalidated_walk_returns",
    "cover__boom_tlb_superpage_refill": "tlb.superpage_refill",
}


def execute(job):
    """Run the selected real engine job; only validated counter/cover IDs count."""
    payload = dict(job.payload)
    arm = os.environ.get("CHIALOOP_ARM", "unknown")
    trial = os.environ.get("CHIALOOP_TRIAL", "pilot")
    work = ARTIFACT_ROOT / trial / arm / job.job_id
    work.mkdir(parents=True, exist_ok=True)
    if job.engine is Engine.FORMAL:
        return _formal(job, work)
    elf = Path(payload["input_elf"]).resolve()
    if not elf.is_file():
        return EngineEvidence(status=JobStatus.ERROR, error_summary=f"missing ELF: {elf}")
    actual_hash = _sha256(elf)
    if actual_hash != payload["sha256"]:
        return EngineEvidence(status=JobStatus.ERROR, error_summary=f"ELF SHA-256 changed: {elf}")
    cov = work / "coverage.dat"
    dram_ini = CHIPYARD / "generators/testchipip/src/main/resources/dramsim2_ini"
    cmd = [str(SIM), "+permissive", "+dramsim", f"+dramsim_ini_dir={dram_ini}",
           f"+loadmem={elf}", "+max-cycles=10000000", "+verbose", "+permissive-off", str(elf)]
    env = os.environ.copy()
    env["CHIPYARD_ROOT"] = str(CHIPYARD)
    env["VM_COVERAGE"] = "1"
    env["COVERAGE_FILE"] = str(cov)
    env["RISCV"] = env.get("RISCV", "/tmp/cy-riscv")
    dram = str(CHIPYARD / "tools/DRAMSim2")
    env["DYLD_LIBRARY_PATH"] = f"{env['RISCV']}/lib:/opt/homebrew/lib:{dram}:" + env.get("DYLD_LIBRARY_PATH", "")
    env["LIBRARY_PATH"] = f"{env['RISCV']}/lib:/opt/homebrew/lib:" + env.get("LIBRARY_PATH", "")
    start = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True,
                              timeout=job.timeout_seconds, check=False)
    except subprocess.TimeoutExpired as exc:
        log = (exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        (work / "sim.log").write_text(log)
        return EngineEvidence(status=JobStatus.TIMEOUT, artifact_paths=(str(work / "sim.log"),),
                              notes=f"timed out after {job.timeout_seconds}s; input sha256={actual_hash}")
    elapsed = time.monotonic() - start
    log = (proc.stdout or "") + (proc.stderr or "")
    log_path = work / "sim.log"
    log_path.write_text(log)
    passed = proc.returncode == 0 and "Verilog $finish" in log and not any(x in log for x in ("*** FAILED ***", "Assertion failed", "%Error:"))
    if not passed or not cov.is_file():
        return EngineEvidence(status=JobStatus.ERROR, artifact_paths=(str(log_path),),
                              error_summary=f"simulator rc={proc.returncode}, coverage={cov.exists()}, finish={'Verilog $finish' in log}")
    summary_path = work / "coverage_summary.json"
    summarizer = ROOT / "verification/boom/summarize_verilator_coverage.py"
    parsed = subprocess.run(["python3", str(summarizer), str(cov), "--catalog", str(CATALOG)],
                            capture_output=True, text=True, check=True)
    report = json.loads(parsed.stdout)
    hits = tuple(report["families"]["scenario"]["hit_family_ids"])
    allowed = set(job.target_point_ids)
    if not set(hits) <= allowed:
        return EngineEvidence(status=JobStatus.ERROR, artifact_paths=(str(log_path), str(cov)),
                              error_summary=f"counter parser emitted out-of-catalog evidence: {sorted(set(hits)-allowed)}")
    report.update({"job_id": job.job_id, "input_elf": str(elf), "input_sha256": actual_hash,
                   "argv": cmd, "elapsed_seconds": elapsed, "returncode": proc.returncode,
                   "arm": arm, "trial_id": trial})
    summary_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return EngineEvidence(status=JobStatus.SUCCESS, hit_point_ids=hits,
                          artifact_version=job.artifact_version,
                          artifact_paths=(str(log_path), str(cov), str(summary_path)),
                          tool_versions={"verilator": "5.036 (prebuilt MediumBoomV3Config; coverage-toggle,user,assert)",
                                         "simulator_sha256": _sha256(SIM)},
                          notes=f"native Verilator scenario counters; input_sha256={actual_hash}; elapsed={elapsed:.3f}s")


def _formal_spec(job):
    """The executable query, shared by admission fingerprinting and execution."""
    payload = dict(job.payload)
    allowed = {"sby_file", "sby_task", "sby_sha256", "required_goal"}
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"unsupported formal options: {sorted(unknown)}")
    if not payload.get("sby_file"):
        raise ValueError("formal job requires sby_file")
    path = Path(payload["sby_file"])
    path = (FORMAL_DIR / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_file():
        raise ValueError(f"missing formal query: {path}")
    digest = _sha256(path)
    if payload.get("sby_sha256") is not None and payload["sby_sha256"] != digest:
        raise ValueError(f"formal query SHA-256 changed: {path}")
    task = payload.get("sby_task")
    if task is not None and (not isinstance(task, str) or not task or task.startswith("-")):
        raise ValueError("sby_task must be a nonempty task name, not an option")
    goal = payload.get("required_goal")
    if goal is not None and goal not in FORMAL_GOALS:
        raise ValueError(f"unknown required formal goal: {goal}")
    return path, digest, task, goal


def execution_fingerprint(job):
    """Identify actual work, excluding job/query labels and output filters."""
    if job.engine is Engine.FORMAL:
        path, digest, task, _goal = _formal_spec(job)
        inputs = {"sby_sha256": digest, "source_directory": str(path.parent),
                  "task": task, "artifact_version": job.artifact_version,
                  "assumption_version": job.assumption_version}
        return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    path = Path(job.payload["input_elf"]).resolve()
    digest = _sha256(path)
    if digest != job.payload["sha256"]:
        raise ValueError(f"ELF SHA-256 changed: {path}")
    return digest


def _formal(job, work):
    path, _digest, task, required_goal = _formal_spec(job)
    out = (work / "sby").resolve()
    cmd = ["sby", "-f", "-d", str(out), str(path)]
    if task is not None:
        cmd.append(task)
    start = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=path.parent, capture_output=True, text=True,
                              timeout=job.timeout_seconds, check=False)
    except subprocess.TimeoutExpired:
        return EngineEvidence(status=JobStatus.TIMEOUT, proof_status=ProofStatus.TIMEOUT,
                              error_summary=f"formal query exceeded {job.timeout_seconds}s")
    elapsed = time.monotonic() - start
    (work / "sby.log").write_text((proc.stdout or "") + (proc.stderr or ""))
    status_file = out / "status"
    status = status_file.read_text(errors="replace").strip() if status_file.exists() else "UNKNOWN"
    log = out / "logfile.txt"
    txt = log.read_text(errors="replace") if log.exists() else ""
    reached = tuple(point for goal, point in FORMAL_GOALS.items() if goal in txt)
    if (proc.returncode != 0 or not status.startswith("PASS")
            or not set(job.target_point_ids) <= set(reached)
            or (required_goal is not None and FORMAL_GOALS[required_goal] not in reached)):
        return EngineEvidence(status=JobStatus.ERROR, proof_status=ProofStatus.ERROR,
                              artifact_paths=(str(work / "sby.log"), str(status_file)),
                              error_summary=f"SBY status={status}; reached mapped goals={reached}; required={job.target_point_ids}")
    return EngineEvidence(status=JobStatus.SUCCESS,
                          hit_point_ids=tuple(point for point in reached if point in job.target_point_ids),
                          proof_status=ProofStatus.REACHED, artifact_version=job.artifact_version,
                          assumption_version=job.assumption_version,
                          artifact_paths=(str(work / "sby.log"), str(status_file), str(log)),
                          tool_versions={"sby_query_sha256": _digest},
                          notes=f"bounded cover reached, not an assertion proof; elapsed={elapsed:.3f}s")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# Campaign admission uses the same executable-input interpretation as execute().
execute.execution_fingerprint = execution_fingerprint
