from __future__ import annotations

import json
import time

from chialoop.core.catalog import JobCatalog
from chialoop.core.coverage import CoverageState
from chialoop.core.resources import ResourceLedger
from chialoop.core.types import (
    CoveragePoint,
    Engine,
    EngineEvidence,
    JobResult,
    JobSpec,
    JobStatus,
    MergeOutcome,
    ProofStatus,
    ResourceCapacity,
    ResourceRequest,
)
from chialoop.core.yields import YieldTable
from chialoop.orchestration.nodes import run_crv, run_formal
from chialoop.orchestration.policies import AdaptivePolicy, StaticHybridPolicy


def _job(
    job_id: str,
    engine: Engine,
    partition: str,
    sequence: int,
    target: str,
) -> JobSpec:
    return JobSpec(
        job_id=job_id,
        engine=engine,
        partition_id=partition,
        sequence_in_lane=sequence,
        target_point_ids=(target,),
        seed_or_query_id=job_id,
        timeout_seconds=60,
        artifact_version="rtl-v1",
        assumption_version="asm-v1" if engine is Engine.FORMAL else None,
    )


def _result(
    job: JobSpec,
    *,
    hits: tuple[str, ...] = (),
    unreachable: tuple[str, ...] = (),
    proof: ProofStatus = ProofStatus.NOT_APPLICABLE,
    assumption: str | None = None,
    hours: float = 1.0,
) -> JobResult:
    return JobResult(
        job_id=job.job_id,
        engine=job.engine,
        status=JobStatus.SUCCESS,
        start_time="start",
        end_time="end",
        compute_hours=hours,
        hit_point_ids=hits,
        proven_unreachable_point_ids=unreachable,
        proof_status=proof,
        artifact_version="rtl-v1",
        assumption_version=assumption,
    )


def test_catalog_round_trip_shares_repeated_target_registry(tmp_path):
    targets = tuple(f"toggle:{index}" for index in range(100))
    target_file = tmp_path / "coverage_points.json"
    target_file.write_text(json.dumps({"points": [{"point_id": point} for point in targets]}))
    jobs = [
        JobSpec("crv0", Engine.CRV, "p_frontend", 0, targets, "elf:a", 60, "rtl-v1"),
        JobSpec("directed0", Engine.DIRECTED, "p_issue", 0, targets, "elf:b", 60, "rtl-v1"),
    ]
    path = tmp_path / "catalog.jsonl"
    catalog = JobCatalog(jobs, shared_target_file=target_file)
    catalog.write_jsonl(path)

    records = path.read_text().splitlines()
    assert len(records) == 3
    assert '"__chialoop_catalog__": "shared-target-file-v1"' in records[0]
    restored = JobCatalog.read_jsonl(path)
    assert [job.to_dict() for job in restored.jobs] == [job.to_dict() for job in catalog.jobs]

    nested = tmp_path / "trial" / "adaptive" / "catalog.jsonl"
    restored.write_jsonl(nested)
    relocated = JobCatalog.read_jsonl(nested)
    assert [job.to_dict() for job in relocated.jobs] == [job.to_dict() for job in catalog.jobs]


def test_homogeneous_resource_ledger_enforces_shared_sim_license():
    ledger = ResourceLedger(ResourceCapacity(compute=2, sim_license=1, formal_license=1))
    crv = ResourceRequest.for_engine(Engine.CRV)
    directed = ResourceRequest.for_engine(Engine.DIRECTED)
    formal = ResourceRequest.for_engine(Engine.FORMAL)

    first = ledger.reserve("crv", crv)
    assert first.compute_slot == 1
    assert not ledger.can_fit(directed)
    assert ledger.can_fit(formal)
    second = ledger.reserve("formal", formal)
    assert second.compute_slot == 2
    assert ledger.available().compute == 0

    ledger.release("crv")
    assert ledger.can_fit(directed)


def test_coverage_merger_accepts_only_sound_formal_unreachability():
    state = CoverageState(
        [CoveragePoint(f"p{i}", str(i)) for i in range(1, 6)],
        artifact_version="rtl-v1",
        assumption_version="asm-v1",
    )
    formal = _job("f1", Engine.FORMAL, "1", 0, "p1")
    good = _result(
        formal,
        unreachable=("p1",),
        proof=ProofStatus.PROVEN_UNREACHABLE,
        assumption="asm-v1",
    )
    merged = state.merge(formal, good)
    assert merged.accepted
    assert merged.new_proven_unreachable_ids == ("p1",)

    bad = _result(
        _job("f2", Engine.FORMAL, "2", 0, "p2"),
        unreachable=("p2",),
        proof=ProofStatus.UNKNOWN,
        assumption="asm-v1",
    )
    rejected = state.merge(_job("f2", Engine.FORMAL, "2", 0, "p2"), bad)
    assert not rejected.accepted
    assert state.snapshot().open_points == 4

    reached_job = _job("f3", Engine.FORMAL, "3", 0, "p3")
    unsound_hit = _result(
        reached_job,
        hits=("p3",),
        proof=ProofStatus.NOT_APPLICABLE,
        assumption="asm-v1",
    )
    assert not state.merge(reached_job, unsound_hit).accepted


