"""Coverage is credited only after simulator identity and Spike checks pass."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.coverage_growth import mediumboom_open_toggle_executor as executor
from chialoop.core.types import JobStatus


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    simulator, elf, monitor = [tmp_path/name for name in ('sim', 'input.elf', 'monitor.json')]
    simulator.write_bytes(b'sim')
    elf.write_bytes(b'elf')
    monitor.write_text('["Monitor"]')
    record = {'page':'v_toggle/Monitor','h':'TOP.dut','o':'signal'}
    point = executor._point_id(record)
    coverage = "C '" + '\x01'.join(k+'\x02'+v for k,v in record.items()) + "' 1\n"
    seen = []

    def run(cmd, cwd, **kwargs):
        assert not any('verilator+coverage+file' in arg for arg in cmd)
        assert any(arg.startswith('+signature=') for arg in cmd)
        seen.append(cwd)
        (cwd/'coverage.dat').write_text(coverage)
        (cwd/'actual.signature').write_text('0'*32+'\n')
        return SimpleNamespace(returncode=0,stdout='Verilog $finish',stderr='')

    monkeypatch.setattr(executor.subprocess,'run',run)
    payload = {'execution_root':str(tmp_path/'runs'),'simulator':str(simulator),
        'simulator_sha256':executor._sha256(simulator),'chipyard_root':str(tmp_path),
        'monitor_modules':str(monitor),'monitor_modules_sha256':executor._sha256(monitor),
        'input_elf':str(elf),'sha256':executor._sha256(elf),'crv_profile':'crv-1',
        'expected_signature_sha256':hashlib.sha256(('0'*32).encode()).hexdigest()}
    job = SimpleNamespace(payload=payload,job_id='same-job',timeout_seconds=1,
                          target_point_ids=[point],artifact_version='frozen')
    return job, seen, point


def test_validated_runs_use_fresh_directories(runtime):
    job, seen, point = runtime
    for _ in range(2):
        result = executor.execute(job)
        assert result.status == JobStatus.SUCCESS
        assert result.hit_point_ids == (point,)
    assert seen[0] != seen[1]


def test_signature_mismatch_never_credits_coverage(runtime):
    job, _, _ = runtime
    job.payload['expected_signature_sha256'] = 'wrong'
    result = executor.execute(job)
    assert result.status == JobStatus.ERROR
    assert not result.hit_point_ids
    assert 'differs from Spike' in result.error_summary


@pytest.mark.parametrize('field', ['simulator_sha256','monitor_modules_sha256','sha256'])
def test_changed_inputs_are_rejected_before_execution(runtime, field):
    job, seen, _ = runtime
    job.payload[field] = 'wrong'
    assert executor.execute(job).status == JobStatus.ERROR
    assert not seen


def test_legacy_profile_without_reference_is_rejected(runtime):
    job, seen, _ = runtime
    del job.payload['expected_signature_sha256']
    assert executor.execute(job).status == JobStatus.ERROR
    assert not seen


@pytest.mark.parametrize('missing', ['actual.signature','coverage.dat'])
def test_missing_output_never_credits_coverage(runtime, monkeypatch, missing):
    job, _, _ = runtime
    original = executor.subprocess.run
    def incomplete(cmd, cwd, **kwargs):
        result = original(cmd, cwd, **kwargs)
        (cwd/missing).unlink()
        return result
    monkeypatch.setattr(executor.subprocess,'run',incomplete)
    result = executor.execute(job)
    assert result.status == JobStatus.ERROR
    assert not result.hit_point_ids
