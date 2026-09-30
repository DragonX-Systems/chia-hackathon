#!/usr/bin/env python3
"""Summarize toggle activity for modules containing named BOOM monitors."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path


def fields_from_record(line: str) -> dict[str, str]:
    metadata = line.split("' ", 1)[0].split("'", 1)[1]
    return {
        field.split("\x02", 1)[0]: field.split("\x02", 1)[1]
        for field in metadata.split("\x01")
        if "\x02" in field
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("coverage", type=Path)
    parser.add_argument("--instance-inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    inventory = json.loads(args.instance_inventory.read_text())
    modules = {item["module"] for item in inventory["instances"] if item.get("module")}
    coverage_hash = sha256(args.coverage)
    expected_hash = inventory.get("coverage_sha256")
    if expected_hash and coverage_hash != expected_hash:
        parser.error("coverage database hash does not match the instance inventory")

    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for line in args.coverage.open(errors="replace"):
        if "page\x02v_toggle/" not in line:
            continue
        try:
            fields = fields_from_record(line)
            module = fields["page"].removeprefix("v_toggle/")
            if module not in modules:
                continue
            hits = int(line.rsplit("' ", 1)[1].strip()) > 0
        except (IndexError, KeyError, ValueError):
            continue
        counts[module][0] += 1
        counts[module][1] += hits

    bins = sum(total for total, _ in counts.values())
    hit_bins = sum(hit for _, hit in counts.values())
    report = {
        "coverage_file": args.coverage.name,
        "coverage_sha256": coverage_hash,
        "inventory_file": args.instance_inventory.name,
        "scope_note": "Toggle activity filtered to generated modules containing named BOOM activation/scenario counters. This is signal activity, not semantic/property coverage.",
        "module_count": len(counts),
        "bins": bins,
        "hit_bins": hit_bins,
        "percent": round(100 * hit_bins / bins, 2) if bins else None,
        "modules": {
            module: {
                "bins": counts[module][0],
                "hit_bins": counts[module][1],
                "percent": round(100 * counts[module][1] / counts[module][0], 2)
                if counts[module][0]
                else None,
            }
            for module in sorted(counts)
        },
    }
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    main()
