#!/usr/bin/env bash
# Clone the local formal DUTs (BOOM sources + ultraembedded RISC-V core).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TP="$ROOT/third_party"
mkdir -p "$TP"
if [[ ! -d "$TP/riscv-boom/.git" ]]; then
  git clone --depth 1 --single-branch https://github.com/riscv-boom/riscv-boom.git "$TP/riscv-boom"
fi
if [[ ! -d "$TP/ultraembedded-riscv/.git" ]]; then
  git clone --depth 1 --single-branch https://github.com/ultraembedded/riscv.git "$TP/ultraembedded-riscv"
fi
echo "BOOM tree: $TP/riscv-boom"
echo "RV32IM core: $TP/ultraembedded-riscv"
echo "Parent SoC expected at: $ROOT/../../riscv_soc"
echo "sby: $(command -v sby || echo 'not on PATH — brew install sby')"
