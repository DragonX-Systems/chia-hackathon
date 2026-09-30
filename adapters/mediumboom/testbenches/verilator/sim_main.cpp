// MediumBOOM adapter: load a raw binary into TCM, then run to tohost or timeout.
#include "Vtb_top.h"
#include "verilated.h"
#include "verilated_cov.h"
#include "svdpi.h"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <vector>

extern "C" int dpi_tcm_write(int addr, char data);
extern "C" char dpi_tcm_read(int addr);

static bool set_tcm_scope() {
    const char* names[] = {"TOP.tb_top", "tb_top", "TOP.tb_top.tb_top"};
    for (const char* name : names) {
        svScope scope = svGetScopeFromName(name);
        if (scope) {
            svSetScope(scope);
            return true;
        }
    }
    return false;
}

static void tick(Vtb_top* top) {
    top->clk = 0;
    top->eval();
    top->clk = 1;
    top->eval();
}

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    const char* bin_path = nullptr;
    const char* cov_path = "coverage.dat";
    uint64_t max_cycles = 20000;
    uint32_t tohost_addr = 0xF000;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "+bin") && i + 1 < argc) bin_path = argv[++i];
        else if (!strncmp(argv[i], "+bin=", 5)) bin_path = argv[i] + 5;
        else if (!strcmp(argv[i], "+cov") && i + 1 < argc) cov_path = argv[++i];
        else if (!strncmp(argv[i], "+cov=", 5)) cov_path = argv[i] + 5;
        else if (!strncmp(argv[i], "+max=", 5)) max_cycles = strtoull(argv[i] + 5, nullptr, 0);
        else if (!strncmp(argv[i], "+tohost=", 8)) tohost_addr = strtoul(argv[i] + 8, nullptr, 0);
    }

    auto* top = new Vtb_top;
    top->rst = 1;
    top->rst_cpu = 1;
    top->clk = 0;
    for (int i = 0; i < 8; i++) tick(top);
    if (!set_tcm_scope()) {
        std::fprintf(stderr, "svSetScope failed for tb_top\n");
        return 2;
    }

    if (bin_path) {
        std::ifstream in(bin_path, std::ios::binary);
        std::vector<unsigned char> bytes((std::istreambuf_iterator<char>(in)), {});
        for (size_t i = 0; i < bytes.size() && i < 65536; i++) {
            dpi_tcm_write((int)i, (char)bytes[i]);
        }
    }

    top->rst = 0;
    top->rst_cpu = 0;
    uint64_t cyc = 0;
    for (; cyc < max_cycles; cyc++) {
        tick(top);
        unsigned char lo = (unsigned char)dpi_tcm_read((int)tohost_addr);
        if (lo != 0) break;
    }

    VerilatedCov::write(cov_path);
    std::printf("cycles=%llu tohost=%u\n", (unsigned long long)cyc,
                (unsigned)(unsigned char)dpi_tcm_read((int)tohost_addr));
    top->final();
    delete top;
    return 0;
}
