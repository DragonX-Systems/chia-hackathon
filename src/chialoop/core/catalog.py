"""Job catalog and frozen Static-Hybrid manifest construction."""

from __future__ import annotations

import json
import hashlib
import os
from collections import defaultdict, deque
from pathlib import Path
from typing import Iterable

from .types import Engine, JobSpec, LaneKey


_ENGINE_ORDER = {Engine.CRV: 0, Engine.DIRECTED: 1, Engine.FORMAL: 2}


class JobCatalog:
    def __init__(self, jobs: Iterable[JobSpec], *, shared_target_file: Path | None = None) -> None:
        ordered = tuple(jobs)
        if not ordered:
            raise ValueError("job catalog cannot be empty")

        by_id: dict[str, JobSpec] = {}
        lane_sequences: dict[tuple[LaneKey, str | None], set[int]] = defaultdict(set)
        for job in ordered:
            if job.job_id in by_id:
                raise ValueError(f"duplicate job_id: {job.job_id}")
            sequence_key = (job.lane, job.node_id)
            if job.sequence_in_lane in lane_sequences[sequence_key]:
                raise ValueError(
                    f"duplicate sequence {job.sequence_in_lane} in lane "
                    f"{job.lane.as_text()}"
                )
            by_id[job.job_id] = job
            lane_sequences[sequence_key].add(job.sequence_in_lane)

        self._jobs = tuple(
            sorted(
                ordered,
                key=lambda j: (
                    j.partition_id,
                    _ENGINE_ORDER[j.engine],
                    j.sequence_in_lane,
                    j.job_id,
                ),
            )
        )
        self._by_id = by_id
        self._shared_target_file = Path(shared_target_file).resolve() if shared_target_file else None
        lanes: dict[LaneKey, list[JobSpec]] = defaultdict(list)
        for job in self._jobs:
            lanes[job.lane].append(job)
        self._lanes = {key: tuple(value) for key, value in lanes.items()}

    @property
    def jobs(self) -> tuple[JobSpec, ...]:
        return self._jobs

    @property
    def lanes(self) -> tuple[LaneKey, ...]:
        return tuple(sorted(self._lanes))

    @property
    def partition_ids(self) -> tuple[str, ...]:
        return tuple(sorted({job.partition_id for job in self._jobs}))

    def job(self, job_id: str) -> JobSpec:
        return self._by_id[job_id]

    def jobs_in_lane(self, lane: LaneKey) -> tuple[JobSpec, ...]:
        return self._lanes.get(lane, ())

    def next_in_lane(
        self,
        lane: LaneKey,
        unavailable_job_ids: set[str],
    ) -> JobSpec | None:
        return next(
            (
                job
                for job in self.jobs_in_lane(lane)
                if job.job_id not in unavailable_job_ids
            ),
            None,
        )

    def static_manifest(self) -> tuple[str, ...]:
        """Build the frozen 4 CRV : 1 directed : 1 formal schedule.

        A pass visits partitions in lexical order and consumes the next job in
        that lane.  Missing lanes are skipped without changing the other lanes.
        """

        queues = {lane: deque(jobs) for lane, jobs in self._lanes.items()}
        manifest: list[str] = []
        partitions = self.partition_ids

        while any(queues.values()):
            before = len(manifest)
            for _ in range(4):
                self._take_pass(queues, partitions, Engine.CRV, manifest)
            self._take_pass(queues, partitions, Engine.DIRECTED, manifest)
            self._take_pass(queues, partitions, Engine.FORMAL, manifest)
            if len(manifest) == before:
                break
        return tuple(manifest)

    @staticmethod
    def _take_pass(
        queues: dict[LaneKey, deque[JobSpec]],
        partitions: tuple[str, ...],
        engine: Engine,
        manifest: list[str],
    ) -> None:
        for partition_id in partitions:
            queue = queues.get(LaneKey(partition_id, engine))
            if queue:
                manifest.append(queue.popleft().job_id)

    def write_jsonl(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as stream:
            shared_targets = self._jobs[0].target_point_ids
            use_shared_targets = all(job.target_point_ids == shared_targets for job in self._jobs)
            if use_shared_targets:
                if self._shared_target_file is not None:
                    document = json.loads(self._shared_target_file.read_text())
                    rows = document["points"] if isinstance(document, dict) else document
                    file_targets = tuple(
                        str(row["point_id"] if isinstance(row, dict) else row) for row in rows
                    )
                    if file_targets != shared_targets:
                        raise ValueError("shared target file does not match catalog job targets")
                    digest = hashlib.sha256(self._shared_target_file.read_bytes()).hexdigest()
                    header = {
                        "__chialoop_catalog__": "shared-target-file-v1",
                        "target_point_ids_file": Path(os.path.relpath(
                            self._shared_target_file, Path(path).resolve().parent
                        )).as_posix(),
                        "target_point_ids_sha256": digest,
                    }
                else:
                    header = {
                        "__chialoop_catalog__": "shared-targets-v1",
                        "target_point_ids": shared_targets,
                    }
                stream.write(json.dumps(header, sort_keys=True) + "\n")
            for job in self._jobs:
                data = job.to_dict()
                if use_shared_targets:
                    data.pop("target_point_ids")
                stream.write(json.dumps(data, sort_keys=True) + "\n")

    @classmethod
    def read_jsonl(cls, path: Path) -> "JobCatalog":
        jobs: list[JobSpec] = []
        shared_targets: tuple[str, ...] | None = None
        shared_target_file: Path | None = None
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                if data.get("__chialoop_catalog__") == "shared-targets-v1":
                    if jobs or shared_targets is not None:
                        raise ValueError("shared-target header must be the first catalog record")
                    shared_targets = tuple(str(x) for x in data["target_point_ids"])
                    continue
                if data.get("__chialoop_catalog__") == "shared-target-file-v1":
                    if jobs or shared_targets is not None:
                        raise ValueError("shared-target header must be the first catalog record")
                    shared_target_file = (Path(path).resolve().parent /
                                          data["target_point_ids_file"]).resolve()
                    digest = hashlib.sha256(shared_target_file.read_bytes()).hexdigest()
                    if digest != data["target_point_ids_sha256"]:
                        raise ValueError("shared target file hash does not match catalog header")
                    document = json.loads(shared_target_file.read_text())
                    rows = document["points"] if isinstance(document, dict) else document
                    shared_targets = tuple(
                        str(row["point_id"] if isinstance(row, dict) else row) for row in rows
                    )
                    continue
                if shared_targets is not None:
                    data["target_point_ids"] = shared_targets
                jobs.append(JobSpec.from_dict(data))
            except Exception as exc:  # noqa: BLE001 - retain source location
                raise ValueError(f"invalid catalog line {line_number}: {exc}") from exc
        return cls(jobs, shared_target_file=shared_target_file)
