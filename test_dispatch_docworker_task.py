"""Real subprocess tests for dispatch-docworker-task.sh.

This script hardcodes several LIVE, outside-this-workspace absolute paths
(/opt/veridian/scripts/resource_governor.py, /opt/veridian/scripts/
veridian-task.py, /opt/veridian/ai-os/tasks/<TASK_ID>/task.yaml) and drives
real systemd units via `systemctl --user`. None of those are safe or
in-scope to exercise for real here (they would create real dispatched
tasks/systemd units on the live system, or require writing outside this
workspace) -- they are true external boundaries, so they're stubbed with
fake `python3` / `systemctl` executables placed first on PATH. Those stubs
log their real argv to files this test asserts on, and the script's OWN
real bash logic (arg validation, the EMERGENCY_STOP gate, CREATE_OUT
parsing, unit stop/disable sequencing) runs for real and unstubbed on
either side of that boundary.

No file is ever created or modified under /opt/veridian (outside this
workspace) by these tests.
"""
import os
import stat
import subprocess

import pytest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dispatch-docworker-task.sh")

FAKE_PYTHON3 = """#!/bin/bash
echo "python3 $*" >> "$CALL_LOG"
if [ "$1" = "-c" ]; then
    # Stands in for the resource_governor.py EMERGENCY_STOP_PATH lookup --
    # the real script embeds the module path only inside this -c source
    # string, never as a bare argv token, so we branch on that.
    if [[ "$2" == *resource_governor* ]]; then
        echo "$FAKE_EMERGENCY_STOP_PATH"
        exit 0
    fi
    exit 1
fi
if [[ "$1" == *veridian-task.py ]]; then
    echo "veridian-task.py $*" >> "$CALL_LOG"
    if [ "$FAKE_CREATE_SHOULD_SUCCEED" = "1" ]; then
        echo "CREATED: $FAKE_TASK_ID"
    else
        echo "some unrelated create output with no CREATED line"
    fi
    exit 0
fi
echo "unexpected python3 invocation: $*" >&2
exit 1
"""

FAKE_SYSTEMCTL = """#!/bin/bash
echo "systemctl $*" >> "$SYSTEMCTL_LOG"
for arg in "$@"; do
    if [ "$arg" = "is-active" ]; then
        echo "inactive"
        exit 0
    fi
done
exit 0
"""


@pytest.fixture
def stub_env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    python_path = bin_dir / "python3"
    python_path.write_text(FAKE_PYTHON3)
    python_path.chmod(python_path.stat().st_mode | stat.S_IEXEC)

    systemctl_path = bin_dir / "systemctl"
    systemctl_path.write_text(FAKE_SYSTEMCTL)
    systemctl_path.chmod(systemctl_path.stat().st_mode | stat.S_IEXEC)

    call_log = tmp_path / "python_calls.log"
    systemctl_log = tmp_path / "systemctl_calls.log"
    call_log.write_text("")
    systemctl_log.write_text("")

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["CALL_LOG"] = str(call_log)
    env["SYSTEMCTL_LOG"] = str(systemctl_log)
    # Default: no emergency stop, create succeeds.
    env["FAKE_EMERGENCY_STOP_PATH"] = str(tmp_path / "no_such_emergency_stop_sentinel")
    env["FAKE_CREATE_SHOULD_SUCCEED"] = "1"
    env["FAKE_TASK_ID"] = "TEST-TASKID-NOT-A-REAL-TASK-abc123"

    return {
        "env": env,
        "call_log": call_log,
        "systemctl_log": systemctl_log,
        "tmp_path": tmp_path,
    }


