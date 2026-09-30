# Compute-Hour Efficient Coverage Growth

This CHIA hackathon project implements a closed-loop verification campaign that
allocates CRV, directed simulation, and formal work according to measured
**sound coverage growth per compute-hour**. Jobs execute asynchronously across
the configured compute resources while simulation and formal-license limits are
enforced separately.

## 1. Primary comparison

| Arm | Name | Policy | Purpose |
| --- | --- | --- | --- |
| B1 | CRV-only | Runs only constrained-random simulation. | Diagnostic floor: shows whether directed and formal engines are necessary. |
| B2 | Static-Hybrid | Uses CRV, directed, and formal according to a frozen schedule. | Fair baseline for measuring orchestration ROI. |
| B3 | Adaptive Chialoop | Uses the same engines but replans after each completion from measured coverage yield and available resources. | Proposed CHIA campaign. |

The primary ROI claim is **B3 versus B2**. Beating B1 alone does not demonstrate
Chialoop scheduling value. B2 and B3 must use the same campaign configuration,
coverage registry, job catalog, executor, resource limits, stopping rules, and
paired trial ID. B1 remains useful for showing engine complementarity.

## 2. Repository hierarchy

```text
chia-hackathon/
├── src/chialoop/
│   ├── core/                 # frozen inputs, coverage state, resources, yield
│   ├── orchestration/        # CHIA functions, policies, runtime, closed loop
│   └── reporting/            # event log and paired ROI comparison
├── adapters/mediumboom/
│   ├── rtl/                  # formal wrappers and properties
│   ├── stimulus/             # constrained-random configurations
│   ├── testbenches/          # Verilator harnesses
│   └── scripts/              # MediumBOOM setup and stimulus generation
├── experiments/coverage_growth/
│   ├── configs/              # frozen campaign JSON files
│   ├── coverage/             # frozen coverage-point registries
│   ├── catalogs/             # frozen JSONL job catalogs
│   ├── smoke/                # runnable orchestration-only sample inputs
│   └── scripts/              # experiment-specific helpers
├── verification/             # qualification and verification methodology
├── tests/                    # core, adapter, integration, verification tests
├── runs/                     # new generated results; ignored by default
├── Docs/                     # plans, architecture, reports, and papers
├── scripts/                  # repository-wide utilities
└── run_chialoop.py           # primary artifact-agnostic CLI
```

The intended dependency direction is:

```text
experiments -> adapters -> src/chialoop
```

The reusable controller does not import MediumBOOM-specific code.
`artifacts_*` directories contain historical experiment evidence; new runs
should be written under `runs/`.

## 3. Primary command line

Run commands from the repository root.

```bash
python3 run_chialoop.py {run,compare} --help
```

### 3.1 Run one campaign arm

```bash
python3 run_chialoop.py run \
  --campaign <campaign.json> \
  --coverage <coverage.json> \
  --catalog <catalog.jsonl> \
  --executor <python.module:callable> \
  --arm adaptive|static \
  --runtime local|chia \
  --trial-id <paired-trial-id> \
  --out <output-directory>
```

| Option | Meaning |
| --- | --- |
| `--campaign` | Frozen campaign identity, target, compute budget, compute-slot count, license counts, and policy parameters. |
| `--coverage` | Frozen coverage-point registry. The current experiment contract requires five partitions. |
| `--catalog` | Frozen JSONL list of prepared CRV, directed, and formal jobs. |
| `--executor` | Importable `package.module:callable` that executes one `JobSpec` and returns `EngineEvidence`. |
| `--arm` | `adaptive` selects by incremental bins per compute-hour from each node’s last K completed runs; `static` follows the frozen hybrid manifest. |
| `--runtime` | `chia` dispatches real CHIA functions; `local` uses isolated local worker processes with the same asynchronous controller semantics. Default: `chia`. |
| `--trial-id` | Pairing key. Static and adaptive runs being compared must use the same value. |
| `--out` | New output directory for the frozen run record, events, policy, and summary. |

### Node and resource configuration

