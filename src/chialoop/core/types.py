"""Typed records shared by the Chialoop core.

The core deliberately knows nothing about MediumBOOM build generation.  A
``JobSpec`` points at already prepared artifacts and an injected executor
returns evidence produced by the selected verification engine.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import math
from typing import Any, Mapping


class Engine(str, Enum):
    CRV = "crv"
    DIRECTED = "directed"
    FORMAL = "formal"


class JobStatus(str, Enum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"
    ERROR = "error"
    CANCELLED = "cancelled"


class ProofStatus(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    REACHED = "reached"
    PROVEN_UNREACHABLE = "proven_unreachable"
    UNKNOWN = "unknown"
    TIMEOUT = "timeout"
    ERROR = "error"


class PointState(str, Enum):
    OPEN = "open"
    HIT = "hit"
    PROVEN_UNREACHABLE = "proven_unreachable"


@dataclass(frozen=True, order=True)
class LaneKey:
    """Legacy catalog ordering key; never a dispatch or concurrency boundary."""

    partition_id: str
    engine: Engine

    def as_text(self) -> str:
        return f"{self.engine.value}:{self.partition_id}"


@dataclass(frozen=True)
class ResourceRequest:
    compute: int = 1
    sim_license: int = 0
    formal_license: int = 0

    def __post_init__(self) -> None:
        if self.compute < 0 or self.sim_license < 0 or self.formal_license < 0:
            raise ValueError("resource requests cannot be negative")

    @classmethod
    def for_engine(cls, engine: Engine) -> "ResourceRequest":
        if engine in (Engine.CRV, Engine.DIRECTED):
            return cls(compute=1, sim_license=1)
        return cls(compute=1, formal_license=1)

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class ResourceCapacity:
    """Resource settings; omitted licenses are resolved using the campaign K."""

    compute: int = 5
    sim_license: int | None = None
    formal_license: int | None = None

    def __post_init__(self) -> None:
        for name in ("compute", "sim_license", "formal_license"):
            value = getattr(self, name)
            if value is None and name != "compute":
                continue
            minimum = 1 if name == "compute" else 0
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")

    def resolve(self, recent_window: int = 10) -> "ResourceCapacity":
        if isinstance(recent_window, bool) or not isinstance(recent_window, int) or recent_window <= 0:
            raise ValueError("recent_window must be a positive integer")
        return ResourceCapacity(
            compute=self.compute,
            sim_license=recent_window if self.sim_license is None else self.sim_license,
            formal_license=recent_window if self.formal_license is None else self.formal_license,
        )

    def to_dict(self) -> dict[str, int | None]:
        return asdict(self)


@dataclass(frozen=True)
class CoveragePoint:
    point_id: str
    partition_id: str

    def __post_init__(self) -> None:
        if not self.point_id:
            raise ValueError("coverage point ID cannot be empty")
        if not self.partition_id:
            raise ValueError("coverage partition ID cannot be empty")


@dataclass(frozen=True)
class JobSpec:
    job_id: str
    engine: Engine
    partition_id: str
    sequence_in_lane: int
    target_point_ids: tuple[str, ...]
    seed_or_query_id: str
    timeout_seconds: float
    artifact_version: str
    assumption_version: str | None = None
    resources: ResourceRequest | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    node_id: str | None = None

    def __post_init__(self) -> None:
        if not self.job_id:
            raise ValueError("job_id cannot be empty")
        if not self.partition_id:
            raise ValueError("partition_id cannot be empty")
        if self.sequence_in_lane < 0:
            raise ValueError("sequence_in_lane cannot be negative")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        if not self.artifact_version:
            raise ValueError("artifact_version cannot be empty")
        if self.engine is Engine.FORMAL and not self.assumption_version:
            raise ValueError("formal jobs require assumption_version")

        expected = ResourceRequest.for_engine(self.engine)
        if self.resources is None:
            object.__setattr__(self, "resources", expected)
        elif self.resources != expected:
            raise ValueError(
                f"{self.engine.value} must request {expected.to_dict()}, "
                f"got {self.resources.to_dict()}"
            )

        if len(set(self.target_point_ids)) != len(self.target_point_ids):
            raise ValueError(f"job {self.job_id} has duplicate target points")

    @property
    def lane(self) -> LaneKey:
        return LaneKey(self.partition_id, self.engine)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["engine"] = self.engine.value
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "JobSpec":
        request = data.get("resources")
        return cls(
            job_id=str(data["job_id"]),
            engine=Engine(data["engine"]),
            partition_id=str(data["partition_id"]),
            sequence_in_lane=int(data["sequence_in_lane"]),
            target_point_ids=tuple(str(x) for x in data.get("target_point_ids", ())),
            seed_or_query_id=str(data["seed_or_query_id"]),
            timeout_seconds=float(data["timeout_seconds"]),
            artifact_version=str(data["artifact_version"]),
            assumption_version=(
                str(data["assumption_version"])
                if data.get("assumption_version") is not None
                else None
            ),
            resources=ResourceRequest(**request) if request is not None else None,
            payload=dict(data.get("payload", {})),
            node_id=str(data["node_id"]) if data.get("node_id") is not None else None,
        )


@dataclass(frozen=True)
class EngineEvidence:
    """Raw evidence returned by an injected engine executor.

    Coverage-bearing results must report ``artifact_version``.  Formal
    coverage-bearing results must also report ``assumption_version``.  The
    controller never fills these values from the expected JobSpec because that
    would make provenance validation circular.

    ``compute_hours`` is optional.  Real jobs normally leave it unset so the
    CHIA node measures elapsed execution time.  Deterministic test or replay
    executors may report a registered modeled duration explicitly.
    """

    status: JobStatus = JobStatus.SUCCESS
    hit_point_ids: tuple[str, ...] = ()
    proven_unreachable_point_ids: tuple[str, ...] = ()
    proof_status: ProofStatus = ProofStatus.NOT_APPLICABLE
    artifact_version: str | None = None
    assumption_version: str | None = None
    artifact_paths: tuple[str, ...] = ()
    tool_versions: Mapping[str, str] = field(default_factory=dict)
    notes: str = ""
    error_summary: str = ""
    compute_hours: float | None = None

    def __post_init__(self) -> None:
        if self.compute_hours is not None and (
            not math.isfinite(self.compute_hours) or self.compute_hours < 0
        ):
            raise ValueError("compute_hours must be finite and nonnegative")


@dataclass(frozen=True)
class JobResult:
    job_id: str
    engine: Engine
    status: JobStatus
    start_time: str
    end_time: str
    compute_hours: float
    hit_point_ids: tuple[str, ...]
    proven_unreachable_point_ids: tuple[str, ...]
    proof_status: ProofStatus
    artifact_version: str | None
    assumption_version: str | None
    artifact_paths: tuple[str, ...] = ()
    tool_versions: Mapping[str, str] = field(default_factory=dict)
    notes: str = ""
    error_summary: str = ""

    def __post_init__(self) -> None:
        if not math.isfinite(self.compute_hours) or self.compute_hours < 0:
            raise ValueError("compute_hours must be finite and nonnegative")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["engine"] = self.engine.value
        data["status"] = self.status.value
        data["proof_status"] = self.proof_status.value
        return data


@dataclass(frozen=True)
class CoverageSnapshot:
    total_points: int
    hit_points: int
    proven_unreachable_points: int
    open_points: int
    sound_closure: float
    open_point_ids: frozenset[str]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["open_point_ids"] = sorted(self.open_point_ids)
        return data


@dataclass(frozen=True)
class MergeOutcome:
    accepted: bool
    reason: str
    new_hit_ids: tuple[str, ...] = ()
    new_proven_unreachable_ids: tuple[str, ...] = ()

    @property
    def new_sound_points(self) -> int:
        return len(self.new_hit_ids) + len(self.new_proven_unreachable_ids)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"new_sound_points": self.new_sound_points}
