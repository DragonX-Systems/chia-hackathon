"""Legacy CHIA node wrappers for the MediumBOOM coverage-growth loop.

Each engine batch is ``@ChiaFunction`` onto a named slot, then ``get()``
(via ``chia_compat.dispatch``). Without chialoops the same ``chia_remote``
path runs in-process — that is a supported CHIA local-driver mode.

The leftover-rich experiment uses these wrappers so Enh 2 (formal prune of
impossibles) and Enh 3 (directed last-mile on hard leftovers) both go through
CHIA nodes, not a shell DAG.

Enh 3 always places ``chia_write_artifact`` (``artifact_writer`` slot) before
``chia_run_directed`` (``verilator_run``) so collateral construction is a
first-class node, not a hidden helper inside the sim job.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from chialoop.compat import ChiaFunction, dispatch
from .engines import Allocation, BatchResult, KnobState
from .meter import CoverageMeterNode, MeterSnapshot
from .nodes.artifact_writer import write_directed_artifact


@ChiaFunction(resources={"coverage_meter": 1.0})
def chia_meter_snapshot(meter: CoverageMeterNode) -> MeterSnapshot:
    return CoverageMeterNode.snapshot(meter)


@ChiaFunction(resources={"coverage_meter": 1.0})
def chia_meter_merge(
    meter: CoverageMeterNode, hits: list[str], retired: list[str], hours: float
) -> MeterSnapshot:
    return CoverageMeterNode.merge_batch(meter, hits, retired, hours)


@ChiaFunction(resources={"torture": 1.0})
def chia_run_torture(batch_fn: Callable, *args: Any):
    return batch_fn(*args)


@ChiaFunction(resources={"riscv_dv": 1.0, "verilator_run": 1.0})
def chia_run_riscv_dv(batch_fn: Callable, *args: Any):
    return batch_fn(*args)


@ChiaFunction(resources={"torture": 0.25})
def chia_run_constraint_tuning(batch_fn: Callable, *args: Any):
    return batch_fn(*args)


@ChiaFunction(resources={"artifact_writer": 1.0})
def chia_write_artifact(asm_path: str, names: list[str], seed: int) -> dict:
    """Construct directed last-mile assembly (LLM or self suite)."""
    return write_directed_artifact(Path(asm_path), list(names), int(seed)).__dict__


@ChiaFunction(resources={"verilator_run": 1.0})
def chia_run_directed(batch_fn: Callable, *args: Any):
    """Hard-to-hit leftovers (non-SVA). Enh 3 last-mile.

    Collateral was already placed by ``chia_write_artifact`` on
    ``artifact_writer``; this node only scores on ``verilator_run``.
    """
    return batch_fn(*args)


@ChiaFunction(resources={"formal": 1.0})
def chia_run_formal(batch_fn: Callable, *args: Any):
    """Impossible / leftover SVA prune. Enh 2. SymbiYosys on a real DUT."""
    return batch_fn(*args)


ENGINE_NODES = {
    "torture": chia_run_torture,
    "riscv_dv": chia_run_riscv_dv,
    "constraint_tuning": chia_run_constraint_tuning,
    "directed": chia_run_directed,
    "formal": chia_run_formal,
}


def snapshot_via_chia(meter: CoverageMeterNode) -> MeterSnapshot:
    return dispatch(chia_meter_snapshot, meter)


def merge_via_chia(
    meter: CoverageMeterNode, hits: list[str], retired: list[str], hours: float
) -> MeterSnapshot:
    return dispatch(chia_meter_merge, meter, hits, retired, hours)


def write_artifact_via_chia(asm_path: Path | str, names: list[str], seed: int) -> dict:
    return dispatch(chia_write_artifact, str(asm_path), list(names), int(seed))


def _directed_names(model: Any, alloc: Allocation) -> list[str]:
    names: list[str] = []
    by_id = getattr(model, "by_id", None)
    table = by_id() if callable(by_id) else {}
    for pid in alloc.target_pids:
        pt = table.get(pid)
        if pt is None:
            names.append(str(pid))
        else:
            names.append(getattr(pt, "name", None) or getattr(pt, "pid", str(pid)))
    return names


def engine_via_chia(
    action: str,
    batch_fn: Callable,
    model: Any,
    covered: set,
    retired: set,
    alloc: Allocation,
    knobs: KnobState,
    rng: Any,
    work: Any = None,
):
    writer_meta: dict | None = None
    if action == "directed":
        work_root = Path(work) if work is not None else Path("/tmp/chia_artifact_writer")
        # One directory per directed placement; avoid clobbering prior collateral.
        n_prior = sum(1 for _ in work_root.glob("directed_*/t.S")) if work_root.exists() else 0
        round_dir = work_root / f"directed_{n_prior:04d}"
        asm = round_dir / "t.S"
        seed = int(getattr(rng, "randint", lambda a, b: 0)(0, 2**31 - 1)) if hasattr(rng, "randint") else 0
        writer_meta = write_artifact_via_chia(asm, _directed_names(model, alloc), seed)
        # Point the batch at the round dir when the backend accepts work.
        if work is not None:
            work = round_dir

    node = ENGINE_NODES.get(action, chia_run_riscv_dv)
    if work is not None:
        result, knobs_out = dispatch(node, batch_fn, model, covered, retired, alloc, knobs, rng, work)
    else:
        result, knobs_out = dispatch(node, batch_fn, model, covered, retired, alloc, knobs, rng)

    if writer_meta is not None and isinstance(result, BatchResult):
        tag = (
            f"artifact_node mode={writer_meta.get('mode')} "
            f"writer={writer_meta.get('writer')} "
            f"bytes={writer_meta.get('n_bytes')} path={writer_meta.get('path')}"
        )
        result = BatchResult(
            result.action,
            result.hours,
            result.hits,
            result.retired,
            f"{tag} | {result.notes}".strip(" |"),
        )
    return result, knobs_out
