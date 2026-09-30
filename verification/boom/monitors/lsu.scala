  // The branch recovery tail update loses to the global exception/reset path.
  private val boomBrRecover = RegNext(io.core.brupdate.b2.mispredict && !io.core.exception, false.B)
  private val boomLdTail = RegNext(io.core.brupdate.b2.uop.ldq_idx)
  private val boomStTail = RegNext(io.core.brupdate.b2.uop.stq_idx)
  boomAssert("lsu.branch_restores_tails", boomBrRecover, ldq_tail === boomLdTail && stq_tail === boomStTail)
  boomAssert("lsu.exception_clears_loads", RegNext(io.core.exception, false.B), !ldq.map(_.valid).reduce(_||_))
  boomAssert("lsu.clear_store_authorized", clear_store,
    stq(stq_head).valid && stq(stq_head).bits.committed &&
      Mux(stq(stq_head).bits.uop.is_fence, io.dmem.ordered, stq(stq_head).bits.succeeded))
  boomAssert("lsu.fence_waits_ordered", stq(stq_head).valid && stq(stq_head).bits.committed &&
    stq(stq_head).bits.uop.is_fence && !io.dmem.ordered, !clear_store && io.dmem.force_order)
  boomCover("lsu.flush_committed_store", io.core.exception && stq.map(s => s.valid && s.bits.committed && !s.bits.succeeded).reduce(_||_))
  boomCover("lsu.branch_exception_race", io.core.exception && io.core.brupdate.b2.mispredict)
  boomCover("lsu.queue_pressure_recovery", io.core.ldq_full.asUInt.orR && io.core.stq_full.asUInt.orR && io.core.brupdate.b2.mispredict)

  for (i <- 0 until numStqEntries) {
    val survives = stq(i).valid && stq(i).bits.committed && io.core.exception &&
      !(clear_store && stq_head === i.U) && !IsKilledByBranch(io.core.brupdate, stq(i).bits.uop)
    boomAssert("lsu.committed_store_survives", RegNext(survives, false.B), stq(i).valid)
    boomCover("lsu.store_data_before_address", stq(i).valid && stq(i).bits.data.valid && !stq(i).bits.addr.valid)
    boomCover("lsu.store_address_before_data", stq(i).valid && stq(i).bits.addr.valid && !stq(i).bits.data.valid)
  }
  for (w <- 0 until memWidth) {
    val s = stq(mem_forward_stq_idx(w))
    val loadMask = lcam_mask(w)
    val storeMask = GenByteMask(s.bits.addr.bits, s.bits.uop.mem_size)
    boomAssert("lsu.forward_byte_coverage", mem_forward_valid(w),
      s.valid && s.bits.addr.valid && !s.bits.addr_is_virtual && !s.bits.uop.is_fence && !s.bits.uop.is_amo &&
      (s.bits.addr.bits(corePAddrBits-1,3) === lcam_addr(w)(corePAddrBits-1,3)) &&
      (loadMask & storeMask) === loadMask && lcam_st_dep_mask(w)(mem_forward_stq_idx(w)))
    boomAssert("lsu.forward_kills_cache", mem_forward_valid(w) && RegNext(dmem_req_fire(w), false.B), io.dmem.s1_kill(w))
    // mem_forward_valid selects a source; it does NOT mean its data is ready.
    boomAssert("lsu.forward_waits_data", wb_forward_valid(w) && !dmem_resp_fired(w) &&
      !stq(wb_forward_stq_idx(w)).bits.data.valid, !io.core.exe(w).iresp.valid && !io.core.exe(w).fresp.valid)
    boomAssert("lsu.response_type_exclusive", true.B, !(io.core.exe(w).iresp.valid && io.core.exe(w).fresp.valid))
    boomAssert("lsu.ordinary_store_committed", dmem_req_fire(w) && !dmem_req(w).bits.is_hella &&
      dmem_req(w).bits.uop.uses_stq && !dmem_req(w).bits.uop.is_amo,
      stq(dmem_req(w).bits.uop.stq_idx).bits.committed)
    boomAssert("lsu.uncached_load_at_head", will_fire_load_wakeup(w) && ldq_wakeup_e.bits.addr_is_uncacheable,
      io.core.commit_load_at_rob_head && ldq_head === ldq_wakeup_idx && !ldq_wakeup_e.bits.st_dep_mask.orR)
    boomCover("lsu.forward_multiple_stores", mem_forward_valid(w) && PopCount(ldst_addr_matches(w)) >= 2.U)
    boomCover("lsu.forward_wrap", mem_forward_valid(w) && mem_forward_stq_idx(w) > lcam_uop(w).stq_idx)
    boomCover("lsu.forward_data_late", wb_forward_valid(w) && !dmem_resp_fired(w) && !stq(wb_forward_stq_idx(w)).bits.data.valid)
    boomCover("lsu.cache_forward_collision", dmem_resp_fired(w) && wb_forward_valid(w))
    val conflict = stq(forwarding_idx(w))
    val conflictMask = GenByteMask(conflict.bits.addr.bits, conflict.bits.uop.mem_size)
    boomCover("lsu.partial_overlap_sleep", do_ld_search(w) && ldst_addr_matches(w).asUInt.orR &&
      !conflict.bits.uop.is_fence && !conflict.bits.uop.is_amo &&
      (lcam_mask(w) & conflictMask).orR && (lcam_mask(w) & conflictMask) =/= lcam_mask(w) && io.dmem.s1_kill(w))
    boomCover("lsu.uncached_after_drain", will_fire_load_wakeup(w) && ldq_wakeup_e.bits.addr_is_uncacheable && dmem_req_fire(w))
    boomCover("lsu.store_nack_wrap", io.dmem.nack(w).valid && !io.dmem.nack(w).bits.is_hella &&
      io.dmem.nack(w).bits.uop.uses_stq && io.dmem.nack(w).bits.uop.stq_idx > stq_execute_head &&
      IsOlder(io.dmem.nack(w).bits.uop.stq_idx, stq_execute_head, stq_head))
  }

  for (i <- 0 until numLdqEntries) {
    val e = ldq(i)
    val killed = IsKilledByBranch(io.core.brupdate, e.bits.uop)
    val commit = (io.core.commit.valids zip io.core.commit.uops).map { case (v,u) =>
      v && u.uses_ldq && u.ldq_idx === i.U }.reduce(_||_)
    val enq = io.core.dis_uops.map(d => d.valid && d.bits.uses_ldq && !d.bits.exception && d.bits.ldq_idx === i.U).reduce(_||_)
    boomAssert("lsu.failed_load_no_commit", e.valid && e.bits.order_fail, !commit)
    boomAssert("lsu.nack_rearms", RegNext(nacking_loads(i), false.B), !e.bits.executed)
    boomAssert("lsu.branch_kills_load", RegNext(e.valid && killed, false.B), !e.valid)
    boomCover("lsu.observed_load_order_failure", e.valid && e.bits.observed && failed_loads(i))
    boomCover("lsu.forwarded_load_order_failure", e.valid && e.bits.forward_std_val && failed_loads(i))
    boomCover("lsu.killed_load_response_race", e.valid && killed && io.dmem.resp.map(r =>
      r.valid && r.bits.uop.uses_ldq && r.bits.uop.ldq_idx === i.U).reduce(_||_))

    // Per-entry lifecycle tracker; reset on every reuse, kill, exception or commit.
    // A response after index wrap cannot finish an older allocation's sequence.
    val nacks = RegInit(0.U(2.W))
    val retried = RegInit(false.B)
    val request = (0 until memWidth).map(w => dmem_req_fire(w) && !dmem_req(w).bits.is_hella &&
      dmem_req(w).bits.uop.uses_ldq && dmem_req(w).bits.uop.ldq_idx === i.U).reduce(_||_)
    when (e.valid && nacking_loads(i)) { nacks := Mux(nacks === 3.U, nacks, nacks + 1.U); retried := false.B }
    when (e.valid && nacks =/= 0.U && request && !nacking_loads(i)) { retried := true.B }
    boomCover("lsu.nack_retry_commit", e.valid && commit && e.bits.succeeded && nacks =/= 0.U && retried && !killed && !io.core.exception)
    boomCover("lsu.multi_nack_retry_commit", e.valid && commit && e.bits.succeeded && nacks >= 2.U && retried && !killed && !io.core.exception)
    when (!e.valid || enq || commit || killed || io.core.exception) { nacks := 0.U; retried := false.B }
  }
  boomCover("lsu.multiple_order_failures", PopCount(failed_loads) >= 2.U)
  boomCover("lsu.memory_fault_order_race", mem_xcpt_valid && ld_xcpt_valid)
  boomAssert("lsu.failure_selects_member", ld_xcpt_valid, failed_loads(ld_xcpt_uop.ldq_idx))
  for (i <- 0 until numLdqEntries) {
    boomAssert("lsu.oldest_failure_selected", ld_xcpt_valid && failed_loads(i),
      !IsOlder(i.U, ld_xcpt_uop.ldq_idx, ldq_head))
  }
  boomAssert("lsu.exception_oldest_wins", mem_xcpt_valid && ld_xcpt_valid,
    use_mem_xcpt === IsOlder(mem_xcpt_uop.rob_idx, ld_xcpt_uop.rob_idx, io.core.rob_head_idx))
  boomAssert("lsu.killed_exception_suppressed", io.core.exception || IsKilledByBranch(io.core.brupdate, r_xcpt.uop), !io.core.lxcpt.valid)
  // Finite local anti-starvation check, NOT an eventual-memory-response assumption.
  if (memWidth == 1) {
    boomAssert("lsu.conflict_yields_store", RegNext(ldst_addr_matches(0).asUInt.orR && !mem_forward_valid(0), false.B), block_load_wakeup)
    boomCover("lsu.conflict_store_drain", block_load_wakeup && will_fire_store_commit(0) && dmem_req_fire(0))
  }
  // Fence sequence follows the head until it drains; stq_head cannot advance
  // while this fence is blocked. Flush may leave this committed fence alive.
  private val boomFenceBlocked = RegInit(false.B)
  when (stq(stq_head).valid && stq(stq_head).bits.committed && stq(stq_head).bits.uop.is_fence && !io.dmem.ordered) { boomFenceBlocked := true.B }
  boomCover("lsu.fence_block_then_drain", boomFenceBlocked && clear_store && stq(stq_head).bits.uop.is_fence)
  when (clear_store || !stq(stq_head).valid) { boomFenceBlocked := false.B }
