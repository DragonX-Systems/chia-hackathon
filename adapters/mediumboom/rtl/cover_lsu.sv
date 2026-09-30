// MediumBOOM Load/Store Unit cover fragment (not generated DUT Verilog).
// Immediate cover/assert so Yosys 0.68 / SymbiYosys can parse them.
// Names follow riscv-boom v3/lsu/lsu.scala: LDQ/STQ, replay, STQ forward.
module cover_lsu (
    input  logic       clk,
    input  logic       rst,
    input  logic       valid,
    input  logic [1:0] cmd,   // 0=idle 1=load 2=store 3=amo
    input  logic       replay,
    input  logic       stq_match
);
    localparam logic HAS_AMO = 1'b0;

    logic busy;
    logic stq_forward;
    always @(posedge clk) begin
        if (rst) begin
            busy <= 1'b0;
            stq_forward <= 1'b0;
        end else begin
            if (valid && cmd != 2'd0) busy <= 1'b1;
            else if (replay) busy <= 1'b0;
            stq_forward <= valid && (cmd == 2'd1) && stq_match && busy;
        end
    end

    always @(posedge clk) begin
        if (!rst) begin
            load_fire: cover (valid && cmd == 2'd1);
            store_fire: cover (valid && cmd == 2'd2);
            replay_after_busy: cover (busy && replay);
            stq_forward_fire: cover (stq_forward);
            amo_without_a: cover (valid && cmd == 2'd3 && HAS_AMO);
            load_and_store: cover (valid && cmd == 2'd1 && cmd == 2'd2);
            amo_without_a_unreach: assert (!(valid && cmd == 2'd3 && HAS_AMO));
            load_and_store_unreach: assert (!(valid && cmd == 2'd1 && cmd == 2'd2));
        end
    end
endmodule
