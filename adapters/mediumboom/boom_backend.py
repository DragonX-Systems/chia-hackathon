"""MediumBOOM generated-RTL backend adapter.

Discovers Chipyard ``gen-collateral`` and scores:

* Formal — SymbiYosys Bounded Model Checking (BMC) on named cover properties.
  Default scope is generated **ChipTop** (entire SoC RTL closure) via
  ``adapters.mediumboom/soc_formal.py`` (``BOOM_FORMAL_SCOPE=chiptop|tile|lsu``).
  Firtool-incompatible monitors / external SRAMs are stubbed. The legacy
  Load/Store Unit (LSU)-only recipe remains as ``BOOM_FORMAL_SCOPE=lsu``.
* Constrained-random verification (CRV) — torture / ``riscv_dv`` / directed
  run UCB ``riscv-torture`` on the Chipyard MediumBOOM Verilator System on
  Chip (SoC) when the suite is on disk (``adapters/mediumboom/scripts/generate_riscv_torture.sh``).
  Mini 26-opcode assembler is fallback only. Directed prefers mem-heavy
  ``mediumboom_dir_*.S``. Hits are **SoC-scoped** opcode / class / run bins
  credited on SoC ``*** PASSED ***`` — not a driven-LSU Verilator dump.
* Opt-in LSU-slice dump: set ``BOOM_USE_LSU_COV=1`` to freeze/score the
  separate ``Vlsu_cov_tb`` line bins (honestly LSU-only; never labeled SoC).
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .coverage_model import CoverPoint, FrozenModel, _hmac
from .engines import Allocation, BatchResult, KnobState

HERE = Path(__file__).resolve().parent
PKG = HERE.parents[1]
RTL = HERE / "rtl"

# RV64 ops we emit and score on MediumBOOM SoC PASS (no commit printf in this config).
SAFE_I = [
    "ADD", "SUB", "AND", "OR", "XOR", "ADDI", "ANDI", "ORI", "XORI",
    "SLL", "SRL", "SLLI", "SRLI", "SLT", "SLTU", "SLTI", "LUI",
]
MEM = ["LD", "SD", "LW", "SW"]
BR = ["BEQ", "BNE", "BLT", "BGE"]
M_EXT = ["MUL", "DIV", "DIVU", "REM", "REMU"]
PORT_RE = re.compile(
    r"^\s*(input|output|inout)\s+(?:wire|logic|reg)?\s*(\[[^\]]+\])?\s*(\w+)",
    re.M,
)
MODULE_RE = re.compile(r"^\s*module\s+(\w+)", re.M)


@dataclass(frozen=True)
class GenDut:
    collateral: Path
    sv: Path
    module: str
    ports: tuple[str, ...]
    dirs: tuple[str, ...]
    clk: str
    rst: str
    widths: tuple[str, ...] = ()


def chipyard_root() -> Path:
    env = os.environ.get("CHIPYARD_ROOT")
    if env:
        return Path(env)
    return PKG / "third_party" / "chipyard"


def find_gen_collateral() -> Path:
    env = os.environ.get("CHIPYARD_GEN")
    if env:
        p = Path(env)
        if p.is_dir():
            return p
        raise FileNotFoundError(f"CHIPYARD_GEN={env} is not a directory")
    marker = PKG / "artifacts_mediumboom" / "gen_collateral.json"
    if marker.exists():
        data = json.loads(marker.read_text())
        p = Path(data["gen_collateral"])
        if p.is_dir():
            return p
    hits = sorted(chipyard_root().glob("sims/verilator/generated-src/**/gen-collateral"))
    if hits:
        return hits[0]
    raise FileNotFoundError(
        "No MediumBOOM gen-collateral. Run: bash adapters/mediumboom/scripts/setup_mediumboom.sh"
    )


def _score_sv(path: Path) -> tuple[int, Path]:
    name = path.name.lower()
    score = 0
    if name in {"lsu.sv", "boomlsu.sv"}:
        score += 50
    if "lsu" in name:
        score += 20
    if "mshr" in name:
        score += 5
    if "tile" in name or "chiptop" in name or "system" in name:
        score -= 40
    try:
        score -= min(path.stat().st_size // 50_000, 15)
    except OSError:
        pass
    return score, path


def pick_lsu_sv(collateral: Path) -> Path:
    cands = [p for p in collateral.glob("*.sv") if re.search(r"lsu", p.name, re.I)]
    if not cands:
        cands = [p for p in collateral.glob("*.sv") if re.search(r"boom", p.name, re.I)]
    if not cands:
        raise FileNotFoundError(f"No LSU/BOOM SystemVerilog in {collateral}")
    return sorted(cands, key=_score_sv)[-1]


def parse_module(sv: Path) -> tuple[str, list[str], list[str], list[str]]:
    text = sv.read_text(errors="replace")
    mods = MODULE_RE.findall(text)
    if not mods:
        raise ValueError(f"No module in {sv}")
    module = mods[0]
    header = text.split(f"module {module}", 1)[-1]
    header = header.split(";", 1)[0]
    ports = PORT_RE.findall(header)
    if not ports:
        body = text.split(f"module {module}", 1)[-1][:12000]
        ports = PORT_RE.findall(body)
    if not ports:
        raise ValueError(f"No ports parsed from {sv} module {module}")
    dirs = [d for d, _w, _n in ports]
    names = [n for _d, _w, n in ports]
    widths = [w or "" for _d, w, _n in ports]
    return module, names, dirs, widths


def _clk_rst(ports: list[str]) -> tuple[str, str]:
    """Prefer real clock/reset ports (ChipTop uses ``clock_uncore`` / ``reset_io``)."""
    lower = {p.lower(): p for p in ports}
    clk = None
    for cand in ("clock", "clk", "clock_uncore", "clock_in"):
        if cand in lower:
            clk = lower[cand]
            break
    if clk is None:
        clk = next(
            (
                p
                for p in ports
                if "clock" in p.lower()
                and "out" not in p.lower()
                and "tap" not in p.lower()
                and "axi4" not in p.lower()
            ),
            ports[0],
        )
    rst = None
    for cand in ("reset", "rst", "reset_io", "reset_in", "rst_n", "resetn"):
        if cand in lower:
            rst = lower[cand]
            break
    if rst is None:
        rst = next(
            (p for p in ports if "reset" in p.lower() and "jtag" not in p.lower()),
            ports[1] if len(ports) > 1 else ports[0],
        )
    return clk, rst


def discover_dut() -> GenDut:
    coll = find_gen_collateral()
    sv = pick_lsu_sv(coll)
    module, ports, dirs, widths = parse_module(sv)
    clk, rst = _clk_rst(ports)
    return GenDut(coll, sv, module, tuple(ports), tuple(dirs), clk, rst, tuple(widths))


# Historical smoke/proxy catalog only. This is NOT microarchitectural coverage.
# Source-pinned semantic assertions/scenarios live in verification/boom/.
SVA_SPEC = (
    ("cp_b000", "mediumboom.lsu.port_toggle_0", "sva", "lsu", "easy", "a"),
    ("cp_b001", "mediumboom.lsu.port_toggle_1", "sva", "lsu", "easy", "b"),
    ("cp_b002", "mediumboom.lsu.both_data", "sva", "lsu", "medium", "both"),
    ("cp_b003", "mediumboom.lsu.rst_release", "sva", "lsu", "medium", "rst_rel"),
    ("cp_b004", "mediumboom.lsu.contradiction", "sva", "lsu", "unreachable", "contra"),
    ("cp_b005", "mediumboom.lsu.never_cmd", "sva", "lsu", "unreachable", "never"),
    ("cp_b006", "mediumboom.lsu.dis_fire", "sva", "lsu", "medium", "dis_fire"),
    ("cp_b007", "mediumboom.lsu.exe_req", "sva", "lsu", "medium", "exe_req"),
)
# Back-compat alias for formal cover-name map.
CATALOG_SPEC = SVA_SPEC

# Opcode / branch bins — SoC CRV yardstick (SoC binary has VM_COVERAGE=0).
CRV_OPS = tuple(SAFE_I + MEM + BR + M_EXT + ["FENCE"])

# Extra SoC-scoped bins: class mix from the program that ran, plus run evidence
# parsed from the SoC simulator log. Not Verilator line coverage.
SOC_CLASS_BINS = (
    ("soc.class.alu", "issue_q", "easy"),
    ("soc.class.mem_load", "lsu", "medium"),
    ("soc.class.mem_store", "lsu", "medium"),
    ("soc.class.branch", "bpred", "medium"),
    ("soc.class.muldiv", "issue_q", "hard"),
    ("soc.class.fence", "lsu", "medium"),
)
SOC_RUN_BINS = (
    ("soc.pass", "issue_q", "easy"),
    ("soc.cycles_ge_1k", "issue_q", "easy"),
    ("soc.cycles_ge_10k", "issue_q", "medium"),
    ("soc.cycles_ge_100k", "issue_q", "hard"),
)
CYCLES_RE = re.compile(r"Completed after\s+(\d+)\s+simulation cycles", re.I)


def _data_ports(dut: GenDut) -> list[str]:
    skip = {dut.clk, dut.rst}
    return [p for p in dut.ports if p not in skip][:8]


def write_wrapper(dut: GenDut, dest: Path) -> Path:
    from .lsu_driver import write_driven_formal_wrapper

    return write_driven_formal_wrapper(dut, dest)


def _child_sv(dut: GenDut) -> list[Path]:
    """Firtool split-Verilog: LSU instantiates a few sibling .sv files."""
    skip = {
        "if",
        "else",
        "for",
        "begin",
        "end",
        "always",
        "initial",
        "assign",
        "module",
        "function",
        "task",
        "case",
        "casex",
        "casez",
        "wire",
        "logic",
        "reg",
        "unique",
        "priority",
        "assert",
        "cover",
        "assume",
    }
    needed = {dut.sv}
    frontier = [dut.sv]
    while frontier:
        text = frontier.pop().read_text(errors="replace")
        for name in set(re.findall(r"^\s*([A-Za-z_]\w*)\s+\w+\s*\(", text, re.M)):
            if name in skip:
                continue
            p = dut.collateral / f"{name}.sv"
            if p.exists() and p not in needed:
                needed.add(p)
                frontier.append(p)
    return sorted(p for p in needed if p != dut.sv)


def write_sby(dut: GenDut, dest: Path, depth: int) -> Path:
    wrapper = dest.parent / "mediumboom_lsu_cover.sv"
    write_wrapper(dut, wrapper)
    kids = _child_sv(dut)
    reads = [
        f"read -formal -DSYNTHESIS -DPRINTF_COND=0 -DSTOP_COND=0 -I {dut.collateral} {wrapper.name}",
        f"read -formal -DSYNTHESIS -DPRINTF_COND=0 -DSTOP_COND=0 -I {dut.collateral} {dut.sv.name}",
    ]
    files = [str(wrapper), str(dut.sv)]
    for kid in kids:
        reads.append(
            f"read -formal -DSYNTHESIS -DPRINTF_COND=0 -DSTOP_COND=0 -I {dut.collateral} {kid.name}"
        )
        files.append(str(kid))
    dest.write_text(
        "[tasks]\ncover\nprove\n\n"
        "[options]\n"
        f"cover: mode cover\ncover: depth {depth}\n"
        f"prove: mode prove\nprove: depth {depth}\n\n"
        "[engines]\nsmtbmc z3\n\n"
        "[script]\n"
        + "\n".join(reads)
        + "\nprep -top mediumboom_lsu_cover\n\n"
        "[files]\n"
        + "\n".join(files)
        + "\n"
    )
    return dest


def freeze_boom_model(work: Path | None = None) -> FrozenModel:
    """LSU SVA (formal) + SoC-scoped CRV bins by default.

    Set ``BOOM_USE_LSU_COV=1`` to append observed driven-LSU Verilator line
    bins instead of the SoC opcode/class/run yardstick (LSU-slice experiment
    only — do not report those as whole-SoC coverage).
    """
    points: list[CoverPoint] = []
    for pid, name, kind, unit, bucket, _cover in SVA_SPEC:
        points.append(
            CoverPoint(pid=pid, kind=kind, unit=unit, bucket=bucket, name=name)  # type: ignore[arg-type]
        )
    i = len(points)
    use_lsu = os.environ.get("BOOM_USE_LSU_COV", "0") == "1"
    observed = _try_observed_bins(work, start_i=i) if use_lsu else []
    if observed:
        points.extend(observed)
        seed = 20260909
    else:
        for op in CRV_OPS:
            unit = "lsu" if op in MEM else ("bpred" if op in BR else "issue_q")
            if op in SAFE_I:
                bucket, kind = "easy", "line"
            elif op in MEM + BR:
                bucket, kind = "medium", "line"
            else:
                bucket, kind = "hard", "toggle"
            points.append(
                CoverPoint(
                    pid=f"cp_b{i:03d}",
                    kind=kind,  # type: ignore[arg-type]
                    unit=unit,  # type: ignore[arg-type]
                    bucket=bucket,  # type: ignore[arg-type]
                    name=f"op.{op.lower()}",
                )
            )
            i += 1
        points.append(
            CoverPoint(
                pid=f"cp_b{i:03d}",
                kind="line",
                unit="bpred",
                bucket="medium",
                name="br.taken",
            )
        )
        i += 1
        points.append(
            CoverPoint(
                pid=f"cp_b{i:03d}",
                kind="line",
                unit="bpred",
                bucket="medium",
                name="br.not_taken",
            )
        )
        i += 1
        for name, unit, bucket in SOC_CLASS_BINS + SOC_RUN_BINS:
            kind = "toggle" if bucket == "hard" else "line"
            points.append(
                CoverPoint(
                    pid=f"cp_b{i:03d}",
                    kind=kind,  # type: ignore[arg-type]
                    unit=unit,  # type: ignore[arg-type]
                    bucket=bucket,  # type: ignore[arg-type]
                    name=name,
                )
            )
            i += 1
        seed = 20260914
    blob = json.dumps([asdict(p) for p in points], sort_keys=True)
    return FrozenModel(seed=seed, hmac=_hmac(blob + str(seed)), points=points)


def _try_observed_bins(work: Path | None, start_i: int) -> list[CoverPoint]:
    from .lsu_driver import coverage_points_from_dump, ensure_lsu_cov_sim, run_lsu_cov

    if os.environ.get("BOOM_SKIP_LSU_COV", "0") == "1":
        return []
    if not ensure_lsu_cov_sim():
        return []
    work = Path(work) if work else PKG / "artifacts_mediumboom" / "lsu_cov_freeze"
    work.mkdir(parents=True, exist_ok=True)
    cov = work / "seed_coverage.dat"
    # Idle-only seed so later load/store pulses can still move line bins.
    log, _ = run_lsu_cov(["idle", "idle"], cov, max_cycles=128)
    pts = coverage_points_from_dump(cov, start_i=start_i)
    if len(pts) < 20:
        return []
    (work / "seed_log.txt").write_text(log)
    return pts


def find_mediumboom_sim() -> Path | None:
    env = os.environ.get("MEDIUMBOOM_SIM")
    if env and Path(env).is_file():
        return Path(env)
    cand = (
        chipyard_root()
        / "sims"
        / "verilator"
        / "simulator-chipyard.harness.MediumBoomV3Config"
    )
    # Chipyard names use dots in MODEL_PACKAGE: simulator-chipyard.harness-MediumBoomV3Config
    alt = chipyard_root() / "sims" / "verilator" / "simulator-chipyard.harness-MediumBoomV3Config"
    for p in (cand, alt):
        if p.is_file():
            return p
    return None


def torture_root() -> Path:
    env = os.environ.get("RISCV_TORTURE_ROOT")
    if env:
        return Path(env)
    return chipyard_root() / "tools" / "torture"


def find_torture_suite() -> Path | None:
    # Compact, reproducible real-DUT pilots can opt into the built-in program
    # generator instead of the much longer checked-in torture corpus.
    if os.environ.get("BOOM_USE_MINI_ASM", "0") == "1":
        return None
    env = os.environ.get("RISCV_TORTURE_SUITE")
    if env:
        p = Path(env)
        if p.is_dir() and any(p.glob("*.S")):
            return p
    for cand in (
        PKG / "artifacts_mediumboom" / "riscv_torture",
        torture_root() / "output",
    ):
        if cand.is_dir() and any(cand.glob("*.S")):
            return cand
    return None


def _pick_torture_source(suite: Path, action: str, rng: random.Random) -> Path:
    """Prefer directed mem-heavy suite for directed; otherwise any mediumboom_*.S."""
    all_src = sorted(suite.glob("*.S"))
    if not all_src:
        raise FileNotFoundError(f"no .S in {suite}")
    if action == "directed":
        directed = [p for p in all_src if "_dir_" in p.name or p.name.startswith("mediumboom_dir")]
        pool = directed or all_src
    elif action == "riscv_dv":
        crv = [p for p in all_src if "_dir_" not in p.name]
        pool = crv or all_src
    else:
        pool = all_src
    return pool[rng.randrange(len(pool))]


def compile_torture_asm(asm: Path, elf: Path) -> None:
    gcc = find_rv64_gcc()
    env_p = torture_root() / "env" / "p"
    cmd = [
        gcc,
        "-nostdlib",
        "-nostartfiles",
        "-march=rv64ima_zicsr_zifencei",
        "-mabi=lp64",
        "-include",
        str(HERE / "stimulus" / "as_compat.h"),
        f"-I{env_p}",
        f"-T{env_p / 'link.ld'}",
        "-o",
        str(elf),
        str(asm),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _credits_from_asm(text: str) -> set[str]:
    names = {op.lower() for op in CRV_OPS}
    credits: set[str] = set()
    toks = re.findall(r"\b([a-z.]+)\b", text.lower())
    for tok in toks:
        if tok in names:
            credits.add(f"op.{tok}")
        if tok in {b.lower() for b in BR}:
            credits.add("br.taken")
            credits.add("br.not_taken")
    if any(t in names and t.upper() in SAFE_I for t in toks):
        credits.add("soc.class.alu")
    if any(t in {"ld", "lw", "lb", "lh", "lwu", "lbu", "lhu"} for t in toks):
        credits.add("soc.class.mem_load")
    if any(t in {"sd", "sw", "sb", "sh"} for t in toks):
        credits.add("soc.class.mem_store")
    if any(t in {b.lower() for b in BR} for t in toks):
        credits.add("soc.class.branch")
    if any(t in {m.lower() for m in M_EXT} for t in toks):
        credits.add("soc.class.muldiv")
    if any(t.startswith("fence") for t in toks):
        credits.add("soc.class.fence")
        credits.add("op.fence")
    return credits


def _soc_run_credits(log: str) -> set[str]:
    """Bins from SoC simulator log evidence (requires PASS caller)."""
    credits = {"soc.pass"}
    m = CYCLES_RE.search(log)
    if not m:
        return credits
    cycles = int(m.group(1))
    if cycles >= 1_000:
        credits.add("soc.cycles_ge_1k")
    if cycles >= 10_000:
        credits.add("soc.cycles_ge_10k")
    if cycles >= 100_000:
        credits.add("soc.cycles_ge_100k")
    return credits


def find_rv64_gcc() -> str:
    env = os.environ.get("RISCV64_GCC") or os.environ.get("RISCV_GCC")
    if env and (Path(env).exists() or shutil.which(env)):
        return env if Path(env).exists() else shutil.which(env)  # type: ignore[return-value]
    for c in ("riscv64-elf-gcc", "riscv64-unknown-elf-gcc"):
        w = shutil.which(c)
        if w:
            return w
    raise FileNotFoundError("No RV64 gcc. Install riscv64-elf-gcc or set RISCV64_GCC.")


def _emit_op(name: str, rng: random.Random) -> tuple[list[str], set[str]]:
    """Return asm lines and credit tags (op.* / br.*)."""
    rd, rs1, rs2 = rng.randint(5, 7), rng.randint(5, 7), rng.randint(5, 7)
    imm = rng.randint(0, 15)
    n = name.upper()
    tags: set[str] = {f"op.{n.lower()}"}
    if n in (
        "ADD", "SUB", "AND", "OR", "XOR", "SLL", "SRL", "SLT", "SLTU",
        "MUL", "DIV", "DIVU", "REM", "REMU",
    ):
        return [f"    {n.lower()} x{rd}, x{rs1}, x{rs2}"], tags
    if n in ("ADDI", "ANDI", "ORI", "XORI", "SLLI", "SRLI", "SLTI"):
        sh = imm & 31
        if n in ("SLLI", "SRLI"):
            return [f"    {n.lower()} x{rd}, x{rs1}, {sh}"], tags
        return [f"    {n.lower()} x{rd}, x{rs1}, {imm}"], tags
    if n == "LUI":
        return [f"    lui x{rd}, {rng.randint(1, 20)}"], tags
    if n in ("LD", "LW"):
        return [f"    {n.lower()} x{rd}, 0(x10)"], tags
    if n in ("SD", "SW"):
        return [f"    {n.lower()} x{rs2}, 0(x10)"], tags
    if n == "FENCE":
        return ["    fence iorw, iorw"], tags
    if n in BR:
        taken = rng.random() < 0.5
        tags.add("br.taken" if taken else "br.not_taken")
        if taken:
            return [
                "    li x8, 1",
                "    li x9, 1",
                f"    {n.lower()} x8, x9, 1f",
                "    nop",
                "1:",
            ], tags
        return [
            "    li x8, 1",
            "    li x9, 3",
            f"    {n.lower()} x8, x9, 1f",
            "    nop",
            "1:",
        ], tags
    return [f"    addi x{rd}, x{rs1}, {imm}"], {f"op.addi"}


def _directed_ops_from_artifact(text: str) -> list[str]:
    """Extract supported instructions from a CHIA artifact for SoC wrapping.

    Artifact writers target a small TCM/Spike-style environment. BOOM instead
    needs the repository's loadmem/tohost wrapper, so preserve the candidate's
    operation stream and regenerate only that SoC-specific shell.
    """
    supported = set(SAFE_I + MEM + BR + M_EXT + ["FENCE"])
    ops: list[str] = []
    for line in text.splitlines():
        line = re.split(r"[#;]", line, maxsplit=1)[0]
        match = re.match(r"^\s*([A-Za-z][A-Za-z0-9.]*)\b", line)
        if match:
            op = match.group(1).upper()
            if op in supported:
                ops.append(op)
    return ops[:64]


def write_rv64_program(path: Path, ops: list[str], rng: random.Random) -> set[str]:
    body: list[str] = []
    credits: set[str] = set()
    for op in ops:
        lines, tags = _emit_op(op, rng)
        body.extend(lines)
        credits |= tags
    # Always +loadmem on MediumBOOM: TSI PutPartial trips TileLink monitors for
    # larger ELFs. tohost exit uses RV64 sd.
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
    sd x6, 0(x5)
1:  j 1b
    .section .data
    .align 3
scratch:
    .space 64
    .section .tohost,"aw",@progbits
    .align 6
    .globl tohost
tohost: .dword 0
    .globl fromhost
fromhost: .dword 0
"""
    path.write_text(src)
    return credits


