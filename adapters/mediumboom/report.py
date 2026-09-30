"""Render legacy MediumBOOM experiment reports."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

COLORS = {
    "baseline": "#4b5563",
    "sva_first": "#1d4ed8",
    "sim_max": "#059669",
    "cph_greedy": "#d97706",
    "stage_clean": "#7c3aed",
    "enh_v1": "#db2777",
    "enh_formal": "#7c3aed",
    "enh_directed": "#ea580c",
    "b1": "#64748b",
    "b2": "#2563eb",
    "b3": "#dc2626",
}

ACTION_COLORS = {
    "torture": "#dc2626",
    "riscv_dv": "#2563eb",
    "formal": "#7c3aed",
    "directed": "#d97706",
    "constraint_tuning": "#94a3b8",
}


def _series(arm_rows: list[dict], field: str) -> list[list[tuple[float, float]]]:
    out = []
    for row in arm_rows:
        out.append([(p["hours"], p[field]) for p in row["trace"]])
    return out


def _interp(series: list[tuple[float, float]], x: float) -> float:
    if x <= series[0][0]:
        return series[0][1]
    if x >= series[-1][0]:
        return series[-1][1]
    for (x0, y0), (x1, y1) in zip(series, series[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y1
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return series[-1][1]


def _mean_band(series_list: list[list[tuple[float, float]]], xs: list[float]) -> tuple[list[float], list[float], list[float]]:
    means, los, his = [], [], []
    for x in xs:
        ys = [_interp(s, x) for s in series_list]
        means.append(sum(ys) / len(ys))
        los.append(min(ys))
        his.append(max(ys))
    return means, los, his


def _poly(xs: list[float], ys: list[float], x0: float, y0: float, w: float, h: float, xmax: float, ymax: float) -> str:
    pts = []
    for x, y in zip(xs, ys):
        px = x0 + (x / xmax) * w
        py = y0 + h - (y / ymax) * h
        pts.append(f"{px:.1f},{py:.1f}")
    return " ".join(pts)


def render_svg(payload: dict, path: Path) -> None:
    arms = payload["arms"]
    budget = float(payload["budget_hours"])
    names = []
    seen = set()
    for a in arms:
        if a["arm"] not in seen:
            seen.add(a["arm"])
            names.append(a["arm"])
    colors = dict(COLORS)
    xs = [budget * i / 80 for i in range(81)]
    W, H = 920, 560
    mx, my, mw, mh = 70, 56, 800, 420
    ymax = 1.02

    def band(lo, hi, color):
        top = _poly(xs, hi, mx, my, mw, mh, budget, ymax)
        bot = _poly(list(reversed(xs)), list(reversed(lo)), mx, my, mw, mh, budget, ymax)
        return f'<polygon points="{top} {bot}" fill="{color}" fill-opacity="0.12" stroke="none"/>'

    def line(mean, color, dash=""):
        d = _poly(xs, mean, mx, my, mw, mh, budget, ymax)
        ds = f'stroke-dasharray="{dash}"' if dash else ""
        return f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="2.2" {ds}/>'

    ticks = []
    for i in range(6):
        x = mx + mw * i / 5
        hrs = budget * i / 5
        ticks.append(f'<line x1="{x}" y1="{my+mh}" x2="{x}" y2="{my+mh+6}" stroke="#444"/>')
        ticks.append(
            f'<text x="{x}" y="{my+mh+22}" text-anchor="middle" font-size="12" fill="#333">{hrs:.0f}</text>'
        )
    for i in range(6):
        yv = i / 5
        y = my + mh * (1 - yv / ymax)
        ticks.append(f'<line x1="{mx-6}" y1="{y}" x2="{mx}" y2="{y}" stroke="#444"/>')
        ticks.append(
            f'<text x="{mx-10}" y="{y+4}" text-anchor="end" font-size="12" fill="#333">{int(yv*100)}%</text>'
        )

    layers = []
    legend = []
    lx = 88
    for name in names:
        rows = [a for a in arms if a["arm"] == name]
        color = colors.get(name, "#111")
        hit = _mean_band(_series(rows, "hit_frac"), xs)
        cl = _mean_band(_series(rows, "closure_frac"), xs)
        layers.append(band(hit[1], hit[2], color))
        layers.append(line(hit[0], color))
        layers.append(line(cl[0], color, "5 4"))
        legend.append(f'<rect x="{lx}" y="36" width="12" height="4" fill="{color}"/>')
        legend.append(f'<text x="{lx+16}" y="41" font-size="11" fill="#111">{name}</text>')
        lx += 110

    speed = payload.get("speedup_hours_to_90pct_of_baseline_final")
    if payload.get("backend") == "leftover":
        speed_s = ""
        names = [a["arm"] for a in payload.get("arms", [])]
        n = payload.get("n_seeds", 3)
        pts = payload.get("n_points", 5000)
        if set(names) <= {"baseline", "enh_directed"} and "enh_directed" in names:
            title = (
                f"Enh 3 directed last-mile — {pts} points, {n} seeds "
                f"(24 h hit, not hours-to-40%)"
            )
        elif set(names) <= {"baseline", "enh_formal"} and "enh_formal" in names:
            title = f"Enh 2 formal prune — {pts} points, {n} seeds"
        else:
            title = f"Coverage vs wall-hours — leftover-rich ({pts} points, {n} seeds)"
        x_label = "Wall-hours (solid=hit, dashed=closure)"
    elif isinstance(speed, dict):
        bits = [f"{k}={v:.2f}×" for k, v in speed.items() if v]
        speed_s = "hours-to-90%: " + ", ".join(bits) if bits else "hours-to-90% n/a"
        title = "Coverage vs simulation-CPU-hours (oracles vs baseline)"
        x_label = "Simulation-CPU-hours (solid=hit, dashed=closure)"
    else:
        speed_s = f"{speed:.2f}× hours-to-90% of baseline-final" if speed else "hours-to-90% n/a"
        title = "Coverage vs simulation-CPU-hours (oracles vs baseline)"
        x_label = "Simulation-CPU-hours (solid=hit, dashed=closure)"

    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <rect width="100%" height="100%" fill="#fbfbfd"/>
  <text x="{W/2}" y="22" text-anchor="middle" font-size="16" font-family="ui-sans-serif, system-ui" fill="#111">
    {title}
  </text>
  <rect x="{mx}" y="{my}" width="{mw}" height="{mh}" fill="#fff" stroke="#ccc"/>
  {''.join(ticks)}
  {''.join(layers)}
  {''.join(legend)}
  <text x="{W/2}" y="{H-16}" text-anchor="middle" font-size="13" fill="#333">{x_label}</text>
  <text x="18" y="{H/2}" transform="rotate(-90,18,{H/2})" text-anchor="middle" font-size="13" fill="#333">Coverage</text>
  <text x="{mx+mw}" y="22" text-anchor="end" font-size="11" fill="#1d4ed8">{speed_s}</text>
</svg>
"""
    path.write_text(svg)


