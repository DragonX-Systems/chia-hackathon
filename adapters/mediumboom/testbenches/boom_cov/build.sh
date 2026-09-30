#!/usr/bin/env bash
# Build the MediumBOOM coverage adapter testbench.
# Incremental Verilator: DUT library (generated LSU + frozen 4-port wrapper)
# is re-elaborated only when RTL/wrapper bytes change. Testbench C++
# (sim_main.cpp) is a separate make step that relinks against Vlsu_cov_tb__ALL.a.
#
#   bash build.sh          # dut if stale, then tb
#   bash build.sh dut      # Verilator frontend + DUT C++
#   bash build.sh tb       # sim_main.o + link only
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../../../.." && pwd)"
OUT="${OUT:-$HERE/obj_dir}"
MODE="${1:-all}"
mkdir -p "$OUT"
export PYTHONPATH="$ROOT/src:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
JOBS="${JOBS:-$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)}"

python3 - <<PY
from pathlib import Path
import sys
sys.path.insert(0, "$ROOT")
from adapters.mediumboom.boom_backend import discover_dut
from adapters.mediumboom.lsu_driver import write_cov_tb
dut = discover_dut()
changed = write_cov_tb(dut, Path("$OUT") / "lsu_cov_tb.sv")
print("wrapper", "updated" if changed else "unchanged", dut.module)
PY

python3 - <<PY
import hashlib
import sys
from pathlib import Path
sys.path.insert(0, "$ROOT")
from adapters.mediumboom.boom_backend import discover_dut, _child_sv
dut = discover_dut()
out = Path("$OUT")
svs = [dut.sv, *(_child_sv(dut))]
(out / "rtl.list").write_text("\n".join(str(p) for p in svs) + "\n" + str(dut.collateral) + "\n")
h = hashlib.sha256()
for p in [out / "lsu_cov_tb.sv", *svs]:
    h.update(p.name.encode())
    data = p.read_bytes() if p.is_file() else b""
    h.update(len(data).to_bytes(8, "little"))
    h.update(data)
(out / ".dut.hash").write_text(h.hexdigest() + "\n")
print("dut hash", h.hexdigest()[:16], "files", len(svs) + 1)
PY

INC="$(tail -n 1 "$OUT/rtl.list")"
STAMP="$(tr -d '\n' < "$OUT/.dut.hash")"

dut_stale=1
if [[ -f "$OUT/.dut.stamp" && -f "$OUT/Vlsu_cov_tb.mk" && -f "$OUT/Vlsu_cov_tb__ALL.a" ]]; then
  if [[ "$(tr -d '\n' < "$OUT/.dut.stamp")" == "$STAMP" ]]; then
    dut_stale=0
  fi
fi

verilate_dut() {
  echo "==> Verilator DUT (stamp miss or missing library)"
  svs=$(sed '$d' "$OUT/rtl.list" | tr '\n' ' ')
  # shellcheck disable=SC2086
  verilator --cc --exe \
    --coverage-line --coverage-user \
    --top-module lsu_cov_tb \
    --Mdir "$OUT" \
    -CFLAGS "-std=c++17 -O1" \
    -Wno-fatal -Wno-UNOPTFLAT -Wno-WIDTH -Wno-CASEINCOMPLETE -Wno-PINMISSING \
    -Wno-DECLFILENAME -Wno-UNUSED -Wno-UNDRIVEN -Wno-CMPCONST \
    -DPRINTF_COND=0 -DSTOP_COND=0 \
    -I"$INC" \
    "$OUT/lsu_cov_tb.sv" \
    $svs \
    "$HERE/sim_main.cpp" \
    --timescale 1ns/1ps
  printf '%s\n' "$STAMP" > "$OUT/.dut.stamp"
}

link_tb() {
  echo "==> make testbench (incremental C++)"
  make -C "$OUT" -f Vlsu_cov_tb.mk -j"$JOBS"
  echo "built $OUT/Vlsu_cov_tb"
}

case "$MODE" in
  dut)
    verilate_dut
    make -C "$OUT" -f Vlsu_cov_tb.mk -j"$JOBS"
    ;;
  tb)
    if [[ ! -f "$OUT/Vlsu_cov_tb.mk" ]]; then
      echo "missing DUT makefile — run: bash $HERE/build.sh dut" >&2
      exit 1
    fi
    link_tb
    ;;
  all)
    if [[ "$dut_stale" == 1 ]]; then
      verilate_dut
    else
      echo "==> DUT library up to date (${STAMP:0:16}...)"
    fi
    link_tb
    ;;
  *)
    echo "usage: $0 [all|dut|tb]" >&2
    exit 2
    ;;
esac
