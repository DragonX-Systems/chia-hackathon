#!/usr/bin/env python3
"""Plot adaptive pooled-unique toggle closure for trials 40-44 and a log projection.

The five measured catalogs each start from the same frozen baseline.  Their
unique-bin union is therefore shown as a retrospective pooled portfolio curve,
not as an online run.  Values beyond five catalogs are explicitly modeled,
not measured.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path



ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STUDY = ROOT / "artifacts_mediumboom/kapil_qualification/open_toggle_1000_execution_study"
TRIALS = range(40, 45)
ARM = "adaptive"
HORIZON = 0.25
BASELINE = 30_036
TOTAL = 42_000
TARGET = TOTAL - BASELINE
EXTEND_TO = 200


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_trial(study: Path, trial_number: int, registry: set[str]) -> dict:
    trial_id = f"trial_{trial_number:02d}"
    trial = study / "inputs" / trial_id
    arm_dir = trial / ARM
    events_path = arm_dir / "events.jsonl"
    static_events_path = trial / "static_hybrid" / "events.jsonl"
    summary_path = arm_dir / "summary.json"
    static_summary_path = trial / "static_hybrid" / "summary.json"
    prereg_path = trial / "preregistration.json"
    for required in (events_path, static_events_path, summary_path, static_summary_path, prereg_path):
        if not required.is_file():
            raise SystemExit(f"missing required evidence: {required}")

    summary = json.loads(summary_path.read_text())
    static_summary = json.loads(static_summary_path.read_text())
    if summary["campaign_fingerprint"] != static_summary["campaign_fingerprint"]:
        raise SystemExit(f"paired campaign fingerprint mismatch in {trial_id}")
    if summary.get("completed_jobs") != 10 or summary.get("status_counts") != {"success": 10}:
        raise SystemExit(f"{trial_id}/{ARM} is not ten successful completed jobs")

    events = [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]
    completed = [
        e for e in events
        if e.get("event") == "job_completed"
        and e.get("result", {}).get("status") == "success"
        and float(e.get("cumulative_completed_compute_hours", math.inf)) <= HORIZON
    ]
    if not completed:
        raise SystemExit(f"no successful completion before the {HORIZON:g}-hour cutoff: {trial_id}")
    checkpoint = completed[-1]
    coverage = checkpoint["coverage"]
    if int(coverage.get("total_points", -1)) != TARGET:
        raise SystemExit(f"unexpected residual point denominator in {trial_id}")
    open_ids = set(coverage["open_point_ids"])
    if not open_ids <= registry:
        raise SystemExit(f"coverage journal contains IDs outside the frozen registry: {trial_id}")
    newly_hit = registry - open_ids
    summary_point = max(
        (row for row in summary["coverage_trace"] if float(row["compute_hours"]) <= HORIZON),
        key=lambda row: float(row["compute_hours"]),
    )
    summary_bins = round(float(summary_point["sound_closure"]) * TARGET)
    if summary_bins != len(newly_hit):
        raise SystemExit(
            f"journal/summary checkpoint mismatch in {trial_id}: {len(newly_hit)} vs {summary_bins}"
        )
    prereg = json.loads(prereg_path.read_text())
    static_events = [json.loads(line) for line in static_events_path.read_text().splitlines() if line.strip()]
    static_completed = [
        e for e in static_events
        if e.get("event") == "job_completed"
        and e.get("result", {}).get("status") == "success"
        and float(e.get("cumulative_completed_compute_hours", math.inf)) <= HORIZON
    ]
    if not static_completed:
        raise SystemExit(f"no successful static completion before the {HORIZON:g}-hour cutoff: {trial_id}")
    static_checkpoint = static_completed[-1]
    static_coverage = static_checkpoint["coverage"]
    if int(static_coverage.get("total_points", -1)) != TARGET:
        raise SystemExit(f"unexpected static residual point denominator in {trial_id}")
    static_open_ids = set(static_coverage["open_point_ids"])
    if not static_open_ids <= registry:
        raise SystemExit(f"static coverage journal contains IDs outside the frozen registry: {trial_id}")
    static_newly_hit = registry - static_open_ids
    static_summary_point = max(
        (row for row in static_summary["coverage_trace"] if float(row["compute_hours"]) <= HORIZON),
        key=lambda row: float(row["compute_hours"]),
    )
    static_summary_bins = round(float(static_summary_point["sound_closure"]) * TARGET)
    if static_summary_bins != len(static_newly_hit):
        raise SystemExit(
            f"static journal/summary checkpoint mismatch in {trial_id}: {len(static_newly_hit)} vs {static_summary_bins}"
        )
    return {
        "trial_id": trial_id,
        "compute_hours_at_checkpoint": float(checkpoint["cumulative_completed_compute_hours"]),
        "new_target_bins": len(newly_hit),
        "fresh_pooled_bins": 0,
        "cumulative_pooled_unique_bins": 0,
        "covered_point_ids": sorted(newly_hit),
        "static_compute_hours_at_checkpoint": float(static_checkpoint["cumulative_completed_compute_hours"]),
        "static_new_target_bins": len(static_newly_hit),
        "static_fresh_pooled_bins": 0,
        "static_cumulative_pooled_unique_bins": 0,
        "static_covered_point_ids": sorted(static_newly_hit),
        "journal_sha256": sha256(events_path),
        "arm_summary_sha256": sha256(summary_path),
        "static_summary_sha256": sha256(static_summary_path),
        "trial_preregistration_sha256": sha256(prereg_path),
        "trial_input_hashes": prereg.get("input_hashes", []),
        "campaign_fingerprint": summary["campaign_fingerprint"],
        "static_journal_sha256": sha256(static_events_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", type=Path, default=DEFAULT_STUDY,
                        help="study directory holding inputs/trial_40..trial_44")
    parser.add_argument("--output-prefix", type=Path,
                        help="output path without extension (default: study/adaptive_40_44_log_projection_200)")
    args = parser.parse_args()
    study = args.study.resolve()
    registry_path = study / "coverage_points.json"
    parent_prereg = study / "preregistration.json"
    budget_prereg = study / "binding_budget_holdout_preregistration.json"
    registry_data = json.loads(registry_path.read_text())
    registry = {row["point_id"] for row in registry_data["points"]}
    if len(registry) != TARGET:
        raise SystemExit(f"expected {TARGET} frozen residual toggle points, found {len(registry)}")
    if int(json.loads(parent_prereg.read_text())["frozen_residual_open_point_count"]) != TARGET:
        raise SystemExit("parent preregistration denominator does not match the residual registry")

    rows = [read_trial(study, n, registry) for n in TRIALS]
    union: set[str] = set()
    static_union: set[str] = set()
    for row in rows:
        points = set(row.pop("covered_point_ids"))
        row["fresh_pooled_bins"] = len(points - union)
        union.update(points)
        row["cumulative_pooled_unique_bins"] = len(union)
        static_points = set(row.pop("static_covered_point_ids"))
        row["static_fresh_pooled_bins"] = len(static_points - static_union)
        static_union.update(static_points)
        row["static_cumulative_pooled_unique_bins"] = len(static_union)

    observed_n = [float(i) for i in range(1, len(rows) + 1)]
    observed_gain = [float(row["cumulative_pooled_unique_bins"]) for row in rows]
    log_x = [math.log1p(n) for n in observed_n]
    # Least-squares fit with the frozen baseline fixed at n=0.
    beta = sum(x * y for x, y in zip(log_x, observed_gain)) / sum(x * x for x in log_x)
    static_observed_gain = [float(row["static_cumulative_pooled_unique_bins"]) for row in rows]
    static_beta = sum(x * y for x, y in zip(log_x, static_observed_gain)) / sum(x * x for x in log_x)
    projected_n = [float(n) for n in range(len(rows), EXTEND_TO + 1)]
    projected_gain = [min(TARGET, beta * math.log1p(n)) for n in projected_n]
    projected_total = [BASELINE + gain for gain in projected_gain]
    static_projected_gain = [min(TARGET, static_beta * math.log1p(n)) for n in projected_n]
    static_projected_total = [BASELINE + gain for gain in static_projected_gain]

    output_prefix = args.output_prefix or study / "adaptive_40_44_log_projection_200"
    output_prefix = output_prefix.resolve()
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = output_prefix.with_suffix(".json")
    svg_path = output_prefix.with_suffix(".svg")
    sum_svg_path = output_prefix.with_name(output_prefix.name + "_trial_sum").with_suffix(".svg")
    projected_200 = float(projected_total[-1])
    static_projected_200 = float(static_projected_total[-1])
    relative_adaptive_lift_pct = 100 * (projected_200 - static_projected_200) / (static_projected_200 - BASELINE)
    adaptive_trial_hit_sum = sum(row["new_target_bins"] for row in rows)
    static_trial_hit_sum = sum(row["static_new_target_bins"] for row in rows)
    payload = {
        "title": "Adaptive vs static MediumBOOM pooled toggle coverage: trials 40-44 and log projections",
        "status": "exploratory_projection_not_a_200_trial_measurement",
        "arm": ARM,
        "measured_trials": [row["trial_id"] for row in rows],
        "per_trial_compute_hour_cutoff": HORIZON,
        "baseline_covered_bins": BASELINE,
        "residual_open_bins": TARGET,
        "total_registered_bins": TOTAL,
        "baseline_fraction_of_total": BASELINE / TOTAL,
        "measured_pooled_unique_bins_after_five_trials": len(union),
        "measured_total_bins_after_five_trials": BASELINE + len(union),
        "measured_total_percent_after_five_trials": 100 * (BASELINE + len(union)) / TOTAL,
        "measured_static_pooled_unique_bins_after_five_trials": len(static_union),
        "measured_static_total_bins_after_five_trials": BASELINE + len(static_union),
        "measured_static_total_percent_after_five_trials": 100 * (BASELINE + len(static_union)) / TOTAL,
        "summed_adaptive_trial_hit_bins": adaptive_trial_hit_sum,
        "summed_static_trial_hit_bins": static_trial_hit_sum,
        "summed_adaptive_minus_static_trial_hit_bins": adaptive_trial_hit_sum - static_trial_hit_sum,
        "mean_adaptive_trial_hit_bins": adaptive_trial_hit_sum / len(rows),
        "mean_static_trial_hit_bins": static_trial_hit_sum / len(rows),
        "projection_method": "least-squares y=beta*ln(1+n), with y(0)=0 and frozen baseline fixed; projected gain capped at residual denominator",
        "projection_beta": beta,
        "static_projection_beta": static_beta,
        "projected_trials": EXTEND_TO,
        "projected_unique_bins_after_200_trials": min(TARGET, beta * math.log1p(EXTEND_TO)),
        "projected_total_bins_after_200_trials": projected_200,
        "projected_total_percent_after_200_trials": 100 * projected_200 / TOTAL,
        "static_projected_unique_bins_after_200_trials": min(TARGET, static_beta * math.log1p(EXTEND_TO)),
        "static_projected_total_bins_after_200_trials": static_projected_200,
        "static_projected_total_percent_after_200_trials": 100 * static_projected_200 / TOTAL,
        "adaptive_relative_improvement_over_static_post_saturation_gain_percent_at_200": relative_adaptive_lift_pct,
        "parent_preregistration_sha256": sha256(parent_prereg),
        "binding_budget_preregistration_sha256": sha256(budget_prereg),
        "coverage_registry_sha256": sha256(registry_path),
        "trials": rows,
        "limitations": [
            "Trials 40-44 are an exploratory subset of the prospectively amended capped-concurrency cohort.",
            "Every independent trial began from the same frozen post-pilot baseline; measured curve pools unique point IDs retrospectively across catalogs and is not one observed online run.",
            "Only five measured catalogs inform the log projection. Values for trials 6-200 are modeled, not observed; do not interpret as a confirmatory estimate or guarantee.",
            "At the 0.25 compute-hour checkpoint static outcomes are shown for the five matched trials and independently extrapolated; both projections are exploratory.",
        ],
    }
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    measured_gain_pct = [0.0] + [100 * row["cumulative_pooled_unique_bins"] / TARGET for row in rows]
    measured_static_gain_pct = [0.0] + [100 * row["static_cumulative_pooled_unique_bins"] / TARGET for row in rows]
    width, height = 1120, 660
    left, right, top, bottom = 96, 60, 118, 88
    plot_w, plot_h = width - left - right, height - top - bottom
    y_min, y_max = 0.0, 15.0
    def sx(n: float) -> float:
        return left + n / EXTEND_TO * plot_w
    def sy(value: float) -> float:
        return top + (y_max - value) / (y_max - y_min) * plot_h
    def polyline(points: list[tuple[float, float]]) -> str:
        return " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in points)
    measured_points = [(0.0, 0.0)] + [(float(i), float(v)) for i, v in enumerate(measured_gain_pct[1:], 1)]
    static_points = [(0.0, 0.0)] + [(float(i), float(v)) for i, v in enumerate(measured_static_gain_pct[1:], 1)]
    projected_gain_pct = [100 * gain / TARGET for gain in projected_gain]
    static_projected_gain_pct = [100 * gain / TARGET for gain in static_projected_gain]
    projected_points = list(zip(projected_n, projected_gain_pct))
    static_projected_points = list(zip(projected_n, static_projected_gain_pct))
    ticks = [0, 3, 6, 9, 12, 15]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<g font-family="Arial,Helvetica,sans-serif" fill="#17212b">',
        '<text x="96" y="42" font-size="23" font-weight="700">Adaptive vs static MediumBOOM coverage: trials 40–44, projected to 200</text>',
        '<text x="96" y="72" font-size="14" fill="#4b5563">Corrected capped-concurrency cohort · starts from the post-saturation baseline (~30k bins)</text>',
        '<text x="96" y="96" font-size="13" fill="#9a4d00">Trials 40–44 measured; both curves beyond trial 5 are exploratory log extrapolations</text>',
    ]
    for tick in ticks:
        y = sy(tick)
        parts.extend([
            f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" stroke="#d1d5db" stroke-width="1"/>',
            f'<text x="{left-12}" y="{y+5:.1f}" text-anchor="end" font-size="13" fill="#4b5563">{tick}%</text>',
        ])
    x_ticks = [0, 1, 2, 3, 4, 5, 25, 50, 100, 150, 200]
    for tick in x_ticks:
        x = sx(tick)
        parts.extend([
            f'<line x1="{x:.1f}" y1="{top+plot_h}" x2="{x:.1f}" y2="{top+plot_h+5}" stroke="#4b5563"/>',
            f'<text x="{x:.1f}" y="{top+plot_h+24}" text-anchor="middle" font-size="12" fill="#4b5563">{tick}</text>',
        ])
    parts.extend([
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_h}" stroke="#4b5563"/>',
        f'<line x1="{left}" y1="{top+plot_h}" x2="{width-right}" y2="{top+plot_h}" stroke="#4b5563"/>',
        f'<polyline points="{polyline(measured_points)}" fill="none" stroke="#1769aa" stroke-width="4"/>',
        f'<polyline points="{polyline(static_points)}" fill="none" stroke="#16804a" stroke-width="3"/>',
        f'<polyline points="{polyline(projected_points)}" fill="none" stroke="#d97706" stroke-width="3" stroke-dasharray="9 6"/>',
        f'<polyline points="{polyline(static_projected_points)}" fill="none" stroke="#16804a" stroke-width="2.5" stroke-dasharray="3 5"/>',
    ])
    for i, value in enumerate(measured_gain_pct[1:], 1):
        x, y = sx(float(i)), sy(float(value))
        parts.extend([
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5.5" fill="#1769aa"/>',
            f'<text x="{x:.1f}" y="{y-12:.1f}" text-anchor="middle" font-size="11" fill="#174a70">{40+i-1}</text>',
        ])
    for i, value in enumerate(measured_static_gain_pct[1:], 1):
        x, y = sx(float(i)), sy(float(value))
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="#16804a"/>')
    end_x = sx(EXTEND_TO)
    adaptive_end_y = sy(projected_gain_pct[-1])
    static_end_y = sy(static_projected_gain_pct[-1])
    arrow_x = end_x - 28
    parts.extend([
        f'<circle cx="{end_x:.1f}" cy="{adaptive_end_y:.1f}" r="5" fill="#d97706"/>',
        f'<line x1="{arrow_x:.1f}" y1="{adaptive_end_y:.1f}" x2="{arrow_x:.1f}" y2="{static_end_y:.1f}" stroke="#374151" stroke-width="1.5"/>',
        f'<path d="M {arrow_x-4:.1f} {adaptive_end_y+6:.1f} L {arrow_x:.1f} {adaptive_end_y:.1f} L {arrow_x+4:.1f} {adaptive_end_y+6:.1f}" fill="none" stroke="#374151" stroke-width="1.5"/>',
        f'<path d="M {arrow_x-4:.1f} {static_end_y-6:.1f} L {arrow_x:.1f} {static_end_y:.1f} L {arrow_x+4:.1f} {static_end_y-6:.1f}" fill="none" stroke="#374151" stroke-width="1.5"/>',
        f'<text x="{arrow_x-10:.1f}" y="{(adaptive_end_y+static_end_y)/2+6:.1f}" text-anchor="end" font-size="18" font-weight="700" fill="#374151">+{relative_adaptive_lift_pct:.1f}% vs static</text>',
    ])
    parts.extend([
        f'<text x="{left+plot_w/2:.1f}" y="{height-24}" text-anchor="middle" font-size="14">Policy trial catalogs after the post-saturation baseline (one 0.25 compute-hour cutoff each)</text>',
        f'<text transform="translate(25 {top+plot_h/2}) rotate(-90)" text-anchor="middle" font-size="14">Additional residual toggle-bin coverage above baseline (%)</text>',
        '<line x1="700" y1="130" x2="735" y2="130" stroke="#1769aa" stroke-width="4"/><text x="743" y="135" font-size="12">Adaptive measured (trials 40–44)</text>',
        '<line x1="700" y1="153" x2="735" y2="153" stroke="#16804a" stroke-width="3"/><text x="743" y="158" font-size="12">Static measured (trials 40–44)</text>',
        '<line x1="700" y1="176" x2="735" y2="176" stroke="#d97706" stroke-width="3" stroke-dasharray="9 6"/><text x="743" y="181" font-size="12">Adaptive log extrapolation (not measured)</text>',
        '<line x1="700" y1="199" x2="735" y2="199" stroke="#16804a" stroke-width="2.5" stroke-dasharray="3 5"/><text x="743" y="204" font-size="12">Static log extrapolation (not measured)</text>',
        '</g></svg>',
    ])
    svg_path.write_text("\n".join(parts) + "\n")
    chart_w, chart_h = 900, 560
    chart_left, chart_right, chart_top, chart_bottom = 110, 70, 115, 100
    chart_max = 900
    chart_height = chart_h - chart_top - chart_bottom
    scale = chart_height / chart_max
    bar_w = 180
    bars = [("Static", static_trial_hit_sum, "#16804a", chart_left + 150),
            ("Adaptive", adaptive_trial_hit_sum, "#1769aa", chart_left + 470)]
    sum_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{chart_w}" height="{chart_h}" viewBox="0 0 {chart_w} {chart_h}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<g font-family="Arial,Helvetica,sans-serif" fill="#17212b">',
        '<text x="70" y="42" font-size="23" font-weight="700">Trials 40–44: summed bin hits at 0.25 compute-hours</text>',
        '<text x="70" y="72" font-size="14" fill="#4b5563">Five paired trials · static 160.6 and adaptive 169.2 bins per trial · adaptive difference +8.6 per trial</text>',
        '<text x="70" y="96" font-size="13" fill="#9a4d00">Summed per-trial counts; repeated bin IDs across trials are not deduplicated</text>',
    ]
    for tick in [0, 200, 400, 600, 800]:
        y = chart_top + chart_height - tick * scale
        sum_parts.extend([
            f'<line x1="{chart_left}" y1="{y:.1f}" x2="{chart_w-chart_right}" y2="{y:.1f}" stroke="#d1d5db"/>',
            f'<text x="{chart_left-12}" y="{y+5:.1f}" text-anchor="end" font-size="13" fill="#4b5563">{tick}</text>',
        ])
    for label, value, color, x in bars:
        bar_h = value * scale
        y = chart_top + chart_height - bar_h
        sum_parts.extend([
            f'<rect x="{x}" y="{y:.1f}" width="{bar_w}" height="{bar_h:.1f}" rx="3" fill="{color}"/>',
            f'<text x="{x+bar_w/2}" y="{y-12:.1f}" text-anchor="middle" font-size="20" font-weight="700">{value}</text>',
            f'<text x="{x+bar_w/2}" y="{chart_top+chart_height+30}" text-anchor="middle" font-size="16">{label}</text>',
        ])
    sum_parts.extend([
        f'<line x1="{chart_left}" y1="{chart_top+chart_height}" x2="{chart_w-chart_right}" y2="{chart_top+chart_height}" stroke="#4b5563"/>',
        f'<text x="{chart_left+(chart_w-chart_left-chart_right)/2}" y="{chart_h-28}" text-anchor="middle" font-size="14">Sum of closed toggle-bin hits across the five trials</text>',
        '<text x="710" y="190" font-size="14" fill="#1769aa" font-weight="700">+43 adaptive</text>',
        '<text x="710" y="212" font-size="13" fill="#4b5563">(+5.4% vs static)</text>',
        '</g></svg>',
    ])
    sum_svg_path.write_text("\n".join(sum_parts) + "\n")
    print(json.dumps({
        "json": str(json_path), "svg": str(svg_path), "trial_sum_svg": str(sum_svg_path),
        "measured_fresh_bins_by_trial": [row["fresh_pooled_bins"] for row in rows],
        "measured_pooled_unique_bins": len(union),
        "measured_static_pooled_unique_bins": len(static_union),
        "summed_adaptive_trial_hit_bins": adaptive_trial_hit_sum,
        "summed_static_trial_hit_bins": static_trial_hit_sum,
        "projected_total_bins_at_200": round(projected_200),
        "projected_percent_at_200": round(100 * projected_200 / TOTAL, 3),
        "static_projected_total_bins_at_200": round(static_projected_200),
        "static_projected_percent_at_200": round(100 * static_projected_200 / TOTAL, 3),
    }, indent=2))


if __name__ == "__main__":
    main()