def _arm_order(payload: dict) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for row in payload["arms"]:
        if row["arm"] not in seen:
            seen.add(row["arm"])
            names.append(row["arm"])
    return names


def render_summary_bars(payload: dict, path: Path) -> None:
    """Grouped bars: final hit vs closure per arm (mean over seeds)."""
    hit = payload["mean_final_hit"]
    closure = payload["mean_final_closure"]
    names = _arm_order(payload)
    W, H = 920, 420
    mx, my, mw, mh = 70, 70, 800, 280
    ymax = 1.0
    n = len(names)
    group_w = mw / max(n, 1)
    bar_w = group_w * 0.32
    gap = group_w * 0.08

    ticks = []
    for i in range(6):
        yv = i / 5
        y = my + mh * (1 - yv / ymax)
        ticks.append(f'<line x1="{mx-6}" y1="{y}" x2="{mx}" y2="{y}" stroke="#444"/>')
        ticks.append(f'<text x="{mx-10}" y="{y+4}" text-anchor="end" font-size="12" fill="#333">{int(yv*100)}%</text>')

    bars = []
    labels = []
    for i, name in enumerate(names):
        cx = mx + group_w * i + group_w / 2
        h_hit = (hit[name] / ymax) * mh
        h_cl = (closure[name] / ymax) * mh
        x_hit = cx - bar_w - gap / 2
        x_cl = cx + gap / 2
        y_hit = my + mh - h_hit
        y_cl = my + mh - h_cl
        color = COLORS.get(name, "#111")
        bars.append(f'<rect x="{x_hit:.1f}" y="{y_hit:.1f}" width="{bar_w:.1f}" height="{h_hit:.1f}" fill="{color}" rx="2"/>')
        bars.append(
            f'<rect x="{x_cl:.1f}" y="{y_cl:.1f}" width="{bar_w:.1f}" height="{h_cl:.1f}" fill="{color}" fill-opacity="0.45" stroke="{color}" stroke-width="1.2" rx="2"/>'
        )
        labels.append(f'<text x="{cx:.1f}" y="{my+mh+28}" text-anchor="middle" font-size="11" fill="#111">{name}</text>')
        labels.append(
            f'<text x="{x_hit + bar_w/2:.1f}" y="{y_hit - 6}" text-anchor="middle" font-size="10" fill="#333">{hit[name]*100:.1f}%</text>'
        )
        labels.append(
            f'<text x="{x_cl + bar_w/2:.1f}" y="{y_cl - 6}" text-anchor="middle" font-size="10" fill="#333">{closure[name]*100:.1f}%</text>'
        )

    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <rect width="100%" height="100%" fill="#fbfbfd"/>
  <text x="{W/2}" y="28" text-anchor="middle" font-size="16" font-family="ui-sans-serif, system-ui" fill="#111">
    Final coverage (mean over {payload.get('n_seeds', 3)} seeds)
  </text>
  <rect x="{mx}" y="{my}" width="{mw}" height="{mh}" fill="#fff" stroke="#ccc"/>
  {''.join(ticks)}
  {''.join(bars)}
  {''.join(labels)}
  <rect x="88" y="44" width="12" height="12" fill="#1d4ed8"/>
  <text x="106" y="54" font-size="11" fill="#111">hit fraction</text>
  <rect x="200" y="44" width="12" height="12" fill="#1d4ed8" fill-opacity="0.45" stroke="#1d4ed8"/>
  <text x="218" y="54" font-size="11" fill="#111">closure fraction</text>
  <text x="{W/2}" y="{H-12}" text-anchor="middle" font-size="12" fill="#555">budget {payload.get('budget_hours', 4)} h · backend {payload.get('backend', 'boom')}</text>
