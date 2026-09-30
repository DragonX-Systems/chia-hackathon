"""No-tool executor for checking MediumBOOM Chialoop orchestration."""

from chialoop import EngineEvidence, JobSpec, JobStatus, ProofStatus


def execute(job: JobSpec) -> EngineEvidence:
    """Return valid, zero-hit evidence without launching verification tools."""
    return EngineEvidence(
        status=JobStatus.SUCCESS,
        artifact_version=job.artifact_version,
        assumption_version=job.assumption_version,
        proof_status=(
            ProofStatus.UNKNOWN if job.assumption_version else ProofStatus.NOT_APPLICABLE
        ),
        notes="flow smoke only; no MediumBOOM tools were launched",
    )
