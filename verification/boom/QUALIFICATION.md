# Qualification status

Reviewed BOOM revision: `58ef2720eae13be26b3008c02b5a74ce29c61c44` (v3).

| Check | Result | Meaning |
|---|---|---|
| Source pin / clean checkout / file hashes | PASS | All reviewed inputs matched the source lock |
| Overlay staging and source preservation | PASS | Eleven Scala files staged; upstream checkout unchanged |
| Scala syntax parsing | PASS | Eleven staged files parsed with tree-sitter 0.26.0 / tree-sitter-scala 0.26.2; zero syntax errors |
| Catalog / monitor consistency | PASS | 67 assertion and 66 scenario families; assertion activation IDs checked |
| Targeted Python tests | PASS, 56 passed / 1 skipped | BOOM backend, evidence, staging, experiment/dispatch regressions |
| Scala typechecking and Chisel elaboration | PASS | Matching Chipyard/BOOM build completed with Chisel 7; exact run details in `artifacts_mediumboom/kapil_qualification/README.md` |
| Generated-RTL verification inventory | PARTIAL | 1,192 named-counter hierarchy rows mapped (465 scenario, 727 activation); 65/67 activation families found; checker-to-instance qualification remains open, including absent/optimized assertions |
| Instrumented DUT simulation | PASS, 2 directed + 1 `riscv_dv` + 2 scalar-FP torture programs | Actual MediumBoomV3Config DUT with Verilator `--assert --coverage-toggle --coverage-user`; all five completed without assertion failures |
| Coverage witness replay | PARTIAL | All nine lowered DTLB cover goals reached; late-refill trace replayed with Icarus and target observed under stated full-fence/PTW assumptions |
| DUT mutation qualification | PARTIAL, PASS | An isolated one-expression ROB internal commit mutant is caught by `rob.rollback_no_commit` on the directed-0000 ELF; the same pinned baseline run passes |
| Formal BMC / cover / induction | PARTIAL / COUNTEREXAMPLE | Reset-constrained depth-2 BoomFrontend BMC passes. Both generated RenameBusyTable variants pass depth-6 BMC and 2-inductive proofs. DTLB cover reaches all nine goals; depth-5 `tlb.no_stale_refill_after_fence` failure replays. No whole-core or DTLB induction proof |

Successful Chisel elaboration and DUT simulation demonstrate that the staged
monitors compile and execute in this particular MediumBoomV3Config. The limited
name inventory and sampled simulation coverage do **not** establish complete
per-instance qualification or prove DUT behavior. In particular, two MapTable
families are inapplicable because this config instantiates `bypass = false`,
and `tlb.killed_walk_not_valid` lowers to a tautology. The DTLB late-refill
concern is still not fully qualified. The DTLB late-refill counterexample is
credible evidence that the current `no_stale_refill_after_fence` assertion
fails on an accepted walk returning after a full fence, but it needs owner
review before classifying it as an RTL bug or property-specification issue.
The staged source's refill path is unconditional on `s_wait_invalidate` and the
computed `invalidate_refill` is unused; a fresh bounded rerun plus Icarus replay
reproduced the step-5 failure. This increases source-level confidence in the
counterexample but does not resolve the upstream PTW protocol contract.
The BoomFrontend 2-step check is bounded subsystem evidence only; it does not
establish processor-wide correctness. The DTLB harness, assumptions, SBY
configuration, cover replay, and counterexample replay are bundled under
`artifacts_mediumboom/kapil_qualification/formal_dtlb/`. The full BoomFrontend
transitive source closure remains exploratory under
`/private/tmp/oss-cad-mediumboom.cXyTSM`. See the instrumented-run and DTLB
formal READMEs for scope, assumptions, coverage counts, RTL hashes, and gaps.
Exact hierarchy paths for the 1,192 emitted named activation/scenario counters
from the merged three-run Verilator database are listed in
`artifacts_mediumboom/kapil_qualification/generated_rtl_instance_inventory.json`.
This maps emitted counters only; it does not prove every source assertion
survived lowering or every replica was formally qualified.
Depth-6 assertion-only BMC results for both integer and floating-point
RenameBusyTable variants, with translated RTL hashes and complete Yosys logs,
are bundled under `artifacts_mediumboom/kapil_qualification/formal_busytable/`.
A separate scalar-FP DUT smoke now passes two RV64UF programs and reaches 63/64
instances for each FP BusyTable activation family; one instance remains
unreached. Results and source/log hashes are in
`artifacts_mediumboom/kapil_qualification/fp_smoke/README.md`.
The unhit counter is the unsuffixed `r = 0` monitor; the free-list source masks
preg 0. This is a plausible structural explanation, not an approved retirement
or proof of unreachable activation. It remains unhit pending owner review.
Across the five programs, merged full-design toggle activity is 221,166 / 300,539
(73.59%), 582 / 1,192 named user-cover counters hit, and 49 / 67 activation
families hit. The per-instance aggregate inventory and measured summary JSON
are checked in beside the qualification README; the raw merged database is
retained outside the repository due to its size.
The one successful ROB mutation pair is documented with both logs and source
hashes under `artifacts_mediumboom/kapil_qualification/mutation_rob_rollback/`;
it qualifies only that checker/stimulus pair.

The targeted test command was:

```sh
python3 -m pytest tests/adapters/mediumboom/test_boom_last_mile.py \
  tests/adapters/mediumboom/test_boom.py \
  tests/integration/legacy_coverage_growth/test_enhancement.py \
  tests/integration/legacy_coverage_growth/test_experiment.py \
  tests/integration/legacy_coverage_growth/test_chia_dispatch.py -q
```

The local Python initially lacked pytest; test dependencies were installed under
`/tmp/boom-verification-test-deps` and that directory was added to `PYTHONPATH`.
No repository/runtime dependency configuration was changed.

The original syntax check used
`tree_sitter.Parser(Language(tree_sitter_scala.language()))` on each `.scala`
file staged by `instrument.py`. The later elaboration used the exact BOOM source
revision listed above. Regenerate the overlay and repeat qualification after
any monitor, source, parameter or dependency change.
