import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from verification.boom.evidence import classify, summarize
from verification.boom.instrument import HERE, stage, validate_catalog


def test_catalog_matches_all_monitors():
    catalog = validate_catalog()
    assert sum(p["kind"] == "assert" for p in catalog["properties"]) == 67
    assert sum(p["kind"] == "cover" for p in catalog["properties"]) == 66
    for prop in catalog["properties"]:
        assert prop["implementation_status"] == "SOURCE_DEFINED_NOT_ELABORATED"
        if prop["kind"] == "assert":
            assert prop["activation_cover"] == "activation." + prop["id"]


@pytest.mark.parametrize("mode,status,rc,expected", [
    ("cover", "FAIL", 2, "BOUNDED_UNREACHED"),
    ("cover", "PASS", 0, "REACHED"),
    ("bmc", "PASS", 0, "BOUNDED_SAFE"),
    ("prove", "PASS", 0, "PROVEN"),
    ("prove_unreachable", "PASS", 0, "PROVEN_UNREACHABLE_LOCAL"),
    ("prove_unreachable", "FAIL", 2, "REACHABILITY_COUNTEREXAMPLE"),
    ("cover", "PASS", 2, "ERROR"),
    ("prove", "FAIL", 0, "ERROR"),
    ("cover", "", 0, "ERROR"),
    ("cover", "UNKNOWN", 4, "UNKNOWN"),
    ("cover", "TIMEOUT", -9, "TIMEOUT"),
])
def test_result_semantics(mode, status, rc, expected):
    assert classify(mode, status, rc) == expected


IDENTITY = {key: f"reviewed-{key}-hash" for key in ("source", "config", "rtl", "monitor", "assumptions", "reset")}


def evidence(pid, mode="cover", native="PASS", rc=0):
    return {"id": pid, "mode": mode, "native_status": native, "returncode": rc,
            "identity": dict(IDENTITY), "qualification": "QUALIFIED",
            "artifact": "native/property/logfile.txt", "witness_replayed": True,
            "activation_passed": True}


def test_nonhits_and_unbound_stay_in_denominator():
    result = summarize({"hit", "deep", "unbound", "locally_excluded"}, [
        evidence("hit"), evidence("deep", native="FAIL", rc=2),
        evidence("locally_excluded", "prove_unreachable")], IDENTITY)
    assert result["hit_fraction"] == .25
    assert result["unresolved"] == ["deep", "unbound"]
    assert result["retired"] == []
    assert result["core_pruning_authorized"] is False


@pytest.mark.parametrize("changed", [
    {"identity": IDENTITY | {"config": "different-config"}},
    {"qualification": "SOURCE_STAGED_ONLY"},
    {"artifact": ""}, {"witness_replayed": False},
])
def test_invalid_evidence_rejected(changed):
    with pytest.raises(ValueError):
        summarize({"a"}, [evidence("a") | changed], IDENTITY)


def test_vacuous_and_contradictory_proofs_rejected():
    with pytest.raises(ValueError, match="activation"):
        summarize({"a"}, [evidence("a", "prove_unreachable") | {"activation_passed": False}], IDENTITY)
    with pytest.raises(ValueError, match="Contradictory"):
        summarize({"a"}, [evidence("a"), evidence("a", "prove_unreachable")], IDENTITY)
    with pytest.raises(ValueError, match="Unknown"):
        summarize({"a"}, [evidence("b")], IDENTITY)


