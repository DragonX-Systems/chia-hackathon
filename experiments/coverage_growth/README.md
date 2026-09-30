# Coverage-growth experiments

This directory owns experiment assembly: frozen inputs, run parameters, and
drivers. Reusable scheduling code lives in `src/chialoop`; DUT-specific code
lives in `adapters/mediumboom`.

`run_legacy_experiment.py` and `cluster_driver.py` preserve the original
MediumBOOM prototype. The new artifact-agnostic controller is launched from the
repository root with `run_chialoop.py`.

What the loop does (the proposal):

1. Look at uncovered points + coverage-per-hour so far.
2. Pick one action: torture, riscv-dv, constraint retune, directed, or formal.
3. `chia_remote` that batch onto the worker that already has the tool.
4. Merge coverage. Repeat.

Install is **not** in the loop. Verilator/riscv-dv live in `ghcr.io/ucb-bar/chia-verilator-run`. `chia up` pulls the image once.

## Resource asks (how `1.0` is chosen)

| Node | Decorator | Meaning |
|------|-----------|---------|
| `run_torture` | `{"torture": 1.0}` | one of 8 random slots |
| `run_riscv_dv` | `{"riscv_dv": 1.0, "verilator_run": 1.0}` | generate **and** score — needs both |
| `run_constraint_tuning` | `{"torture": 0.25}` | cheap; four retunes can share one slot |
| `run_write_artifact` | `{"artifact_writer": 1.0}` | LLM / self last-mile `.S` (collateral) |
| `run_directed` | `{"verilator_run": 1.0}` | score the written assembly on Verilator |
| `run_formal` | `{"formal": 1.0}` | one of 4 solver slots |
| meter | `{"coverage_meter": 1.0}` | isolated yardstick worker |

The **10** in `verilator_run: 10` is how many of those jobs you allow at once on that worker. The function asks for **1.0** of that pile.

## Files

| File | Purpose |
|------|----------------|
| `cluster.yaml` | Cluster resource advertisement, following `chia/examples/memcpy/cluster.yaml`. |
| `cluster_nodes.py` | Legacy MediumBOOM CHIA functions. |
| `cluster_driver.py` | Legacy cluster-shaped controller. |
| `run_legacy_experiment.py` | Legacy local B1/B2/B3 experiment entry point. |
| `configs/` | Frozen campaign configurations for the reusable controller. |
| `catalogs/` | Frozen job catalogs for paired runs. |
| `coverage/` | Coverage-point registries. |
| `scripts/` | Experiment-only preparation or launch helpers. |

## Run without a cluster (this Mac)

```bash
conda activate chia_env
cd /path/to/dragon-project/chia_hackathon
python experiments/coverage_growth/cluster_driver.py --budget-hours 20 --seeds 1
```

The loop calls `ray.init(resources={...})` with the **same names** as the YAML so scheduling works locally.

Default engine bodies are the yield models. To score with Verilator:

```bash
conda activate chia_env
export PATH="$HOME/.local/riscv-pulp/bin:$PATH"
python experiments/coverage_growth/cluster_driver.py \
  --backend verilator --budget-hours 8 --seeds 1 --out runs/legacy/verilator-new
```

On a cluster, `chia viz-profile` is the compute-hour clock (Ray task wall).

## Run on a CHIA cluster (Linux)

```bash
export THIS_MACHINE=$(hostname)
conda activate chia_env
chia up experiments/coverage_growth/cluster.yaml
chia job submit -- python experiments/coverage_growth/cluster_driver.py
chia viz-profile   # hours from Ray task timings
chia down experiments/coverage_growth/cluster.yaml
```

Fill extra `compatible_ips` when you have more than one machine (the snippet with `min_workers: 4` / `verilator_run: 10`).
