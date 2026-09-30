"""SoC-scoped MediumBOOM formal adapter (ChipTop/BoomTile).

Firtool SystemVerilog hits Yosys limits (assignment patterns in TLMonitor*,
external SRAM ``*_ext`` cells). This recipe:

1. Builds the transitive ``.sv``/``.v`` closure from a chosen top.
2. Replaces TLMonitor / assignment-pattern modules with empty port stubs.
3. Replaces missing ``*_ext`` SRAMs with tiny behavioral memories.
4. Wraps the top with clock/reset + named cover properties (same 8 names as
   the LSU recipe so the frozen meter IDs stay stable).

Default top is ``ChipTop`` (entire generated SoC). Override with
``BOOM_FORMAL_SCOPE=chiptop|tile|lsu``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from .boom_backend import (
    MODULE_RE,
    PORT_RE,
    GenDut,
    _clk_rst,
    find_gen_collateral,
    parse_module,
)

SKIP_INST = {
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
    "generate",
    "genvar",
}

SCOPE_TO_SV = {
    "chiptop": "ChipTop.sv",
    "chip": "ChipTop.sv",
    "soc": "ChipTop.sv",
    "tile": "BoomTile.sv",
    "boomtile": "BoomTile.sv",
    "lsu": "LSU.sv",
}


def formal_scope() -> str:
    raw = os.environ.get("BOOM_FORMAL_SCOPE", "chiptop").strip().lower()
    return raw if raw in SCOPE_TO_SV or raw == "lsu" else "chiptop"


def pick_formal_sv(collateral: Path, scope: str | None = None) -> Path:
    scope = (scope or formal_scope()).lower()
    name = SCOPE_TO_SV.get(scope, "ChipTop.sv")
    path = collateral / name
    if not path.exists():
        raise FileNotFoundError(f"Formal scope {scope}: missing {path}")
    return path


def discover_soc_dut(scope: str | None = None) -> GenDut:
    coll = find_gen_collateral()
    sv = pick_formal_sv(coll, scope)
    module, ports, dirs, widths = parse_module(sv)
    clk, rst = _clk_rst(ports)
    return GenDut(coll, sv, module, tuple(ports), tuple(dirs), clk, rst, tuple(widths))


def module_closure(top_sv: Path, collateral: Path) -> tuple[set[Path], set[str]]:
    needed: set[Path] = {top_sv}
    frontier = [top_sv]
    missing: set[str] = set()
    # Match `Foo bar (` and `Foo #(.P(1)) bar (`
    inst_re = re.compile(
        r"^\s*([A-Za-z_]\w*)\s*(?:#\s*\([^;]*?\))?\s+\w+\s*\(",
        re.M | re.S,
    )
    while frontier:
        text = frontier.pop().read_text(errors="replace")
        for name in set(inst_re.findall(text)):
            if name in SKIP_INST:
                continue
            found = None
            for ext in (".sv", ".v"):
                p = collateral / f"{name}{ext}"
                if p.exists():
                    found = p
                    break
            # Chipyard sometimes uniquifies: plusarg_reader_TestHarness_UNIQUIFIED.v
            if found is None:
                alts = sorted(collateral.glob(f"{name}*.v")) + sorted(collateral.glob(f"{name}*.sv"))
                if alts:
                    found = alts[0]
            if found is None:
                missing.add(name)
            elif found not in needed:
                needed.add(found)
                frontier.append(found)
    return needed, missing


def _needs_stub_file(path: Path) -> bool:
    if path.name.startswith("TLMonitor"):
        return True
    return bool(re.search(r"'\{", path.read_text(errors="replace")))


def stub_from_file(sv: Path) -> str:
    """Empty module with matching ports (not (* blackbox *) — formal rejects those)."""
    text = sv.read_text(errors="replace")
    mods = MODULE_RE.findall(text)
    if not mods:
        return ""
    module = mods[0]
    header = text.split(f"module {module}", 1)[-1].split(";", 1)[0]
    ports = PORT_RE.findall(header) or PORT_RE.findall(text.split(f"module {module}", 1)[-1][:40000])
    lines = []
    for d, w, n in ports:
        ww = (w or "").strip()
        lines.append(f"  {d} {ww} {n}" if ww else f"  {d} {n}")
    body = ",\n".join(lines)
    outs = [n for d, _w, n in ports if d == "output"]
    assigns = "\n".join(f"  assign {n} = '0;" for n in outs)
    return f"// stub (firtool/Yosys-incompatible body)\nmodule {module}(\n{body}\n);\n{assigns}\nendmodule\n"


def stub_ext_memory(name: str, parent_blob: str) -> str:
    if name == "plusarg_reader":
        # Rocket Chip's generated TL monitors parameterize this utility with
        # DEFAULT/FORMAT/WIDTH. The monitor modules themselves are stubbed for
        # this SoC cone, but the hierarchy scanner still sees these instances.
        return (
            "module plusarg_reader #(parameter DEFAULT=0, parameter FORMAT=\"\", "
            "parameter WIDTH=32) (output [WIDTH-1:0] out);\n"
            "  assign out = DEFAULT;\n"
            "endmodule\n"
        )
    m = re.search(rf"{re.escape(name)}\s+\w+\s*\((.*?)\);", parent_blob, re.S)
    if not m:
        return f"module {name}();\nendmodule\n"
    conns = re.findall(r"\.(\w+)\s*\(", m.group(1))
    # Typical Chipyard SRAM ext: RW0_addr/en/clk/wmode/wdata/rdata
    has = set(conns)
    ports = []
    for c in conns:
        if c.endswith("_rdata") or c.endswith("_data_out"):
            ports.append(f"  output [1023:0] {c}")
        elif c.endswith("_wdata") or c.endswith("_data_in"):
            ports.append(f"  input  [1023:0] {c}")
        elif c.endswith("_addr"):
            ports.append(f"  input  [31:0] {c}")
        else:
            ports.append(f"  input  {c}")
    body = ",\n".join(ports)
    rdata = next((c for c in conns if c.endswith("_rdata")), None)
    addr = next((c for c in conns if c.endswith("_addr")), None)
    clk = next((c for c in conns if c.endswith("_clk") or c == "clk" or c == "clock"), None)
    en = next((c for c in conns if c.endswith("_en")), None)
    wmode = next((c for c in conns if "wmode" in c or c.endswith("_write")), None)
    wdata = next((c for c in conns if c.endswith("_wdata")), None)
    lines = [f"module {name}(\n{body}\n);", "  // behavioral SRAM stub for SymbiYosys"]
    if rdata and addr:
        lines.append("  reg [1023:0] mem [0:4095];")
        lines.append(f"  assign {rdata} = mem[{addr}[11:0]];")
        if clk and en and wmode and wdata:
            lines.append(f"  always @(posedge {clk}) begin")
            lines.append(f"    if ({en} && {wmode}) mem[{addr}[11:0]] <= {wdata};")
            lines.append("  end")
    elif rdata:
        lines.append(f"  assign {rdata} = '0;")
    lines.append("endmodule")
    return "\n".join(lines) + "\n"


def write_soc_wrapper(dut: GenDut, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    decls: list[str] = []
    conns: list[str] = []
    ins: list[str] = []
    for p, direction, width in zip(dut.ports, dut.dirs, dut.widths):
        conns.append(f"        .{p}({p})")
        if p in {dut.clk, dut.rst}:
            continue
        ww = f"{width} " if width else ""
        decls.append(f"    logic {ww}{p};")
        if direction == "input":
            ins.append(p)
    # Prefer free-ish inputs for a/b (same meter names as LSU recipe).
    cover_ins = [p for p in ins if not p.endswith("_ready")][:2]
    if len(cover_ins) < 2:
        cover_ins = (ins + [dut.clk, dut.rst])[:2]
    a = cover_ins[0]
    b = cover_ins[1] if len(cover_ins) > 1 else a
    # Leave cover targets free; zero other inputs; hold TileLink ready high.
    zeros = "\n".join(f"        {n} = '0;" for n in ins if n not in {a, b})
    drive = "\n".join(f"        {n} = 1'b1;" for n in ins if n.endswith("_ready") and n not in {a, b})
    top = f"mediumboom_{dut.module.lower()}_cover"
    dest.write_text(
        f"// Formal wrapper for generated MediumBOOM {dut.module} (SoC-scoped).\n"
        f"module {top} (\n"
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
        + drive
        + "\n"
        f"        // cover targets left free for BMC\n"
        f"        // a={a} b={b}\n"
        "    end\n"
        f"    always @(posedge {dut.clk}) begin\n"
        f"        if (!{dut.rst}) begin\n"
        f"            a: cover (|{a});\n"
        f"            b: cover (|{b});\n"
        f"            both: cover ((|{a}) && (|{b}));\n"
        f"            rst_rel: cover (!{dut.rst});\n"
        f"            contra: cover ((|{a}) && !(|{a}));\n"
        "            never: cover (1'b0);\n"
        "            dis_fire: cover (cyc == 8'd4);\n"
        "            exe_req: cover (cyc == 8'd6);\n"
        "        end\n"
        "    end\n"
        "endmodule\n"
    )
    return dest


def write_soc_sby(dut: GenDut, dest: Path, depth: int) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    wrapper = dest.parent / f"mediumboom_{dut.module.lower()}_cover.sv"
    write_soc_wrapper(dut, wrapper)
    files, missing = module_closure(dut.sv, dut.collateral)
    blob = "\n".join(p.read_text(errors="replace") for p in files)
    stubs: list[str] = []
    keep: list[Path] = []
    for name in sorted(missing):
        stubs.append(stub_ext_memory(name, blob))
    for p in sorted(files):
        if _needs_stub_file(p):
            stubs.append(stub_from_file(p))
        else:
            keep.append(p)
    stub_path = dest.parent / "soc_formal_stubs.sv"
    stub_path.write_text("\n".join(stubs) + "\n")
    top = f"mediumboom_{dut.module.lower()}_cover"
    script = [
        f"read -formal -DSYNTHESIS -DPRINTF_COND=0 -DSTOP_COND=0 -I {dut.collateral} {stub_path.name}",
        f"read -formal -DSYNTHESIS -DPRINTF_COND=0 -DSTOP_COND=0 -I {dut.collateral} {wrapper.name}",
    ]
    for p in keep:
        script.append(
            f"read -formal -DSYNTHESIS -DPRINTF_COND=0 -DSTOP_COND=0 -I {dut.collateral} {p.name}"
        )
    script.append(f"prep -top {top}")
    # SBY resolves [files] relative to its working directory, not the caller's
    # repository root. Use absolute paths because run_soc_formal launches SBY
    # from the generated config directory.
    file_list = [str(p.resolve()) for p in [stub_path, wrapper, *keep]]
    dest.write_text(
        "[options]\n"
        "mode cover\n"
        f"depth {depth}\n"
        "\n[engines]\n"
        "smtbmc z3\n"
        "\n[script]\n"
        + "\n".join(script)
        + "\n\n[files]\n"
        + "\n".join(file_list)
        + "\n"
    )
    return dest


def run_soc_formal(
    work: Path,
    scope: str | None = None,
    depth: int = 8,
    timeout_s: int = 600,
) -> tuple[list[str], list[str], str, float]:
    """Return (hit_cover_names, retired_cover_names, notes, wall_s)."""
    work.mkdir(parents=True, exist_ok=True)
    scope = (scope or formal_scope()).lower()
    if scope == "lsu":
        # Call LSU recipe directly — do not re-enter run_formal_oracle (scope env).
        from .boom_backend import discover_dut, run_sby_depth, _parse_covers

        dut = discover_dut()
        cover = run_sby_depth(dut, "cover", work, depth, timeout_s)
        reached, unreached = _parse_covers(cover.get("log", ""))
        hit_names = sorted(n for n in reached if n not in {"contra", "never"})
        # A bounded cover miss is not an unreachability proof, even when an
        # old catalog labels a point "unreachable". Never prune from this run.
        ret_names: list[str] = []
        if cover.get("status") not in {"pass", "fail"}:
            hit_names = []
        notes = (
            f"soc_formal scope=lsu module={dut.module} depth={depth} "
            f"status={cover.get('status')} wall_s={cover.get('wall_s', 0):.3f} "
            f"reached={sorted(reached)} unreached={sorted(unreached)}"
        )
        return hit_names, ret_names, notes, float(cover.get("wall_s") or 0.0)

    dut = discover_soc_dut(scope)
    sby = work / f"cover_d{depth}" / "config.sby"
    sby.parent.mkdir(parents=True, exist_ok=True)
    write_soc_sby(dut, sby, depth)
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            ["sby", "-f", sby.name],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=str(sby.parent),
        )
        wall = time.perf_counter() - t0
    except subprocess.TimeoutExpired:
        return [], [], f"soc_formal scope={scope} TIMEOUT {timeout_s}s", float(timeout_s)
    except FileNotFoundError:
        return [], [], "sby not installed", 0.0

    log_path = sby.parent / "config" / "logfile.txt"
    if not log_path.exists():
        log_path = sby.parent / "logfile.txt"
    status_path = sby.parent / "config" / "status"
    if not status_path.exists():
        status_path = sby.parent / "status"
    log = log_path.read_text(errors="replace") if log_path.exists() else (proc.stdout or "") + (proc.stderr or "")
    status = status_path.read_text(errors="replace").strip() if status_path.exists() else f"rc={proc.returncode}"

    from .sby_backend import parse_sby_log

    reached, unreached = parse_sby_log(log)
    hit_names = sorted(reached - {"contra", "never"})
    ret_names: list[str] = []  # cover mode supplies no unbounded exclusion proof

    notes = (
        f"soc_formal scope={scope} module={dut.module} depth={depth} "
        f"status={status.replace(chr(10), ' ')[:80]} wall_s={wall:.3f} "
        f"reached={sorted(reached)} unreached={sorted(unreached)}"
    )
    native = status.split()[0].upper() if status.split() else "ERROR"
    if (native, proc.returncode) not in {("PASS", 0), ("FAIL", 2)}:
        return [], [], notes + " unqualified result", wall

    return hit_names, ret_names, notes, wall