</svg>
"""
    path.write_text(svg)


def render_speedup_bars(payload: dict, path: Path) -> None:
    """Hours-to-90% of baseline-final (lower is faster)."""
    speed = payload.get("speedup_hours_to_90pct_of_baseline_final") or {}
    items = [(k, v) for k, v in speed.items() if v]
    if not items:
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="80"><text x="10" y="40">n/a</text></svg>')
        return
    items.sort(key=lambda kv: kv[1])
    W, H = 720, 320
    mx, my, mw, mh = 160, 56, 520, 220
    row_h = mh / len(items)
    bars = []
    labels = []
    xmax = max(v for _, v in items) * 1.15
    for i, (name, val) in enumerate(items):
        y = my + i * row_h + row_h * 0.2
        h = row_h * 0.6
        w = (val / xmax) * mw
        color = COLORS.get(name, "#111")
        bars.append(f'<rect x="{mx}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" fill="{color}" rx="3"/>')
        labels.append(f'<text x="{mx-8}" y="{y+h/2+4}" text-anchor="end" font-size="12" fill="#111">{name}</text>')
        labels.append(f'<text x="{mx+w+8}" y="{y+h/2+4}" font-size="12" fill="#333">{val:.2f}×</text>')
    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <rect width="100%" height="100%" fill="#fbfbfd"/>
  <text x="{W/2}" y="28" text-anchor="middle" font-size="15" font-family="ui-sans-serif, system-ui" fill="#111">
    Hours to 90% of baseline-final hit (lower is faster)
  </text>
  {''.join(bars)}
  {''.join(labels)}
</svg>
"""
    path.write_text(svg)


