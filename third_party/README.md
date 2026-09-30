# Formal DUT sources (not committed)

```bash
bash scripts/fetch_formal_duts.sh
```

| Tree | Why it is here |
|------|----------------|
| `riscv-boom` | Berkeley Out-of-Order Machine (BOOM) Chisel. We do **not** generate MediumBOOM Verilog on this Mac (needs Chipyard). `cover_lsu.sv` uses LSU names from `src/main/scala/v3/lsu/lsu.scala`. |
| `chipyard/tools/torture` | UCB RISC-V torture (not generated RTL). Generate MediumBOOM tests with `bash adapters/mediumboom/scripts/generate_riscv_torture.sh`. Google `riscv-dv` is not in this Chipyard tree. |
| `ultraembedded-riscv` | RV32IM core used by parent `riscv_soc` (the `core/` submodule was empty). `decoder_cover` instantiates `riscv_decoder.v`. |

`irq_cover` reads `riscv_soc/soc/irq_ctrl.v` from the parent folder (`RISCV_SOC_ROOT` override).
