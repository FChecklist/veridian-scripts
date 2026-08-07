#!/usr/bin/env python3
"""Real tests for check_latest_task.py.

SAFETY: check_latest_task.py is bare top-level script code with NO
`if __name__ == "__main__":` guard. It runs immediately on import/exec,
including a REAL `subprocess.run(["systemctl", "--user", "start", ...])`
call in the zero-active-units branch. It is therefore NEVER imported or
exec'd in-process here. It is invoked ONLY as a real subprocess
(`python3 check_latest_task.py`) with a modified PATH that puts a temp
directory first, containing a fake `systemctl` shell-script stub that
intercepts both the `list-units` call (so we control the reported active
unit count) and the `start` call (so it only appends its argv to a log
file instead of touching real systemd).

The script also hardcodes the real, live `/opt/veridian/ai-os/tasks`
directory with no env-var override, so these tests read that directory
read-only and assert on the script's general real logic (line shape,
active_units=N matching exactly what the stub reported, and -- in the
zero-active-units case -- that the auto-start stub log recorded the
correct unit name derived from whatever the real latest task entry
currently is) rather than hardcoding today's specific task/file name.
"""
import glob
import os
import stat
import subprocess
import sys
import tempfile

import pytest
import yaml

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SUT_PATH = os.path.join(SCRIPTS_DIR, "check_latest_task.py")
REAL_TASKS_DIR = "/opt/veridian/ai-os/tasks"

STUB_SYSTEMCTL = """#!/bin/bash
# Fake systemctl for check_latest_task.py tests. Never touches real systemd.
if [ "$1" = "--user" ] && [ "$2" = "list-units" ]; then
    if [ -f "$STUB_LIST_UNITS_FILE" ]; then
        cat "$STUB_LIST_UNITS_FILE"
    fi
    exit 0
fi
if [ "$1" = "--user" ] && [ "$2" = "start" ]; then
    echo "$@" >> "$STUB_START_LOG_FILE"
    exit 0
fi
exit 0
"""


def _real_latest_and_status():
    """Read-only replica of the SUT's own latest-task + status detection
    logic (glob + getmtime + yaml.safe_load of task.yaml) -- no systemctl
    calls, no writes, completely safe to run in-process. Used only to know
    what to *expect* from the subprocess output, mirroring the SUT exactly."""
    dirs = sorted(glob.glob(os.path.join(REAL_TASKS_DIR, "*")), key=os.path.getmtime, reverse=True)
    assert dirs, "expected at least one real entry under /opt/veridian/ai-os/tasks for this test to run"
    latest_path = dirs[0]
    latest = os.path.basename(latest_path)
    task_yaml = os.path.join(latest_path, "task.yaml")
    try:
        d = yaml.safe_load(open(task_yaml))
        status = d.get("status")
    except Exception:
        status = "UNKNOWN"
    return latest, status


@pytest.fixture
def stub_env(tmp_path):
    """A real temp dir on PATH ahead of everything else, containing a real
    executable `systemctl` stub. Returns (env, list_units_file, start_log_file)."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    stub_path = bin_dir / "systemctl"
    stub_path.write_text(STUB_SYSTEMCTL)
    st = os.stat(stub_path)
    os.chmod(stub_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    list_units_file = tmp_path / "list_units_output.txt"
    start_log_file = tmp_path / "start_calls.log"

    env = dict(os.environ)
    env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
    env["STUB_LIST_UNITS_FILE"] = str(list_units_file)
    env["STUB_START_LOG_FILE"] = str(start_log_file)
    return env, list_units_file, start_log_file


def _run_sut(env):
    return subprocess.run(
        ["python3", SUT_PATH],
        capture_output=True, text=True, env=env, cwd=SCRIPTS_DIR, timeout=30,
    )


def test_active_units_greater_than_zero_prints_correct_count_and_does_not_autostart(stub_env):
    env, list_units_file, start_log_file = stub_env
    # Real systemctl --user list-units --no-legend shape: one unit per line.
    list_units_file.write_text(
        "veridian-worker@task-fake-a.service loaded active running Veridian worker fake a\n"
        "veridian-worker@task-fake-b.service loaded active running Veridian worker fake b\n"
    )

    result = _run_sut(env)
    assert result.returncode == 0, result.stderr

    lines = [l for l in result.stdout.splitlines() if l.strip()]
    assert lines, result.stdout
    first_line = lines[0]

    fields = first_line.split("|")
    assert len(fields) == 4, first_line
    latest_field, status_field, note_field, active_field = fields
    assert active_field == "active_units=2"

    # the printed dirname must correspond to a real, currently-existing entry
    assert os.path.exists(os.path.join(REAL_TASKS_DIR, latest_field)), latest_field

    # no auto-start should fire when active units were found
    assert "AUTO_STARTED=" not in result.stdout
    assert not start_log_file.exists() or start_log_file.read_text().strip() == ""


def test_zero_active_units_prints_zero_and_autostarts_correct_unit_or_notes_completed(stub_env):
    env, list_units_file, start_log_file = stub_env
    # Real systemctl output when nothing matches: no stdout lines at all.
    list_units_file.write_text("")

    expected_latest, expected_status = _real_latest_and_status()

    result = _run_sut(env)
    assert result.returncode == 0, result.stderr

    lines = [l for l in result.stdout.splitlines() if l.strip()]
    first_line = lines[0]
    fields = first_line.split("|")
    assert len(fields) == 4, first_line
    latest_field, status_field, note_field, active_field = fields
    assert active_field == "active_units=0"
    assert latest_field == expected_latest
    assert status_field == expected_status

    if expected_status == "completed":
        # zero active units + status completed -> informational NOTE only,
        # no auto-start, per the script's own branching.
        assert "NOTE=" in result.stdout
        assert "AUTO_STARTED=" not in result.stdout
        assert not start_log_file.exists() or start_log_file.read_text().strip() == ""
    else:
        # zero active units + status not completed -> real auto-start branch.
        # Assert the stub's systemctl --user start log recorded a call for
        # exactly the correct unit name derived from the real latest task.
        assert f"AUTO_STARTED={expected_latest}" in result.stdout
        assert start_log_file.exists(), "expected the stubbed systemctl start to have been invoked"
        start_log = start_log_file.read_text()
        assert "--user start" in start_log
        assert f"veridian-worker@{expected_latest}.service" in start_log


def test_active_units_count_matches_stub_exactly_for_five_units(stub_env):
    env, list_units_file, start_log_file = stub_env
    list_units_file.write_text(
        "\n".join(
            f"veridian-worker@task-fake-{i}.service loaded active running fake {i}"
            for i in range(5)
        )
        + "\n"
    )
    result = _run_sut(env)
    assert result.returncode == 0, result.stderr
    assert "active_units=5" in result.stdout
    assert "AUTO_STARTED=" not in result.stdout


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
