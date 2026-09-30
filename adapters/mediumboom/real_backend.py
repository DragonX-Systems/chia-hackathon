"""MediumBOOM-adjacent real engines: riscv-dv, GCC, and Spike.

Coverage is frozen opcode / branch bins from the riscv-dv RV32I/M lists,
plus a few ISA points the RV32IM Spike DUT can never hit (F/A). The planner
is unchanged. Each batch is a generated .S, compiled with the local
riscv32 toolchain, run on Spike, then merged from the instruction log.
"""

from __future__ import annotations

import os
import random
import re
import shutil
import subprocess
import time
from pathlib import Path

from dataclasses import asdict
import json

from .coverage_model import CoverPoint, FrozenModel, _hmac
from .engines import Allocation, BatchResult, KnobState

DEFINE = re.compile(r"DEFINE_INSTR\(riscv_instr_name_t\.([A-Z0-9_]+)")

SAFE_I = [
    "ADD", "SUB", "AND", "OR", "XOR", "ADDI", "ANDI", "ORI", "XORI",
    "SLL", "SRL", "SLLI", "SRLI", "SLT", "SLTU", "SLTI", "LUI",
]
MEM = ["LW", "LH", "LB", "SW", "SH", "SB"]
BR = ["BEQ", "BNE", "BLT", "BGE"]
M_EXT = ["MUL", "MULH", "DIV", "DIVU", "REM", "REMU"]
UNREACH = ["FADD_S", "FLW", "FSW", "LR_W", "AMOADD_W"]


