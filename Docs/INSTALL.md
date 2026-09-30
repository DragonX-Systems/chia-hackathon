# Install CHIA on this Mac

Official docs want Python **3.10.19**, then:

```bash
conda create -n chia_env python=3.10.19
conda activate chia_env
git clone https://github.com/ucb-bar/chia.git
pip install -e /path/to/chia
```

That last step **fails on this machine** because:

1. PyPI has no `ray==2.54.0` wheel for macOS (CHIA pins 2.54).
2. This conda env is **x86_64 under Rosetta** on an arm64 Mac; latest `cryptography` tried to build from source and broke.

What we did instead (already applied):

```bash
conda activate chia_env
# clone lives at: ../chia  (semi-total/chia)

pip install "cryptography==43.0.3"
pip install "ray[default]==2.49.2" "google-genai==1.64.0" \
  "mcp==1.27.1" "pydantic==2.12.4" "fastapi==0.121.0" \
  "pyyaml==6.0.3" "graphviz==0.21" "boto3>=1.28.0" \
  "google-cloud-compute>=1.19.0" "requests>=2.31.0"
pip install -e ../chia --no-deps
```

One local patch in that clone: `chia/base/ChiaFunction.py` `get()` only passes `_use_object_store` if this Ray version has it (2.49 does not).

## Check it works

```bash
conda activate chia_env
chia --help
python chia_hackathon/scripts/chia_hello.py
cd chia_hackathon && python -m pytest tests -q
```

## What still needs a Linux box

`chia up cluster.yaml` + Chipyard/Verilator **Docker workers** is the real cluster. The quickstart assumes Linux + SSH + Docker images (`ghcr.io/ucb-bar/chia-chisel-build`). Docker is installed here, but those images are Linux; use a Linux machine (or the hackathon cloud) for MediumBOOM / FireSim workers.

On this Mac you get: the `chia` CLI, `@ChiaFunction`, local Ray, and our coverage planner. You do not get a Chipyard worker until you `chia up` on Linux.

## SymbiYosys (formal retirement)

```bash
brew install sby    # pulls Yosys; use the Z3 already on PATH
bash chia_hackathon/scripts/fetch_formal_duts.sh
which sby yosys z3
cd chia_hackathon && python3 -m pytest tests/adapters/mediumboom/test_sby.py -q
```

Parent `riscv_soc` is used as-is (`irq_ctrl.v`). MediumBOOM Verilog is still a Chipyard/Linux job.
