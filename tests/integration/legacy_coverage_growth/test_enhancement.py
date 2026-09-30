from __future__ import annotations

import json

from adapters.mediumboom.enhancement import ENHANCEMENT_ID, enhancement_plan
from adapters.mediumboom.loop import run_experiment
from adapters.mediumboom.meter import MeterSnapshot
from adapters.mediumboom.prune import past_crv_70, past_crv_80, split_leftovers
from adapters.mediumboom.yield_table import EngineYieldNode


def _pt(pid: str, kind: str) -> dict:
    return {"pid": pid, "kind": kind, "unit": "u", "name": pid}


def _snap(uncovered: list[dict], n_total: int, hit_frac: float | None = None) -> MeterSnapshot:
    n_open = len(uncovered)
    n_closed = n_total - n_open
    hf = hit_frac if hit_frac is not None else n_closed / n_total
    return MeterSnapshot(
        hours=1.0,
        n_total=n_total,
        n_covered=n_closed,
        n_retired=0,
        hit_frac=hf,
        closure_frac=n_closed / n_total,
        uncovered=uncovered,
    )


def test_split_leftovers_sva_vs_crv():
    uncovered = (
        [_pt(f"sva_{i}", "sva") for i in range(6)]
        + [_pt(f"line_{i}", "line") for i in range(4)]
        + [_pt(f"tog_{i}", "toggle") for i in range(2)]
    )
    split = split_leftovers(_snap(uncovered, n_total=38))
    assert all(u["kind"] == "sva" for u in split.formal)
    assert len(split.formal) == 6
    assert all(u["kind"] != "sva" for u in split.crv)
    assert len(split.crv) == 6
    assert {u["kind"] for u in split.crv} == {"line", "toggle"}


def test_crv_closed_frac_all_crv_open():
    # n_total=38, 6 SVA open, 32 CRV open → denom=32, crv_closed_frac == 0.0
    uncovered = [_pt(f"sva_{i}", "sva") for i in range(6)] + [
        _pt(f"line_{i}", "line") for i in range(32)
    ]
    split = split_leftovers(_snap(uncovered, n_total=38, hit_frac=0.0))
    assert split.n_crv == 32
    assert split.n_formal == 6
    assert split.crv_closed_frac == 0.0


def test_crv_closed_frac_partial():
    # n_total=38, 6 SVA open, 6 CRV open (26 CRV closed) → 1 - 6/32 = 0.8125
    uncovered = [_pt(f"sva_{i}", "sva") for i in range(6)] + [
        _pt(f"line_{i}", "line") for i in range(6)
    ]
    split = split_leftovers(_snap(uncovered, n_total=38))
    assert split.n_crv == 6
    assert split.crv_closed_frac == 0.8125


def test_past_crv_70_and_80():
    open_all = [_pt(f"sva_{i}", "sva") for i in range(6)] + [
        _pt(f"line_{i}", "line") for i in range(32)
    ]
    split0 = split_leftovers(_snap(open_all, n_total=38, hit_frac=0.0))
    assert split0.crv_closed_frac == 0.0
    assert not past_crv_70(split0)
    assert not past_crv_80(split0)

    # 6 SVA + 10 CRV open → crv_closed = 1 - 10/32 = 0.6875, hit_frac ~ 22/38
    mid = [_pt(f"sva_{i}", "sva") for i in range(6)] + [
        _pt(f"line_{i}", "line") for i in range(10)
    ]
    split_mid = split_leftovers(_snap(mid, n_total=38, hit_frac=0.50))
    assert split_mid.crv_closed_frac == 1.0 - 10 / 32
    assert not past_crv_70(split_mid)
    assert not past_crv_80(split_mid)

    # 6 SVA + 6 CRV → 0.8125
    hi = [_pt(f"sva_{i}", "sva") for i in range(6)] + [
        _pt(f"line_{i}", "line") for i in range(6)
    ]
    split_hi = split_leftovers(_snap(hi, n_total=38, hit_frac=0.684))
    assert past_crv_70(split_hi)
    assert past_crv_80(split_hi)

    # CRV empty, SVA leftover → past 70 and 80
    sva_only = [_pt(f"sva_{i}", "sva") for i in range(6)]
    split_sva = split_leftovers(_snap(sva_only, n_total=38, hit_frac=0.84))
    assert past_crv_70(split_sva)
    assert past_crv_80(split_sva)

    # hit_frac threshold without crv_closed
    low_closed_high_hit = split_leftovers(_snap(open_all, n_total=38, hit_frac=0.71))
    assert past_crv_70(low_closed_high_hit)
    assert not past_crv_80(low_closed_high_hit)
    hit80 = split_leftovers(_snap(open_all, n_total=38, hit_frac=0.80))
    assert past_crv_80(hit80)


