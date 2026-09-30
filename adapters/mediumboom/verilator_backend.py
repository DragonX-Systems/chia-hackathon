"""MediumBOOM-adjacent Verilator scoring adapter.

Frozen yardstick = line / toggle / user-cover bins from a real ``Vtb_top``
dump, plus the named SystemVerilog Assertion (SVA) list SymbiYosys can
prove. Torture / riscv-dv / directed compile a program, load it into Tightly
Coupled Memory (TCM), run Verilator, merge counts. Formal is Bounded Model
Checking (BMC) / k-induction on leftover SVA holes, not a yield model.

This is not generated MediumBOOM Verilog.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

from .astra_writer import write_last_mile_asm
from .coverage_model import CoverPoint, FrozenModel, _hmac
from .engines import Allocation, BatchResult, KnobState
from .real_backend import _pick_ops, find_gcc
from .sby_backend import CATALOG, run_formal_oracle

HERE = Path(__file__).resolve().parent
TB = HERE / "testbenches" / "verilator"
PKG = HERE.parents[1]
CORE_FILES = (
    "riscv_decoder.v",
    "riscv_alu.v",
    "riscv_lsu.v",
    "riscv_exec.v",
    "riscv_issue.v",
    "riscv_csr.v",
    "riscv_fetch.v",
    "riscv_decode.v",
    "riscv_multiplier.v",
    "riscv_divider.v",
    "tb_top.sv",
)
# Verilator 5 packed record: C '\x01f\x02file\x01l\x02102…' COUNT
C_LINE = re.compile(r"^C '(.*)'\s+(\d+)\s*$")


def verilator_sim() -> Path:
    return TB / "obj_dir" / "Vtb_top"


def ensure_built() -> Path:
    sim = verilator_sim()
    if sim.exists():
        return sim
    script = TB / "build.sh"
    subprocess.run(["bash", str(script)], check=True)
    if not sim.exists():
        raise FileNotFoundError(f"Verilator build did not produce {sim}")
    return sim


def find_objcopy() -> str:
    gcc = find_gcc()
    cand = gcc.replace("-gcc", "-objcopy")
    if Path(cand).exists() or shutil.which(cand):
        return cand
    w = shutil.which("riscv32-unknown-elf-objcopy")
    if w:
        return w
    raise FileNotFoundError("No RISC-V objcopy. Set RISCV_GCC.")


def find_nm() -> str:
    gcc = find_gcc()
    cand = gcc.replace("-gcc", "-nm")
    if Path(cand).exists() or shutil.which(cand):
        return cand
    w = shutil.which("riscv32-unknown-elf-nm")
    if w:
        return w
    raise FileNotFoundError("No RISC-V nm.")


def tohost_from_elf(elf: Path) -> int:
    try:
        out = subprocess.check_output([find_nm(), str(elf)], text=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return 0xF000
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[-1] == "tohost":
            return int(parts[0], 16)
    return 0xF000


def compile_bin(asm: Path, work: Path) -> tuple[Path, int]:
    gcc = find_gcc()
    objcopy = find_objcopy()
    elf = work / "t.elf"
    raw = work / "t.bin"
    ld = TB / "link.ld"
    subprocess.run(
        [
            gcc,
            "-static",
            "-mcmodel=medany",
            "-nostdlib",
            "-nostartfiles",
            f"-T{ld}",
            "-march=rv32im",
            "-mabi=ilp32",
            str(asm),
            "-o",
            str(elf),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run([objcopy, "-O", "binary", str(elf), str(raw)], check=True, capture_output=True)
    return raw, tohost_from_elf(elf)


def write_tcm_program(path: Path, ops: list[str], rng: random.Random) -> None:
    """Like Spike's writer, but halt via an absolute tohost store at 0xF000."""
    from .real_backend import _emit_op

    body: list[str] = []
    for op in ops:
        body.extend(_emit_op(op, rng))
    path.write_text(
        f"""    .section .text
    .globl _start
_start:
    la x10, scratch
    li x5, 1
    li x6, 2
    li x7, 3
{chr(10).join(body)}
    li x5, 0xF000
    li x6, 1
    sw x6, 0(x5)
halt:
    j halt
    .section .data
    .align 4
scratch:
    .space 64
    .section .tohost,"aw",@progbits
    .align 4
    .globl tohost
tohost: .word 0
"""
    )


