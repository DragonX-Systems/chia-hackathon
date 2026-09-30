"""Five riscv-torture profiles and explicit machine/user trap checks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import random
import struct

PROFILE_PATH = Path(__file__).parent / "stimulus/crv_profiles.json"
PREFIX = "torture.generator."
PROFILE_OVERRIDES = {
    "amo", "mul", "divider", "profile.muldiv_only",
    "profile.atomic_percent", "profile.fence_percent", "profile.privilege_tests",
}


def read_config(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "!")):
            continue
        key, value = line.replace("=", " ", 1).split(None, 1)
        if key in values:
            raise ValueError(f"duplicate config key: {key}")
        values[key] = value.strip()
    return values


def effective_config(profile: dict, profiles_path: Path = PROFILE_PATH) -> dict[str, str]:
    """Extend a supplied Torture profile without changing its source file."""
    base = read_config(profiles_path.parent / profile["config"])
    overrides = profile.get("overrides", {})
    if set(overrides) - PROFILE_OVERRIDES:
        raise ValueError("unknown profile override")
    for key, value in overrides.items():
        base[PREFIX + key] = str(value)
    return base


def load_profiles(path: Path = PROFILE_PATH) -> dict:
    profiles = json.loads(path.read_text())
    if set(profiles) != {f"crv-{i}" for i in range(1, 6)}:
        raise ValueError("profiles must define exactly crv-1 through crv-5")
    fingerprints = set()
    for node, profile in profiles.items():
        config = effective_config(profile, path)
        mix = {k.removeprefix(PREFIX + "mix."): int(v) for k, v in config.items()
               if k.startswith(PREFIX + "mix.")}
        if sum(mix.values()) != 100 or any(v < 0 for v in mix.values()):
            raise ValueError(f"invalid instruction mix: {node}")
        if set(mix) != {"xalu", "xmem", "xbranch", "fgen", "fpmem", "fax", "fdiv", "vec"}:
            raise ValueError(f"unsupported instruction mix: {node}")
        if any(mix[k] for k in ("fgen", "fpmem", "fax", "fdiv", "vec")):
            raise ValueError("these profiles require integer RV64IMA stimulus")
        atomic = int(config[PREFIX + "profile.atomic_percent"])
        fence = int(config[PREFIX + "profile.fence_percent"])
        traps = int(config[PREFIX + "profile.privilege_tests"])
        if min(atomic, fence) < 0 or atomic + fence > 100:
            raise ValueError(f"invalid memory submix: {node}")
        if atomic and config[PREFIX + "amo"] != "true":
            raise ValueError("atomic mix requires amo=true")
        if traps and traps < 4:
            raise ValueError("privilege_tests must be zero or at least four")
        if config[PREFIX + "profile.muldiv_only"] == "true" and not any(
            config[PREFIX + k] == "true" for k in ("mul", "divider")
        ):
            raise ValueError("muldiv_only requires multiplication or division")
        if int(config[PREFIX + "nseqs"]) <= 0 or int(config[PREFIX + "memsize"]) < 64:
            raise ValueError("invalid sequence count or memory size")
        fingerprint = json.dumps(config, sort_keys=True)
        if fingerprint in fingerprints:
            raise ValueError("each node needs a different generator configuration")
        fingerprints.add(fingerprint)
    return profiles


def add_privilege_checks(assembly: str, seed: int, count: int) -> str:
    """Run a checked M/U prelude, then restore the stock user-mode harness."""
    if not 0 <= seed < 1 << 63:
        raise ValueError("seed must fit a nonnegative Scala Long")
    if count == 0:
        return assembly
    if count < 4:
        raise ValueError("at least four traps are required")
    if assembly.count("RVTEST_CODE_BEGIN") != 1 or assembly.count("RVTEST_RV64U\n") != 1:
        raise ValueError("unexpected torture harness")
    rng = random.Random(seed)
    kinds = [2, 3, 8, 11] + rng.choices([2, 3, 8, 11], k=count - 4)
    rng.shuffle(kinds)
    lines = [".option push", ".option norvc", "csrr s4, mtvec",
             "la t0, crv_trap_handler", "csrw mtvec, t0", "li s6, -1", "li s8, 0"]
    for i, cause in enumerate(kinds):
        a, b = rng.getrandbits(64), rng.getrandbits(64)
        lines += [f"li t0, 0x{a:016x}", f"li t1, 0x{b:016x}", "csrw mscratch, t0",
                  "csrrw t2, mscratch, t1", "bne t2, t0, crv_fail",
                  "csrr t2, mscratch", "bne t2, t1, crv_fail",
                  f"li s6, {cause}", f"la s7, crv_resume_{i}", f"la s9, crv_fault_{i}",
                  f"li s5, {0 if cause == 8 else 3}", "addi s10, s8, 1"]
        if cause == 8:
            lines += ["li t0, 0x1800", "csrc mstatus, t0", "csrw mepc, s9", "mret"]
        lines += [f"crv_fault_{i}:", {2: ".word 0", 3: "ebreak", 8: "ecall", 11: "ecall"}[cause],
                  "j crv_fail", f"crv_resume_{i}:", "bne s8, s10, crv_fail"]
    lines += ["csrw mtvec, s4", "li t0, 0x1800", "csrc mstatus, t0",
              "la t0, crv_user_body", "csrw mepc, t0", "mret", ".balign 4", "crv_trap_handler:",
              "csrr t4, mcause", "bne t4, s6, crv_fail", "csrr t4, mepc", "bne t4, s9, crv_fail",
              "csrr t4, mstatus", "srli t4, t4, 11", "andi t4, t4, 3", "bne t4, s5, crv_fail",
              "addi s8, s8, 1", "li s6, -1", "csrw mepc, s7", "li t4, 0x1800",
              "csrs mstatus, t4", "mret", "crv_fail:", "li t0, 3", "la t1, tohost",
              "sd t0, 0(t1)", "crv_halt:", "j crv_halt", "crv_user_body:", ".option pop"]
    return assembly.replace("RVTEST_RV64U\n", "RVTEST_RV64M\n", 1).replace(
        "RVTEST_CODE_BEGIN", "RVTEST_CODE_BEGIN\n" + "\n".join(lines), 1)


def stimulus_sha256(elf: Path) -> str:
    """Hash loaded ELF sections, excluding symbols, filenames and comments."""
    data = elf.read_bytes()
    if data[:6] != b"\x7fELF\x02\x01":
        raise ValueError("expected little-endian ELF64")
    shoff = struct.unpack_from("<Q", data, 40)[0]
    size, count = struct.unpack_from("<HH", data, 58)
    digest = hashlib.sha256()
    sections = 0
    for index in range(count):
        _, kind, flags, address, offset, length, *_ = struct.unpack_from("<IIQQQQIIQQ", data, shoff + index * size)
        if flags & 2:
            digest.update(struct.pack("<QQQ", flags, address, length))
            if kind != 8:  # NOBITS has a zero initialized extent, no file bytes.
                digest.update(data[offset:offset + length])
            sections += 1
    if not sections:
        raise ValueError("ELF contains no allocated sections")
    return digest.hexdigest()


def signature_sha256(path: Path) -> str:
    lines = path.read_text().lower().split()
    if not lines or any(len(line) != 32 or any(c not in "0123456789abcdef" for c in line) for line in lines):
        raise ValueError(f"missing or invalid 128-bit signature: {path}")
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()
