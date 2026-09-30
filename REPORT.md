# Report: Compute-hour efficient coverage growth

Written from the code and **local runs on this Mac** (2026-09-01 … 2026-09-09).
This is what we can claim. What we did not run is labeled as such.

**Judge pack (5000-point leftover-rich, both enhancements, architecture):**
[FINAL_REPORT.md](FINAL_REPORT.md). This file is the lab notebook: every DUT,
every honesty limit, and the full source of the four named oracles. The
38-point MediumBOOM curve is **not** the poster pack.

---

## 0. Ideas we came up with

The engines already exist. The contribution is **who gets the next compute-hour**,
and how we refuse to cheat the meter.

1. **If yields do not differ, no planner wins.** Coverage growth is a bandit.
   Payout is new coverage per compute-hour. A spreadsheet beats a chat model when
   torture still pays; nothing beats a fixed recipe when every engine pays the same.
2. **Hit versus retired.** Hit is observation. Retired is “this cannot happen on
   this Device Under Test (DUT).” Only formal may retire. Baseline never calls
   formal, so it chases ghosts with directed tests — that is the closure gap.
3. **Planner-blind meter.** Frozen list before any arm. Planner sees open names
   (id, kind, unit) only. No easy/hard. No “impossible” tag. SHA-256 seal fails
   the run if the JSON is edited (isolation demo, not a secret).
4. **Pre-register, then freeze.** Four named oracles vs industrial baseline,
   catalog `oracles-20260907`. Wrong assumption → new catalog id. Do not retcon.
   The sticky heuristic’s latch (`formal.batches > 0` never returns to constrained-
   random), 1.2 h formal floor (billed request, not wall), and directed-on-SVA
   were bugs, not taste.
5. **Kind-based leftover split.** SystemVerilog Assertion (SVA) leftovers belong
   to formal. Directed never snipes SVA. Gates (~70% / ~80% / 40% hit) use the
   **CRV-reachable** leftover, not planted impossibles. Formal prune **after**
   40% hit so hours-to-40% is not stolen by an early solver.
6. **Bill wall, not the request.** MediumBOOM cover Bounded Model Checking is
   ~55–70 s on generated `LSU.sv`. Asking for 1.2 h wastes budget that was not
   compute. Formal request 0.15 h.
7. **Language models write tests; they do not pick the hour.** Last-mile
   assembly is the useful LLM job. We did not judge an LLM planner. OpenEvolve /
   AlphaEvolve did not run on leftover-rich (`0` `constraint_tuning` batches) —
   do not show it.
8. **Pick the hours axis that measures last-mile.** MediumBOOM: hours to 90% of
   baseline-final (`cph_greedy` **0.30×**). Leftover-rich: that bar is **1.0×**
   (shared CRV prefix) — quote **hours to 40% hit** instead (**8.0 h vs 23.8 h**).
9. **Show only what moved the curve.** Formal prune and directed last-mile did.
   Genome retune did not.
10. **The solver can correct the property.** `irq_without_mer` was reachable;
    it is now `irq_mer_hold` (hit). SymbiYosys is the meter of last resort.

**Headline numbers (poster = leftover-rich 5000 points / 10 seeds).**

| Pack | Arm | Result |
|------|-----|--------|
| **Evidence A** Enh 2 impossible prune | `enh_formal` | **10 seeds.** Hit **53.5%** / closure **53.6%** vs baseline **39.8%**; 40% hit in **7.9 h vs 22.0 h**; 50% hit in **18.3 h** |
| **Evidence B** Enh 3 hard last-mile | `enh_directed` | **10 seeds** (hit 44.5–45.5%). Mean hit **44.8%** vs **39.8%**. **Not** hours-to-40%: that bar is still CRV. 12 directed batches/seed, 0 formal. |

MediumBOOM 38-point SoC is in section F.5 of this notebook only — too few cover points for the poster.

---

## 1. The planning problem (not a dataset)

We do not train on a public ML dataset. The object is a **fixed cover list**
and a **budget of compute**. Each round: look at open holes + how many new
holes each engine closed **per hour last time**, then spend the next block
on one engine.

That is a **bandit / scheduling** problem.

### What “If the engines’ yields do not differ, no LLM wins” means

A **bandit** is: several slot machines, you do not know which pays. You pull
one arm, see the payout, update your guess, pull again.

Here each “arm” is an engine (torture, riscv-dv, retune, directed, formal).
The “payout” is **new coverage per compute-hour**.

- If torture still adds 50 bins/hour and formal adds 2, the next hour should
  go to torture. A spreadsheet sees that. A chat model is optional flavor.
- If **every** engine adds the same bins per hour, switching does not help.
  GPT-5, Astra, or a human will all pick something, and the **curve will
  look the same as a fixed recipe**. Smarter chat does not create slope
  that is not in the engines.

So: first **measure** whether yields differ (the consolation in the
proposal). Only then is a planner—rule or LLM—worth anything.

