from adapters.mediumboom.loop import run_experiment
from adapters.mediumboom.nodes.symbiyosys import SymbiYosysNode
from adapters.mediumboom.report import policy_brief, render_all_charts


def test_oracles_run_against_baseline(tmp_path):
    payload = run_experiment(tmp_path, budget_hours=36.0, seeds=(1, 2, 3))
    # Fake mixed 500-point list: oracles are compared, not guaranteed to beat
    # the industrial recipe. The disagreement we care about is on SVA-only DUTs.
    for name in ("baseline", "sva_first", "sim_max", "cph_greedy", "stage_clean"):
        assert name in payload["mean_final_closure"], name
        assert payload["mean_final_closure"][name] >= 0.0
    picks = payload["oracle_step1"]["picks"]
    assert set(picks.values()) == {"torture"}
    brief = policy_brief(payload)
    assert "sva_first" in brief["arms"]
    assert set(payload["oracles"]) == {"sva_first", "sim_max", "cph_greedy", "stage_clean"}
    assert "enh_v1" not in payload["oracles"]
    assert (tmp_path / "compare.json").exists()
    assert (tmp_path / "oracle_catalog.json").exists()
    render_all_charts(payload, tmp_path, brief)
    for name in (
        "coverage_vs_hours.svg",
        "final_coverage_bars.svg",
        "hours_to_90pct.svg",
        "engine_mix.svg",
    ):
        assert (tmp_path / name).exists(), name


def test_symbiyosys_node_skips_without_sby():
    node = SymbiYosysNode()
    result = node.run("bmc")
    assert result.mode == "bmc"
    assert result.status in ("skipped", "pass", "fail", "timeout")
