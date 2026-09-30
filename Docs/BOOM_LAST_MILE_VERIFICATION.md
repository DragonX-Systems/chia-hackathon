# BOOM: implementation-grounded formal and last-mile coverage

## What changes

The previous 51-point experiment measures allocator behavior against a small
proxy model. It does not establish CPU verification closure. This package
defines a new semantic verification baseline: 67 assertion families, 66 scenario
families and separate activation coverage for every assertion family, with
exact Chisel predicates at eleven implementation scopes.

The [catalog](../verification/boom/catalog.json) is the machine-readable contract;
the [property index](../verification/boom/PROPERTY_INDEX.md) is the review view;
the [monitor snippets](../verification/boom/monitors/) contain executable source
definitions. The pinned overlay has now been elaborated and simulated on the
matching MediumBOOM configuration, with selected witness-replay, mutation, and
bounded-formal evidence. This is still partial qualification, not signoff or an
unbounded proof. This document also names the remaining gaps; property count is
not a signoff metric.

## Audit of the old artifact

| Finding | Why it invalidates last-mile claims | Disposition |
|---|---|---|
| `a`, `b`, `both` select arbitrary generated ports | Free wrapper inputs need not exercise CPU state | Keep only as historical smoke results |
| `dis_fire` and `exe_req` can be `cyc == 4/6` | The names imply DUT events that are never observed | No use in the new model |
| `contra` and `never` are deliberately false | They test the solver, not architecture | Excluded from semantic denominator |
| Cover `Unreached` retires points; SoC path even retires missing names | Finite depth cannot establish unreachability; missing instrumentation is not a proof | Removed in both BOOM formal paths |
| Tile/ChipTop timeout silently falls back to LSU | Same label has different scope and environment | Fallback now requires explicit opt-in |
| Assembly text plus PASS credits opcode and both branch directions | Instruction presence is not execution, retirement, direction, or concurrency | No simulation proxy credits in new model |
| Generic memory and zero-output logic stubs | Memory aliasing, latency and protocol behavior change | Prohibited for qualified results without reviewed abstraction/refinement |
| No source/configuration/monitor/assumption identity in proof closure | Results cannot safely transfer to another build | Exact identity required; no automatic exclusions |

Historical result files are preserved. Their hit and closure numbers are not
rerun, reinterpreted or carried forward. Existing smoke helpers remain usable,
but are explicitly separate from this new verification package.

## Reviewed DUT and scope