A **node** is a repeatable verification strategy; a **job** is one unique prepared
run of that node. Nodes are independent of coverage partitions. By default there
are N1=5 CRV nodes, N2=1 directed node, N3=1 formal node, R=5 compute slots, and
K=10 completed runs of history per node. Multiple distinct jobs from the same
node may run concurrently. There is no per-partition concurrency limit.

| Node ID | Default strategy |
| --- | --- |
| `crv-1` | Integer and control flow |
| `crv-2` | Load/store and memory ordering |
| `crv-3` | Multiply, divide, and atomics |
| `crv-4` | Privilege and exception behavior |
| `crv-5` | Mixed long-running system behavior |
| `directed-1` | Directed verification |
| `formal-1` | Formal verification |

Set `n1`, `n2`, `n3`, `recent_window`, and `resources` in the campaign JSON,
or override them before execution with `--n1`, `--n2`, `--n3`, `--k`, and
`--r` (alias `--resources`). For example, append
`--n1 5 --n2 1 --n3 1 --r 5 --k 10` to the run command.
License counts are separately configurable with `--sim-licenses` and
`--formal-licenses`; restrictive license counts can leave compute slots idle.
Unspecified simulation and formal license counts each default to K (10 by
default), including when K is overridden on the command line. Explicit license
counts in the campaign or command line take precedence. Compute slots default to 5.
Python `ResourceCapacity` uses `None` for unspecified licenses; `CampaignSpec`
resolves them against K and saves concrete capacities in the run record.

Each catalog job declares a `node_id`, such as `"crv-2"`, and a unique
`seed_or_query_id`: a fresh CRV seed, directed-test identity, or formal-query
identity. Identities must be unique within an engine across all nodes. Known
simulation input hashes and ELF paths may not be reused under new identities.
A missing `node_id` is accepted only when that engine has exactly one configured
node. Input preparation must actually generate the assigned category and use
the declared seed/test/query; the controller does not turn an existing binary
into a fresh test by relabeling it. The no-tool smoke catalog uses synthetic
assignments and does not generate category-specific RTL stimulus.

Executors can attach `execution_fingerprint(job)` to their callable. It must
identify the actual inputs and execution options, excluding job labels and
reporting filters. Admission rejects repeated fingerprints before dispatch.
Without this hook, the controller checks declared identities and conservative
payload metadata; it cannot infer the semantics of an opaque executor.
The native MediumBOOM adapter supplies the hook and uses the same formal query
parser for fingerprinting and execution. Its formal payload requires `sby_file`,
accepts an optional `sby_task` and `sby_sha256`, and treats `required_goal` as an
output check rather than a different solver run. Unsupported options fail early.

After a completion, the controller merges coverage and refills every slot that
has compatible pending work. Expired jobs are cancelled before refill even when
other jobs keep completing. Adaptive selection initially probes unobserved
nodes, then maximizes **globally new coverage bins divided by total compute-hours
over that node's last K completed runs**. This includes newly hit bins and sound formal
unreachability proofs; duplicate coverage and failed runs contribute zero.
History follows completion order, pools all partitions belonging to the same
node, and uses available samples when fewer than K runs have completed. Time normalization uses the ratio of sums over the same window. No periodic
exploration overrides the score. Ties use deterministic catalog ordering. Pending initial probes
never force compatible resources to sit idle.

The controller consumes a finite catalog of prepared jobs, never reruns a
completed job, and stops at target, budget, or catalog exhaustion. Populate each
node with enough distinct jobs for the intended campaign. N1/N2/N3 declare
available strategies; they do not synthesize inputs. Counts below five expose
the first N1 default CRV categories; additional CRV nodes require user-prepared
strategies and inputs. Nodes without catalog jobs cannot be dispatched.

Old catalogs with multiple configured nodes require explicit node assignments;
use N1=1 for a legacy single CRV strategy rather than guessing categories from
coverage partitions. Legacy `exploration_period` and `max_in_flight_per_lane`
JSON fields no longer affect scheduling. New campaign fingerprints prevent
resuming old scheduler journals under the new policy. Historical frozen result
artifacts are unchanged.

