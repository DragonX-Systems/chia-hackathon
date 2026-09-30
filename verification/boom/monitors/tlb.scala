  if (usingVM) {
    val fullFence = io.sfence.valid && !io.sfence.bits.rs1 && !io.sfence.bits.rs2
    boomAssert("tlb.full_fence_invalidates", RegNext(fullFence, false.B), !all_entries.map(_.valid.orR).reduce(_||_))
    boomAssert("tlb.multihit_recovers", RegNext(multipleHits.orR, false.B), !all_entries.map(_.valid.orR).reduce(_||_))
    boomAssert("tlb.killed_walk_not_valid", io.kill, !io.ptw.req.bits.valid)
    boomCover("tlb.sfence_ptw_accept", state === s_request && io.ptw.req.ready && io.sfence.valid && !io.kill)
    boomCover("tlb.sfence_refill_collision", do_refill && io.sfence.valid)
    boomCover("tlb.invalidated_walk_returns", state === s_wait_invalidate && do_refill)
    boomCover("tlb.superpage_refill", do_refill && io.ptw.resp.bits.level < (pgLevels-1).U)
    // A full fence invalidated an already accepted walk. Track its late return.
    // This is a specification check, not a claim that upstream satisfies it.
    val fencedWalk = RegInit(false.B)
    when (fullFence && (state === s_wait || (state === s_request && io.ptw.req.ready && !io.kill))) { fencedWalk := true.B }
    when (io.ptw.resp.valid) { fencedWalk := false.B }
    val late = fencedWalk && do_refill
    val oldTag = RegNext(r_refill_tag)
    boomAssert("tlb.no_stale_refill_after_fence", RegNext(late, false.B), !all_entries.map(_.hit(oldTag)).reduce(_||_))
    boomCover("tlb.full_fence_late_refill", late)
  }