def render_engine_mix(policy: dict, path: Path) -> None:
    """Stacked bars of engine actions per arm (policy brief)."""
    arms = policy.get("arms", {})
    names = list(arms.keys())
    actions = ["torture", "riscv_dv", "constraint_tuning", "directed", "formal"]
    W, H = 920, 400
    mx, my, mw, mh = 70, 70, 800, 260
    n = len(names)
    bar_w = min(100, mw / max(n, 1) * 0.65)
    gap = (mw - n * bar_w) / max(n + 1, 1)
    bars = []
    labels = []
    for i, name in enumerate(names):
        counts = arms[name].get("action_counts", {})
        total = sum(counts.get(a, 0) for a in actions) or 1
        x = mx + gap + i * (bar_w + gap)
        y0 = my + mh
        for action in actions:
            c = counts.get(action, 0)
            if not c:
                continue
            h = (c / total) * mh
            y0 -= h
            color = ACTION_COLORS.get(action, "#64748b")
            bars.append(f'<rect x="{x:.1f}" y="{y0:.1f}" width="{bar_w:.1f}" height="{h:.1f}" fill="{color}"/>')
        labels.append(f'<text x="{x+bar_w/2:.1f}" y="{my+mh+24}" text-anchor="middle" font-size="11" fill="#111">{name}</text>')
        labels.append(
            f'<text x="{x+bar_w/2:.1f}" y="{my+mh+40}" text-anchor="middle" font-size="10" fill="#555">{total} steps</text>'
        )
    legend = []
    lx = 88
    for action in actions:
        legend.append(f'<rect x="{lx}" y="40" width="10" height="10" fill="{ACTION_COLORS[action]}"/>')
        legend.append(f'<text x="{lx+14}" y="49" font-size="10" fill="#111">{action}</text>')
        lx += 110
    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <rect width="100%" height="100%" fill="#fbfbfd"/>
  <text x="{W/2}" y="24" text-anchor="middle" font-size="15" font-family="ui-sans-serif, system-ui" fill="#111">
    Engine mix per arm (action counts across seeds)
  </text>
  {''.join(legend)}
  <rect x="{mx}" y="{my}" width="{mw}" height="{mh}" fill="#fff" stroke="#ccc"/>
  {''.join(bars)}
  {''.join(labels)}
</svg>
"""
    path.write_text(svg)


def render_hours_to_40pct(payload: dict, path: Path) -> None:
    """Hours to 40% hit (leftover-rich primary hours claim)."""
    from .roi import hours_to_hit

    names = _arm_order(payload)
    items: list[tuple[str, float, dict]] = []
    for name in names:
        rec = hours_to_hit(payload, name, 0.40)
        if rec["hours_mean"] is None:
            continue
        items.append((name, rec["hours_mean"], rec))
    if not items:
        path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="80">'
            "<text x=\"10\" y=\"40\">hours to 40% hit n/a</text></svg>\n"
        )
        return
    W, H = 720, 80 + 70 * len(items)
    mx, my, mw, mh = 160, 72, 500, 48 * len(items) + 12 * (len(items) - 1)
    xmax = max(v for _, v, _ in items) * 1.15
    bars = []
    labels = []
    n_seeds = payload.get("n_seeds", 3)
    for i, (name, val, rec) in enumerate(items):
        y = 88.0 + i * 70
        h = 49.5
        w = (val / xmax) * mw
        color = COLORS.get(name, "#111")
        bars.append(
            f'<rect x="{mx}" y="{y:.1f}" width="{w:.1f}" height="{h}" fill="{color}" rx="3"/>'
        )
        labels.append(
            f'<text x="{mx-8}" y="{y+h/2+4:.1f}" text-anchor="end" font-size="14" fill="#111">{name}</text>'
        )
        labels.append(
            f'<text x="{mx+w+8:.1f}" y="{y+h/2+4:.1f}" font-size="14" fill="#333">{val:.1f} h</text>'
        )
        labels.append(
            f'<text x="{mx}" y="{y+h+16:.1f}" font-size="10" fill="#555">'
            f"{rec['reached_seeds']}/{rec['n_seeds']} seeds reached 40%</text>"
        )
    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">
  <rect width="100%" height="100%" fill="#fbfbfd"/>
  <text x="{W/2}" y="28" text-anchor="middle" font-size="16" fill="#111">Hours to 40% hit (leftover-rich, 5000 points)</text>
  <text x="{W/2}" y="50" text-anchor="middle" font-size="12" fill="#555">Lower is faster · wall-hour model · 24 h budget · {n_seeds} seeds</text>
  {''.join(bars)}
  {''.join(labels)}
</svg>
"""
    path.write_text(svg)


