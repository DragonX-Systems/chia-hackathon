from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
import time

import pytest

from chialoop import (
    AdaptivePolicy,
    CampaignRunner,
    CampaignSpec,
    CoveragePoint,
    Engine,
    EngineEvidence,
    JobCatalog,
    JobSpec,
    JobStatus,
    LocalAsyncRuntime,
    ProofStatus,
    ResourceCapacity,
    StaticHybridPolicy,
    compare_paired_runs,
    write_comparison,
)


def _fixture_catalog() -> tuple[list[CoveragePoint], JobCatalog]:
    points = [CoveragePoint(f"p{i}", str(i)) for i in range(1, 6)]
    jobs = []
    for partition in map(str, range(1, 6)):
        target = f"p{partition}"
        jobs.extend(
            [
                JobSpec(
                    job_id=f"c-{partition}",
                    engine=Engine.CRV,
                    node_id=f"crv-{partition}",
                    partition_id=partition,
                    sequence_in_lane=0,
                    target_point_ids=(target,),
                    seed_or_query_id=f"seed-{partition}",
                    timeout_seconds=60,
                    artifact_version="rtl-v1",
                ),
                JobSpec(
                    job_id=f"d-{partition}",
                    engine=Engine.DIRECTED,
                    partition_id=partition,
                    sequence_in_lane=0,
                    target_point_ids=(target,),
                    seed_or_query_id=f"test-{partition}",
                    timeout_seconds=60,
                    artifact_version="rtl-v1",
                ),
                JobSpec(
                    job_id=f"f-{partition}",
                    engine=Engine.FORMAL,
                    partition_id=partition,
                    sequence_in_lane=0,
                    target_point_ids=(target,),
                    seed_or_query_id=f"query-{partition}",
                    timeout_seconds=60,
                    artifact_version="rtl-v1",
                    assumption_version="asm-v1",
                ),
            ]
        )
    return points, JobCatalog(jobs)


def _executor(job: JobSpec) -> EngineEvidence:
    # Different sleeps make completion order differ from dispatch order.
    time.sleep(0.003 if job.engine is Engine.FORMAL else 0.006)
    if job.engine is Engine.CRV:
        return EngineEvidence(
            hit_point_ids=job.target_point_ids,
            artifact_version=job.artifact_version,
            compute_hours=0.1,
            notes="registered test executor",
        )
    if job.engine is Engine.FORMAL:
        return EngineEvidence(
            status=JobStatus.UNKNOWN,
            proof_status=ProofStatus.UNKNOWN,
            artifact_version=job.artifact_version,
            assumption_version="asm-v1",
            compute_hours=0.05,
        )
    return EngineEvidence(
        artifact_version=job.artifact_version,
        compute_hours=0.2,
    )


def _slow_executor(job: JobSpec) -> EngineEvidence:
    time.sleep(2.0)
    return EngineEvidence(
        hit_point_ids=job.target_point_ids,
        artifact_version=job.artifact_version,
        assumption_version=job.assumption_version,
        proof_status=(
            ProofStatus.REACHED
            if job.engine is Engine.FORMAL
            else ProofStatus.NOT_APPLICABLE
        ),
    )


def _spec() -> CampaignSpec:
    return CampaignSpec(
        campaign_id="test-campaign",
        artifact_version="rtl-v1",
        assumption_version="asm-v1",
        target_closure=1.0,
        budget_compute_hours=20.0,
        resources=ResourceCapacity(compute=2, sim_license=1, formal_license=1),
    )


def test_campaign_is_async_and_writes_reconstructable_outputs(tmp_path):
    points, catalog = _fixture_catalog()
    runtime = LocalAsyncRuntime(max_workers=2)
    try:
        summary = CampaignRunner(
            spec=_spec(),
            catalog=catalog,
            coverage_points=points,
            policy=AdaptivePolicy(),
            runtime=runtime,
            executor=_executor,
            trial_id="trial-1",
            output_dir=tmp_path,
        ).run()
    finally:
        runtime.close()

    assert summary.reached_target
    assert summary.final_sound_closure == 1.0
    assert summary.compute95 is not None
    assert summary.coverage_trace[-1]["compute_hours"] == pytest.approx(
        summary.compute95,
        rel=1e-3,
    )
    assert (tmp_path / "campaign_spec.json").exists()
    assert (tmp_path / "job_catalog.jsonl").exists()
    assert (tmp_path / "policy.json").exists()
    assert (tmp_path / "events.jsonl").exists()
    assert (tmp_path / "summary.json").exists()

    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    dispatched = [event for event in events if event["event"] == "job_dispatched"]
    completed = [event for event in events if event["event"] == "job_completed"]
    assert {event["compute_slot"] for event in dispatched} == {1, 2}
    first_completion = next(i for i, event in enumerate(events) if event["event"] == "job_completed")
    assert sum(event["event"] == "job_dispatched" for event in events[:first_completion]) >= 2
    assert any(event["engine"] == "formal" for event in completed)
    assert events[-1]["event"] == "campaign_finished"


