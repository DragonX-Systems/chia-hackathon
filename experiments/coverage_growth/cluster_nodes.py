"""Legacy MediumBOOM experiment nodes for a CHIA cluster.

Each function asks for one named slot. Install already happened in the
worker image; this only borrows a CPU-slot for the batch.

Set ``COVERAGE_BACKEND=verilator`` so torture / riscv-dv / directed are
scored by Verilator and formal is SymbiYosys. Default is the yield model.
"""

from __future__ import annotations

import os
from pathlib import Path

from chia.base.ChiaFunction import ChiaFunction

from adapters.mediumboom.engines import Allocation, KnobState, simulate_batch
from adapters.mediumboom.meter import CoverageMeterNode


def _run_engine(model, covered, retired, alloc, knobs, seed):
    import random

    rng = random.Random(seed)
    backend = os.environ.get("COVERAGE_BACKEND", "fake")
    if backend == "verilator":
        from adapters.mediumboom.verilator_backend import simulate_verilator_batch

        work = Path(os.environ.get("COVERAGE_WORK", "/tmp/chia_verilator_jobs")) / f"{alloc.action}_{seed}"
        return simulate_verilator_batch(model, set(covered), set(retired), alloc, knobs, rng, work)
    if backend == "leftover":
        from adapters.mediumboom.leftover_backend import simulate_leftover_batch

        return simulate_leftover_batch(model, set(covered), set(retired), alloc, knobs, rng)
    return simulate_batch(model, set(covered), set(retired), alloc, knobs, rng)


@ChiaFunction(resources={"coverage_meter": 1.0})
def meter_snapshot(store_path: str) -> dict:
    meter = CoverageMeterNode(store_path=store_path)
    snap = meter.snapshot()
    return {
        "hours": snap.hours,
        "hit_frac": snap.hit_frac,
        "closure_frac": snap.closure_frac,
        "n_covered": snap.n_covered,
        "n_retired": snap.n_retired,
        "uncovered": snap.uncovered,
        "covered": list(meter.covered),
        "retired": list(meter.retired),
    }


@ChiaFunction(resources={"coverage_meter": 1.0})
def meter_merge(store_path: str, hits: list, retired: list, hours: float) -> dict:
    meter = CoverageMeterNode(store_path=store_path)
    # Restore prior hits from disk sidecar if present — meter is process-local.
    # The loop keeps covered/retired on the driver and only uses merge for HMAC.
    snap = meter.merge_batch(hits, retired, hours)
    return {
        "hours": snap.hours,
        "hit_frac": snap.hit_frac,
        "closure_frac": snap.closure_frac,
        "n_covered": snap.n_covered,
        "n_retired": snap.n_retired,
        "uncovered": snap.uncovered,
    }


@ChiaFunction(resources={"torture": 1.0})
def run_torture(model, covered, retired, alloc: Allocation, knobs: KnobState, seed: int):
    return _run_engine(model, covered, retired, alloc, knobs, seed)


@ChiaFunction(resources={"riscv_dv": 1.0, "verilator_run": 1.0})
def run_riscv_dv(model, covered, retired, alloc: Allocation, knobs: KnobState, seed: int):
    """Constrained-random generate + Verilator coverage merge — needs both slots."""
    return _run_engine(model, covered, retired, alloc, knobs, seed)


@ChiaFunction(resources={"torture": 0.25})
def run_constraint_tuning(model, covered, retired, alloc: Allocation, knobs: KnobState, seed: int):
    """Almost free: 0.25 of a torture slot (many retunes can share a worker)."""
    return _run_engine(model, covered, retired, alloc, knobs, seed)


@ChiaFunction(resources={"artifact_writer": 1.0})
def run_write_artifact(asm_path: str, names: list, seed: int) -> dict:
    """Construct last-mile assembly (LLM auto / self / template)."""
    from adapters.mediumboom.nodes.artifact_writer import write_directed_artifact

    return write_directed_artifact(Path(asm_path), list(names), int(seed)).__dict__


@ChiaFunction(resources={"verilator_run": 1.0})
def run_directed(model, covered, retired, alloc: Allocation, knobs: KnobState, seed: int):
    """Score directed stimulus; collateral should already be on disk from run_write_artifact."""
    return _run_engine(model, covered, retired, alloc, knobs, seed)


@ChiaFunction(resources={"formal": 1.0})
def run_formal(model, covered, retired, alloc: Allocation, knobs: KnobState, seed: int):
    return _run_engine(model, covered, retired, alloc, knobs, seed)


DISPATCH = {
    "torture": run_torture,
    "riscv_dv": run_riscv_dv,
    "constraint_tuning": run_constraint_tuning,
    "write_artifact": run_write_artifact,
    "directed": run_directed,
    "formal": run_formal,
}