def compile_rv64(asm: Path, elf: Path) -> None:
    gcc = find_rv64_gcc()
    # Include M when any mul/div may appear; harmless for I-only programs.
    cmd = [
        gcc,
        "-nostdlib",
        "-nostartfiles",
        "-march=rv64im",
        "-mabi=lp64",
        "-Ttext=0x80000000",
        "-Wl,--no-relax",
        "-o",
        str(elf),
        str(asm),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _sim_env() -> dict[str, str]:
    env = os.environ.copy()
    riscv = env.get("RISCV", "/tmp/cy-riscv")
    dram = str(chipyard_root() / "tools" / "DRAMSim2")
    lib = f"{riscv}/lib:/opt/homebrew/lib:{dram}"
    env["RISCV"] = riscv
    env["DYLD_LIBRARY_PATH"] = f"{lib}:{env.get('DYLD_LIBRARY_PATH', '')}"
    env["LIBRARY_PATH"] = f"{riscv}/lib:/opt/homebrew/lib:{env.get('LIBRARY_PATH', '')}"
    return env


def run_mediumboom_elf(elf: Path, work: Path, max_cycles: int = 500_000) -> tuple[bool, str, float]:
    sim = find_mediumboom_sim()
    if not sim:
        return False, "no MediumBOOM simulator binary", 0.0
    elf = elf.resolve()
    work = work.resolve()
    if not elf.is_file():
        return False, f"elf missing: {elf}", 0.0
    dram_ini = (
        chipyard_root()
        / "generators"
        / "testchipip"
        / "src"
        / "main"
        / "resources"
        / "dramsim2_ini"
    )
    log_path = work / "sim.log"
    cmd = [
        str(sim.resolve()),
        "+permissive",
        "+dramsim",
        f"+dramsim_ini_dir={dram_ini}",
        f"+loadmem={elf}",
        f"+max-cycles={max_cycles}",
        "+verbose",
        "+permissive-off",
        str(elf),
    ]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=work,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env=_sim_env(),
        )
    except subprocess.TimeoutExpired:
        return False, "timeout", 120.0
    wall = time.perf_counter() - t0
    log = (proc.stdout or "") + (proc.stderr or "")
    log_path.write_text(log)
    failed = "*** FAILED ***" in log or "Assertion failed" in log or "%Error:" in log
    passed = (not failed) and ("*** PASSED ***" in log or (proc.returncode == 0 and "Verilog $finish" in log))
    return passed, log[-4000:], wall


