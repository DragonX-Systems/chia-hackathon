#!/usr/bin/env bash
# Generate RISC-V torture programs (UCB riscv-torture) for MediumBOOM.
# Does not pull Google riscv-dv. Uses the Chipyard tools/torture submodule.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
CY="${CHIPYARD_ROOT:-$ROOT/third_party/chipyard}"
TORTURE="$CY/tools/torture"
CFG_SRC="${TORTURE_CONFIG:-$ROOT/adapters/mediumboom/stimulus/mediumboom.config}"
OUT="${TORTURE_OUT:-$ROOT/artifacts_mediumboom/riscv_torture}"
N="${TORTURE_N:-5}"

# The generator runs from a short symlinked Chipyard path below.  Keep a
# caller-supplied relative output path anchored at this repository.
if [[ "$OUT" != /* ]]; then
  OUT="$ROOT/$OUT"
fi

if [[ ! -d "$TORTURE/.git" && ! -f "$TORTURE/.git" ]]; then
  echo "riscv-torture missing at $TORTURE" >&2
  echo "Init: git -C $CY submodule update --init tools/torture" >&2
  exit 1
fi
if [[ ! -f "$TORTURE/env/p/riscv_test.h" ]]; then
  echo "==> init torture env submodule"
  git -C "$TORTURE" submodule update --init env
fi

# Scala 2.11 compiles on JDK 17 with sbt 1.8.2. The vendored sbt-launch.jar
# is 1.4.4 and dies on this Mac with JNA Unix-domain sockets.
pick_java() {
  for home in \
    /opt/homebrew/opt/openjdk@17 \
    /usr/local/opt/openjdk@17 \
    "${JAVA_HOME:-}"
  do
    if [[ -n "$home" && -x "$home/bin/java" ]]; then
      echo "$home"
      return 0
    fi
  done
  return 1
}

if ! JAVA_HOME="$(pick_java)"; then
  echo "==> brew install openjdk@17"
  brew install openjdk@17
  JAVA_HOME="/opt/homebrew/opt/openjdk@17"
fi
export JAVA_HOME
export PATH="$JAVA_HOME/bin:$PATH"
echo "==> java $("$JAVA_HOME/bin/java" -version 2>&1 | head -1)"

GCC="${RISCV64_GCC:-}"
if [[ -z "$GCC" ]]; then
  for c in riscv64-elf-gcc riscv64-unknown-elf-gcc; do
    if command -v "$c" >/dev/null; then GCC="$(command -v "$c")"; break; fi
  done
fi
if [[ -z "$GCC" ]]; then
  echo "Need riscv64-elf-gcc (brew install riscv64-elf-gcc) or set RISCV64_GCC" >&2
  exit 1
fi
OBJDUMP="${GCC%-gcc}-objdump"
echo "==> gcc $GCC"

if [[ ! -f "$CFG_SRC" ]]; then
  echo "riscv-torture config missing: $CFG_SRC" >&2
  exit 1
fi
CFG_NAME="$(basename "$CFG_SRC")"
cp "$CFG_SRC" "$TORTURE/config/$CFG_NAME"
mkdir -p "$OUT" "$TORTURE/output"
LAUNCH="${SBT_LAUNCH_JAR:-/tmp/sbt-launch-1.8.2.jar}"
if [[ ! -f "$LAUNCH" ]]; then
  echo "==> fetch sbt-launch 1.8.2"
  curl -fsSL -o "$LAUNCH" https://repo1.maven.org/maven2/org/scala-sbt/sbt-launch/1.8.2/sbt-launch-1.8.2.jar
fi
echo "sbt.version=1.8.2" > "$TORTURE/project/build.properties"
SBT_HOME="${SBT_HOME:-/tmp/cy-torture-sbt18}"
JNA_TMP="${JNA_TMP:-/tmp/jna-torture}"
mkdir -p "$SBT_HOME/boot" "$SBT_HOME/ivy2" "$SBT_HOME/server" "$JNA_TMP"
TORTURE_SHORT="${TORTURE_SHORT:-/tmp/cy-torture}"
ln -sfn "$TORTURE" "$TORTURE_SHORT"
SBT=( "$JAVA_HOME/bin/java" -Xmx2G -Xss8M
  -Dsbt.ivy.home="$SBT_HOME/ivy2"
  -Dsbt.global.base="$SBT_HOME"
  -Dsbt.boot.directory="$SBT_HOME/boot"
  -Dsbt.color=false
  -Dsbt.supershell=false
  -Dsbt.server.forcestart=false
  -Dsbt.server.autostart=false
  -Dsbt.server.dir="$SBT_HOME/server"
  -Dsbt.ci=true
  -Djna.tmpdir="$JNA_TMP"
  -jar "$LAUNCH"
)

cd "$TORTURE_SHORT"
# Balanced carpet (torture / riscv_dv) + mem-heavy directed suite.
declare -a JOBS=()
if (( N > 0 )); then
  for i in $(seq -w 0 $((N - 1))); do
    JOBS+=("mediumboom_$i:config/$CFG_NAME")
  done
fi
DIR_N="${TORTURE_DIR_N:-$N}"
cp "$ROOT/adapters/mediumboom/stimulus/mediumboom_directed.config" "$TORTURE/config/mediumboom_directed.config"
if (( DIR_N > 0 )); then
  for i in $(seq -w 0 $((DIR_N - 1))); do
    JOBS+=("mediumboom_dir_$i:config/mediumboom_directed.config")
  done
fi

for job in "${JOBS[@]}"; do
  name="${job%%:*}"
  cfg="${job#*:}"
  echo "==> generate $name.S ($cfg)"
  "${SBT[@]}" "generator/run -C $cfg -o $name"
done

compile_one() {
  local src="$1"
  local elf="$2"
  "$GCC" -nostdlib -nostartfiles \
    -march=rv64ima_zicsr_zifencei -mabi=lp64 \
    -include "$ROOT/adapters/mediumboom/stimulus/as_compat.h" \
    -I"$TORTURE/env/p" -T"$TORTURE/env/p/link.ld" \
    -o "$elf" "$src"
}

ok=0
fail=0
manifest='[]'
shopt -s nullglob
for job in "${JOBS[@]}"; do
  base="${job%%:*}"
  src="$TORTURE/output/$base.S"
  if [[ ! -f "$src" ]]; then
    echo "generator did not produce $src" >&2
    fail=$((fail + 1))
    continue
  fi
  cp "$src" "$OUT/$base.S"
  if [[ -f "$TORTURE/output/$base.stats" ]]; then
    cp "$TORTURE/output/$base.stats" "$OUT/$base.stats"
  fi
  elf="$OUT/$base.elf"
  echo "==> compile $base"
  if compile_one "$src" "$elf"; then
    if [[ -x "$OBJDUMP" ]]; then
      "$OBJDUMP" -d "$elf" > "$OUT/$base.dump" || true
    fi
    ok=$((ok + 1))
  else
    echo "compile failed: $base" >&2
    fail=$((fail + 1))
    rm -f "$elf"
  fi
done

python3 - <<PY
import json
from pathlib import Path
out = Path("$OUT")
files = sorted(p.name for p in out.iterdir())
stats = {}
for p in out.glob("*.stats"):
    txt = p.read_text(errors="replace")
    for line in txt.splitlines():
        if "instcnt" in line:
            stats[p.name] = line.strip()
            break
payload = {
    "generator": "ucb-bar/riscv-torture",
    "torture_root": "$TORTURE",
    "config": "$CFG_SRC",
    "directed_config": "adapters/mediumboom/stimulus/mediumboom_directed.config",
    "n_requested": int("$N") + int("$DIR_N"),
    "compiled_ok": int("$ok"),
    "compile_fail": int("$fail"),
    "files": files,
    "instcnt_lines": stats,
    "gcc": "$GCC",
    "java": """$("$JAVA_HOME/bin/java" -version 2>&1 | head -1)""",
}
(out / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n")
print(json.dumps({k: payload[k] for k in ("compiled_ok", "compile_fail", "files")}, indent=2))
PY

echo "OK suite: $OUT"
exit $(( fail > 0 && ok == 0 ? 1 : 0 ))
