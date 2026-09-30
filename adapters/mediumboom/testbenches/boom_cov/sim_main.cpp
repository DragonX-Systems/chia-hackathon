// MediumBOOM adapter: drive the generated LSU with dispatch/execute pulses.
// Writes Verilator line + user-cover dumps (observed coverage, not assembler credits).
#include "Vlsu_cov_tb.h"
#include "verilated.h"
#include "verilated_cov.h"
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

static void tick(Vlsu_cov_tb* top) {
    top->clk = 0;
    top->eval();
    top->clk = 1;
    top->eval();
}

static std::vector<int> parse_stim(const char* spec) {
    std::vector<int> out;
    if (!spec || !*spec) {
        return out;
    }
    std::string cur;
    for (const char* p = spec;; p++) {
        if (*p == ',' || *p == '\0') {
            if (cur == "load" || cur == "ld") {
                out.push_back(1);
            } else if (cur == "store" || cur == "st" || cur == "sd") {
                out.push_back(2);
            } else if (cur == "fence") {
                out.push_back(3);
            } else if (cur == "idle" || cur == "alu") {
                out.push_back(0);
            }
            cur.clear();
            if (*p == '\0') {
                break;
            }
        } else {
            cur.push_back(*p);
        }
    }
    return out;
}

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    const char* cov_path = "coverage.dat";
    const char* stim_spec = "idle,idle";
    uint64_t max_cycles = 256;
    for (int i = 1; i < argc; i++) {
        if (!strncmp(argv[i], "+cov=", 5)) {
            cov_path = argv[i] + 5;
        } else if (!strncmp(argv[i], "+stim=", 6)) {
            stim_spec = argv[i] + 6;
        } else if (!strncmp(argv[i], "+max=", 5)) {
            max_cycles = strtoull(argv[i] + 5, nullptr, 0);
        }
    }

    auto* top = new Vlsu_cov_tb;
    top->clk = 0;
    top->rst = 1;
    top->stim_kind = 0;
    top->stim_pulse = 0;
    for (int i = 0; i < 8; i++) {
        tick(top);
    }
    top->rst = 0;

    auto stims = parse_stim(stim_spec);
    if (stims.empty()) {
        stims = {0, 0};
    }
    uint64_t cyc = 0;
    size_t si = 0;
    while (cyc < max_cycles) {
        if (si < stims.size() && (cyc % 12) == 0) {
            top->stim_kind = stims[si++] & 3;
            top->stim_pulse = 1;
        } else {
            top->stim_pulse = 0;
        }
        tick(top);
        cyc++;
    }

    VerilatedCov::write(cov_path);
    std::printf("lsu_cov cycles=%llu stims=%zu cov=%s\n",
                (unsigned long long)cyc, stims.size(), cov_path);
    top->final();
    delete top;
    return 0;
}