**What we use:** four **named oracles** (`adapters/mediumboom/oracle_policy.py`,
catalog `oracles-20260907`). They are the judged agent arms. The old
**sticky last-mile heuristic** (`heuristic_plan` in
`adapters/mediumboom/planner.py`) is still in the tree behind
`COVERAGE_PLANNER=heuristic`, but it is **not** an experiment arm and
**not** the default. Default `COVERAGE_PLANNER=sva_first`. An optional
`COVERAGE_PLANNER=llm` path can call OpenAI `gpt-4o-mini` (override
`COVERAGE_PLANNER_MODEL`). Tokens are logged, never billed as sim-hours.
We did **not** judge an LLM **planner** arm. Section F writes the
heuristic and each oracle in full.

Last-mile **test writing** is wired: Astra if `ASTRA_API_KEY`+`ASTRA_API_BASE`
are set, else Poolside Laguna (`POOLSIDE_API_KEY`), else a name-directed
template. Verilator scores whatever came back. That is the useful LLM job.

---

## 2. Meter, HMAC, hit vs retired

**Meter** = scoreboard. It owns the frozen list. The planner only sees
open names (id, kind, unit). Not easy/hard, not “impossible.”

**HMAC** here = a **seal**, not a login. After the list is built, we
SHA-256 the JSON (prefix `meter-only|` + seed). On every snapshot/merge
we recompute. If someone edits a point, the seal fails and scoring stops.
The prefix lives in the same file, so this is a **demo of isolation**,
not crypto against someone who owns the repo. On a real cluster the lock
is: meter on a worker the planner cannot write.

**Hit** = we observed the point.

**Retired** = we **stop chasing** it because it cannot happen on this DUT.
Not “we got bored.”

### How retirement is decided (honest)

**Should be:** SymbiYosys BMC/induction. Property “this cover can fire”:
proved impossible → retire; found a trace → hit; timeout → leave open.

**What the code does:**

| Backend | How “unreachable” is known | How we retire |
|---------|----------------------------|---------------|
| Fake yield model (`experiments/coverage_growth/run_legacy_experiment.py`) | ~8% of 500 points tagged `bucket: unreachable` at freeze (planner cannot see the tag) | Formal action; ~78% chance to retire if that hidden tag is set. Meter only accepts retire if `bucket == unreachable`. |
| Spike / riscv-dv catalog (`--backend real`) | F/A opcodes (`FADD_S`, `FLW`, `LR_W`, …) are not in `--isa=rv32im` | Formal stub retires those IDs in one shot. No SBY. |
| Real SBY (`--backend sby`) | Solver status on named covers | **Cover witness → hit. k-induction PASS on the matching assert → retire.** Installed 2026-09-07: Homebrew `sby` 0.68, Yosys 0.68, `smtbmc z3`. |

Torture / CRV / directed **cannot** retire.

### What we ran locally (2026-09-07) — and what we still did not

**Did:**