def test_enhancement_plan_never_directed_snipes_sva():
    sva = [_pt(f"sva_{i}", "sva") for i in range(6)]
    lines = [_pt(f"line_{i}", "line") for i in range(6)]
    snap = _snap(sva + lines, n_total=38, hit_frac=0.70)
    yields = EngineYieldNode()
    # High CRV closed → last-mile / prune, not genome retune.
    alloc = enhancement_plan(snap, yields, remaining=4.0)
    if alloc.action == "directed":
        crv_pids = {u["pid"] for u in lines}
        assert alloc.target_pids
        assert all(pid in crv_pids for pid in alloc.target_pids)
        assert not any(pid.startswith("sva_") for pid in alloc.target_pids)

    # Yield death also routes to prune/last-mile without SVA snipes.
    dead = EngineYieldNode()
    dead.record("torture", 2.0, 1, 0)
    dead.record("riscv_dv", 2.0, 1, 0)
    alloc_dead = enhancement_plan(snap, dead, remaining=4.0)
    if alloc_dead.action == "directed":
        assert all(not pid.startswith("sva_") for pid in alloc_dead.target_pids)

    # SVA-only leftovers after CRV empty → formal, never directed on SVA.
    sva_only = _snap(sva, n_total=38, hit_frac=0.84)
    alloc_sva = enhancement_plan(sva_only, EngineYieldNode(), remaining=4.0)
    assert alloc_sva.action == "formal"
    assert all(pid.startswith("sva_") for pid in alloc_sva.target_pids)


def test_last_mile_never_before_40pct():
    uncovered = [_pt(f"sva_{i}", "sva") for i in range(4)] + [
        _pt(f"line_{i}", "line") for i in range(20)
    ]
    snap = _snap(uncovered, n_total=38, hit_frac=0.36)
    yields = EngineYieldNode()
    for _ in range(16):
        yields.record("riscv_dv", 2.0, 40, 0)
    alloc = enhancement_plan(snap, yields, remaining=8.0)
    assert alloc.action != "directed"


def test_last_mile_after_16_batches_and_40pct():
    uncovered = [_pt(f"sva_{i}", "sva") for i in range(4)] + [
        _pt(f"line_{i}", "line") for i in range(8)
    ]
    snap = _snap(uncovered, n_total=38, hit_frac=0.50)
    yields = EngineYieldNode()
    for _ in range(16):
        yields.record("riscv_dv", 2.0, 30, 0)
    alloc = enhancement_plan(snap, yields, remaining=8.0)
    assert alloc.action == "directed"
    assert alloc.hour_budget == 1.2
    assert all(not pid.startswith("sva_") for pid in alloc.target_pids)

    formal_only = enhancement_plan(snap, yields, remaining=8.0, mode="formal")
    assert formal_only.action == "formal"
    assert all(pid.startswith("sva_") for pid in formal_only.target_pids)

    directed_only = enhancement_plan(snap, yields, remaining=8.0, mode="directed")
    assert directed_only.action == "directed"
    assert all(not pid.startswith("sva_") for pid in directed_only.target_pids)


def test_last_mile_peak_cph_drop():
    uncovered = [_pt(f"sva_{i}", "sva") for i in range(4)] + [
        _pt(f"line_{i}", "line") for i in range(8)
    ]
    snap = _snap(uncovered, n_total=38, hit_frac=0.50)
    yields = EngineYieldNode()
    for _ in range(11):
        yields.record("riscv_dv", 1.0, 20, 0)  # last_cph = 20
    yields.record("riscv_dv", 1.0, 5, 0)  # batch 12, last_cph = 5 < 0.5 * 20
    assert yields.dv_peak_cph == 20.0
    alloc = enhancement_plan(snap, yields, remaining=8.0)
    assert alloc.action == "directed"


def test_enhancement_plan_stays_on_crv_before_gates():
    # Below 40% hit and without last-mile gates → constrained-random, not retune.
    uncovered = [_pt(f"sva_{i}", "sva") for i in range(4)] + [
        _pt(f"line_{i}", "line") for i in range(20)
    ]
    snap = _snap(uncovered, n_total=38, hit_frac=0.36)
    split = split_leftovers(snap)
    assert not past_crv_70(split)

    yields = EngineYieldNode()
    yields.record("riscv_dv", 1.5, 3, 0)
    alloc = enhancement_plan(snap, yields, remaining=8.0)
    assert alloc.action == "riscv_dv"


def test_fake_backend_smoke_with_enhancement(tmp_path):
    payload = run_experiment(tmp_path, budget_hours=12.0, seeds=(1,), enhancement=True)
    catalog_names = {"sva_first", "sim_max", "cph_greedy", "stage_clean"}
    assert set(payload["oracles"]) == catalog_names
    assert "enh_v1" not in payload["oracles"]
    assert "enh_v1" in payload["mean_final_closure"]
    assert "enh_v1" in payload["mean_final_hit"]
    assert payload["enhancement"]["id"] == ENHANCEMENT_ID
    assert payload["enhancement"]["catalog_oracles_untouched"] == "oracles-20260907"
    assert (tmp_path / "enhancement.json").exists()
    catalog = json.loads((tmp_path / "oracle_catalog.json").read_text())
    assert catalog["catalog_id"] == "oracles-20260907"

    frozen = json.loads((tmp_path / "frozen_coverage_model.json").read_text())
    kind_by_pid = {p["pid"]: p["kind"] for p in frozen["points"]}

    enh_arms = [a for a in payload["arms"] if a["arm"] == "enh_v1"]
    assert enh_arms
    for arm in enh_arms:
        for step in arm["policy"]:
            if step.get("action") == "directed":
                for pid in step.get("targets") or []:
                    assert kind_by_pid.get(pid) != "sva", pid
