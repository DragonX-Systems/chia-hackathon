"""Regression tests for result accounting at the campaign boundary."""

from dataclasses import replace
import json
import time
from types import SimpleNamespace

import pytest

from chialoop import (
    AdaptivePolicy, CampaignRunner, CampaignSpec, CoveragePoint, Engine,
    EngineEvidence, JobCatalog, JobResult, JobSpec, JobStatus, ProofStatus,
    ResourceCapacity,
)
from chialoop.orchestration.runtime import ChiaAsyncRuntime, CompletedTask, TaskHandle


def _job(job_id, point, sequence):
    return JobSpec(job_id, Engine.CRV, "p", sequence, (point,), job_id,
                   60, "rtl", node_id="crv-1")


def _result(job, *, engine=None, point=None, hours=1.0):
    return JobResult(job.job_id, engine or job.engine, JobStatus.SUCCESS,
                     "", "", hours, (point,) if point else (), (),
                     ProofStatus.NOT_APPLICABLE, "rtl", None)


class BatchRuntime:
    """All submitted jobs finish before wait_first returns."""

    def __init__(self, result_for):
        self.result_for = result_for
        self.cancelled = []
        self.waits = 0

    def submit(self, job, executor):
        return TaskHandle(job, None, time.monotonic(), time.time())

    def wait_first(self, handles, *, timeout_seconds=None):
        self.waits += 1
        assert self.waits == 1
        return [CompletedTask(handle, self.result_for(handle.job)) for handle in handles]

    def consumed_compute_hours(self, handle, *, now=None):
        return self.result_for(handle.job).compute_hours if self.waits else 0.0

    def started_epoch_seconds(self, handle):
        return None

    def cancel(self, handle):
        self.cancelled.append(handle.job.job_id)


def test_chia_runtime_drains_all_ready_completions_before_dispatch():
    jobs = [_job(name, name, index) for index, name in enumerate(("a", "b", "c"))]
    handles = [TaskHandle(job, SimpleNamespace(ref=job.job_id), 0, 0) for job in jobs]
    calls = []
    cleared = []

    def fake_wait(tracked, *, num_returns, timeout, retry):
        calls.append((num_returns, timeout, retry))
        if len(calls) == 1:
            return tracked[:1], tracked[1:]
        return tracked, []

    runtime = ChiaAsyncRuntime.__new__(ChiaAsyncRuntime)
    runtime._chia_wait = fake_wait
    runtime._get = lambda ref: _result(next(job for job in jobs if job.job_id == ref))
    runtime._start_registry = SimpleNamespace(
        clear=SimpleNamespace(remote=lambda job_id: cleared.append(job_id)))

    completed = runtime.wait_first(handles, timeout_seconds=5)

    assert [item.handle.job.job_id for item in completed] == ["a", "b", "c"]
    assert calls == [(1, 5, False), (2, 0, False)]
    assert cleared == ["a", "b", "c"]


def _runner(tmp_path, jobs, points, runtime, *, target=.5, budget=100.0):
    return CampaignRunner(
        spec=CampaignSpec("results", "rtl", "asm", target_closure=target,
                          budget_compute_hours=budget,
                          resources=ResourceCapacity(2, 2, 1)),
        catalog=JobCatalog(jobs), coverage_points=points,
        policy=AdaptivePolicy(), runtime=runtime,
        executor=lambda job: None, trial_id="one", output_dir=tmp_path,
    )


def test_all_ready_results_are_accounted_after_target_crossing(tmp_path):
    jobs = [_job("a", "a", 0), _job("b", "b", 1)]
    runtime = BatchRuntime(lambda job: _result(job, point=job.target_point_ids[0]))

    summary = _runner(tmp_path, jobs,
                      [CoveragePoint("a", "p"), CoveragePoint("b", "p")],
                      runtime).run()

    assert summary.compute95 == pytest.approx(2.0)
    assert summary.completed_jobs == 2
    assert summary.final_hit_points == 2
    assert summary.total_compute_hours == pytest.approx(2.0)
    assert summary.zero_yield_compute_hours == 0
    assert runtime.cancelled == []
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert sum(event["event"] == "job_completed" for event in events) == 2


def test_all_ready_results_are_accounted_after_budget_crossing(tmp_path):
    jobs = [_job("a", "a", 0), _job("b", "b", 1)]
    runtime = BatchRuntime(lambda job: _result(job))

    summary = _runner(tmp_path, jobs,
                      [CoveragePoint("a", "p"), CoveragePoint("b", "p")],
                      runtime, budget=1.0).run()

    assert summary.stop_reason == "budget_exhausted"
    assert summary.completed_jobs == 2
    assert summary.total_compute_hours == pytest.approx(2.0)
    assert runtime.cancelled == []


def test_result_ready_during_terminal_processing_is_drained(tmp_path):
    class LaterReadyRuntime(BatchRuntime):
        def wait_first(self, handles, *, timeout_seconds=None):
            self.waits += 1
            if self.waits == 1:
                return [CompletedTask(handles[0], self.result_for(handles[0].job))]
            assert timeout_seconds == 0
            return [CompletedTask(handle, self.result_for(handle.job)) for handle in handles]

    jobs = [_job("a", "a", 0), _job("b", "b", 1)]
    runtime = LaterReadyRuntime(lambda job: _result(job, point=job.target_point_ids[0]))
    summary = _runner(tmp_path, jobs,
                      [CoveragePoint("a", "p"), CoveragePoint("b", "p")],
                      runtime).run()

    assert runtime.waits == 2
    assert summary.completed_jobs == 2
    assert summary.final_hit_points == 2
    assert runtime.cancelled == []
    trace = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert [record["source"] for record in trace
            if record["event"] == "results_delivered"] == ["runtime_wait", "terminal_drain"]