### 3.2 Full-flow smoke test

The smoke executor launches no RTL, simulator, or formal tool. It verifies input
loading, scheduling, asynchronous completion, resource accounting, output
generation, and paired comparison only; it does **not** demonstrate ROI. Run
the complete local flow with this command sequence:

```bash
python3 run_chialoop.py run \
  --campaign experiments/coverage_growth/smoke/campaign.json \
  --coverage experiments/coverage_growth/smoke/coverage.json \
  --catalog experiments/coverage_growth/smoke/catalog.jsonl \
  --executor adapters.mediumboom.smoke_executor:execute \
  --arm static --runtime local --trial-id smoke-1 \
  --out runs/smoke/static && \
python3 run_chialoop.py run \
  --campaign experiments/coverage_growth/smoke/campaign.json \
  --coverage experiments/coverage_growth/smoke/coverage.json \
  --catalog experiments/coverage_growth/smoke/catalog.jsonl \
  --executor adapters.mediumboom.smoke_executor:execute \
  --arm adaptive --runtime local --trial-id smoke-1 \
  --out runs/smoke/adaptive && \
python3 run_chialoop.py compare \
  --static-summary runs/smoke/static/summary.json \
  --adaptive-summary runs/smoke/adaptive/summary.json \
  --bootstrap-samples 5000 \
  --out runs/smoke/comparison
```

Successful completion writes both run records and the paired comparison under
`runs/smoke/`. This smoke flow exercises B2 and B3 controller paths. B1 is
available through the legacy three-arm runner described in Section 4.

### 3.3 Compare paired runs

```bash
python3 run_chialoop.py compare \
  --static-summary <static-run>/summary.json \
  --adaptive-summary <adaptive-run>/summary.json \
  --bootstrap-samples 5000 \
  --out <comparison-output-directory>
```

| Option | Meaning |
| --- | --- |
| `--static-summary` | Static-Hybrid `summary.json`. Repeat the option for multiple paired trials. |
| `--adaptive-summary` | Adaptive Chialoop `summary.json`. Repeat the option for multiple paired trials. |
| `--bootstrap-samples` | Resamples used for the paired median confidence interval. Default: `5000`. |
| `--out` | Directory for comparison JSON, Markdown, and plots. |

## 4. Legacy MediumBOOM command line

The older B1/B2/B3 and backend experiments remain available for reproducing
historical evidence:

```bash
python3 experiments/coverage_growth/run_legacy_experiment.py [options]
```

Run all three B1/B2/B3 arms with:

```bash
python3 experiments/coverage_growth/run_legacy_experiment.py \
  --backend fake --arms b1,b2,b3 --seeds 1,2,3 \
  --out runs/legacy/b123
```

| Option | Meaning |
| --- | --- |
| `--budget-hours` | Per-arm compute-hour budget. Default: `40`. |
| `--out` | Output directory. Default: `runs/legacy/batch`. |
| `--backend` | `fake`, `real`, `sby`, `verilator`, `boom`, or `leftover`. |
| `--seeds` | Comma-separated trial seeds. Default: `1,2,3`. |
| `--arms` | Comma-separated subset of `b1,b2,b3`; disables legacy oracle/enhancement selection. |
| `--oracles` | `all`, `none`, or selected legacy oracle names when `--arms` is not supplied. |
| `--enhancement` / `--no-enhancement` | Enable or disable legacy enhancement arms. |
| `--parallel` | Enable concurrent simulation and formal admission. |
| `--n-sim` | Simulation-seat count for the legacy parallel runner. Default: `4`. |
| `--n-formal` | Formal-seat count for the legacy parallel runner. Default: `1`. |

The legacy cluster-shaped driver and resource advertisement are documented in
[`experiments/coverage_growth/README.md`](experiments/coverage_growth/README.md).

## 5. Inputs