def _pick_ops(
    action: str,
    knobs: KnobState,
    targets: list[str],
    rng: random.Random,
    n: int,
    model: FrozenModel,
) -> list[str]:
    by_id = model.by_id()
    named = []
    for pid in targets:
        pt = by_id.get(pid)
        if pt and pt.name.startswith("op.") and pt.bucket != "unreachable":
            named.append(pt.name[3:].upper())
    if action == "torture":
        pool = list(SAFE_I)
    elif action == "directed":
        pool = named or list(M_EXT + MEM + ["FENCE"])
    else:
        pool = list(SAFE_I)
        if knobs.unit_boost.get("lsu", 1.0) >= 1.4:
            pool += MEM * 3 + ["FENCE"] * 2
        else:
            pool += MEM + ["FENCE"]
        if knobs.unit_boost.get("bpred", 1.0) >= 1.4:
            pool += BR * 3
        else:
            pool += BR
        if knobs.unit_boost.get("issue_q", 1.0) >= 1.4:
            pool += M_EXT * 3
        else:
            pool += M_EXT
        pool += named
    return [rng.choice(pool) for _ in range(n)]


def run_crv_batch(
    model: FrozenModel,
    alloc: Allocation,
    knobs: KnobState,
    rng: random.Random,
    work_dir: Path,
) -> tuple[BatchResult, KnobState]:
    work_dir.mkdir(parents=True, exist_ok=True)
    if not find_mediumboom_sim():
        hours = max(0.05, min(alloc.hour_budget, 2.0))
        return (
            BatchResult(
                alloc.action,
                hours,
                [],
                [],
                "boom: MediumBOOM simulator missing — hours charged, 0 hits",
            ),
            knobs,
        )
    n = 48 if alloc.action == "riscv_dv" else 24
    if alloc.action == "directed":
        n = 16
    ops = _pick_ops(alloc.action, knobs, alloc.target_pids, rng, n, model)
    asm = work_dir / "t.S"
    elf = work_dir / "t.elf"
    # Torture / riscv_dv / directed all use UCB riscv-torture when the suite
    # exists. Mini assembler is fallback only (CI without the generator).
    suite = find_torture_suite()
    used_torture = False
    asm_text = ""
    artifact_path: Path | None = None
    if alloc.action == "directed":
        candidate = work_dir.parent / "t.S"
        if not candidate.is_file():
            return (
                BatchResult("directed", 0.0, [], [], f"no CHIA directed artifact at {candidate}"),
                knobs,
            )
        artifact_path = candidate
        asm_text = candidate.read_text(errors="replace")
        ops = _directed_ops_from_artifact(asm_text)
        if not ops:
            return (
                BatchResult("directed", 0.0, [], [], f"directed artifact has no supported ops: {candidate}"),
                knobs,
            )
        credits = write_rv64_program(asm, ops, rng)
        try:
            compile_rv64(asm, elf)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            hours = max(0.05, min(alloc.hour_budget, 0.2))
            return BatchResult("directed", hours, [], [], f"directed SoC compile failed: {e}"), knobs
    elif suite is not None:
        src = _pick_torture_source(suite, alloc.action, rng)
        asm_text = src.read_text(errors="replace")
        # Enh 3: UCB torture never emits fence, so op.fence / soc.class.fence
        # stay open under carpet CRV. Directed injects one legal fence.
        if alloc.action == "directed" and not re.search(r"\bfence\b", asm_text, re.I):
            asm_text = asm_text.rstrip() + "\n\tfence iorw, iorw\n"
        asm.write_text(asm_text)
        credits = _credits_from_asm(asm_text)
        ops = [c[3:].upper() for c in credits if c.startswith("op.")]
        try:
            compile_torture_asm(asm, elf)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            hours = max(0.05, min(alloc.hour_budget, 0.2))
            return BatchResult(alloc.action, hours, [], [], f"riscv-torture compile failed: {e}"), knobs
        used_torture = True
    else:
        credits = write_rv64_program(asm, ops, rng)
        asm_text = asm.read_text(errors="replace")
        try:
            compile_rv64(asm, elf)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            hours = max(0.05, min(alloc.hour_budget, 0.2))
            return BatchResult(alloc.action, hours, [], [], f"rv64 compile failed: {e}"), knobs
    max_cycles = 10_000_000 if used_torture else 500_000
    passed, log_tail, wall = run_mediumboom_elf(elf, work_dir, max_cycles=max_cycles)
    log_full = ""
    log_path = work_dir / "sim.log"
    if log_path.is_file():
        log_full = log_path.read_text(errors="replace")
    hits: list[str] = []
    cov_wall = 0.0
    observed = False
    cov_log = ""
    # Default: SoC-scoped credits on PASS. Opt-in LSU dump is a separate slice.
    use_lsu = os.environ.get("BOOM_USE_LSU_COV", "0") == "1"
    if passed:
        credits |= _soc_run_credits(log_full or log_tail)
        by_name = {p.name: p.pid for p in model.points}
        for name in credits:
            if name in by_name:
                hits.append(by_name[name])
    if use_lsu:
        from .lsu_driver import run_lsu_cov, stim_from_asm, stim_from_ops
        from .verilator_backend import hits_from_coverage

        cov = work_dir / "coverage.dat"
        stim = stim_from_asm(asm_text) if used_torture else stim_from_ops(ops)
        cov_log, cov_wall = run_lsu_cov(stim, cov)
        observed = cov.exists() and cov.stat().st_size > 20
        if passed and observed:
            # Replace opcode credits with LSU dump hits when in LSU-slice mode.
            hits = hits_from_coverage(cov, model)
    else:
        stim = []
    hours = max((wall + cov_wall) / 3600.0, 1e-6)
    if alloc.action == "riscv_dv":
        knobs.freshness *= 0.45
    note = (
        f"mediumboom SoC {'PASS' if passed else 'FAIL'} wall_s={wall:.3f} "
        f"score=soc_bins lsu_cov={'on' if use_lsu else 'off'} "
        f"lsu_cov_s={cov_wall:.3f} observed={int(observed)} "
        f"credits={len(credits)} hits={len(hits)} loadmem=1 "
        f"crv={'riscv-torture' if used_torture else 'mini_asm'} "
        f"directed_artifact={artifact_path if artifact_path else '-'} "
        f"directed_ops={','.join(ops) if artifact_path else '-'} "
        f"action={alloc.action} "
        f"stim={','.join(stim[:8]) if stim else '-'} "
        f"log={log_tail[-160:]!r} cov={cov_log[-80:]!r}"
    )
    return BatchResult(alloc.action, hours, hits, [], note), knobs