def test_resume_preserves_compute_at_target_with_concurrent_jobs(tmp_path):
    jobs = [_job("a", "a", 0), _job("b", "b", 1)]
    points = [CoveragePoint("a", "p"), CoveragePoint("b", "p")]
    original = _runner(tmp_path / "first", jobs, points,
                       BatchRuntime(lambda job: _result(job, point=job.target_point_ids[0]))).run()
    events = [json.loads(line) for line in
              (tmp_path / "first" / "events.jsonl").read_text().splitlines()]
    completed = [event for event in events if event["event"] == "job_completed"]
    assert completed[0]["cumulative_completed_compute_hours"] == pytest.approx(1.0)
    assert original.compute95 == pytest.approx(2.0)

    resumed = CampaignRunner(
        spec=CampaignSpec("results", "rtl", "asm", target_closure=.5,
                          resources=ResourceCapacity(2, 2, 1)),
        catalog=JobCatalog(jobs), coverage_points=points, policy=AdaptivePolicy(),
        runtime=BatchRuntime(lambda job: _result(job)),
        executor=lambda job: None, trial_id="one", resume_events=events,
    ).run()

    assert resumed.compute95 == original.compute95
    assert resumed.coverage_trace == original.coverage_trace


def test_resume_uses_target_event_for_older_journals(tmp_path):
    jobs = [_job("a", "a", 0), _job("b", "b", 1)]
    points = [CoveragePoint("a", "p"), CoveragePoint("b", "p")]
    original = _runner(tmp_path / "first", jobs, points,
                       BatchRuntime(lambda job: _result(job, point=job.target_point_ids[0]))).run()
    events = [json.loads(line) for line in
              (tmp_path / "first" / "events.jsonl").read_text().splitlines()]
    for event in events:
        event.pop("cumulative_compute_hours", None)

    resumed = CampaignRunner(
        spec=CampaignSpec("results", "rtl", "asm", target_closure=.5,
                          resources=ResourceCapacity(2, 2, 1)),
        catalog=JobCatalog(jobs), coverage_points=points, policy=AdaptivePolicy(),
        runtime=BatchRuntime(lambda job: _result(job)),
        executor=lambda job: None, trial_id="one", resume_events=events,
    ).run()

    assert resumed.compute95 == original.compute95
    assert resumed.coverage_trace == original.coverage_trace


def test_resume_keeps_compute_spent_on_cancelled_inflight_job(tmp_path):
    class OneReadyRuntime(BatchRuntime):
        def wait_first(self, handles, *, timeout_seconds=None):
            self.waits += 1
            if self.waits == 1:
                return [CompletedTask(handles[0], self.result_for(handles[0].job))]
            assert timeout_seconds == 0
            return []

    jobs = [_job("a", "a", 0), _job("b", "b", 1)]
    points = [CoveragePoint("a", "p"), CoveragePoint("b", "p")]
    original_runtime = OneReadyRuntime(
        lambda job: _result(job, point=job.target_point_ids[0]))
    original = _runner(tmp_path / "first", jobs, points, original_runtime).run()
    assert original_runtime.cancelled == ["b"]
    assert original.total_compute_hours == pytest.approx(2.0)
    events = [json.loads(line) for line in
              (tmp_path / "first" / "events.jsonl").read_text().splitlines()]

    resumed = CampaignRunner(
        spec=CampaignSpec("results", "rtl", "asm", target_closure=.5,
                          resources=ResourceCapacity(2, 2, 1)),
        catalog=JobCatalog(jobs), coverage_points=points, policy=AdaptivePolicy(),
        runtime=BatchRuntime(lambda job: _result(job)),
        executor=lambda job: None, trial_id="one", resume_events=events,
    ).run()

    assert resumed.total_compute_hours == pytest.approx(original.total_compute_hours)
    assert resumed.zero_yield_compute_hours == pytest.approx(original.zero_yield_compute_hours)
    assert resumed.compute_hours_by_engine == original.compute_hours_by_engine
    assert resumed.resource_timeline == original.resource_timeline


def test_rejected_wrong_engine_result_is_charged_to_dispatched_engine(tmp_path):
    job = _job("a", "a", 0)
    runtime = BatchRuntime(lambda work: _result(work, engine=Engine.FORMAL, point="a", hours=2))

    summary = _runner(tmp_path, [job], [CoveragePoint("a", "p")], runtime).run()

    assert summary.rejected_results == 1
    assert summary.final_hit_points == 0
    assert summary.compute_hours_by_engine == {"crv": 2, "directed": 0, "formal": 0}
    assert summary.sim_license_hours == 2
    assert summary.formal_license_hours == 0
    assert summary.coverage_trace[-1]["engine"] == "crv"
    assert summary.resource_timeline[-1]["engine"] == "crv"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_campaign_budget_and_job_timeout_are_rejected(bad):
    with pytest.raises(ValueError, match="budget_compute_hours"):
        CampaignSpec("results", "rtl", "asm", budget_compute_hours=bad)
    with pytest.raises(ValueError, match="timeout_seconds"):
        replace(_job("a", "a", 0), timeout_seconds=bad)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_reported_compute_time_is_rejected(bad):
    with pytest.raises(ValueError, match="compute_hours"):
        EngineEvidence(compute_hours=bad)
    with pytest.raises(ValueError, match="compute_hours"):
        _result(_job("a", "a", 0), hours=bad)
