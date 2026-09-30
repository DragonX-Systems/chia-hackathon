  boomAssert("rob.exception_serializes", exception_thrown, !io.commit.valids.asUInt.orR && io.flush.valid)
  boomAssert("rob.mini_exception_no_trap", exception_thrown && is_mini_exception, !io.com_xcpt.valid)
  boomAssert("rob.arch_exception_traps", exception_thrown && !is_mini_exception, io.com_xcpt.valid)
  boomAssert("rob.rollback_no_commit", io.commit.rollback, !io.commit.valids.asUInt.orR)
  boomAssert("rob.full_backpressure", full, !io.ready)
  for (w <- 0 until coreWidth) {
    boomAssert("rob.commit_ready", io.commit.valids(w), rob_head_vals(w) && can_commit(w) && !can_throw_exception(w))
    boomAssert("rob.arch_subset", io.commit.arch_valids(w), io.commit.valids(w))
    for (older <- 0 until w) {
      // A hole is legal. An older VALID stalled/excepting bank is not a hole.
      boomAssert("rob.no_commit_past_blocker", io.commit.valids(w) && rob_head_vals(older), io.commit.valids(older))
      boomCover("rob.commit_over_hole", !rob_head_vals(older) && io.commit.valids(w))
      boomCover("rob.older_commit_younger_exception", io.commit.valids(older) && can_throw_exception(w) && !exception_thrown)
    }
  }
  boomCover("rob.partial_row_at_wrap", r_partial_row && rob_head === (numRobRows-1).U && io.commit.valids.asUInt.orR)
  boomCover("rob.full_mispredict", full && io.brupdate.b2.mispredict)
  boomCover("rob.ordering_replay", exception_thrown && r_xcpt_uop.exc_cause === MINI_EXCEPTION_MEM_ORDERING)
  boomCover("rob.csr_replay", exception_thrown && r_xcpt_uop.exc_cause === MINI_EXCEPTION_CSR_REPLAY)
  boomCover("rob.exception_branch_race", io.lxcpt.valid && io.brupdate.b2.mispredict)
  boomCover("rob.rollback_wrap", io.commit.rollback && rob_tail === 0.U && rob_tail =/= rob_head)
  boomCover("rob.commit_predicated", (io.commit.valids.asUInt & ~io.commit.arch_valids.asUInt).orR)
  boomCover("rob.fp_flags_dual_commit", PopCount(fflags_val) > 1.U && fflags.map(_.orR).reduce(_||_))
