"""Paired Static-Hybrid versus Adaptive Chialoop comparison."""

from __future__ import annotations

import json
import random
import statistics
from html import escape
from pathlib import Path
from typing import Any, Iterable, Mapping


def compare_paired_runs(
    static_runs: Iterable[Mapping[str, Any]],
    adaptive_runs: Iterable[Mapping[str, Any]],
    *,
    bootstrap_samples: int = 5000,
    bootstrap_seed: int = 20260922,
) -> dict[str, Any]:
    static_by_trial = _index_runs(
        static_runs,
        expected_arm="static_hybrid",
    )
    adaptive_by_trial = _index_runs(
        adaptive_runs,
        expected_arm="adaptive",
    )
    if set(static_by_trial) != set(adaptive_by_trial):
        raise ValueError("static and adaptive trial IDs must match exactly")
    trial_ids = sorted(static_by_trial)
    if not trial_ids:
        raise ValueError("at least one paired trial is required")

    for trial_id in trial_ids:
        static = static_by_trial[trial_id]
        adaptive = adaptive_by_trial[trial_id]
        for field in ("campaign_fingerprint", "runtime_name"):
            if not static.get(field) or not adaptive.get(field):
                raise ValueError(
                    f"paired trial {trial_id} is missing required {field}"
                )
            if static[field] != adaptive[field]:
                raise ValueError(
                    f"paired trial {trial_id} has different {field} values"
                )

    static_success = sum(bool(static_by_trial[t]["reached_target"]) for t in trial_ids)
    adaptive_success = sum(bool(adaptive_by_trial[t]["reached_target"]) for t in trial_ids)
    all_reached = static_success == adaptive_success == len(trial_ids)

    result: dict[str, Any] = {
        "paired_trials": len(trial_ids),
        "static_reached_target": static_success,
        "adaptive_reached_target": adaptive_success,
        "primary_comparison_valid": all_reached,
        "median_final_sound_closure": {
            "static_hybrid": statistics.median(
                float(static_by_trial[t]["final_sound_closure"]) for t in trial_ids
            ),
            "adaptive": statistics.median(
                float(adaptive_by_trial[t]["final_sound_closure"]) for t in trial_ids
            ),
        },
        "plot_data": {
            "coverage_traces": {
                "static_hybrid": [
                    static_by_trial[t].get("coverage_trace", []) for t in trial_ids
                ],
                "adaptive": [
                    adaptive_by_trial[t].get("coverage_trace", []) for t in trial_ids
                ],
            },
            "resource_timeline": {
                "trial_id": trial_ids[0],
                "static_hybrid": static_by_trial[trial_ids[0]].get(
                    "resource_timeline", []
                ),
                "adaptive": adaptive_by_trial[trial_ids[0]].get(
                    "resource_timeline", []
                ),
            },
        },
    }

    if all_reached:
        static_compute = [float(static_by_trial[t]["compute95"]) for t in trial_ids]
        adaptive_compute = [float(adaptive_by_trial[t]["compute95"]) for t in trial_ids]
        if any(value <= 0 for value in static_compute + adaptive_compute):
            raise ValueError("reached-target runs require positive Compute95 values")
        paired_reductions = [
            1.0 - adaptive / static
            for static, adaptive in zip(static_compute, adaptive_compute)
        ]
        result["compute95"] = {
            "static_median": statistics.median(static_compute),
            "adaptive_median": statistics.median(adaptive_compute),
            "median_paired_reduction": statistics.median(paired_reductions),
            "bootstrap_95pct_ci": _bootstrap_median_ci(
                paired_reductions,
                samples=bootstrap_samples,
                seed=bootstrap_seed,
            ),
        }
    else:
        result["compute95"] = None
        result["reason"] = (
            "At least one arm failed to reach the target; compare final closure "
            "at the common budget instead of calculating a speedup."
        )
    return result