def run_sby_depth(
    dut: GenDut,
    task: str,
    work: Path,
    depth: int,
    timeout_s: int,
) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    work = work.resolve()
    sby = shutil.which("sby")
    if not sby:
        return {"status": "skipped", "wall_s": 0.0, "log": "sby not on PATH", "depth": depth, "task": task}
    recipe = work / f"mediumboom_{task}_d{depth}.sby"
    write_sby(dut, recipe, depth)
    out = work / f"{task}_d{depth}"
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [sby, "-f", "-d", str(out), str(recipe), task],
            cwd=work,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        status = "error"
        log = (proc.stdout or "") + (proc.stderr or "")
        lf = out / "logfile.txt"
        if lf.exists():
            log = lf.read_text(errors="replace") + "\n" + log
        if "DONE (PASS" in log:
            status = "pass"
        elif "DONE (FAIL" in log:
            status = "fail"
        elif "DONE (UNKNOWN" in log:
            status = "unknown"
        elif proc.returncode != 0:
            status = "error"
        wall = time.perf_counter() - t0
        return {"status": status, "wall_s": wall, "log": log[-8000:], "depth": depth, "task": task, "rc": proc.returncode}
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "wall_s": float(timeout_s),
            "log": f"timeout after {timeout_s}s",
            "depth": depth,
            "task": task,
        }


