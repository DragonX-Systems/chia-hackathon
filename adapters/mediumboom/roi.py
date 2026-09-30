"""Write legacy experiment ROI artifacts."""

from __future__ import annotations

import json
from pathlib import Path


def hours_to_hit(payload: dict, arm: str, thr: float) -> dict:
    xs: list[float | None] = []
    for r in payload["arms"]:
        if r["arm"] != arm:
            continue
        h = None
        for t in r["trace"]:
            if t["hit_frac"] >= thr:
                h = t["hours"]
                break
        xs.append(h)
    finite = [x for x in xs if x is not None]
    return {
        "threshold": thr,
        "hours_mean": round(sum(finite) / len(finite), 2) if finite else None,
        "reached_seeds": len(finite),
        "n_seeds": len(xs),
    }


def _ratio(num: dict, den: dict) -> float | None:
    if num["hours_mean"] and den["hours_mean"]:
        return num["hours_mean"] / den["hours_mean"]
    return None


def write_roi(payload: dict, out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    hit = payload["mean_final_hit"]
    cl = payload["mean_final_closure"]
    arms = [n for n in ("baseline", "enh_formal", "enh_directed", "enh_v1") if n in hit]
    h40 = {arm: hours_to_hit(payload, arm, 0.40) for arm in arms}
    h50 = {arm: hours_to_hit(payload, arm, 0.50) for arm in arms}
    ratios = {
        arm: _ratio(h40[arm], h40["baseline"]) for arm in arms if arm != "baseline" and "baseline" in h40
    }
    rec = {
        "n_points": payload.get("n_points", 5000),
        "n_seeds": payload.get("n_seeds"),
        "seed": payload.get("yardstick_seed", 20260910),
        "mean_final_hit": hit,
        "mean_final_closure": cl,
        "hours_to_40pct_hit": {**h40, "ratio_over_baseline": ratios},
        "hours_to_50pct_hit": h50,
        "speedup_hours_to_90pct_of_baseline_final": payload.get(
            "speedup_hours_to_90pct_of_baseline_final"
        ),
        "honesty": (
            "leftover-rich wall-hour MODEL, 5000 points, ≥10 seeds, not MediumBOOM 38-point SoC, not Ray. "
            "Primary hours claim is hours to 40% hit. "
            "hours-to-90%-of-baseline-final can be ~1× because that bar sits inside the shared CRV prefix."
        ),
    }
    (out_dir / "roi.json").write_text(json.dumps(rec, indent=2) + "\n")

    def row(arm: str) -> str:
        return (
            f"| `{arm}` | {hit[arm]:.1%} | {cl[arm]:.1%} |"
            if arm != "baseline"
            else f"| baseline | {hit[arm]:.1%} | {cl[arm]:.1%} |"
        )

    def hrow(arm: str) -> str:
        h = h40[arm]
        return f"| {arm} | {h['hours_mean']} | {h['reached_seeds']}/{h['n_seeds']} |"

    ratio_lines = []
    for arm, r in ratios.items():
        if r:
            cut = (1.0 - r) * 100
            ratio_lines.append(f"- `{arm}` / baseline = **{r:.3f}** → {cut:.0f}% fewer hours to 40% hit.")
        else:
            ratio_lines.append(f"- `{arm}` / baseline = n/a")

    md = f"""# Leftover-rich hours ROI (5000 points, {payload.get('n_seeds')} seeds)

**5000-point** freeze, seed `20260910`. Wall-hour MODEL, not MediumBOOM 38-point SoC, not Ray.
Catalog `oracles-20260907` unchanged. `--oracles none --backend leftover`.

Arms: industrial baseline; **enh_formal** (formal prune only); **enh_directed** (directed last-mile only); **enh_v1** (both). Genome retune is not shown (0 `constraint_tuning` on judged leftover traces).

## Coverage in a 24 h budget

| Arm | Mean hit | Mean closure |
|-----|---------:|-------------:|
{chr(10).join(row(a) for a in arms)}

## Hours to 40% hit (primary)

| Arm | Hours to 40% hit | Seeds reached |
|-----|-----------------:|--------------:|
{chr(10).join(hrow(a) for a in arms)}

{chr(10).join(ratio_lines)}

`speedup_hours_to_90pct_of_baseline_final` can stay ~1×: that bar is ~36% hit, still in the shared CRV prefix. Do not quote it as the last-mile result.
"""
    (out_dir / "ROI.md").write_text(md)
    return rec
