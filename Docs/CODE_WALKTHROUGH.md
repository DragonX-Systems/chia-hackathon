# Code walkthrough

How a **MediumBOOM** coverage-growth run moves through this repo.

**Start here for submission:** [SUBMIT.md](../SUBMIT.md).

## High-level script (poster)

```bash
python3 experiments/coverage_growth/run_legacy_experiment.py --backend boom --oracles none --enhancement \
  --budget-hours 4 --seeds 1 --out artifacts_mediumboom/real_enhancements_fixed
```

| Piece | Path |
|-------|------|
| CLI | `experiments/coverage_growth/run_legacy_experiment.py` (`--backend boom`) |
| Loop | `adapters/mediumboom/loop.py` (`run_arm` / `run_experiment`) |
| MediumBOOM engines | `adapters/mediumboom/boom_backend.py` |
| Enh 2 / Enh 3 plan | `adapters/mediumboom/enhancement.py` |
| Meter | `adapters/mediumboom/meter.py` |
| CHIA dispatch | `adapters/mediumboom/chia_dispatch.py` |
| Cluster-shaped twin | `experiments/coverage_growth/cluster_driver.py` |

Default `python3 experiments/coverage_growth/run_legacy_experiment.py` without `--backend boom` is the **fake**
yield model (smoke / charts only). It is **not** the judged DUT.

## What one MediumBOOM round does

```
CoverageMeterNode.snapshot          → uncovered ids (kind, unit only)
        │
        ▼
enhancement_plan / baseline         → Allocation(action, hours, targets)
        │
        ▼
boom_backend.simulate_boom_batch
  torture / riscv_dv / directed     → SoC Verilator ELF → PASS credits
  formal                            → SymbiYosys SVA covers (LSU; ChipTop fallback)
        │
        ▼
CoverageMeterNode.merge_batch(hits, retired, hours)
        │
        ▼
EngineYieldNode.record              → coverage-per-hour for next round
```

Without CHIA installed, `src/chialoop/compat.py` still provides `@ChiaFunction` /
`chia_remote` in-process.

## Three arms (what the poster compares)

| Arm | Engines used | MediumBOOM result (seed 1) |
|-----|--------------|----------------------------|
| `baseline` | SoC CRV only | 41/51 hit = closure |
| `enh_formal` | CRV then formal on leftover SVA | **47/51** hit / **49/51** closure |
| `enh_directed` | CRV then directed (non-SVA) | 41/51 (null vs baseline) |

CRV credits **43 homemade SoC bins** (opcodes / class / run on PASS). Formal
moves the **8** named SVA points. Directed cannot retire; on this seed it
added no new hits.

## Package map (`adapters/mediumboom/`)

| Module | Read for |
|--------|----------|
| `boom_backend.py` | SoC CRV scoring + formal oracle (LSU / ChipTop scope) |
| `soc_formal.py` | ChipTop/BoomTile elaboration + LSU fallback |
| `enhancement.py` | Enh 2 formal prune / Enh 3 directed last-mile |
| `prune.py` | Kind-only leftover split (SVA vs line/toggle) |
| `coverage_model.py` | Frozen list + HMAC seal |
| `meter.py` | Only legal write path for hits / retire |
| `nodes/artifact_writer.py` | CHIA node — last-mile `.S` for Enh 3 |
| `nodes/symbiyosys.py` | SymbiYosys placement wrapper |
| `baseline.py` | Industrial CRV → untargeted directed |
| `report.py` | SVG charts from `results.json` |
| `leftover_backend.py` | **Lab only** 5000-point model — not poster |
| `engines.py` | Fake yield models (`--backend fake`) |

## Actions

1. **torture** / **riscv_dv** — SoC constrained-random (UCB riscv-torture when present)
2. **directed** — mem-heavy / artifact-written SoC programs (Enh 3; never SVA targets)
3. **formal** — cover BMC; **only** path that may **retire** unreachable SVA
4. **constraint_tuning** — knob reweight (not the MediumBOOM poster win)

## Hit versus retired

- **Hit** — observed on the frozen 51-point list.
- **Retired** — formal Unreached on a planted unreachable SVA.
- **Closure** — (hit + retired) / 51.

## Other `--backend` flags (not poster)

| Flag | Use |
|------|-----|
| `fake` | Toy yields (default CLI) |
| `leftover` | 5000-point **lab** scaling study |
| `verilator` / `sby` / `real` | TCM / local SBY / Spike — see [REPORT.md](../REPORT.md) |

## Artifacts

Under `--out` (poster: `artifacts_mediumboom/real_enhancements_fixed/`):

- `frozen_coverage_model.json` — HMAC yardstick
- `results.json` — traces, mean hit / closure
- `coverage_vs_hours.svg`, `final_coverage_bars.svg`
- `policy.json` — action mix

Do **not** cite top-level `artifacts_mediumboom/results.json` (~19%): old freeze.
See `artifacts_mediumboom/DO_NOT_CITE_OLD_RESULTS.md`.

## Tests

```bash
python3 -m pytest tests -q
```

## Related

- [SUBMIT.md](../SUBMIT.md) · [FINAL_REPORT.md](FINAL_REPORT.md) · [EVAL.md](../artifacts_mediumboom/EVAL.md)
- [ARCHITECTURE.md](Verification%20ChiaLoop%20Architecture%20Overview.md) · [CLASS_DIAGRAM.md](CLASS_DIAGRAM.md)
