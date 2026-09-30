  // Include the reserved allocation outputs: free_list alone is NOT all free state.
  private val boomReserved = io.alloc_pregs.map(p => UIntToOH(p.bits, numPregs) & Fill(numPregs, p.valid)).reduce(_|_)
  boomAssert("free.zero_reserved", true.B, !free_list(0) && !boomReserved(0))
  boomAssert("free.reservation_disjoint", true.B, !(free_list & boomReserved).orR)
  for (a <- 0 until plWidth; b <- a + 1 until plWidth) {
    boomAssert("free.unique_alloc", io.alloc_pregs(a).valid && io.alloc_pregs(b).valid,
      io.alloc_pregs(a).bits =/= io.alloc_pregs(b).bits)
  }
  for (w <- 0 until plWidth) {
    boomAssert("free.request_available", io.reqs(w), io.alloc_pregs(w).valid)
    val held = RegNext(io.alloc_pregs(w).valid && !io.reqs(w), false.B)
    val preg = RegNext(io.alloc_pregs(w).bits)
    boomAssert("free.reservation_stable", held,
      io.alloc_pregs(w).valid && io.alloc_pregs(w).bits === preg)
  }
  private val boomReturned = RegNext(dealloc_mask & ~1.U(numPregs.W))
  boomAssert("free.reclaim_visible", boomPastValid, (free_list & boomReturned) === boomReturned)
  boomCover("free.recover_and_return", io.brupdate.b2.mispredict && br_deallocs.orR && io.dealloc_pregs.map(_.valid).reduce(_||_))
  boomCover("free.exhausted_reservations", !free_list.orR && io.alloc_pregs.map(_.valid).reduce(_&&_))

  // ABA/leak sequence, tracked independently for each preg. A branch snapshots
  // while preg is allocated; preg is freed and reallocated under THAT branch;
  // recovery must return it. Branch-tag resolution/reuse cancels the tracker.
  for (r <- 1 until numPregs) {
    val phase = RegInit(0.U(2.W))
    val tag = Reg(UInt(brTagSz.W))
    val newBranch = io.ren_br_tags(0).valid
    val allocatedNow = (io.alloc_pregs zip io.reqs).map { case (a, q) => q && a.bits === r.U }.reduce(_||_)
    val returnedNow = io.dealloc_pregs.map(d => d.valid && d.bits === r.U).reduce(_||_)
    val recover = io.brupdate.b2.mispredict && io.brupdate.b2.uop.br_tag === tag
    val reuse = io.ren_br_tags.map(t => t.valid && t.bits === tag).reduce(_||_)
    val resolved = io.brupdate.b1.resolve_mask(tag)
    when (phase === 0.U && newBranch && !io.debug.freelist(r) && !returnedNow) {
      phase := 1.U; tag := io.ren_br_tags(0).bits
    }
    when (phase === 1.U && returnedNow) { phase := 2.U }
    when (phase === 2.U && allocatedNow) { phase := 3.U }
    // b1 mispredict precedes b2; do not cancel this branch at b1 mispredict.
    when (phase =/= 0.U && ((resolved && !io.brupdate.b1.mispredict_mask(tag)) || reuse ||
      io.brupdate.b2.mispredict || (io.brupdate.b1.mispredict_mask.orR && !io.brupdate.b1.mispredict_mask(tag)))) { phase := 0.U }
    val aba = phase === 3.U && recover && !reuse
    boomCover("free.aba_recovery", aba)
    boomAssert("free.aba_reclaimed", RegNext(aba, false.B), free_list(r))
  }
