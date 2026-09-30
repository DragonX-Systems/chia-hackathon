# Installation and user guide

**Poster command first** — see also [SUBMIT.md](../SUBMIT.md).

## 0. Judged MediumBOOM run (what you submit)

Needs: Python 3.10+, `sby` (SymbiYosys + Z3), Chipyard MediumBOOM Verilator
binary + `gen-collateral`, RISC-V toolchain (`RISCV`).

```bash
cd chia_hackathon
# export RISCV=...   # Chipyard toolchain
# export DYLD_LIBRARY_PATH=...  # if macOS DRAMSim / toolchain libs

python3 experiments/coverage_growth/run_legacy_experiment.py --backend boom --oracles none --enhancement \
  --budget-hours 4 --seeds 1 --out artifacts_mediumboom/real_enhancements_fixed
```

Expect: baseline **41/51**, Enh 2 **47/51** hit / **49/51** closure, Enh 3 **41/51**.
Details: [FINAL_REPORT.md](FINAL_REPORT.md), [EVAL.md](../artifacts_mediumboom/EVAL.md).

If the SoC simulator is missing, the boom backend charges hours with 0 hits
and notes the miss — fix the binary path before citing numbers.

## 1. Clone and Python

```bash
git clone https://github.com/kbansal1512/chia-hackathon.git
cd chia-hackathon
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-coverage.txt   # if present
python3 -m pytest tests -q
```

## 2. Install CHIA (optional)

Local poster runs use `chia_compat` in-process. Full cluster needs
[ucb-bar/chia](https://github.com/ucb-bar/chia). On macOS, Ray 2.54 often has
no wheel — pin Ray 2.49.2 (see [INSTALL.md](INSTALL.md)).

```bash
conda create -n chia_env python=3.10.19 && conda activate chia_env
pip install "cryptography==43.0.3" "ray[default]==2.49.2" ...
pip install -e /path/to/chia --no-deps
```

## 3. Smoke without MediumBOOM (not poster)

```bash
python3 experiments/coverage_growth/run_legacy_experiment.py --budget-hours 40 --out artifacts   # fake backend
```

## 4. Lab-only leftover-rich (not poster)

5000-point wall-hour model for allocator scaling. **Do not** put on the poster.

```bash
python3 experiments/coverage_growth/run_legacy_experiment.py --backend leftover --oracles none --enhancement \
  --budget-hours 24 --seeds 1,2,3,4,5,6,7,8,9,10 --out artifacts_leftover_rich
```

Numbers live in [REPORT.md](../REPORT.md), not [FINAL_REPORT.md](FINAL_REPORT.md).

## 5. Other lab backends

| Backend | Command sketch | Not |
|---------|----------------|-----|
| Spike ISA | `--backend real` | Not MediumBOOM |
| Local SBY slices | `--backend sby` | Not ChipTop |
| Verilator TCM | `--backend verilator` | Not generated MediumBOOM |

## 6. CHIA-shaped example loop

```bash
conda activate chia_env
python experiments/coverage_growth/cluster_driver.py --budget-hours 20 --seeds 1
```

Cluster (Linux + Docker workers):

```bash
chia up experiments/coverage_growth/cluster.yaml
chia job submit -- python experiments/coverage_growth/cluster_driver.py
chia viz-profile
```

Slots: `torture`, `riscv_dv`+`verilator_run`, `formal`, `coverage_meter`,
`artifact_writer`. See `experiments/coverage_growth/README.md`.

## 7. Flags

```text
--backend boom          # poster
--backend fake|leftover|verilator|sby|real
--budget-hours N
--seeds 1
--oracles none          # poster ablation
--enhancement           # run enh_formal + enh_directed
--out DIR
```

## 8. What still needs Linux / cluster

- `chia up` + Chipyard Docker images
- Multi-seed MediumBOOM table (`seeds 1,2,3`) when wall time allows
- FireSim-class workers

Laptop alone: planner + fake/leftover lab models + local SymbiYosys + boom if
the SoC binary is built.
