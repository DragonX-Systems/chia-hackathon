# Final report — coverage per compute-hour

## Thesis

Verification engines already exist. The scarce resource is the next
compute-hour (and, at scale, which of **N** heterogeneous seats gets the next
job). We submit a CHIA-shaped control plane and an evaluation framing with
three baselines, then a parallelism claim for large cover spaces and large
formal partitions.

## Feature 1 — three baselines

| Arm                  | Policy                                                                                            | Fair use                                          |
| -------------------- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| **B1 CRV only**      | Constrained-random only                                                                           | Diagnostic floor: what simulation alone closes    |
| **B2 Static hybrid** | Fixed CRV → directed → formal; schedule frozen before held-out outcomes; **same engines** as CHIA | Primary control for CHIA ROI                      |
| **B3 CHIA adaptive** | Same pool; next admitted job from open debt + measured yield                                      | Treatment: coverage closed per compute-hour vs B2 |

**Win condition:** B3 reaches a pre-registered sound target sooner or cheaper
than B2. Beating B1 alone is not enough — B1 withholds formal and directed.

**Pilot (not ROI):** historic MediumBOOM smoke showed engine complementarity
(SoC CRV bins vs LSU-scoped SystemVerilog Assertion covers). Those figures are
**not** a B1/B2/B3 compute-hour table. A bounded, one-seed real-DUT B1/B2/B3
mini-assembler feasibility smoke scored 35/51, 31/51, and 35/51 points on the
legacy proxy meter. Generated directed artifacts were compiled into the BOOM
SoC wrapper and passed (one B2, two B3); B2 ChipTop formal timed out at 20
seconds. This one-seed plumbing check does not support an adaptive win. The
earlier 34/34/35 table is withdrawn: that run did not compile the generated
artifact. Bounded cover non-hits do not retire points; LSU is an explicit
scope, not a silent ChipTop substitute.
Details: [corrected pilot notes](../artifacts_mediumboom/b123_directed_artifact_fixed_20260923/NOTES.md).

## Feature 2 — from one seat to N parallel resources

| Today | Next phase |
|-------|------------|
| Artifact loop places **one** engine class per round | Many simulator slots + scarce formal license(s) + build / artifact-writer seats |
| Small frozen pilot list | Large coverage space + large formal partition |
| Serial `experiments/coverage_growth/run_legacy_experiment.py` | Asynchronous CHIA graph with admission, cache keys, stale-job cancel |

Under Feature 2, CHIA’s job is to keep high-yield simulation busy while the
formal seat works the highest expected-closure batch — without inventing
closure from timeouts or scope demotion.

Full protocol (paired runs, ablations, stopping rules):
[whitepaper/CHIA_ADAPTIVE_VERIFICATION_WHITEPAPER.md](whitepaper/CHIA_ADAPTIVE_VERIFICATION_WHITEPAPER.md)
— note whitepaper labels B0≈sim reference, B1≈static hybrid, T1≈adaptive;
same science as B1/B2/B3 here.

## What shipped in this artifact

- Coverage-growth loop with named CHIA slots (CRV, directed write, formal,
  meter) — implements the **shape** of B3; Feature 2 parallelism not yet
  measured at campaign scale.
- Source-pinned BOOM collateral package (67 assertion / 66 scenario families,
  activation identities, overlay staging, last-mile obligations) with partial
  MediumBOOM elaboration, simulation, witness-replay, mutation, bounded formal,
  and local 2-inductive proof evidence. This is still **next-phase
  qualification**, not whole-core proof or CPU signoff.
- Evidence discipline: no retirement from bounded cover miss; no silent
  SoC→LSU fallback; qualification record lists what has **not** run.

## Qualification boundary (Kapil package)

| Check | Status |
|-------|--------|
| Source pin / staging / catalog consistency / targeted Python tests | Pass |
| Chisel elaboration on generated MediumBOOM RTL | Pass; exact source revisions and config recorded |
| Instrumented DUT simulation | Pass, 2 directed + 1 `riscv_dv` workload plus 2 scalar-FP torture smokes; five-workload aggregate has 73.59% full-design toggle activity, 582/1,192 named user-cover counters, and 77.96% signal-toggle activity in the filtered 15-module monitor subset (not property coverage). FP smoke hits 63/64 instances for each FP BusyTable activation family; the unhit preg-0 instance remains open, not retired |
| Cover witness replay / DUT mutation | Partial: DTLB late-refill witness replays; one ROB rollback mutant is caught by the intended checker |
| Formal BMC / cover / induction | Partial: depth-2 BoomFrontend and depth-6 integer/FP RenameBusyTable BMCs pass; both RenameBusyTable assertion cones also pass 2-inductive proofs. DTLB covers reach, while depth-5 BMC returns a replayed `no_stale_refill_after_fence` counterexample; no whole-core or DTLB induction proof |
| Per-instance RTL inventory | Partial; 1,192 named activation/scenario counter hierarchy paths mapped; checker-to-instance qualification and source assertions optimized away remain open |
| Multi-seed B1/B2/B3 compute-hour campaign on N workers | Not run; the corrected one-seed legacy-meter feasibility smoke above is only a plumbing check |

Details: [BOOM_LAST_MILE_VERIFICATION.md](BOOM_LAST_MILE_VERIFICATION.md),
[verification/boom/QUALIFICATION.md](../verification/boom/QUALIFICATION.md).
Evidence, including the generated DTLB formal traces and ROB mutation logs, is
under [artifacts_mediumboom/kapil_qualification](../artifacts_mediumboom/kapil_qualification/README.md).

## Do not say

- That adaptive CHIA already beat static hybrid on a published hours-to-target
  table.
- That 47/51 or 49/51 (or “two retirements”) are current submission results.
- That all BOOM monitor families are fully mutation-qualified or proven.
- That an LSU-only cover job is whole-chip closure.

## Reproduce plumbing

```bash
python3 -m pytest tests/adapters/mediumboom/test_boom_last_mile.py tests/adapters/mediumboom/test_boom.py \
  tests/integration/legacy_coverage_growth/test_enhancement.py tests/integration/legacy_coverage_growth/test_experiment.py tests/integration/legacy_coverage_growth/test_chia_dispatch.py -q
```

Front door: [SUBMIT.md](../SUBMIT.md). Paper:
[paper/main.pdf](paper/main.pdf).
