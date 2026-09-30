from adapters.mediumboom.meter import MeterSnapshot
from adapters.mediumboom.oracle_policy import (
    ORACLE_NAMES,
    audit_step,
    catalog,
    oracle_cph_greedy,
    oracle_sim_max,
    oracle_sva_first,
    oracle_stage_clean,
    start_picks,
)
from adapters.mediumboom.yield_table import EngineYieldNode


def _sva_snap(n: int = 6) -> MeterSnapshot:
    uncovered = [
        {"pid": f"cp_b{i:03d}", "kind": "sva", "unit": "lsu", "name": f"p{i}"}
        for i in range(n)
    ]
    return MeterSnapshot(
        hours=0.0,
        n_total=n,
        n_covered=0,
        n_retired=0,
        hit_frac=0.0,
        closure_frac=0.0,
        uncovered=uncovered,
    )


def test_catalog_has_four_named_oracles():
    cat = catalog()
    assert cat["catalog_id"] == "oracles-20260907"
    assert cat["written_before_run"] is True
    assert set(ORACLE_NAMES) == {"sva_first", "sim_max", "cph_greedy", "stage_clean"}
    for spec in cat["oracles"]:
        assert spec["assumption"]
        assert spec["will_fail_if"]


def test_sva_only_oracles_disagree_at_step1():
    snap = _sva_snap()
    picks = start_picks(snap, 12.0)["picks"]
    assert picks["sva_first"] == "formal"
    assert picks["sim_max"] == "torture"
    assert picks["cph_greedy"] == "torture"
    assert picks["stage_clean"] == "torture"
    row = audit_step(snap, EngineYieldNode(), 12.0)
    assert row["agree"] is False
    assert row["picks"]["sva_first"]["action"] == "formal"


def test_mixed_list_sva_first_still_tortures():
    snap = _sva_snap()
    snap.uncovered.append({"pid": "cp_line", "kind": "line", "unit": "lsu", "name": "l0"})
    y = EngineYieldNode()
    assert oracle_sva_first(snap, y, 12.0).action == "torture"
    assert oracle_stage_clean(snap, y, 12.0).action == "torture"
    assert oracle_sim_max(snap, y, 12.0).action == "torture"
    assert oracle_cph_greedy(snap, y, 12.0).action == "torture"


def test_sva_first_stops_after_dry_formal():
    snap = _sva_snap()
    y = EngineYieldNode()
    y.record("formal", 0.2, 0, 0)
    best = oracle_sva_first(snap, y, 10.0)
    assert best.action == "formal"
    assert best.hour_budget <= 0.05
