"""The trace shows the scheduler's handoffs, choices, and node gains."""

import json
import time

from chialoop import (
    AdaptivePolicy, CampaignRunner, CampaignSpec, CoveragePoint, Engine,
    JobCatalog, JobResult, JobSpec, JobStatus, ProofStatus, ResourceCapacity,
)
from chialoop.orchestration.runtime import CompletedTask, TaskHandle


class ReadyTogetherRuntime:
    def __init__(self):
        self.waited = False

    def submit(self, job, executor):
        return TaskHandle(job, None, time.monotonic(), time.time())

    def wait_first(self, handles, *, timeout_seconds=None):
        assert not self.waited
        self.waited = True
        return [CompletedTask(handle, JobResult(
            handle.job.job_id, handle.job.engine, JobStatus.SUCCESS,
            "", "", 1, handle.job.target_point_ids, (),
            ProofStatus.NOT_APPLICABLE, "rtl", None,
        )) for handle in handles]

    def consumed_compute_hours(self, handle, *, now=None):
        return 1 if self.waited else 0

    def started_epoch_seconds(self, handle):
        return None

    def cancel(self, handle):
        raise AssertionError("all results were delivered")


def test_trace_orders_handoff_selection_and_per_node_gains(tmp_path):
    jobs = [JobSpec(name, Engine.CRV, "p", index, (name,), name, 60, "rtl",
                    node_id=node)
            for index, (name, node) in enumerate((
                ("a", "crv-1"), ("b", "crv-2"), ("c", "crv-1")))]
    CampaignRunner(
        spec=CampaignSpec("trace", "rtl", "asm", target_closure=1,
                          resources=ResourceCapacity(3, 3, 0), n1=2, n2=0, n3=0),
        catalog=JobCatalog(jobs),
        coverage_points=[CoveragePoint(name, "p") for name in ("a", "b", "c")],
        policy=AdaptivePolicy(), runtime=ReadyTogetherRuntime(),
        executor=lambda job: None, trial_id="trace-test", output_dir=tmp_path,
    ).run()

    records = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert [record["sequence"] for record in records] == list(range(1, len(records) + 1))
    elapsed = [record["campaign_elapsed_seconds"] for record in records]
    assert elapsed == sorted(elapsed)
    assert all(record["trial_id"] == "trace-test" and "timestamp" in record
               for record in records)
    selections = [record for record in records if record["event"] == "node_selected"]
    assert [record["job_id"] for record in selections] == ["a", "b", "c"]
    assert [record["node_id"] for record in selections] == ["crv-1", "crv-2", "crv-1"]
    assert all(record["reason"] for record in selections)
    assert selections[0]["candidate_count"] == 3
    assert set(selections[0]["candidate_node_scores"]) == {"crv-1", "crv-2"}
    assert selections[0]["candidate_node_scores"]["crv-1"]["cumulative_new_bins"] == 0
    delivery = next(record for record in records if record["event"] == "results_delivered")
    assert delivery["job_ids"] == ["a", "b", "c"]
    gains = [record for record in records if record["event"] == "result_processed"]
    assert [record["node_cumulative_new_bins"] for record in gains] == [1, 1, 2]
    assert [record["campaign_cumulative_closed_bins"] for record in gains] == [1, 2, 3]
    assert delivery["sequence"] < gains[0]["sequence"] < gains[1]["sequence"] < gains[2]["sequence"]
    stop = next(record for record in records if record["event"] == "stop_decision")
    assert stop["reason"] == "target_reached"
    assert gains[-1]["sequence"] < stop["sequence"]


def test_trace_records_coverage_based_skip_decision(tmp_path):
    jobs = [JobSpec(name, Engine.CRV, "p", index, ("a",), name, 60, "rtl")
            for index, name in enumerate(("first", "redundant"))]
    CampaignRunner(
        spec=CampaignSpec("trace", "rtl", "asm", target_closure=1,
                          resources=ResourceCapacity(1, 1, 0), n1=1, n2=0, n3=0),
        catalog=JobCatalog(jobs),
        coverage_points=[CoveragePoint("a", "p"), CoveragePoint("b", "p")],
        policy=AdaptivePolicy(), runtime=ReadyTogetherRuntime(),
        executor=lambda job: None, trial_id="trace-skip", output_dir=tmp_path,
    ).run()

    records = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    skip = next(record for record in records if record["event"] == "job_skipped")
    assert skip["job_id"] == "redundant"
    assert skip["node_id"] == "crv-1"
    assert records[-2]["event"] == "stop_decision"
    assert records[-2]["reason"] == "catalog_exhausted"
