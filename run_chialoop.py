#!/usr/bin/env python3
"""Run the artifact-agnostic core Chialoop or compare completed paired runs.

The engine executor is supplied as ``package.module:callable``.  The callable
receives one ``JobSpec`` and returns ``EngineEvidence``.  MediumBOOM build and
test generation intentionally remain outside this entry point.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from chialoop import (  # noqa: E402
    AdaptivePolicy,
    CampaignRunner,
    CampaignSpec,
    ChiaAsyncRuntime,
    CoveragePoint,
    EngineEvidence,
    JobCatalog,
    JobSpec,
    LocalAsyncRuntime,
    StaticHybridPolicy,
    compare_paired_runs,
    write_comparison,
)


def _load_executor(spec: str) -> Callable[[JobSpec], EngineEvidence]:
    try:
        module_name, attribute = spec.split(":", 1)
    except ValueError as exc:
        raise ValueError("executor must use package.module:callable syntax") from exc
    value: Any = getattr(importlib.import_module(module_name), attribute)
    if not callable(value):
        raise TypeError(f"executor is not callable: {spec}")
    return value


def _read_coverage(path: Path) -> list[CoveragePoint]:
    data = json.loads(path.read_text())
    raw_points = data["points"] if isinstance(data, dict) else data
    return [CoveragePoint(**item) for item in raw_points]


def _read_summary(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _run(args: argparse.Namespace) -> None:
    # Apply overrides before resolving defaults so omitted licenses follow --k.
    campaign = json.loads(args.campaign.read_text())
    for name in ("n1", "n2", "n3"):
        if getattr(args, name) is not None:
            campaign[name] = getattr(args, name)
    if args.k is not None:
        campaign["recent_window"] = args.k
    resources = campaign.setdefault("resources", {})
    for field, arg in (("compute", "resources"), ("sim_license", "sim_licenses"),
                       ("formal_license", "formal_licenses")):
        if getattr(args, arg) is not None:
            resources[field] = getattr(args, arg)
    spec = CampaignSpec.from_dict(campaign)
    catalog = JobCatalog.read_jsonl(args.catalog)
    coverage = _read_coverage(args.coverage)
    executor = _load_executor(args.executor)
    policy = (
        AdaptivePolicy()
        if args.arm == "adaptive"
        else StaticHybridPolicy(catalog)
    )
    runtime = (
        ChiaAsyncRuntime()
        if args.runtime == "chia"
        else LocalAsyncRuntime(max_workers=spec.resources.compute)
    )
    try:
        summary = CampaignRunner(
            spec=spec,
            catalog=catalog,
            coverage_points=coverage,
            policy=policy,
            runtime=runtime,
            executor=executor,
            trial_id=args.trial_id,
            output_dir=args.out,
        ).run()
    finally:
        runtime.close()
    print(json.dumps(summary.to_dict(), indent=2, sort_keys=True))


def _compare(args: argparse.Namespace) -> None:
    comparison = compare_paired_runs(
        [_read_summary(path) for path in args.static_summary],
        [_read_summary(path) for path in args.adaptive_summary],
        bootstrap_samples=args.bootstrap_samples,
    )
    write_comparison(comparison, args.out)
    print(json.dumps(comparison, indent=2, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run one frozen campaign arm")
    run.add_argument("--campaign", type=Path, required=True)
    run.add_argument("--coverage", type=Path, required=True)
    run.add_argument("--catalog", type=Path, required=True)
    run.add_argument("--executor", required=True, help="package.module:callable")
    run.add_argument("--arm", choices=("adaptive", "static"), required=True)
    run.add_argument("--runtime", choices=("local", "chia"), default="chia")
    run.add_argument("--trial-id", required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--n1", type=int, help="CRV node count (campaign default: 5)")
    run.add_argument("--n2", type=int, help="directed node count (campaign default: 1)")
    run.add_argument("--n3", type=int, help="formal node count (campaign default: 1)")
    run.add_argument("--resources", "--r", type=int, help="compute slots (campaign default: 5); licenses remain separately configured")
    run.add_argument("--sim-licenses", type=int, help="simulation license tokens (default: K unless set in campaign)")
    run.add_argument("--formal-licenses", type=int, help="formal license tokens (default: K unless set in campaign)")
    run.add_argument("--k", type=int, help="completed runs per node used for scoring (campaign default: 10)")
    run.set_defaults(handler=_run)

    compare = commands.add_parser("compare", help="compare paired summary files")
    compare.add_argument("--static-summary", type=Path, action="append", required=True)
    compare.add_argument("--adaptive-summary", type=Path, action="append", required=True)
    compare.add_argument("--bootstrap-samples", type=int, default=5000)
    compare.add_argument("--out", type=Path, required=True)
    compare.set_defaults(handler=_compare)
    return parser


def main() -> None:
    args = _parser().parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