def _subset(payload: dict, keep: set[str]) -> dict:
    arms = [a for a in payload["arms"] if a["arm"] in keep]
    hit = {k: v for k, v in payload.get("mean_final_hit", {}).items() if k in keep}
    cl = {k: v for k, v in payload.get("mean_final_closure", {}).items() if k in keep}
    speed = {
        k: v
        for k, v in (payload.get("speedup_hours_to_90pct_of_baseline_final") or {}).items()
        if k in keep and k != "baseline"
    }
    out = dict(payload)
    out["arms"] = arms
    out["mean_final_hit"] = hit
    out["mean_final_closure"] = cl
    out["speedup_hours_to_90pct_of_baseline_final"] = speed
    return out


def render_all_charts(payload: dict, out_dir: Path, policy: dict | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    render_svg(payload, out_dir / "coverage_vs_hours.svg")
    render_summary_bars(payload, out_dir / "final_coverage_bars.svg")
    render_speedup_bars(payload, out_dir / "hours_to_90pct.svg")
    pol = policy or payload.get("learned_policy") or policy_brief(payload)
    render_engine_mix(pol, out_dir / "engine_mix.svg")
    if payload.get("backend") == "leftover":
        render_hours_to_40pct(payload, out_dir / "hours_to_40pct.svg")
        names = set(_arm_order(payload))
        if "enh_formal" in names:
            sub = _subset(payload, {"baseline", "enh_formal"})
            render_svg(sub, out_dir / "coverage_vs_hours_enh_formal.svg")
            render_summary_bars(sub, out_dir / "final_coverage_bars_enh_formal.svg")
        if "enh_directed" in names:
            sub = _subset(payload, {"baseline", "enh_directed"})
            render_svg(sub, out_dir / "coverage_vs_hours_enh_directed.svg")
            render_summary_bars(sub, out_dir / "final_coverage_bars_enh_directed.svg")


def policy_brief(payload: dict) -> dict:
    by_arm: dict[str, Counter[str]] = {}
    late_formal: dict[str, int] = {}
    late_directed: dict[str, int] = {}
    names = []
    seen = set()
    for row in payload["arms"]:
        if row["arm"] not in seen:
            seen.add(row["arm"])
            names.append(row["arm"])
        by_arm.setdefault(row["arm"], Counter())
        late_formal.setdefault(row["arm"], 0)
        late_directed.setdefault(row["arm"], 0)
        for step in row["policy"]:
            by_arm[row["arm"]][step["action"]] += 1
            if step["closure"] >= 0.80:
                if step["action"] == "formal":
                    late_formal[row["arm"]] += 1
                if step["action"] == "directed":
                    late_directed[row["arm"]] += 1
    return {
        "arms": {
            name: {
                "action_counts": dict(by_arm[name]),
                "late_stage_formal_steps": late_formal[name],
                "late_stage_directed_steps": late_directed[name],
            }
            for name in names
        },
        "note": "Named oracles vs industrial baseline. Solid/dashed in the SVG are hit vs closure.",
    }
