import shutil
from pathlib import Path

import pytest

from adapters.mediumboom.verilator_backend import parse_coverage_dat


FIXTURE = """# SystemC::Coverage-3
C '\x01f\x02/tmp/riscv_alu.v\x01l\x02161\x01n\x0216\x01page\x02v_line/riscv_alu\x01o\x02case\x01h\x02TOP.tb_top.dut.u_core.u_exec.u_alu' 12
C '\x01f\x02/tmp/tb_top.sv\x01l\x0253\x01n\x0229\x01page\x02v_user/tb_top\x01o\x02core_not_reset\x01h\x02TOP.tb_top.core_not_reset' 0
C '\x01f\x02/tmp/riscv_alu.v\x01l\x02102\x01n\x0213\x01page\x02v_branch/riscv_alu\x01o\x02if\x01h\x02TOP.tb_top.dut.u_core.u_exec.u_alu' 3
"""


def test_parse_verilator5_packed_records(tmp_path):
    path = tmp_path / "coverage.dat"
    path.write_text(FIXTURE)
    rows = parse_coverage_dat(path)
    assert len(rows) == 3
    by_kind = {r["kind"]: r for r in rows}
    assert by_kind["line"]["count"] == 12
    assert by_kind["line"]["base"] == "riscv_alu.v"
    assert by_kind["toggle"]["count"] == 3
    assert by_kind["sva"]["name"] == "tb.core_not_reset"
    assert by_kind["sva"]["count"] == 0


@pytest.mark.skipif(
    shutil.which("verilator") is None or not Path(
        Path(__file__).resolve().parents[3]
        / "adapters/mediumboom/testbenches/verilator/obj_dir/Vtb_top"
    ).exists(),
    reason="Verilator sim not built",
)
def test_freeze_has_line_and_sva():
    from adapters.mediumboom.sby_backend import CATALOG
    from adapters.mediumboom.verilator_backend import freeze_verilator_model

    model = freeze_verilator_model()
    kinds = {p.kind for p in model.points}
    assert "line" in kinds
    assert "sva" in kinds
    names = {p.name for p in model.points}
    for prop in CATALOG:
        assert prop.name in names
    assert len(model.points) > 40