def run_sim(bin_path: Path, cov_path: Path, tohost: int = 0xF000, timeout_s: float = 8.0) -> tuple[str, float]:
    sim = ensure_built()
    bin_path = Path(bin_path).resolve()
    cov_path = Path(cov_path).resolve()
    cov_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [
                str(sim),
                f"+bin={bin_path}",
                f"+cov={cov_path}",
                "+max=20000",
                f"+tohost={tohost}",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            cwd=str(TB),
        )
    except subprocess.TimeoutExpired as exc:
        return f"timeout {exc}", timeout_s
    elapsed = time.perf_counter() - t0
    return ((proc.stdout or "") + (proc.stderr or "")).strip(), elapsed


def _fields_from_payload(payload: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in payload.split("\x01"):
        if "\x02" not in part:
            continue
        key, val = part.split("\x02", 1)
        fields[key] = val
    return fields


def parse_coverage_dat(path: Path) -> list[dict]:
    """Parse a Verilator 5 coverage.dat dump into file/kind/line/count/name rows."""
    if not path.exists():
        return []
    rows: list[dict] = []
    for raw in path.read_text(errors="replace").splitlines():
        m = C_LINE.match(raw.rstrip())
        if not m:
            continue
        fields = _fields_from_payload(m.group(1))
        count = int(m.group(2))
        filename = fields.get("f", "")
        base = Path(filename).name or filename
        lineno = int(fields.get("l") or 0)
        col = int(fields.get("n") or 0)
        page = fields.get("page", "")
        obj = fields.get("o", "")
        if page.startswith("v_user"):
            kind = "sva"
            name = f"tb.{obj}" if obj else f"{base}:user:l{lineno}c{col}"
        elif page.startswith("v_branch"):
            kind = "toggle"
            name = f"{base}:toggle:l{lineno}c{col}:{obj}"
        else:
            kind = "line"
            name = f"{base}:line:l{lineno}c{col}:{obj}"
        rows.append(
            {
                "file": filename,
                "base": base,
                "kind": kind,
                "line": lineno,
                "col": col,
                "count": count,
                "name": name,
                "page": page,
                "obj": obj,
            }
        )
    return rows


def _keep_file(base: str) -> bool:
    return base in CORE_FILES


def _unit_bucket(base: str, kind: str) -> tuple[str, str]:
    if base in ("riscv_lsu.v", "tb_top.sv"):
        return "lsu", "medium" if kind == "toggle" else "easy"
    if base in ("riscv_fetch.v", "riscv_decode.v"):
        return "bpred", "medium"
    if base in ("riscv_multiplier.v", "riscv_divider.v", "riscv_csr.v", "riscv_issue.v"):
        return "issue_q", "hard"
    return "issue_q", "easy"


def _seed_program(work: Path) -> tuple[Path, int]:
    work.mkdir(parents=True, exist_ok=True)
    asm = work / "seed.S"
    write_tcm_program(asm, ["ADD", "ADDI"], random.Random(0))
    return compile_bin(asm, work)


def freeze_verilator_model(work: Path | None = None) -> FrozenModel:
    work = Path(work) if work else Path("/tmp/chia_verilator_freeze")
    work.mkdir(parents=True, exist_ok=True)
    raw, _tohost = _seed_program(work)
    cov = work / "seed_coverage.dat"
    log, _ = run_sim(raw, cov, tohost=0xF000)
    rows = [r for r in parse_coverage_dat(cov) if _keep_file(r["base"])]
    if len(rows) < 20:
        raise RuntimeError(
            f"Verilator freeze produced {len(rows)} core bins ({cov} exists={cov.exists()} log={log!r}). "
            "Rebuild adapters/mediumboom/testbenches/verilator and ensure "
            "Vtb_top writes coverage.dat."
        )
    # Drop RAM-like toggle explosion; keep a stride of toggles so the list stays
    # a chip-shaped mix without 10k bit bins.
    lines = [r for r in rows if r["kind"] == "line"]
    toggles = [r for r in rows if r["kind"] == "toggle"]
    users = [r for r in rows if r["kind"] == "sva"]
    if len(toggles) > 180:
        step = max(1, len(toggles) // 180)
        toggles = toggles[::step][:180]
    if len(lines) > 280:
        step = max(1, len(lines) // 280)
        lines = lines[::step][:280]

    points: list[CoverPoint] = []
    seen: set[str] = set()
    i = 0
    for r in lines + toggles + users:
        if r["name"] in seen:
            continue
        seen.add(r["name"])
        unit, bucket = _unit_bucket(r["base"], r["kind"])
        kind = r["kind"] if r["kind"] in ("line", "toggle", "sva") else "line"
        points.append(
            CoverPoint(pid=f"cp_{i:04d}", kind=kind, unit=unit, bucket=bucket, name=r["name"])  # type: ignore[arg-type]
        )
        i += 1

    for prop in CATALOG:
        points.append(
            CoverPoint(
                pid=f"cp_{i:04d}",
                kind="sva",
                unit=prop.unit,  # type: ignore[arg-type]
                bucket=prop.bucket,  # type: ignore[arg-type]
                name=prop.name,
            )
        )
        i += 1

    seed = 20260907
    blob = json.dumps([asdict(p) for p in points], sort_keys=True)
    return FrozenModel(seed=seed, points=points, hmac=_hmac(blob + str(seed)))


def hits_from_coverage(cov: Path, model: FrozenModel) -> list[str]:
    by_name = {p.name: p.pid for p in model.points}
    hits: list[str] = []
    for r in parse_coverage_dat(cov):
        if r["count"] <= 0:
            continue
        pid = by_name.get(r["name"])
        if pid:
            hits.append(pid)
            continue
        # User-cover names in the dump may be the bare label.
        bare = r["name"].split(".")[-1]
        for name, pid2 in by_name.items():
            if name.endswith(bare) or name.split(".")[-1] == bare:
                hits.append(pid2)
    # Dedup
    seen: set[str] = set()
    out: list[str] = []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def _sva_targets(model: FrozenModel, wanted: list[str]) -> list[str]:
    """Map this model's SVA pids onto the SymbiYosys catalog pids."""
    by_name = {p.name: p.pid for p in CATALOG}
    out = []
    by_id = model.by_id()
    for pid in wanted:
        pt = by_id.get(pid)
        if pt is None or pt.kind != "sva":
            continue
        cat = by_name.get(pt.name)
        if cat:
            out.append(cat)
    return out


def simulate_verilator_batch(
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
        open_pts = [p for p in model.points if p.pid not in covered | retired and p.kind == "sva"]
        wanted = alloc.target_pids or [p.pid for p in open_pts]
        cat_ids = _sva_targets(model, wanted)
        if not cat_ids:
            hours = max(0.05, min(alloc.hour_budget, 0.2))
            return BatchResult("formal", hours, [], [], "no leftover SVA mapped to SymbiYosys"), knobs
        hits_cat, rets_cat, notes, wall = run_formal_oracle(cat_ids, work_dir / "sby")
        cat_by_pid = {p.pid: p.name for p in CATALOG}
        name_to_here = {p.name: p.pid for p in model.points}
        hits = [name_to_here[cat_by_pid[x]] for x in hits_cat if cat_by_pid.get(x) in name_to_here]
        rets = [name_to_here[cat_by_pid[x]] for x in rets_cat if cat_by_pid.get(x) in name_to_here]
        hours = max(wall, 0.05)
        return BatchResult("formal", hours, hits, rets, f"sby wall_s={wall:.3f} {notes}"), knobs

    n = 40 if alloc.action == "riscv_dv" else 24
    if alloc.action == "directed":
        n = 20
    asm = work_dir / "t.S"
    writer = "template"
    if alloc.action == "directed":
        # Prefer collateral already built by chia_write_artifact (Enh 3).
        if asm.exists() and asm.stat().st_size >= 20:
            writer = "chia_artifact_node:prewritten"
        else:
            names = [model.by_id()[p].name for p in alloc.target_pids if p in model.by_id()]
            writer = write_last_mile_asm(asm, names, rng)
            if not asm.exists() or asm.stat().st_size < 20:
                ops = _pick_ops(alloc.action, knobs, alloc.target_pids, rng, n, model)
                write_tcm_program(asm, ops, rng)
                writer = "template-fallback"
    else:
        ops = _pick_ops(alloc.action, knobs, alloc.target_pids, rng, n, model)
        write_tcm_program(asm, ops, rng)

    t0 = time.perf_counter()
    raw, _tohost = compile_bin(asm, work_dir)
    cov = work_dir / "coverage.dat"
    log, sim_s = run_sim(raw, cov, tohost=0xF000)
    wall = time.perf_counter() - t0
    hits = hits_from_coverage(cov, model)
    if alloc.action == "riscv_dv":
        knobs.freshness *= 0.45
    hours = max(wall, 0.05)
    return (
        BatchResult(
            alloc.action,
            hours,
            hits,
            [],
            f"verilator wall_s={wall:.3f} sim={sim_s:.3f} hits={len(hits)} writer={writer} {log[:80]}",
        ),
        knobs,
    )
