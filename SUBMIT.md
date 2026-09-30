# CHIA Hackathon submission

## One-sentence claim

We treat verification as a **coverage-per-compute-hour** scheduling problem:
compare CRV-only, a fixed CRV→directed+formal hybrid, and a CHIA adaptive
allocator on the same engines — then scale the same loop from one resource at
a time to **N** parallel compute resources over a large cover space and large
formal partition.

## Three baselines (Feature 1)

| Arm | Schedule | What it answers |
|-----|----------|-----------------|
| **B1 — CRV only** | Constrained-random only | How much of the frozen contract is reachable without directed or formal |
| **B2 — Static hybrid** | Fixed CRV → directed → formal (same engines as CHIA; schedule frozen before outcomes) | Fair control: does feedback beat a competent fixed hybrid that is *allowed* formal |
| **B3 — CHIA adaptive** | Same engines and resource pool; next job chosen from measured yield / open debt | Does adaptive placement improve coverage per compute-hour (or time-to-target) vs B2 |

**Primary metric:** sound coverage closed per compute-hour (and wall time / cost
to a pre-registered target).  
**Not the claim:** “formal found points simulation cannot” alone — that only
shows engine complementarity. CHIA earns a systems claim only if **B3 beats
B2** under identical engines, machines, caches, and license limits.

Honesty rules (Kapil / BOOM package):

- Bounded cover non-hits do **not** retire coverage.
- LSU formal is an explicit scope (`BOOM_FORMAL_SCOPE=lsu`), not a silent
  substitute for ChipTop.
- Historic 51-point MediumBOOM figures are **pilot/smoke** (engine connectivity),
  not a publishable B1/B2/B3 ROI table and not CPU signoff.

## Feature 2 — parallel multi-resource CHIA

| Today (artifact loop) | Next claim |
|-----------------------|------------|
| Mostly **one resource at a time** (serialize CRV / directed / formal rounds) | **N** heterogeneous workers: many simulator slots + scarce formal licenses + build/writer seats |
| Small pilot cover list | **Large** coverage space + **large** formal partition |
| Local `experiments/coverage_growth/run_legacy_experiment.py` driver | Asynchronous CHIA graph: admit jobs only when seats exist, cancel stale work, reuse cached results |

CHIA’s value under Feature 2 is not “more formal.” It is **admission and
replanning under scarcity**: keep simulators busy on high-yield cells while the
one formal seat works the highest expected-closure property batch, without
turning timeouts into fake lower-scope wins.

Design detail: [whitepaper/CHIA_ADAPTIVE_VERIFICATION_WHITEPAPER.md](CHIA_ADAPTIVE_VERIFICATION_WHITEPAPER.md)
(there B0≈sim reference, B1≈static hybrid, T1≈adaptive — same science as B1/B2/B3 above).

## What is in the repo now

| Piece | Role vs the claim |
|-------|-------------------|
| `adapters/mediumboom/` loop + named CHIA slots | Control plane for B3; today mostly serial (Feature 2 not yet measured) |
| MediumBOOM pilot artifacts | Shows CRV vs formal complementarity; a one-seed real-DUT B1/B2/B3 mini-assembler smoke is recorded, but **not** publishable ROI |
| `verification/boom/` monitors + last-mile plan | Source-pinned and elaborated on MediumBoomV3Config; five assertion-enabled DUT workloads pass. The aggregate records 73.59% full-design toggle activity and 582/1,192 named cover counters. Both generated BusyTable variants pass 2-inductive proofs; per-instance qualification remains partial, and the replayed DTLB stale-refill counterexample still needs owner review. |
| Paper + this file | Frames Feature 1 baselines and Feature 2 parallelism without overclaiming |

