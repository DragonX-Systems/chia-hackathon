from pathlib import Path
from types import SimpleNamespace

import pytest

from adapters.mediumboom.boom_backend import (
    GenDut,
    find_gen_collateral,
    freeze_boom_model,
    parse_module,
    pick_lsu_sv,
    write_wrapper,
)
from adapters.mediumboom.lsu_driver import stim_from_ops, write_cov_tb, write_if_changed


def test_freeze_has_named_sva(monkeypatch):
    monkeypatch.setenv("BOOM_SKIP_LSU_COV", "1")
    monkeypatch.setenv("BOOM_BUILD_LSU_COV", "0")
    monkeypatch.delenv("BOOM_USE_LSU_COV", raising=False)
    model = freeze_boom_model()
    names = {p.name for p in model.points}
    assert "mediumboom.lsu.contradiction" in names
    assert "mediumboom.lsu.exe_req" in names
    assert any(p.bucket == "unreachable" for p in model.points)
    assert any(p.kind == "sva" for p in model.points)
    assert any(p.name.startswith("op.") for p in model.points)
    assert "soc.pass" in names
    assert "soc.class.mem_load" in names
    assert model.seed == 20260914
    # LSU dump bins must not appear in the default SoC yardstick.
    assert not any(":line:" in p.name for p in model.points)


def test_find_sim_path():
    from adapters.mediumboom.boom_backend import find_mediumboom_sim

    # May be absent in CI; just ensure the helper does not raise.
    find_mediumboom_sim()



def test_parse_module_ports(tmp_path):
    sv = tmp_path / "LSU.sv"
    sv.write_text(
        "module LSU(\n"
        "  input clock,\n"
        "  input reset,\n"
        "  input [39:0] io_core_exe_0_req_bits_addr,\n"
        "  input io_core_exe_0_req_valid,\n"
        "  output io_core_exe_0_iresp_valid\n"
        ");\n"
        "endmodule\n"
    )
    module, names, dirs, widths = parse_module(sv)
    assert module == "LSU"
    assert names[:2] == ["clock", "reset"]
    assert names[2] == "io_core_exe_0_req_bits_addr"
    assert dirs[3] == "input"
    assert dirs[4] == "output"
    assert widths[0] == ""
    assert widths[2] == "[39:0]"


def test_pick_lsu_prefers_named_file(tmp_path):
    (tmp_path / "ChipTop.sv").write_text("module ChipTop; endmodule\n")
    (tmp_path / "LSU.sv").write_text("module LSU; endmodule\n")
    (tmp_path / "BoomTile.sv").write_text("module BoomTile; endmodule\n")
    assert pick_lsu_sv(tmp_path).name == "LSU.sv"


def test_wrapper_uses_generated_ports(tmp_path):
    dut = GenDut(
        collateral=tmp_path,
        sv=tmp_path / "LSU.sv",
        module="LSU",
        ports=("clock", "reset", "io_v"),
        dirs=("input", "input", "input"),
        clk="clock",
        rst="reset",
    )
    dest = tmp_path / "wrap.sv"
    write_wrapper(dut, dest)
    text = dest.read_text()
    assert "LSU dut" in text
    assert "cover (io_v)" in text
    assert "contra: cover (io_v && !io_v)" in text


def test_formal_wrapper_does_not_drive_lsu_outputs(tmp_path):
    dut = GenDut(
        collateral=tmp_path,
        sv=tmp_path / "LSU.sv",
        module="LSU",
        ports=(
            "clock",
            "reset",
            "io_dmem_req_ready",
            "io_hellacache_req_ready",
            "io_dmem_release_ready",
            "io_core_fp_stdata_ready",
            "io_core_dis_uops_0_valid",
        ),
        dirs=("input", "input", "input", "output", "output", "output", "input"),
        clk="clock",
        rst="reset",
        widths=("", "", "", "", "", "", ""),
    )
    dest = tmp_path / "wrap.sv"
    write_wrapper(dut, dest)
    text = dest.read_text()
    assert "io_dmem_req_ready = 1'b1;" in text
    assert "io_core_dis_uops_0_valid =" in text
    assert "io_hellacache_req_ready = 1'b1;" not in text
    assert "io_dmem_release_ready = 1'b1;" not in text
    assert "io_core_fp_stdata_ready = 1'b1;" not in text


