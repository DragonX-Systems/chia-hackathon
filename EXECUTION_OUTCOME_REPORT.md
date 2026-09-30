# Execution outcome report

## Run used for the result

The reported result is the corrected MediumBOOM run in
[`artifacts_mediumboom/real_enhancements_fixed/`](artifacts_mediumboom/real_enhancements_fixed/).
It used:

```bash
python3 run_experiment.py --backend boom --oracles none --enhancement \
  --budget-hours 4 --seeds 1 --out artifacts_mediumboom/real_enhancements_fixed
```

The run used one seed and a four-hour budget. The yardstick was the 51-point
freeze from seed `20260914`: 43 SoC CRV bins plus 8 formal SVA points.

## Results

| Arm | Hits | Retired | Closure |
|---|---:|---:|---:|
| Baseline | 41/51 (80.4%) | 0 | 41/51 (80.4%) |
| `enh_formal` | 47/51 (92.2%) | 2 | 49/51 (96.1%) |
| `enh_directed` | 41/51 (80.4%) | 0 | 41/51 (80.4%) |

The exact source is
[`results.json`](artifacts_mediumboom/real_enhancements_fixed/results.json).
Charts are [`coverage_vs_hours.svg`](artifacts_mediumboom/real_enhancements_fixed/coverage_vs_hours.svg)
and [`final_coverage_bars.svg`](artifacts_mediumboom/real_enhancements_fixed/final_coverage_bars.svg).

## What happened

- Baseline used SoC CRV and then directed work. It reached 41 points.
- `enh_formal` ran CRV and then one formal batch. Formal reached six SVA
  covers and retired two unreachable SVA covers. It did not change the 43 SoC
  CRV bins.
- `enh_directed` generated and ran directed collateral, but added no point on
  this seed. It matched baseline.
- The measured policy is in
  [`policy.json`](artifacts_mediumboom/real_enhancements_fixed/policy.json).
- The generated program inventory is in
  [`riscv_torture/manifest.json`](artifacts_mediumboom/riscv_torture/manifest.json):
  12 programs compiled successfully and none failed compilation.

## Interpretation

Formal was the useful enhancement for this run because the open SVA points
could not be closed by SoC CRV. It improved hit coverage from 80.4% to 92.2%
and closure from 80.4% to 96.1%.

Directed generation was implemented and exercised, but it was neutral here.
The result is not evidence that directed stimulus always loses; it says that
this one-seed, four-hour MediumBOOM run found no additional frozen point.

## Caveats

The SoC simulator was built with `VM_COVERAGE=0`; the result is not a UCIS
database or Verilator line-coverage dump. Formal `DONE (FAIL, rc=2)` in the
corrected SBY cover run means the two intentionally unreachable covers remained
unreached; it is a valid cover result, not an elaboration error. The prior
formal wrapper error and its fix are documented in
[`formal_wrapper_fix/NOTES.md`](artifacts_mediumboom/formal_wrapper_fix/NOTES.md).

Do not use the top-level
[`artifacts_mediumboom/results.json`](artifacts_mediumboom/results.json) for
this outcome; it belongs to an older freeze and is marked in
[`DO_NOT_CITE_OLD_RESULTS.md`](artifacts_mediumboom/DO_NOT_CITE_OLD_RESULTS.md).
