  // Project monitors sample pre-edge state. No DUT inputs/state are driven here.
  // A reset edge and one active edge are required before temporal checks sample.
  private val boomPastValid = RegNext(!reset.asBool, false.B)
  private def boomCover(id: String, event: Bool): Unit = {
    val hit = boomPastValid && !reset.asBool && event
    chisel3.cover(hit).suggestName("boom_" + id.replace('.', '_'))
    if (sys.env.get("BOOM_MONITOR_PRINTF").contains("1")) {
      when (hit) { printf("BOOM_COVER " + id + "\n") }
    }
  }
  private def boomAssert(id: String, trigger: Bool, result: Bool): Unit = {
    boomCover("activation." + id, trigger)
    when (boomPastValid && !reset.asBool && trigger) {
      chisel3.assert(result, "BOOM_ASSERT " + id).suggestName("boom_" + id.replace('.', '_'))
    }
  }