def test_formal_evidence_cannot_close_a_point_outside_the_query_targets():
    state = CoverageState(
        [CoveragePoint(f"p{i}", str(i)) for i in range(1, 6)],
        artifact_version="rtl-v1",
        assumption_version="asm-v1",
    )
    formal = _job("f1", Engine.FORMAL, "1", 0, "p1")
    wrong_point = _result(
        formal,
        unreachable=("p2",),
        proof=ProofStatus.PROVEN_UNREACHABLE,
        assumption="asm-v1",
    )

    merged = state.merge(formal, wrong_point)

    assert not merged.accepted
    assert "outside the dispatched job targets" in merged.reason
    assert state.snapshot().open_points == 5


def test_node_does_not_synthesize_missing_provenance():
    formal = _job("f1", Engine.FORMAL, "1", 0, "p1")
    execute = getattr(run_formal, "_chia_original", run_formal)
    result = execute(
        formal,
        lambda _job: EngineEvidence(
            proven_unreachable_point_ids=("p1",),
            proof_status=ProofStatus.PROVEN_UNREACHABLE,
            artifact_version="rtl-v1",
        ),
    )
    state = CoverageState(
        [CoveragePoint(f"p{i}", str(i)) for i in range(1, 6)],
        artifact_version="rtl-v1",
        assumption_version="asm-v1",
    )

    assert result.assumption_version is None
    assert not state.merge(formal, result).accepted


def test_node_discards_evidence_returned_after_timeout():
    job = JobSpec(
        job_id="slow-crv",
        engine=Engine.CRV,
        partition_id="1",
        sequence_in_lane=0,
        target_point_ids=("p1",),
        seed_or_query_id="seed",
        timeout_seconds=0.001,
        artifact_version="rtl-v1",
    )
    execute = getattr(run_crv, "_chia_original", run_crv)

    def slow_executor(_job: JobSpec) -> EngineEvidence:
        time.sleep(0.01)
        return EngineEvidence(
            hit_point_ids=("p1",),
            artifact_version="rtl-v1",
        )

    result = execute(job, slow_executor)

    assert result.status is JobStatus.TIMEOUT
    assert result.hit_point_ids == ()


def test_static_manifest_is_four_crv_then_directed_then_formal_passes():
    jobs = []
    for partition in map(str, range(1, 6)):
        for sequence in range(4):
            jobs.append(_job(f"c{partition}-{sequence}", Engine.CRV, partition, sequence, f"p{partition}"))
        jobs.append(_job(f"d{partition}", Engine.DIRECTED, partition, 0, f"p{partition}"))
        jobs.append(_job(f"f{partition}", Engine.FORMAL, partition, 0, f"p{partition}"))
    catalog = JobCatalog(jobs)
    manifest = catalog.static_manifest()
    assert len(manifest) == 30
    assert all(catalog.job(job_id).engine is Engine.CRV for job_id in manifest[:20])
    assert all(catalog.job(job_id).engine is Engine.DIRECTED for job_id in manifest[20:25])
    assert all(catalog.job(job_id).engine is Engine.FORMAL for job_id in manifest[25:])
    assert StaticHybridPolicy(catalog).manifest == manifest


def test_adaptive_policy_probes_then_uses_recent_yield():
    crv = _job("c1", Engine.CRV, "1", 0, "p1")
    directed = _job("d1", Engine.DIRECTED, "2", 0, "p2")
    catalog = JobCatalog([crv, directed, _job("f1", Engine.FORMAL, "3", 0, "p3")])
    yields = YieldTable(["crv-1", "directed-1", "formal-1"], recent_window=3)
    policy = AdaptivePolicy()

    first = policy.choose([directed, crv], yields, dispatch_count=0)
    assert first.job is crv

    yields.record(crv, _result(crv, hits=("p1",), hours=0.5), MergeOutcome(True, "ok", ("p1",), ()))
    yields.record(directed, _result(directed, hits=(), hours=1.0), MergeOutcome(True, "ok"))
    choice = policy.choose([directed, crv], yields, dispatch_count=3)
    assert choice.job is crv