def _index_runs(
    runs: Iterable[Mapping[str, Any]],
    *,
    expected_arm: str,
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for run in runs:
        trial_id = str(run["trial_id"])
        if trial_id in indexed:
            raise ValueError(f"duplicate {expected_arm} trial ID: {trial_id}")
        arm = str(run.get("arm", ""))
        if arm != expected_arm:
            raise ValueError(
                f"trial {trial_id} has arm={arm!r}; expected {expected_arm!r}"
            )
        indexed[trial_id] = dict(run)
    return indexed


def write_comparison(comparison: Mapping[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "comparison.json").write_text(
        json.dumps(comparison, indent=2, sort_keys=True) + "\n"
    )
    compute = comparison.get("compute95")
    if compute is None:
        primary = "Compute95 comparison: not valid because at least one run missed the target."
    else:
        low, high = compute["bootstrap_95pct_ci"]
        primary = (
            f"Median Compute95 was {compute['adaptive_median']:.4g} h for Adaptive "
            f"Chialoop versus {compute['static_median']:.4g} h for Static-Hybrid. "
            f"Median paired reduction: {100 * compute['median_paired_reduction']:.2f}% "
            f"(bootstrap 95% CI: {100 * low:.2f}% to {100 * high:.2f}%)."
        )
    markdown = (
        "# Chialoop paired comparison\n\n"
        f"Paired trials: {comparison['paired_trials']}\n\n"
        f"Static-Hybrid reached target: {comparison['static_reached_target']}/"
        f"{comparison['paired_trials']}\n\n"
        f"Adaptive Chialoop reached target: {comparison['adaptive_reached_target']}/"
        f"{comparison['paired_trials']}\n\n"
        f"{primary}\n"
    )
    (output_dir / "comparison.md").write_text(markdown)
    _write_coverage_svg(comparison, output_dir / "coverage_vs_compute_hours.svg")
    _write_resource_timeline_svg(comparison, output_dir / "resource_timeline.svg")


def _bootstrap_median_ci(
    values: list[float], *, samples: int, seed: int
) -> tuple[float, float]:
    if not values:
        raise ValueError("cannot bootstrap an empty sample")
    if samples <= 0:
        raise ValueError("bootstrap_samples must be positive")
    rng = random.Random(seed)
    medians = sorted(
        statistics.median(rng.choice(values) for _ in values)
        for _ in range(samples)
    )
    low_index = int(0.025 * (len(medians) - 1))
    high_index = int(0.975 * (len(medians) - 1))
    return medians[low_index], medians[high_index]


def _write_coverage_svg(comparison: Mapping[str, Any], path: Path) -> None:
    width, height = 900, 520
    left, right, top, bottom = 80, 30, 35, 70
    plot_width = width - left - right
    plot_height = height - top - bottom
    traces = comparison.get("plot_data", {}).get("coverage_traces", {})
    all_points = [
        point
        for arm_traces in traces.values()
        for trace in arm_traces
        for point in trace
    ]
    x_max = max((float(point.get("compute_hours", 0.0)) for point in all_points), default=1.0)
    x_max = max(x_max, 1e-12)

    def xy(point: Mapping[str, Any]) -> tuple[float, float]:
        x = left + float(point.get("compute_hours", 0.0)) / x_max * plot_width
        y = top + (1.0 - float(point.get("sound_closure", 0.0))) * plot_height
        return x, y

    elements = _svg_axes(width, height, left, right, top, bottom, x_max, "Cumulative compute-hours")
    colors = {"static_hybrid": "#b91c1c", "adaptive": "#1d4ed8"}
    for arm, arm_traces in traces.items():
        for trace in arm_traces:
            points = " ".join(f"{x:.2f},{y:.2f}" for x, y in map(xy, trace))
            if points:
                elements.append(
                    f'<polyline points="{points}" fill="none" '
                    f'stroke="{colors.get(arm, "#334155")}" stroke-width="2.5" '
                    f'opacity="0.65" />'
                )
    elements.extend(
        [
            '<line x1="650" y1="28" x2="685" y2="28" stroke="#b91c1c" stroke-width="3" />',
            '<text x="692" y="33" font-size="13">Static-Hybrid</text>',
            '<line x1="650" y1="50" x2="685" y2="50" stroke="#1d4ed8" stroke-width="3" />',
            '<text x="692" y="55" font-size="13">Adaptive Chialoop</text>',
            f'<text x="{width / 2}" y="20" text-anchor="middle" font-size="18" font-weight="600">Sound closure versus compute</text>',
        ]
    )
    path.write_text(_svg_document(width, height, elements))


def _write_resource_timeline_svg(comparison: Mapping[str, Any], path: Path) -> None:
    timeline = comparison.get("plot_data", {}).get("resource_timeline", {})
    arms = ("static_hybrid", "adaptive")
    rows: list[tuple[str, int]] = []
    for arm in arms:
        slots = sorted({int(item["compute_slot"]) for item in timeline.get(arm, [])})
        rows.extend((arm, slot) for slot in slots)
    width = 1000
    row_height = 42
    top, left, right, bottom = 55, 180, 30, 55
    height = max(220, top + len(rows) * row_height + bottom)
    plot_width = width - left - right
    all_items = [item for arm in arms for item in timeline.get(arm, [])]
    x_max = max((float(item.get("end_seconds", 0.0)) for item in all_items), default=1.0)
    x_max = max(x_max, 1e-12)
    colors = {"crv": "#16a34a", "directed": "#7c3aed", "formal": "#f59e0b"}
    elements = [
        f'<rect width="{width}" height="{height}" fill="white" />',
        f'<text x="{width / 2}" y="24" text-anchor="middle" font-size="18" font-weight="600">Asynchronous resource timeline</text>',
        f'<text x="{width / 2}" y="43" text-anchor="middle" font-size="12">trial {escape(str(timeline.get("trial_id", "")))}</text>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height - bottom}" stroke="#111827" />',
        f'<line x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}" stroke="#111827" />',
    ]
    row_index = {row: index for index, row in enumerate(rows)}
    for arm, slot in rows:
        y = top + row_index[(arm, slot)] * row_height
        label = f"{arm} / resource {slot}"
        elements.append(f'<text x="{left - 8}" y="{y + 24}" text-anchor="end" font-size="12">{escape(label)}</text>')
        elements.append(f'<line x1="{left}" y1="{y + row_height}" x2="{width - right}" y2="{y + row_height}" stroke="#e2e8f0" />')
    for arm in arms:
        for item in timeline.get(arm, []):
            row = row_index[(arm, int(item["compute_slot"]))]
            x = left + float(item["start_seconds"]) / x_max * plot_width
            end = left + float(item["end_seconds"]) / x_max * plot_width
            bar_width = max(2.0, end - x)
            y = top + row * row_height + 8
            engine = str(item.get("engine", ""))
            elements.append(
                f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_width:.2f}" height="25" '
                f'fill="{colors.get(engine, "#64748b")}" opacity="0.85">'
                f'<title>{escape(str(item.get("job_id", "")))} ({escape(engine)})</title></rect>'
            )
    for tick in range(6):
        value = x_max * tick / 5
        x = left + plot_width * tick / 5
        elements.append(f'<line x1="{x:.2f}" y1="{height - bottom}" x2="{x:.2f}" y2="{height - bottom + 6}" stroke="#111827" />')
        elements.append(f'<text x="{x:.2f}" y="{height - bottom + 22}" text-anchor="middle" font-size="11">{value:.3g}</text>')
    elements.append(f'<text x="{left + plot_width / 2}" y="{height - 10}" text-anchor="middle" font-size="13">Wall time (seconds)</text>')
    path.write_text(_svg_document(width, height, elements))


def _svg_axes(
    width: int,
    height: int,
    left: int,
    right: int,
    top: int,
    bottom: int,
    x_max: float,
    x_label: str,
) -> list[str]:
    plot_width = width - left - right
    plot_height = height - top - bottom
    elements = [
        f'<rect width="{width}" height="{height}" fill="white" />',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height - bottom}" stroke="#111827" />',
        f'<line x1="{left}" y1="{height - bottom}" x2="{width - right}" y2="{height - bottom}" stroke="#111827" />',
    ]
    for tick in range(6):
        fraction = tick / 5
        x = left + plot_width * fraction
        y = top + plot_height * (1 - fraction)
        elements.extend(
            [
                f'<line x1="{x:.2f}" y1="{height - bottom}" x2="{x:.2f}" y2="{height - bottom + 6}" stroke="#111827" />',
                f'<text x="{x:.2f}" y="{height - bottom + 22}" text-anchor="middle" font-size="11">{x_max * fraction:.3g}</text>',
                f'<line x1="{left - 6}" y1="{y:.2f}" x2="{left}" y2="{y:.2f}" stroke="#111827" />',
                f'<text x="{left - 10}" y="{y + 4:.2f}" text-anchor="end" font-size="11">{fraction:.1f}</text>',
            ]
        )
    elements.extend(
        [
            f'<text x="{left + plot_width / 2}" y="{height - 14}" text-anchor="middle" font-size="13">{escape(x_label)}</text>',
            f'<text x="18" y="{top + plot_height / 2}" text-anchor="middle" font-size="13" transform="rotate(-90 18 {top + plot_height / 2})">Sound closure</text>',
        ]
    )
    return elements


def _svg_document(width: int, height: int, elements: list[str]) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">\n'
        + "\n".join(elements)
        + "\n</svg>\n"
    )
