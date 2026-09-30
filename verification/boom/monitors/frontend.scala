  boomAssert("fetch.packet_ftq_atomic", true.B, fb.io.enq.fire === ftq.io.enq.fire)
  boomAssert("fetch.clear_discards_half", RegNext(f3_clear, false.B), !f3_prev_is_half)
  boomAssert("fetch.sfence_clears_pipeline", io.cpu.sfence.valid,
    fb.io.clear && f1_clear && f2_clear && f3_clear && f4_clear && !s0_valid && s0_is_sfence)
  boomAssert("fetch.redirect_restores_history", io.cpu.redirect_flush && !io.cpu.sfence.valid,
    s0_ghist.asUInt === io.cpu.redirect_ghist.asUInt && !s0_is_replay)
  boomCover("fetch.halfword_redirect", f3_prev_is_half && io.cpu.redirect_flush)
  boomCover("fetch.sfence_replay_collision", io.cpu.sfence.valid && s2_is_replay)
  boomCover("fetch.packet_ftq_backpressure", f4.io.deq.valid && (fb.io.enq.ready ^ ftq.io.enq.ready))
  boomCover("fetch.ras_repair_call_collision", ftq.io.ras_update && enableRasTopRepair.B &&
    f3.io.deq.valid && f4_ready && f3_fetch_bundle.cfi_is_call && f3_fetch_bundle.cfi_idx.valid)
  boomCover("fetch.edge_instruction_fault", f3.io.deq.fire && f3_prev_is_half &&
    (f3_imemresp.xcpt.pf.inst || f3_imemresp.xcpt.ae.inst))
