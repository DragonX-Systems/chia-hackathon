"""Behavioral tests for node scoring, unique runs, and work-conserving dispatch."""
from dataclasses import replace
import json
import time

import pytest

from chialoop import (AdaptivePolicy, CampaignRunner, CampaignSpec, CoveragePoint,
                      Engine, JobCatalog, JobResult, JobSpec, JobStatus, ProofStatus,
                      ResourceCapacity)
from chialoop.core.types import MergeOutcome
from chialoop.core.yields import YieldTable
from chialoop.orchestration.runtime import CompletedTask, TaskHandle


def job(name, node="crv-1", sequence=0, partition="p"):
    return JobSpec(name, Engine.CRV, partition, sequence, ("bin",), name, 60, "rtl",
                   node_id=node)


def result(work, hours=1):
    return JobResult(work.job_id, work.engine, JobStatus.SUCCESS, "", "", hours,
                     (), (), ProofStatus.NOT_APPLICABLE, "rtl", None)


def test_defaults_roundtrip_and_explicit_nodes():
    spec = CampaignSpec("test", "rtl", "asm")
    assert (spec.n1, spec.n2, spec.n3, spec.resources.compute, spec.recent_window) == (5, 1, 1, 5, 10)
    assert len(spec.nodes) == 7
    assert spec.nodes["crv-2"] == "Load/store and memory ordering"
    assert CampaignSpec.from_dict(spec.to_dict()) == spec
    assert replace(spec, n1=7, n2=2, n3=3).node_for(job("x", "crv-7")) == "crv-7"
    with pytest.raises(ValueError, match="node_id"):
        spec.node_for(replace(job("x"), node_id=None))
    with pytest.raises(ValueError, match="node_id"):
        spec.node_for(job("x", "directed-1"))
    with pytest.raises(ValueError):
        replace(spec, n1=-1)


def test_window_is_per_node_time_normalized_and_evicts_old_samples():
    a, b = job("a"), job("b", "crv-2", 1)
    table = YieldTable(["crv-1", "crv-2"])
    table.record(a, result(a, 100), MergeOutcome(True, "ok", tuple(str(i) for i in range(20))))
    table.record(b, result(b, .001), MergeOutcome(True, "ok", ("b",)))
    policy = AdaptivePolicy()
    # Includes dispatch 10: no periodic exploration overrides the score.
    assert policy.choose([a, b], table, 9).job == b
    assert table.row("crv-1").recent_yield == pytest.approx(.2)
    assert table.row("crv-2").recent_yield == pytest.approx(1000)
    for i in range(10):
        other_partition = replace(a, partition_id=f"p{i}")
        table.record(other_partition, result(a), MergeOutcome(True, "zero"))
    assert len(table.row("crv-1").recent) == 10
    assert table.row("crv-1").recent_bins == 0
    assert table.row("crv-2").recent_bins == 1
    assert policy.choose([a, b], table, 10).job == b


class ControlledRuntime:
    """Finish one job while leaving its sibling running; assert immediate refill."""
    def __init__(self):
        self.active = {}
        self.submissions = []
        self.wait_occupancy = []

    def submit(self, work, executor):
        handle = TaskHandle(work, None, time.monotonic(), time.time())
        self.active[work.job_id] = handle
        self.submissions.append(work)
        return handle

    def wait_first(self, handles, *, timeout_seconds=None):
        self.wait_occupancy.append(len(handles))
        handle = handles[0]
        del self.active[handle.job.job_id]
        return [CompletedTask(handle, result(handle.job, 0))]

    def consumed_compute_hours(self, handle, *, now=None):
        return 0

    def started_epoch_seconds(self, handle):
        return None

    def cancel(self, handle):
        self.active.pop(handle.job.job_id, None)


def runner(jobs, runtime, tmp_path, **options):
    spec = CampaignSpec("test", "rtl", "asm", resources=ResourceCapacity(2, 2, 1), **options)
    return CampaignRunner(spec=spec, catalog=JobCatalog(jobs),
                          coverage_points=[CoveragePoint("bin", "p")],
                          policy=AdaptivePolicy(), runtime=runtime,
                          executor=lambda work: None, trial_id="test", output_dir=tmp_path)


def test_same_node_concurrent_unique_runs_refill_without_batch_barrier(tmp_path):
    jobs = [job(f"seed-{i}", sequence=i) for i in range(4)]
    runtime = ControlledRuntime()
    summary = runner(jobs, runtime, tmp_path).run()
    assert summary.completed_jobs == 4
    assert runtime.wait_occupancy == [2, 2, 2, 1]
    assert len({work.seed_or_query_id for work in runtime.submissions}) == 4
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    sequence = [e["event"] for e in events if e["event"] in {"job_completed", "job_dispatched"}]
    assert sequence[:6] == ["job_dispatched", "job_dispatched", "job_completed",
                            "job_dispatched", "job_completed", "job_dispatched"]
    assert set(summary.yield_table) == set(CampaignSpec("t", "r", "a").nodes)