| Input | Location or specification |
| --- | --- |
| Campaign configuration | [`experiments/coverage_growth/configs/`](experiments/coverage_growth/configs/README.md) |
| Coverage registry | [`experiments/coverage_growth/coverage/`](experiments/coverage_growth/coverage/README.md) |
| Job catalog | [`experiments/coverage_growth/catalogs/`](experiments/coverage_growth/catalogs/README.md) |
| Runnable sample | [`experiments/coverage_growth/smoke/`](experiments/coverage_growth/smoke) |
| Executor contract and schemas | [`Docs/Verification ChiaLoop Implementation Overview.md`](Docs/Verification%20ChiaLoop%20Implementation%20Overview.md) |
| ROI experiment contract | [`Docs/CHIALOOP_ROI_PLAN.md`](Docs/CHIALOOP_ROI_PLAN.md) |
| MediumBOOM DUT and corpus | [`Docs/MediumBOOM Overview.md`](Docs/MediumBOOM%20Overview.md) |

## 6. Outputs

Every `run` output directory contains:

| File | Contents |
| --- | --- |
| `campaign_spec.json` | Exact campaign configuration used by the run. |
| `job_catalog.jsonl` | Exact frozen job catalog used by the run. |
| `policy.json` | Selected policy and policy parameters. |
| `static_manifest.json` | Frozen Static-Hybrid order; written only for the static arm. |
| `events.jsonl` | Dispatch, start, completion, merge, rejection, and cancellation events. |
| `trace.jsonl` | Time-ordered Chialoop decisions: node selection and scores, finished-result handoffs, per-node cumulative bin gains, skips, timeouts, cancellations, and stopping. |
| `summary.json` | Coverage, Compute95 eligibility, compute/license use, utilization, overhead, and status totals. |

In `trace.jsonl`, `results_delivered` records the ordered batch handed from the runtime to Chialoop. Each following `result_processed` records the coverage gain and cumulative gain for that job's node. `node_selected` records the available candidates and score context used for a dispatch decision.

Every `compare` output directory contains:

| File | Contents |
| --- | --- |
| `comparison.json` | Machine-readable paired ROI statistics and plot data. |
| `comparison.md` | Short human-readable result. |
| `coverage_vs_compute_hours.svg` | Static versus adaptive coverage growth. |
| `resource_timeline.svg` | Resource allocation over time. |

New outputs belong under [`runs/`](runs/README.md). Curated historical examples
are under [`runs/legacy/`](runs/legacy), while older evidence remains in the
top-level `artifacts_*` directories.

## 7. Documentation

- [ROI plan](Docs/CHIALOOP_ROI_PLAN.md) — fair baseline, frozen campaign, metrics, and expected comparison.
- [Implementation overview](Docs/Verification%20ChiaLoop%20Implementation%20Overview.md) — input flow, controller, CHIA nodes, evidence acceptance, and outputs.
- [Architecture overview](Docs/Verification%20ChiaLoop%20Architecture%20Overview.md) — legacy MediumBOOM agent/node architecture.
- [Installation and user guide](Docs/INSTALLATION_AND_USER_GUIDE.md) — environment and backend setup.
- [Final report](Docs/FINAL_REPORT.md) and [submission summary](SUBMIT.md) — demonstrated results and claim boundaries.
- [Whitepaper](Docs/whitepaper/CHIA_ADAPTIVE_VERIFICATION_WHITEPAPER.md) and [short paper](Docs/paper/main.pdf).

Latest pinned MediumBOOM qualification results (five passing instrumented
workloads, toggle and named-cover coverage, bounded/inductive formal evidence,
and remaining review items) are recorded in
[`artifacts_mediumboom/kapil_qualification/README.md`](artifacts_mediumboom/kapil_qualification/README.md)
and [the qualification status](verification/boom/QUALIFICATION.md). These are
partial subsystem results, not a whole-processor signoff or the paired B1/B2/B3
ROI result.

## 8. Tests

```bash
python3 -m pytest tests/chialoop tests/adapters tests/integration tests/verification -q
```

The current suite covers resource admission, static-manifest order, adaptive
selection, evidence validation, asynchronous completion, cancellation, output
generation, MediumBOOM adapters, and paired comparison.