def time_formal(work: Path, depths: tuple[int, ...] = (4, 8, 16), timeout_s: int = 180) -> dict:
    dut = discover_dut()
    rows = []
    for depth in depths:
        for task in ("cover", "prove"):
            rows.append(run_sby_depth(dut, task, work, depth, timeout_s))
            if rows[-1]["status"] == "timeout":
                break
        if rows and rows[-1]["status"] == "timeout":
            break
    payload = {
        "dut": {
            "module": dut.module,
            "sv": str(dut.sv),
            "collateral": str(dut.collateral),
            "n_ports": len(dut.ports),
            "clk": dut.clk,
            "rst": dut.rst,
        },
        "depths": list(depths),
        "timeout_s": timeout_s,
        "runs": [
            {k: v for k, v in r.items() if k != "log"} | {"log_tail": r.get("log", "")[-400:]}
            for r in rows
        ],
    }
    return payload


def _parse_covers(log: str) -> tuple[set[str], set[str]]:
    from .sby_backend import parse_sby_log

    return parse_sby_log(log)


def run_formal_oracle(work: Path, depth: int = 8, timeout_s: int = 120) -> tuple[list[str], list[str], str, float]:
    """Cover BMC on MediumBOOM formal scope (ChipTop / BoomTile / LSU).

    Default ``BOOM_FORMAL_SCOPE=chiptop`` runs SymbiYosys on generated ChipTop
    with memory/monitor stubs. Set ``tile`` or ``lsu`` to shrink. SoC CRV
    opcode bins are still not in the formal recipe — only the eight named
    cover properties (mapped to frozen ``cp_b000``… IDs).
    """
    scope = os.environ.get("BOOM_FORMAL_SCOPE", "chiptop").strip().lower()
    if scope in ("chiptop", "chip", "soc", "tile", "boomtile"):
        from .soc_formal import run_soc_formal

        # Posedge covers need depth >= 2; cycle covers need >= 7. Honor env override.
        soc_depth = int(os.environ.get("BOOM_FORMAL_DEPTH", str(max(depth, 8))))
        soc_timeout = int(os.environ.get("BOOM_FORMAL_TIMEOUT_S", str(max(timeout_s, 600))))
        hit_names, ret_names, notes, wall = run_soc_formal(
            work, scope=scope, depth=soc_depth, timeout_s=soc_timeout
        )
        name_by_cover = {spec[5]: spec[0] for spec in CATALOG_SPEC}
        hits = [name_by_cover[n] for n in hit_names if n in name_by_cover]
        retired = [name_by_cover[n] for n in ret_names if n in name_by_cover]
        # Scope changes require explicit opt-in. A local LSU witness is not a
        # whole-core witness and must not silently replace a timed-out SoC job.
        if (
            not hits
            and not retired
            and os.environ.get("BOOM_FORMAL_ALLOW_LSU_FALLBACK", "0") == "1"
            and os.environ.get("BOOM_FORMAL_NO_LSU_FALLBACK", "0") != "1"
            and ("TIMEOUT" in notes.upper() or "ERROR" in notes.upper() or wall >= soc_timeout * 0.95)
        ):
            lsu_hits, lsu_ret, lsu_notes, lsu_wall = _run_lsu_formal_oracle(
                work / "lsu_fallback", depth=depth, timeout_s=timeout_s
            )
            return (
                lsu_hits,
                lsu_ret,
                f"{notes} | fallback_lsu: {lsu_notes}",
                wall + lsu_wall,
            )
        return hits, retired, notes, wall

    return _run_lsu_formal_oracle(work, depth=depth, timeout_s=timeout_s)


