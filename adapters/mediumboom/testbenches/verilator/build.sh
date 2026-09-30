#!/usr/bin/env bash
# Build the MediumBOOM adapter's Verilator testbench.
# Build Verilator coverage sim for the ultraembedded RV32IM TCM core.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
CORE="$ROOT/third_party/ultraembedded-riscv"
if [[ ! -f "$CORE/core/riscv/riscv_core.v" ]]; then
  echo "missing $CORE — run scripts/fetch_formal_duts.sh" >&2
  exit 1
fi
OUT="${1:-$HERE/obj_dir}"
mkdir -p "$OUT"
# Line + user-cover only. Toggle on 64KB TCM RAM explodes the C++ build.
verilator --cc --exe --build --coverage-line --coverage-user \
  --top-module tb_top \
  --public \
  --Mdir "$OUT" \
  -CFLAGS "-std=c++17 -O2" \
  -Wno-fatal -Wno-UNOPTFLAT -Wno-WIDTH -Wno-CASEINCOMPLETE -Wno-PINMISSING \
  -I"$CORE/core/riscv" \
  "$HERE/tb_top.sv" \
  "$HERE/sim_main.cpp" \
  "$CORE/top_tcm_wrapper/riscv_tcm_wrapper.v" \
  "$CORE/top_tcm_wrapper/tcm_mem.v" \
  "$CORE/top_tcm_wrapper/tcm_mem_ram.v" \
  "$CORE/top_tcm_wrapper/tcm_mem_pmem.v" \
  "$CORE/top_tcm_wrapper/dport_mux.v" \
  "$CORE/top_tcm_wrapper/dport_axi.v" \
  "$CORE/core/riscv/riscv_core.v" \
  "$CORE/core/riscv/riscv_fetch.v" \
  "$CORE/core/riscv/riscv_decode.v" \
  "$CORE/core/riscv/riscv_decoder.v" \
  "$CORE/core/riscv/riscv_exec.v" \
  "$CORE/core/riscv/riscv_alu.v" \
  "$CORE/core/riscv/riscv_lsu.v" \
  "$CORE/core/riscv/riscv_issue.v" \
  "$CORE/core/riscv/riscv_csr.v" \
  "$CORE/core/riscv/riscv_csr_regfile.v" \
  "$CORE/core/riscv/riscv_multiplier.v" \
  "$CORE/core/riscv/riscv_divider.v" \
  "$CORE/core/riscv/riscv_mmu.v" \
  "$CORE/core/riscv/riscv_pipe_ctrl.v" \
  "$CORE/core/riscv/riscv_regfile.v" \
  --timescale 1ns/1ps
echo "built $OUT/Vtb_top"
