from pathlib import Path

from adapters.mediumboom.chia_dispatch import (
    ENGINE_NODES,
    chia_run_directed,
    chia_run_formal,
    chia_write_artifact,
    write_artifact_via_chia,
)
from chialoop.compat import dispatch
from adapters.mediumboom.engines import Allocation, BatchResult, KnobState
from adapters.mediumboom.chia_dispatch import engine_via_chia


def test_engine_nodes_expose_chia_remote():
    for action, fn in ENGINE_NODES.items():
        assert hasattr(fn, "chia_remote"), action
    assert hasattr(chia_run_formal, "chia_remote")
    assert hasattr(chia_run_directed, "chia_remote")
    assert hasattr(chia_write_artifact, "chia_remote")


def test_dispatch_formal_calls_batch():
    called = {}

    def batch_fn(*args):
        called["n"] = len(args)
        return ("ok", args[0])

    out = dispatch(chia_run_formal, batch_fn, "model")
    assert called["n"] == 1
    assert out == ("ok", "model")


def test_write_artifact_via_chia_writes_asm(tmp_path):
    asm = tmp_path / "t.S"
    meta = write_artifact_via_chia(asm, ["lsu.line.foo", "bpred.toggle.bar"], seed=7)
    assert asm.exists()
    assert asm.stat().st_size >= 20
    assert meta["n_bytes"] >= 20
    assert meta["mode"] in ("self", "astra", "poolside", "template", "self-fallback")
    assert "writer" in meta


def test_directed_engine_places_artifact_node_first(tmp_path):
    class _Pt:
        def __init__(self, pid, name):
            self.pid = pid
            self.name = name

    class _Model:
        def by_id(self):
            return {"cp1": _Pt("cp1", "lsu.mem.lw")}

    calls = []

    def batch_fn(model, covered, retired, alloc, knobs, rng, work=None):
        calls.append({"work": Path(work) if work else None, "asm": (Path(work) / "t.S") if work else None})
        asm = Path(work) / "t.S"
        assert asm.exists() and asm.stat().st_size >= 20
        return BatchResult("directed", 0.1, ["cp1"], [], "scored"), knobs

    alloc = Allocation("directed", 1.0, target_pids=["cp1"], reason="test")
    result, _ = engine_via_chia(
        "directed",
        batch_fn,
        _Model(),
        set(),
        set(),
        alloc,
        KnobState(),
        __import__("random").Random(1),
        tmp_path,
    )
    assert calls and calls[0]["asm"].exists()
    assert "artifact_node" in result.notes
