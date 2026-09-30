# Plan

CHIA hackathon track: **autonomous construction of verification collateral**.

**Submission front door:** [SUBMIT.md](../SUBMIT.md).
**Poster numbers:** [FINAL_REPORT.md](FINAL_REPORT.md).

## Goal

Allocate the next compute-hour across existing engines (CRV, directed, formal)
so leftover impossible SystemVerilog Assertions get **retired** instead of
chased forever. Fitness is coverage-gained-per-compute-hour.

## What is shipped for submission

| Piece | Status |
|-------|--------|
| MediumBOOM 51-pt freeze `20260914` | **Poster** |
| Baseline SoC CRV → 41/51 | Done |
| Enh 2 formal prune → 47/51 hit, 49/51 closure | Done (LSU BMC; ChipTop elaborates, falls back) |
| Enh 3 directed last-mile | Implemented; **null** vs baseline on seed 1 |
| Artifact-writer CHIA node | Done |
| Paper + FINAL_REPORT | Done |
| Leftover-rich 5000-pt | Lab only — **not** poster |
| Enh 1 OpenEvolve / genome | Off poster — do not claim |
| Enh 4 license dispatch | Not implemented |
| Multi-seed MediumBOOM table | Open |
| Full `chia up` MediumBOOM on cluster | Open |

## Yardsticks (do not mix)

| Yardstick | Points | Role |
|-----------|-------:|------|
| MediumBOOM SoC bins + LSU SVA | **51** | **Poster** |
| Leftover-rich | 5000 | Lab scaling only |
| Fake / Spike / TCM / local SBY | varies | Dev honesty only |

## Pending before / after HotCRP (ordered)

### P0 — narrative lock (do now)

1. [x] [SUBMIT.md](../SUBMIT.md) with command, numbers, non-claims
2. [x] Four docs point at MediumBOOM first; leftover demoted
3. [x] Warn old `artifacts_mediumboom/results.json` (~19%)

### P1 — package

4. [ ] Confirm `real_enhancements_fixed/` SVGs + `results.json` in the zip judges open
5. [ ] Optional: re-run seed 1 once and attach formal log snippet to EVAL
6. [ ] Push same tree to `kbansal1512/chia-hackathon` `main`

### P2 — if wall time remains

7. [x] `--seeds 1,2,3` on the same 51-pt freeze → `real_enhancements_multiseed/`
8. [x] Enh 3: directed injects `fence` so `op.fence` / `soc.class.fence` can hit
9. [x] ChipTop elaborates / BMC timeout documented as **future work** only

## Success criteria (honest)

- Judges can run or at least reproduce numbers from
  `artifacts_mediumboom/real_enhancements_fixed/`
- Claim is Enh 2 vs baseline on MediumBOOM — not leftover-rich, not UCIS
- Enh 3 stated as null on seed 1
- No citation of top-level ~19% `results.json`

## Deliverables checklist

- [x] [SUBMIT.md](../SUBMIT.md)
- [x] [FINAL_REPORT.md](FINAL_REPORT.md)
- [x] [CODE_WALKTHROUGH.md](CODE_WALKTHROUGH.md)
- [x] [INSTALLATION_AND_USER_GUIDE.md](INSTALLATION_AND_USER_GUIDE.md)
- [x] [CLASS_DIAGRAM.md](CLASS_DIAGRAM.md)
- [x] [ARCHITECTURE.md](Verification%20ChiaLoop%20Architecture%20Overview.md)
- [x] Paper PDF under `paper/`
- [ ] Multi-seed table (optional strength)
- [ ] Cluster-scale boom run (optional)
