// MediumBOOM adapter formal wrapper over riscv_soc irq_ctrl.
`include "irq_ctrl_defs.v"

module irq_cover (
    input        clk,
    input        rst,
    input        irq0,
    input        irq1,
    input        irq2,
    input        irq3,
    input        cfg_awvalid,
    input [31:0] cfg_awaddr,
    input        cfg_wvalid,
    input [31:0] cfg_wdata,
    input        cfg_arvalid,
    input [31:0] cfg_araddr
);
    wire cfg_awready, cfg_wready, cfg_bvalid, cfg_arready, cfg_rvalid, intr_o;
    wire [1:0] cfg_bresp, cfg_rresp;
    wire [31:0] cfg_rdata;

    irq_ctrl dut (
        .clk_i(clk),
        .rst_i(rst),
        .cfg_awvalid_i(cfg_awvalid),
        .cfg_awaddr_i(cfg_awaddr),
        .cfg_wvalid_i(cfg_wvalid),
        .cfg_wdata_i(cfg_wdata),
        .cfg_wstrb_i(4'hF),
        .cfg_bready_i(1'b1),
        .cfg_arvalid_i(cfg_arvalid),
        .cfg_araddr_i(cfg_araddr),
        .cfg_rready_i(1'b1),
        .interrupt0_i(irq0),
        .interrupt1_i(irq1),
        .interrupt2_i(irq2),
        .interrupt3_i(irq3),
        .cfg_awready_o(cfg_awready),
        .cfg_wready_o(cfg_wready),
        .cfg_bvalid_o(cfg_bvalid),
        .cfg_bresp_o(cfg_bresp),
        .cfg_arready_o(cfg_arready),
        .cfg_rvalid_o(cfg_rvalid),
        .cfg_rdata_o(cfg_rdata),
        .cfg_rresp_o(cfg_rresp),
        .intr_o(intr_o)
    );

    always @(posedge clk) begin
        if (!rst) begin
            irq0_host: cover (irq0 && intr_o);
            irq1_host: cover (irq1 && intr_o);
            // Real DUT quirk: MER is flopped; host can stay high one cycle after MER drops.
            irq_mer_hold: cover (intr_o && !dut.irq_mer_me_q);
            irq_source4: cover (dut.irq_ivr_vector_in_w == 32'd4);
            irq_bresp_err: cover (cfg_bresp != 2'b00);
            irq_source4_unreach: assert (dut.irq_ivr_vector_in_w != 32'd4);
            irq_bresp_err_unreach: assert (cfg_bresp == 2'b00);
        end
    end
endmodule
