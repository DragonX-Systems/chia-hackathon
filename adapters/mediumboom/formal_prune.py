"""MediumBOOM formal-prune enhancement.

CRV / directed never receive SVA pids. Planner sees ``kind`` only.
"""

from __future__ import annotations

from .prune import LeftoverSplit

FORMAL_PID_CAP = 12
DIRECTED_PID_CAP = 8


def is_sva_pid(uncovered_row: dict) -> bool:
    """True when the planner-visible row is leftover SVA (kind only)."""
    return uncovered_row.get("kind") == "sva"


def formal_targets(split: LeftoverSplit) -> list[str]:
    """SVA leftover pids for a formal batch, capped at 12.

    Stride across the leftover SVA list so Unreached is not stuck on a
    prefix of planner-visible rows (kind order is clustered in the yardstick).
    """
    pids = [u["pid"] for u in split.formal if is_sva_pid(u)]
    if len(pids) <= FORMAL_PID_CAP:
        return pids
    stride = max(1, len(pids) // FORMAL_PID_CAP)
    return pids[::stride][:FORMAL_PID_CAP]


def directed_targets(split: LeftoverSplit) -> list[str]:
    """Non-SVA leftover pids only (Enh 2 parking — never snipe SVA)."""
    rows = split.directed if split.directed else split.crv
    return [u["pid"] for u in rows if not is_sva_pid(u)][:DIRECTED_PID_CAP]
