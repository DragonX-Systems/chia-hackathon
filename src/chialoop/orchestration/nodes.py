"""The three remote CHIA functions in the core experiment."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

from ..compat import ChiaFunction
from ..core.types import (
    Engine,
    EngineEvidence,
    JobResult,
    JobSpec,
    JobStatus,
    ProofStatus,
)

JobExecutor = Callable[[JobSpec], EngineEvidence]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(
    expected_engine: Engine,
    job: JobSpec,
    executor: JobExecutor,
    start_notifier: Any = None,
) -> JobResult:
    if job.engine is not expected_engine:
        raise ValueError(
            f"{expected_engine.value} node received {job.engine.value} job {job.job_id}"
        )

    started_epoch_seconds = time.time()
    started_at = datetime.fromtimestamp(
        started_epoch_seconds,
        timezone.utc,
    ).isoformat()
    started = time.perf_counter()
    if start_notifier is not None:
        import ray  # type: ignore

        ray.get(
            start_notifier.mark.remote(
                job.job_id,
                started_epoch_seconds,
            )
        )
    try:
        evidence = executor(job)
        if not isinstance(evidence, EngineEvidence):
            raise TypeError("job executor must return EngineEvidence")
        elapsed_seconds = time.perf_counter() - started
        if elapsed_seconds > job.timeout_seconds:
            evidence = EngineEvidence(
                status=JobStatus.TIMEOUT,
                proof_status=(
                    ProofStatus.TIMEOUT
                    if expected_engine is Engine.FORMAL
                    else ProofStatus.NOT_APPLICABLE
                ),
                artifact_version=evidence.artifact_version,
                assumption_version=evidence.assumption_version,
                artifact_paths=evidence.artifact_paths,
                tool_versions=evidence.tool_versions,
                notes=evidence.notes,
                error_summary=(
                    f"job exceeded timeout of {job.timeout_seconds:g} seconds"
                ),
            )
        status = evidence.status
        error_summary = evidence.error_summary
    except Exception as exc:  # noqa: BLE001 - tool failures become results
        evidence = EngineEvidence(
            status=JobStatus.ERROR,
            proof_status=(
                ProofStatus.ERROR
                if expected_engine is Engine.FORMAL
                else ProofStatus.NOT_APPLICABLE
            ),
            error_summary=f"{type(exc).__name__}: {exc}",
        )
        status = JobStatus.ERROR
        error_summary = evidence.error_summary

    elapsed_hours = (time.perf_counter() - started) / 3600.0
    compute_hours = (
        evidence.compute_hours
        if evidence.compute_hours is not None
        else elapsed_hours
    )
    return JobResult(
        job_id=job.job_id,
        engine=job.engine,
        status=status,
        start_time=started_at,
        end_time=_utc_now(),
        compute_hours=compute_hours,
        hit_point_ids=tuple(evidence.hit_point_ids),
        proven_unreachable_point_ids=tuple(
            evidence.proven_unreachable_point_ids
        ),
        proof_status=evidence.proof_status,
        artifact_version=evidence.artifact_version,
        assumption_version=evidence.assumption_version,
        artifact_paths=tuple(evidence.artifact_paths),
        tool_versions=dict(evidence.tool_versions),
        notes=evidence.notes,
        error_summary=error_summary,
    )


@ChiaFunction(resources={"compute": 1, "sim_license": 1})
def run_crv(
    job: JobSpec,
    executor: JobExecutor,
    start_notifier: Any = None,
) -> JobResult:
    return _run(Engine.CRV, job, executor, start_notifier)


@ChiaFunction(resources={"compute": 1, "sim_license": 1})
def run_directed(
    job: JobSpec,
    executor: JobExecutor,
    start_notifier: Any = None,
) -> JobResult:
    return _run(Engine.DIRECTED, job, executor, start_notifier)


@ChiaFunction(resources={"compute": 1, "formal_license": 1})
def run_formal(
    job: JobSpec,
    executor: JobExecutor,
    start_notifier: Any = None,
) -> JobResult:
    return _run(Engine.FORMAL, job, executor, start_notifier)


ENGINE_NODES = {
    Engine.CRV: run_crv,
    Engine.DIRECTED: run_directed,
    Engine.FORMAL: run_formal,
}
