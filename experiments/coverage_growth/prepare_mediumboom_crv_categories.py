"""Freeze five seeded torture profiles, Spike signatures and a ChiaLoop catalog."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "src")]
from adapters.mediumboom.crv_profiles import (PROFILE_PATH, PREFIX, add_privilege_checks,
    effective_config, load_profiles, signature_sha256, stimulus_sha256)
from chialoop.core.catalog import JobCatalog
from chialoop.core.config import CRV_CATEGORIES, CampaignSpec
from chialoop.core.identity import validate_unique_work
from chialoop.core.types import Engine, JobSpec, ResourceCapacity

OVERLAY = ROOT / "experiments/coverage_growth/riscv_torture_profile_src"
SUPPORTED_BASES = {"b2b66a66d51b360e0ae95017774d03377c78c574",
                   "7c37f5ab454562cf076c8ff07b108b1b55f17250"}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
        return digest.hexdigest()


def generate_sources(source: Path, out: Path, plans: list[dict], args) -> dict:
    """Use an isolated, pinned source snapshot; never patch the Chipyard checkout."""
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if commit not in SUPPORTED_BASES:
        raise ValueError(f"unsupported torture revision: {commit}")
    generator = out / "generator"
    generator.mkdir()
    archive = subprocess.check_output(["git", "-C", str(source), "archive", "HEAD"])
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        members = tar.getmembers()
        if any(m.name.startswith("/") or ".." in Path(m.name).parts for m in members):
            raise ValueError("unsafe generator archive")
        tar.extractall(generator)
    (generator / "output").mkdir(exist_ok=True)
    shutil.copytree(source / "env", generator / "env", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".git"))
    (generator / "project/build.properties").write_text("sbt.version=1.8.2\n")
    digest = hashlib.sha256()
    for patch in sorted(OVERLAY.glob("*.scala")):
        digest.update(patch.name.encode() + b"\0" + patch.read_bytes())
        shutil.copy2(patch, generator / "generator/src/main/scala" / patch.name)
    for path in (out / "configs").glob("*.config"):
        shutil.copy2(path, generator / "config" / path.name)
    commands = [f"generator/run -C config/{p['node']}.config -o {p['job_id']} --seed {p['seed']}" for p in plans]
    java = shutil.which(args.java)
    if not java:
        raise ValueError(f"Java not found: {args.java}")
    cmd = [java, "-Xmx2G", "-Xss8M", "-XX:ActiveProcessorCount=4", "-Dsbt.ci=true",
           "-Dsbt.server.autostart=false", "-Dsbt.supershell=false"]
    if args.sbt_cache:
        cache = args.sbt_cache.resolve()
        cmd += [f"-Dsbt.global.base={cache}", f"-Dsbt.boot.directory={cache}/boot", f"-Dsbt.ivy.home={cache}/ivy2"]
    cmd += ["-jar", str((args.sbt_launch or generator / "sbt-launch.jar").resolve()), *commands]
    with (out / "generator.log").open("w") as log:
        subprocess.run(cmd, cwd=generator, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=1800)
    for plan in plans:
        for extension in ("S", "stats"):
            shutil.copy2(generator / "output" / f"{plan['job_id']}.{extension}", out / f"{plan['job_id']}.{extension}")
    return {"generator_commit": commit, "overlay_sha256": digest.hexdigest()}


def reference_signature(spike: str, elf: Path, out: Path, timeout: int) -> str:
    signature = out.with_suffix(".signature")
    cmd = [spike, "--isa=rv64ima", f"+signature={signature}", str(elf)]
    with out.with_suffix(".spike.log").open("w") as log:
        subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=True)
    return signature_sha256(signature)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--chipyard", type=Path, default=Path(os.environ.get("CHIPYARD_ROOT", ROOT / "third_party/chipyard")))
    parser.add_argument("--simulator", type=Path, help="existing VM_COVERAGE=1 simulator")
    parser.add_argument("--riscv-gcc", default=os.environ.get("RISCV64_GCC", "riscv64-unknown-elf-gcc"))
    parser.add_argument("--spike", default=os.environ.get("SPIKE", "spike"))
    parser.add_argument("--java", default="java")
    parser.add_argument("--sbt-launch", type=Path)
    parser.add_argument("--sbt-cache", type=Path)
    parser.add_argument("--march")
    parser.add_argument("--profiles", type=Path, default=PROFILE_PATH)
    parser.add_argument("--per-node", type=int, default=4)
    parser.add_argument("--seed-base", type=int, help="reproducible base; default is fresh random 63-bit base")
    parser.add_argument("--loop-count", type=int, help="override body repetitions for all profiles (same generator)")
    parser.add_argument("--timeout", type=int, default=900, help="per-job RTL timeout in seconds")
    parser.add_argument("--reference-timeout", type=int, default=120)
    parser.add_argument("--directed-catalog", type=Path, help="include two existing directed jobs, also checked with Spike")
    args = parser.parse_args()
    if min(args.per_node, args.timeout, args.reference_timeout) < 1 or (args.loop_count is not None and args.loop_count < 1):
        parser.error("counts and timeouts must be positive")
    count = 5 * args.per_node
    base = args.seed_base if args.seed_base is not None else secrets.randbelow((1 << 63) - count)
    if base < 0 or base + count > 1 << 63:
        parser.error("seed range must fit nonnegative Scala Longs")
    profiles = load_profiles(args.profiles)
    for index, label in enumerate(CRV_CATEGORIES, 1):
        if profiles[f"crv-{index}"]["name"] != label:
            parser.error(f"crv-{index} label differs from ChiaLoop")
    out, study, chipyard = args.out.resolve(), args.study.resolve(), args.chipyard.resolve()
    simulator = (args.simulator or chipyard / "sims/verilator/simulator-chipyard.harness-MediumBoomV3Config").resolve()
    gcc, spike = shutil.which(args.riscv_gcc), shutil.which(args.spike)
    if not gcc or not spike:
        parser.error("RISC-V GCC and Spike must be available")
    gcc_major = int(subprocess.check_output([gcc, "-dumpversion"], text=True).split(".")[0])
    march = args.march or ("rv64ima" if gcc_major < 11 else "rv64ima_zicsr_zifencei")
    env_dir = chipyard / "tools/torture/env/p"
    for required in (simulator, env_dir / "riscv_test.h", env_dir / "link.ld",
                     study / "coverage_points.json", study / "monitor_modules.json"):
        if not required.is_file():
            parser.error(f"missing required input: {required}")
    registry = json.loads((study / "coverage_points.json").read_text())
    rows = registry["points"] if isinstance(registry, dict) else registry
    if not rows:
        parser.error("coverage registry is empty")
    targets, partition = tuple(r["point_id"] for r in rows), rows[0]["partition_id"]
    directed = []
    if args.directed_catalog:
        directed = [j for j in JobCatalog.read_jsonl(args.directed_catalog).jobs if j.engine is Engine.DIRECTED][:2]
        if len(directed) != 2:
            parser.error("directed catalog must contain two directed jobs")
        for job in directed:
            if sha256(Path(job.payload["input_elf"])) != job.payload["sha256"]:
                parser.error("directed ELF hash changed")
    if out.exists() and any(out.iterdir()):
        parser.error(f"refusing to overwrite nonempty output: {out}")
    out.mkdir(parents=True, exist_ok=True)
    for name in ("coverage_points.json", "monitor_modules.json"):
        shutil.copy2(study / name, out / name)
    (out / "configs").mkdir()
    configs = {}
    for node, profile in profiles.items():
        config = effective_config(profile, args.profiles)
        if args.loop_count is not None:
            config[PREFIX + "loop"] = "true"
            # Upstream executes its body once before decrementing loop_count.
            config[PREFIX + "loop_size"] = str(args.loop_count - 1)
        configs[node] = config
        (out / "configs" / f"{node}.config").write_text("".join(f"{k} {v}\n" for k, v in config.items()))
    plans = [{"node": f"crv-{n}", "index": i, "job_id": f"crv-{n}-{i:03d}",
              "seed": base + (n - 1) * args.per_node + i} for n in range(1, 6) for i in range(args.per_node)]
    provenance = generate_sources(chipyard / "tools/torture", out, plans, args)
    simulator_hash = sha256(simulator)
    config_hash = hashlib.sha256(json.dumps(configs, sort_keys=True).encode()).hexdigest()
    version = f"mediumboom:sim:{simulator_hash}:profiles:{config_hash}:overlay:{provenance['overlay_sha256']}"
    runtime = {"simulator": str(simulator), "simulator_sha256": simulator_hash,
               "chipyard_root": str(chipyard), "execution_root": str(out / "runs"),
               "monitor_modules": str(out / "monitor_modules.json"),
               "monitor_modules_sha256": sha256(out / "monitor_modules.json")}
    jobs, manifests, seen = [], [], set()
    for plan in plans:
        node, job_id, seed = plan["node"], plan["job_id"], plan["seed"]
        asm, elf = out / f"{job_id}.S", out / f"{job_id}.elf"
        asm.write_text(add_privilege_checks(asm.read_text(), seed, int(configs[node][PREFIX + "profile.privilege_tests"])))
        cmd = [gcc, "-nostdlib", "-nostartfiles", f"-march={march}", "-mabi=lp64", "-mcmodel=medany",
               "-Wl,--no-relax", "-include", str(ROOT / "adapters/mediumboom/stimulus/as_compat.h"),
               "-I" + str(env_dir), "-T", str(env_dir / "link.ld"), "-o", str(elf), str(asm)]
        subprocess.run(cmd, check=True)
        stimulus = stimulus_sha256(elf)
        if stimulus in seen:
            raise ValueError(f"duplicate executable stimulus: {job_id}")
        seen.add(stimulus)
        expected = reference_signature(spike, elf, elf, args.reference_timeout)
        payload = {**runtime, "input_elf": str(elf), "sha256": sha256(elf), "assembly_sha256": sha256(asm),
                   "stimulus_sha256": stimulus, "work_type": "crv", "crv_profile": node,
                   "crv_profile_name": profiles[node]["name"], "seed": seed,
                   "config_sha256": sha256(out / "configs" / f"{node}.config"),
                   "expected_signature": str(elf.with_suffix(".signature")), "expected_signature_sha256": expected}
        jobs.append(JobSpec(job_id=job_id, engine=Engine.CRV, partition_id=partition,
            sequence_in_lane=plan["index"], target_point_ids=targets, seed_or_query_id=f"crv-seed:{seed}",
            timeout_seconds=args.timeout, artifact_version=version, payload=payload, node_id=node))
        manifests.append({"job_id": job_id, **payload})
    for index, original in enumerate(directed):
        job_id = f"directed-{index:03d}"
        elf = out / f"{job_id}.elf"
        shutil.copy2(original.payload["input_elf"], elf)
        stimulus = stimulus_sha256(elf)
        if stimulus in seen:
            raise ValueError(f"duplicate executable stimulus: {job_id}")
        seen.add(stimulus)
        expected = reference_signature(spike, elf, elf, args.reference_timeout)
        payload = {**original.payload, **runtime, "input_elf": str(elf), "sha256": sha256(elf),
                   "stimulus_sha256": stimulus, "expected_signature": str(elf.with_suffix(".signature")),
                   "expected_signature_sha256": expected}
        jobs.append(JobSpec(job_id=job_id, engine=Engine.DIRECTED, partition_id=partition,
            sequence_in_lane=index, target_point_ids=targets, seed_or_query_id=original.seed_or_query_id,
            timeout_seconds=args.timeout, artifact_version=version, payload=payload, node_id="directed-1"))
    validate_unique_work(jobs, executor=lambda job: None)
    JobCatalog(jobs, shared_target_file=out / "coverage_points.json").write_jsonl(out / "catalog.jsonl")
    CampaignSpec(campaign_id=out.name, artifact_version=version,
        assumption_version="not-applicable-no-formal-jobs", n1=5, n2=int(bool(directed)), n3=0,
        budget_compute_hours=max(3, len(jobs) * args.timeout / 3600), recent_window=10,
        resources=ResourceCapacity(compute=2, sim_license=1, formal_license=0)).write_json(out / "campaign.json")
    source_config_hashes = {node: sha256(args.profiles.parent / profile["config"])
                            for node, profile in profiles.items()}
    manifest = {**provenance, "seed_base": base, "profiles": profiles,
                "profiles_sha256": sha256(args.profiles),
                "source_config_sha256": source_config_hashes,
                "effective_configs": configs,
                "crv_jobs": manifests, "march": march, "simulator_sha256": simulator_hash,
                "python_generator_sha256": sha256(ROOT / "adapters/mediumboom/crv_profiles.py"),
                "compiler": subprocess.check_output([gcc, "--version"], text=True).splitlines()[0],
                "directed_jobs": [j.job_id for j in jobs if j.engine is Engine.DIRECTED]}
    (out / "generator_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"crv": count, "directed": len(directed), "nodes": 5, "out": str(out)}, indent=2))


if __name__ == "__main__":
    main()