def _run(stub_env, args):
    return subprocess.run(
        ["bash", SCRIPT, *args],
        env=stub_env["env"],
        capture_output=True,
        text=True,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# Argument validation (real `set -u` positional-parameter behavior)
# ---------------------------------------------------------------------------

def test_missing_all_args_fails_fast_under_set_u(stub_env):
    proc = _run(stub_env, [])
    assert proc.returncode != 0
    # Nothing should have reached python3/systemctl at all.
    assert stub_env["call_log"].read_text() == ""
    assert stub_env["systemctl_log"].read_text() == ""


def test_missing_prompt_file_arg_fails_fast(stub_env):
    proc = _run(stub_env, ["Some title", "some-repo"])
    assert proc.returncode != 0
    assert stub_env["call_log"].read_text() == ""


# ---------------------------------------------------------------------------
# EMERGENCY_STOP gate -- real, security-relevant early-exit control
# ---------------------------------------------------------------------------

def test_emergency_stop_present_blocks_dispatch_with_no_downstream_calls(stub_env, tmp_path):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Do the docworker task.")
    sentinel = tmp_path / "emergency_stop_sentinel"
    sentinel.write_text("{}")
    stub_env["env"]["FAKE_EMERGENCY_STOP_PATH"] = str(sentinel)

    proc = _run(stub_env, ["Test Title", "test-repo", str(prompt_file)])

    assert proc.returncode == 1
    assert "EMERGENCY_STOP sentinel present" in proc.stderr
    assert str(sentinel) in proc.stderr
    # The -c resource_governor lookup is expected to have run (that's how
    # the script determines whether to block) -- but create must never have
    # been reached.
    calls = stub_env["call_log"].read_text()
    assert "veridian-task.py" not in calls
    assert stub_env["systemctl_log"].read_text() == ""


def test_emergency_stop_absent_proceeds_past_the_gate(stub_env, tmp_path):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Do the docworker task.")
    # FAKE_EMERGENCY_STOP_PATH already points at a nonexistent file (default fixture state).
    stub_env["env"]["FAKE_CREATE_SHOULD_SUCCEED"] = "0"  # force early stop right after, for a focused assertion

    proc = _run(stub_env, ["Test Title", "test-repo", str(prompt_file)])

    calls = stub_env["call_log"].read_text()
    assert "veridian-task.py" in calls
    assert "--title" in calls and "Test Title" in calls
    assert "--repo" in calls and "test-repo" in calls


# ---------------------------------------------------------------------------
# CREATE_OUT / TASK_ID parsing
# ---------------------------------------------------------------------------

def test_create_output_with_no_created_line_dies_silently_before_its_own_error_message(stub_env, tmp_path):
    """GENUINE BUG (dispatch-docworker-task.sh:37-40): the script's intended
    behavior is 'no CREATED: line -> print a helpful "ERROR: could not parse
    TASK_ID from create output" to stderr and exit 1' (see the `if [ -z
    "$TASK_ID" ]` block). But TASK_ID is populated via
    `TASK_ID=$(echo "$CREATE_OUT" | grep '^CREATED:' | sed ...)` under this
    script's own `set -euo pipefail` (line 11). When CREATE_OUT has no
    CREATED: line, `grep` exits 1 (no match); pipefail propagates that
    failure as the pipeline's exit status even though the trailing `sed`
    itself exits 0 on empty input; and `set -e` then terminates the script
    immediately at that assignment line -- before the `if [ -z "$TASK_ID" ]`
    check, and therefore before its own diagnostic message, ever runs. The
    real, observed result: the script does exit 1 (accidentally the
    "correct" code), but completely silently -- the human-readable error
    message at line 39 is dead code, unreachable for the exact failure mode
    it was written to explain. Verified directly with `bash -x` against the
    real script (see this test's docstring investigation) before writing
    this regression test; not fixed here per instructions."""
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Do the docworker task.")
    stub_env["env"]["FAKE_CREATE_SHOULD_SUCCEED"] = "0"

    proc = _run(stub_env, ["Test Title", "test-repo", str(prompt_file)])

    assert proc.returncode == 1
    assert "some unrelated create output with no CREATED line" in proc.stdout
    # The bug: this intended message is NEVER printed, because `set -e`
    # kills the script at the TASK_ID=$(...) pipeline before this line runs.
    assert "could not parse TASK_ID from create output" not in proc.stderr
    assert proc.stderr == ""
    # Never reached systemctl since the script died at the TASK_ID line.
    assert stub_env["systemctl_log"].read_text() == ""


def test_create_output_echoed_verbatim_to_stdout(stub_env, tmp_path):
    """CREATE_OUT is echoed before TASK_ID is even parsed -- real visible
    behavior a caller/log-reader depends on."""
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Do the docworker task.")
    stub_env["env"]["FAKE_CREATE_SHOULD_SUCCEED"] = "1"
    stub_env["env"]["FAKE_TASK_ID"] = "TEST-ECHO-CHECK-xyz"

    proc = _run(stub_env, ["Test Title", "test-repo", str(prompt_file)])

    assert "CREATED: TEST-ECHO-CHECK-xyz" in proc.stdout


# ---------------------------------------------------------------------------
# Wrong-unit stop/disable sequencing (the actual gap this script exists to close)
# ---------------------------------------------------------------------------

def test_wrong_unit_stopped_and_disabled_before_task_yaml_is_touched(stub_env, tmp_path):
    """This is the script's whole reason for existing: stop (not just
    disable) the wrong veridian-worker@ unit before ever touching
    task.yaml. We verify the real argv order the script issues to
    systemctl, and that the script legitimately halts (via `set -e` on a
    real sed failure against a task.yaml path that only veridian-task.py's
    real, un-stubbed side effect would have created) before ever reaching
    the correct unit's enable/start -- proving stop+disable of the wrong
    unit really does happen first, exactly as documented, without this test
    creating anything under the live /opt/veridian/ai-os/tasks/ tree."""
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Do the docworker task.")
    stub_env["env"]["FAKE_CREATE_SHOULD_SUCCEED"] = "1"
    task_id = "TEST-TASKID-NOT-A-REAL-TASK-abc123"
    stub_env["env"]["FAKE_TASK_ID"] = task_id

    proc = _run(stub_env, ["Test Title", "test-repo", str(prompt_file)])

    systemctl_calls = stub_env["systemctl_log"].read_text().splitlines()
    assert any(f"stop veridian-worker@{task_id}.service" in line for line in systemctl_calls)
    assert any(f"disable veridian-worker@{task_id}.service" in line for line in systemctl_calls)
    # stop must come before disable, matching the script's documented order.
    stop_idx = next(i for i, l in enumerate(systemctl_calls) if "stop veridian-worker@" in l)
    disable_idx = next(i for i, l in enumerate(systemctl_calls) if "disable veridian-worker@" in l)
    assert stop_idx < disable_idx

    # The script then fails for real (set -euo pipefail) at the sed step,
    # because /opt/veridian/ai-os/tasks/<task_id>/task.yaml genuinely does
    # not exist in this test (only real veridian-task.py, which we
    # deliberately did not invoke, would have created it).
    assert proc.returncode != 0
    # And therefore the CORRECT unit's enable/start must never have been
    # reached -- the wrong-unit stop/disable step is provably before, and
    # gated ahead of, ever bringing up the correct unit.
    assert not any(f"enable veridian-docworker@{task_id}.service" in line for line in systemctl_calls)
    assert not any(f"start veridian-docworker@{task_id}.service" in line for line in systemctl_calls)
