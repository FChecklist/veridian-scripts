#!/usr/bin/env python3
"""Real, end-to-end test for supervisor-sweep.sh -- the safety-net loop that
discovers any task stuck in pending_review with no review.json (missed
supervisor trigger, or an `adopt`ed task) and starts its supervisor unit.
SPEC: owner absolute stop-work order, task-20260806-165921, first real test
batch for the ~101-script untested gap, UMR-20260806-175915-8f47.

Runs the REAL script (bash /opt/.../supervisor-sweep.sh) as a real
subprocess -- not a reimplementation of its discovery logic -- against a
throwaway fixture task directory via its own documented, purpose-built
override seam (VERIDIAN_TASKS_DIR / VERIDIAN_SWEEP_LOG_DIR; see the
script's own header comment: "purely so tests/supervisor_sweep_discovery_
test.sh can run this REAL script against a throwaway fixture instead of
the live /opt/veridian/ai-os/tasks"). `systemctl` is stubbed via a PATH
override (writes its invocation to a real file) so the test proves the
real script decides correctly which units to start, without depending on
or mutating any real systemd unit.
"""
import os
import stat
import subprocess
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(SCRIPTS_DIR, "supervisor-sweep.sh")

STUB_SYSTEMCTL = """#!/bin/bash
echo "$@" >> "$SYSTEMCTL_CALLS_LOG"
exit 0
"""


def _make_task(tasks_dir, task_id, status, has_review_json):
    d = os.path.join(tasks_dir, task_id)
    os.makedirs(d)
    with open(os.path.join(d, "task.yaml"), "w") as f:
        f.write(f"status: {status}\n")
    if has_review_json:
        with open(os.path.join(d, "review.json"), "w") as f:
            f.write("{}\n")


def _run_sweep(tasks_dir, log_dir):
    calls_log = os.path.join(log_dir, "systemctl_calls.log")
    open(calls_log, "w").close()

    with tempfile.TemporaryDirectory() as bin_dir:
        systemctl_path = os.path.join(bin_dir, "systemctl")
        with open(systemctl_path, "w") as f:
            f.write(STUB_SYSTEMCTL)
        os.chmod(systemctl_path, os.stat(systemctl_path).st_mode | stat.S_IEXEC)

        env = dict(os.environ)
        env["VERIDIAN_TASKS_DIR"] = tasks_dir
        env["VERIDIAN_SWEEP_LOG_DIR"] = log_dir
        env["SYSTEMCTL_CALLS_LOG"] = calls_log
        env["PATH"] = bin_dir + os.pathsep + env["PATH"]

        proc = subprocess.run(
            ["bash", SCRIPT], capture_output=True, text=True, timeout=30, env=env,
        )
        with open(calls_log) as f:
            calls = [line.strip() for line in f if line.strip()]
        return proc, calls


def test_starts_supervisor_for_pending_review_with_no_review_json():
    with tempfile.TemporaryDirectory() as tasks_dir, tempfile.TemporaryDirectory() as log_dir:
        _make_task(tasks_dir, "task-missed-trigger", "pending_review", has_review_json=False)
        proc, calls = _run_sweep(tasks_dir, log_dir)
        assert proc.returncode == 0, proc.stderr
        assert any("daemon-reload" in c for c in calls), calls
        assert any("start veridian-supervisor@task-missed-trigger.service" in c for c in calls), calls


def test_does_not_start_supervisor_when_review_json_already_present():
    with tempfile.TemporaryDirectory() as tasks_dir, tempfile.TemporaryDirectory() as log_dir:
        _make_task(tasks_dir, "task-already-reviewed", "pending_review", has_review_json=True)
        proc, calls = _run_sweep(tasks_dir, log_dir)
        assert proc.returncode == 0, proc.stderr
        assert calls == [], f"must not touch systemctl when review.json exists: {calls}"


def test_does_not_start_supervisor_for_non_pending_review_status():
    with tempfile.TemporaryDirectory() as tasks_dir, tempfile.TemporaryDirectory() as log_dir:
        _make_task(tasks_dir, "task-in-progress", "in_progress", has_review_json=False)
        proc, calls = _run_sweep(tasks_dir, log_dir)
        assert proc.returncode == 0, proc.stderr
        assert calls == [], f"must not touch systemctl for a non pending_review task: {calls}"


def test_handles_task_dir_with_no_task_yaml_gracefully():
    with tempfile.TemporaryDirectory() as tasks_dir, tempfile.TemporaryDirectory() as log_dir:
        os.makedirs(os.path.join(tasks_dir, "task-empty-dir"))
        proc, calls = _run_sweep(tasks_dir, log_dir)
        assert proc.returncode == 0, proc.stderr
        assert calls == []


def test_real_sweep_log_file_written_with_start_and_done_markers():
    with tempfile.TemporaryDirectory() as tasks_dir, tempfile.TemporaryDirectory() as log_dir:
        _make_task(tasks_dir, "task-x", "queued", has_review_json=False)
        proc, _ = _run_sweep(tasks_dir, log_dir)
        assert proc.returncode == 0, proc.stderr
        sweep_logs = [f for f in os.listdir(log_dir) if f.startswith("supervisor-sweep-")]
        assert len(sweep_logs) == 1, sweep_logs
        content = open(os.path.join(log_dir, sweep_logs[0])).read()
        assert "=== supervisor sweep" in content
        assert "=== done" in content


if __name__ == "__main__":
    test_starts_supervisor_for_pending_review_with_no_review_json()
    test_does_not_start_supervisor_when_review_json_already_present()
    test_does_not_start_supervisor_for_non_pending_review_status()
    test_handles_task_dir_with_no_task_yaml_gracefully()
    test_real_sweep_log_file_written_with_start_and_done_markers()
    print("PASS: all supervisor-sweep.sh tests")
