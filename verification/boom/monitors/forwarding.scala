  boomAssert("forward.valid_iff_match", true.B, io.forwarding_val === io.addr_matches.orR)
  boomAssert("forward.selected_matches", io.forwarding_val, io.addr_matches(io.forwarding_idx))
  // Independent circular distance oracle: tail points PAST youngest store.
  // Minimum positive distance to tail wins, including wrap and tail==0.
  private val boomSelectedDistance = Mux(io.forwarding_idx < io.youngest_st_idx,
    io.youngest_st_idx - io.forwarding_idx,
    io.youngest_st_idx +& num_entries.U - io.forwarding_idx)
  for (i <- 0 until num_entries) {
    val distance = Mux(i.U < io.youngest_st_idx, io.youngest_st_idx - i.U,
      io.youngest_st_idx +& num_entries.U - i.U)
    boomAssert("forward.youngest_match", io.forwarding_val && io.addr_matches(i), boomSelectedDistance <= distance)
  }
  boomCover("forward.wrap_competition", io.forwarding_val && PopCount(io.addr_matches) >= 2.U && io.forwarding_idx >= io.youngest_st_idx)
  boomCover("forward.tail_zero", io.youngest_st_idx === 0.U && PopCount(io.addr_matches) >= 2.U)