def test_stage_actual_pinned_source_without_modifying_it(tmp_path):
    root = HERE.parents[1] / "third_party/riscv-boom"
    if not (root / ".git").exists():
        pytest.skip("Pinned BOOM checkout absent; use instrument.py on the locked revision")
    lock = json.loads((HERE / "source_lock.json").read_text())
    before = {name: (root / name).read_bytes() for name in lock["files"]}
    manifest = stage(root, tmp_path / "overlay")
    assert manifest["qualification"] == "SOURCE_STAGED_ONLY"
    assert not manifest["core_pruning_authorized"]
    for name, data in before.items():
        assert (root / name).read_bytes() == data
    lsu = (tmp_path / "overlay/src/main/scala/v3/lsu/lsu.scala").read_text()
    assert lsu.count("BOOM LAST-MILE MONITORS BEGIN") == 2
    # LSU monitor ends before GenByteMask; selector monitor is in its own class.
    assert lsu.index('boomAssert("lsu.') < lsu.index("object GenByteMask")
    assert lsu.index('boomAssert("forward.') > lsu.index("class ForwardingAgeLogic")
    with pytest.raises(FileExistsError):
        stage(root, tmp_path / "overlay")


def test_stage_refuses_source_drift_before_writing(tmp_path, monkeypatch):
    import verification.boom.instrument as instrument
    root = tmp_path / "upstream"
    root.mkdir()
    lock = json.loads((HERE / "source_lock.json").read_text())
    first = next(iter(lock["files"]))
    path = root / first
    path.parent.mkdir(parents=True)
    path.write_text("changed design\n")
    monkeypatch.setattr(instrument.subprocess, "check_output", lambda cmd, **kw:
                        lock["revision"] if "rev-parse" in cmd else "")
    with pytest.raises(ValueError, match="differs"):
        stage(root, tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("status", ["pass", "fail", "unknown", "timeout", "error"])
def test_legacy_lsu_never_retires_bounded_nonhits(tmp_path, monkeypatch, status):
    from adapters.mediumboom import boom_backend as boom
    monkeypatch.setattr(boom, "discover_dut", lambda: SimpleNamespace(module="LSU"))
    monkeypatch.setattr(boom, "run_sby_depth", lambda *a, **k:
                        {"status": status, "wall_s": .1, "log": "unreached"})
    monkeypatch.setattr(boom, "_parse_covers", lambda log: ({"dis_fire"}, {"contra", "never"}))
    hits, retired, _, _ = boom._run_lsu_formal_oracle(tmp_path)
    assert retired == []
    assert bool(hits) == (status in {"pass", "fail"})


def test_soc_timeout_does_not_silently_change_scope(tmp_path, monkeypatch):
    from adapters.mediumboom import boom_backend as boom, soc_formal
    monkeypatch.setenv("BOOM_FORMAL_SCOPE", "chiptop")
    monkeypatch.delenv("BOOM_FORMAL_ALLOW_LSU_FALLBACK", raising=False)
    monkeypatch.setattr(soc_formal, "run_soc_formal", lambda *a, **kw: ([], [], "TIMEOUT", 600.))
    def forbidden(*args, **kwargs):
        pytest.fail("Scope changed silently")
    monkeypatch.setattr(boom, "_run_lsu_formal_oracle", forbidden)
    assert boom.run_formal_oracle(tmp_path) == ([], [], "TIMEOUT", 600.)


def test_soc_bounded_nonhit_is_not_retired(tmp_path, monkeypatch):
    from adapters.mediumboom import soc_formal, sby_backend
    monkeypatch.setattr(soc_formal, "discover_soc_dut", lambda scope: SimpleNamespace(module="ChipTop"))
    monkeypatch.setattr(soc_formal, "write_soc_sby", lambda *a: None)
    def fake_run(*a, **kw):
        work = Path(kw["cwd"]) / "config"
        work.mkdir()
        (work / "status").write_text("FAIL\n")
        return SimpleNamespace(returncode=2, stdout="", stderr="")
    monkeypatch.setattr(soc_formal.subprocess, "run", fake_run)
    monkeypatch.setattr(sby_backend, "parse_sby_log", lambda log: (set(), {"contra", "never"}))
    hits, retired, _, _ = soc_formal.run_soc_formal(tmp_path, scope="chiptop")
    assert hits == retired == []