1. `brew install sby` (Yosys was already here; SBY is a separate package). Engine is Z3, not Boolector.
2. Cloned [riscv-boom](https://github.com/riscv-boom/riscv-boom) into `third_party/riscv-boom` for LSU names (`v3/lsu/lsu.scala`: LDQ/STQ, replay, store-queue forward).
3. Cloned [ultraembedded/riscv](https://github.com/ultraembedded/riscv) — the empty `riscv_soc/core` submodule — and instantiated **`riscv_decoder.v`**.
4. Instantiated parent **`riscv_soc/soc/irq_ctrl.v`** (four interrupt sources, AXI-lite).
5. Wired `--backend sby` so formal batches call `sby` and parse `Reached` / `Unreached` / `successful proof by k-induction`.

**Still not:**

- A complete `make verilog` (SRAM split-mems-conf fails on this Mac). **Firtool
  split-Verilog did emit** `gen-collateral` for `MediumBoomV3Config`: 610
  files, including `LSU.sv` (16 470 lines, 502 ports). See section F.
- Full-core BOOM proofs (hours of solver time and property writing).
- Formal as the judged engine on the 500-point synthetic list (that list has no Verilog).

SBY also **corrected** us: `irq_without_mer` (`intr_o && !MER`) is reachable. MER and the host line are separate flops, so the host can stay high one cycle after MER drops. That point is now `irq_mer_hold` (hit), not retired. That is the point of a real solver.

---

## 3. Local results (what we actually ran)

**Who “agent” is in A–E.** Those tables compare the **sticky heuristic**
(`heuristic_plan`) to the industrial baseline. They were frozen before
the named-oracle catalog. Section F is the new judged set. Do not read
A–E as `sva_first`.

### A. Yield-model arm — `artifacts/` (2026-09-01)

Command: `python3 experiments/coverage_growth/run_legacy_experiment.py --budget-hours 36 --seeds 1,2,3 --out artifacts`
DUT: 500 frozen synthetic points (line/toggle/SVA mix). No Spike, no Verilator.

| | Agent | Baseline (riscv-dv then directed) |
|---|---|---|
| Mean **hit** | 84.8% | 81.8% |
| Mean **closure** (hit + retired) | **88.1%** | 81.8% |
| Hours to 90% of baseline-final hit | 20.2 | 22.7 (**1.12×**) |
| Hours to 95% | 25.4 | 28.7 |
| Hours to 99% | 30.6 | 33.3 |

Agent action counts (3 seeds): torture 6, riscv-dv 31, formal 19, directed 17.
Late stage (closure ≥ 0.80): formal 14, directed 14.

**Read this as:** the planner left carpet-bombing, then split last mile
between “drop ghosts” and “snipe leftovers.” Closure gap is mostly
**retirement**, not extra hits. Yields **did** differ enough to exploit
in this model (that is the proposal’s risk check).

Curve: `artifacts/coverage_vs_hours.svg`.

### B. gcc + Spike ISA bins — `artifacts_real/` (2026-09-02)

Command: `--backend real --budget-hours 16 --seeds 1,2`  
Toolchain: `riscv32-unknown-elf-gcc`, Spike `--isa=rv32im`, instruction
lists from `~/riscv-dv` `rv32i_instr.py` / `rv32m_instr.py`.  
**41 bins**, 5 unreachable (F/A). `--budget-hours` here is **wall seconds**.

| | Agent | Baseline |
|---|---|---|
| Mean hit | 87.8% | 87.8% |
| Mean closure | **100%** | 87.8% |

Same hits; agent **retired the 5 impossible opcodes**. Baseline never
calls formal, so it stays at 87.8% and still wastes directed shots on
ghosts. Hours-to-90% favored baseline (agent spent early seconds on
torture). The headline here is **closure**, not speed.

### C. CHIA-shaped example — `runs/legacy/model/` (2026-09-04)

`cluster_driver.py` with local `ray.init(resources={torture, verilator_run, …})`.
Seed 1, 16 “hours” of the yield model.

| | Agent | Baseline |
|---|---|---|
| Hit / closure | 70.8% | 68.8% |

Agent: torture 2 → riscv-dv 7 (budget ended before last-mile formal).
Proves **scheduling**: each step is `chia_remote` onto a named slot.

Same loop with `--backend verilator --budget-hours 2` (2026-09-07): local
`ray.init`, Verilator scoring. Agent 79.7% hit / 80.8% closure vs baseline
77.4%. Last step was formal on the leftover SVA list. Still not
`chia viz-profile` on a cluster.

### D. Real SymbiYosys — `artifacts_sby/` (2026-09-07)

Command: `python3 experiments/coverage_growth/run_legacy_experiment.py --backend sby --budget-hours 8 --seeds 1,2 --out artifacts_sby`
17 frozen SVA points on three DUTs. Formal is **sby + Z3**. Other actions still use the yield model on that same list.

| | Agent | Baseline |
|---|---|---|
| Mean hit | 64.7% (11/17 reachable) | 38.2% |
| Mean **closure** | **100%** | 38.2% |
| Hours to 90% of baseline-final hit | 3.2 | 8.0 (**2.5×**) |
| Late-stage formal steps | 2 | 0 |

Oracle (one SBY pass, ~2.9 s wall): `artifacts_sby/sby_oracle.json`.

**Hits (cover witness):** BOOM LSU load / store / replay / STQ forward; decoder ADDI / LW / BEQ / MUL-when-M; `irq_ctrl` irq0, irq1, and the one-cycle MER hold.

**Retired (k-induction PASS):** AMO with `HAS_AMO=0`; load-and-store on the same `cmd`; FADD as a legal RV32IM decode; MUL with `enable_muldiv=0`; IRQ vector 4; `bresp != OK` (tied to 0 in `irq_ctrl`).

Baseline never calls formal, so the six ghosts stay open and the hit rate stays below the reachable ceiling.

### Tests

`python3 -m pytest tests -q` — 14 passed (meter HMAC/tamper, unreachable
cannot be “hit”, four named oracles disagree on an SVA-only list and all
carpet on the mixed fake list, boom backend discovers `gen-collateral` when
present, SBY cover/prove on `cover_lsu` when `sby` is on PATH, Verilator 5
coverage parser + freeze).

### E. Verilator scores gcc jobs — `artifacts_verilator/` (2026-09-07)

Command: `--backend verilator --budget-hours 12 --seeds 1,2 --out artifacts_verilator`  
DUT: ultraembedded RV32IM Tightly Coupled Memory (TCM) core under
**Verilator 5.036** (`adapters/mediumboom/testbenches/verilator`). **Not** generated
MediumBOOM.

Frozen yardstick (HMAC’d): **354** bins from a real `coverage.dat` dump —

| Kind | Count | Source |
|------|------:|--------|
| Line | 183 | decoder / ALU / LSU / issue / CSR / fetch |
| Branch-toggle | 150 | Verilator `v_branch` (bit-toggles on 64KB RAM were dropped; they explode the C++ build) |
| SystemVerilog Assertion (SVA) | 21 | 4 user covers in `tb_top` + 17 SymbiYosys properties |

Torture / riscv-dv / directed: `riscv32-unknown-elf-gcc` → raw binary into TCM → `Vtb_top` → merge counts.  
Formal: **sby + Z3** on leftover SVA (same three slices as section D).  
Clock: **batch wall seconds** charged as hour units; per-step wall is in
`chia_profile.json`. On a cluster, `chia viz-profile` is the same currency
(Ray task wall). This is **not** the 1.12× yield-model number.

| | Agent | Baseline (riscv-dv until plateau) |
|---|---|---|
| Mean **hit** | **85.6%** | 77.5% |
| Mean **closure** | **87.3%** | 77.5% |
| Hours to 90% of baseline-final hit | 0.11 | **0.08** (baseline faster) |
| Hours to 95% | 0.17 | 0.14 |
| Hours to 99% | 2.65 | 0.66 |
| Late-stage formal / directed | 3 / 65 | 0 / 0 |

Agent: 1 torture → a few CRV batches → last-mile formal (cover witness +
k-induction PASS on the six unreachable properties) → directed sniper.  
Baseline never calls formal, so it does not retire the ghosts.

**Read this as:** Verilator **does** score real jobs. The last-mile **hit**
gap (85.6% vs 77.5%) is the self-authored directed suite, not random
CRV. Hours-to-90% still does **not** favor the agent (torture tax +
baseline’s first CRV batch already covers always-on lines). Closure adds
**real SBY retirement** on top. Formal is seconds of Bounded Model
Checking (BMC) / induction on named leftover properties, not hours on
every unhit CSR `ecall` line. Those leftover line bins need more
directed tests, not more random.

Last-mile writer: **this session** (`LAST_MILE_WRITER=self`, default).
The directed suite is `adapters/mediumboom/testbenches/verilator/last_mile_self.S` —
shift-lane if/else, signed SLT, LBU/LH/SH, both branch polarities, JALR,
M-extension. Verilator scores it. One self directed batch after CRV added
**35 new bins** (257 → 292 / 354) vs the old random template. Astra/Poolside
are still optional via `LAST_MILE_WRITER=poolside`.

Curve: `artifacts_verilator/coverage_vs_hours.svg`. Profile:
`artifacts_verilator/chia_profile.json`.

### F. Policies: industrial baseline, sticky heuristic, four named oracles

Every judged run has **one industrial baseline** plus **four named oracles**.
The **sticky heuristic** is a fifth policy that used to be “the agent.” It
is still in `adapters/mediumboom/planner.py` as `heuristic_plan`, but
`experiments/coverage_growth/run_legacy_experiment.py --oracles all` does **not** plot it. Catalog id
`oracles-20260907`. Code: `adapters/mediumboom/oracle_policy.py`. Notes:
`artifacts_mediumboom/PRE_REGISTERED_POLICY.md`.

If an assumption is wrong after a curve exists, **add a new catalog id**.
Do not edit these functions to match the plot.

Shared vocabulary:

| Engine | What it does |
|--------|----------------|
| torture | Random instruction-stream carpet-bomb. Cannot retire. |
| `riscv_dv` | Constrained-random verification (CRV) from the riscv-dv catalog. Cannot retire. |
| constraint_tuning | Cheap retune of CRV knobs when the last CRV batch dried up. Not a stage. |
| directed | Named leftover points. Hits possible; **cannot** retire unreachable SystemVerilog Assertions (SVA). |
| formal | Bounded Model Checking (BMC) / k-induction. The only engine that may **retire**. |

The planner never sees easy/hard. It sees open names, kinds (`sva` vs
line/toggle), units, and last-batch coverage-per-hour.

#### F.0 Industrial baseline (`baseline_plan`) — the recipe we have to beat

This is **not** an oracle. It is the industrial default in
`adapters/mediumboom/baseline.py`.

1. Run `riscv_dv` in 2.0 h blocks while hit fraction &lt; 78% **and** CRV
   has not stalled (`batches ≥ 4` and coverage-per-hour &lt; 3.5).
2. Then a **fixed directed suite**: 1.2 h, first 10 open point ids, **no
   formal**, no distinction between reachable line bins and unreachable SVA.

On an SVA-only list it therefore opens with constrained-random and **never
retires**. That is why Spike / SymbiYosys / Verilator curves show a
**closure** gap even when **hit** is tied.

#### F.0b Sticky heuristic (`heuristic_plan`) — archived, not judged

This is what “the agent” was in sections A–E of this report (the 1.12×
yield-model number, the Spike closure 100%, the Verilator 85.6% hit). It
is a **stage machine with a latch**, not a bandit.

**Stages (checked in this order):**

1. **Last-mile latch.** Enter last-mile if **any** of:
   - closure ≥ 80%, **or**
   - we have already run directed **or** formal even once
     (`directed.batches > 0 or formal.batches > 0`) — this is the
     **sticky** bit: one formal batch keeps us in last-mile forever, **or**
   - CRV has four dry-ish batches and closure ≥ 68%, **or**
   - the leftover list is **SVA-only** and we already did one torture or
     CRV batch, **or**
   - Verilator-shaped: CRV last-batch coverage-per-hour &lt; 2 after two
     batches, and (torture ran **or** closure ≥ 45%).
   Once latched: if any SVA remain and the last formal batch was not dry
   (0 hits and 0 retired), spend **1.2 h formal**. Else **1.2 h directed**,
   including leftover SVA that only formal can retire.
2. **Mid CRV.** If torture has already burned ≥ 4 h **or** closure ≥ 50%:
   `riscv_dv` 1.8 h, with a 0.08 h retune when CRV last-batch
   coverage-per-hour &lt; 3.
3. **Early carpet.** First batch is always torture 2.0 h. Stay on torture
   while last-batch coverage-per-hour ≥ 12 and torture hours &lt; 8.
   Otherwise leave for CRV.

**Why we stopped using it as the agent.** Three bugs, not taste:

- **Latch:** `formal.batches > 0` is enough to skip CRV for the rest of
  the budget. One explore-formal on a mixed list never comes back.
- **1.2 h formal floor.** The boom backend charges
  `max(hour_budget, wall/3600, 0.05)`. Asking for 1.2 h when SymbiYosys
  took 70 s **bills 1.2 h of budget that was not compute**.
- **Directed on unreachable SVA.** Last-mile directed targets whatever is
  open, same as baseline. Sim cannot retire those points.

`COVERAGE_PLANNER=heuristic` still runs this function. It is **not** in
`--oracles all`. Sections A–E numbers are this policy vs baseline, and
should be read that way.

#### F.1 `sva_first` — retire-first (default judged agent)

**Assumes.** Leftover SVA only close via formal. Sim engines cannot
retire. Keep the formal *request* at 0.15 h so **wall seconds** are the
cost (on MediumBOOM that was 70 s cover BMC — section F timing).

**Decision order:**

1. If the open list is **SVA-only**: formal 0.15 h, at most twice, unless
   the last formal batch was dry or looked like a timeout (last hours ≥
   0.9 and last coverage-per-hour = 0). After that, formal 0.05 h — **do
   not recarpet**.
2. Else last-mile only when closure ≥ 80% **or** (CRV is dead after two
   batches **and** torture has run). Not because we already touched
   formal. Then formal 0.15 h for remaining SVA (cap two), else directed
   0.8 h on non-SVA leftovers.
3. Else if we already carpet-bombed once and torture collapsed / hit 2 h /
   closure ≥ 45%: mid CRV 1.5 h, with the cheap retune.
4. Else one early torture 1.5 h; stay only while last-batch
   coverage-per-hour ≥ 12 and torture hours &lt; 3.

**Step 1.** SVA-only MediumBOOM list → **formal**. Mixed list (fake 500-point
**or** MediumBOOM seed `20260908` with RV64 opcode bins) → torture.

**Fails if.** Formal is so slow that even 0.15 h wastes the budget, or
sim actually hits the named covers (then we paid a solver for hits a
random stream would have got).

#### F.2 `sim_max` — formal-last

**Assumes.** BMC wall time on MediumBOOM is unknown and possibly huge.
Hits from torture / CRV / directed are cheap relative to a solver
timeout. (The 70 s cover number **weakens** this assumption but does not
kill it: prove still fails immediately on unconstrained ports, and a
deeper bound or a full core could still be huge.)

**Decision order:**

1. Formal **only** when sim is dead (torture ran, CRV last-batch
   coverage-per-hour &lt; 2 after two batches, directed also dead) **or**
   when nothing but SVA remain. Cap two formal batches, 0.15 h.
2. Until then: wring torture (2.0 h, stay while last-batch
   coverage-per-hour ≥ 10 and hours &lt; 6), then CRV 1.8 h up to 12 h if
   it still pays, then **directed before formal** even if SVA are open.

**Step 1.** Always torture, including SVA-only.

**Fails if.** The leftover is mostly unreachable SVA — directed cannot
hit them, and we burn hours before retiring.

#### F.3 `cph_greedy` — last-batch coverage-per-hour bandit

**Assumes.** Last-batch coverage-per-hour is a sufficient statistic.
Stages are a prior we should not freeze. This is the CHIA claim:
sequencing is a bandit, not a chat.

**Decision order:**

1. **Explore once:** torture → CRV → formal (only if SVA are open) →
   directed. Formal explore is 0.15 h.
2. Cheap CRV retune if CRV collapsed, same rule as the others.
3. Then pick the engine with the **highest last-batch coverage-per-hour**
   among torture / CRV / directed (drop torture if last-batch
   coverage-per-hour &lt; 8). Formal stays in the set only while SVA
   remain, the last formal batch was not dry, and we have used formal
   fewer than three times.

**Step 1.** Torture (explore), including SVA-only.

**Fails if.** One lucky directed hit keeps us on a dead engine, or
explore-formal on a mixed list spends solver time too early.

#### F.4 `stage_clean` — industrial stages, no latch

**Assumes.** Carpet → CRV → last-mile is the right **shape** if last-mile
cannot stick just because we already touched formal or directed.

**Decision order** — same hour sizes as the old heuristic’s early/mid
stages (torture 2.0 h until 8 h or collapse, then CRV 1.8 h), but
last-mile is **only**

- closure ≥ 80%, **or**
- CRV last-batch coverage-per-hour &lt; 2 after two batches **and**
  torture has run.

No `formal.batches > 0` latch. Formal 0.15 h, at most twice, SVA only;
otherwise directed 0.8 h.

**Step 1.** Torture, including SVA-only (because last-mile has not
triggered yet).

**Fails if.** Always-on line bins inflate early closure and we enter
last-mile before CRV has done its job (the Verilator Tightly Coupled
Memory failure mode that made hours-to-90% favor baseline).

#### How they disagree (pre-registered)

On an **SVA-only** MediumBOOM list, empty yields, step 1:

| Policy | Step 1 | Formal request |
|--------|--------|----------------|
| baseline | constrained-random 2.0 h | never |
| sticky heuristic | torture 2.0 h (last-mile latch is off until one torture/CRV batch, then 1.2 h formal) | 1.2 h once latched |
| `sva_first` | **formal 0.15 h** | 0.15 h |
| `sim_max` | torture 2.0 h | only after sim dies |
| `cph_greedy` | torture 1.5 h (explore) | third explore if SVA still open |
| `stage_clean` | torture 2.0 h | last-mile only |

On the **mixed 500-point fake list**, all four oracles **and** the
heuristic open with torture. That is why fake-yield 36 h does not
separate them. The disagreement we care about is `--backend boom`.

**Did:** lock the four functions + catalog id; `python3 experiments/coverage_growth/run_legacy_experiment.py
--oracles all` runs baseline plus every oracle on the same seeds; SVG
overlays all arms; tests check the step-1 table above.

**Chisel emit (2026-09-07 … 2026-09-08, this Mac).** `adapters/mediumboom/scripts/setup_mediumboom.sh`:
OpenJDK 17, sbt server off, Homebrew Bash 5 (for `|&`), GNU sed `-i`.
Firtool 1.158.0 wrote `gen-collateral`. Dropped the phantom
`rob_debug_inst_mem_ext` SRAM name so `split-mems-conf.py` finishes.
`make CONFIG=MediumBoomV3Config` produced
`simulator-chipyard.harness-MediumBoomV3Config` (~12 MB). A macOS
`SimTSI` patch (`scripts/patches/SimTSI_empty_in_bits.patch`) avoids
calling `in_bits()` / `deque::front()` on an empty host queue (SIGSEGV).

**SymbiYosys on generated `LSU` (Z3, depth 4–8, unconstrained ports).**
`artifacts_mediumboom/formal_timing.json` / cover BMC ~55–70 s. Prove is
off by default (`BOOM_FORMAL_PROVE=1`) — Chisel protocol asserts fire with
no core driving the LSU. Retirement of unreachable covers uses cover
**Unreached**, not prove PASS.

| Task | Wall | Result |
|------|-----:|--------|
| Cover BMC (depth 4, earlier) | **70.5 s** | Reached `a`, `b`, `both`, `rst_rel`. **Unreached** `contra`, `never`. |
| Cover BMC (depth 8, oracle runs) | **~55–57 s** | Same six-name map on the 502-port wrapper. |
| Prove (optional) | FAIL | Protocol asserts in vacuum — not a statement about named covers. |

`--backend boom` charges `max(hour_budget, wall/3600, 0.05)` for formal.

#### F.5 Judged MediumBOOM curve (2026-09-08) — real SoC CRV + formal

Yardstick seed **`20260908`**: six SystemVerilog Assertion (SVA) Load/Store
Unit (LSU) covers **plus** RV64 opcode / branch bins (38 points). Constrained-
random verification (CRV) is **real**: generate RV64 programs, run the
MediumBOOM Verilator System on Chip (SoC) with `+loadmem`, credit bins only
on `*** PASSED ***`. Not a Verilator coverage dump (`VM_COVERAGE=0`) and not
BOOM commit-log printf (disabled in this config). Details:
`artifacts_mediumboom/EVAL.md`.

Command: `--oracles all --backend boom --budget-hours 4 --seeds 1,2,3`
(~14 min wall).

| Arm | Mean hit | Mean closure |
|-----|---------:|-------------:|
| baseline | 84.2% | 84.2% |
| `sva_first` | 94.7% | **100%** |
| `sim_max` | 94.7% | **100%** |
| `cph_greedy` | 94.7% | **100%** |
| `stage_clean` | 94.7% | **100%** |

Step-1 picks are all **torture** (mixed list, not SVA-only). Baseline never
calls formal, so the two unreachable SVA points stay open (84% hit =
32/38). Named oracles reach formal after CRV and close to 100%. Hours to
90% of baseline-final (lower is faster): `cph_greedy` 0.30×, `sim_max`
0.34×, `stage_clean` 0.43×, `sva_first` 0.50×.

**Did not retcon** catalog `oracles-20260907` after this curve. Fake-yield
36 h on the mixed 500-point list is still **not** a MediumBOOM claim.

### Enhancement arm `enh_v1` (not catalog)

A separate allocator (`enh_v1`, plan id `enh-20260909`) sits **outside**
catalog `oracles-20260907`. Full figure pack (same layout as §4 MediumBOOM
Figures 1–4, plus hours-to-40% hit): **[FINAL_REPORT.md](FINAL_REPORT.md)**.

Leftover-rich wall-hour model, **5000 points**, 24 h, **10 seeds**
(`artifacts_leftover_rich/`): baseline **39.8%** hit. `enh_formal` **53.5%** /
**53.6%** closure; `enh_directed` **44.8%**; `enh_v1` **45.0%** / **45.2%**.
Hours to **40% hit**: 22.0 h vs **7.9 h** (ratio **0.36**, all three arms).
Architecture: [ARCHITECTURE.md](Verification%20ChiaLoop%20Architecture%20Overview.md). **Not shown:** AlphaEvolve.
Do **not** quote hours-to-90%-of-baseline-final (1.0×). MediumBOOM 38-point
eval was **not** re-run for this arm.

```bash
python3 experiments/coverage_growth/run_legacy_experiment.py --oracles all --backend fake --budget-hours 36 --out artifacts
python3 -m adapters.mediumboom.boom_backend --out artifacts_mediumboom --depths 4 --timeout 180
export RISCV=/tmp/cy-riscv
export DYLD_LIBRARY_PATH="/tmp/cy-riscv/lib:/opt/homebrew/lib:$PWD/third_party/chipyard/tools/DRAMSim2"
python3 experiments/coverage_growth/run_legacy_experiment.py --oracles all --backend boom --budget-hours 4 --seeds 1,2,3 \
  --out artifacts_mediumboom
python3 experiments/coverage_growth/run_legacy_experiment.py --backend leftover --oracles none --enhancement \
  --budget-hours 24 --seeds 1,2,3,4,5,6,7,8,9,10 --out artifacts_leftover_rich
```

---

## 4. What to show at the hackathon

Poster pack: [FINAL_REPORT.md](FINAL_REPORT.md). **5000 cover points, 10 seeds.**
Evidence A = Enh 2 (`enh_formal`). Evidence B = Enh 3 (`enh_directed`).
The 38-point MediumBOOM pack is **not** on the poster (too few points).

### Pitch (30 seconds)

1. **Problem:** last-mile coverage is **allocation**, not more random.
2. **Loop:** frozen meter → leftover split → `enh_formal` or `enh_directed` →
   `chia_remote` onto a CHIA node → `get()` → merge.
3. **Result:** Enh 2 **53.5%** hit (closure **53.6%**, 10 seeds). Enh 3
   **44.8%** hit (10 seeds, range 44.5–45.5%), **not** the 7.9 h hours bar.

### Figures (submission pack)

Command: `--backend leftover --oracles none --enhancement --budget-hours 24 --seeds 1,2,3,4,5,6,7,8,9,10`  
Artifacts: `artifacts_leftover_rich/`.

**Evidence A — Enh 2 impossible prune** (`coverage_vs_hours_enh_formal.svg`)

![Enh 2 coverage](artifacts_leftover_rich/coverage_vs_hours_enh_formal.svg)

![Enh 2 bars](artifacts_leftover_rich/final_coverage_bars_enh_formal.svg)

**Evidence B — Enh 3 hard last-mile** (`coverage_vs_hours_enh_directed.svg`)

![Enh 3 coverage](artifacts_leftover_rich/coverage_vs_hours_enh_directed.svg)

![Enh 3 bars](artifacts_leftover_rich/final_coverage_bars_enh_directed.svg)

**Overlay (both vs baseline)**

![Coverage vs hours — leftover-rich](artifacts_leftover_rich/coverage_vs_hours.svg)

![Hours to 40% hit](artifacts_leftover_rich/hours_to_40pct.svg)

![Engine mix — leftover-rich](artifacts_leftover_rich/engine_mix.svg)

### Not on the poster

Chipyard MediumBOOM 38-point SoC (section F.5 / `artifacts_mediumboom/`) is
supporting lab work, not the submission curve.

### Policy story (with that curve)

- The four **pre-registered oracles** vs industrial **baseline** (section F),
  catalog `oracles-20260907`. Do **not** retcon after seeing curves.
- Archived **sticky heuristic** (`COVERAGE_PLANNER=heuristic`): last-mile
  latch + **1.2 h** formal floor — what we replaced.
- **Step-1 table** (section F): SVA-only → `sva_first` opens with **formal**;
  this MediumBOOM yardstick (seed `20260908`, mixed SVA + opcode bins) → all
  open with **torture**.

### Hours-to-X%

Quote hours to 90 / 95 / 99% **only** for MediumBOOM on this slide. Do not
mix other DUTs onto the same axis.

### Close with

This report’s **CHIA helped / did not** list (section 5).

### Do not say

- That we are demoing Spike / synthetic-yield / TCM-Verilator / local-SBY
  curves as the judged result (those runs exist in the report as supporting
  evidence; they are **not** the hackathon headline).
- MediumBOOM Verilator **coverage dumps** or **commit-log** scoring.
- That we edited an oracle after seeing a curve.

---

## 5. Where CHIA helped — and where it did not

**Helped (use this)**

- Named resources (`verilator_run: 1.0`, `formal: 1.0`, `torture: 0.25`) so
  the loop switches engines without a shell DAG.
- Install once (`chia up` / image pull); jobs only **borrow** slots.
- Isolation *story*: meter worker vs planner (seal is the cheap check).
- Example layout matches `examples/memcpy` and `examples/common/verilator.py`.
- `chia viz-profile` is the right clock if/when we run on a cluster.

**Did not / we did not use**

- Full `chia up` + Chipyard/Verilator Docker on this Mac. Official pin
  `ray==2.54.0` has no macOS wheel; we used Ray **2.49.2** in `chia_env`
  and local `ray.init`. See `INSTALL.md`.
- FireSim / full-core BOOM proofs (section 2). SBY retirement **is** real on
  the three local slices and on generated MediumBOOM LSU covers. Verilator
  scoring **is** real on the ultraembedded Tightly Coupled Memory (TCM) core
  (section E) **and** on MediumBOOM SoC CRV (section F.5), with the honesty
  limits in `EVAL.md`.
- Planner as a CHIA **agent/LLM node**. It is four frozen Python oracles
  (section F), not live chat. The old sticky heuristic is kept only behind
  `COVERAGE_PLANNER=heuristic`.
- HMAC as a real secret.

**One line for the poster**

> CHIA is the right **scheduler** for heterogeneous verification engines.
> The **science** is the yield table and the allocation policy. We did not
> need a frontier LLM to choose the next hour; we use one to **write**
> last-mile tests. Verilator and SymbiYosys score them. CHIA places the jobs.

---

## 6. How to reproduce

```bash
cd chia_hackathon
python3 experiments/coverage_growth/run_legacy_experiment.py --oracles all --budget-hours 36 --seeds 1,2,3 --out artifacts
python3 -m pytest tests -q

# named oracles vs baseline on MediumBOOM (needs gen-collateral + Verilator SoC)
bash adapters/mediumboom/scripts/setup_mediumboom.sh
# after building simulator-chipyard.harness-MediumBoomV3Config and applying
# scripts/patches/SimTSI_empty_in_bits.patch:
export RISCV=/tmp/cy-riscv
export DYLD_LIBRARY_PATH="/tmp/cy-riscv/lib:/opt/homebrew/lib:$PWD/third_party/chipyard/tools/DRAMSim2"
python3 experiments/coverage_growth/run_legacy_experiment.py --oracles all --backend boom --budget-hours 4 --seeds 1,2,3 \
  --out artifacts_mediumboom

# leftover-rich wall-hour model vs baseline (enh_v1; not the MediumBOOM judged curve)
python3 experiments/coverage_growth/run_legacy_experiment.py --backend leftover --oracles none --enhancement \
  --budget-hours 24 --seeds 1,2,3,4,5,6,7,8,9,10 --out artifacts_leftover_rich

# optional Spike (needs gcc + spike + ~/riscv-dv)
export PATH="$HOME/.local/riscv-pulp/bin:$PATH"
python3 experiments/coverage_growth/run_legacy_experiment.py --backend real --budget-hours 16 --seeds 1,2 --out artifacts_real

# real SymbiYosys (needs `sby` + z3; `brew install sby`)
bash scripts/fetch_formal_duts.sh
python3 experiments/coverage_growth/run_legacy_experiment.py --backend sby --budget-hours 8 --seeds 1,2 --out artifacts_sby

# Verilator scores gcc jobs (needs verilator + riscv gcc; not MediumBOOM)
export PATH="$HOME/.local/riscv-pulp/bin:$PATH"
bash adapters/mediumboom/testbenches/verilator/build.sh
python3 experiments/coverage_growth/run_legacy_experiment.py --backend verilator --budget-hours 12 --seeds 1,2 --out artifacts_verilator

conda activate chia_env
python experiments/coverage_growth/cluster_driver.py --budget-hours 16 --seeds 1
# same loop, Verilator scoring:
python experiments/coverage_growth/cluster_driver.py --backend verilator --budget-hours 8 --seeds 1 --out runs/legacy/verilator
```

Repo: https://github.com/kbansal1512/chia-hackathon (private).
