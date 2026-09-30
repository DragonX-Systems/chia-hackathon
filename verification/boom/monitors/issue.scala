  private val boomKilled = IsKilledByBranch(io.brupdate, slot_uop)
  private val boomPoison = io.ldspec_miss && (p1_poisoned || p2_poisoned)
  boomAssert("issue.flush_dominates", RegNext(io.kill, false.B), state === s_invalid)
  // out_uop moves to a different slot in the collapsing issue queue. Checking
  // only this slot's next registered state would miss the moved wrong-path uop.
  boomAssert("issue.kill_moving_uop", boomKilled || io.kill, io.out_uop.iw_state === s_invalid)
  boomAssert("issue.poison_retains_uop", io.grant && is_valid && boomPoison && !boomKilled && !io.kill,
    io.out_uop.iw_state === state)
  boomAssert("issue.split_retains_half", state === s_valid_2 && io.grant && (p1 ^ p2) && ppred &&
    !boomPoison && !boomKilled && !io.kill, io.out_uop.iw_state === s_valid_1)
  boomAssert("issue.split_sta_to_std", state === s_valid_2 && io.grant && p1 && !p2 && ppred &&
    !boomPoison && !boomKilled && !io.kill, io.out_uop.uopc === uopSTD && io.out_uop.lrs1_rtype === RT_X)
  boomAssert("issue.split_std_to_sta", state === s_valid_2 && io.grant && !p1 && p2 && ppred &&
    !boomPoison && !boomKilled && !io.kill, io.out_uop.lrs2_rtype === RT_X)
  boomAssert("issue.invalid_no_request", is_invalid || io.kill, !io.request)
  boomCover("issue.poison_grant_move", is_valid && io.grant && io.clear && boomPoison && !boomKilled && !io.kill)
  boomCover("issue.branch_kill_grant", is_valid && io.grant && boomKilled)
  boomCover("issue.flush_replace", io.kill && io.in_uop.valid && io.clear)
  boomCover("issue.store_address_first", state === s_valid_2 && io.grant && p1 && !p2 && !boomPoison && !boomKilled && !io.kill)
  boomCover("issue.store_data_first", state === s_valid_2 && io.grant && !p1 && p2 && !boomPoison && !boomKilled && !io.kill)
  for (operand <- 1 to 2) {
    val preg = if (operand == 1) next_uop.prs1 else next_uop.prs2
    val poisoned = if (operand == 1) next_p1_poisoned else next_p2_poisoned
    val ready = if (operand == 1) p1 else p2
    val realWake = io.wakeup_ports.map(p => p.valid && p.bits.pdst === preg).reduce(_||_)
    boomAssert("issue.real_wakeup_wins", RegNext(io.ldspec_miss && poisoned && realWake, false.B), ready)
    boomCover("issue.poison_real_wakeup", io.ldspec_miss && poisoned && realWake)
  }
