#!/usr/bin/env python3
"""Overlay trials 40–44 with the live 50-job profile run on common bins/time.

Only the shared 11,964-bin native-toggle registry is compared. The 40–44 curve
is the mean across its five independent trials through 0.25 compute-hours per
arm; the 50-job curve is one paired campaign. Log curves beyond observed data
are exploratory projections to the frozen 5 compute-hour arm budget.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import mean
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[2]
QUAL = ROOT / "artifacts_mediumboom/kapil_qualification"
OLD_STUDY = QUAL / "open_toggle_1000_execution_study"
NEW_STUDY = QUAL / "profiled_50job_study"
OUT_SVG = QUAL / "adaptive_40_44_plus_profiled_50job_compute_hours.svg"
OUT_JSON = QUAL / "adaptive_40_44_plus_profiled_50job_compute_hours.json"
OLD_BASELINE = 30036
OLD_TOTAL = 42000
OLD_HORIZON_H = 0.25
COMMON_HORIZON_H = 5.0


def read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def job_records(path: Path, registry: set[str]) -> list[tuple[float, set[str], str]]:
    rows = [row for row in read_events(path) if row.get("event") == "job_completed"]
    records = []
    for row in rows:
        result = row.get("result", {})
        elapsed_h = row.get("cumulative_completed_compute_hours")
        if elapsed_h is None:
            elapsed_h = row.get("cumulative_compute_hours")
        if elapsed_h is None:
            elapsed_h = sum(item["compute_hours"] for item in records) + result.get("compute_hours", 0.0)
        hits = set(result.get("hit_point_ids", ())) & registry if result.get("status") == "success" else set()
        records.append((float(elapsed_h), hits, result.get("status", "unknown")))
    records.sort(key=lambda item: item[0])
    return records


def cumulative(records: list[tuple[float, set[str], str]], at_h: float | None = None) -> list[tuple[float, int]]:
    seen: set[str] = set()
    points = [(0.0, 0)]
    for hours, ids, _status in records:
        if at_h is not None and hours > at_h:
            break
        seen.update(ids)
        points.append((hours, len(seen)))
    return points


def value_at(points: list[tuple[float, int]], x: float) -> int:
    value = 0
    for hour, bins in points:
        if hour > x:
            break
        value = bins
    return value


def mean_trial_curve(trial_points: list[list[tuple[float, int]]], horizon_h: float) -> list[tuple[float, float]]:
    grid = {0.0, horizon_h}
    for points in trial_points:
        grid.update(x for x, _ in points if x <= horizon_h)
    return [(x, mean(value_at(points, x) for points in trial_points)) for x in sorted(grid)]


def log_beta(points: list[tuple[float, float]]) -> float:
    usable = [(math.log1p(x), y) for x, y in points if x > 0]
    denom = sum(x * x for x, _ in usable)
    return sum(x * y for x, y in usable) / denom if denom else 0.0


def svg_chart(data: dict) -> str:
    width, height = 1200, 750
    left, right, top, bottom = 110, 1120, 160, 560
    xmax = COMMON_HORIZON_H
    curves = data["curves"]
    y_values = [OLD_BASELINE / OLD_TOTAL * 100]
    for curve in curves.values():
        y_values.extend((OLD_BASELINE + y) / OLD_TOTAL * 100 for _, y in curve["observed"])
        y_values.append(curve["projected_total_percent_at_5h"])
    ymin = max(68.0, math.floor((min(y_values) - 0.25) * 2) / 2)
    ymax = min(85.0, math.ceil((max(y_values) + 0.25) * 2) / 2)
    if ymax <= ymin:
        ymax = ymin + 1
    xmap = lambda x: left + x / xmax * (right - left)
    ymap = lambda y: bottom - (y - ymin) / (ymax - ymin) * (bottom - top)
    baseline_pct = OLD_BASELINE / OLD_TOTAL * 100

    colors = {
        "old_static": ("#64748b", "40–44 mean · Static-Hybrid"),
        "old_adaptive": ("#147d92", "40–44 mean · Adaptive"),
        "new_static": ("#a16207", "50-job run · Static-Hybrid"),
        "new_adaptive": ("#c2415d", "50-job run · Adaptive"),
    }
    output = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        '<title id="title">MediumBOOM coverage versus cumulative simulator compute-hours</title>',
        '<desc id="desc">Compares trials 40–44 mean curves and the single 50-job CRV-heavy paired run using their shared native toggle registry. Positive arrows mark measured adaptive gains at the registered 0.25-hour endpoint for trials 40–44 and equal 17-attempt checkpoint for the 50-job run. Dashed five-hour log projections are exploratory and may cross.</desc>',
        '<rect width="100%" height="100%" fill="#fff"/>',
        '<g font-family="Arial,Helvetica,sans-serif" fill="#17212b">',
        '<text x="110" y="39" font-size="24" font-weight="700">MediumBOOM toggle coverage vs compute time</text>',
        '<text x="110" y="68" font-size="14" fill="#475569">Trials 40–44 (five-run mean) vs one profiled 50-job CRV:Directed 4:1 run</text>',
        f'<text x="110" y="94" font-size="13" fill="#475569">Measured checkpoint deltas: 40–44 {data["measured_deltas"]["trials_40_44"]["relative_percent"]:+.1f}%; 50-job at 17 attempts {data["measured_deltas"]["profiled_50job_17_attempts"]["relative_percent"]:+.1f}% (shared toggles)</text>',
        f'<text x="110" y="118" font-size="13" fill="#a34b00">Interim 50-job snapshot: static {data["measured_deltas"]["profiled_50job_17_attempts"]["static_successes"]} successes/{data["measured_deltas"]["profiled_50job_17_attempts"]["static_timeouts"]} timeouts; adaptive {data["measured_deltas"]["profiled_50job_17_attempts"]["adaptive_successes"]}/{data["measured_deltas"]["profiled_50job_17_attempts"]["adaptive_timeouts"]}. Dashed 5h fits are exploratory.</text>',
    ]
    # Grid and axes.
    tick = 0.5
    ytick = math.ceil(ymin / tick) * tick
    while ytick <= ymax + 1e-9:
        y = ymap(ytick)
        output.append(f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="#e2e8f0"/>')
        output.append(f'<text x="{left-12}" y="{y+5:.1f}" text-anchor="end" font-size="12" fill="#475569">{ytick:.1f}%</text>')
        ytick += tick
    for xtick in range(0, int(xmax) + 1):
        x = xmap(xtick)
        output.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{bottom}" stroke="#edf1f5"/>')
        output.append(f'<text x="{x:.1f}" y="{bottom+23}" text-anchor="middle" font-size="12" fill="#475569">{xtick}</text>')
    output.append(f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="#64748b"/>')
    output.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" stroke="#64748b"/>')
    output.append(f'<text x="{(left+right)/2}" y="{bottom+58}" text-anchor="middle" font-size="14" fill="#334155">Cumulative simulator compute-hours per arm</text>')
    output.append(f'<text x="28" y="{(top+bottom)/2}" transform="rotate(-90 28 {(top+bottom)/2})" text-anchor="middle" font-size="14" fill="#334155">Coverage of 42,000-bin registry</text>')

    # Shared post-saturation baseline and old 0.25 h checkpoint.
    base_y = ymap(baseline_pct)
    output.append(f'<line x1="{left}" y1="{base_y:.1f}" x2="{right}" y2="{base_y:.1f}" stroke="#94a3b8" stroke-width="1.5" stroke-dasharray="3 5"/>')
    output.append(f'<text x="{right-4}" y="{base_y-7:.1f}" text-anchor="end" font-size="11" fill="#64748b">post-saturation baseline</text>')
    checkpoint_x = xmap(OLD_HORIZON_H)
    output.append(f'<line x1="{checkpoint_x:.1f}" y1="{top}" x2="{checkpoint_x:.1f}" y2="{bottom}" stroke="#94a3b8" stroke-width="1.5" stroke-dasharray="4 5"/>')
    output.append(f'<text x="{checkpoint_x+7:.1f}" y="{top+16}" font-size="11" fill="#64748b">40–44 measured horizon</text>')

    for key, (color, _label) in colors.items():
        curve = curves[key]
        obs = curve["observed"]
        fit_start = obs[-1][0] if obs else 0.0
        beta = curve["log_beta"]
        proj = [(fit_start, curve["fit_gain_at_observed_end"])]
        for i in range(1, 61):
            x = fit_start + (COMMON_HORIZON_H - fit_start) * i / 60
            y0 = curve["fit_gain_at_observed_end"]
            proj.append((x, min(data["common_residual_bins"], y0 + beta * (math.log1p(x) - math.log1p(fit_start)))))
        def path(points):
            return " ".join(("M" if i == 0 else "L") + f"{xmap(x):.1f},{ymap((OLD_BASELINE+y)/OLD_TOTAL*100):.1f}" for i,(x,y) in enumerate(points))
        output.append(f'<path d="{path(obs)}" fill="none" stroke="{color}" stroke-width="3" stroke-linejoin="round"/>')
        output.append(f'<path d="{path(proj)}" fill="none" stroke="{color}" stroke-width="2.5" stroke-dasharray="8 6" stroke-linejoin="round"/>')
        if obs:
            x, y = obs[-1]
            output.append(f'<circle cx="{xmap(x):.1f}" cy="{ymap((OLD_BASELINE+y)/OLD_TOTAL*100):.1f}" r="4.5" fill="{color}"/>')

    # Measured checkpoint arrows: trial 40–44's frozen 0.25h mean endpoint,
    # and the 50-job run's equal 17-attempt interim checkpoint (timeouts retained).
    measured = data["measured_deltas"]
    old = measured["trials_40_44"]
    old_x = xmap(OLD_HORIZON_H)
    old_y_static = ymap((OLD_BASELINE + old["static_shared_toggle_bins"]) / OLD_TOTAL * 100)
    old_y_adaptive = ymap((OLD_BASELINE + old["adaptive_shared_toggle_bins"]) / OLD_TOTAL * 100)
    output.append(f'<line x1="{old_x+8:.1f}" y1="{old_y_static:.1f}" x2="{old_x+8:.1f}" y2="{old_y_adaptive:.1f}" stroke="#147d92" stroke-width="2.5" marker-end="url(#deltaArrow)"/>')
    output.append(f'<circle cx="{old_x:.1f}" cy="{old_y_static:.1f}" r="4" fill="#64748b"/><circle cx="{old_x:.1f}" cy="{old_y_adaptive:.1f}" r="4" fill="#147d92"/>')
    output.append(f'<rect x="{old_x+27:.1f}" y="{old_y_adaptive-29:.1f}" width="190" height="24" rx="5" fill="#fff" fill-opacity="0.95" stroke="#147d92"/>')
    output.append(f'<text x="{old_x+122:.1f}" y="{old_y_adaptive-13:.1f}" text-anchor="middle" font-size="12" font-weight="700" fill="#116273">40–44 measured: +{old["relative_percent"]:.1f}%</text>')

    new = measured["profiled_50job_17_attempts"]
    nx_static = xmap(new["static_compute_hours"])
    nx_adaptive = xmap(new["adaptive_compute_hours"])
    ny_static = ymap((OLD_BASELINE + new["static_shared_toggle_bins"]) / OLD_TOTAL * 100)
    ny_adaptive = ymap((OLD_BASELINE + new["adaptive_shared_toggle_bins"]) / OLD_TOTAL * 100)
    output.append(f'<line x1="{nx_static:.1f}" y1="{ny_static:.1f}" x2="{nx_adaptive:.1f}" y2="{ny_adaptive:.1f}" stroke="#a34b00" stroke-width="2.5" marker-end="url(#deltaArrow)"/>')
    output.append(f'<circle cx="{nx_static:.1f}" cy="{ny_static:.1f}" r="4" fill="#a16207"/><circle cx="{nx_adaptive:.1f}" cy="{ny_adaptive:.1f}" r="4" fill="#c2415d"/>')
    new_badge_x, new_badge_y = nx_static + 20, ny_adaptive - 25
    output.append(f'<rect x="{new_badge_x:.1f}" y="{new_badge_y:.1f}" width="222" height="24" rx="5" fill="#fff" fill-opacity="0.95" stroke="#a34b00"/>')
    output.append(f'<text x="{new_badge_x+111:.1f}" y="{new_badge_y+16:.1f}" text-anchor="middle" font-size="12" font-weight="700" fill="#9a3412">50-job, 17 attempts: +{new["relative_percent"]:.1f}%</text>')

    # Legend arranged in two columns; solid=observed, dashed=log extrapolation.
    legend = [("old_static", 128, 650), ("old_adaptive", 512, 650), ("new_static", 128, 680), ("new_adaptive", 512, 680)]
    for key, x, y in legend:
        color, label = colors[key]
        output.append(f'<line x1="{x}" y1="{y-5}" x2="{x+34}" y2="{y-5}" stroke="{color}" stroke-width="3"/><line x1="{x+38}" y1="{y-5}" x2="{x+72}" y2="{y-5}" stroke="{color}" stroke-width="2.5" stroke-dasharray="7 5"/>')
        output.append(f'<text x="{x+82}" y="{y}" font-size="13" fill="#334155">{label}</text>')
    output.append(f'<text x="110" y="716" font-size="11" fill="#64748b">At equal 17 attempts, the full 12,156-point endpoint was {measured["profiled_50job_17_attempts"]["static_full_registry_bins"]} static vs {measured["profiled_50job_17_attempts"]["adaptive_full_registry_bins"]} adaptive (+{measured["profiled_50job_17_attempts"]["full_registry_relative_percent"]:.1f}%); static timeouts count as attempts.</text>')
    output.append('<text x="110" y="735" font-size="11" fill="#64748b">Measured Δ% = 100×(Adaptive−Static)/Static gained bins. Shared 11,964 native-toggle scope shown; dashed 5h log fits are exploratory only.</text>')
    output.insert(1, '<defs><marker id="deltaArrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 Z" fill="context-stroke"/></marker></defs>')
    output.append('</g></svg>')
    return "\n".join(output) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-svg", type=Path, default=OUT_SVG)
    parser.add_argument("--output-json", type=Path, default=OUT_JSON)
    args = parser.parse_args()

    old_points_doc = json.loads((OLD_STUDY / "coverage_points.json").read_text())
    registry = {point["point_id"] for point in old_points_doc["points"]}
    assert len(registry) == 11964, f"unexpected shared registry: {len(registry)}"
    new_doc = json.loads((NEW_STUDY / "coverage_points.json").read_text())
    new_registry = {point["point_id"] for point in new_doc["points"]}
    assert registry <= new_registry, "profiled 50-job target universe no longer contains the 40–44 registry"

    curves: dict[str, dict] = {}
    source_hashes: dict[str, str] = {}
    old_endpoint_bins: dict[str, float] = {}
    for arm, short in (("static_hybrid", "static"), ("adaptive", "adaptive")):
        trial_curves = []
        for trial_num in range(40, 45):
            journal = OLD_STUDY / "inputs" / f"trial_{trial_num}" / arm / "events.jsonl"
            records = job_records(journal, registry)
            trial_curves.append(cumulative(records, OLD_HORIZON_H))
            source_hashes[f"trial_{trial_num}_{short}"] = __import__("hashlib").sha256(journal.read_bytes()).hexdigest()
        old_mean = mean_trial_curve(trial_curves, OLD_HORIZON_H)
        old_endpoint_bins[short] = float(old_mean[-1][1])
        old_fit = log_beta(old_mean)
        curves[f"old_{short}"] = {
            "observed": old_mean,
            "observed_end_compute_hours": old_mean[-1][0],
            "observed_mean_gain_at_0_25h": old_mean[-1][1],
            "fit_gain_at_observed_end": old_mean[-1][1],
            "log_beta": old_fit,
            "projected_gain_at_5h": min(len(registry), old_mean[-1][1] + old_fit * (math.log1p(COMMON_HORIZON_H) - math.log1p(old_mean[-1][0]))),
            "projected_total_percent_at_5h": (OLD_BASELINE + min(len(registry), old_mean[-1][1] + old_fit * (math.log1p(COMMON_HORIZON_H) - math.log1p(old_mean[-1][0])))) / OLD_TOTAL * 100,
        }

        journal = NEW_STUDY / "inputs/trial_01" / arm / "events.jsonl"
        records = job_records(journal, registry)
        new_curve = cumulative(records)
        beta = log_beta([(x, float(y)) for x, y in new_curve])
        source_hashes[f"profiled_50job_{short}"] = __import__("hashlib").sha256(journal.read_bytes()).hexdigest()
        statuses = [status for _, _, status in records]
        if len(records) < 17:
            raise RuntimeError(f"{arm} has only {len(records)} completed jobs; cannot compute the 17-attempt checkpoint")
        checkpoint_records = records[:17]
        checkpoint_curve = cumulative(checkpoint_records)
        raw_events = [row for row in read_events(journal) if row.get("event") == "job_completed"]
        checkpoint_event = raw_events[16]
        curves[f"new_{short}"] = {
            "observed": [(x, float(y)) for x, y in new_curve],
            "observed_end_compute_hours": new_curve[-1][0],
            "observed_gain_at_last_event": new_curve[-1][1],
            "fit_gain_at_observed_end": new_curve[-1][1],
            "successful_jobs": statuses.count("success"),
            "timed_out_jobs": statuses.count("timeout"),
            "log_beta": beta,
            "projected_gain_at_5h": min(len(registry), new_curve[-1][1] + beta * (math.log1p(COMMON_HORIZON_H) - math.log1p(new_curve[-1][0]))),
            "projected_total_percent_at_5h": (OLD_BASELINE + min(len(registry), new_curve[-1][1] + beta * (math.log1p(COMMON_HORIZON_H) - math.log1p(new_curve[-1][0])))) / OLD_TOTAL * 100,
            "equal_17_attempts_checkpoint": {
                "shared_toggle_bins": checkpoint_curve[-1][1],
                "compute_hours": checkpoint_curve[-1][0],
                "successful_jobs": sum(status == "success" for _, _, status in checkpoint_records),
                "timed_out_jobs": sum(status == "timeout" for _, _, status in checkpoint_records),
                "full_registry_bins": checkpoint_event.get("coverage", {}).get("hit_points"),
            },
        }

    data = {
        "status": "interim_exploratory_projection_not_measurement",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "axis": "cumulative simulator compute-hours per arm",
        "common_projection_horizon_compute_hours": COMMON_HORIZON_H,
        "trials_40_44_measured_horizon_compute_hours_per_trial": OLD_HORIZON_H,
        "trials_40_44_cohort": "mean curve across five independent trials 40–44; each measured through 0.25 compute-hours/arm",
        "profiled_50job_cohort": "one paired run, 50 jobs/arm, CRV:Directed 4:1; current event journals only",
        "total_registered_bins": OLD_TOTAL,
        "post_saturation_baseline_bins": OLD_BASELINE,
        "post_saturation_baseline_percent": OLD_BASELINE / OLD_TOTAL * 100,
        "common_residual_bins": len(registry),
        "coverage_rule": "successful validated native-toggle IDs only; count only IDs in the exact shared 11,964-bin registry; timeouts contribute compute-hours but zero coverage",
        "projection_method": "fit beta by least-squares through-origin y=beta*ln(1+h) to each observed curve; continue its log slope from the last observed point to 5 simulator compute-hours/arm; projections are exploratory, not observed",
        "limitations": [
            "The profiled 50-job run is a single paired campaign and was still in progress when this plot was generated.",
            "Trials 40–44 are a five-run mean at 0.25 compute-hours/trial; they are not the same input mixture as the 50-job CRV:Directed profile campaign.",
            "Only exactly shared native-toggle IDs are compared; the 50-job study's additional toggles and ten custom bins are excluded.",
            "The log curves extrapolate beyond measured data and must not be described as observed coverage or a confirmatory policy effect.",
        ],
        "source_event_journal_sha256": source_hashes,
        "curves": curves,
        "projected_adaptive_minus_static": {},
        "measured_deltas": {},
    }
    for cohort, static_key, adaptive_key in (
        ("trials_40_44_mean", "old_static", "old_adaptive"),
        ("profiled_50job_interim", "new_static", "new_adaptive"),
    ):
        static_gain = curves[static_key]["projected_gain_at_5h"]
        adaptive_gain = curves[adaptive_key]["projected_gain_at_5h"]
        data["projected_adaptive_minus_static"][cohort] = {
            "gain_delta_bins": adaptive_gain - static_gain,
            "relative_to_static_projected_gain_percent": (adaptive_gain - static_gain) / static_gain * 100 if static_gain else 0.0,
            "formula": "100 * (Adaptive projected incremental gain - Static projected incremental gain) / Static projected incremental gain",
        }
    old_delta = old_endpoint_bins["adaptive"] - old_endpoint_bins["static"]
    static17 = curves["new_static"]["equal_17_attempts_checkpoint"]
    adaptive17 = curves["new_adaptive"]["equal_17_attempts_checkpoint"]
    new_delta = adaptive17["shared_toggle_bins"] - static17["shared_toggle_bins"]
    full_delta = adaptive17["full_registry_bins"] - static17["full_registry_bins"]
    data["measured_deltas"] = {
        "trials_40_44": {
            "basis": "mean over five paired trials at the frozen 0.25 compute-hours/arm endpoint",
            "static_shared_toggle_bins": old_endpoint_bins["static"],
            "adaptive_shared_toggle_bins": old_endpoint_bins["adaptive"],
            "gain_delta_bins": old_delta,
            "relative_percent": old_delta / old_endpoint_bins["static"] * 100,
        },
        "profiled_50job_17_attempts": {
            "basis": "interim, equal 17 completed job attempts per arm; timeout outcomes retained",
            "attempts_per_arm": 17,
            "static_shared_toggle_bins": static17["shared_toggle_bins"],
            "adaptive_shared_toggle_bins": adaptive17["shared_toggle_bins"],
            "static_compute_hours": static17["compute_hours"],
            "adaptive_compute_hours": adaptive17["compute_hours"],
            "static_successes": static17["successful_jobs"],
            "static_timeouts": static17["timed_out_jobs"],
            "adaptive_successes": adaptive17["successful_jobs"],
            "adaptive_timeouts": adaptive17["timed_out_jobs"],
            "gain_delta_bins": new_delta,
            "relative_percent": new_delta / static17["shared_toggle_bins"] * 100,
            "static_full_registry_bins": static17["full_registry_bins"],
            "adaptive_full_registry_bins": adaptive17["full_registry_bins"],
            "full_registry_delta_bins": full_delta,
            "full_registry_relative_percent": full_delta / static17["full_registry_bins"] * 100,
        },
        "measured_delta_formula": "100 * (Adaptive measured incremental bins - Static measured incremental bins) / Static measured incremental bins",
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    args.output_svg.parent.mkdir(parents=True, exist_ok=True)
    args.output_svg.write_text(svg_chart(data))
    print(json.dumps({
        "svg": str(args.output_svg), "json": str(args.output_json),
        "static_observed": [curves[k]["observed_end_compute_hours"] for k in ("old_static", "new_static")],
        "adaptive_observed": [curves[k]["observed_end_compute_hours"] for k in ("old_adaptive", "new_adaptive")],
        "projection_percent_at_5h": {k: round(v["projected_total_percent_at_5h"], 4) for k, v in curves.items()},
    }, indent=2))


if __name__ == "__main__":
    main()
