"""Create a descriptive coverage-versus-compute-time figure for trials 1-25."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics
from pathlib import Path

from summarize_mediumboom_open_toggle_replicates import arm_result


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STUDY = ROOT / "artifacts_mediumboom/kapil_qualification/open_toggle_1000_execution_study"
GRID = [i / 100 for i in range(91)]
TOTAL_POINTS = 42_000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def count_at(trace: list[dict], hours: float, target: int) -> int:
    bins = 0
    for row in trace:
        if float(row["compute_hours"]) > hours:
            break
        exact = float(row["sound_closure"]) * target
        rounded = round(exact)
        if abs(exact - rounded) > 1e-5:
            raise ValueError(f"non-integral toggle-bin closure: {exact}")
        bins = rounded
    return bins


def load_cohort(study: Path) -> tuple[list[dict], int]:
    prereg = json.loads((study / "preregistration.json").read_text())
    target = int(prereg["frozen_residual_open_point_count"])
    trials = []
    seen_inputs = set()
    for number in range(1, 26):
        trial_id = f"trial_{number:02d}"
        trial = study / "inputs" / trial_id
        frozen = json.loads((trial / "preregistration.json").read_text())
        hashes = {row["job_id"]: row["elf_sha256"] for row in frozen["input_hashes"]}
        if len(hashes) != 10:
            raise ValueError(f"expected ten frozen jobs: {trial_id}")
        for digest in hashes.values():
            if digest in seen_inputs:
                raise ValueError(f"reused ELF across trials: {trial_id}")
            seen_inputs.add(digest)
        arms = {}
        for arm in ("static_hybrid", "adaptive"):
            result = arm_result(trial, arm, target, 0.9)
            if result["completed_jobs"] != 10 or len(result["jobs"]) != 10:
                raise ValueError(f"incomplete arm catalog: {trial_id}/{arm}")
            for job in result["jobs"]:
                if job["status"] == "success":
                    if (job["input_elf_sha256"] != hashes[job["job_id"]]
                            or not job["coverage_database_verified"]
                            or not job["coverage_dat_sha256"]
                            or not job["evidence_sha256"]
                            or not job["sim_log_sha256"]):
                        raise ValueError(f"evidence/hash validation failed: {trial_id}/{arm}/{job['job_id']}")
                elif job["status"] == "timeout":
                    if job["new_target_bins"] != 0 or "timeout" not in job["error_summary"].lower():
                        raise ValueError(f"invalid timeout record: {trial_id}/{arm}/{job['job_id']}")
                else:
                    raise ValueError(f"unexpected status: {trial_id}/{arm}/{job['job_id']}/{job['status']}")
            arms[arm] = result
        if arms["static_hybrid"]["campaign_fingerprint"] != arms["adaptive"]["campaign_fingerprint"]:
            raise ValueError(f"paired fingerprint mismatch: {trial_id}")
        trials.append({
            "trial_id": trial_id,
            "input_preregistration_sha256": sha256(trial / "preregistration.json"),
            "event_journals": {
                arm: [{"path": str(path.relative_to(study)), "sha256": sha256(path)}
                      for path in [trial / arm / "events.jsonl"] + sorted(
                          (trial / arm / "segments").glob("resume_*/events.jsonl")) if path.is_file()]
                for arm in ("static_hybrid", "adaptive")
            },
            "summary_sha256": {
                arm: sha256(trial / arm / "summary.json")
                for arm in ("static_hybrid", "adaptive")
            },
            "arms": arms,
        })
    return trials, target


def bootstrap_bands(values: dict[str, list[list[int]]], samples: int = 2000) -> dict[str, list[dict]]:
    rng = random.Random(20260924)
    arms = ("static_hybrid", "adaptive")
    boot = {arm: [[] for _ in GRID] for arm in arms}
    n = len(values["static_hybrid"])
    for _ in range(samples):
        indices = [rng.randrange(n) for _ in range(n)]
        for arm in arms:
            curves = values[arm]
            for point in range(len(GRID)):
                boot[arm][point].append(statistics.mean(curves[i][point] for i in indices))
    result = {}
    for arm in arms:
        result[arm] = []
        for point, hours in enumerate(GRID):
            ordered = sorted(boot[arm][point])
            result[arm].append({
                "compute_hours": hours,
                "mean_percent": statistics.mean(values[arm][i][point] for i in range(n)),
                "bootstrap_95_percent_ci": [ordered[49], ordered[1949]],
            })
    return result


def svg_plot(curves: dict[str, list[dict]], target: int, catalog_hours: dict[str, float]) -> str:
    width, height = 1120, 680
    left, right, top, bottom = 102, 44, 86, 105
    plot_w, plot_h = width - left - right, height - top - bottom
    x_max, y_min, y_max = 0.9, 71.2, 72.25
    x = lambda hour: left + hour / x_max * plot_w
    y = lambda percent: top + (y_max - percent) / (y_max - y_min) * plot_h
    navy, muted, grid = "#17324d", "#5c6b78", "#dce2e6"
    series = {
        "static_hybrid": ("#197b9b", "Static-Hybrid"),
        "adaptive": ("#d97924", "Adaptive"),
    }
    pieces = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        '<title id="title">Total native toggle coverage versus compute time in the first 25 MediumBOOM trials</title>',
        '<desc id="desc">Mean overall coverage, including the approximately 30,036 bins already hit at baseline, over simulator compute-hours per arm. The denominator is 42,000 catalog bins. Pointwise 95 percent paired-trial bootstrap intervals. This retrospective first-25 cohort is descriptive; the later frozen holdout is analyzed separately.</desc>',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{left}" y="37" fill="{navy}" font-family="Arial, sans-serif" font-size="22" font-weight="bold">Total native toggle coverage versus compute time</text>',
        f'<text x="{left}" y="60" fill="{muted}" font-family="Arial, sans-serif" font-size="13">First 25 completed paired trials · 30,036 / 42,000 bins already covered at baseline · descriptive cohort</text>',
    ]
    for pct in (71.2, 71.4, 71.6, 71.8, 72.0, 72.2):
        yy = y(pct)
        pieces.append(f'<line x1="{left}" y1="{yy:.2f}" x2="{width-right}" y2="{yy:.2f}" stroke="{grid}" stroke-width="1"/>')
        pieces.append(f'<text x="{left-13}" y="{yy+4:.2f}" text-anchor="end" fill="{muted}" font-family="Arial, sans-serif" font-size="12">{pct:.1f}%</text>')
    for hour in (0, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9):
        xx = x(hour)
        pieces.append(f'<line x1="{xx:.2f}" y1="{top}" x2="{xx:.2f}" y2="{height-bottom}" stroke="{grid}" stroke-width="1"/>')
        pieces.append(f'<text x="{xx:.2f}" y="{height-bottom+23}" text-anchor="middle" fill="{muted}" font-family="Arial, sans-serif" font-size="12">{hour:.2f}</text>')
    pieces.extend([
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height-bottom}" stroke="{navy}" stroke-width="1"/>',
        f'<line x1="{left}" y1="{height-bottom}" x2="{width-right}" y2="{height-bottom}" stroke="{navy}" stroke-width="1"/>',
        f'<text x="{left + plot_w/2:.1f}" y="{height-31}" text-anchor="middle" fill="{navy}" font-family="Arial, sans-serif" font-size="14">Cumulative simulator compute time per arm (hours)</text>',
        f'<text x="24" y="{top + plot_h/2:.1f}" transform="rotate(-90 24 {top + plot_h/2:.1f})" text-anchor="middle" fill="{navy}" font-family="Arial, sans-serif" font-size="14">Mean total toggle coverage (%) · axis truncated</text>',
    ])
    for arm, (color, label) in series.items():
        rows = curves[arm]
        upper = " ".join(f'{x(row["compute_hours"]):.2f},{y(row["bootstrap_95_percent_ci"][1]):.2f}' for row in rows)
        lower = " ".join(f'{x(row["compute_hours"]):.2f},{y(row["bootstrap_95_percent_ci"][0]):.2f}' for row in reversed(rows))
        pieces.append(f'<polygon points="{upper} {lower}" fill="{color}" fill-opacity="0.15"/>')
        path = " ".join(("M" if i == 0 else "L") + f' {x(row["compute_hours"]):.2f} {y(row["mean_percent"]):.2f}' for i, row in enumerate(rows))
        pieces.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="3"/>')
    legend_x, legend_y = left + 8, top + 20
    for i, (arm, (color, label)) in enumerate(series.items()):
        yy = legend_y + i * 25
        pieces.append(f'<line x1="{legend_x}" y1="{yy}" x2="{legend_x+29}" y2="{yy}" stroke="{color}" stroke-width="3"/>')
        pieces.append(f'<text x="{legend_x+38}" y="{yy+4}" fill="{navy}" font-family="Arial, sans-serif" font-size="12">{label} mean (band: 95% CI)</text>')
    pieces.append(f'<text x="{width-right}" y="{top+24}" text-anchor="end" fill="{muted}" font-family="Arial, sans-serif" font-size="11">Mean full-catalog time: Static {catalog_hours["static_hybrid"]:.2f} h · Adaptive {catalog_hours["adaptive"]:.2f} h</text>')
    pieces.append(f'<text x="{left}" y="{height-10}" fill="{muted}" font-family="Arial, sans-serif" font-size="11">95% intervals resample paired trials; curves stay flat after a catalog has no more completed jobs.</text>')
    pieces.append("</svg>")
    return "\n".join(pieces) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", type=Path, default=DEFAULT_STUDY)
    parser.add_argument("--output-prefix", type=Path)
    args = parser.parse_args()
    study = args.study.resolve()
    prefix = args.output_prefix or study / "coverage_time_first25"
    trials, target = load_cohort(study)
    values = {arm: [] for arm in ("static_hybrid", "adaptive")}
    catalog_hours = {arm: [] for arm in values}
    for trial in trials:
        for arm in values:
            result = trial["arms"][arm]
            trace = result["coverage_trace"]
            # The campaign begins after a common baseline of already-covered bins.
            # Plot total-catalog coverage, not only closure of the frozen residual set.
            values[arm].append([(TOTAL_POINTS - target + count_at(trace, hour, target)) / TOTAL_POINTS * 100
                                for hour in GRID])
            catalog_hours[arm].append(result["compute_hours"])
    mean_catalog_hours = {arm: statistics.mean(hours) for arm, hours in catalog_hours.items()}
    curves = bootstrap_bands(values)
    report = {
        "cohort": "trials_01_25",
        "interpretation": "descriptive retrospective cohort; the registered binding-budget holdout is trials_26_50",
        "endpoint": "mean total native toggle coverage (baseline covered bins plus residual bins closed) versus cumulative simulator compute-hours per arm",
        "total_catalog_points": TOTAL_POINTS,
        "baseline_covered_points": TOTAL_POINTS - target,
        "target_bins": target,
        "paired_trials": len(trials),
        "bootstrap": {"resamples": 2000, "seed": 20260924, "interval": "pointwise percentile 95% paired-trial bootstrap"},
        "mean_full_catalog_compute_hours": mean_catalog_hours,
        "curves": curves,
        "source_preregistration_sha256": sha256(study / "preregistration.json"),
        "trials": [{
            "trial_id": trial["trial_id"],
            "input_preregistration_sha256": trial["input_preregistration_sha256"],
            "event_journals": trial["event_journals"],
            "summary_sha256": trial["summary_sha256"],
            "curve_percent_by_grid": {
                arm: values[arm][i] for arm in values
            },
        } for i, trial in enumerate(trials)],
    }
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    prefix.with_suffix(".svg").write_text(svg_plot(curves, target, mean_catalog_hours))
    print(json.dumps({"json": str(prefix.with_suffix(".json")), "svg": str(prefix.with_suffix(".svg")),
                      "paired_trials": len(trials), "target_bins": target, "total_catalog_points": TOTAL_POINTS,
                      "mean_full_catalog_compute_hours": mean_catalog_hours,
                      "mean_bins_at_0_25h": {arm: round(statistics.mean(curve[25] for curve in values[arm]) * target / 100)
                                             for arm in values}}, indent=2))


if __name__ == "__main__":
    main()