def test_campaign_resume_replays_prior_evidence_without_rerunning_jobs(tmp_path):
    points, catalog = _fixture_catalog()
    spec = CampaignSpec(
        campaign_id="test-campaign", artifact_version="rtl-v1",
        assumption_version="asm-v1", target_closure=1.0,
        budget_compute_hours=20.0,
        resources=ResourceCapacity(compute=1, sim_license=1, formal_license=1),
    )
    first_dir = tmp_path / "first"
    runtime = LocalAsyncRuntime(max_workers=2)
    try:
        first = CampaignRunner(
            spec=spec, catalog=catalog, coverage_points=points,
            policy=AdaptivePolicy(), runtime=runtime, executor=_executor,
            trial_id="resume-test", output_dir=first_dir,
        ).run()
    finally:
        runtime.close()
    prior_events = [
        json.loads(line) for line in (first_dir / "events.jsonl").read_text().splitlines()
    ]

    def must_not_execute(_job):
        raise AssertionError("restored terminal campaign dispatched work again")

    runtime = LocalAsyncRuntime(max_workers=2)
    try:
        resumed = CampaignRunner(
            spec=spec, catalog=catalog, coverage_points=points,
            policy=AdaptivePolicy(), runtime=runtime, executor=must_not_execute,
            trial_id="resume-test", output_dir=tmp_path / "resumed",
            resume_events=prior_events,
        ).run()
    finally:
        runtime.close()

    assert resumed.completed_jobs == first.completed_jobs
    assert resumed.final_hit_points == first.final_hit_points
    assert resumed.total_compute_hours == pytest.approx(first.total_compute_hours)
    assert resumed.coverage_trace == first.coverage_trace


def test_campaign_resume_replays_multiple_interrupted_segments(tmp_path):
    points, catalog = _fixture_catalog()
    spec = CampaignSpec(
        campaign_id="test-campaign", artifact_version="rtl-v1",
        assumption_version="asm-v1", target_closure=1.0,
        budget_compute_hours=20.0,
        resources=ResourceCapacity(compute=1, sim_license=1, formal_license=1),
    )
    first_dir = tmp_path / "first"
    runtime = LocalAsyncRuntime(max_workers=2)
    try:
        first = CampaignRunner(
            spec=spec, catalog=catalog, coverage_points=points,
            policy=AdaptivePolicy(), runtime=runtime, executor=_executor,
            trial_id="resume-test", output_dir=first_dir,
        ).run()
    finally:
        runtime.close()
    events = [json.loads(line) for line in (first_dir / "events.jsonl").read_text().splitlines()]
    split = next(i for i, event in enumerate(events) if event["event"] == "job_completed")
    elapsed_at_split = events[split]["elapsed_seconds"]
    second_start = dict(events[0], timestamp=events[split]["timestamp"], elapsed_seconds=0.0)
    later_events = [
        dict(event, elapsed_seconds=max(0.0, event["elapsed_seconds"] - elapsed_at_split))
        for event in events[split + 1:]
    ]
    segmented_events = events[:split + 1] + [second_start] + later_events

    def must_not_execute(_job):
        raise AssertionError("multi-segment resume dispatched completed work")

    runtime = LocalAsyncRuntime(max_workers=2)
    try:
        resumed = CampaignRunner(
            spec=spec, catalog=catalog, coverage_points=points,
            policy=AdaptivePolicy(), runtime=runtime, executor=must_not_execute,
            trial_id="resume-test", output_dir=tmp_path / "resumed",
            resume_events=segmented_events,
        ).run()
    finally:
        runtime.close()

    assert resumed.completed_jobs == first.completed_jobs
    assert resumed.total_compute_hours == pytest.approx(first.total_compute_hours)
    assert resumed.coverage_trace == first.coverage_trace
    assert resumed.wall_time_hours >= first.wall_time_hours * 0.9


