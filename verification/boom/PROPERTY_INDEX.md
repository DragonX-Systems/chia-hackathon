# BOOM property index

Exact predicates and temporal trackers are linked below. All entries are source-defined;
qualification is partial: selected monitors have been elaborated, exercised in simulation,
replayed as formal witnesses, or mutation-tested. This is not an exhaustive per-instance
qualification or an unbounded proof. See [qualification](README.md).

Assertion activation covers are separate from functional scenario covers. Loop-replicated
assertions apply to every instance; scenario families are not a substitute for an elaborated
per-instance inventory. Catalog fields include local contracts, stimulus and mutation targets.

## RenameFreeList

| ID | Kind | Intent |
|---|---|---|
| [free.zero_reserved](monitors/freelist.scala#L3) | assert | Physical p0 cannot enter the backing free bitmap or a valid allocation reservation. |
| [free.reservation_disjoint](monitors/freelist.scala#L4) | assert | A reserved physical destination must not also be available for another reservation. |
| [free.unique_alloc](monitors/freelist.scala#L6) | assert | Two valid reservation lanes must never offer the same physical destination. |
| [free.request_available](monitors/freelist.scala#L10) | assert | Every consumed physical-register reservation must be valid; this is a rename-to-free-list integration contract. |
| [free.reservation_stable](monitors/freelist.scala#L13) | assert | An unconsumed valid reservation retains its destination and valid bit at the next edge. |
| [free.reclaim_visible](monitors/freelist.scala#L17) | assert | Every nonzero physical register returned by commit or branch recovery appears in the backing free bitmap on the next edge. |
| [free.recover_and_return](monitors/freelist.scala#L18) | cover | A nonempty branch reclaim list and a ROB deallocation arrive together. |
| [free.exhausted_reservations](monitors/freelist.scala#L19) | cover | Backing bitmap is exhausted while all reservation lanes still have valid destinations. |
| [free.aba_recovery](monitors/freelist.scala#L42) | cover | The same preg is allocated at branch creation, freed, reallocated, then reclaimed by that same branch; resolved/reused tags and other recovery cancel tracking. |
| [free.aba_reclaimed](monitors/freelist.scala#L43) | assert | After the tracked ABA branch misprediction, that preg is in the next backing free bitmap. |

## RenameMapTable

| ID | Kind | Intent |
|---|---|---|
| [map.x0_fixed](monitors/maptable.scala#L2) | assert | The integer logical x0 mapping remains p0; the FP table has no such restriction. |
| [map.restore_snapshot](monitors/maptable.scala#L8) | assert | Branch recovery restores the saved pre-edge snapshot, taking priority over current remap requests. |
| [map.recover_with_remap](monitors/maptable.scala#L9) | cover | Recovery and valid remapping contend on the same edge. |
| [map.raw_bypass](monitors/maptable.scala#L12) | assert | The immediately older rename lane bypasses its physical destination into a matching younger source. |
| [map.waw_stale_bypass](monitors/maptable.scala#L14) | assert | A younger same-bundle writer receives the older writer's newly allocated destination as stale_pdst. |
| [map.waw_branch_snapshot](monitors/maptable.scala#L16) | cover | Two lanes rename the same logical destination and the younger lane creates a branch snapshot. |
| [map.dual_branch](monitors/maptable.scala#L18) | cover | Two adjacent lanes allocate branch snapshots in one cycle. |
| [map.youngest_writer](monitors/maptable.scala#L27) | assert | For each logical register the youngest valid remapping wins next-state update, unless branch recovery overrides it. |

## RenameBusyTable

| ID | Kind | Intent |
|---|---|---|
| [busy.reallocate_wins](monitors/busytable.scala#L4) | assert | Allocation of a preg sets next busy state even when an old writeback targets that preg on the same edge. |
| [busy.writeback_clears](monitors/busytable.scala#L5) | assert | Writeback with no same-edge reallocation clears the preg busy bit next cycle. |
| [busy.writeback_reallocate_collision](monitors/busytable.scala#L6) | cover | Writeback and allocation target the same physical register on one edge. |

## IssueSlot

| ID | Kind | Intent |
|---|---|---|
| [issue.flush_dominates](monitors/issue.scala#L3) | assert | Global kill leaves the registered issue slot invalid even if replacement and clear also occur. |
| [issue.kill_moving_uop](monitors/issue.scala#L6) | assert | Branch kill or global flush invalidates the outgoing compacted uop, not just the stationary slot. |
| [issue.poison_retains_uop](monitors/issue.scala#L7) | assert | A granted uop poisoned by a failed speculative load retains outgoing state unless killed. |
| [issue.split_retains_half](monitors/issue.scala#L9) | assert | A store with exactly one ready operand retains the other half after a successful grant. |
| [issue.split_sta_to_std](monitors/issue.scala#L11) | assert | Issuing only the address changes the residual uop to store-data and removes the address source dependency. |
| [issue.split_std_to_sta](monitors/issue.scala#L13) | assert | Issuing only the data removes the residual address uop's data dependency. |
| [issue.invalid_no_request](monitors/issue.scala#L15) | assert | Invalid or globally killed slots cannot request issue. |
| [issue.poison_grant_move](monitors/issue.scala#L16) | cover | A poisoned speculative grant coincides with entry compaction; retention must travel with the uop. |
| [issue.branch_kill_grant](monitors/issue.scala#L17) | cover | A valid issue slot is granted while its branch mask is killed. |
| [issue.flush_replace](monitors/issue.scala#L18) | cover | Global flush, replacement input and compaction clear coincide. |
| [issue.store_address_first](monitors/issue.scala#L19) | cover | A split store issues its address while its data operand is still unavailable. |
| [issue.store_data_first](monitors/issue.scala#L20) | cover | A split store issues its data while its address operand is still unavailable. |
| [issue.real_wakeup_wins](monitors/issue.scala#L26) | assert | A real wakeup matching the next uop's source overrides poison-driven readiness cancellation next cycle. |
| [issue.poison_real_wakeup](monitors/issue.scala#L27) | cover | A real matching wakeup and speculative-load cancellation collide for the same source operand. |

## Rob

| ID | Kind | Intent |
|---|---|---|
| [rob.exception_serializes](monitors/rob.scala#L1) | assert | Throwing an exception generates a redirect and no commit valid on that edge. |
| [rob.mini_exception_no_trap](monitors/rob.scala#L2) | assert | Memory-ordering or CSR replay mini-exceptions redirect without asserting the architectural trap interface. |
| [rob.arch_exception_traps](monitors/rob.scala#L3) | assert | A non-mini exception at commit asserts the architectural trap interface. |
| [rob.rollback_no_commit](monitors/rob.scala#L4) | assert | Rollback never overlaps normal commit valids. |
| [rob.full_backpressure](monitors/rob.scala#L5) | assert | A full ROB cannot accept dispatch. |
| [rob.commit_ready](monitors/rob.scala#L7) | assert | Each committing bank is valid, ready and has no exception. |
| [rob.arch_subset](monitors/rob.scala#L8) | assert | Architectural commit is a subset of internal commit; predicated-out uops may only have internal commit. |
| [rob.no_commit_past_blocker](monitors/rob.scala#L11) | assert | A committing younger bank cannot pass an older valid bank that does not commit; invalid holes are allowed. |
| [rob.commit_over_hole](monitors/rob.scala#L12) | cover | A younger bank commits across an invalid older bank in the same row. |
| [rob.older_commit_younger_exception](monitors/rob.scala#L13) | cover | An older bank commits while a younger head-row exception is deferred. |
| [rob.partial_row_at_wrap](monitors/rob.scala#L16) | cover | A partially filled row at the last ROB row commits. |
| [rob.full_mispredict](monitors/rob.scala#L17) | cover | ROB full coincides with branch recovery. |
| [rob.ordering_replay](monitors/rob.scala#L18) | cover | A memory-ordering mini-exception reaches the ROB head and redirects. |
| [rob.csr_replay](monitors/rob.scala#L19) | cover | A CSR replay mini-exception reaches the ROB head and redirects. |
| [rob.exception_branch_race](monitors/rob.scala#L20) | cover | An LSU exception arrives in the cycle of b2 branch recovery. |
| [rob.rollback_wrap](monitors/rob.scala#L21) | cover | Rollback decrements tail row zero while head is elsewhere. |
| [rob.commit_predicated](monitors/rob.scala#L22) | cover | An internally committed predicated-out uop does not produce architectural commit. |
| [rob.fp_flags_dual_commit](monitors/rob.scala#L23) | cover | Multiple flag-producing FP commits coincide, with at least one nonzero exception flag set. |

## LSU

| ID | Kind | Intent |
|---|---|---|
| [lsu.branch_restores_tails](monitors/lsu.scala#L5) | assert | b2 branch recovery restores both queue tails next cycle unless a same-edge global exception overrides it. |
| [lsu.exception_clears_loads](monitors/lsu.scala#L6) | assert | A global exception invalidates every load queue entry next cycle. |
| [lsu.clear_store_authorized](monitors/lsu.scala#L7) | assert | Store removal requires a valid committed head and either successful completion or an ordered fence. |
| [lsu.fence_waits_ordered](monitors/lsu.scala#L10) | assert | A committed fence cannot drain until D-cache ordered is true and must request ordering while blocked. |
| [lsu.flush_committed_store](monitors/lsu.scala#L12) | cover | Global exception occurs with a committed store still waiting for completion. |
| [lsu.branch_exception_race](monitors/lsu.scala#L13) | cover | Global exception and b2 misprediction coincide, exercising queue-tail priority. |
| [lsu.queue_pressure_recovery](monitors/lsu.scala#L14) | cover | Load and store queue full indications are both present at branch recovery. |
| [lsu.committed_store_survives](monitors/lsu.scala#L19) | assert | A committed non-clearing store survives an exception; branch-killed stores are a separately checked upstream contract. |
| [lsu.store_data_before_address](monitors/lsu.scala#L20) | cover | A live store receives data while its address remains unavailable. |
| [lsu.store_address_before_data](monitors/lsu.scala#L21) | cover | A live store receives an address while its data remains unavailable. |
| [lsu.forward_byte_coverage](monitors/lsu.scala#L27) | assert | A forwarding source is an older live ordinary physical-address store with identical doubleword address and full load-byte coverage. |
| [lsu.forward_kills_cache](monitors/lsu.scala#L31) | assert | Selecting store forwarding kills the parallel previously accepted cache request. |
| [lsu.forward_waits_data](monitors/lsu.scala#L33) | assert | A selected store with unavailable data cannot write back a load through the forwarding path. |
| [lsu.response_type_exclusive](monitors/lsu.scala#L35) | assert | A lane cannot produce both integer and floating-point load responses. |
| [lsu.ordinary_store_committed](monitors/lsu.scala#L36) | assert | A non-AMO, non-Hella store request reaches the cache only after its queue entry is committed; AMOs use a separate rule. |
| [lsu.uncached_load_at_head](monitors/lsu.scala#L39) | assert | An uncacheable wakeup is the ROB-head load, LDQ head and has no older store dependencies. |
| [lsu.forward_multiple_stores](monitors/lsu.scala#L41) | cover | A load has at least two address-conflicting older stores and selects forwarding. |
| [lsu.forward_wrap](monitors/lsu.scala#L42) | cover | Forwarding selects a store numerically above the load's saved store-tail boundary. |
| [lsu.forward_data_late](monitors/lsu.scala#L43) | cover | A forwarding candidate reaches writeback before its store data is available, with no cache response taking priority. |
| [lsu.cache_forward_collision](monitors/lsu.scala#L44) | cover | Cache response and store forwarding compete for the same response lane. |
| [lsu.partial_overlap_sleep](monitors/lsu.scala#L47) | cover | The selected ordinary store overlaps some but not all requested load bytes; the accepted cache request is killed. |
| [lsu.uncached_after_drain](monitors/lsu.scala#L50) | cover | An uncacheable head load is actually accepted by the cache after older stores drain. |
| [lsu.store_nack_wrap](monitors/lsu.scala#L51) | cover | A store nack rolls the execute pointer backward in circular age despite a numerically larger index. |
| [lsu.failed_load_no_commit](monitors/lsu.scala#L62) | assert | A live load already marked order_fail cannot commit. |
| [lsu.nack_rearms](monitors/lsu.scala#L63) | assert | A load nack clears its executed bit on the next edge so it can retry. |
| [lsu.branch_kills_load](monitors/lsu.scala#L64) | assert | A live branch-killed LDQ entry is invalid next cycle. |
| [lsu.observed_load_order_failure](monitors/lsu.scala#L65) | cover | A cache-observed load becomes an ordering failure. |
| [lsu.forwarded_load_order_failure](monitors/lsu.scala#L66) | cover | A load that previously forwarded from a store is invalidated by a subsequent ordering discovery. |
| [lsu.killed_load_response_race](monitors/lsu.scala#L67) | cover | A returning cache load response coincides with a branch kill of the same live LDQ entry. |
| [lsu.nack_retry_commit](monitors/lsu.scala#L78) | cover | Within one LDQ allocation epoch: nack, later accepted retry, successful completion and commit. |
| [lsu.multi_nack_retry_commit](monitors/lsu.scala#L79) | cover | Within one LDQ allocation epoch: at least two nacks, later accepted retry, successful completion and commit. |
| [lsu.multiple_order_failures](monitors/lsu.scala#L82) | cover | At least two loads fail ordering on the same cycle and contend for the single exception port. |
| [lsu.memory_fault_order_race](monitors/lsu.scala#L83) | cover | A translation/access fault and memory-ordering failure contend for the single LSU exception port. |
| [lsu.failure_selects_member](monitors/lsu.scala#L84) | assert | The selected memory-order exception names one of the actually failing LDQ entries. |
| [lsu.oldest_failure_selected](monitors/lsu.scala#L86) | assert | No failing load is older in circular LDQ age than the selected ordering exception. |
| [lsu.exception_oldest_wins](monitors/lsu.scala#L89) | assert | When memory and ordering exceptions coexist, ROB age determines which cause/uop wins. |
| [lsu.killed_exception_suppressed](monitors/lsu.scala#L91) | assert | A global exception or branch kill suppresses a pending LSU exception report. |
| [lsu.conflict_yields_store](monitors/lsu.scala#L94) | assert | In the single-memory-lane configuration, a non-forwardable conflict blocks load wakeup on the next cycle to allow store progress. |
| [lsu.conflict_store_drain](monitors/lsu.scala#L95) | cover | A blocked load wakeup coincides with an actually accepted store drain. |
| [lsu.fence_block_then_drain](monitors/lsu.scala#L101) | cover | The same committed head fence first waits for ordering and later drains; tracker resets at removal. |

## ForwardingAgeLogic

| ID | Kind | Intent |
|---|---|---|
| [forward.valid_iff_match](monitors/forwarding.scala#L1) | assert | Forwarding selector valid is equivalent to a nonempty match mask. |
| [forward.selected_matches](monitors/forwarding.scala#L2) | assert | A valid forwarding index identifies an asserted match bit. |
| [forward.youngest_match](monitors/forwarding.scala#L11) | assert | The selected index minimizes positive circular distance to the saved store tail over all candidates. |
| [forward.wrap_competition](monitors/forwarding.scala#L13) | cover | Two or more stores compete and the youngest matching store is on the wrapped side of the saved tail. |
| [forward.tail_zero](monitors/forwarding.scala#L14) | cover | Two or more matching stores are arbitrated with saved store tail zero. |

## NBDTLB

| ID | Kind | Intent |
|---|---|---|
| [tlb.full_fence_invalidates](monitors/tlb.scala#L3) | assert | A full SFENCE invalidates every DTLB sector/superpage on the next edge, including a concurrent refill. |
| [tlb.multihit_recovers](monitors/tlb.scala#L4) | assert | Detected overlapping TLB hits invalidate the entries next cycle for retry. |
| [tlb.killed_walk_not_valid](monitors/tlb.scala#L5) | assert | A killed PTW request has its inner valid bit deasserted; outer Decoupled valid alone is not walk acceptance. |
| [tlb.sfence_ptw_accept](monitors/tlb.scala#L6) | cover | SFENCE collides with an accepted PTW request and transitions into invalidate-wait handling. |
| [tlb.sfence_refill_collision](monitors/tlb.scala#L7) | cover | SFENCE and translation refill arrive on the same cycle. |
| [tlb.invalidated_walk_returns](monitors/tlb.scala#L8) | cover | An outstanding walk returns in the invalidate-wait state. |
| [tlb.superpage_refill](monitors/tlb.scala#L9) | cover | A refill installs a translation above the base-page level. |
| [tlb.no_stale_refill_after_fence](monitors/tlb.scala#L17) | assert | Contract-sensitive candidate: a walk accepted before a full SFENCE must not reinstall a matching translation on its late return unless independently revalidated; upstream satisfaction is unproven. |
| [tlb.full_fence_late_refill](monitors/tlb.scala#L18) | cover | The same outstanding walk is fenced by a full SFENCE and later returns. |

## BoomNonBlockingDCacheModule

| ID | Kind | Intent |
|---|---|---|
| [dcache.failed_sc_no_write](monitors/dcache.scala#L1) | assert | A failed store-conditional does not create next-cycle store write enable. |
| [dcache.reservation_blocks_probe](monitors/dcache.scala#L2) | assert | A live LR reservation backpressures incoming probes and prevents probe-unit request acceptance. |
| [dcache.killed_response_suppressed](monitors/dcache.scala#L4) | assert | Branch-killed or exception-flushed load data does not escape the D-cache response filter. |
| [dcache.killed_nack_suppressed](monitors/dcache.scala#L6) | assert | Branch-killed or exception-flushed load nacks do not escape the D-cache nack filter. |
| [dcache.newest_store_bypass](monitors/dcache.scala#L8) | assert | The newest in-flight store bypass wins over older matching bypass stages for a valid request. |
| [dcache.three_store_bypass](monitors/dcache.scala#L9) | cover | A valid request matches all three store bypass generations for the same word. |
| [dcache.probe_nack](monitors/dcache.scala#L10) | cover | A live request is nacked due to a probe/metadata hazard. |
| [dcache.mshr_full_nack](monitors/dcache.scala#L11) | cover | A cache miss is nacked because its MSHR request cannot be accepted. |
| [dcache.probe_reservation_collision](monitors/dcache.scala#L13) | cover | A TileLink probe arrives while a live LR reservation prevents its acceptance. |
| [dcache.sc_reservation_expired](monitors/dcache.scala#L14) | cover | SC completes after its reservation has expired and must fail. |
| [dcache.sc_wrong_line](monitors/dcache.scala#L15) | cover | SC completes with a live reservation for a different line and must fail. |
| [dcache.replay_under_probe](monitors/dcache.scala#L16) | cover | An MSHR replay reaches cache stage two while an incoming probe is pending. |

## BoomFrontendModule

| ID | Kind | Intent |
|---|---|---|
| [fetch.packet_ftq_atomic](monitors/frontend.scala#L1) | assert | Fetch-buffer insertion and FTQ allocation occur atomically even with independent backpressure. |
| [fetch.clear_discards_half](monitors/frontend.scala#L2) | assert | F3 clear discards saved half-instruction state next cycle. |
| [fetch.sfence_clears_pipeline](monitors/frontend.scala#L3) | assert | SFENCE clears every fetch stage and starts the sfence path instead of replaying a fetch. |
| [fetch.redirect_restores_history](monitors/frontend.scala#L5) | assert | A CPU redirect restores supplied global history and cancels replay, unless SFENCE overrides it. |
| [fetch.halfword_redirect](monitors/frontend.scala#L7) | cover | A saved instruction halfword exists when a CPU redirect flush arrives. |
| [fetch.sfence_replay_collision](monitors/frontend.scala#L8) | cover | SFENCE collides with a replaying F2 fetch. |
| [fetch.packet_ftq_backpressure](monitors/frontend.scala#L9) | cover | A valid F4 packet sees exactly one of FTQ and fetch-buffer ready. |
| [fetch.ras_repair_call_collision](monitors/frontend.scala#L10) | cover | RAS repair and a consumed predicted call compete for the RAS write port. |
| [fetch.edge_instruction_fault](monitors/frontend.scala#L12) | cover | The second packet of a split instruction arrives with an instruction access/page fault. |

## FetchTargetQueue

| ID | Kind | Intent |
|---|---|---|
| [ftq.redirect_overrides_enqueue](monitors/ftq.scala#L2) | assert | FTQ redirect sets next enqueue pointer to redirect index plus one, even if normal enqueue fires. |
| [ftq.no_commit_training_during_repair](monitors/ftq.scala#L3) | assert | Mispredict/repair/redirect suppresses ordinary commit-based predictor training while recovery updates remain permitted. |
| [ftq.redirect_enqueue_collision](monitors/ftq.scala#L5) | cover | Redirect and normal FTQ enqueue handshake coincide. |
| [ftq.redirect_wrap](monitors/ftq.scala#L6) | cover | Redirect at the final FTQ index exercises enqueue-pointer wrap. |
| [ftq.repair_commit_backlog](monitors/ftq.scala#L7) | cover | Predictor repair runs while ordinary committed FTQ updates remain pending. |
