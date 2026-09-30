"""Test actual formal command selection without launching SBY or a solver."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from chialoop.core.identity import validate_unique_work
from chialoop.core.types import Engine, JobSpec, JobStatus
from experiments.coverage_growth import mediumboom_native_executor as native


@pytest.fixture
def formal_job(tmp_path):
    config = tmp_path / "query.sby"
    config.write_text("[tasks]\nfirst\nsecond\n[options]\nmode cover\n")
    return JobSpec("formal-a", Engine.FORMAL, "p", 0,
                   ("tlb.full_fence_late_refill",), "label-a", 10, "rtl", "asm",
                   payload={"sby_file": str(config), "sby_task": "first"}, node_id="formal-1")


def test_renamed_formal_job_and_reporting_filter_are_not_distinct_work(formal_job):
    renamed = replace(formal_job, job_id="formal-b", seed_or_query_id="label-b", sequence_in_lane=1,
                      payload=dict(formal_job.payload, required_goal="cover__boom_tlb_sfence_ptw_accept"))
    with pytest.raises(ValueError, match="reuses execution"):
        validate_unique_work([formal_job, renamed], native.execute)


def test_distinct_tasks_are_distinct_executions(formal_job):
    second = replace(formal_job, job_id="formal-b", seed_or_query_id="label-b", sequence_in_lane=1,
                     payload=dict(formal_job.payload, sby_task="second"))
    validate_unique_work([formal_job, second], native.execute)
    assert native.execution_fingerprint(formal_job) != native.execution_fingerprint(second)


@pytest.mark.parametrize("task", [None, "first", "second"])
def test_formal_command_honors_selected_file_task_and_target_scope(formal_job, tmp_path, monkeypatch, task):
    work = tmp_path / "work"
    work.mkdir()
    payload = dict(formal_job.payload)
    payload.pop("sby_task")
    if task is not None:
        payload["sby_task"] = task
    job = replace(formal_job, payload=payload)
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        out = Path(cmd[cmd.index("-d") + 1])
        out.mkdir()
        (out / "status").write_text("PASS")
        (out / "logfile.txt").write_text("\n".join(native.FORMAL_GOALS))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(native.subprocess, "run", run)
    result = native._formal(job, work)
    command, options = calls[0]
    assert command[4:] == [str(Path(payload["sby_file"]).resolve())] + ([task] if task else [])
    assert options["cwd"] == tmp_path
    assert result.status is JobStatus.SUCCESS
    assert result.hit_point_ids == job.target_point_ids  # Do not leak other reached goals.


@pytest.mark.parametrize("change", [ {"sby_task": "--bad"}, {"sby_task": ""},
    {"depth": 99}, {"sby_sha256": "wrong"}, {"required_goal": "unknown"}, {"sby_file": "missing.sby"}])
def test_invalid_or_ignored_query_options_fail_before_launch(formal_job, change):
    with pytest.raises(ValueError):
        native.execution_fingerprint(replace(formal_job, payload=dict(formal_job.payload, **change)))


def test_timeout_is_reported_as_timeout(formal_job, tmp_path, monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("sby", 10)
    monkeypatch.setattr(native.subprocess, "run", timeout)
    assert native._formal(formal_job, tmp_path).status is JobStatus.TIMEOUT


def test_nonzero_exit_cannot_credit_stale_pass(formal_job, tmp_path, monkeypatch):
    def fail(cmd, **kwargs):
        out = Path(cmd[3]); out.mkdir()
        (out / "status").write_text("PASS")
        (out / "logfile.txt").write_text("\n".join(native.FORMAL_GOALS))
        return SimpleNamespace(returncode=1, stdout="", stderr="failed")
    monkeypatch.setattr(native.subprocess, "run", fail)
    assert native._formal(formal_job, tmp_path).status is JobStatus.ERROR


def test_relative_and_absolute_query_paths_have_same_fingerprint(formal_job, monkeypatch):
    path = Path(formal_job.payload["sby_file"])
    monkeypatch.setattr(native, "FORMAL_DIR", path.parent)
    relative = replace(formal_job, payload=dict(formal_job.payload, sby_file="./" + path.name))
    assert native.execution_fingerprint(relative) == native.execution_fingerprint(formal_job)


def test_same_query_content_under_different_filename_is_duplicate(formal_job, tmp_path):
    copied = tmp_path / "renamed.sby"
    copied.write_bytes(Path(formal_job.payload["sby_file"]).read_bytes())
    renamed = replace(formal_job, job_id="new", seed_or_query_id="new", sequence_in_lane=1,
                      payload=dict(formal_job.payload, sby_file=str(copied)))
    with pytest.raises(ValueError, match="reuses execution"):
        validate_unique_work([formal_job, renamed], native.execute)


def test_required_goal_must_be_reached_even_when_targets_are_reached(formal_job, tmp_path, monkeypatch):
    job = replace(formal_job, payload=dict(formal_job.payload, required_goal="cover__boom_tlb_sfence_ptw_accept"))
    def run(cmd, **kwargs):
        out = Path(cmd[3]); out.mkdir()
        (out / "status").write_text("PASS")
        (out / "logfile.txt").write_text("cover__boom_tlb_full_fence_late_refill")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(native.subprocess, "run", run)
    assert native._formal(job, tmp_path).status is JobStatus.ERROR
