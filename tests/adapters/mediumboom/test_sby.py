import shutil

import pytest

from adapters.mediumboom.sby_backend import freeze_sby_model, parse_sby_log, run_sby


pytestmark = pytest.mark.skipif(shutil.which("sby") is None, reason="sby not on PATH")


def test_parse_sby_log_names():
    log = """
Reached cover statement in step 1 at cover_lsu: load_fire
Unreached cover statement at cover_lsu: amo_without_a
summary:   reached cover statement cover_lsu.store_fire at cover_lsu.sv:30
"""
    reached, unreached = parse_sby_log(log)
    assert "load_fire" in reached
    assert "store_fire" in reached
    assert "amo_without_a" in unreached


def test_cover_lsu_cover_and_prove(tmp_path):
    status, log, _ = run_sby("cover_lsu", "cover", tmp_path)
    reached, unreached = parse_sby_log(log)
    assert status == "fail"  # cover mode FAIL = at least one witness
    assert "load_fire" in reached
    assert "store_fire" in reached
    assert "amo_without_a" in unreached
    assert "load_and_store" in unreached
    pr, pr_log, _ = run_sby("cover_lsu", "prove", tmp_path)
    assert pr == "pass"
    assert "successful proof by k-induction" in pr_log


def test_sby_model_has_real_duts():
    model = freeze_sby_model()
    names = {p.name for p in model.points}
    assert "boom.lsu.amo_without_a" in names
    assert "riscv_soc.dec.fadd_legal" in names
    assert "riscv_soc.irq.irq_source4" in names
    assert sum(1 for p in model.points if p.bucket == "unreachable") == 6
    assert "riscv_soc.irq.irq_mer_hold" in names