def test_cov_tb_does_not_drive_lsu_outputs(tmp_path):
    from adapters.mediumboom.lsu_driver import write_cov_tb

    dut = GenDut(
        collateral=tmp_path,
        sv=tmp_path / "LSU.sv",
        module="LSU",
        ports=(
            "clock",
            "reset",
            "io_dmem_req_ready",
            "io_hellacache_req_ready",
            "io_core_exe_0_req_valid",
        ),
        dirs=("input", "input", "input", "output", "input"),
        clk="clock",
        rst="reset",
        widths=("", "", "", "", ""),
    )
    dest = tmp_path / "lsu_cov_tb.sv"
    write_cov_tb(dut, dest)
    text = dest.read_text()
    assert "io_dmem_req_ready = 1'b1;" in text
    assert "io_hellacache_req_ready = 1'b1;" not in text


def test_missing_collateral_is_loud(monkeypatch, tmp_path):
    from adapters.mediumboom import boom_backend as bb

    monkeypatch.setattr(bb, "chipyard_root", lambda: tmp_path / "no-chipyard")
    monkeypatch.delenv("CHIPYARD_GEN", raising=False)
    monkeypatch.setattr(bb, "PKG", tmp_path)
    with pytest.raises(FileNotFoundError, match="setup_mediumboom"):
        find_gen_collateral()


def test_stim_from_ops():
    assert stim_from_ops(["ADD", "LD", "SD", "FENCE"]) == ["idle", "load", "store", "fence"]


def test_stim_from_asm():
    from adapters.mediumboom.lsu_driver import stim_from_asm

    text = "\tld x1, 0(x2)\n\tsw x3, 0(x4)\n\tadd x5, x6, x7\n\tfence\n"
    assert stim_from_asm(text) == ["load", "store", "fence"]


def test_credits_from_asm_soc_classes():
    from adapters.mediumboom.boom_backend import _credits_from_asm, _soc_run_credits

    text = "\tld x1, 0(x2)\n\tadd x3, x4, x5\n\tbeq x0, x0, 1f\n\tmul x6, x7, x8\n"
    c = _credits_from_asm(text)
    assert "op.ld" in c
    assert "soc.class.mem_load" in c
    assert "soc.class.alu" in c
    assert "soc.class.branch" in c
    assert "soc.class.muldiv" in c
    r = _soc_run_credits("*** PASSED *** Completed after               172526 simulation cycles\n")
    assert r == {"soc.pass", "soc.cycles_ge_1k", "soc.cycles_ge_10k", "soc.cycles_ge_100k"}


def test_pick_torture_prefers_directed(tmp_path):
    from adapters.mediumboom.boom_backend import _pick_torture_source
    import random

    (tmp_path / "mediumboom_0.S").write_text("// carpet\n")
    (tmp_path / "mediumboom_dir_0.S").write_text("// directed\n")
    src = _pick_torture_source(tmp_path, "directed", random.Random(0))
    assert src.name == "mediumboom_dir_0.S"


def test_mini_asm_override_skips_available_torture_suite(monkeypatch, tmp_path):
    from adapters.mediumboom.boom_backend import find_torture_suite

    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "mediumboom_0.S").write_text("// torture input\n")
    monkeypatch.setenv("RISCV_TORTURE_SUITE", str(suite))
    monkeypatch.setenv("BOOM_USE_MINI_ASM", "1")

    assert find_torture_suite() is None


def test_directed_candidate_ops_are_used_for_mediumboom_soc_wrapper():
    from adapters.mediumboom.boom_backend import _directed_ops_from_artifact

    text = """
    .section .text
_start:
    li x5, 1
    lw x6, 0(x10)  # memory candidate
    beq x5, x6, 1f
    nop
1:
    fence iorw, iorw
    sw x5, 0(x10)
"""

    assert _directed_ops_from_artifact(text) == ["LW", "BEQ", "FENCE", "SW"]


