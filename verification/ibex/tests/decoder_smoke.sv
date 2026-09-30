// Block-level monitor check, not an Ibex program regression.
module decoder_smoke;
  logic [31:0] instruction = '0;
  logic first_cycle = 1'b1;
  logic branch_taken = 1'b0;
  logic illegal_compressed = 1'b0;

  decoder_formal model (.*);

  task automatic expect_event(input logic [31:0] insn, input int index);
    instruction = insn;
    #1;
    if (model.events !== (15'b1 << index))
      $fatal(1, "Instruction %h: expected event %0d, got %h",
             insn, index, model.events);
  endtask

  initial begin
    expect_event(32'h00002083, 0); // lw x1, 0(x0)
    expect_event(32'h022081b3, 1); // mul x3, x1, x2
    expect_event(32'h0220c1b3, 2); // div x3, x1, x2
    expect_event(32'h00208463, 3); // beq x1, x2, +8
    expect_event(32'h000000ef, 4); // jal x1, 0
    illegal_compressed = 1'b1;
    #1;
    if (model.events !== '0)
      $fatal(1, "Illegal decode must not sample events");
    $display("PASS: five actual decoder events and illegal gating");
    $finish;
  end
endmodule
