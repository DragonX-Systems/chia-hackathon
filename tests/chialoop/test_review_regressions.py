"""Regression cases found by reviewing the node scheduler, beyond happy paths."""
from dataclasses import replace
import json

import pytest

from chialoop import (AdaptivePolicy, CampaignRunner, CampaignSpec, CoveragePoint,
                      Engine, JobCatalog, JobResult, JobSpec, JobStatus, ProofStatus,
                      ResourceCapacity)
from chialoop.core.resources import ResourceLedger
from chialoop.core.types import MergeOutcome
from chialoop.core.yields import YieldTable
from chialoop.orchestration.runtime import CompletedTask, TaskHandle


def make_job(name, node="crv-1", sequence=0, **kwargs):
    return JobSpec(name, Engine.CRV, "p", sequence, ("bin",), name, 10, "rtl",
                   node_id=node, **kwargs)


def make_result(job):
    return JobResult(job.job_id, job.engine, JobStatus.SUCCESS, "", "", 0,
                     (), (), ProofStatus.NOT_APPLICABLE, "rtl", None)


class ClockRuntime:
    def __init__(self):
        self.now = 0.0
        self.actions = []

    def submit(self, job, executor):
        self.actions.append(("submit", job.job_id))
        return TaskHandle(job, None, self.now, 0)

    def wait_first(self, handles, *, timeout_seconds=None):
        fast = next((h for h in handles if h.job.job_id != "slow"), None)
        if fast is None:
            self.now += timeout_seconds or 0
            return []
        self.now += 11
        return [CompletedTask(fast, make_result(fast.job))]

    def consumed_compute_hours(self, handle, *, now=None):
        return 0

    def started_epoch_seconds(self, handle):
        return None

    def cancel(self, handle):
        self.actions.append(("cancel", handle.job.job_id))


def make_runner(jobs, runtime, tmp_path, policy=None, executor=None):
    return CampaignRunner(
        spec=CampaignSpec("test", "rtl", "asm", resources=ResourceCapacity(2, 2, 2)),
        catalog=JobCatalog(jobs), coverage_points=[CoveragePoint("bin", "p")],
        policy=policy or AdaptivePolicy(), runtime=runtime,
        executor=executor or (lambda job: None), trial_id="test", output_dir=tmp_path,
    )


def test_expired_job_cancelled_before_refill_despite_ready_completions(tmp_path, monkeypatch):
    runtime = ClockRuntime()
    monkeypatch.setattr("chialoop.orchestration.driver.time.monotonic", lambda: runtime.now)
    jobs = [make_job("slow"), make_job("fast-1", sequence=1),
            make_job("fast-2", sequence=2), make_job("fast-3", sequence=3)]
    jobs = [work if work.job_id == "slow" else replace(work, timeout_seconds=100) for work in jobs]
    summary = make_runner(jobs, runtime, tmp_path).run()
    assert runtime.actions.index(("cancel", "slow")) < runtime.actions.index(("submit", "fast-2"))
    assert summary.status_counts == {"success": 3, "timeout": 1}
    assert summary.completed_jobs == 4


@pytest.mark.parametrize("window", [1, 7, 20])
@pytest.mark.parametrize("licenses", [{}, {"sim_license": 0}, {"formal_license": 10},
                                      {"sim_license": 2, "formal_license": 3}])
def test_python_and_json_resource_defaults_agree(window, licenses):
    direct = CampaignSpec("test", "rtl", "asm", recent_window=window,
                          resources=ResourceCapacity(compute=3, **licenses))
    loaded = CampaignSpec.from_dict({"campaign_id": "test", "artifact_version": "rtl",
        "assumption_version": "asm", "recent_window": window,
        "resources": {"compute": 3, **licenses}})
    assert direct == loaded
    assert direct.resources.sim_license == licenses.get("sim_license", window)
    assert direct.resources.formal_license == licenses.get("formal_license", window)
    assert CampaignSpec.from_dict(direct.to_dict()) == direct
    assert ResourceLedger(direct.resources).snapshot()["capacity"] == direct.resources.to_dict()


class RenamedAdaptive(AdaptivePolicy):
    name = "adaptive_variant"


@pytest.mark.parametrize("policy", [AdaptivePolicy(), RenamedAdaptive()])
def test_policy_name_does_not_change_probing(tmp_path, monkeypatch, policy):
    runtime = ClockRuntime()
    monkeypatch.setattr("chialoop.orchestration.driver.time.monotonic", lambda: runtime.now)
    jobs = [make_job("a"), make_job("b", sequence=1), make_job("c", "crv-2", 2)]
    make_runner(jobs, runtime, tmp_path, policy).run()
    assert runtime.actions[:2] == [("submit", "a"), ("submit", "c")]
    payload = json.loads((tmp_path / "policy.json").read_text())
    assert payload["initial_probe_per_node"] is True
    assert "compute-hours" in payload["score"]


