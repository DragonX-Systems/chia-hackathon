"""MediumBOOM generated Load/Store Unit driver for coverage and formal.

The unconstrained 502-port vacuum fires Chisel protocol asserts and only
reaches trivial covers. This writer keeps every generated port, then drives a
short legal-ish dispatch → execute → data-memory ready sequence.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from .coverage_model import CoverPoint
from .verilator_backend import parse_coverage_dat

HERE = Path(__file__).resolve().parent
TB = HERE / "testbenches" / "boom_cov"


def _w(width: str) -> str:
    return f"{width} " if width else ""


def write_if_changed(path: Path, text: str) -> bool:
    """Write only when bytes change so Verilator/make keep incremental .o files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text() == text:
        return False
    path.write_text(text)
    return True


def write_cov_tb(dut, dest: Path) -> Path:
    """Port-stable DUT wrapper: clk/rst/stim_* only. Regenerating identical text
    does not bump mtime (see ``write_if_changed``)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    decls = []
    conns = []
    in_names = []
    for p, direction, width in zip(dut.ports, dut.dirs, getattr(dut, "widths", ()) or ("",) * len(dut.ports)):
        decls.append(f"  logic {_w(width)}{p};")
        conns.append(f"    .{p}({p})")
        if direction == "input" and p not in {dut.clk, dut.rst}:
            in_names.append(p)

    def has(name: str) -> bool:
        return name in dut.ports

    in_set = set(in_names)

    def has_input(name: str) -> bool:
        return name in in_set

    zeros = "\n".join(f"      {n} = '0;" for n in in_names)
    return write_if_changed(
        dest,
        "// Generated driven LSU DUT wrapper. Ports are frozen; stim lives in C++.\n"
        "module lsu_cov_tb(\n"
        "  input clk,\n"
        "  input rst,\n"
        "  input [1:0] stim_kind,\n"
        "  input stim_pulse\n"
        ");\n"
        + "\n".join(decls)
        + "\n"
        f"  {dut.module} dut (\n"
        + ",\n".join(conns)
        + "\n  );\n\n"
        "  logic [7:0] phase;\n"
        "  logic [1:0] kind_hold;\n"
        "  always @(posedge clk) begin\n"
        f"    if ({dut.rst}) begin\n"
        "      phase <= 0;\n"
        "      kind_hold <= 0;\n"
        "    end else if (stim_pulse) begin\n"
        "      phase <= 1;\n"
        "      kind_hold <= stim_kind;\n"
        "    end else if (phase != 0 && phase < 8) begin\n"
        "      phase <= phase + 1;\n"
        "    end else begin\n"
        "      phase <= 0;\n"
        "    end\n"
        "  end\n\n"
        f"  assign {dut.clk} = clk;\n"
        f"  assign {dut.rst} = rst;\n"
        "  always @(*) begin\n"
        + zeros
        + "\n"
        + _drive_body(has_input)
        + "  end\n\n"
        + _user_covers(has, dut)
        + "endmodule\n",
    )


def _drive_body(has) -> str:
    # `has` is the DUT-input set. Never assign LSU outputs such as
    # io_hellacache_req_ready / io_dmem_release_ready / io_core_fp_stdata_ready;
    # Yosys rejects an output port tied to a constant.
    lines = []
    if has("io_ptw_req_ready"):
        lines.append("    io_ptw_req_ready = 1'b1;")
    if has("io_dmem_req_ready"):
        lines.append("    io_dmem_req_ready = 1'b1;")
    lines.append("    if (phase == 8'd1 || phase == 8'd2) begin")
    if has("io_core_dis_uops_0_valid"):
        lines.append("      io_core_dis_uops_0_valid = 1'b1;")
    if has("io_core_dis_uops_0_bits_uses_ldq"):
        lines.append("      io_core_dis_uops_0_bits_uses_ldq = (kind_hold == 2'd1);")
    if has("io_core_dis_uops_0_bits_uses_stq"):
        lines.append("      io_core_dis_uops_0_bits_uses_stq = (kind_hold == 2'd2);")
    if has("io_core_dis_uops_0_bits_ctrl_is_load"):
        lines.append("      io_core_dis_uops_0_bits_ctrl_is_load = (kind_hold == 2'd1);")
    if has("io_core_dis_uops_0_bits_ctrl_is_sta"):
        lines.append("      io_core_dis_uops_0_bits_ctrl_is_sta = (kind_hold == 2'd2);")
    if has("io_core_dis_uops_0_bits_is_fence"):
        lines.append("      io_core_dis_uops_0_bits_is_fence = (kind_hold == 2'd3);")
    if has("io_core_dis_uops_0_bits_mem_cmd"):
        lines.append("      io_core_dis_uops_0_bits_mem_cmd = (kind_hold == 2'd2) ? 5'd1 : 5'd0;")
    if has("io_core_dis_uops_0_bits_mem_size"):
        lines.append("      io_core_dis_uops_0_bits_mem_size = 2'd3;")
    lines.append("    end")
    lines.append("    if (phase == 8'd3 || phase == 8'd4) begin")
    if has("io_core_exe_0_req_valid"):
        lines.append("      io_core_exe_0_req_valid = (kind_hold != 2'd0);")
    if has("io_core_exe_0_req_bits_uop_uses_ldq"):
        lines.append("      io_core_exe_0_req_bits_uop_uses_ldq = (kind_hold == 2'd1);")
    if has("io_core_exe_0_req_bits_uop_uses_stq"):
        lines.append("      io_core_exe_0_req_bits_uop_uses_stq = (kind_hold == 2'd2);")
    if has("io_core_exe_0_req_bits_uop_ctrl_is_load"):
        lines.append("      io_core_exe_0_req_bits_uop_ctrl_is_load = (kind_hold == 2'd1);")
    if has("io_core_exe_0_req_bits_uop_ctrl_is_sta"):
        lines.append("      io_core_exe_0_req_bits_uop_ctrl_is_sta = (kind_hold == 2'd2);")
    if has("io_core_exe_0_req_bits_uop_is_fence"):
        lines.append("      io_core_exe_0_req_bits_uop_is_fence = (kind_hold == 2'd3);")
    if has("io_core_exe_0_req_bits_uop_mem_cmd"):
        lines.append("      io_core_exe_0_req_bits_uop_mem_cmd = (kind_hold == 2'd2) ? 5'd1 : 5'd0;")
    if has("io_core_exe_0_req_bits_uop_mem_size"):
        lines.append("      io_core_exe_0_req_bits_uop_mem_size = 2'd3;")
    if has("io_core_exe_0_req_bits_addr"):
        lines.append("      io_core_exe_0_req_bits_addr = 40'h80000000;")
    if has("io_core_fence_dmem"):
        lines.append("      io_core_fence_dmem = (kind_hold == 2'd3);")
    lines.append("    end")
    if has("io_dmem_resp_0_valid"):
        lines.append("    if (phase == 8'd5 || phase == 8'd6) begin")
        lines.append("      io_dmem_resp_0_valid = 1'b1;")
        if has("io_dmem_resp_0_bits_uop_uses_ldq"):
            lines.append("      io_dmem_resp_0_bits_uop_uses_ldq = (kind_hold == 2'd1);")
        if has("io_dmem_resp_0_bits_uop_uses_stq"):
            lines.append("      io_dmem_resp_0_bits_uop_uses_stq = (kind_hold == 2'd2);")
        lines.append("    end")
    if has("io_core_commit_valids_0"):
        lines.append("    if (phase == 8'd7) io_core_commit_valids_0 = 1'b1;")
    if has("io_core_commit_uops_0_uses_ldq"):
        lines.append("    if (phase == 8'd7) io_core_commit_uops_0_uses_ldq = (kind_hold == 2'd1);")
    if has("io_core_commit_uops_0_uses_stq"):
        lines.append("    if (phase == 8'd7) io_core_commit_uops_0_uses_stq = (kind_hold == 2'd2);")
    return "\n".join(lines) + "\n"


def _user_covers(has, dut) -> str:
    clk = dut.clk
    rst = dut.rst
    bits = []
    if has("io_core_dis_uops_0_valid"):
        bits.append(("dis_fire", "io_core_dis_uops_0_valid"))
    if has("io_core_exe_0_req_valid"):
        bits.append(("exe_req", "io_core_exe_0_req_valid"))
    if has("io_dmem_req_valid"):
        bits.append(("dmem_req", "io_dmem_req_valid"))
    if has("io_core_exe_0_req_bits_uop_ctrl_is_load"):
        bits.append(("exe_load", "io_core_exe_0_req_valid && io_core_exe_0_req_bits_uop_ctrl_is_load"))
    if has("io_core_exe_0_req_bits_uop_ctrl_is_sta"):
        bits.append(("exe_store", "io_core_exe_0_req_valid && io_core_exe_0_req_bits_uop_ctrl_is_sta"))
    extra = "\n".join(f"        {name}: cover ({expr});" for name, expr in bits)
    return (
        f"  always @(posedge {clk}) begin\n"
        f"    if (!{rst}) begin\n"
        f"      rst_rel: cover (!{rst});\n"
        f"{extra}\n"
        "      contra: cover (1'b1 && !1'b1);\n"
        "      never: cover (1'b0);\n"
        "    end\n"
        "  end\n"
    )


def write_driven_formal_wrapper(dut, dest: Path) -> Path:
    """Formal top: clock/reset only. Driver ties the other generated ports."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    decls = []
    conns = []
    in_names = []
    widths = getattr(dut, "widths", ()) or ("",) * len(dut.ports)
    for p, direction, width in zip(dut.ports, dut.dirs, widths):
        conns.append(f"        .{p}({p})")
        if p in {dut.clk, dut.rst}:
            continue
        decls.append(f"    logic {_w(width)}{p};")
        if direction == "input":
            in_names.append(p)
    in_set = set(in_names)
    has_input = in_set.__contains__
    zeros = "\n".join(f"        {n} = '0;" for n in in_names)
    extras = [p for p in dut.ports if p not in {dut.clk, dut.rst}][:2]
    a = extras[0] if extras else dut.clk
    b = extras[1] if len(extras) > 1 else a
    dest.write_text(
        "// Driven formal wrapper around generated MediumBOOM LSU.\n"
        "module mediumboom_lsu_cover (\n"
        f"    input logic {dut.clk},\n"
        f"    input logic {dut.rst}\n"
        ");\n"
        + "\n".join(decls)
        + "\n"
        f"    {dut.module} dut (\n"
        + ",\n".join(conns)
        + "\n    );\n"
        "    logic [7:0] cyc;\n"
        f"    always @(posedge {dut.clk}) begin\n"
        f"        if ({dut.rst}) cyc <= 0;\n"
        "        else if (cyc != 8'hff) cyc <= cyc + 1;\n"
        "    end\n"
        "    always @(*) begin\n"
        + zeros
        + "\n"
        + _formal_drive(has_input)
        + "    end\n"
        f"    always @(posedge {dut.clk}) begin\n"
        f"        if (!{dut.rst}) begin\n"
        f"            a: cover ({a});\n"
        f"            b: cover ({b});\n"
        f"            both: cover ({a} && {b});\n"
        f"            rst_rel: cover (!{dut.rst});\n"
        f"            contra: cover ({a} && !{a});\n"
        "            never: cover (1'b0);\n"
        "            dis_fire: cover (cyc == 8'd4);\n"
        "            exe_req: cover (cyc == 8'd6);\n"
        "        end\n"
        "    end\n"
        "endmodule\n"
    )
    return dest


