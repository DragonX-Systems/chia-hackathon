# Enhancement arm `enh_v1` (not catalog)

This is **not** catalog `oracles-20260907`. Do not treat `enh_v1` as a fifth judged oracle. The 38-point MediumBOOM eval in `artifacts_mediumboom/` was **not** re-run for this arm.

## 0. Ideas (why this arm exists)

Formal owns leftover SystemVerilog Assertions (SVA). Directed never snipes them.
Gates use the CRV-reachable leftover, not planted impossibles. Formal prune
**after** 40% hit. Show only enhancements that moved the leftover-rich curve
(formal prune + directed last-mile). Genome retune did not. Full list:
[FINAL_REPORT.md](FINAL_REPORT.md) § Ideas we came up with.

Architecture (CHIA nodes / `chia_remote` for Enh 2 Unreached prune and Enh 3
hard leftovers): [ARCHITECTURE.md](Verification%20ChiaLoop%20Architecture%20Overview.md).

## 1. Goal

Allocate the next compute-hour more efficiently than the industrial vanilla constrained-random verification (CRV) baseline (`riscv_dv` until stall, then untargeted directed — `adapters/mediumboom/baseline.py`). Fitness is coverage gained per compute-hour, not chat quality.

## 2. What shipped

| Field | Value |
|--------|--------|
| Arm | `enh_v1` |
| Plan id | `enh-20260909` |
| Catalog left alone | `oracles-20260907` |
| Code | `adapters/mediumboom/enhancement.py` (`enhancement_plan`) |
| Loop flag | `run_experiment(..., enhancement=True)` |
| Manifest | written as `enhancement.json` when the flag is on |

Vanilla baseline and the four named oracles are unchanged.

## 3. Pipeline

Leftover split (`adapters/mediumboom/prune.py`) uses planner-visible **kind** only. SystemVerilog Assertion (SVA) points go to formal; everything else is the CRV-reachable leftover set. Planted `1'b0` / `unreachable` buckets are **not** shown to the planner. The ~70% / ~80% gates are **of CRV-reachable leftover** (`crv_closed_frac`), not of all points including planted impossibles.

### Enh 1 — genome retune (not shown)

**Did not help on the leftover-rich eval.** Traces have **zero** `constraint_tuning` batches. OpenEvolve / AlphaEvolve was not used in the judged loop. Do not put this on the poster. Code remains in `genome.py` if we wire it later.

### Enh 2 — formal prune of leftover SVA (shown)

On leftover-rich 24 h, **3 formal batches per seed** after 40% hit. Mean
closure **45.0%** vs hit **44.8%** (6–9 Unreached retired per seed). Baseline
retired 0.

After CRV has already reached **40% hit** (`snap.hit_frac >= 0.40` and `dv.batches >= 8`), run formal on leftover SVA (`formal_targets`). Formal **Unreached** retires planted `bucket==unreachable` SVA, so **closure can exceed hit**. Cap **three** useful formal batches; stop if the last formal batch is dry (0 hits and 0 retired). Do not insert formal before the 40% hit bar.

### Enh 3 — directed last-mile on non-SVA only (shown)

**12 directed** jobs per seed after 40% hit; no SVA targets. Hours to 40% hit
**8.0 h vs 23.8 h**. Never last-mile if hit_frac < 0.40.

Never last-mile if hit_frac < 0.40. With leftover CRV remaining: existing past_crv_80 / CRV-dead / stall, **or** `riscv_dv` batches ≥ 12 and last_cph < 50% of `dv_peak_cph`, **or** batches ≥ 16. Directed uses `directed_targets()` only (no SVA). Same 1.2 h request. After the first directed, Enh 2 may still run one formal prune of leftover SVA.

Order in `enhancement_plan`: Enh 2 gate → Enh 3 gate → Enh 1 retune → else one vanilla CRV carpet (`torture` then `riscv_dv`).

## 4. How to run

CLI default is **on**. Python API default is **off**.

```bash
python experiments/coverage_growth/run_legacy_experiment.py --backend fake --enhancement
python experiments/coverage_growth/run_legacy_experiment.py --backend fake --no-enhancement
```

```python
from adapters.mediumboom.loop import run_experiment
run_experiment(out_dir, enhancement=True)   # Python default is False
```

`--enhancement` does not retcon catalog `oracles-20260907`. Charts color `enh_v1` magenta (`#db2777` in `adapters/mediumboom/report.py`).

## 5. Honesty

- Leftover-rich wall-hour **model**, **5000 points** (`--backend leftover`, seed `20260910`).
- Primary hours claim: **hours to 40% hit** = 8.0 h (`enh_v1`) vs 23.8 h (baseline), ratio **0.336** (66% fewer hours). Directed counts on leftover-rich: **12 per seed**. See `FINAL_REPORT.md`.
- Do **not** quote hours-to-90%-of-baseline-final on this yardstick (it is 1.0×; that bar is still in the shared CRV prefix).
- Not MediumBOOM 38-point SoC wall time. AlphaEvolve / OpenEvolve is **not** a claimed enhancement (0 retune batches).

## 6. Files

| File | Role |
|------|------|
| `adapters/mediumboom/prune.py` | Leftover split; `past_crv_70` / `past_crv_80` on CRV-reachable fraction |
| `adapters/mediumboom/enhancement.py` | `enh_v1` / `enh-20260909` plan + manifest |
| `adapters/mediumboom/loop.py` | `enhancement=` flag; extra arm; writes `enhancement.json` |
| `adapters/mediumboom/report.py` | `enh_v1` color `#db2777` |
| `experiments/coverage_growth/run_legacy_experiment.py` | `--enhancement` / `--no-enhancement` (CLI default True) |
