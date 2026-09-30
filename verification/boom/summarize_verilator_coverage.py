#!/usr/bin/env python3
"""Summarize Verilator v_toggle and Kapil named v_user cover counters."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("coverage", type=Path, help="Verilator coverage.dat")
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path(__file__).with_name("catalog.json"),
    )
    parser.add_argument(
        "--instances-out",
        type=Path,
        help="also write the matched named-cover hierarchy inventory as JSON",
    )
    args = parser.parse_args()
    properties = json.loads(args.catalog.read_text())["properties"]
    expected = {
        "scenario": {p["id"] for p in properties if p["kind"] == "cover"},
        "activation": {p["id"] for p in properties if p["kind"] == "assert"},
    }

    toggle_total = toggle_hit = user_total = user_hit = 0
    groups: dict[str, dict[str, list[tuple[int, str]]]] = {
        "scenario": defaultdict(list),
        "activation": defaultdict(list),
    }
    unmapped: set[str] = set()
    instances: list[dict[str, object]] = []
    for line in args.coverage.open(errors="replace"):
        if "page\x02v_toggle/" in line:
            toggle_total += 1
            toggle_hit += int(line.rsplit("' ", 1)[1].strip()) > 0
        if "page\x02v_user/" not in line:
            continue
        user_total += 1
        count = int(line.rsplit("' ", 1)[1].strip())
        user_hit += count > 0
        try:
            metadata = line.split("' ", 1)[0].split("'", 1)[1]
            fields = {
                field.split("\x02", 1)[0]: field.split("\x02", 1)[1]
                for field in metadata.split("\x01")
                if "\x02" in field
            }
            obj = fields["o"]
        except (IndexError, KeyError):
            unmapped.add("<malformed v_user record>")
            continue
        marker = "cover___05Fboom_"
        if not obj.startswith(marker):
            continue
        raw = obj[len(marker) :]
        kind = "activation" if raw.startswith("activation_") else "scenario"
        if kind == "activation":
            raw = raw[len("activation_") :]
        candidates = [
            family
            for family in expected[kind]
            if raw == family.replace(".", "_")
            or re.fullmatch(re.escape(family.replace(".", "_")) + r"_\d+", raw)
        ]
        if not candidates:
            unmapped.add(obj)
            continue
        family = max(candidates, key=len)
        groups[kind][family].append((count, obj))
        instances.append(
            {
                "kind": kind,
                "family": family,
                "object": obj,
                "hierarchy": fields.get("h"),
                "source": Path(fields["f"]).name if fields.get("f") else None,
                "line": int(fields["l"]) if fields.get("l", "").isdigit() else None,
                "module": fields.get("page", "").removeprefix("v_user/"),
                "hits": count,
            }
        )

    report = {
        "coverage_file": str(args.coverage.resolve()),
        "toggle": {
            "bins": toggle_total,
            "hit_bins": toggle_hit,
            "percent": round(100 * toggle_hit / toggle_total, 2) if toggle_total else None,
        },
        "user": {"bins": user_total, "hit_bins": user_hit},
        "families": {},
        "unmapped_named_objects": sorted(unmapped),
    }
    for kind, ids in expected.items():
        matched = groups[kind]
        report["families"][kind] = {
            "expected_families": len(ids),
            "matched_families": len(matched),
            "hit_families": sum(any(n > 0 for n, _ in matched.get(family, [])) for family in ids),
            "hit_family_ids": sorted(
                family for family in ids if any(n > 0 for n, _ in matched.get(family, []))
            ),
            "instances": sum(len(items) for items in matched.values()),
            "hit_instances": sum(n > 0 for items in matched.values() for n, _ in items),
            "missing_families": sorted(ids - matched.keys()),
            "unhit_family_ids": sorted(
                family for family in ids if not any(n > 0 for n, _ in matched.get(family, []))
            ),
        }
    if args.instances_out:
        args.instances_out.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with args.coverage.open("rb") as coverage_file:
            for chunk in iter(lambda: coverage_file.read(1024 * 1024), b""):
                digest.update(chunk)
        args.instances_out.write_text(
            json.dumps(
                {
                    "coverage_file": args.coverage.name,
                    "coverage_sha256": digest.hexdigest(),
                    "scope_note": "Verilator named user-cover counters; hierarchy is from the instrumented generated DUT. Presence does not imply full property qualification.",
                    "instances": sorted(
                        instances,
                        key=lambda item: (
                            str(item["kind"]),
                            str(item["family"]),
                            str(item["hierarchy"]),
                            str(item["object"]),
                        ),
                    ),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
