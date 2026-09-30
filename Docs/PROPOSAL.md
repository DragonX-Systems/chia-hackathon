# Compute-Hour Efficient Coverage Growth: Agentic Sequencing of Verification Engines

**Track:** autonomous construction of verification collateral

Verification reaches coverage by carpet bombing. A large constrained-random
regression is efficient at first, then plateaus: random stimulus recovers
ground it already holds, and a slice of remaining cover points is unreachable.
The engines are not weak; the **allocation** is. This loop keeps coverage
progress compute-hour efficient. An agent observes coverage state and measured
coverage-per-hour per engine, then spends the next block of compute on the
action most likely to move the curve.

**DUT.** MediumBOOM in a Chipyard SoC — dynamic engines already exist as CHIA
nodes; effort is the planner plus formal integration.

**Frozen yardstick.** Verilator line, toggle, and hand-written SVA covers over
LSU, branch predictor, and issue queues. Frozen before any arm; collected on a
worker the agent cannot write. Planner sees uncovered identity only.

**Arsenal.** Torture (cheap, saturates early); riscv-dv (mid-range CRV);
constraint tuning (almost free, re-aims a stalled engine); directed
(agent-authored assembly at named points); formal / SymbiYosys (prove or
retire). Each round: query uncovered + yields + reachable denominator →
allocate (action, target, parameters, hour budget) → CHIA fans the batch →
merge coverage, retire unreachable, update yield table.

**Baseline.** riscv-dv at default weights until plateau, then a fixed directed
suite. **Accounting.** Sim-CPU-hours from Ray via `chia viz-profile`; solver
hours in the same currency. LLM tokens reported separately, never billed to
the compute budget. Three seeds.

**Deliverable.** Coverage vs cumulative sim-CPU-hours, agent vs baseline;
hours to 90/95/99% of baseline-final; the learned policy (formal retirement vs
directed last-mile); reusable `SymbiYosysNode`, `EngineYieldNode`,
`CoverageMeterNode`. Week-1 optional: CHIA has no formal node — we contribute
one (Yosys/SBY, BMC and induction) independently useful to the community.

**Risk.** Planner matches a fixed schedule. Consolation: per-action yield
curves are unpublished; a small-budget pilot shows whether the profiles differ
enough to exploit. **Cost.** ~$1000 (sim + solver + head node + LLM + S3).
This is heterogeneous scheduling — five actions, switched mid-run — which is
why it belongs in CHIA rather than a shell script.
