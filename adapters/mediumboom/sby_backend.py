"""MediumBOOM SymbiYosys retirement and hit adapter.

Frozen points are named covers on three local DUTs:

* ``cover_lsu`` — BOOM LSU fragment (names from riscv-boom v3/lsu/lsu.scala)
* ``decoder_cover`` — ultraembedded RV32IM decoder (riscv_soc CPU core)
* ``irq_cover`` — riscv_soc ``irq_ctrl`` from the parent folder

Formal batches run ``sby`` + Z3. Cover FAIL with a named witness → hit.
Prove PASS (k-induction) on the matching assert → retire. Torture / CRV /
directed still use the yield model on this same list; they cannot retire.
"""

from __future__ import annotations

import os
import random
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .coverage_model import CoverPoint, FrozenModel, _hmac
from .engines import Allocation, BatchResult, KnobState, simulate_batch

RTL_DIR = Path(__file__).resolve().parent / "rtl"
PKG_ROOT = Path(__file__).resolve().parents[2]
REACHED = re.compile(r"Reached cover statement.*?:\s*(\w+)")
UNREACHED = re.compile(r"Unreached cover statement.*?:\s*(\w+)")
SUMMARY_REACHED = re.compile(r"reached cover statement \S+\.(\w+)")


@dataclass(frozen=True)
class FormalProp:
    pid: str
    name: str
    kind: str
    unit: str
    bucket: str
    dut: str
    cover: str


# Planner still cannot see ``bucket``. Formal uses SBY status, not this tag.
CATALOG: tuple[FormalProp, ...] = (
    FormalProp("cp_0000", "boom.lsu.load_fire", "sva", "lsu", "easy", "cover_lsu", "load_fire"),
    FormalProp("cp_0001", "boom.lsu.store_fire", "sva", "lsu", "easy", "cover_lsu", "store_fire"),
    FormalProp("cp_0002", "boom.lsu.replay_after_busy", "sva", "lsu", "medium", "cover_lsu", "replay_after_busy"),
    FormalProp("cp_0003", "boom.lsu.stq_forward", "sva", "lsu", "medium", "cover_lsu", "stq_forward_fire"),
    FormalProp("cp_0004", "boom.lsu.amo_without_a", "sva", "lsu", "unreachable", "cover_lsu", "amo_without_a"),
    FormalProp("cp_0005", "boom.lsu.load_and_store", "sva", "lsu", "unreachable", "cover_lsu", "load_and_store"),
    FormalProp("cp_0006", "riscv_soc.dec.addi_exec", "sva", "issue_q", "easy", "decoder_cover", "addi_exec"),
    FormalProp("cp_0007", "riscv_soc.dec.lw_lsu", "sva", "lsu", "easy", "decoder_cover", "lw_lsu"),
    FormalProp("cp_0008", "riscv_soc.dec.beq_branch", "sva", "bpred", "medium", "decoder_cover", "beq_branch"),
    FormalProp("cp_0009", "riscv_soc.dec.mul_when_m", "sva", "issue_q", "hard", "decoder_cover", "mul_when_m"),
    FormalProp("cp_0010", "riscv_soc.dec.fadd_legal", "sva", "issue_q", "unreachable", "decoder_cover", "fadd_legal"),
    FormalProp("cp_0011", "riscv_soc.dec.mul_without_m", "sva", "issue_q", "unreachable", "decoder_cover", "mul_without_m"),
    FormalProp("cp_0012", "riscv_soc.irq.irq0_host", "sva", "issue_q", "medium", "irq_cover", "irq0_host"),
    FormalProp("cp_0013", "riscv_soc.irq.irq1_host", "sva", "issue_q", "medium", "irq_cover", "irq1_host"),
    FormalProp("cp_0014", "riscv_soc.irq.irq_mer_hold", "sva", "issue_q", "hard", "irq_cover", "irq_mer_hold"),
    FormalProp("cp_0015", "riscv_soc.irq.irq_source4", "sva", "issue_q", "unreachable", "irq_cover", "irq_source4"),
    FormalProp("cp_0016", "riscv_soc.irq.irq_bresp_err", "sva", "issue_q", "unreachable", "irq_cover", "irq_bresp_err"),
)


def riscv_core_dir() -> Path:
    env = os.environ.get("ULTRAEMBEDDED_RISCV")
    if env:
        return Path(env)
    return PKG_ROOT / "third_party" / "ultraembedded-riscv" / "core" / "riscv"


def riscv_soc_dir() -> Path:
    env = os.environ.get("RISCV_SOC_ROOT")
    if env:
        return Path(env) / "soc"
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "riscv_soc" / "soc"
        if (cand / "irq_ctrl.v").exists():
            return cand
    return here.parents[3] / "riscv_soc" / "soc"


def freeze_sby_model() -> FrozenModel:
    points = [
        CoverPoint(pid=p.pid, kind=p.kind, unit=p.unit, bucket=p.bucket, name=p.name)  # type: ignore[arg-type]
        for p in CATALOG
    ]
    seed = 20260907
    blob = __import__("json").dumps([asdict(p) for p in points], sort_keys=True)
    return FrozenModel(seed=seed, points=points, hmac=_hmac(blob + str(seed)))


def parse_sby_log(log: str) -> tuple[set[str], set[str]]:
    reached: set[str] = set()
    unreached: set[str] = set()
    for line in log.splitlines():
        m = REACHED.search(line)
        if m:
            reached.add(m.group(1))
            continue
        m = SUMMARY_REACHED.search(line)
        if m:
            reached.add(m.group(1))
            continue
        m = UNREACHED.search(line)
        if m:
            unreached.add(m.group(1))
    return reached, unreached


