  private val boomRedirectTail = RegNext(WrapInc(io.redirect.bits, num_entries))
  boomAssert("ftq.redirect_overrides_enqueue", RegNext(io.redirect.valid, false.B), enq_ptr === boomRedirectTail)
  boomAssert("ftq.no_commit_training_during_repair", bpd_update_mispredict || bpd_update_repair || io.redirect.valid || io.brupdate.b2.mispredict,
    !do_commit_update)
  boomCover("ftq.redirect_enqueue_collision", io.redirect.valid && io.enq.fire)
  boomCover("ftq.redirect_wrap", io.redirect.valid && io.redirect.bits === (num_entries-1).U)
  boomCover("ftq.repair_commit_backlog", bpd_update_repair && bpd_ptr =/= deq_ptr)