def test_initial_probes_cover_ready_nodes_before_repeating(tmp_path):
    jobs = [job("a", sequence=0), job("b", sequence=1), job("c", "crv-2", 2)]
    runtime = ControlledRuntime()
    runner(jobs, runtime, tmp_path).run()
    assert [work.node_id for work in runtime.submissions[:2]] == ["crv-1", "crv-2"]


@pytest.mark.parametrize("engine", list(Engine))
def test_reused_identity_rejected_before_any_dispatch(tmp_path, engine):
    a = replace(job("a"), engine=engine, node_id=f"{engine.value}-1", resources=None,
                assumption_version="asm" if engine is Engine.FORMAL else None)
    b = replace(a, job_id="b", sequence_in_lane=1)
    runtime = ControlledRuntime()
    with pytest.raises(ValueError, match="unique seed/test/query"):
        runner([a, b], runtime, tmp_path)
    assert runtime.submissions == []


def test_same_prepared_binary_under_new_identity_is_rejected(tmp_path):
    a = replace(job("seed-a"), payload={"sha256": "same-binary"})
    b = replace(job("seed-b", sequence=1), payload={"sha256": "same-binary"})
    with pytest.raises(ValueError, match="reuses simulation input"):
        runner([a, b], ControlledRuntime(), tmp_path)


def test_distinct_nodes_can_share_partition_and_sequence(tmp_path):
    runtime = ControlledRuntime()
    jobs = [job("seed-a", "crv-1"), job("seed-b", "crv-2")]
    summary = runner(jobs, runtime, tmp_path).run()
    assert summary.completed_jobs == 2
    assert runtime.wait_occupancy == [2, 1]
    assert summary.yield_table["crv-1"]["completed_jobs"] == 1
    assert summary.yield_table["crv-2"]["completed_jobs"] == 1


def test_history_window_is_configurable_and_rejected_evidence_scores_zero():
    work = job("a")
    table = YieldTable(["crv-1"], recent_window=2)
    table.record(work, result(work), MergeOutcome(True, "ok", ("a", "b")))
    table.record(work, result(work), MergeOutcome(True, "ok", ("c",)))
    table.record(work, result(work), MergeOutcome(False, "invalid", ("bad",)))
    assert table.row("crv-1").recent_bins == 1
    assert len(table.row("crv-1").recent) == 2


@pytest.mark.parametrize("window", [3, 10, 20])
def test_unspecified_licenses_follow_k(window):
    direct = CampaignSpec("test", "rtl", "asm", recent_window=window)
    loaded = CampaignSpec.from_dict({
        "campaign_id": "test", "artifact_version": "rtl", "assumption_version": "asm",
        "recent_window": window, "resources": {"compute": 4},
    })
    for spec in (direct, loaded):
        assert spec.resources.sim_license == window
        assert spec.resources.formal_license == window
    explicit = CampaignSpec.from_dict(loaded.to_dict() | {
        "resources": {"compute": 4, "sim_license": 0},
    })
    assert explicit.resources.sim_license == 0
    assert explicit.resources.formal_license == window


def test_periodic_exploration_does_not_override_normalized_score():
    a, b = job("a"), job("b", "crv-2", 1)
    table = YieldTable(["crv-1", "crv-2"])
    table.record(a, result(a), MergeOutcome(True, "ok", ("a",)))
    for point in ("b", "c"):
        table.record(b, result(b, .1), MergeOutcome(True, "ok", (point,)))
    assert table.row("crv-1").completed_jobs < table.row("crv-2").completed_jobs
    assert AdaptivePolicy().choose([a, b], table, 9).job == b


@pytest.mark.parametrize("extra,expected", [([], (3, 3)), (["--sim-licenses", "2", "--formal-licenses", "2"], (2, 2))])
def test_cli_license_defaults_use_overridden_k(tmp_path, extra, expected):
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    smoke = root / "experiments/coverage_growth/smoke"
    subprocess.run([
        sys.executable, str(root / "run_chialoop.py"), "run",
        "--campaign", str(smoke / "campaign.json"),
        "--coverage", str(smoke / "coverage.json"),
        "--catalog", str(smoke / "catalog.jsonl"),
        "--executor", "adapters.mediumboom.smoke_executor:execute",
        "--arm", "adaptive", "--runtime", "local", "--trial-id", "licenses",
        "--out", str(tmp_path), "--k", "3", *extra,
    ], cwd=root, check=True, capture_output=True, text=True)
    spec = json.loads((tmp_path / "campaign_spec.json").read_text())
    assert spec["recent_window"] == 3
    assert (spec["resources"]["sim_license"], spec["resources"]["formal_license"]) == expected