def _formal_drive(has) -> str:
    # `has` is the DUT-input set. Do not drive LSU outputs; Yosys errors on
    # `io_hellacache_req_ready` (an output) tied to 1'b1.
    lines = []
    if has("io_ptw_req_ready"):
        lines.append("        io_ptw_req_ready = 1'b1;")
    if has("io_dmem_req_ready"):
        lines.append("        io_dmem_req_ready = 1'b1;")
    if has("io_core_dis_uops_0_valid"):
        lines.append("        io_core_dis_uops_0_valid = (cyc >= 8'd3 && cyc <= 8'd4);")
    if has("io_core_dis_uops_0_bits_uses_ldq"):
        lines.append("        io_core_dis_uops_0_bits_uses_ldq = (cyc >= 8'd3 && cyc <= 8'd4);")
    if has("io_core_exe_0_req_valid"):
        lines.append("        io_core_exe_0_req_valid = (cyc >= 8'd5 && cyc <= 8'd6);")
    if has("io_core_exe_0_req_bits_uop_uses_ldq"):
        lines.append("        io_core_exe_0_req_bits_uop_uses_ldq = (cyc >= 8'd5 && cyc <= 8'd6);")
    if has("io_core_exe_0_req_bits_addr"):
        lines.append("        io_core_exe_0_req_bits_addr = 40'h80000000;")
    if has("io_dmem_resp_0_valid"):
        lines.append("        io_dmem_resp_0_valid = (cyc >= 8'd7 && cyc <= 8'd8);")
    return "\n".join(lines) + "\n"


