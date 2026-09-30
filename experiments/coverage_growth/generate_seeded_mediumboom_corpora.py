"""Generate immutable, explicitly seeded MediumBOOM torture corpora."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BALANCED = ROOT / "adapters/mediumboom/stimulus/mediumboom.config"
DIRECTED = ROOT / "adapters/mediumboom/stimulus/mediumboom_directed.config"
COMPAT = ROOT / "adapters/mediumboom/stimulus/as_compat.h"
SEEDED_SOURCES = ROOT / "experiments/coverage_growth/riscv_torture_seed_src"
GENERATOR_BASE = "7c37f5ab454562cf076c8ff07b108b1b55f17250"
PATCHED_FILES = ("Rand.scala", "SeqMem.scala", "main.scala")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generator-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", default=",".join(str(20260925 + i * 100) for i in range(10)))
    parser.add_argument("--trial-offset", type=int, default=0,
                        help="start generated trial numbering after this many existing trials")
    parser.add_argument("--sbt-launch", type=Path, default=Path("/tmp/sbt-launch-1.8.2.jar"))
    parser.add_argument("--java", type=Path, default=Path("/opt/homebrew/opt/openjdk@17/bin/java"))
    parser.add_argument("--riscv-gcc", default=os.environ.get("RISCV64_GCC", "riscv64-elf-gcc"))
    args = parser.parse_args()

    generator = args.generator_root.resolve()
    out = args.output.resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"refusing to overwrite non-empty corpus directory: {out}")
    out.mkdir(parents=True, exist_ok=True)
    generator_commit = subprocess.check_output(
        ["git", "-C", str(generator), "rev-parse", "HEAD"], text=True).strip()
    if generator_commit != GENERATOR_BASE:
        raise SystemExit(f"generator base commit mismatch: {generator_commit} != {GENERATOR_BASE}")
    patch_digest = hashlib.sha256()
    for name in PATCHED_FILES:
        source = SEEDED_SOURCES / name
        patch_digest.update(name.encode() + b"\0" + source.read_bytes())
        shutil.copy2(source, generator / "generator/src/main/scala" / name)
    patch_sha256 = patch_digest.hexdigest()
    seeds = [int(value) for value in args.seeds.split(",") if value]
    if len(seeds) < 2 or len(set(seeds)) != len(seeds):
        raise SystemExit("provide at least two distinct replicate seeds")
    gcc = shutil.which(args.riscv_gcc) or (args.riscv_gcc if Path(args.riscv_gcc).is_file() else None)
    if not gcc:
        raise SystemExit(f"RISC-V GCC not found: {args.riscv_gcc}")
    for cfg, name in ((BALANCED, "mediumboom.config"), (DIRECTED, "mediumboom_directed.config")):
        shutil.copy2(cfg, generator / "config" / name)

    commands: list[str] = []
    plan: list[dict] = []
    for replicate, base_seed in enumerate(seeds, start=1 + args.trial_offset):
        trial = f"trial_{replicate:02d}"
        for lane, count, config, seed_offset in (
            ("crv", 8, "config/mediumboom.config", 0),
            ("directed", 2, "config/mediumboom_directed.config", 100),
        ):
            for index in range(count):
                job_id = f"{lane}_{index:02d}"
                seed = base_seed + seed_offset + index
                generator_name = f"open_toggle_{replicate:02d}_{lane}_{index:02d}"
                commands.append(
                    f"generator/run -C {config} -o {generator_name} --seed {seed}"
                )
                plan.append({"trial": trial, "job_id": job_id, "seed": seed,
                             "generator_output": generator_name, "config": config})

    env = os.environ.copy()
    env.update({"JAVA_HOME": str(args.java.resolve().parent.parent),
                "PATH": f"{args.java.resolve().parent}:{os.environ.get('PATH', '')}"})
    java_args = [str(args.java), "-Xmx2G", "-Xss8M",
                 "-Dsbt.ivy.home=/tmp/cy-torture-sbt18/ivy2",
                 "-Dsbt.global.base=/tmp/cy-torture-sbt18",
                 "-Dsbt.boot.directory=/tmp/cy-torture-sbt18/boot",
                 "-Dsbt.server.forcestart=false", "-Dsbt.server.autostart=false",
                 "-Dsbt.ci=true", "-jar", str(args.sbt_launch)]
    proc = subprocess.run(java_args + commands, cwd=generator, env=env,
                          capture_output=True, text=True, check=False)
    (out / "generator.log").write_text(proc.stdout + proc.stderr)
    if proc.returncode:
        raise SystemExit(f"seeded generator failed with exit {proc.returncode}; see {out / 'generator.log'}")

    trial_ids = [f"trial_{i:02d}" for i in range(1 + args.trial_offset,
                                                  len(seeds) + 1 + args.trial_offset)]
    corpus: dict[str, list[dict]] = {trial: [] for trial in trial_ids}
    for item in plan:
        stem = item["generator_output"]
        asm = generator / "output" / f"{stem}.S"
        stats = generator / "output" / f"{stem}.stats"
        if not asm.is_file() or not stats.is_file():
            raise SystemExit(f"generator omitted expected output: {stem}")
        destination = out / item["trial"]
        destination.mkdir(exist_ok=True)
        asm_out = destination / f"{item['job_id']}.S"
        stats_out = destination / f"{item['job_id']}.stats"
        elf_out = destination / f"{item['job_id']}.elf"
        shutil.copy2(asm, asm_out)
        shutil.copy2(stats, stats_out)
        compile_cmd = [gcc, "-nostdlib", "-nostartfiles", "-march=rv64ima_zicsr_zifencei",
                       "-mabi=lp64", "-include", str(COMPAT),
                       "-I" + str(generator / "env/p"), "-T", str(generator / "env/p/link.ld"),
                       "-o", str(elf_out), str(asm_out)]
        subprocess.run(compile_cmd, check=True, capture_output=True, text=True)
        corpus[item["trial"]].append({
            "job_id": item["job_id"], "seed": item["seed"], "config": item["config"],
            "assembly_sha256": sha256(asm_out), "elf_sha256": sha256(elf_out),
            "stats_sha256": sha256(stats_out),
        })

    manifest = {
        "status": "inputs_frozen_before_simulation",
        "generator_base_commit": generator_commit,
        "generator_patch_sha256": patch_sha256,
        "generator_patch_sources": list(PATCHED_FILES),
        "balanced_config_sha256": sha256(BALANCED),
        "directed_config_sha256": sha256(DIRECTED),
        "compiler": str(gcc),
        "replicate_count": len(seeds),
        "jobs_per_trial": 10,
        "static_roster_ratio": "8 CRV : 2 directed (4:1)",
        "replicates": [{"trial": trial, "base_seed": seeds[index],
                        "jobs": sorted(jobs, key=lambda job: job["job_id"])}
                       for index, (trial, jobs) in enumerate(sorted(corpus.items()))],
    }
    (out / "generator_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"replicates": len(seeds), "programs": len(plan),
                      "manifest": str(out / "generator_manifest.json")}, indent=2))


if __name__ == "__main__":
    main()
