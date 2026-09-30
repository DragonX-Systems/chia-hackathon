"""Stage source-pinned BOOM v3 monitors without editing the upstream checkout.

This is source preparation, not elaboration or formal qualification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_catalog() -> dict:
    catalog = json.loads((HERE / "catalog.json").read_text())
    observed = {}
    for path in sorted((HERE / "monitors").glob("*.scala")):
        if path.stem == "common":
            continue
        for kind, pid in re.findall(r'boom(Assert|Cover)\("([a-z0-9_.]+)"', path.read_text()):
            if pid in observed:
                raise ValueError(f"Duplicate property family: {pid}")
            observed[pid] = (kind.lower(), f"monitors/{path.name}")
    ids = [row["id"] for row in catalog["properties"]]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate catalog IDs")
    expected = {row["id"]: (row["kind"], row["monitor"]) for row in catalog["properties"]}
    if expected != observed:
        raise ValueError("Catalog and executable monitor declarations differ")
    for row in catalog["properties"]:
        for field in ("intent", "stimulus", "mutation", "source", "contract", "priority"):
            if not row.get(field):
                raise ValueError(f"Missing {field}: {row['id']}")
    return catalog


def stage(root: Path, out: Path) -> dict:
    root, out = root.resolve(), out.resolve()
    if out == root or root in out.parents:
        raise ValueError("Output must be outside the upstream checkout")
    catalog = validate_catalog()
    lock = json.loads((HERE / "source_lock.json").read_text())
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"], text=True)
    if revision != lock["revision"] or dirty:
        raise ValueError("An unchanged checkout of the locked BOOM revision is required")
    # Read and validate ALL source files before creating any output.
    sources = {}
    for name, expected in lock["files"].items():
        data = (root / name).read_bytes()
        if sha256(data) != expected:
            raise ValueError(f"BOOM source differs from reviewed revision: {name}")
        sources[name] = data.decode()
    common = (HERE / "monitors/common.scala").read_text()
    for binding in lock["bindings"]:
        name, cls, monitor = binding["source"], binding["class"], binding["monitor"]
        source = sources[name]
        start = re.search(rf"^class {re.escape(cls)}(?:\(|\s)", source, re.M)
        if start is None:
            raise ValueError(f"Missing class {cls}")
        # This pinned source uses column-zero braces for top-level class ends.
        end = source.find("\n}", start.end())
        if end < 0:
            raise ValueError(f"Missing class boundary: {cls}")
        block = (HERE / monitor).read_text()
        sources[name] = source[:end] + "\n  // BOOM LAST-MILE MONITORS BEGIN\n" + common + block + "\n  // BOOM LAST-MILE MONITORS END\n" + source[end:]
    out.mkdir(parents=True, exist_ok=False)
    written = {}
    for name, source in sources.items():
        dest = out / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(source)
        written[name] = sha256(dest.read_bytes())
    manifest = {
        "schema": 1, "model": catalog["model"], "source_revision": lock["revision"],
        "catalog_sha256": sha256((HERE / "catalog.json").read_bytes()),
        "source_lock_sha256": sha256((HERE / "source_lock.json").read_bytes()),
        "monitor_sha256": {p.name: sha256(p.read_bytes()) for p in sorted((HERE / "monitors").glob("*.scala"))},
        "input_sha256": lock["files"], "output_sha256": written,
        "qualification": "SOURCE_STAGED_ONLY", "core_pruning_authorized": False,
        "required_next_steps": ["Compile with the matching Chipyard dependency lock",
                                "Elaborate and inventory each monitor instance",
                                "Run reset/activation, positive and mutation qualification",
                                "Run assertions and covers on the actual RTL"],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boom-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        manifest = stage(args.boom_root, args.out)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Refusing instrumentation: {exc}\n")
    print(json.dumps({"out": str(args.out), "qualification": manifest["qualification"]}))


if __name__ == "__main__":
    main()
