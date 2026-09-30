from __future__ import annotations

import random

from adapters.mediumboom.engines import Allocation, KnobState
from adapters.mediumboom.enhancement import enhancement_plan
from adapters.mediumboom.formal_prune import directed_targets, formal_targets, is_sva_pid
from adapters.mediumboom.leftover_backend import freeze_leftover_rich, simulate_leftover_batch
from adapters.mediumboom.loop import run_experiment
from adapters.mediumboom.meter import MeterSnapshot
from adapters.mediumboom.prune import split_leftovers
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


def test_is_sva_pid_kind_only():
    assert is_sva_pid(_pt("sva_0", "sva"))
    assert not is_sva_pid(_pt("line_0", "line"))
    assert not is_sva_pid(_pt("tog_0", "toggle"))


def test_sva_only_formal_targets():
    sva = [_pt(f"sva_{i}", "sva") for i in range(6)]
    split = split_leftovers(_snap(sva, n_total=38, hit_frac=0.84))
    targets = formal_targets(split)
    assert targets == [f"sva_{i}" for i in range(6)]
    assert all(pid.startswith("sva_") for pid in targets)
    assert directed_targets(split) == []


def test_formal_targets_cap_12():
    sva = [_pt(f"sva_{i}", "sva") for i in range(20)]
    split = split_leftovers(_snap(sva, n_total=40, hit_frac=0.5))
    assert len(formal_targets(split)) == 12


def test_formal_targets_stride_not_prefix():
    sva = [_pt(f"sva_{i}", "sva") for i in range(120)]
    split = split_leftovers(_snap(sva, n_total=200, hit_frac=0.5))
    targets = formal_targets(split)
    assert len(targets) == 12
    assert targets[0] == "sva_0"
    assert targets != [f"sva_{i}" for i in range(12)]
    assert "sva_119" in targets or int(targets[-1].split("_")[1]) > 50


def test_mixed_directed_targets_have_no_sva():
    uncovered = (
        [_pt(f"sva_{i}", "sva") for i in range(6)]
        + [_pt(f"line_{i}", "line") for i in range(4)]
        + [_pt(f"tog_{i}", "toggle") for i in range(2)]
    )
    split = split_leftovers(_snap(uncovered, n_total=38))
    formal = formal_targets(split)
    directed = directed_targets(split)
    assert all(pid.startswith("sva_") for pid in formal)
    assert len(formal) == 6
    assert directed
    assert all(not pid.startswith("sva_") for pid in directed)
    assert set(directed) == {f"line_{i}" for i in range(4)} | {f"tog_{i}" for i in range(2)}


def _yields_after_crv(*, dv_batches: int = 8, last_cph_high: bool = True, formal_batches: int = 0, formal_last=(1, 1)):
    y = EngineYieldNode()
    hits = 40 if last_cph_high else 4
    hours = 2.0
    for _ in range(dv_batches):
        y.record("riscv_dv", hours, hits, 0)
    last_h, last_r = formal_last
    for i in range(formal_batches):
        if i == formal_batches - 1:
            y.record("formal", 0.3, last_h, last_r)
        else:
            y.record("formal", 0.3, 1, 1)
    return y


def test_enhancement_plan_formal_only_after_40pct_hit():
    uncovered = [_pt(f"sva_{i}", "sva") for i in range(8)] + [
        _pt(f"line_{i}", "line") for i in range(10)
    ]
    below = _snap(uncovered, n_total=38, hit_frac=0.39)
    alloc_below = enhancement_plan(below, _yields_after_crv(), remaining=8.0)
    assert alloc_below.action != "formal"

    above = _snap(uncovered, n_total=38, hit_frac=0.40)
    alloc_above = enhancement_plan(above, _yields_after_crv(), remaining=8.0)
    assert alloc_above.action == "formal"
    assert all(pid.startswith("sva_") for pid in alloc_above.target_pids)


def test_enhancement_plan_up_to_three_formal_then_stop():
    uncovered = [_pt(f"sva_{i}", "sva") for i in range(8)] + [
        _pt(f"line_{i}", "line") for i in range(10)
    ]
    snap = _snap(uncovered, n_total=38, hit_frac=0.42)
    for n in (0, 1, 2):
        alloc = enhancement_plan(snap, _yields_after_crv(formal_batches=n), remaining=8.0)
        assert alloc.action == "formal", n
    after_three = enhancement_plan(snap, _yields_after_crv(formal_batches=3), remaining=8.0)
    assert after_three.action != "formal" or "stop recarpet" in after_three.reason


def test_enhancement_plan_stops_on_formal_dry():
    uncovered = [_pt(f"sva_{i}", "sva") for i in range(8)] + [
        _pt(f"line_{i}", "line") for i in range(10)
    ]
    snap = _snap(uncovered, n_total=38, hit_frac=0.42)
    dry = _yields_after_crv(formal_batches=1, formal_last=(0, 0))
    alloc = enhancement_plan(snap, dry, remaining=8.0)
    assert alloc.action != "formal" or "stop recarpet" in alloc.reason


def test_simulate_formal_retires_unreachable_sva():
    model = freeze_leftover_rich()
    unreach_sva = [p.pid for p in model.points if p.kind == "sva" and p.bucket == "unreachable"][:12]
    assert unreach_sva
    alloc = Allocation("formal", hour_budget=1.2, target_pids=unreach_sva, reason="test")
    result, _ = simulate_leftover_batch(
        model, set(), set(), alloc, KnobState(), random.Random(1)
    )
    assert result.retired, "formal Unreached must retire planted unreachable SVA"
    assert result.hits == []


def test_leftover_enh_v1_runs_formal_and_closure_ge_hit_if_retired(tmp_path):
    payload = run_experiment(
        tmp_path,
        budget_hours=16.0,
        seeds=(1,),
        backend="leftover",
        oracles=(),
        enhancement=True,
    )
    arms = [a for a in payload["arms"] if a["arm"] == "enh_v1"]
    assert arms
    arm = arms[0]
    trace = arm["trace"]
    actions = [s["action"] for s in trace]
    assert "formal" in actions
    # Formal only after snap.hit_frac has already reached 0.40 (hours-to-40% unchanged).
    prior_hit = 0.0
    for s in trace:
        if s["action"] == "formal":
            assert prior_hit >= 0.40, prior_hit
        prior_hit = s["hit_frac"]
    retired_any = any(int(s.get("retired") or 0) > 0 for s in trace)
    if retired_any:
        assert arm["final_closure"] >= arm["final_hit"]