@pytest.mark.skipif(not hasattr(os, "setsid"), reason="process groups require POSIX")
def test_local_runtime_cancel_stops_simulator_child():
    child_pid = multiprocessing.Value("i", 0)

    def launch_child(_job):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        child_pid.value = child.pid
        child.wait()
        raise RuntimeError("cancelled worker returned unexpectedly")

    _, catalog = _fixture_catalog()
    runtime = LocalAsyncRuntime(max_workers=1)
    handle = runtime.submit(catalog.jobs[0], launch_child)
    try:
        for _ in range(250):
            if child_pid.value:
                break
            time.sleep(0.02)
        assert child_pid.value, "simulator-like subprocess did not start"
        runtime.cancel(handle)
        for _ in range(250):
            try:
                os.kill(child_pid.value, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            pytest.fail("subprocess remained alive after local worker cancellation")
    finally:
        runtime.close()
        if child_pid.value:
            try:
                os.kill(child_pid.value, 9)
            except ProcessLookupError:
                pass


def test_static_and_adaptive_summaries_are_pair_comparable(tmp_path):
    points, catalog = _fixture_catalog()
    summaries = {}
    for name, policy in (
        ("static", StaticHybridPolicy(catalog)),
        ("adaptive", AdaptivePolicy()),
    ):
        runtime = LocalAsyncRuntime(max_workers=2)
        try:
            summaries[name] = CampaignRunner(
                spec=_spec(),
                catalog=catalog,
                coverage_points=points,
                policy=policy,
                runtime=runtime,
                executor=_executor,
                trial_id="paired-1",
                output_dir=tmp_path / name,
            ).run().to_dict()
        finally:
            runtime.close()

    assert (tmp_path / "static" / "static_manifest.json").exists()

    comparison = compare_paired_runs([summaries["static"]], [summaries["adaptive"]], bootstrap_samples=100)
    assert comparison["paired_trials"] == 1
    assert comparison["primary_comparison_valid"]
    write_comparison(comparison, tmp_path / "comparison")
    assert (tmp_path / "comparison" / "comparison.json").exists()
    assert (tmp_path / "comparison" / "comparison.md").exists()
    assert (tmp_path / "comparison" / "coverage_vs_compute_hours.svg").exists()
    assert (tmp_path / "comparison" / "resource_timeline.svg").exists()


def test_comparator_rejects_mismatched_campaign_fingerprints():
    common = {
        "trial_id": "paired-1",
        "runtime_name": "ChiaAsyncRuntime",
        "reached_target": True,
        "compute95": 1.0,
        "final_sound_closure": 0.95,
    }
    static = common | {
        "arm": "static_hybrid",
        "campaign_fingerprint": "campaign-a",
    }
    adaptive = common | {
        "arm": "adaptive",
        "campaign_fingerprint": "campaign-b",
    }

    with pytest.raises(ValueError, match="different campaign_fingerprint"):
        compare_paired_runs([static], [adaptive], bootstrap_samples=10)


def test_comparator_rejects_duplicate_trial_ids():
    run = {
        "trial_id": "duplicate",
        "runtime_name": "ChiaAsyncRuntime",
        "reached_target": True,
        "compute95": 1.0,
        "final_sound_closure": 0.95,
        "arm": "static_hybrid",
        "campaign_fingerprint": "campaign-a",
    }
    adaptive = run | {"arm": "adaptive"}

    with pytest.raises(ValueError, match="duplicate static_hybrid trial ID"):
        compare_paired_runs([run, run], [adaptive], bootstrap_samples=10)


def test_campaign_hard_cancels_jobs_at_their_deadline():
    points, original = _fixture_catalog()
    jobs = [
        JobSpec.from_dict(job.to_dict() | {"timeout_seconds": 0.02})
        for job in original.jobs
    ]
    runtime = LocalAsyncRuntime(max_workers=2)
    started = time.perf_counter()
    try:
        summary = CampaignRunner(
            spec=_spec(),
            catalog=JobCatalog(jobs),
            coverage_points=points,
            policy=AdaptivePolicy(),
            runtime=runtime,
            executor=_slow_executor,
            trial_id="timeouts",
        ).run()
    finally:
        runtime.close()

    assert time.perf_counter() - started < 1.5
    assert summary.status_counts == {"timeout": len(jobs)}
    assert summary.final_sound_closure == 0.0
