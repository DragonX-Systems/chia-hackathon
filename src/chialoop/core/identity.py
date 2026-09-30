"""Validate unique work separately from scheduling and resource admission.

Executors may provide execution_fingerprint(job) -> str. That fingerprint must
represent the inputs/options actually executed, excluding labels and reporting
filters. Otherwise conservative catalog metadata checks are used. Identity
labels alone cannot establish semantic uniqueness for an opaque executor.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable, Iterable

from .types import Engine, JobSpec


def _metadata_keys(job: JobSpec) -> list[tuple[str, str]]:
    if job.engine is Engine.FORMAL and job.payload:
        canonical = json.dumps(dict(job.payload), sort_keys=True, separators=(",", ":"))
        return [("formal execution", hashlib.sha256(canonical.encode()).hexdigest())]
    keys = []
    if job.engine in (Engine.CRV, Engine.DIRECTED):
        for field in ("sha256", "input_elf"):
            value = job.payload.get(field)
            if value:
                value = str(Path(value).resolve()) if field == "input_elf" else str(value)
                keys.append(("simulation input", f"{field}:{value}"))
    return keys


def validate_unique_work(jobs: Iterable[JobSpec], executor: Callable) -> None:
    fingerprint = getattr(executor, "execution_fingerprint", None)
    seen: dict[tuple[Engine, str, str], str] = {}
    for job in jobs:
        if not job.seed_or_query_id.strip():
            raise ValueError(f"job {job.job_id} needs a unique seed/test/query identity")
        keys = [("seed/test/query identity", job.seed_or_query_id)]
        if fingerprint is not None:
            value = fingerprint(job)
            if not isinstance(value, str) or not value:
                raise ValueError(f"job {job.job_id} has an invalid execution fingerprint")
            keys.append(("execution", value))
        else:
            keys.extend(_metadata_keys(job))
        for kind, value in keys:
            key = (job.engine, kind, value)
            if key in seen:
                if kind == "seed/test/query identity":
                    raise ValueError(f"job {job.job_id} needs a unique seed/test/query identity; already used by {seen[key]}")
                raise ValueError(f"job {job.job_id} reuses {kind} from {seen[key]}")
            seen[key] = job.job_id
