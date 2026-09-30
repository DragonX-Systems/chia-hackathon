  if (!float) {
    boomAssert("map.x0_fixed", true.B, map_table(0) === 0.U)
  }
  // Recovery must win over same-cycle remapping. Capture the old snapshot,
  // because the branch tag may also be reused by subsequent traffic.
  private val boomRecover = RegNext(io.brupdate.b2.mispredict, false.B)
  private val boomSnapshot = RegNext(br_snapshots(io.brupdate.b2.uop.br_tag).asUInt)
  boomAssert("map.restore_snapshot", boomRecover, map_table.asUInt === boomSnapshot)
  boomCover("map.recover_with_remap", io.brupdate.b2.mispredict && io.remap_reqs.map(_.valid).reduce(_||_))
  for (w <- 1 until plWidth) {
    val older = io.remap_reqs(w-1)
    boomAssert("map.raw_bypass", bypass.B && older.valid && older.ldst === io.map_reqs(w).lrs1,
      io.map_resps(w).prs1 === older.pdst)
    boomAssert("map.waw_stale_bypass", bypass.B && older.valid && older.ldst === io.map_reqs(w).ldst,
      io.map_resps(w).stale_pdst === older.pdst)
    boomCover("map.waw_branch_snapshot", older.valid && io.remap_reqs(w).valid &&
      older.ldst === io.remap_reqs(w).ldst && io.ren_br_tags(w).valid)
    boomCover("map.dual_branch", io.ren_br_tags(w-1).valid && io.ren_br_tags(w).valid)
  }
  // Independent per-logical-register winner check, including younger-wins WAW.
  for (r <- 0 until numLregs if float || r != 0) {
    for (w <- 0 until plWidth) {
      val wins = io.remap_reqs(w).valid && io.remap_reqs(w).ldst === r.U &&
        !(w+1 until plWidth).map(k => io.remap_reqs(k).valid && io.remap_reqs(k).ldst === r.U).foldLeft(false.B)(_||_) &&
        !io.brupdate.b2.mispredict
      val dst = RegNext(io.remap_reqs(w).pdst)
      boomAssert("map.youngest_writer", RegNext(wins, false.B), map_table(r) === dst)
    }
  }
