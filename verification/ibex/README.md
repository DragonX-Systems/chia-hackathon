# Ibex Coverage Qualification

This directory currently implements **local decoder formal qualification only**.
It does not yet run program CRV, directed assembly, whole-core coverage collection
or the combined experiment. See the [plan](../../docs/IBEX_OPEN_SOURCE_VERIFICATION_PLAN.md),
[scope](../../docs/IBEX_BOUNDED_COVERAGE_SPEC.md) and
[methodology and user guide](../../docs/IBEX_VERIFICATION_METHODOLOGY.md).

## Run the Implemented Qualification

Use Python 3.10+, SBY, Yosys with `read_slang`, `yosys-smtbmc`, and Z3 on PATH.
Provide an unchanged checkout of Ibex at
`e9f55342edbd27e9e17a0e41b1c95a81abb5eac8`.

```bash
python verification/ibex/qualify_formal.py --ibex-root /path/to/ibex --bin all --simulate
```

Or select one ID from `formal/decoder_bins.json`:

```bash
python verification/ibex/qualify_formal.py --ibex-root /path/to/ibex --bin decode.memory_branch
```

Results go to a unique `artifacts/ibex/formal-*` directory containing snapshots,
source/predicate hashes, native SBY logs and normalized results. A prove task
asserts the negation of the requested bin. A proof counterexample is expected
for a reachable bin; cover success is expected for positive controls. Bounded
cover failure alone is not an unreachability proof.

`--simulate` additionally requires Verilator and a C++ toolchain. It checks five
known decoder inputs and illegal-input gating with the same monitor/wrapper.
Build staging uses `/tmp` because Verilator's generated Makefiles do not support
build directories containing spaces. The resulting logs are preserved in the
run directory. This is a decoder-vector smoke test, not program CRV.

The completed initial run reached all five singleton controls, proved all ten
pair crosses unreachable locally, and passed the Verilator vector checks.
See the [qualification report](../../docs/IBEX_QUALIFICATION_STATUS.md).

The shared monitor has five singleton controls and ten pairwise control crosses.
No difficulty/unreachable labels determine their results. The wrapper instantiates
the actual upstream decoder and models unrestricted data inputs under fixed
parameters. Project properties remain enabled; upstream assertion macros are
disabled by `SYNTHESIS` and are not claimed as checked.

**These are local decoder results.** No full-core simulation probe has yet been
qualified. Output therefore explicitly sets `core_pruning_authorized=false`.
Full-core pruning requires matching sampling/wiring/configuration and evidence
validation. This prevents a local proof from being mistaken for a completed
coverage-growth experiment.

The adopted minimal plan reports this local formal evidence separately from
program coverage. It does not require a full-core decoder probe or automatic
pruning integration. Consolidating the pairwise checks into one mutual-exclusion
property and adding the selected illegal/decode-mapping checks remain planned;
the existing qualifier and historical results are unchanged.
