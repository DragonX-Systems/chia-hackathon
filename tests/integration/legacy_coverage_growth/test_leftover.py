from __future__ import annotations

import json
import random

from adapters.mediumboom.coverage_model import load_model
from adapters.mediumboom.engines import Allocation, KnobState
from adapters.mediumboom.leftover_backend import freeze_leftover_rich, simulate_leftover_batch
from adapters.mediumboom.loop import run_experiment
from adapters.mediumboom.oracle_policy import CATALOG_ID


def test_freeze_has_unreachable_and_sva():
    model = freeze_leftover_rich()
    buckets = {p.bucket for p in model.points}
    kinds = {p.kind for p in model.points}
    assert "unreachable" in buckets
    assert "sva" in kinds
    assert len(model.points) == 5000
    assert model.seed == 20260910
    n_unreach = sum(1 for p in model.points if p.bucket == "unreachable")
    n_sva = sum(1 for p in model.points if p.kind == "sva")
    assert n_unreach > 50
    assert n_sva > 50


def test_simulate_leftover_batch_directed_wall_hours():
    model = freeze_leftover_rich()
    open_hard = [p.pid for p in model.points if p.bucket == "hard"][:4]
    alloc = Allocation("directed", hour_budget=0.8, target_pids=open_hard, reason="test")
    result, _knobs = simulate_leftover_batch(
        model, set(), set(), alloc, KnobState(), random.Random(1)
    )
    assert abs(result.hours - 1.20) < 1e-9
    assert "leftover-rich wall_h=" in result.notes


def test_run_experiment_leftover_enh_only(tmp_path):
    payload = run_experiment(
        tmp_path,
        budget_hours=8.0,
        seeds=(1,),
        backend="leftover",
        oracles=(),
        enhancement=True,
    )
    assert payload["oracles"] == []
    assert "enh_v1" in payload["mean_final_hit"]
    assert "enh_formal" in payload["mean_final_hit"]
    assert "enh_directed" in payload["mean_final_hit"]
    assert "enh_v1" in payload["mean_final_closure"]
    assert "baseline" in payload["mean_final_hit"]
    assert payload["backend"] == "leftover"
    catalog = json.loads((tmp_path / "oracle_catalog.json").read_text())
    assert catalog["catalog_id"] == CATALOG_ID
    assert catalog["catalog_id"] == "oracles-20260907"
    frozen = load_model(tmp_path / "frozen_coverage_model.json")
    assert any(p.bucket == "unreachable" for p in frozen.points)
    assert any(p.kind == "sva" for p in frozen.points)
