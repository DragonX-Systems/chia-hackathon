from adapters.mediumboom.coverage_model import freeze_model, uncovered_view
from adapters.mediumboom.meter import CoverageMeterNode


def test_frozen_hmac_rejects_tamper(tmp_path):
    meter = CoverageMeterNode(tmp_path / "frozen.json")
    raw = (tmp_path / "frozen.json").read_text().replace("lsu", "hack", 1)
    (tmp_path / "frozen.json").write_text(raw)
    try:
        CoverageMeterNode(tmp_path / "frozen.json")
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_planner_view_hides_bucket():
    model = freeze_model()
    view = uncovered_view(model.points, set(), set())
    assert view
    assert "bucket" not in view[0]
    assert set(view[0]) == {"pid", "kind", "unit", "name"}


def test_meter_cannot_cover_unreachable(tmp_path):
    meter = CoverageMeterNode(tmp_path / "frozen.json")
    unreachable = [p.pid for p in meter.model.points if p.bucket == "unreachable"]
    snap = meter.merge_batch(unreachable[:5], [], 1.0)
    assert snap.n_covered == 0
    snap = meter.merge_batch([], unreachable[:5], 0.5)
    assert snap.n_retired == 5
    assert snap.n_covered == 0