def _write_sby(dut: str, dest: Path) -> Path:
    core = riscv_core_dir()
    soc = riscv_soc_dir()
    if dut == "cover_lsu":
        script = "read -formal cover_lsu.sv\nprep -top cover_lsu\n"
        files = "cover_lsu.sv\n"
    elif dut == "decoder_cover":
        script = (
            f"read -formal -I {core} decoder_cover.sv\n"
            f"read -formal -I {core} {core / 'riscv_decoder.v'}\n"
            "prep -top decoder_cover\n"
        )
        files = f"decoder_cover.sv\n{core / 'riscv_decoder.v'}\n{core / 'riscv_defs.v'}\n"
    elif dut == "irq_cover":
        script = (
            f"read -formal -I {soc} irq_cover.sv\n"
            f"read -formal -I {soc} {soc / 'irq_ctrl.v'}\n"
            "prep -top irq_cover\n"
        )
        files = f"irq_cover.sv\n{soc / 'irq_ctrl.v'}\n{soc / 'irq_ctrl_defs.v'}\n"
    else:
        raise ValueError(dut)
    dest.write_text(
        "[tasks]\ncover\nprove\n\n"
        "[options]\ncover: mode cover\ncover: depth 16\nprove: mode prove\nprove: depth 16\n\n"
        "[engines]\nsmtbmc z3\n\n"
        f"[script]\n{script}\n"
        f"[files]\n{files}"
    )
    return dest


_SBY_CACHE: dict[tuple[str, str], tuple[str, str, float]] = {}


def run_sby(dut: str, task: str, work: Path, timeout_s: int = 90) -> tuple[str, str, float]:
    cached = _SBY_CACHE.get((dut, task))
    if cached:
        return cached
    sby = shutil.which("sby")
    if not sby:
        return "skipped", "sby not on PATH", 0.0
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    # Keep the recipe next to the Verilog so SBY can copy sources.
    sby_file = _write_sby(dut, RTL_DIR / f".gen_{dut}.sby")
    out = work / f"{dut}_{task}"
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [sby, "-f", "-d", str(out), str(sby_file), task],
            cwd=RTL_DIR,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return "timeout", str(exc), timeout_s
    elapsed = time.perf_counter() - t0
    log = (proc.stdout or "") + (proc.stderr or "")
    log_file = out / "logfile.txt"
    if log_file.exists():
        log = log_file.read_text(errors="replace") + "\n" + log
    if "DONE (PASS" in log:
        status = "pass"
    elif "DONE (FAIL" in log:
        status = "fail"
    elif "DONE (UNKNOWN" in log or "timeout" in log.lower():
        status = "unknown"
    elif "skipped" in log.lower() and proc.returncode != 0:
        status = "error"
    else:
        status = "error" if proc.returncode not in (0, 2) else "fail"
    _SBY_CACHE[(dut, task)] = (status, log, elapsed)
    return status, log, elapsed


def run_formal_oracle(targets: list[str], work: Path) -> tuple[list[str], list[str], str, float]:
    by_cover = {p.cover: p for p in CATALOG}
    by_pid = {p.pid: p for p in CATALOG}
    aimed = [by_pid[t] for t in targets if t in by_pid] or list(CATALOG)
    duts = sorted({p.dut for p in aimed})
    hits: list[str] = []
    retired: list[str] = []
    notes: list[str] = []
    wall = 0.0
    for dut in duts:
        cov_status, cov_log, cov_s = run_sby(dut, "cover", work)
        wall += cov_s
        reached, unreached = parse_sby_log(cov_log)
        notes.append(f"{dut}.cover={cov_status} reached={sorted(reached)} unreached={sorted(unreached)} {cov_s:.2f}s")
        for name in reached:
            prop = by_cover.get(name)
            if prop and prop.bucket != "unreachable":
                hits.append(prop.pid)
        need_prove = [p for p in aimed if p.dut == dut and p.bucket == "unreachable" and p.cover in unreached]
        if not need_prove and cov_status == "skipped":
            continue
        if any(p.dut == dut and p.bucket == "unreachable" for p in aimed):
            pr_status, pr_log, pr_s = run_sby(dut, "prove", work)
            wall += pr_s
            notes.append(f"{dut}.prove={pr_status} {pr_s:.2f}s")
            if pr_status == "pass":
                for p in aimed:
                    if p.dut == dut and p.bucket == "unreachable" and p.cover in unreached:
                        retired.append(p.pid)
            elif "Status: failed" in pr_log or pr_status == "fail":
                notes.append(f"{dut}.prove contradicted (assert broken)")
    return hits, retired, "; ".join(notes), wall


def simulate_sby_batch(
    model: FrozenModel,
    covered: set[str],
    retired: set[str],
    alloc: Allocation,
    knobs: KnobState,
    rng: random.Random,
    work_dir: Path,
) -> tuple[BatchResult, KnobState]:
    if alloc.action != "formal":
        return simulate_batch(model, covered, retired, alloc, knobs, rng)
    open_pts = [p for p in model.points if p.pid not in covered | retired]
    wanted = alloc.target_pids or [p.pid for p in open_pts]
    hits, rets, notes, wall = run_formal_oracle(wanted, work_dir / "sby")
    hours = max(alloc.hour_budget, wall / 3600.0, 0.05)
    return BatchResult("formal", hours, hits, rets, f"sby {wall:.2f}s {notes}"), knobs
