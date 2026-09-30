# One five-category CRV set

The canonical inputs are the five supplied files in `crv_profiles/`.
`crv_profiles.json` maps each file to a ChiaLoop node and adds only the
generator controls missing from the original configs. The preparation script
records both each source file's hash and its effective config. There is one
riscv-torture generator with a seeded Scala overlay; the former small Python
ALU/memory generator has been replaced.

| Node | Torture sequences | xALU / branch / memory mix | Specialization |
| --- | ---: | --- | --- |
| crv-1 | 200 | 65 / 30 / 5 | Integer/control; multiply, divide and AMO disabled |
| crv-2 | 200 | 25 / 10 / 65 | Memory; memory selections include 10% AMO and 20% fences |
| crv-3 | 200 | 65 / 10 / 25 | xALU restricted to multiply/divide; memory selections 85% AMO, 5% fences |
| crv-4 | 200 | 50 / 30 / 20 | 64 checked CSR/trap cases before the random body |
| crv-5 | 400 | 65 / 20 / 15 | Longer mixed body; 25% AMO / 10% fences within memory selections, 16 trap cases |

These are selection probabilities for instruction sequences, not exact retired
instruction percentages or guarantees of particular coverage bins. Privilege
checks guarantee illegal-instruction, breakpoint, M-mode ecall and U-mode ecall
cases. They check `mcause`, `mepc`, previous privilege, trap count and CSR values,
then restore the normal user-mode torture harness. They do not implement paging,
supervisor-mode OS workloads or multicore memory-order litmus tests.

The Scala overlay lives in `experiments/coverage_growth/riscv_torture_profile_src/`.
Preparation snapshots a supported torture Git revision into its own output
folder and applies the overlay there. It never modifies the Chipyard generator
checkout. The stock generator alone does not interpret the specialization
settings; use the preparation command below.

## Prepare 20 CRV + 2 directed on EC2

From `~/chia-hackathon`, with Java, RISC-V GCC, Spike and the existing simulator
built with `VM_COVERAGE=1`:

```sh
export CHIPYARD_ROOT="$HOME/chia-hackathon/third_party/chipyard"
export RISCV=/tmp/cy-riscv
export LD_LIBRARY_PATH="$RISCV/lib:$CHIPYARD_ROOT/tools/DRAMSim2:${LD_LIBRARY_PATH:-}"
RUN=$(mktemp -d /tmp/mediumboom-crv5.XXXXXX)
python3 experiments/coverage_growth/prepare_mediumboom_crv_categories.py \
  --study artifacts_mediumboom/kapil_qualification/open_toggle_multi_seed_study \
  --out "$RUN" --per-node 4 --spike "$RISCV/bin/spike" \
  --directed-catalog /tmp/mediumboom-chialoop-20crv-2directed/catalog.jsonl
```

The directed catalog refers to the earlier EC2 run; another catalog with two
valid directed ELF inputs can be supplied. Omit it for CRV only. `--per-node 1`
produces five CRV programs. `--loop-count 1` limits each torture body to one
execution using the same profiles and instruction generator. Without the
loop override, the supplied configs retain their original loop sizes (the
upstream body executes `loop_size + 1` times).

Preparation compiles every ELF and runs it in Spike, saving an expected memory
signature. It refuses to publish the catalog if compilation, reference execution
or signature validation fails. Every CRV program gets a distinct nonnegative
63-bit seed. A new preparation chooses a fresh random seed base; `--seed-base`
is only for intentional reproduction. The builder rejects duplicate loaded ELF
content, even when filenames, symbols or provenance comments differ.

The frozen output includes effective configs, generated assembly/statistics,
ELFs, reference signatures, generator/Spike logs, coverage registry,
`generator_manifest.json`, `campaign.json` and `catalog.jsonl`.

## Run ChiaLoop

```sh
python3 run_chialoop.py run \
  --campaign "$RUN/campaign.json" \
  --coverage "$RUN/coverage_points.json" \
  --catalog "$RUN/catalog.jsonl" \
  --executor experiments.coverage_growth.mediumboom_open_toggle_executor:execute \
  --arm adaptive --runtime local \
  --trial-id "$(basename "$RUN")" --out "$RUN/output"
```

Use `--arm static` for the fixed scheduling policy. The catalog has five CRV
nodes, optionally one directed node, and no formal node. All nodes target the
same coverage universe; the existing scheduler learns from actual new-bin yield.
This is a finite corpus: create a new bundle for fresh programs in another run.

Each execution uses a new directory under the bundle's `runs/`. The executor
checks the ELF, simulator and monitor-registry hashes, requires successful RTL
completion and a fresh `coverage.dat`, and compares the DUT signature with the
Spike reference before crediting coverage. It uses the coverage-enabled
Verilator main's default output file, without the unsupported
`+verilator+coverage+file+...` option. No hardware recompilation is needed to
change these stimulus profiles.

## Validation of the unified implementation

On EC2, all 20 CRV programs and two imported directed programs compiled and
passed Spike reference execution. The prepared bundle is
`/tmp/mediumboom-unified-20crv-2directed` on that instance.

A separate validation bundle generated one program from each of the **same
five profiles**, with `--loop-count 1` and seeds 73000–73004. All five completed
successfully through ChiaLoop's adaptive arm, matched the Spike signatures and
produced fresh coverage. Its results and logs are under
`/tmp/mediumboom-unified-validation` on EC2. This confirms the five-category RTL
flow; it is not a completed RTL run of the full 22-job bundle.

The actual Scala generator reproduced identical assembly for a repeated seed
and different assembly for a new seed. Sixteen local regression checks passed
for configuration mapping, executable identity, privilege checks, catalog
wiring, signature checking, changed-input rejection and fresh output directories.