def lsu_cov_sim() -> Path:
    return TB / "obj_dir" / "Vlsu_cov_tb"


def ensure_lsu_cov_sim(*, build: bool | None = None) -> Path | None:
    sim = lsu_cov_sim()
    if build is None:
        build = os.environ.get("BOOM_BUILD_LSU_COV", "0") == "1"
    # Incremental make under DYLD from the SoC tree can relink sim_main.o as
    # x86_64 against an arm64 library. Reuse a working binary unless forced.
    if sim.exists() and os.access(sim, os.X_OK) and os.environ.get("BOOM_REBUILD_LSU_COV") != "1":
        return sim
    if sim.exists() and not build:
        return sim
    if not build or shutil.which("verilator") is None:
        return sim if sim.exists() else None
    script = TB / "build.sh"
    try:
        subprocess.run(
            ["bash", str(script), "all"],
            check=True,
            timeout=int(os.environ.get("LSU_COV_BUILD_S", "900")),
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return sim if sim.exists() else None
    return sim if sim.exists() else None


def run_lsu_cov(stim: list[str], cov: Path, max_cycles: int = 256) -> tuple[str, float]:
    sim = ensure_lsu_cov_sim()
    if not sim:
        return "lsu_cov sim missing", 0.0
    cov.parent.mkdir(parents=True, exist_ok=True)
    cov = cov.resolve()
    spec = ",".join(stim) if stim else "idle"
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [str(sim.resolve()), f"+cov={cov}", f"+stim={spec}", f"+max={max_cycles}"],
            cwd=str(cov.parent),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "lsu_cov timeout", 60.0
    wall = time.perf_counter() - t0
    log = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return log, wall


def coverage_points_from_dump(cov: Path, start_i: int = 0) -> list[CoverPoint]:
    keep = {"LSU.sv", "lsu_cov_tb.sv", "NBDTLB.sv", "ForwardingAgeLogic.sv", "PMPChecker_s3.sv"}
    rows = [r for r in parse_coverage_dat(cov) if r["base"] in keep or "lsu" in r["base"].lower()]
    lines = [r for r in rows if r["kind"] == "line"]
    users = [r for r in rows if r["kind"] == "sva"]
    toggles = [r for r in rows if r["kind"] == "toggle"]
    if len(lines) > 220:
        step = max(1, len(lines) // 220)
        lines = lines[::step][:220]
    if len(toggles) > 80:
        step = max(1, len(toggles) // 80)
        toggles = toggles[::step][:80]
    points: list[CoverPoint] = []
    seen: set[str] = set()
    i = start_i
    for r in lines + toggles + users:
        if r["name"] in seen:
            continue
        seen.add(r["name"])
        unit = "lsu"
        if "tlb" in r["base"].lower() or "ptw" in r["name"].lower():
            unit = "bpred"
        bucket = "easy" if r["kind"] == "line" else ("medium" if r["kind"] == "toggle" else "hard")
        if r["count"] == 0:
            bucket = "hard"
        points.append(
            CoverPoint(
                pid=f"cp_b{i:03d}",
                kind=r["kind"] if r["kind"] in ("line", "toggle", "sva") else "line",  # type: ignore[arg-type]
                unit=unit,  # type: ignore[arg-type]
                bucket=bucket,  # type: ignore[arg-type]
                name=r["name"],
            )
        )
        i += 1
    return points


def stim_from_ops(ops: list[str]) -> list[str]:
    out: list[str] = []
    for op in ops:
        n = op.upper()
        if n in {"LD", "LW", "LB", "LH", "LWU", "LD"}:
            out.append("load")
        elif n in {"SD", "SW", "SB", "SH"}:
            out.append("store")
        elif n in {"FENCE", "FENCEI", "FENCE.I"}:
            out.append("fence")
        else:
            out.append("idle")
    return out[:48] or ["idle"]


def stim_from_asm(text: str, cap: int = 48) -> list[str]:
    """Map a full RISC-V torture .S listing into Load/Store Unit stim kinds."""
    out: list[str] = []
    for tok in text.lower().split():
        if tok in {"ld", "lw", "lb", "lh", "lwu", "lbu", "lhu"}:
            out.append("load")
        elif tok in {"sd", "sw", "sb", "sh"}:
            out.append("store")
        elif tok.startswith("fence"):
            out.append("fence")
        if len(out) >= cap:
            break
    # Prefer a dense mem/fence stream; pad with idle only if empty.
    return out or ["idle"]
