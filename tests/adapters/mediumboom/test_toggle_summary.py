import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "verification/boom/summarize_monitored_toggle.py"


def record(module, count):
    return (
        "C '\x01f\x02generated.sv\x01l\x0210\x01page\x02v_toggle/"
        f"{module}\x01o\x02signal[0]\x01h\x02top.{module}' {count}\n"
    )


def test_toggle_summary_filters_inventory_modules_and_checks_identity(tmp_path):
    coverage = tmp_path / "coverage.dat"
    coverage.write_text(record("Rob", 4) + record("NBDTLB", 0) + record("Other", 3))
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({
        "coverage_sha256": hashlib.sha256(coverage.read_bytes()).hexdigest(),
        "instances": [{"module": "Rob"}, {"module": "NBDTLB"}],
    }))

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(coverage), "--instance-inventory", str(inventory)],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    assert report["module_count"] == 2
    assert report["bins"] == 2
    assert report["hit_bins"] == 1
    assert report["modules"]["Rob"]["percent"] == 100.0
    assert report["modules"]["NBDTLB"]["percent"] == 0.0
    assert "Other" not in report["modules"]

    inventory_data = json.loads(inventory.read_text())
    inventory_data["coverage_sha256"] = "wrong"
    inventory.write_text(json.dumps(inventory_data))
    rejected = subprocess.run(
        [sys.executable, str(SCRIPT), str(coverage), "--instance-inventory", str(inventory)],
        capture_output=True,
        text=True,
    )
    assert rejected.returncode != 0
    assert "does not match" in rejected.stderr
