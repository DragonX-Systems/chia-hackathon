"""Frozen campaign configuration for a Chialoop ROI experiment."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .types import Engine, JobSpec, ResourceCapacity


CRV_CATEGORIES = (
    "Integer and control flow",
    "Load/store and memory ordering",
    "Multiply, divide, and atomics",
    "Privilege and exception behavior",
    "Mixed long-running system behavior",
)


@dataclass(frozen=True)
class CampaignSpec:
    campaign_id: str
    artifact_version: str
    assumption_version: str
    target_closure: float = 0.95
    budget_compute_hours: float = 100.0
    resources: ResourceCapacity | None = None
    recent_window: int = 10
    n1: int = 5
    n2: int = 1
    n3: int = 1

    def __post_init__(self) -> None:
        if not self.campaign_id:
            raise ValueError("campaign_id cannot be empty")
        if not self.artifact_version:
            raise ValueError("artifact_version cannot be empty")
        if not self.assumption_version:
            raise ValueError("assumption_version cannot be empty")
        if not 0 < self.target_closure <= 1:
            raise ValueError("target_closure must be in (0, 1]")
        if not math.isfinite(self.budget_compute_hours) or self.budget_compute_hours <= 0:
            raise ValueError("budget_compute_hours must be finite and positive")
        resources = self.resources if self.resources is not None else ResourceCapacity()
        object.__setattr__(self, "resources", resources.resolve(self.recent_window))
        for name in ("n1", "n2", "n3"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if self.n1 + self.n2 + self.n3 == 0:
            raise ValueError("at least one node must be configured")

    @property
    def nodes(self) -> dict[str, str]:
        nodes = {}
        for engine, count in ((Engine.CRV, self.n1), (Engine.DIRECTED, self.n2), (Engine.FORMAL, self.n3)):
            for index in range(1, count + 1):
                label = (CRV_CATEGORIES[index - 1] if engine is Engine.CRV and index <= 5
                         else f"{engine.value} node {index}")
                nodes[f"{engine.value}-{index}"] = label
        return nodes

    def node_for(self, job: JobSpec) -> str:
        count = {Engine.CRV: self.n1, Engine.DIRECTED: self.n2, Engine.FORMAL: self.n3}[job.engine]
        node_id = job.node_id
        if node_id is None and count == 1:
            node_id = f"{job.engine.value}-1"
        if node_id not in {f"{job.engine.value}-{i}" for i in range(1, count + 1)}:
            raise ValueError(f"job {job.job_id} needs a node_id belonging to its configured {job.engine.value} nodes")
        return node_id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CampaignSpec":
        window = data.get("recent_window", 10)
        resources = data.get("resources") or {}
        return cls(
            campaign_id=str(data["campaign_id"]),
            artifact_version=str(data["artifact_version"]),
            assumption_version=str(data["assumption_version"]),
            target_closure=float(data.get("target_closure", 0.95)),
            budget_compute_hours=float(data.get("budget_compute_hours", 100.0)),
            resources=ResourceCapacity(**resources),
            recent_window=window,
            n1=data.get("n1", 5), n2=data.get("n2", 1), n3=data.get("n3", 1),
        )

    @classmethod
    def read_json(cls, path: Path) -> "CampaignSpec":
        return cls.from_dict(json.loads(path.read_text()))
