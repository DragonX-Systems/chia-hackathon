# MediumBOOM coverage adapter image: tools, DUT library, and testbench.
# Rebuild of sim_main.cpp does not re-copy generated LSU RTL.
#
#   docker build -f adapters/mediumboom/testbenches/boom_cov/Dockerfile \
#     --build-arg CHIPYARD_GEN=/abs/path/to/gen-collateral \
#     -t chia-lsu-cov \
#     chia_hackathon
#
# CHIPYARD_GEN must be inside the build context or this stage is skipped locally.

syntax = docker/dockerfile:1

FROM debian:bookworm-slim AS tools
RUN apt-get update && apt-get install -y --no-install-recommends \
      verilator g++ make python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src

FROM tools AS dut
COPY adapters /src/adapters
COPY src /src/src
ARG CHIPYARD_GEN=third_party/chipyard/sims/verilator/generated-src/chipyard.harness.TestHarness.MediumBoomV3Config/gen-collateral
# When the path exists in context it is copied; otherwise the dut stage fails loudly.
COPY ${CHIPYARD_GEN} /rtl/gen-collateral
ENV CHIPYARD_GEN=/rtl/gen-collateral
ENV PYTHONPATH=/src/src:/src
ENV BOOM_BUILD_LSU_COV=1
RUN bash adapters/mediumboom/testbenches/boom_cov/build.sh dut

FROM dut AS tb
COPY adapters/mediumboom/testbenches/boom_cov/sim_main.cpp /src/adapters/mediumboom/testbenches/boom_cov/sim_main.cpp
RUN bash adapters/mediumboom/testbenches/boom_cov/build.sh tb
CMD ["/src/adapters/mediumboom/testbenches/boom_cov/obj_dir/Vlsu_cov_tb", "+stim=idle"]
