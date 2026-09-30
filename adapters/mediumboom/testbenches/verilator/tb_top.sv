`timescale 1ns / 1ps
// Simulation harness around ultraembedded riscv_tcm_wrapper (64KB TCM at 0).
// AXI ports are tied idle; programs must stay in 0x0000..0xFFFF.
module tb_top (
    input clk,
    input rst,
    input rst_cpu
);
    /* verilator lint_off PINMISSING */
    riscv_tcm_wrapper #(
        .BOOT_VECTOR(32'h0),
        .CORE_ID(0),
        .TCM_MEM_BASE(32'h0)
    ) dut (
        .clk_i(clk),
        .rst_i(rst),
        .rst_cpu_i(rst_cpu),
        .axi_i_awready_i(1'b1),
        .axi_i_wready_i(1'b1),
        .axi_i_bvalid_i(1'b0),
        .axi_i_bresp_i(2'b00),
        .axi_i_bid_i(4'b0),
        .axi_i_arready_i(1'b1),
        .axi_i_rvalid_i(1'b0),
        .axi_i_rdata_i(32'b0),
        .axi_i_rresp_i(2'b00),
        .axi_i_rid_i(4'b0),
        .axi_i_rlast_i(1'b0),
        .axi_t_awvalid_i(1'b0),
        .axi_t_awaddr_i(32'b0),
        .axi_t_awid_i(4'b0),
        .axi_t_awlen_i(8'b0),
        .axi_t_awburst_i(2'b0),
        .axi_t_wvalid_i(1'b0),
        .axi_t_wdata_i(32'b0),
        .axi_t_wstrb_i(4'b0),
        .axi_t_wlast_i(1'b0),
        .axi_t_bready_i(1'b1),
        .axi_t_arvalid_i(1'b0),
        .axi_t_araddr_i(32'b0),
        .axi_t_arid_i(4'b0),
        .axi_t_arlen_i(8'b0),
        .axi_t_arburst_i(2'b0),
        .axi_t_rready_i(1'b1),
        .intr_i(32'b0)
    );
    /* verilator lint_on PINMISSING */

    // Named covers the SystemVerilog Assertion (SVA) half of the frozen list
    // can bind to. Immediate covers so Verilator 5 records them as user bins.
    always @(posedge clk) begin
        if (!rst_cpu) begin
            core_not_reset: cover (!rst_cpu);
            fetch_accept: cover (dut.ifetch_accept_w);
            lsu_dport_rd: cover (dut.dport_rd_w);
            lsu_dport_wr: cover (|dut.dport_wr_w);
        end
    end

    export "DPI-C" function dpi_tcm_write;
    export "DPI-C" function dpi_tcm_read;

    function int dpi_tcm_write(input int addr, input byte data);
        dpi_tcm_write = dut.u_tcm.write(addr, data);
    endfunction

    function byte dpi_tcm_read(input int addr);
        dpi_tcm_read = dut.u_tcm.read(addr);
    endfunction
endmodule
