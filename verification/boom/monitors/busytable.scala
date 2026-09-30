  for (r <- 0 until numPregs) {
    val wb = (io.wb_pdsts zip io.wb_valids).map { case (p,v) => v && p === r.U }.reduce(_||_)
    val allocate = (io.ren_uops zip io.rebusy_reqs).map { case (u,v) => v && u.pdst === r.U }.reduce(_||_)
    boomAssert("busy.reallocate_wins", RegNext(allocate, false.B), busy_table(r))
    boomAssert("busy.writeback_clears", RegNext(wb && !allocate, false.B), !busy_table(r))
    boomCover("busy.writeback_reallocate_collision", wb && allocate)
  }