Reviewed source: BOOM
[`58ef2720eae13be26b3008c02b5a74ce29c61c44`](https://github.com/riscv-boom/riscv-boom/tree/58ef2720eae13be26b3008c02b5a74ce29c61c44),
`src/main/scala/v3`. The v3 `WithNMediumBooms` definition specifies two-wide
decode, 64 ROB entries, 80 integer/64 FP physical registers, 16 LDQ/16 STQ entries,
12 branch tags, 32 FTQ entries and two D-cache MSHRs. These are reviewed defaults,
not evidence of the parameters of an absent generated DUT. Match the actual
Chipyard configuration and dependency revisions before elaborating.

The overlay reads register state at the Chisel source level, before generated
names can change. All sizes come from the enclosing DUT parameters. Every source
file is hash locked, and preparation requires a clean checkout at the reviewed
Git revision. The source lock includes the shared LSU file containing both LSU
and forwarding-age classes. It does not modify functional assignments.

| Implemented scope | Checks emphasize | Rare scenarios emphasize |
|---|---|---|
| RenameFreeList | Reserved outputs, disjointness, stability, reclaim | Allocation/free/reallocation/recovery ABA, exhausted backing bitmap |
| RenameMapTable | Snapshot restore, RAW/WAW bypass, youngest writer | Same-bundle WAW plus branch snapshot, dual branch allocation |
| RenameBusyTable | Reallocation wins over old writeback | Same-preg allocation/writeback collision |
| IssueSlot | Outgoing compacted uop, poison retention, split stores | Poison + grant + movement, kill + grant, data-before-address |
| Rob | Valid older blockers, holes, exception ordering | Sparse partial rows, wrap/rollback, mini-exceptions, FP flags |
| LSU | Forwarding eligibility, replay, exception age, queue recovery | Multiple conflicts, partial overlap, repeated nacks, committed stores across flush |
| ForwardingAgeLogic | Independent circular-distance minimum | Tail zero, wrap-side competition |
| NBDTLB | Invalidation, killed walks, pending refill contract | SFENCE + accept/return, late fenced walk, superpage refill |
| D-cache | Kill filtering, SC failure, store bypass precedence | Probe + LR reservation, three store generations, MSHR backpressure |
| Frontend | Packet/FTQ atomicity, partial instruction discard, history recovery | Split instruction fault, SFENCE replay, RAS repair versus call |
| FetchTargetQueue | Redirect/enqueue priority, repair versus commit training | Pointer wrap, simultaneous enqueue/redirect, update backlog |

## Assertions that catch plausible silicon failures

### Register reclamation must include reserved destinations

`free_list` is not the complete free-register state. The free list preselects
destinations into registered `alloc_pregs` outputs. A slot can hold an unconsumed
reservation while the backing bitmap is empty. An assertion that simply requires
every offered preg to be set in `free_list` would be wrong.

The monitor checks reservation/backing disjointness, pairwise lane uniqueness,
hold stability, p0 exclusion, legal consumption, and next-cycle visibility of
returned bits. The ABA tracker starts when a branch is allocated while preg R is
in use; it then observes R returned by the ROB, R reallocated, and that same tag
mispredicting. It checks R is reclaimed on the next edge. It cancels on correct
resolution, tag reuse and other recovery, and does not cancel on the tracked
branch's b1 mispredict before its b2 recovery. This targets the actual leakage
hazard explained by the upstream [rename documentation](https://docs.boom-core.org/en/latest/sections/rename-stage.html).

The full-core composition must separately establish live tag ownership and legal
deallocation. A local harness may assume these only after that boundary is
verified. It must not assume the free-list result it is trying to prove.

### Recovery, bypass and store splitting have real priority rules

Map recovery samples the old branch snapshot and compares the following table
state. Normal remap winner checks are disabled at that edge, so they do not
contradict recovery. WAW stale-destination bypass uses the newly allocated older
lane destination; using the old map loses a physical register at later commit.
Integer x0 is checked separately; FP f0 is an ordinary architectural register.

Issue-slot checks observe `io.out_uop.iw_state` as well as registered state. The
outgoing uop may move in a collapsing queue while a replacement overwrites the
physical slot. A poisoned speculative grant must preserve that outgoing uop.
Real matching wakeup has priority over poison cancellation. A split store may
issue address-first or data-first; only the unissued half remains. Global kill
dominates replacement, and branch kill invalidates the outgoing state.

### Memory dependencies require byte masks, age and lifecycle

A forwarding source must be a live older dependency with physical address,
matching doubleword and complete byte coverage; AMOs and fences are not ordinary
forwarding sources. `mem_forward_valid` only selects a candidate. Store data may
still be unavailable in writeback. A D-cache response takes precedence over that
forwarding candidate, so a generic "forward valid implies writeback" is incorrect.

The selector checker independently minimizes positive circular distance from a
matching store index to the saved tail. It does not reuse the implementation's
duplicated match-vector priority encoder. Equality to tail means distance N, not
zero. Covers force competition and wrap, rather than merely asserting some store
forwarded. Partial overlap covers check an actual non-full byte intersection.

Per-LDQ-entry trackers record nack count and accepted retries, and finish only
at successful commit of that allocation. Invalid entries, redispatch, branch
kill, exception and commit clear the history. Counter saturation prevents wrap
from manufacturing a hit. This prevents an unrelated later allocation at the
same index from completing an old sequence. The surrounding cache contract must
still prevent a stale response from being misidentified as a new transaction.

Committed stores survive exception flush; speculative loads do not. Ordinary
stores reach memory only after commit, but AMOs have their own issue rule and
are expressly excluded from that assertion. Uncacheable wakeup requires ROB-head
authorization, LDQ head identity and no older store dependencies. Fence drain
requires `ordered`, not an arbitrary delay. Single-lane conflict yielding is a
one-cycle local check; it makes no unbounded memory-progress promise.

### ROB retirement is ordered, but commit masks need not be contiguous

Empty banks in a partially filled ROB row are legal. A younger bank can commit
over an empty older bank. It cannot pass an older **valid** noncommitting bank.
Checks distinguish internal commit from architectural commit of predicated uops.
Mini-exceptions for memory ordering and CSR replay redirect fetch but do not
assert the architectural exception interface. Covers target an older bank
committing while a younger exception waits, full/mispredict overlap, rollback
across row zero and partial-row completion at the last row.

### Translation, frontend and cache interactions

The PTW interface has outer request valid and an inner valid bit that a kill
clears. Request-ready alone is insufficient to claim a usable walk. Full SFENCE
and multi-hit recovery clear all TLB entries. Frontend SFENCE clears every stage;
saved half-instruction state cannot survive a redirect/clear. Fetch packet and
FTQ allocation must occur together under independent ready signals. Predictor
repair can legitimately update structures on a mispredict, so only **ordinary
commit training** is prohibited during repair.

At the D-cache boundary, killed load responses and nacks must be filtered even
though an internal transaction can outlive its originating speculative uop.
An SC failure cannot enable the store-write stage. A reservation can backpressure
a probe, so "every probe accepted immediately" would remove the interesting
case. Store-load bypass selects the newest of the three in-flight store stages.

## Source-level concern requiring qualification

In the reviewed [DTLB source](https://github.com/riscv-boom/riscv-boom/blob/58ef2720eae13be26b3008c02b5a74ce29c61c44/src/main/scala/v3/lsu/tlb.scala#L148),
`invalidate_refill` is declared for `s_request`, `s_wait_invalidate`, or concurrent
SFENCE. The reviewed file has no other reference to that value, and `Entry.insert`
sets the entry valid. A same-cycle full SFENCE clears entries later in assignment
priority; a delayed response after the fence deserves separate analysis.

`tlb.full_fence_late_refill` tracks that sequence and
`tlb.no_stale_refill_after_fence` checks that it cannot reinstall a matching
translation. **This is a contract-sensitive candidate, not a confirmed bug.**
The system PTW may independently cancel or revalidate a walk. Qualify with an
accepted walk to VPN V, change its PTE, perform full SFENCE, return the old walk,
then access V. Inspect both the PTW's ordering contract and observed translation.
If independent revalidation guarantees freshness, refine the property to check
freshness rather than forbidding that installation. Do not weaken it merely to
make a proof pass; record the contract and evidence.

## Last-mile scenario and stimulus strategy

Use dependency structure, resource pressure and protocol scheduling to reach
these bins. Assembly alone cannot guarantee a microarchitectural event. Credit
the monitor event and retain the waveform/trace that demonstrates its identity.

| Campaign | Required causal sequence / crosses | Productive engine |
|---|---|---|
| Register leak | Branch unresolved → older stale preg freed → same preg reallocated → branch recovery; cross near-exhaustion and dual rename | Local formal with legal tag ledger; core pressure program |
| Store forwarding | At least two older same-doubleword stores; cross youngest full/partial/disjoint masks, STQ wrap, data late, signed width | Exhaustive selector proof, LSU formal, delayed-address program |
| Replay completion | Same LDQ allocation: accepted request → nack → accepted retry → completion → commit; cross 1 versus 2+ nacks and queue wrap | Cache response scheduler + scoreboard |
| Ordering replay | Younger load executes before older alias store; cross forwarded/cache data, observed release, two failing loads, translation fault | LSU/ROB composition; litmus-style program |
| Recovery races | Branch/global exception versus response, nack, forwarding, full queues; distinguish killed and surviving committed stores | Local formal then full-core witness replay |
| Speculative wakeup | Load wakes dependent → cache miss → granted dependent moves slot → later real wakeup | Issue formal and L1-miss injection |
| Precise exception | Older busy instruction blocks younger fault → older retires → fault redirects; cross partial row and wrap | ROB formal and delayed-load test |
| Translation fence | Accepted walk → full/selective SFENCE → late walk; cross page/superpage, fault, kill and response timing | PTW contract harness; supervisor-mode tests |
| LR/SC coherence | LR reservation → probe/expiry/conflicting line → SC result; verify no write on failure | TileLink-aware cache formal and coherence test |
| Fetch recovery | Split 32-bit instruction → replay/fault/redirect; cross FTQ full and RAS repair | Frontend formal and boundary-address program |

Do not explode every dimension into an all-to-all Cartesian product. Require
pairwise bins where interactions are independent and targeted three-way crosses
where assignment priority or shared ownership can lose state. Freeze the exact
configuration-qualified bin list before a campaign; add new bins only in a new
model revision with an explicit delta report.

## Required additional obligations before whole-CPU signoff

These are specification targets, **not implemented monitors or excluded bins**:

| Obligation | Identity, sequence and assertion | Why the current checks are insufficient |
|---|---|---|
| End-to-end wrong-path retirement | Capture `(ROB index, dispatch epoch)` and branch ancestry; mark it killed; forbid later architectural commit for that epoch; cover a late return after index reuse | Local kill checks do not prove composed retirement safety |
| Architectural store data | Model bytes at observed addresses with commit order; every retired load gets newest older legal bytes or allowed RVWMO value; cross sign extension and mixed sizes | Forward-source eligibility does not prove returned data value |
| MSHR ownership | Track `(source ID, allocation epoch, line)` through acquire/grant beats, replay and release; forbid duplicates and reuse before completion | Cache nack bins do not prove TileLink ownership |
| Refill/probe/dirty eviction | Same set/way sees last refill beat, probe and dirty victim writeback; data and metadata must match the surviving coherence state | No full coherence-state refinement is supplied |
| AMO linearization | Observe successful AMO old-value result and one atomic memory update at the coherence serialization point; cross nack and probe | Ordinary-store commit rule deliberately excludes AMOs |
| LR/SC system progress | Distinguish allowable SC failure from sustained lack of forward progress under an explicit fair interference model | Reservation/failure checks prove safety, not ISA eventuality |
| Precise trap payload | Oldest faulting epoch supplies cause, EPC, tval and privilege; no younger architectural register/store effect escapes | ROB mini-trap routing is only one part of precise exceptions |
| Interrupt boundary | Interrupt arrives during multi-bank commit, exception and CSR serialization; inspect the chosen retirement boundary and trap state | External interrupt/CSR module contracts are not modeled |
| FP and long-latency completion | Killed/reused destination epoch must reject late divide/FP result and flags; cover rounding-mode changes and exception flush | Busy-bit and fflags concurrency are not arithmetic correctness |
| Predictor/RAS epoch | Nested calls/returns overflow/wrap RAS, recover FTQ history, and train only surviving identity; repair updates remain legal | Local pointer/priority checks do not prove complete prediction history |
| Translation permissions | Check SUM/MXR/U/S, PMP/PMA, A/D bits, superpage alignment and scoped SFENCE against a translation reference model | Invalidation checks do not prove permission correctness |
| Liveness composition | Under stated bounded/fair cache, PTW and TileLink service contracts, a surviving oldest transaction eventually retires or traps | No safety cover is an eventual-progress proof |

These obligations remain visible on the closure dashboard as **unbound** until
implemented and qualified. They must never disappear because a probe is absent
or a configuration is difficult to elaborate.

## Formal partition contracts and anti-vacuity

| Partition | Environment that must be modeled | Assumptions that would invalidate useful results |
|---|---|---|
| Rename | Legal ROB returns, consumed reservations, tag lifecycle and b1/b2 relation | No register reuse; no simultaneous recovery and return |
| Issue | Arbiter grant legality and compaction lifecycle; real/speculative wakeup identity | No miss+grant; no movement; no wakeup collision |
| LSU/ROB | Dispatch index ledger, ordered commit, real queue capacities and exceptions | One transaction only; all responses immediate; no speculative requests |
| D-cache | Request/response source ledger, multibeat order, faithful data storage | Ready always true; probes disabled; reduced/aliased SRAM addresses |
| PTW/TLB | Accepted-walk ledger, privilege and fence semantics | No pending walk during fence; no faults; response forced fresh without proof |
| Frontend/FTQ | Fetch/predictor responses, backpressure, valid redirect indices | No split instructions; no predictor repair; permanently ready queues |

Reset must be established from an actual reset sequence, including every memory
or ghost state read by the property. The helper gates checks during reset and
requires previous active history; that gate does not replace a reset harness.
No `assume` is inserted into the DUT. In full-core runs, boundary contracts stay
as assertions. In cutpoint proofs, assumptions need separately discharged
upstream guarantees and a recorded composition boundary.

For every assertion family, cover its actual antecedent (`activation.<id>`).
Constant-trigger invariants need separate nonempty/pressure scenarios as well.
Cover reset exit and legal dispatch in the harness; cover each legal response
class and competing priority input before interpreting proofs. Monitor removal,
zero-instance matching and unreachable antecedents fail qualification.

Run targeted mutations listed in the catalog: change circular comparison at
wrap, drop a reclaim bit, invert collision priority, remove kill filtering,
allow SC-failure write, and preserve a split half through clear. Ensure each
mutation reaches its activation and fails the intended assertion. A passing
unmutated source alone does not qualify a checker.

## Evidence and closure accounting

Keep three ledgers: safety assertions; assertion activations; functional scenario
coverage. Do not add safety proof counts to covered scenario counts. Expand
family IDs into elaborated instance IDs before signoff: int/FP free lists,
separate issue queues, lanes, ROB banks and queue entries are distinct proof
obligations. Family debug printf lines intentionally cannot close all replicas.

Every result identifies source commit, elaborated configuration, RTL hash,
monitor hash, reset contract, assumptions, exact property instance, engine,
depth/bound, tool version, status and native artifact path. Keep cover witnesses
and replay them. The supplied evidence helper checks reviewed certificate
metadata; an authenticated native evidence collector is still required.

| Evidence | Interpretation | Coverage treatment |
|---|---|---|
| Qualified replayed cover witness | Scenario reached in this exact scope | Hit that instance only |
| Cover FAIL at depth D | No witness through D under this harness | Open; increase depth or change strategy |
| BMC PASS through D | No safety violation through D | Bounded-safe, not proven |
| Inductive safety proof + activation qualification | Invariant proven under listed contract | Safety ledger only |
| Unbounded proof of the scenario's negation | Local unreachable under listed contract | Candidate exclusion; review configuration and composition |
| Timeout, unknown, tool error, missing monitor | No conclusive evidence | Open/unbound |

Report `reached / all required scenario instances` independently of exclusions.
Show reviewed exclusions separately with reasons and proof IDs. Reusing evidence
across a changed parameter, scope, assumption, monitor or reset hash is forbidden.
Conflicting reachability/unreachability evidence for the same identity is an
error, not whichever result happened last. No local proof automatically retires
whole-core coverage.

## Execution order and current status

1. Compile/elaborate the source overlay in the matching Chipyard dependency tree;
   inspect RTL verification nodes and complete per-instance inventory.
2. Qualify forwarding selector, rename, busy table and issue-slot partitions.
   Use exhaustive tiny combinational domains and legal symbolic lifecycle ledgers.
3. Qualify LSU/ROB recovery and temporal trackers, preserving real queue sizes;
   add same-epoch reference scoreboards and fairness only for explicit liveness.
4. Exercise the pending-walk SFENCE concern, then cache and frontend partitions.
5. Replay partition witnesses in composition, run directed programs and protocol
   scheduling, then add the outstanding whole-core obligations above.

Completed locally: pinned-source overlay preparation, Scala parsing and
typechecking/elaboration, targeted Python tests, three instrumented DUT
programs, partial generated-RTL inventory, nine DTLB bounded covers, replay of
the DTLB late-refill witness and counterexample, one targeted ROB rollback
mutation caught by its intended checker, and a two-step BoomFrontend bounded
assertion run. See the [qualification record](../verification/boom/QUALIFICATION.md)
and [artifact bundle](../artifacts_mediumboom/kapil_qualification/README.md).

Still open: full per-instance hierarchy/replica audit, explanation and owner
review of the `tlb.no_stale_refill_after_fence` counterexample, broader
property-specific BMC/induction, complete DTLB/PTW/SFENCE contracts, and
whole-core composition/replay. Three assertion families are absent or
optimized away for this configuration. Bounded non-hits do not retire coverage,
and no measured B1/B2/B3 coverage-per-hour gain is claimed.
