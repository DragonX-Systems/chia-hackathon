#!/usr/bin/env bash
# Emit MediumBOOM Verilog here. Does NOT pull the ~30 GB chia-chisel-build image
# (GHCR is also private from this account). Uses Homebrew OpenJDK + mill and a
# 75 MB macOS firtool binary.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
TP="$ROOT/third_party"
CY="$TP/chipyard"
CIRCT="$TP/circt"
FIRTOOL_VER="${FIRTOOL_VER:-1.158.0}"
CONFIG="${MEDIUMBOOM_CONFIG:-MediumBoomV3Config}"
JOBS="${JOBS:-$(sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

# Chipyard still pins sbt 1.8.2. That combination crashes on this Mac unless:
#   1. OpenJDK 17 (not 21/26) — sbt 1.8.2's Scala parser cannot read JDK 21 classfiles
#   2. A short sbt home under /tmp — the default chipyard/.sbt path is 108 chars and
#      ipcsocket JNI overflows sockaddr_un (Trace/BPT trap, "detected buffer overflow")
#   3. sbt server off — Chipyard's default -Dsbt.server.forcestart=true forces that JNI bind
export JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@17}"
export PATH="$JAVA_HOME/bin:/opt/homebrew/bin:$PATH"
SBT_HOME="${SBT_HOME:-/tmp/cy-sbt}"
CY_SHORT="${CY_SHORT:-/tmp/cy}"
mkdir -p "$SBT_HOME/boot" "$SBT_HOME/ivy2" "$SBT_HOME/server"
# sbt 1.8.2 ipcsocket overflows Darwin sockaddr_un (~104 bytes). Work from a
# 7-char symlink so $(abspath ../..) in the Verilator Makefile stays short.
ln -sfn "$CY" "$CY_SHORT"

if ! command -v java >/dev/null; then
  echo "OpenJDK missing. brew install mill  (pulls openjdk)" >&2
  exit 1
fi

mkdir -p "$TP" "$CIRCT"
if [[ ! -x "$CIRCT/bin/firtool" ]]; then
  echo "==> firtool ${FIRTOOL_VER} (macOS arm64, ~75 MB)"
  tarball="$TP/firrtl-bin-macos-arm64.tar.gz"
  url="https://github.com/llvm/circt/releases/download/firtool-${FIRTOOL_VER}/firrtl-bin-macos-arm64.tar.gz"
  curl -fL --retry 3 -o "$tarball" "$url"
  tar -xzf "$tarball" -C "$CIRCT" --strip-components=1
  test -x "$CIRCT/bin/firtool"
  rm -f "$tarball"
fi
"$CIRCT/bin/firtool" --version | head -3

if [[ ! -d "$CY/.git" ]]; then
  echo "==> clone chipyard (shallow, default branch)"
  git clone --depth 1 --single-branch https://github.com/ucb-bar/chipyard.git "$CY"
fi

echo "==> chipyard $(git -C "$CY" rev-parse --short HEAD)  $(git -C "$CY" log -1 --oneline)"

# Submodules + skip conda/toolchain/FireSim/FireMarshal/CIRCT. We already have
# Java and firtool. Chipyard on macOS is unofficial; GNU sed helps the Makefiles.
if ! command -v gsed >/dev/null; then
  echo "==> brew install gnu-sed (Makefile SED=gsed)"
  brew install gnu-sed
fi
# Chipyard recipes use `|&` (bash 4+). macOS /bin/bash is 3.2.
if [[ ! -x /opt/homebrew/bin/bash ]]; then
  echo "==> brew install bash (Makefile SHELL, for |&)"
  brew install bash
fi

if [[ ! -f "$CY/generators/boom/src/main/scala/v3/common/config-mixins.scala" ]]; then
  echo "==> init generators (no RISC-V toolchain, no FireSim)"
  cd "$CY"
  if [[ -x ./build-setup.sh ]]; then
    ./build-setup.sh --skip-conda --skip-toolchain --skip-firesim --skip-marshal \
      --skip-ctags --skip-circt --skip-precompile --skip-clean || true
  fi
  if [[ ! -f generators/boom/src/main/scala/v3/common/config-mixins.scala ]]; then
    if [[ -x ./scripts/init-submodules-no-riscv-tools.sh ]]; then
      ./scripts/init-submodules-no-riscv-tools.sh
    else
      git submodule update --init --recursive --depth 1 generators/boom generators/rocket-chip \
        generators/testchipip tools/rocket-chip-blocks tools/cde 2>/dev/null || \
      git submodule update --init --recursive generators/boom
    fi
  fi
fi

export FIRTOOL="$CIRCT/bin/firtool"
export FIRTOOL_BIN="$FIRTOOL"
export PATH="$(dirname "$FIRTOOL"):$PATH"
export SED="${SED:-gsed}"
export JAVA_TOOL_OPTIONS="-Xmx${JAVA_HEAP_SIZE:-12G} -Djava.io.tmpdir=$CY_SHORT/.java_tmp"
mkdir -p "$CY_SHORT/.java_tmp"
# Override Chipyard's SBT_OPTS (which force-starts the Unix-domain server).
export SBT_OPTS="-Dsbt.ivy.home=$SBT_HOME/ivy2 -Dsbt.global.base=$SBT_HOME -Dsbt.boot.directory=$SBT_HOME/boot -Dsbt.color=always -Dsbt.supershell=false -Dsbt.server.forcestart=false -Dsbt.server.autostart=false -Dsbt.server.dir=$SBT_HOME/server -Dsbt.ci=true"

echo "==> make verilog CONFIG=${CONFIG}  (java=$("$JAVA_HOME/bin/java" -version 2>&1 | head -1))"
cd "$CY_SHORT/sims/verilator"
MAKE=$(command -v gmake || echo make)
set +e
$MAKE verilog CONFIG="$CONFIG" FIRTOOL_BIN="$FIRTOOL" SED="$SED" \
  SBT_OPTS="$SBT_OPTS" JAVA_HEAP_SIZE="${JAVA_HEAP_SIZE:-12G}" \
  SHELL=/opt/homebrew/bin/bash SED_INPLACE=-i -j"$JOBS"
rc=$?
set -e

GEN=$(find "$CY/sims/verilator/generated-src" -type d -name gen-collateral 2>/dev/null | head -1 || true)
if [[ -n "$GEN" ]]; then
  echo "OK generated Verilog: $GEN"
  ls "$GEN" | wc -l
  ls "$GEN" | rg -i 'lsu|boom|exu' | head
  mkdir -p "$ROOT/artifacts_mediumboom"
  python3 - <<PY
import json
from pathlib import Path
gen = Path("$GEN")
(Path("$ROOT") / "artifacts_mediumboom" / "gen_collateral.json").write_text(
    json.dumps({"gen_collateral": str(gen), "config": "$CONFIG", "n_files": len(list(gen.iterdir()))}, indent=2) + "\n"
)
PY
  exit 0
fi
echo "Verilog emit failed (make rc=$rc). See sims/verilator build logs." >&2
exit "${rc:-1}"