A corrected one-seed real-DUT feasibility smoke scored 35/51, 31/51, and
35/51 legacy proxy points for B1/B2/B3. Generated directed artifacts were
translated into the BOOM SoC wrapper, compiled, and passed on the DUT (one B2
and two B3 directed jobs). B2's ChipTop formal job still timed out at 20s.
This is a single-seed plumbing check, not evidence of an adaptive win: the
legacy meter is not retirement or semantic RTL coverage, formal closed no
points, and the comparison is underpowered. The earlier 34/34/35 table is
withdrawn because its directed artifact was not the program actually compiled.
Details: [`artifacts_mediumboom/b123_directed_artifact_fixed_20260923/NOTES.md`](artifacts_mediumboom/b123_directed_artifact_fixed_20260923/NOTES.md).

## Reproduce checked plumbing

```bash
python3 -m pytest tests/adapters/mediumboom/test_boom_last_mile.py tests/adapters/mediumboom/test_boom.py \
  tests/integration/legacy_coverage_growth/test_enhancement.py tests/integration/legacy_coverage_growth/test_experiment.py tests/integration/legacy_coverage_growth/test_chia_dispatch.py \
  tests/integration/legacy_coverage_growth/test_b123.py -q
```

Does **not** elaborate MediumBOOM RTL, run the multi-seed/N-worker publishable
campaign, or prove CPU properties.

## Feature-1 / Feature-2 experiments (leftover scaffolding)

```bash
# Exp A — serial B1/B2/B3
python3 experiments/coverage_growth/run_legacy_experiment.py --backend leftover --oracles none \
  --arms b1,b2,b3 --budget-hours 24 --seeds 1,2,3 \
  --out artifacts_leftover_rich/b123_compare

# Exp C — parallel B2/B3 (4 sim + 1 formal seats)
python3 experiments/coverage_growth/run_legacy_experiment.py --backend leftover --oracles none \
  --arms b2,b3 --parallel --n-sim 4 --n-formal 1 \
  --budget-hours 24 --seeds 1,2 \
  --out artifacts_leftover_rich/b23_parallel
```

Measured notes: [artifacts_leftover_rich/b123_compare/NOTES.md](artifacts_leftover_rich/b123_compare/NOTES.md),
[artifacts_leftover_rich/b23_parallel/NOTES.md](artifacts_leftover_rich/b23_parallel/NOTES.md).

Artifact hygiene: experiment `work/` trees, regenerable `frozen_coverage_model.json` dumps, `tmp/`, `.DS_Store`, and `Untitled.md` are gitignored / removed from the tree. Keep NOTES, charts, `results.json`, and `parallel_stats.json`.

On the leftover wall model within 24h, B3 did **not** beat B2 (calibration
still open). Parallel admission **did** reach peak_sim=4 with peak_formal=1.

## Do not claim

- That B3 already beat B2 on a published compute-hour table (experiment plan
  exists; paired ROI campaign not closed).
- 47/51, 49/51, or “2 retirements” from the old proxy meter.
- That boom monitors are qualified / proven / wired into the judged loop.
- That the DTLB counterexample is a confirmed product bug before BOOM-owner review.
- That an LSU cover run is a ChipTop result.
- UCIS / line-coverage / production CPU closure.

The five real DUT runs and their coverage/formal evidence are summarized in
[`artifacts_mediumboom/kapil_qualification/README.md`](artifacts_mediumboom/kapil_qualification/README.md).

## Docs map

| Doc | Role |
|-----|------|
| **This file** | Submission front door |
| [Docs/FINAL_REPORT.md](Docs/FINAL_REPORT.md) | Judge narrative (baselines + Feature 2) |
| [Docs/paper/main.pdf](Docs/paper/main.pdf) | Updated short design paper |
| [whitepaper/CHIA_ADAPTIVE_VERIFICATION_WHITEPAPER.md](CHIA_ADAPTIVE_VERIFICATION_WHITEPAPER.md) | Full adaptive-vs-static experiment design |
| [Docs/BOOM_LAST_MILE_VERIFICATION.md](Docs/BOOM_LAST_MILE_VERIFICATION.md) | Formal methodology package (next phase) |
| [verification/boom/QUALIFICATION.md](verification/boom/QUALIFICATION.md) | What is / is not proven today |
