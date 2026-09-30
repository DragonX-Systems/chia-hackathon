// Shared predicates for the local decoder qualification and future core probe.
module decoder_events (
  input logic valid_i,
  input logic illegal_i,
  input logic memory_i,
  input logic multiply_i,
  input logic divide_i,
  input logic branch_i,
  input logic jump_i,
  output logic [14:0] events_o
);
  logic [4:0] flags;
  assign flags = {jump_i, branch_i, divide_i, multiply_i, memory_i};
  always_comb begin
    events_o = '0;
    if (valid_i && !illegal_i) begin
      events_o[4:0] = flags;
      events_o[5]  = memory_i   && multiply_i;
      events_o[6]  = memory_i   && divide_i;
      events_o[7]  = memory_i   && branch_i;
      events_o[8]  = memory_i   && jump_i;
      events_o[9]  = multiply_i && divide_i;
      events_o[10] = multiply_i && branch_i;
      events_o[11] = multiply_i && jump_i;
      events_o[12] = divide_i   && branch_i;
      events_o[13] = divide_i   && jump_i;
      events_o[14] = branch_i   && jump_i;
    end
  end
endmodule
