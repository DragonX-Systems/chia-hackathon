"""Frozen adaptive and Static-Hybrid scheduling policies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from ..core.catalog import JobCatalog
from ..core.types import JobSpec
from ..core.yields import YieldTable


@dataclass(frozen=True)
class PolicyDecision:
    job: JobSpec
    reason: str


class SelectionPolicy(Protocol):
    name: str

    def choose(
        self,
        candidates: Sequence[JobSpec],
        yields: YieldTable,
        dispatch_count: int,
        *,
        running_nodes: frozenset[str] = frozenset(),
    ) -> PolicyDecision: ...
    def describe(self) -> dict: ...


class AdaptivePolicy:
    name = "adaptive"

    def choose(
        self,
        candidates: Sequence[JobSpec],
        yields: YieldTable,
        dispatch_count: int,
        *,
        running_nodes: frozenset[str] = frozenset(),
    ) -> PolicyDecision:
        del dispatch_count
        if not candidates:
            raise ValueError("adaptive policy requires at least one candidate")
        unprobed = [job for job in candidates
                    if yields.row(yields.node_for(job)).completed_jobs == 0
                    and yields.node_for(job) not in running_nodes]
        if unprobed:
            job = min(unprobed, key=_tie_key)
            return PolicyDecision(job, f"initial probe for node {yields.node_for(job)}")
        observed = [job for job in candidates
                    if yields.row(yields.node_for(job)).completed_jobs > 0]
        if observed:
            candidates = observed
        else:
            # Pending probes do not prevent using otherwise idle capacity.
            job = min(candidates, key=_tie_key)
            return PolicyDecision(job, f"additional run while probes pending for node {yields.node_for(job)}")
        job = min(candidates, key=lambda item: (
            -yields.row(yields.node_for(item)).recent_yield, *_tie_key(item)))
        row = yields.row(yields.node_for(job))
        return PolicyDecision(job, f"highest recent yield={row.recent_yield:.6g} incremental-bins/compute-hour; "
                              f"runs={len(row.recent)}; K={yields.recent_window}")

    def describe(self) -> dict:
        return {
            "score": "incremental sound bins / compute-hours over last K completed runs per node",
            "initial_probe_per_node": True,
            "tie_break": ["partition_id", "engine", "sequence_in_lane", "job_id"],
        }


class StaticHybridPolicy:
    name = "static_hybrid"

    def __init__(self, catalog: JobCatalog) -> None:
        self.manifest = catalog.static_manifest()
        self._rank = {job_id: index for index, job_id in enumerate(self.manifest)}

    def choose(
        self,
        candidates: Sequence[JobSpec],
        yields: YieldTable,
        dispatch_count: int,
        *,
        running_nodes: frozenset[str] = frozenset(),
    ) -> PolicyDecision:
        del yields, dispatch_count, running_nodes
        if not candidates:
            raise ValueError("static policy requires at least one candidate")
        job = min(candidates, key=lambda item: self._rank[item.job_id])
        return PolicyDecision(
            job,
            f"frozen manifest position {self._rank[job.job_id]}",
        )

    def describe(self) -> dict:
        return {"score": "frozen manifest position", "initial_probe_per_node": False}


def _tie_key(job: JobSpec) -> tuple[str, str, int, str]:
    return (
        job.partition_id,
        job.engine.value,
        job.sequence_in_lane,
        job.job_id,
    )
