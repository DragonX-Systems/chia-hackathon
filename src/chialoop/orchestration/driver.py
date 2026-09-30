"""Completion-driven controller for the core Chialoop ROI experiment."""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from ..core.catalog import JobCatalog
from ..core.config import CampaignSpec
from ..core.coverage import CoverageState
from ..core.resources import Reservation, ResourceLedger
from ..core.types import CoveragePoint, Engine, JobResult, JobSpec, JobStatus, ProofStatus
from ..core.yields import YieldTable
from ..core.identity import validate_unique_work
from ..reporting.events import EventLogger
from .nodes import JobExecutor
from .policies import PolicyDecision, SelectionPolicy
from .runtime import AsyncRuntime, CompletedTask, TaskHandle


@dataclass
class RunningJob:
    job: JobSpec
    handle: TaskHandle
    reservation: Reservation
    decision: PolicyDecision
    dispatched_elapsed_seconds: float


@dataclass(frozen=True)
class CampaignRunSummary:
    campaign_id: str
    campaign_fingerprint: str
    runtime_name: str
    arm: str
    trial_id: str
    stop_reason: str
    reached_target: bool
    target_closure: float
    final_sound_closure: float
    final_hit_points: int
    final_proven_unreachable_points: int
    final_open_points: int
    compute95: float | None
    t95_hours: float | None
    total_compute_hours: float
    compute_hours_by_engine: dict[str, float]
    zero_yield_compute_hours: float
    sim_license_hours: float
    formal_license_hours: float
    wall_time_hours: float
    compute_slot_utilization: float | None
    scheduler_overhead_fraction: float
    dispatched_jobs: int
    completed_jobs: int
    skipped_jobs: int
    rejected_results: int
    status_counts: dict[str, int]
    coverage_trace: tuple[dict[str, Any], ...]
    resource_timeline: tuple[dict[str, Any], ...]
    yield_table: dict[str, dict]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CampaignRunner:
    """Run one adaptive or static arm against a frozen catalog."""

    def __init__(
        self,
        *,
        spec: CampaignSpec,
        catalog: JobCatalog,
        coverage_points: list[CoveragePoint] | tuple[CoveragePoint, ...],
        policy: SelectionPolicy,
        runtime: AsyncRuntime,
        executor: JobExecutor,
        trial_id: str,
        output_dir: Path | None = None,
        resume_events: Sequence[dict[str, Any]] = (),
    ) -> None:
        self.spec = spec
        self.catalog = catalog
        self.policy = policy
        self.runtime = runtime
        self.executor = executor
        self.trial_id = trial_id
        self.output_dir = Path(output_dir) if output_dir is not None else None
        self._trace_logger: EventLogger | None = None
        self._campaign_fingerprint = _campaign_fingerprint(
            spec,
            catalog,
            coverage_points,
        )

        self.coverage = CoverageState(
            coverage_points,
            artifact_version=spec.artifact_version,
            assumption_version=spec.assumption_version,
        )
        self.yields = YieldTable(spec.nodes, recent_window=spec.recent_window, node_for=spec.node_for)
        self.resources = ResourceLedger(spec.resources)

        self._running: dict[str, RunningJob] = {}
        self._completed: set[str] = set()
        self._skipped: set[str] = set()
        self._dispatch_count = 0
        self._completed_compute_hours = 0.0
        self._restored_cancelled_compute_hours = 0.0
        self._engine_compute_hours: dict[Engine, float] = defaultdict(float)
        self._zero_yield_compute_hours = 0.0
        self._scheduler_seconds = 0.0
        self._status_counts: Counter[str] = Counter()
        self._rejected_results = 0
        self._compute95: float | None = None
        self._t95_hours: float | None = None
        self._coverage_trace: list[dict[str, Any]] = [
            {"compute_hours": 0.0, "sound_closure": 0.0, "job_id": None}
        ]
        self._resource_timeline: list[dict[str, Any]] = []
        self._resume_elapsed_seconds = 0.0

        self._validate_inputs(coverage_points)
        if resume_events:
            self._restore_completed_events(resume_events)

    def _restore_completed_events(self, events: Sequence[dict[str, Any]]) -> None:
        """Restore completed work from a prior interrupted segment.

        The caller must ensure that every dispatched-but-uncompleted job in
        the prior segment never entered its executor. Results are replayed
        through the same evidence validator as a normal run.
        """
        starts = [event for event in events if event.get("event") == "campaign_started"]
        if not starts or any(
            start.get("campaign_id") != self.spec.campaign_id
            or start.get("campaign_fingerprint") != self._campaign_fingerprint
            for start in starts
        ):
            raise ValueError("every resume segment must start with matching frozen campaign inputs")
        jobs = {job.job_id: job for job in self.catalog.jobs}
        dispatches = {event["job_id"] for event in events if event.get("event") == "job_dispatched"}
        completed_ids: set[str] = set()
        segment_offset = 0.0
        segment_elapsed = 0.0
        segment_started_epoch: float | None = None
        for event in events:
            kind = event.get("event")
            if kind == "campaign_started":
                if segment_started_epoch is not None:
                    segment_offset += segment_elapsed
                segment_elapsed = 0.0
                segment_started_epoch = datetime.fromisoformat(event["timestamp"]).timestamp()
            if segment_started_epoch is None:
                raise ValueError("resume event precedes campaign_started")
            segment_elapsed = max(segment_elapsed, float(event.get("elapsed_seconds", 0.0)))
            if kind == "job_skipped":
                self._skipped.add(str(event["job_id"]))
                continue
            if kind == "target_reached":
                # Older journals recorded in-flight work only on this event.
                # Prefer its measured endpoint over completed-only replay.
                recorded_compute95 = float(event["compute95"])
                if not math.isfinite(recorded_compute95) or recorded_compute95 < 0:
                    raise ValueError("invalid Compute95 in resume journal")
                self._compute95 = recorded_compute95
                self._t95_hours = float(event["t95_hours"])
                self._coverage_trace[-1]["compute_hours"] = recorded_compute95
                continue
            if kind == "job_cancelled":
                job_id = str(event["job_id"])
                if job_id not in jobs or job_id not in dispatches or job_id in completed_ids:
                    raise ValueError(f"invalid cancelled job in resume journal: {job_id}")
                consumed = float(event["consumed_compute_hours"])
                if not math.isfinite(consumed) or consumed < 0:
                    raise ValueError(f"invalid cancelled compute hours for {job_id}")
                self._restored_cancelled_compute_hours += consumed
                self._engine_compute_hours[jobs[job_id].engine] += consumed
                self._zero_yield_compute_hours += consumed
                self._resource_timeline.append({
                    "job_id": job_id, "engine": jobs[job_id].engine.value,
                    "compute_slot": event.get("compute_slot"),
                    "start_seconds": segment_offset + float(event.get(
                        "start_seconds", event.get("elapsed_seconds", 0.0))),
                    "end_seconds": segment_offset + float(event.get(
                        "end_seconds", event.get("elapsed_seconds", 0.0))),
                    "status": "cancelled",
                })
                continue
            if kind != "job_completed":
                continue
            raw = event["result"]
            job_id = str(event["job_id"])
            if job_id not in jobs or job_id in completed_ids or job_id not in dispatches:
                raise ValueError(f"invalid or duplicate completed job in resume journal: {job_id}")
            result = JobResult(
                job_id=str(raw["job_id"]), engine=Engine(raw["engine"]),
                status=JobStatus(raw["status"]), start_time=str(raw["start_time"]),
                end_time=str(raw["end_time"]), compute_hours=float(raw["compute_hours"]),
                hit_point_ids=tuple(raw.get("hit_point_ids", ())),
                proven_unreachable_point_ids=tuple(raw.get("proven_unreachable_point_ids", ())),
                proof_status=ProofStatus(raw["proof_status"]),
                artifact_version=raw.get("artifact_version"),
                assumption_version=raw.get("assumption_version"),
                artifact_paths=tuple(raw.get("artifact_paths", ())),
                tool_versions=raw.get("tool_versions", {}),
                notes=str(raw.get("notes", "")), error_summary=str(raw.get("error_summary", "")),
            )
            merge = self.coverage.merge(jobs[job_id], result)
            recorded_merge = event.get("merge", {})
            if (merge.accepted != recorded_merge.get("accepted")
                    or list(merge.new_hit_ids) != recorded_merge.get("new_hit_ids", [])
                    or list(merge.new_proven_unreachable_ids) != recorded_merge.get("new_proven_unreachable_ids", [])):
                raise ValueError(f"resume evidence replay does not match prior merge for {job_id}")
            self.yields.record(jobs[job_id], result, merge)
            self._completed.add(job_id)
            completed_ids.add(job_id)
            self._completed_compute_hours += result.compute_hours
            self._engine_compute_hours[jobs[job_id].engine] += result.compute_hours
            self._status_counts[result.status.value] += 1
            if not merge.accepted:
                self._rejected_results += 1
            if merge.new_sound_points == 0:
                self._zero_yield_compute_hours += result.compute_hours
            snapshot = self.coverage.snapshot()
            cumulative = float(event.get(
                "cumulative_compute_hours",
                event.get("cumulative_completed_compute_hours", self._completed_compute_hours),
            ))
            self._coverage_trace.append({
                "compute_hours": cumulative, "sound_closure": snapshot.sound_closure,
                "job_id": job_id, "engine": jobs[job_id].engine.value,
            })
            if self._compute95 is None and snapshot.sound_closure >= self.spec.target_closure:
                self._compute95 = cumulative
                self._t95_hours = (
                    segment_offset + float(event.get("elapsed_seconds", 0.0))
                ) / 3600.0
            if "start_seconds" in event and "end_seconds" in event:
                start_seconds = segment_offset + float(event["start_seconds"])
                end_seconds = segment_offset + float(event["end_seconds"])
            else:
                try:
                    start_seconds = segment_offset + max(
                        0.0, datetime.fromisoformat(result.start_time).timestamp() - segment_started_epoch
                    )
                    end_seconds = segment_offset + max(
                        0.0, datetime.fromisoformat(result.end_time).timestamp() - segment_started_epoch
                    )
                except (TypeError, ValueError):
                    start_seconds = end_seconds = segment_offset + float(event.get("elapsed_seconds", 0.0))
            self._resource_timeline.append({
                "job_id": job_id, "engine": jobs[job_id].engine.value,
                "compute_slot": event.get("compute_slot"),
                "start_seconds": start_seconds, "end_seconds": end_seconds,
                "status": result.status.value,
            })
        self._dispatch_count = sum(
            event.get("event") == "job_dispatched" for event in events
        )
        self._resume_elapsed_seconds = segment_offset + segment_elapsed

    @property
    def arm(self) -> str:
        return self.policy.name

    def _trace_event(self, event_type: str, **fields: Any) -> None:
        if self._trace_logger is not None:
            self._trace_logger.emit(
                event_type,
                campaign_elapsed_seconds=(
                    self._resume_elapsed_seconds + self._trace_logger.elapsed_seconds
                ),
                **fields,
            )

    def run(self) -> CampaignRunSummary:
        output_dir = self.output_dir
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            self.spec.write_json(output_dir / "campaign_spec.json")
            self.catalog.write_jsonl(output_dir / "job_catalog.jsonl")
            policy_payload = self._policy_payload()
            (output_dir / "policy.json").write_text(
                json.dumps(policy_payload, indent=2, sort_keys=True) + "\n"
            )
            if "manifest" in policy_payload:
                (output_dir / "static_manifest.json").write_text(
                    json.dumps(policy_payload["manifest"], indent=2) + "\n"
                )
            event_path = output_dir / "events.jsonl"
            trace_path = output_dir / "trace.jsonl"
        else:
            event_path = None
            trace_path = None

        logger = EventLogger(event_path, arm=self.arm, trial_id=self.trial_id)
        self._trace_logger = (
            EventLogger(trace_path, arm=self.arm, trial_id=self.trial_id,
                        retain_records=False)
            if trace_path is not None else None
        )
        stop_reason = "unknown"
        try:
            self._trace_event(
                "campaign_started",
                trace_schema="chialoop-trace-v1",
                campaign_id=self.spec.campaign_id,
                policy=self.arm,
                node_labels=self.spec.nodes,
                target_closure=self.spec.target_closure,
                budget_compute_hours=self.spec.budget_compute_hours,
            )
            logger.emit(
                "campaign_started",
                campaign_id=self.spec.campaign_id,
                campaign_fingerprint=self._campaign_fingerprint,
                runtime_name=type(self.runtime).__name__,
                target_closure=self.spec.target_closure,
                budget_compute_hours=self.spec.budget_compute_hours,
                resources=self.spec.resources.to_dict(),
                policy=self._policy_payload(),
            )

            while True:
                snapshot = self.coverage.snapshot()
                if snapshot.sound_closure >= self.spec.target_closure:
                    stop_reason = "target_reached"
                    self._capture_target(logger)
                    break
                # Completions from other jobs must not postpone expired deadlines.
                self._expire_timed_out_jobs(logger)
                if self._current_compute_hours() >= self.spec.budget_compute_hours:
                    stop_reason = "budget_exhausted"
                    break

                self._dispatch_while_possible(logger)

                if not self._running:
                    self._skip_closed_jobs(logger)
                    if self._all_jobs_terminal():
                        stop_reason = "catalog_exhausted"
                    else:
                        stop_reason = "no_schedulable_job"
                    break

                timeout = min(
                    self._seconds_until_budget(),
                    self._seconds_until_job_timeout(),
                )
                completions = self.runtime.wait_first(
                    [entry.handle for entry in self._running.values()],
                    timeout_seconds=timeout,
                )
                if not completions:
                    continue  # Deadline and budget checks run before the next refill.

                self._trace_delivery(completions, source="runtime_wait")
                terminal_condition_seen = False
                for completion in completions:
                    self._process_completion(completion, logger)
                    if self.coverage.snapshot().sound_closure >= self.spec.target_closure:
                        self._capture_target(logger)
                        terminal_condition_seen = True
                    if self._current_compute_hours() >= self.spec.budget_compute_hours:
                        terminal_condition_seen = True
                if terminal_condition_seen:
                    stop_reason = (
                        "target_reached" if self._compute95 is not None
                        else "budget_exhausted"
                    )
                    break

            if stop_reason in {"target_reached", "budget_exhausted"}:
                self._drain_ready_completions(logger)
                if self._compute95 is not None:
                    stop_reason = "target_reached"
            self._trace_event(
                "stop_decision",
                reason=stop_reason,
                sound_closure=self.coverage.snapshot().sound_closure,
                completed_jobs=len(self._completed),
                skipped_jobs=len(self._skipped),
                running_job_ids=list(self._running),
            )
            cancelled_compute = self._cancel_running(logger, stop_reason)
            total_compute = (
                self._completed_compute_hours
                + self._restored_cancelled_compute_hours
                + cancelled_compute
            )
            final = self.coverage.snapshot()
            wall_hours = (self._resume_elapsed_seconds + logger.elapsed_seconds) / 3600.0
            denominator = self.spec.resources.compute * wall_hours
            utilization = total_compute / denominator if denominator > 0 else None
            scheduler_fraction = (
                self._scheduler_seconds / logger.elapsed_seconds
                if logger.elapsed_seconds > 0
                else 0.0
            )
            summary = CampaignRunSummary(
                campaign_id=self.spec.campaign_id,
                campaign_fingerprint=self._campaign_fingerprint,
                runtime_name=type(self.runtime).__name__,
                arm=self.arm,
                trial_id=self.trial_id,
                stop_reason=stop_reason,
                reached_target=final.sound_closure >= self.spec.target_closure,
                target_closure=self.spec.target_closure,
                final_sound_closure=final.sound_closure,
                final_hit_points=final.hit_points,
                final_proven_unreachable_points=final.proven_unreachable_points,
                final_open_points=final.open_points,
                compute95=self._compute95,
                t95_hours=self._t95_hours,
                total_compute_hours=total_compute,
                compute_hours_by_engine={
                    engine.value: self._engine_compute_hours.get(engine, 0.0)
                    for engine in Engine
                },
                zero_yield_compute_hours=self._zero_yield_compute_hours,
                sim_license_hours=(
                    self._engine_compute_hours.get(Engine.CRV, 0.0)
                    + self._engine_compute_hours.get(Engine.DIRECTED, 0.0)
                ),
                formal_license_hours=self._engine_compute_hours.get(Engine.FORMAL, 0.0),
                wall_time_hours=wall_hours,
                compute_slot_utilization=utilization,
                scheduler_overhead_fraction=scheduler_fraction,
                dispatched_jobs=self._dispatch_count,
                completed_jobs=len(self._completed),
                skipped_jobs=len(self._skipped),
                rejected_results=self._rejected_results,
                status_counts=dict(sorted(self._status_counts.items())),
                coverage_trace=tuple(self._coverage_trace),
                resource_timeline=tuple(self._resource_timeline),
                yield_table=self.yields.to_dict(),
            )
            logger.emit("campaign_finished", summary=summary.to_dict())
            self._trace_event(
                "campaign_finished",
                reason=stop_reason,
                final_sound_closure=summary.final_sound_closure,
                total_compute_hours=summary.total_compute_hours,
                completed_jobs=summary.completed_jobs,
                rejected_results=summary.rejected_results,
            )
            if output_dir is not None:
                (output_dir / "summary.json").write_text(
                    json.dumps(summary.to_dict(), indent=2, sort_keys=True) + "\n"
                )
            return summary
        except Exception as exc:
            self._trace_event(
                "campaign_failed",
                error=f"{type(exc).__name__}: {exc}",
                running_job_ids=list(self._running),
            )
            logger.emit(
                "campaign_failed",
                error=f"{type(exc).__name__}: {exc}",
                resources=self.resources.snapshot(),
            )
            self._cancel_running(logger, "campaign_failed")
            raise
        finally:
            logger.close()
            if self._trace_logger is not None:
                self._trace_logger.close()
                self._trace_logger = None

    def _validate_inputs(self, coverage_points: list[CoveragePoint] | tuple[CoveragePoint, ...]) -> None:
        coverage_partitions = {point.partition_id for point in coverage_points}
        engines = {job.engine for job in self.catalog.jobs}
        if not engines:
            raise ValueError("core job catalog must contain at least one engine")
        if set(self.catalog.partition_ids) - coverage_partitions:
            raise ValueError("job catalog refers to unknown coverage partitions")

        point_ids = {point.point_id for point in coverage_points}
        validate_unique_work(self.catalog.jobs, self.executor)
        for job in self.catalog.jobs:
            self.spec.node_for(job)
            if job.artifact_version != self.spec.artifact_version:
                raise ValueError(f"artifact version mismatch for job {job.job_id}")
            if job.engine is Engine.FORMAL and job.assumption_version != self.spec.assumption_version:
                raise ValueError(f"assumption version mismatch for job {job.job_id}")
            unknown_targets = set(job.target_point_ids) - point_ids
            if unknown_targets:
                raise ValueError(
                    f"job {job.job_id} targets unknown points {sorted(unknown_targets)}"
                )
            request = job.resources
            assert request is not None
            if (
                request.compute > self.spec.resources.compute
                or request.sim_license > self.spec.resources.sim_license
                or request.formal_license > self.spec.resources.formal_license
            ):
                raise ValueError(f"job {job.job_id} can never fit configured resources")

    def _policy_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": self.policy.name}
        payload.update({
            "recent_window": self.spec.recent_window,
            "nodes": self.spec.nodes,
            "concurrent_runs_per_node": True,
        })
        payload.update(self.policy.describe())
        manifest = getattr(self.policy, "manifest", None)
        if manifest is not None:
            payload["manifest"] = list(manifest)
        return payload

    def _skip_closed_jobs(self, logger: EventLogger) -> None:
        closed = self.coverage.closed_point_ids()
        unavailable = self._completed | self._skipped | set(self._running)
        for job in self.catalog.jobs:
            if job.job_id in unavailable or not job.target_point_ids:
                continue
            if set(job.target_point_ids) <= closed:
                self._skipped.add(job.job_id)
                logger.emit(
                    "job_skipped",
                    job_id=job.job_id,
                    engine=job.engine.value,
                    partition_id=job.partition_id,
                    reason="all declared target points already closed",
                )
                self._trace_event(
                    "job_skipped",
                    job_id=job.job_id,
                    node_id=self.spec.node_for(job),
                    reason="all declared target points already closed",
                )

    def _candidate_jobs(self, logger: EventLogger) -> list[JobSpec]:
        self._skip_closed_jobs(logger)
        unavailable = self._completed | self._skipped | set(self._running)
        candidates = [job for job in self.catalog.jobs
                      if job.job_id not in unavailable
                      and self.resources.can_fit(job.resources)]
        return candidates

    def _dispatch_while_possible(self, logger: EventLogger) -> None:
        while self.resources.available().compute > 0:
            started = time.perf_counter()
            candidates = self._candidate_jobs(logger)
            if not candidates:
                self._scheduler_seconds += time.perf_counter() - started
                self._trace_event(
                    "no_runnable_candidate",
                    running_job_ids=list(self._running),
                    available_resources=self.resources.available().to_dict(),
                )
                return
            decision = self.policy.choose(
                candidates, self.yields, self._dispatch_count,
                running_nodes=frozenset(self.spec.node_for(entry.job) for entry in self._running.values()),
            )
            self._scheduler_seconds += time.perf_counter() - started
            job = decision.job
            candidate_nodes = sorted({self.spec.node_for(item) for item in candidates})
            self._trace_event(
                "node_selected",
                job_id=job.job_id,
                node_id=self.spec.node_for(job),
                node_label=self.spec.nodes[self.spec.node_for(job)],
                engine=job.engine.value,
                partition_id=job.partition_id,
                reason=decision.reason,
                candidate_count=len(candidates),
                candidate_node_scores={
                    node_id: {
                        "recent_yield": self.yields.row(node_id).recent_yield,
                        "completed_jobs": self.yields.row(node_id).completed_jobs,
                        "cumulative_new_bins": self.yields.row(node_id).total_new_sound_points,
                    }
                    for node_id in candidate_nodes
                },
                running_job_ids=list(self._running),
                available_resources=self.resources.available().to_dict(),
            )
            assert job.resources is not None
            reservation = self.resources.reserve(job.job_id, job.resources)
            try:
                handle = self.runtime.submit(job, self.executor)
            except Exception:
                self.resources.release(job.job_id)
                raise
            self._running[job.job_id] = RunningJob(
                job=job,
                handle=handle,
                reservation=reservation,
                decision=decision,
                dispatched_elapsed_seconds=logger.elapsed_seconds,
            )
            self._dispatch_count += 1
            logger.emit(
                "job_dispatched",
                job_id=job.job_id,
                engine=job.engine.value,
                partition_id=job.partition_id,
                node_id=self.spec.node_for(job),
                seed_or_query_id=job.seed_or_query_id,
                compute_slot=reservation.compute_slot,
                resource_request=job.resources.to_dict(),
                reason=decision.reason,
                resources=self.resources.snapshot(),
            )
            self._trace_event(
                "job_dispatched",
                job_id=job.job_id,
                node_id=self.spec.node_for(job),
                compute_slot=reservation.compute_slot,
                running_job_ids=list(self._running),
            )

    def _trace_delivery(self, completions: Sequence[CompletedTask], *, source: str) -> None:
        self._trace_event(
            "results_delivered",
            source=source,
            job_ids=[item.handle.job.job_id for item in completions],
            statuses=[item.result.status.value for item in completions],
            result_end_times=[item.result.end_time for item in completions],
        )

    def _process_completion(self, completion: CompletedTask, logger: EventLogger) -> None:
        job_id = completion.handle.job.job_id
        running = self._running.pop(job_id)
        self.resources.release(job_id)
        result = completion.result
        merge = self.coverage.merge(running.job, result)
        self.yields.record(running.job, result, merge)
        self._completed.add(job_id)
        self._completed_compute_hours += result.compute_hours
        self._engine_compute_hours[running.job.engine] += result.compute_hours
        self._status_counts[result.status.value] += 1
        if not merge.accepted:
            self._rejected_results += 1
        if merge.new_sound_points == 0:
            self._zero_yield_compute_hours += result.compute_hours

        snapshot = self.coverage.snapshot()
        self._coverage_trace.append(
            {
                "compute_hours": self._current_compute_hours(),
                "sound_closure": snapshot.sound_closure,
                "job_id": job_id,
                "engine": running.job.engine.value,
            }
        )
        self._resource_timeline.append(
            {
                "job_id": job_id,
                "engine": running.job.engine.value,
                "compute_slot": running.reservation.compute_slot,
                "start_seconds": self._relative_result_time(
                    result.start_time,
                    logger,
                    fallback=running.dispatched_elapsed_seconds,
                ) + self._resume_elapsed_seconds,
                "end_seconds": self._relative_result_time(
                    result.end_time,
                    logger,
                    fallback=logger.elapsed_seconds,
                ) + self._resume_elapsed_seconds,
                "status": result.status.value,
            }
        )
        logger.emit(
            "job_completed",
            job_id=job_id,
            engine=running.job.engine.value,
            partition_id=running.job.partition_id,
            node_id=self.spec.node_for(running.job),
            compute_slot=running.reservation.compute_slot,
            result=result.to_dict(),
            merge=merge.to_dict(),
            coverage=snapshot.to_dict(),
            cumulative_completed_compute_hours=self._completed_compute_hours,
            cumulative_compute_hours=self._coverage_trace[-1]["compute_hours"],
            start_seconds=self._resource_timeline[-1]["start_seconds"] - self._resume_elapsed_seconds,
            end_seconds=self._resource_timeline[-1]["end_seconds"] - self._resume_elapsed_seconds,
            resources=self.resources.snapshot(),
        )
        node_id = self.spec.node_for(running.job)
        node_stats = self.yields.row(node_id)
        self._trace_event(
            "result_processed",
            job_id=job_id,
            node_id=node_id,
            node_label=self.spec.nodes[node_id],
            engine=running.job.engine.value,
            status=result.status.value,
            compute_hours=result.compute_hours,
            evidence_accepted=merge.accepted,
            merge_reason=merge.reason,
            new_hit_ids=list(merge.new_hit_ids),
            new_proven_unreachable_ids=list(merge.new_proven_unreachable_ids),
            new_bin_gain=merge.new_sound_points,
            node_cumulative_new_bins=node_stats.total_new_sound_points,
            node_completed_jobs=node_stats.completed_jobs,
            campaign_cumulative_closed_bins=(
                snapshot.hit_points + snapshot.proven_unreachable_points
            ),
            campaign_hit_points=snapshot.hit_points,
            campaign_proven_unreachable_points=snapshot.proven_unreachable_points,
            sound_closure=snapshot.sound_closure,
            campaign_compute_hours=self._coverage_trace[-1]["compute_hours"],
        )

    def _drain_ready_completions(self, logger: EventLogger) -> None:
        """Collect results that became ready while a terminal batch was processed."""
        while self._running:
            ready = self.runtime.wait_first(
                [entry.handle for entry in self._running.values()],
                timeout_seconds=0,
            )
            if not ready:
                return
            self._trace_delivery(ready, source="terminal_drain")
            for completion in ready:
                self._process_completion(completion, logger)
                if self.coverage.snapshot().sound_closure >= self.spec.target_closure:
                    self._capture_target(logger)

    def _capture_target(self, logger: EventLogger) -> None:
        if self._compute95 is not None:
            return
        # The last trace sample is captured at the completion event that changed
        # coverage, including partial occupancy from every other running job.
        # Reusing it keeps the primary endpoint and plotted crossing identical.
        self._compute95 = float(self._coverage_trace[-1]["compute_hours"])
        self._t95_hours = (self._resume_elapsed_seconds + logger.elapsed_seconds) / 3600.0
        logger.emit(
            "target_reached",
            compute95=self._compute95,
            t95_hours=self._t95_hours,
            coverage=self.coverage.snapshot().to_dict(),
        )
        self._trace_event(
            "target_reached",
            compute95=self._compute95,
            t95_hours=self._t95_hours,
            sound_closure=self.coverage.snapshot().sound_closure,
        )

    def _current_compute_hours(self) -> float:
        now = time.monotonic()
        return self._completed_compute_hours + self._restored_cancelled_compute_hours + sum(
            self.runtime.consumed_compute_hours(entry.handle, now=now)
            for entry in self._running.values()
        )

    def _seconds_until_budget(self) -> float:
        remaining = self.spec.budget_compute_hours - self._current_compute_hours()
        if remaining <= 0:
            return 0.0
        return remaining * 3600.0 / max(1, len(self._running))

    def _seconds_until_job_timeout(self) -> float:
        now = time.monotonic()
        return min(
            max(
                0.0,
                entry.job.timeout_seconds
                - (now - entry.handle.submitted_monotonic),
            )
            for entry in self._running.values()
        )

    def _expire_timed_out_jobs(self, logger: EventLogger) -> bool:
        now = time.monotonic()
        expired = [
            entry
            for entry in self._running.values()
            if now - entry.handle.submitted_monotonic >= entry.job.timeout_seconds
        ]
        for entry in expired:
            self._trace_event(
                "timeout_decision",
                job_id=entry.job.job_id,
                node_id=self.spec.node_for(entry.job),
                timeout_seconds=entry.job.timeout_seconds,
            )
            compute_hours = self.runtime.consumed_compute_hours(
                entry.handle,
                now=now,
            )
            started_epoch = self.runtime.started_epoch_seconds(entry.handle)
            self.runtime.cancel(entry.handle)
            ended = datetime.now(timezone.utc)
            started = (
                datetime.fromtimestamp(started_epoch, timezone.utc)
                if started_epoch is not None
                else ended
            )
            result = JobResult(
                job_id=entry.job.job_id,
                engine=entry.job.engine,
                status=JobStatus.TIMEOUT,
                start_time=started.isoformat(),
                end_time=ended.isoformat(),
                compute_hours=compute_hours,
                hit_point_ids=(),
                proven_unreachable_point_ids=(),
                proof_status=(
                    ProofStatus.TIMEOUT
                    if entry.job.engine is Engine.FORMAL
                    else ProofStatus.NOT_APPLICABLE
                ),
                artifact_version=None,
                assumption_version=None,
                error_summary=(
                    f"job exceeded timeout of {entry.job.timeout_seconds:g} seconds"
                ),
            )
            self._process_completion(
                CompletedTask(entry.handle, result),
                logger,
            )
        return bool(expired)

    def _cancel_running(self, logger: EventLogger, reason: str) -> float:
        cancelled_compute = 0.0
        now = time.monotonic()
        for job_id, entry in list(self._running.items()):
            consumed = self.runtime.consumed_compute_hours(entry.handle, now=now)
            started_epoch = self.runtime.started_epoch_seconds(entry.handle)
            cancelled_compute += consumed
            self._engine_compute_hours[entry.job.engine] += consumed
            self._zero_yield_compute_hours += consumed
            try:
                self.runtime.cancel(entry.handle)
            finally:
                self.resources.release(job_id)
                self._running.pop(job_id, None)
            start_seconds = max(
                0.0,
                (started_epoch if started_epoch is not None else time.time())
                - logger.started_epoch_seconds,
            )
            end_seconds = logger.elapsed_seconds
            self._resource_timeline.append({
                "job_id": job_id,
                "engine": entry.job.engine.value,
                "compute_slot": entry.reservation.compute_slot,
                "start_seconds": self._resume_elapsed_seconds + start_seconds,
                "end_seconds": self._resume_elapsed_seconds + end_seconds,
                "status": "cancelled",
            })
            logger.emit(
                "job_cancelled",
                job_id=job_id,
                engine=entry.job.engine.value,
                compute_slot=entry.reservation.compute_slot,
                consumed_compute_hours=consumed,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                reason=reason,
            )
            self._trace_event(
                "job_cancelled",
                job_id=job_id,
                node_id=self.spec.node_for(entry.job),
                reason=reason,
                consumed_compute_hours=consumed,
            )
        return cancelled_compute

    def _all_jobs_terminal(self) -> bool:
        return len(self._completed | self._skipped) == len(self.catalog.jobs)

    @staticmethod
    def _relative_result_time(
        timestamp: str,
        logger: EventLogger,
        *,
        fallback: float,
    ) -> float:
        try:
            epoch_seconds = datetime.fromisoformat(timestamp).timestamp()
        except (TypeError, ValueError):
            return fallback
        return max(0.0, epoch_seconds - logger.started_epoch_seconds)


def _campaign_fingerprint(
    spec: CampaignSpec,
    catalog: JobCatalog,
    coverage_points: list[CoveragePoint] | tuple[CoveragePoint, ...],
) -> str:
    payload = {
        "schema": "chialoop-nodes-v3",
        "campaign_spec": spec.to_dict(),
        "coverage_points": sorted(
            (asdict(point) for point in coverage_points),
            key=lambda point: (point["partition_id"], point["point_id"]),
        ),
        "job_catalog": [job.to_dict() for job in catalog.jobs],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