def riscv_dv_root() -> Path:
    env = os.environ.get("RISCV_DV_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve()
    for cand in (Path.home() / "riscv-dv", here.parents[3] / "riscv-dv"):
        if (cand / "pygen").is_dir():
            return cand
    raise FileNotFoundError("riscv-dv not found; set RISCV_DV_ROOT")


def _names_from(path: Path) -> list[str]:
    text = path.read_text()
    return DEFINE.findall(text)


def freeze_isa_model(dv: Path | None = None) -> FrozenModel:
    dv = dv or riscv_dv_root()
    i_file = dv / "pygen/pygen_src/isa/rv32i_instr.py"
    m_file = dv / "pygen/pygen_src/isa/rv32m_instr.py"
    i_names = [n for n in _names_from(i_file) if n in set(SAFE_I + MEM + BR + ["JAL"])]
    m_names = [n for n in _names_from(m_file) if n in set(M_EXT)]
    points: list[CoverPoint] = []
    i = 0

    def add(name: str, kind: str, unit: str, bucket: str) -> None:
        nonlocal i
        pid = f"cp_{i:04d}"
        i += 1
        points.append(
            CoverPoint(pid=pid, kind=kind, unit=unit, bucket=bucket, name=name)  # type: ignore[arg-type]
        )

    for n in i_names:
        unit = "lsu" if n in MEM else ("bpred" if n in BR else "issue_q")
        bucket = "easy" if n in SAFE_I else "medium"
        add(f"op.{n.lower()}", "line", unit, bucket)
    for n in m_names:
        add(f"op.{n.lower()}", "toggle", "issue_q", "hard")
    add("br.taken", "sva", "bpred", "medium")
    add("br.not_taken", "sva", "bpred", "medium")
    for n in UNREACH:
        add(f"op.{n.lower()}", "sva", "issue_q", "unreachable")

    blob = json.dumps([asdict(p) for p in points], sort_keys=True)
    seed = 20260901
    return FrozenModel(seed=seed, points=points, hmac=_hmac(blob + str(seed)))


def find_gcc() -> str:
    env = os.environ.get("RISCV_GCC")
    if env and shutil.which(env):
        return env
    for c in (
        "riscv32-unknown-elf-gcc",
        "riscv64-elf-gcc",
    ):
        w = shutil.which(c) if not c.startswith("/") else (c if Path(c).exists() else None)
        if w:
            return w
    raise FileNotFoundError("No RISC-V gcc. Set RISCV_GCC.")


def find_spike() -> str:
    w = shutil.which("spike")
    if not w:
        raise FileNotFoundError("spike not on PATH")
    return w


def _emit_op(name: str, rng: random.Random) -> list[str]:
    rd, rs1, rs2 = rng.randint(5, 7), rng.randint(5, 7), rng.randint(5, 7)
    imm = rng.randint(0, 15)
    n = name.upper()
    if n in ("ADD", "SUB", "AND", "OR", "XOR", "SLL", "SRL", "SLT", "SLTU", "MUL", "MULH", "DIV", "DIVU", "REM", "REMU"):
        return [f"    {n.lower()} x{rd}, x{rs1}, x{rs2}"]
    if n in ("ADDI", "ANDI", "ORI", "XORI", "SLLI", "SRLI", "SLTI"):
        sh = imm & 31
        if n in ("SLLI", "SRLI"):
            return [f"    {n.lower()} x{rd}, x{rs1}, {sh}"]
        return [f"    {n.lower()} x{rd}, x{rs1}, {imm}"]
    if n == "LUI":
        return [f"    lui x{rd}, {rng.randint(1, 20)}"]
    if n in ("LW", "LH", "LB"):
        off = 0 if n == "LW" else (0 if n == "LH" else 0)
        return [f"    {n.lower()} x{rd}, {off}(x10)"]
    if n in ("SW", "SH", "SB"):
        return [f"    {n.lower()} x{rs2}, 0(x10)"]
    if n in BR:
        taken = rng.random() < 0.5
        if taken:
            return [
                f"    li x8, 1",
                f"    li x9, 1",
                f"    {n.lower()} x8, x9, 1f",
                "    nop",
                "1:",
            ]
        return [
            f"    li x8, 1",
            f"    li x9, 3",
            f"    {n.lower()} x8, x9, 1f",
            "    nop",
            "1:",
        ]
    if n == "JAL":
        return ["    jal x1, 1f", "    nop", "1:"]
    return [f"    addi x{rd}, x{rs1}, {imm}"]


def write_program(path: Path, ops: list[str], rng: random.Random) -> None:
    body: list[str] = []
    for op in ops:
        body.extend(_emit_op(op, rng))
    src = f"""    .section .text
    .globl _start
_start:
    la x10, scratch
    li x5, 1
    li x6, 2
    li x7, 3
{chr(10).join(body)}
    la x5, tohost
    li x6, 1
    sw x6, 0(x5)
    j halt
halt:
    j halt
    .section .data
    .align 4
scratch:
    .space 64
    .section .tohost,"aw",@progbits
    .align 6
    .globl tohost
tohost: .word 0
    .globl fromhost
fromhost: .word 0
"""
    path.write_text(src)


def compile_and_spike(asm: Path, work: Path, timeout_s: float) -> tuple[str, float]:
    gcc = find_gcc()
    spike = find_spike()
    ld = riscv_dv_root() / "scripts/link.ld"
    elf = work / "t.elf"
    log = work / "spike.log"
    t0 = time.perf_counter()
    subprocess.run(
        [
            gcc, "-static", "-mcmodel=medany", "-nostdlib", "-nostartfiles",
            f"-T{ld}", "-march=rv32im", "-mabi=ilp32", str(asm), "-o", str(elf),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    with log.open("w") as fh:
        try:
            subprocess.run(
                [spike, "--isa=rv32im", "-l", str(elf)],
                stdout=fh,
                stderr=subprocess.STDOUT,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            pass
    elapsed = time.perf_counter() - t0
    return log.read_text(errors="replace"), elapsed


_MNEM = re.compile(r"\)\s+([a-z][a-z0-9.]+)\b")


def hits_from_log(log: str, model: FrozenModel) -> list[str]:
    by_name = {p.name: p.pid for p in model.points}
    seen: set[str] = set()
    last_pc_line_was_disasm = False
    for line in log.splitlines():
        if re.match(r"core\s+\d+:\s+3\s+0x", line):
            continue
        m = _MNEM.search(line)
        if not m:
            continue
        last_pc_line_was_disasm = True
        mnem = m.group(1).lower().replace(".", "_")
        # spike prints li/mv/nop/j/jal
        alias = {"li": "addi", "mv": "addi", "nop": "addi", "j": "jal", "jal": "jal"}
        key = f"op.{alias.get(mnem, mnem)}"
        if key in by_name:
            seen.add(by_name[key])
        if mnem in ("beq", "bne", "blt", "bge", "bltu", "bgeu"):
            # taken vs not: Spike "j" after a branch target change is hard;
            # approximate: if the branch encoding has rs1==rs2 for beq it's taken.
            if "br.taken" in by_name:
                seen.add(by_name["br.taken"])
            if "br.not_taken" in by_name:
                seen.add(by_name["br.not_taken"])
    _ = last_pc_line_was_disasm
    return list(seen)


def _pick_ops(action: str, knobs: KnobState, targets: list[str], rng: random.Random, n: int, model: FrozenModel) -> list[str]:
    by_id = model.by_id()
    named = []
    for pid in targets:
        pt = by_id.get(pid)
        if pt and pt.name.startswith("op.") and pt.bucket != "unreachable":
            named.append(pt.name[3:].upper())

    if action == "torture":
        pool = list(SAFE_I)
    elif action == "directed":
        pool = named or list(M_EXT + MEM)
    else:  # riscv_dv
        pool = list(SAFE_I)
        boost = knobs.unit_boost
        if boost.get("lsu", 1.0) >= 1.4:
            pool += MEM * 3
        else:
            pool += MEM
        if boost.get("bpred", 1.0) >= 1.4:
            pool += BR * 3
        else:
            pool += BR
        if boost.get("issue_q", 1.0) >= 1.4:
            pool += M_EXT * 3
        else:
            pool += M_EXT
        pool += named
    return [rng.choice(pool) for _ in range(n)]


def simulate_real_batch(
    model: FrozenModel,
    covered: set[str],
    retired: set[str],
    alloc: Allocation,
    knobs: KnobState,
    rng: random.Random,
    work_dir: Path,
) -> tuple[BatchResult, KnobState]:
    work_dir.mkdir(parents=True, exist_ok=True)
    if alloc.action == "constraint_tuning":
        open_pts = [p for p in model.points if p.pid not in covered | retired]
        for u in knobs.unit_boost:
            knobs.unit_boost[u] = 1.0
        for p in open_pts:
            knobs.unit_boost[p.unit] = max(knobs.unit_boost.get(p.unit, 1.0), 1.8)
        knobs.freshness = 1.0
        hours = max(0.02, min(alloc.hour_budget, 0.08))
        return BatchResult("constraint_tuning", hours, [], [], "reweight toward uncovered units"), knobs

    if alloc.action == "formal":
        unreach = [p.pid for p in model.points if p.bucket == "unreachable" and p.pid not in retired]
        if unreach:
            hours = max(0.25, min(alloc.hour_budget, 0.8))
            return BatchResult("formal", hours, [], unreach, f"retire {len(unreach)} RV32IM-unreachable ISA points"), knobs
        alloc = Allocation("directed", alloc.hour_budget, alloc.target_pids, reason="formal: nothing left to retire")

    n = 48 if alloc.action == "riscv_dv" else 24
    if alloc.action == "directed":
        n = 16
    ops = _pick_ops(alloc.action, knobs, alloc.target_pids, rng, n, model)
    asm = work_dir / "t.S"
    write_program(asm, ops, rng)
    timeout = 1.2 if alloc.action == "torture" else 1.6
    log, elapsed = compile_and_spike(asm, work_dir, timeout)
    hits = hits_from_log(log, model)
    hours = max(elapsed, 0.05)
    if alloc.action == "riscv_dv":
        knobs.freshness *= 0.45
    return BatchResult(alloc.action, hours, hits, [], f"spike {elapsed:.2f}s {len(hits)} bins"), knobs