def test_identical_formal_work_cannot_be_renamed_to_bypass_validation(tmp_path):
    first = replace(make_job("query-a"), engine=Engine.FORMAL, node_id="formal-1",
                    resources=None, assumption_version="asm",
                    payload={"sby_file": "query.sby", "required_goal": "goal"})
    second = replace(first, job_id="query-b", seed_or_query_id="query-b", sequence_in_lane=1)
    runtime = ClockRuntime()
    with pytest.raises(ValueError, match="execution|formal|duplicate"):
        make_runner([first, second], runtime, tmp_path)
    assert runtime.actions == []


def test_batch_completions_at_deadline_are_not_cancelled_twice(tmp_path, monkeypatch):
    class BatchRuntime(ClockRuntime):
        def wait_first(self, handles, *, timeout_seconds=None):
            self.now = 10
            return [CompletedTask(h, make_result(h.job)) for h in handles]
    runtime = BatchRuntime()
    monkeypatch.setattr("chialoop.orchestration.driver.time.monotonic", lambda: runtime.now)
    summary = make_runner([make_job("a"), make_job("b", sequence=1)], runtime, tmp_path).run()
    assert summary.status_counts == {"success": 2}
    assert all(action != "cancel" for action, _ in runtime.actions)


def test_empty_wait_expires_all_due_jobs_and_releases_tokens(tmp_path, monkeypatch):
    class EmptyRuntime(ClockRuntime):
        def wait_first(self, handles, *, timeout_seconds=None):
            self.now += timeout_seconds
            return []
    runtime = EmptyRuntime()
    monkeypatch.setattr("chialoop.orchestration.driver.time.monotonic", lambda: runtime.now)
    controller = make_runner([make_job("a"), make_job("b", sequence=1)], runtime, tmp_path)
    summary = controller.run()
    assert summary.status_counts == {"timeout": 2}
    assert controller.resources.available().compute == 2
    assert controller.resources.available().sim_license == 2
    assert runtime.actions[-2:] == [("cancel", "a"), ("cancel", "b")]


def test_failed_submission_releases_reservation_and_cancels_other_jobs(tmp_path):
    class FailingRuntime(ClockRuntime):
        def submit(self, job, executor):
            if job.job_id == "b":
                raise RuntimeError("submission failed")
            return super().submit(job, executor)
    runtime = FailingRuntime()
    controller = make_runner([make_job("a"), make_job("b", sequence=1)], runtime, tmp_path)
    with pytest.raises(RuntimeError, match="submission failed"):
        controller.run()
    assert controller.resources.used().compute == 0
    assert controller.resources.used().sim_license == 0
    assert runtime.actions[-1] == ("cancel", "a")


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "10", float("nan")])
def test_invalid_window_rejected_consistently(bad):
    with pytest.raises(ValueError, match="positive integer"):
        CampaignSpec("test", "rtl", "asm", recent_window=bad)
    with pytest.raises(ValueError, match="positive integer"):
        CampaignSpec.from_dict({"campaign_id": "test", "artifact_version": "rtl",
                               "assumption_version": "asm", "recent_window": bad})


@pytest.mark.parametrize("field", ["compute", "sim_license", "formal_license"])
@pytest.mark.parametrize("bad", [-1, True, 1.5, float("nan")])
def test_invalid_resource_counts_fail_at_configuration_boundary(field, bad):
    with pytest.raises(ValueError, match="integer"):
        ResourceCapacity(**{field: bad})


def test_probing_and_spare_capacity_rules_are_owned_by_policy():
    a, b = make_job("a"), make_job("b", "crv-2", 1)
    table = YieldTable(["crv-1", "crv-2"])
    policy = RenamedAdaptive()
    # Probe b while a has its first run in flight.
    assert policy.choose([a, b], table, 1, running_nodes=frozenset({"crv-1"})).job == b
    # Both initial probes are in flight: a further distinct job may still run.
    assert policy.choose([a, b], table, 2, running_nodes=frozenset({"crv-1", "crv-2"})).job == a
    table.record(b, replace(make_result(b), compute_hours=1), MergeOutcome(True, "ok", ("bin",)))
    # Prefer observed b to repeating a pending initial probe, even if a sorts first.
    assert policy.choose([a, b], table, 3, running_nodes=frozenset({"crv-1"})).job == b


def test_executor_fingerprint_hook_catches_renaming(tmp_path):
    def execute(job):
        raise AssertionError("validation must precede execution")
    execute.execution_fingerprint = lambda job: "same-actual-work"
    with pytest.raises(ValueError, match="reuses execution"):
        make_runner([make_job("a"), make_job("b", sequence=1)], ClockRuntime(), tmp_path,
                    executor=execute)


@pytest.mark.parametrize("fingerprint", [None, "", 123])
def test_invalid_adapter_fingerprints_fail_closed(tmp_path, fingerprint):
    def execute(job):
        raise AssertionError("must not execute")
    execute.execution_fingerprint = lambda job: fingerprint
    with pytest.raises(ValueError, match="invalid execution fingerprint"):
        make_runner([make_job("a")], ClockRuntime(), tmp_path, executor=execute)
