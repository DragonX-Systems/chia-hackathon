"""MediumBOOM artifact-writer CHIA node.

Track claim: autonomous construction of verification collateral. Directed
scoring stays on ``verilator_run``; *writing* the assembly is a separate
named slot so the graph can place generate ≠ score.

Default writer mode is ``auto``: Astra if ``ASTRA_API_KEY``+``ASTRA_API_BASE``,
else Poolside if ``POOLSIDE_API_KEY``, else the repo suite
``testbenches/verilator/last_mile_self.S``. Force with ``LAST_MILE_WRITER``.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from pathlib import Path

from ..astra_writer import write_last_mile_asm, write_self_asm, write_template_asm


@dataclass
class ArtifactWriteResult:
    path: str
    writer: str
    n_bytes: int
    n_names: int
    mode: str


def _resolve_mode() -> str:
    raw = os.environ.get("LAST_MILE_WRITER", "auto").strip().lower()
    if raw in ("", "auto"):
        if os.environ.get("ASTRA_API_KEY") and os.environ.get("ASTRA_API_BASE"):
            return "astra"
        if os.environ.get("POOLSIDE_API_KEY"):
            return "poolside"
        return "self"
    return raw


def write_directed_artifact(asm_path: Path, names: list[str], seed: int) -> ArtifactWriteResult:
    """Write last-mile assembly for uncovered cover-point names."""
    asm_path = Path(asm_path)
    asm_path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    mode = _resolve_mode()
    # Temporarily pin LAST_MILE_WRITER so astra_writer's resolver matches.
    prev = os.environ.get("LAST_MILE_WRITER")
    os.environ["LAST_MILE_WRITER"] = mode
    try:
        if mode in ("self", "grok", "cursor"):
            note = write_self_asm(asm_path, names, rng)
        elif mode == "template":
            note = write_template_asm(asm_path, names, rng)
        else:
            note = write_last_mile_asm(asm_path, names, rng)
    finally:
        if prev is None:
            os.environ.pop("LAST_MILE_WRITER", None)
        else:
            os.environ["LAST_MILE_WRITER"] = prev

    if not asm_path.exists() or asm_path.stat().st_size < 20:
        note = write_self_asm(asm_path, names, rng)
        mode = "self-fallback"

    return ArtifactWriteResult(
        path=str(asm_path.resolve()),
        writer=note,
        n_bytes=asm_path.stat().st_size,
        n_names=len(names),
        mode=mode,
    )


class ArtifactWriterNode:
    """CHIA node: construct directed verification collateral (assembly)."""

    def write(self, asm_path: str, names: list[str], seed: int) -> dict:
        result = write_directed_artifact(Path(asm_path), list(names), int(seed))
        return {
            "path": result.path,
            "writer": result.writer,
            "n_bytes": result.n_bytes,
            "n_names": result.n_names,
            "mode": result.mode,
        }
