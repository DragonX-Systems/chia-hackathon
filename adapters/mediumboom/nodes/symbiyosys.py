"""MediumBOOM SymbiYosys CHIA node.

If ``sby`` is not on PATH, ``run`` returns a structured skip rather than
pretending a proof. Hits and retirements come from solver status, not from
hidden cover-point tags.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from chialoop.compat import ChiaFunction
from ..sby_backend import run_formal_oracle, run_sby


@dataclass
class SbyResult:
    status: str
    mode: str
    log: str
    hours_charged: float
    binary: str | None
    hits: list[str] = field(default_factory=list)
    retired: list[str] = field(default_factory=list)
    wall_s: float = 0.0


class SymbiYosysNode:
    def __init__(self, rtl_dir: Path | None = None, timeout_s: int = 90):
        self.rtl_dir = Path(rtl_dir) if rtl_dir else Path(__file__).resolve().parent.parent / "rtl"
        self.timeout_s = timeout_s

    @ChiaFunction(resources={"formal": 1})
    def run(self, mode: str = "bmc", dut: str = "cover_lsu", work: Path | None = None) -> SbyResult:
        task = "prove" if mode == "prove" else "cover"
        sby = shutil.which("sby")
        if not sby:
            return SbyResult("skipped", mode, "sby not on PATH", 0.0, None)
        work = Path(work) if work else Path("/tmp/chia_sby")
        status, log, wall = run_sby(dut, task, work, timeout_s=self.timeout_s)
        return SbyResult(status, mode, log[-4000:], max(wall / 3600.0, 1.0 / 3600.0), sby, wall_s=wall)

    def retire(self, work: Path | None = None) -> SbyResult:
        work = Path(work) if work else Path("/tmp/chia_sby_oracle")
        hits, retired, notes, wall = run_formal_oracle([], work)
        status = "pass" if retired or hits else "unknown"
        return SbyResult(status, "oracle", notes, max(wall / 3600.0, 1.0 / 3600.0), shutil.which("sby"), hits, retired, wall)