def test_directed_artifact_is_compiled_and_scored(monkeypatch, tmp_path):
    from adapters.mediumboom import boom_backend
    from adapters.mediumboom.engines import Allocation, KnobState
    import random

    work_dir = tmp_path / "round" / "directed"
    work_dir.mkdir(parents=True)
    candidate = work_dir.parent / "t.S"
    candidate.write_text("_start:\n  add x5, x6, x7\n  sw x5, 0(x10)\n")
    monkeypatch.setattr(boom_backend, "find_mediumboom_sim", lambda: tmp_path / "sim")
    compiled = []

    def compile_fake(asm, elf):
        compiled.append((asm, elf, asm.read_text()))
        elf.write_bytes(b"fake elf")

    monkeypatch.setattr(boom_backend, "compile_rv64", compile_fake)
    monkeypatch.setattr(
        boom_backend,
        "run_mediumboom_elf",
        lambda _elf, _work, max_cycles: (True, "*** PASSED *** Completed after 10 simulation cycles", 1.0),
    )
    monkeypatch.delenv("BOOM_USE_LSU_COV", raising=False)
    model = boom_backend.freeze_boom_model()
    alloc = Allocation(action="directed", hour_budget=1.0, target_pids=[], reason="test")

    result, _knobs = boom_backend.run_crv_batch(
        model, alloc, KnobState(), random.Random(0), work_dir
    )

    assert len(compiled) == 1
    assert "add" in compiled[0][2] and "sw" in compiled[0][2]
    assert "tohost:" in compiled[0][2]
    assert "directed_artifact=" in result.notes
    assert "directed_ops=ADD,SW" in result.notes
    assert result.hits


def test_soc_formal_invokes_sby_config_from_working_directory(monkeypatch, tmp_path):
    from adapters.mediumboom import soc_formal

    captured = {}
    monkeypatch.setattr(
        soc_formal, "discover_soc_dut", lambda _scope: SimpleNamespace(module="ChipTop")
    )
    monkeypatch.setattr(
        soc_formal,
        "write_soc_sby",
        lambda _dut, dest, _depth: dest.write_text("[options]\nmode cover\n"),
    )

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["cwd"] = Path(kwargs["cwd"])
        status = captured["cwd"] / "config" / "status"
        status.parent.mkdir(parents=True)
        status.write_text("PASS\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(soc_formal.subprocess, "run", fake_run)
    _hits, _retired, notes, _wall = soc_formal.run_soc_formal(
        tmp_path / "work", scope="chiptop", depth=8, timeout_s=1
    )

    assert captured["argv"] == ["sby", "-f", "config.sby"]
    assert captured["cwd"] == tmp_path / "work" / "cover_d8"
    assert "status=PASS" in notes


def test_soc_formal_sby_file_inputs_are_absolute(tmp_path):
    from adapters.mediumboom import soc_formal

    collateral = tmp_path / "rtl"
    collateral.mkdir()
    top = collateral / "ChipTop.sv"
    top.write_text(
        "module ChipTop(input logic clock, input logic reset, output logic done);\n"
        "assign done = clock & reset;\nendmodule\n"
    )
    dut = GenDut(
        collateral=collateral,
        sv=top,
        module="ChipTop",
        ports=("clock", "reset", "done"),
        dirs=("input", "input", "output"),
        clk="clock",
        rst="reset",
        widths=("", "", ""),
    )
    recipe = soc_formal.write_soc_sby(dut, tmp_path / "formal" / "config.sby", 8)
    file_lines = recipe.read_text().split("[files]\n", 1)[1].splitlines()

    assert file_lines
    assert all(Path(path).is_absolute() for path in file_lines)


def test_soc_formal_plusarg_reader_stub_keeps_generated_parameters():
    from adapters.mediumboom.soc_formal import stub_ext_memory

    stub = stub_ext_memory(
        "plusarg_reader",
        "plusarg_reader #( .DEFAULT(0), .FORMAT(\"tilelink_timeout=%d\"), "
        ".WIDTH(32) ) plusarg_reader (.out(timeout));",
    )

    assert "parameter WIDTH=32" in stub
    assert "output [WIDTH-1:0] out" in stub
    assert "assign out = DEFAULT" in stub


def test_write_cov_tb(tmp_path):
    dut = GenDut(
        collateral=tmp_path,
        sv=tmp_path / "LSU.sv",
        module="LSU",
        ports=("clock", "reset", "io_core_exe_0_req_valid"),
        dirs=("input", "input", "input"),
        clk="clock",
        rst="reset",
        widths=("", "", ""),
    )
    dest = tmp_path / "lsu_cov_tb.sv"
    assert write_cov_tb(dut, dest) is True
    assert write_cov_tb(dut, dest) is False
    text = dest.read_text()
    assert "module lsu_cov_tb" in text
    assert "LSU dut" in text
    assert "exe_req: cover" in text


def test_write_if_changed_keeps_mtime(tmp_path):
    path = tmp_path / "x.sv"
    assert write_if_changed(path, "a\n") is True
    m0 = path.stat().st_mtime_ns
    assert write_if_changed(path, "a\n") is False
    assert path.stat().st_mtime_ns == m0
    assert write_if_changed(path, "b\n") is True
