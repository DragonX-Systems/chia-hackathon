// MediumBOOM adapter formal wrapper over the ultraembedded RV32IM decoder.
`include "riscv_defs.v"

module decoder_cover (
    input        clk,
    input        rst,
    input        valid_i,
    input        fetch_fault_i,
    input        enable_muldiv_i,
    input [31:0] opcode_i
);
    wire invalid_o, exec_o, lsu_o, branch_o, mul_o, div_o, csr_o, rd_valid_o;

    riscv_decoder dec (
        .valid_i(valid_i),
        .fetch_fault_i(fetch_fault_i),
        .enable_muldiv_i(enable_muldiv_i),
        .opcode_i(opcode_i),
        .invalid_o(invalid_o),
        .exec_o(exec_o),
        .lsu_o(lsu_o),
        .branch_o(branch_o),
        .mul_o(mul_o),
        .div_o(div_o),
        .csr_o(csr_o),
        .rd_valid_o(rd_valid_o)
    );

    wire addi = valid_i && ((opcode_i & `INST_ADDI_MASK) == `INST_ADDI);
    wire lw   = valid_i && ((opcode_i & `INST_LW_MASK) == `INST_LW);
    wire beq  = valid_i && ((opcode_i & `INST_BEQ_MASK) == `INST_BEQ);
    wire mul  = valid_i && ((opcode_i & `INST_MUL_MASK) == `INST_MUL);
    wire fadd = valid_i && (opcode_i[6:0] == 7'b1010011);

    always @(posedge clk) begin
        if (!rst) begin
            addi_exec: cover (addi && exec_o);
            lw_lsu: cover (lw && lsu_o);
            beq_branch: cover (beq && branch_o);
            mul_when_m: cover (mul && enable_muldiv_i && mul_o);
            fadd_legal: cover (fadd && !invalid_o);
            mul_without_m: cover (mul && !enable_muldiv_i && mul_o);
            fadd_legal_unreach: assert (!(fadd && !invalid_o));
            mul_without_m_unreach: assert (!(mul && !enable_muldiv_i && mul_o));
        end
    end
endmodule
