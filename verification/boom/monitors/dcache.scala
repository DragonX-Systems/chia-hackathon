  boomAssert("dcache.failed_sc_no_write", RegNext(s2_valid(0) && s2_sc_fail, false.B), !s3_valid)
  boomAssert("dcache.reservation_blocks_probe", lrsc_valid, !tl_out.b.ready && !prober.io.req.valid)
  for (w <- 0 until memWidth) {
    boomAssert("dcache.killed_response_suppressed", IsKilledByBranch(io.lsu.brupdate, resp(w).bits.uop) ||
      (io.lsu.exception && resp(w).bits.uop.uses_ldq), !io.lsu.resp(w).valid)
    boomAssert("dcache.killed_nack_suppressed", IsKilledByBranch(io.lsu.brupdate, s2_req(w).uop) ||
      (io.lsu.exception && s2_req(w).uop.uses_ldq), !io.lsu.nack(w).valid)
    boomAssert("dcache.newest_store_bypass", s2_valid(w) && s3_bypass(w), s2_data_word(w) === s3_req.data)
    boomCover("dcache.three_store_bypass", s2_valid(w) && s3_bypass(w) && s4_bypass(w) && s5_bypass(w))
    boomCover("dcache.probe_nack", s2_valid(w) && s2_nack_hit(w) && s2_send_nack(w))
    boomCover("dcache.mshr_full_nack", s2_valid(w) && s2_nack_miss(w) && s2_send_nack(w))
  }
  boomCover("dcache.probe_reservation_collision", lrsc_valid && tl_out.b.valid)
  boomCover("dcache.sc_reservation_expired", s2_valid(0) && s2_sc && !lrsc_valid && s2_send_resp(0))
  boomCover("dcache.sc_wrong_line", s2_valid(0) && s2_sc && lrsc_valid && !s2_lrsc_addr_match(0) && s2_send_resp(0))
  boomCover("dcache.replay_under_probe", s2_valid(0) && s2_type === t_replay && tl_out.b.valid)
