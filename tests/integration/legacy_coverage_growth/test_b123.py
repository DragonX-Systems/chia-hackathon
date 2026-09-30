"""Legacy B1/B2/B3 planners and parallel admission."""

from __future__ import annotations

from adapters.mediumboom.baselines_b123 import (
    b1_crv_only_plan,
    b2_static_hybrid_plan,
    b3_adaptive_plan,
)
from adapters.mediumboom.loop import run_experiment
from adapters.mediumboom.meter import MeterSnapshot
from adapters.mediumboom.yield_table import EngineYieldNode


def _empty_snap(n: int = 20) -> MeterSnapshot:
    uncovered = [
        {"pid": f"cp_{i}", "kind": "line" if i % 3 else "sva", "unit": "lsu", "bucket": "hard"}
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


def test_b1_never_directed_or_formal():
    snap = _empty_snap()
    y = EngineYieldNode()
    for _ in range(12):
        alloc = b1_crv_only_plan(snap, y, 10.0)
        assert alloc.action == "riscv_dv"
        y.record("riscv_dv", 0.8, 1, 0)


def test_b2_phases_are_batch_count_driven():
    snap = _empty_snap()
    y = EngineYieldNode()
    # Phase CRV
    for _ in range(8):
        alloc = b2_static_hybrid_plan(snap, y, 10.0)
        assert alloc.action == "riscv_dv", alloc.reason
        y.record("riscv_dv", 0.8, 2, 0)
    # Phase directed
    for _ in range(4):
        alloc = b2_static_hybrid_plan(snap, y, 10.0)
        assert alloc.action == "directed", alloc.reason
        y.record("directed", 1.2, 1, 0)
    # Phase formal (SVA present in uncovered)
    for _ in range(3):
        alloc = b2_static_hybrid_plan(snap, y, 10.0)
        assert alloc.action == "formal", alloc.reason
        y.record("formal", 0.3, 0, 1)


def test_b3_can_choose_formal_after_crv():
    snap = _empty_snap()
    y = EngineYieldNode()
    y.record("riscv_dv", 0.8, 5, 0)
    y.record("riscv_dv", 0.8, 5, 0)
    alloc = b3_adaptive_plan(snap, y, 10.0)
    assert alloc.action in {"formal", "directed", "riscv_dv"}


def test_b123_leftover_experiment(tmp_path):
    payload = run_experiment(
        tmp_path,
        budget_hours=6.0,
        seeds=(1,),
        backend="leftover",
        oracles=(),
        enhancement=False,
        arms=("b1", "b2", "b3"),
    )
    assert set(payload["mean_final_hit"]) == {"b1", "b2", "b3"}
    assert payload["reference_arm"] == "b2"
    assert (tmp_path / "b123.json").exists()
    by_arm = {row["arm"]: row for row in payload["arms"] if row["seed"] == 1}
    b1_actions = {p["action"] for p in by_arm["b1"]["policy"]}
    assert b1_actions <= {"riscv_dv"}
    b2_actions = [p["action"] for p in by_arm["b2"]["policy"]]
    assert b2_actions, "b2 produced no steps"
    # With 6h, B2 may still be in CRV; first non-init actions must be riscv_dv.
    assert b2_actions[0] == "riscv_dv"


def test_parallel_peak_sim_exceeds_one(tmp_path):
    payload = run_experiment(
        tmp_path,
        budget_hours=4.0,
        seeds=(1,),
        backend="leftover",
        oracles=(),
        enhancement=False,
        arms=("b2", "b3"),
        parallel=True,
        n_sim=4,
        n_formal=1,
    )
    assert payload["parallel"] is True
    stats = payload["parallel_stats"]
    assert stats
    assert max(s["max_in_flight"] for s in stats) >= 2
    assert max(s["peak_sim"] for s in stats) >= 2
    assert max(s["peak_formal"] for s in stats) <= 1
