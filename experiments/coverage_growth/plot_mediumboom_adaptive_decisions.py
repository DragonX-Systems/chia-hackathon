"""Plot the online adaptive dispatch decisions from the first 25 frozen trials.

The plot shows decisions actually recorded by the controller, not a hindsight
ordering or an adaptive-versus-static effect estimate. A compact JSON export
keeps the plotted decisions and their event-file hashes auditable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STUDY = ROOT / "artifacts_mediumboom/kapil_qualification/open_toggle_1000_execution_study"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def reason_kind(reason: str) -> str:
    if reason.startswith("initial probe for lane"):
        return "initial_probe"
    if reason.startswith("periodic exploration of least-sampled lane"):
        return "periodic_exploration"
    if reason.startswith("highest recent yield="):
        return "recent_yield"
    raise ValueError(f"unknown adaptive decision reason: {reason}")


def collect(study: Path) -> list[dict]:
    trials = []
    for number in range(1, 26):
        trial_id = f"trial_{number:02d}"
        trial = study / "inputs" / trial_id
        arm = trial / "adaptive"
        if not (arm / "summary.json").is_file():
            raise ValueError(f"incomplete adaptive arm: {trial_id}")
        event_files = [arm / "events.jsonl"] + sorted((arm / "segments").glob("resume_*/events.jsonl"))
        if not event_files[0].is_file():
            raise ValueError(f"missing primary event journal: {trial_id}")
        events = [json.loads(line) for path in event_files for line in path.read_text().splitlines()
                  if line.strip()]
        first_dispatch: dict[str, dict] = {}
        completions: dict[str, dict] = {}
        for event in events:
            if event.get("event") == "job_dispatched":
                first_dispatch.setdefault(event["job_id"], event)
            elif event.get("event") == "job_completed":
                if event["job_id"] in completions:
                    raise ValueError(f"duplicate completion: {trial_id}/{event['job_id']}")
                completions[event["job_id"]] = event
        frozen = {row["job_id"] for row in json.loads((trial / "preregistration.json").read_text())["input_hashes"]}
        if set(first_dispatch) != frozen or set(completions) != frozen or len(frozen) != 10:
            raise ValueError(f"dispatch/completion catalog mismatch: {trial_id}")
        order = sorted(first_dispatch, key=lambda job_id: first_dispatch[job_id]["timestamp"])
        decisions = []
        for position, job_id in enumerate(order, 1):
            dispatch = first_dispatch[job_id]
            completion = completions[job_id]
            decisions.append({
                "position": position,
                "job_id": job_id,
                "engine": dispatch["engine"],
                "reason_kind": reason_kind(dispatch["reason"]),
                "reason": dispatch["reason"],
                "status": completion["result"]["status"],
            })
        if Counter(row["engine"] for row in decisions) != {"crv": 8, "directed": 2}:
            raise ValueError(f"unexpected engine mix: {trial_id}")
        if decisions[0]["job_id"] != "crv_00" or decisions[1]["job_id"] != "directed_00":
            raise ValueError(f"initial-probe order changed: {trial_id}")
        for decision in decisions:
            expected = ("initial_probe" if decision["position"] <= 2 else
                        "periodic_exploration" if decision["position"] == 10 else "recent_yield")
            if decision["reason_kind"] != expected or decision["status"] not in ("success", "timeout"):
                raise ValueError(f"unexpected decision or outcome: {trial_id}/{decision['job_id']}")
        trials.append({
            "trial_id": trial_id,
            "campaign_fingerprint": json.loads((arm / "summary.json").read_text())["campaign_fingerprint"],
            "event_files": [{"path": str(path.relative_to(study)), "sha256": sha256(path)}
                            for path in event_files],
            "decisions": decisions,
        })
    return trials


def draw_svg(trials: list[dict]) -> str:
    width, height = 1090, 920
    left, cell_w, cell_h, gap = 104, 73, 21, 3
    grid_top = 234
    grid_w = 10 * cell_w
    dark = "#17324d"
    muted = "#5c6b78"
    crv = "#197b9b"
    directed = "#d97924"
    pale = "#e9edf0"
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        '<title id="title">Adaptive dispatch decisions in the first 25 MediumBOOM trials</title>',
        '<desc id="desc">Twenty-five rows show the ten online dispatches in each trial. CRV is teal and directed is orange. The first two positions are lane probes, positions three through nine use recent measured yield, and the tenth is periodic exploration. One timeout is marked with an X.</desc>',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{left}" y="37" fill="{dark}" font-family="Arial, sans-serif" font-size="23" font-weight="bold">Adaptive dispatch decisions across 25 MediumBOOM trials</text>',
        f'<text x="{left}" y="62" fill="{muted}" font-family="Arial, sans-serif" font-size="13">Observed online choices from frozen event journals; not a hindsight ordering or policy-win estimate</text>',
        f'<text x="{left}" y="99" fill="{dark}" font-family="Arial, sans-serif" font-size="14" font-weight="bold">Second directed job: dispatch position</text>',
    ]
    second_directed = Counter(
        next(row["position"] for row in trial["decisions"] if row["job_id"] == "directed_01")
        for trial in trials
    )
    bar_base, bar_height = 165, 50
    for tick in (0, 5, 10):
        y = bar_base - tick * bar_height / 10
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + grid_w}" y2="{y:.1f}" stroke="{pale}" stroke-width="1"/>')
        parts.append(f'<text x="{left - 12}" y="{y + 4:.1f}" text-anchor="end" fill="{muted}" font-family="Arial, sans-serif" font-size="11">{tick}</text>')
    for position in range(1, 11):
        x = left + (position - 1) * cell_w
        count = second_directed[position]
        if count:
            bar_h = count * bar_height / 10
            parts.append(f'<rect x="{x + 19}" y="{bar_base - bar_h:.1f}" width="{cell_w - 38}" height="{bar_h:.1f}" fill="{directed}"/>')
            parts.append(f'<text x="{x + cell_w / 2:.1f}" y="{bar_base - bar_h - 5:.1f}" text-anchor="middle" fill="{dark}" font-family="Arial, sans-serif" font-size="11">{count}</text>')
        parts.append(f'<text x="{x + cell_w / 2:.1f}" y="191" text-anchor="middle" fill="{muted}" font-family="Arial, sans-serif" font-size="11">{position}</text>')
    parts.extend([
        f'<text x="{left + cell_w}" y="215" text-anchor="middle" fill="{muted}" font-family="Arial, sans-serif" font-size="11">initial lane probes</text>',
        f'<text x="{left + cell_w * 5.5:.1f}" y="215" text-anchor="middle" fill="{muted}" font-family="Arial, sans-serif" font-size="11">highest recent measured yield</text>',
        f'<text x="{left + cell_w * 9.5:.1f}" y="215" text-anchor="middle" fill="{muted}" font-family="Arial, sans-serif" font-size="11">explore</text>',
    ])
    for row_index, trial in enumerate(trials):
        y = grid_top + row_index * (cell_h + gap)
        parts.append(f'<text x="{left - 12}" y="{y + 15}" text-anchor="end" fill="{dark}" font-family="Arial, sans-serif" font-size="12">{escape(trial["trial_id"].replace("trial_", "Trial "))}</text>')
        for decision in trial["decisions"]:
            x = left + (decision["position"] - 1) * cell_w
            color = directed if decision["engine"] == "directed" else crv
            letter = "D" if decision["engine"] == "directed" else "C"
            parts.append(f'<rect x="{x + 2}" y="{y}" width="{cell_w - 4}" height="{cell_h}" fill="{color}" rx="2"/>')
            parts.append(f'<text x="{x + cell_w / 2:.1f}" y="{y + 15}" text-anchor="middle" fill="#ffffff" font-family="Arial, sans-serif" font-size="11" font-weight="bold">{letter}</text>')
            if decision["status"] == "timeout":
                parts.append(f'<text x="{x + cell_w - 11}" y="{y + 15}" text-anchor="middle" fill="#ffffff" font-family="Arial, sans-serif" font-size="14" font-weight="bold">×</text>')
    legend_y = grid_top + len(trials) * (cell_h + gap) + 19
    parts.extend([
        f'<rect x="{left}" y="{legend_y - 11}" width="17" height="17" fill="{crv}" rx="2"/>',
        f'<text x="{left + 25}" y="{legend_y + 2}" fill="{dark}" font-family="Arial, sans-serif" font-size="12">C = CRV</text>',
        f'<rect x="{left + 120}" y="{legend_y - 11}" width="17" height="17" fill="{directed}" rx="2"/>',
        f'<text x="{left + 145}" y="{legend_y + 2}" fill="{dark}" font-family="Arial, sans-serif" font-size="12">D = directed</text>',
        f'<text x="{left + 265}" y="{legend_y + 2}" fill="{dark}" font-family="Arial, sans-serif" font-size="12">× = timed out</text>',
        f'<text x="{left}" y="{legend_y + 27}" fill="{muted}" font-family="Arial, sans-serif" font-size="11">Each trial has exactly 8 CRV and 2 directed jobs; position 10 is periodic exploration in every trial.</text>',
        '</svg>',
    ])
    return "\n".join(parts) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--study", type=Path, default=DEFAULT_STUDY)
    parser.add_argument("--output-prefix", type=Path)
    args = parser.parse_args()
    study = args.study.resolve()
    prefix = args.output_prefix or study / "adaptive_decisions_first25"
    trials = collect(study)
    data = {
        "description": "Online adaptive decisions in the first 25 frozen MediumBOOM paired trials",
        "not_a_policy_effect_estimate": True,
        "trial_count": 25,
        "jobs_per_trial": 10,
        "second_directed_position_counts": dict(sorted(Counter(
            next(row["position"] for row in trial["decisions"] if row["job_id"] == "directed_01")
            for trial in trials
        ).items())),
        "trials": trials,
    }
    prefix.parent.mkdir(parents=True, exist_ok=True)
    prefix.with_suffix(".json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    prefix.with_suffix(".svg").write_text(draw_svg(trials))
    print(json.dumps({"json": str(prefix.with_suffix(".json")), "svg": str(prefix.with_suffix(".svg")),
                      "trial_count": len(trials), "second_directed_position_counts": data["second_directed_position_counts"]},
                     indent=2))


if __name__ == "__main__":
    main()
