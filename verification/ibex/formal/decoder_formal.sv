module decoder_formal (
  input logic [31:0] instruction,
  input logic first_cycle,
  input logic branch_taken,
  input logic illegal_compressed
);
  logic illegal, memory_req, multiply, divide, branch, jump;
  logic [14:0] events;

  // Unrestricted inputs over-approximate valid core decode opportunities.
  ibex_decoder #(
    .BaseIsa(ibex_pkg::BaseIsaRV32I),
    .RV32E(0),
    .RV32M(ibex_pkg::RV32MFast),
    .RV32B(ibex_pkg::RV32BNone),
    .BranchTargetALU(0)
  ) dut (
    .clk_i(1'b0),
    .rst_ni(1'b1),
    .cheriot_enable_i(ibex_pkg::IbexMuBiOff),
    .instr_rdata_i(instruction),
    .instr_rdata_alu_i(instruction),
    .instr_first_cycle_i(first_cycle),
    .branch_taken_i(branch_taken),
    .illegal_c_insn_i(illegal_compressed),
    .illegal_insn_o(illegal),
    .data_req_o(memory_req),
    .mult_en_o(multiply),
    .div_en_o(divide),
    .branch_in_dec_o(branch),
    .jump_in_dec_o(jump)
  );

  decoder_events monitor (
    .valid_i(1'b1),
    .illegal_i(illegal),
    .memory_i(memory_req),
    .multiply_i(multiply),
    .divide_i(divide),
    .branch_i(branch),
    .jump_i(jump),
    .events_o(events)
  );

  always_comb begin
`ifdef CHECK_COVER
    cover(events[`BIN_INDEX]);
`else
    assert(!events[`BIN_INDEX]);
`endif
  end
endmodule