def _run_lsu_formal_oracle(
    work: Path, depth: int = 8, timeout_s: int = 120
) -> tuple[list[str], list[str], str, float]:
    """Legacy LSU-only SymbiYosys cover BMC (no SoC dispatch)."""
    dut = discover_dut()
    cover = run_sby_depth(dut, "cover", work, depth, timeout_s)
    prove: dict = {"status": "skipped", "wall_s": 0.0}
    if os.environ.get("BOOM_FORMAL_PROVE", "0") == "1":
        prove = run_sby_depth(dut, "prove", work, depth, timeout_s)
    reached, unreached = _parse_covers(cover.get("log", ""))
    hits, retired = [], []
    name_by_cover = {spec[5]: spec[0] for spec in CATALOG_SPEC}
    for name, pid in name_by_cover.items():
        bucket = next(s[4] for s in CATALOG_SPEC if s[0] == pid)
        if name in reached and bucket != "unreachable" and cover.get("status") in {"pass", "fail"}:
            hits.append(pid)
        # A cover-mode non-hit (or a catalog difficulty label) cannot retire
        # a point. Even a separate prove PASS must prove THIS bin's negation,
        # with matching scope/assumptions, before exclusion can be reviewed.
    notes = (
        f"module={dut.module} scope=lsu cover={cover['status']} {cover['wall_s']:.2f}s "
        f"prove={prove['status']} {prove['wall_s']:.2f}s depth={depth} "
        f"reached={sorted(reached)} unreached={sorted(unreached)} "
        f"engines=formal_sva;line_toggle=need_crv "
        f"crv=mediumboom_soc"
    )
    return hits, retired, notes, cover["wall_s"] + float(prove.get("wall_s") or 0.0)


