"""Authoritative sound-coverage state and evidence validation."""

from __future__ import annotations

from typing import Iterable

from .types import (
    CoveragePoint,
    CoverageSnapshot,
    Engine,
    JobResult,
    JobSpec,
    JobStatus,
    MergeOutcome,
    PointState,
    ProofStatus,
)


class CoverageState:
    def __init__(
        self,
        points: Iterable[CoveragePoint],
        *,
        artifact_version: str,
        assumption_version: str,
    ) -> None:
        point_list = tuple(points)
        if not point_list:
            raise ValueError("coverage registry cannot be empty")
        if len({point.point_id for point in point_list}) != len(point_list):
            raise ValueError("coverage registry contains duplicate point IDs")
        self._points = {point.point_id: point for point in point_list}
        self._states = {point.point_id: PointState.OPEN for point in point_list}
        self.artifact_version = artifact_version
        self.assumption_version = assumption_version

    @property
    def point_ids(self) -> frozenset[str]:
        return frozenset(self._points)

    def state_of(self, point_id: str) -> PointState:
        return self._states[point_id]

    def closed_point_ids(self) -> frozenset[str]:
        return frozenset(
            point_id
            for point_id, state in self._states.items()
            if state is not PointState.OPEN
        )

    def snapshot(self) -> CoverageSnapshot:
        total = len(self._states)
        hit = sum(state is PointState.HIT for state in self._states.values())
        unreachable = sum(
            state is PointState.PROVEN_UNREACHABLE
            for state in self._states.values()
        )
        open_ids = frozenset(
            point_id
            for point_id, state in self._states.items()
            if state is PointState.OPEN
        )
        return CoverageSnapshot(
            total_points=total,
            hit_points=hit,
            proven_unreachable_points=unreachable,
            open_points=len(open_ids),
            sound_closure=(hit + unreachable) / total,
            open_point_ids=open_ids,
        )

    def merge(self, job: JobSpec, result: JobResult) -> MergeOutcome:
        error = self._validation_error(job, result)
        if error:
            return MergeOutcome(False, error)

        if result.status is not JobStatus.SUCCESS:
            return MergeOutcome(True, f"{result.status.value}: no coverage evidence")

        hits = tuple(dict.fromkeys(result.hit_point_ids))
        unreachable = tuple(dict.fromkeys(result.proven_unreachable_point_ids))
        hit_set = set(hits)
        unreachable_set = set(unreachable)

        unknown = (hit_set | unreachable_set) - self.point_ids
        if unknown:
            return MergeOutcome(False, f"unknown coverage points: {sorted(unknown)}")
        overlap = hit_set & unreachable_set
        if overlap:
            return MergeOutcome(False, f"same points hit and unreachable: {sorted(overlap)}")
        if any(self._states[point_id] is PointState.PROVEN_UNREACHABLE for point_id in hits):
            return MergeOutcome(False, "hit conflicts with an existing unreachability proof")
        if any(self._states[point_id] is PointState.HIT for point_id in unreachable):
            return MergeOutcome(False, "unreachability proof conflicts with an existing hit")

        new_hits = tuple(
            point_id
            for point_id in hits
            if self._states[point_id] is PointState.OPEN
        )
        new_unreachable = tuple(
            point_id
            for point_id in unreachable
            if self._states[point_id] is PointState.OPEN
        )
        for point_id in new_hits:
            self._states[point_id] = PointState.HIT
        for point_id in new_unreachable:
            self._states[point_id] = PointState.PROVEN_UNREACHABLE

        return MergeOutcome(
            True,
            "evidence merged",
            new_hit_ids=new_hits,
            new_proven_unreachable_ids=new_unreachable,
        )

    def _validation_error(self, job: JobSpec, result: JobResult) -> str:
        if result.job_id != job.job_id:
            return "result job_id does not match dispatched job"
        if result.engine is not job.engine:
            return "result engine does not match dispatched job"
        if result.compute_hours < 0:
            return "result compute_hours cannot be negative"
        has_evidence = bool(
            result.hit_point_ids or result.proven_unreachable_point_ids
        )
        if result.status is not JobStatus.SUCCESS and has_evidence:
            return f"{result.status.value} result cannot close coverage"
        if has_evidence and result.artifact_version != self.artifact_version:
            return "result artifact version does not match frozen campaign"

        reported_points = set(result.hit_point_ids) | set(
            result.proven_unreachable_point_ids
        )
        undeclared = reported_points - set(job.target_point_ids)
        if undeclared:
            return (
                "result contains evidence outside the dispatched job targets: "
                f"{sorted(undeclared)}"
            )

        if result.proven_unreachable_point_ids:
            if result.engine is not Engine.FORMAL:
                return "only formal may prove points unreachable"
            if result.hit_point_ids:
                return "one formal query result cannot both reach and prove unreachable"
            if result.proof_status is not ProofStatus.PROVEN_UNREACHABLE:
                return "unreachable points require a complete proof result"
            if result.assumption_version != self.assumption_version:
                return "formal assumption version does not match frozen campaign"

        if result.engine is Engine.FORMAL:
            if has_evidence and result.assumption_version != self.assumption_version:
                return "formal assumption version does not match frozen campaign"
            if result.hit_point_ids and result.proof_status is not ProofStatus.REACHED:
                return "formal hits require a reached cover result"
            expected_for_non_success = {
                JobStatus.TIMEOUT: ProofStatus.TIMEOUT,
                JobStatus.UNKNOWN: ProofStatus.UNKNOWN,
                JobStatus.ERROR: ProofStatus.ERROR,
            }
            expected = expected_for_non_success.get(result.status)
            if expected is not None and result.proof_status is not expected:
                return (
                    f"formal {result.status.value} result requires "
                    f"proof_status={expected.value}"
                )
        elif result.proof_status is not ProofStatus.NOT_APPLICABLE:
            return "simulation jobs cannot report a formal proof status"
        return ""
