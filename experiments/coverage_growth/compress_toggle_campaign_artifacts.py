"""Losslessly compress parsed toggle databases and keep event artifact paths valid."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--study", type=Path, required=True,
                        help="Study directory containing inputs/<trial>/<arm>/events.jsonl")
    args = parser.parse_args()
    raw = args.raw.resolve()
    study = args.study.resolve()
    manifest = []
    count = 0
    for events_path in sorted((study / "inputs").rglob("events.jsonl")):
        rows = [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]
        changed = False
        for row in rows:
            if row.get("event") != "job_completed":
                continue
            result = row.get("result", {})
            updated = []
            for item in result.get("artifact_paths", []):
                path = Path(item)
                if path.name != "coverage.dat" or not path.is_file():
                    updated.append(item)
                    continue
                try:
                    path.resolve().relative_to(raw)
                except ValueError as exc:
                    raise SystemExit(f"refusing to compress coverage database outside raw root: {path}") from exc
                raw_sha = sha256(path)
                original_bytes = path.stat().st_size
                compressed = path.with_name("coverage.dat.gz")
                temp = compressed.with_suffix(".gz.tmp")
                with path.open("rb") as source, gzip.open(temp, "wb", compresslevel=6) as target:
                    while block := source.read(1 << 20):
                        target.write(block)
                with gzip.open(temp, "rb") as check:
                    digest = hashlib.sha256()
                    for block in iter(lambda: check.read(1 << 20), b""):
                        digest.update(block)
                if digest.hexdigest() != raw_sha:
                    temp.unlink(missing_ok=True)
                    raise SystemExit(f"gzip verification failed for {path}")
                os.replace(temp, compressed)
                path.unlink()
                updated.append(str(compressed))
                manifest.append({"original_path": str(path), "compressed_path": str(compressed),
                                 "uncompressed_sha256": raw_sha,
                                 "compressed_sha256": sha256(compressed),
                                 "original_bytes": original_bytes,
                                 "compressed_bytes": compressed.stat().st_size})
                count += 1
                changed = True
            if changed:
                result["artifact_paths"] = updated
        if changed:
            temp_events = events_path.with_suffix(".jsonl.tmp")
            temp_events.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
            os.replace(temp_events, events_path)
    manifest_path = raw / "compressed_coverage_manifest.json"
    existing = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    existing.extend(manifest)
    manifest_path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"compressed_databases": count,
                      "saved_bytes": sum(row["original_bytes"] - row["compressed_bytes"]
                                         for row in manifest if row["original_bytes"] is not None),
                      "manifest": str(manifest_path)}, indent=2))


if __name__ == "__main__":
    main()
