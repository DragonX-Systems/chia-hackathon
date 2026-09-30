"""MediumBOOM leftover classification for formal and directed work.

The planner-visible uncovered view hides reachability. Labels use ``kind`` only
plus yield (coverage-per-hour). IMPOSSIBLE is discovered via formal Unreached,
never via a hidden bucket — planted ``unreachable`` bins stay off the planner.
"""

from __future__ import annotations

from dataclasses import dataclass

from .meter import MeterSnapshot


@dataclass(frozen=True)
class LeftoverSplit:
    crv: list[dict]
    formal: list[dict]
    directed: list[dict]
    n_open: int
    n_crv: int
    n_formal: int
    crv_closed_frac: float
    hit_frac: float


def split_leftovers(snap: MeterSnapshot) -> LeftoverSplit:
    """Partition planner-visible uncovered points (kind only).

    IMPOSSIBLE is discovered later via formal Unreached on leftover SVA.
    This split never invents a hidden impossible bucket.
    """
    formal = [u for u in snap.uncovered if u.get("kind") == "sva"]
    crv = [u for u in snap.uncovered if u.get("kind") != "sva"]
    n_open = len(snap.uncovered)
    n_crv_open = len(crv)
    denom = max(1, snap.n_total - len(formal))
    crv_closed = min(1.0, max(0.0, 1.0 - (n_crv_open / denom)))
    return LeftoverSplit(
        crv=crv,
        formal=formal,
        directed=list(crv),
        n_open=n_open,
        n_crv=n_crv_open,
        n_formal=len(formal),
        crv_closed_frac=crv_closed,
        hit_frac=snap.hit_frac,
    )


def past_crv_70(split: LeftoverSplit) -> bool:
    """Notes: CRV → ~70% then prune SVA / impossible via formal."""
    if split.n_crv == 0 and split.n_formal > 0:
        return True
    return split.crv_closed_frac >= 0.70 or split.hit_frac >= 0.70


def past_crv_80(split: LeftoverSplit) -> bool:
    """Notes: CRV → ~80% then pull tough leftovers into directed."""
    if split.n_crv == 0:
        return True
    return split.crv_closed_frac >= 0.80 or split.hit_frac >= 0.80
