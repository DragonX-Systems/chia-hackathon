# BOOM v3 semantic verification

**67 assertion families, 66 scenario-cover families, and 67 separate assertion
activation-cover families**, derived from eleven BOOM v3 implementation classes.
These are source-defined monitors, **not qualified RTL or completed proofs**.

Start with the [design review and closure plan](../../Docs/BOOM_LAST_MILE_VERIFICATION.md)
and the [property index](PROPERTY_INDEX.md). Exact predicates are in
[monitors](monitors/); [catalog.json](catalog.json) records intent, source,
assumptions, stimulus strategy and mutation targets for every family.

The reviewed BOOM revision is
[`58ef2720eae13be26b3008c02b5a74ce29c61c44`](https://github.com/riscv-boom/riscv-boom/tree/58ef2720eae13be26b3008c02b5a74ce29c61c44).
This is a new reviewed source baseline. The historical simulator's BOOM revision
was not recorded here and must **not** be assumed identical.

## Prepare the source overlay

Use a clean checkout of that revision. Preparation requires only Python and Git:

```sh
python3 verification/boom/instrument.py \
  --boom-root third_party/riscv-boom \
  --out /tmp/boom-last-mile-overlay
```

The output contains only the reviewed source files with inserted monitors and a
hash manifest. It is **not a standalone BOOM checkout**. Apply those files to a
separate matching Chipyard BOOM checkout, then compile/elaborate there. The
command never edits the supplied upstream checkout, refuses source drift and
existing output directories, and labels its manifest `SOURCE_STAGED_ONLY`.

The monitors use the actual Chisel state and signals inside each module. There
is no guessed generated-Verilog port binding and no synthetic substitute DUT.
Enable `BOOM_MONITOR_PRINTF=1` **during elaboration** for `BOOM_COVER <family-id>`
simulation events. Those family-level lines are for debug; they are insufficient
for per-instance coverage signoff. Formal uses named Chisel assert/cover nodes.

## Qualification required before coverage credit

1. Record Chipyard/Rocket/BOOM/tool revisions and actual elaborated parameters.
   Compile all inserted monitors and preserve their assertions/covers in RTL.
2. Inventory every named assertion and cover instance after lowering. Match each
   to its family, module, int/FP variant, bank, entry and lane as applicable.
   Check that every expected module instance is instrumented. Zero matches fail.
3. Establish reset, then exercise each assertion's `activation.*` cover. A
   safety proof without qualified activation is not adequate signoff.
4. Replay cover witnesses against the same RTL, exercise the listed positive
   scenarios, and run targeted DUT mutations. A mutation must cause the intended
   assertion failure, rather than an unrelated protocol assertion.
5. Run per-property cover/BMC/prove jobs under reviewed local contracts. Keep
   native results, witnesses, depths, assumptions and hashes.

Do not pass `-DSYNTHESIS` or disable emitted verification constructs and then
claim these monitors ran. Use an assertion-preserving frontend such as
`read_slang` when required; do not replace memories/logic with zero-output stubs.

[evidence.py](evidence.py) keeps `BOUNDED_UNREACHED`, `BOUNDED_SAFE`, `PROVEN`,
`REACHED`, `UNKNOWN`, `TIMEOUT`, and tool errors separate. It accepts reviewed
per-instance certificates, checks identity and qualification fields, and never
automatically prunes core coverage. It is not a native-log collector or a
substitute for reviewing the attached evidence.

## Validation performed locally

The clean pinned source was fetched and the overlay was staged successfully.
All eleven staged Scala files passed syntax parsing, and 57 targeted Python
tests passed. See the [qualification record](QUALIFICATION.md) for exact scope.
Catalog/monitor consistency, source-drift refusal, source preservation, evidence
identity, vacuity gates, contradictory results and legacy bounded-nonhit
regressions are covered by `tests/adapters/mediumboom/test_boom_last_mile.py`.

An isolated matching Chipyard/BOOM checkout is now available and the monitors
have been compiled, elaborated, lowered into RTL, and simulated on three
MediumBoomV3Config programs with Verilator assertion/toggle/user coverage
enabled. A DTLB late-refill cover trace and bounded counterexample have both
been replayed; a targeted ROB rollback mutant is caught by its intended
checker. See [the current qualification record](QUALIFICATION.md), the
[instrumented-run evidence](../../artifacts_mediumboom/kapil_qualification/README.md),
the [DTLB formal bundle](../../artifacts_mediumboom/kapil_qualification/formal_dtlb/README.md),
and the [ROB mutation evidence](../../artifacts_mediumboom/kapil_qualification/mutation_rob_rollback/README.md).
Qualification remains partial: the hierarchy audit is incomplete, three
assertion families are absent or optimized away, there is no induction or
unbounded proof, and the DTLB counterexample requires BOOM-owner review.

The legacy `experiments/coverage_growth/run_legacy_experiment.py --backend boom`
still uses the historical 51-point
proxy model. It is not wired to this unqualified model, and its old percentages
must not be presented as coverage of these properties. Its unsafe cover-miss
retirement behavior has been removed, and automatic SoC-to-LSU fallback is off.