def simulate_boom_batch(
    model: FrozenModel,
    covered: set[str],
    retired: set[str],
    alloc: Allocation,
    knobs: KnobState,
    rng: random.Random,
    work_dir: Path,
) -> tuple[BatchResult, KnobState]:
    if alloc.action == "constraint_tuning":
        open_pts = [p for p in model.points if p.pid not in covered | retired]
        for u in knobs.unit_boost:
            knobs.unit_boost[u] = 1.0
        for p in open_pts:
            knobs.unit_boost[p.unit] = max(knobs.unit_boost.get(p.unit, 1.0), 1.8)
        knobs.freshness = 1.0
        hours = 0.5 / 3600.0
        return BatchResult("constraint_tuning", hours, [], [], "reweight toward uncovered units"), knobs

    if alloc.action == "formal":
        open_sva = [
            p
            for p in model.points
            if p.kind == "sva"
            and p.name.startswith("mediumboom.lsu.")
            and p.pid not in covered
            and p.pid not in retired
        ]
        if not open_sva:
            # SoC CRV leftovers cannot be closed by SymbiYosys on the LSU SVA
            # wrapper — divert to directed riscv-torture instead of a dry BMC.
            divert = Allocation(
                "directed",
                alloc.hour_budget,
                target_pids=list(alloc.target_pids),
                reason="formal→directed: no open LSU SVA; SoC bins need CRV",
            )
            result, knobs = run_crv_batch(model, divert, knobs, rng, work_dir / "directed")
            result.notes = f"diverted_from_formal; {result.notes}"
            return result, knobs
        hits, rets, notes, wall = run_formal_oracle(work_dir / "sby")
        hours = max(wall / 3600.0, 1e-6)
        return (
            BatchResult("formal", hours, hits, rets, f"mediumboom sby wall_s={wall:.3f} {notes}"),
            knobs,
        )

    # torture / riscv_dv / directed → UCB riscv-torture on MediumBOOM SoC
    return run_crv_batch(model, alloc, knobs, rng, work_dir / alloc.action)


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Time SymbiYosys on generated MediumBOOM LSU")
    p.add_argument("--out", type=Path, default=PKG / "artifacts_mediumboom")
    p.add_argument("--depths", default="4,8,16")
    p.add_argument("--timeout", type=int, default=180)
    args = p.parse_args()
    depths = tuple(int(x) for x in args.depths.split(",") if x.strip())
    args.out.mkdir(parents=True, exist_ok=True)
    payload = time_formal(args.out / "formal_work", depths, args.timeout)
    (args.out / "formal_timing.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps({k: payload[k] for k in ("dut", "depths", "timeout_s")}, indent=2))
    for r in payload["runs"]:
        print(f"  {r['task']:6} d={r['depth']:<3} {r['status']:8} {r['wall_s']:.2f}s")


if __name__ == "__main__":
    main()
