#!/usr/bin/env python3
"""Real tests for the 2026-08-13 RCA fix (task-20260813-131054-stop-fleet-wide-worker-
crash-loop--missi, addendum to UMR-20260806-171945-5767 / PR #249) to
systemd/veridian-worker@.service's ExecStopPost.

Incident this closes: PR #249 merged worker-exit-status-bridge.py and wired it in as
`ExecStopPost=/opt/veridian/scripts/worker-exit-status-bridge.py %i`, but the file was
never deployed to the live tree. Every worker stop then hit
`Control process exited, code=exited, status=203/EXEC` -- and because systemd's
Restart=on-failure decision is driven by the unit's overall Result (which a failing
CONTROL process sets to 'exit-code' regardless of ExecMainStatus, not just the main
process's own exit code), already-finished workers were restarted from ExecStart,
re-running already-completed work, up to StartLimitBurst=3 times, then permanently
failing ("Start request repeated too quickly"). Confirmed live on task-20260813-104547
and task-20260813-123933 (see this task's own PROGRESS.md for full journalctl evidence).

The fix wraps ExecStopPost in a shell command that always `exit 0`s regardless of what
the wrapped script does -- including if it can't even exec. Two layers, same "parse the
real file, then genuinely exec a scratch systemd unit" convention
test_worker_exit_status_bridge.py's own test_real_systemd_stop_fires_the_bridge_end_to_end
already established for this same ExecStopPost mechanism:
  1. `test_unit_file_execstoppost_is_wrapped_to_always_exit_0` -- static: parses the real
     systemd/veridian-worker@.service and asserts the ExecStopPost line is structurally a
     `/bin/sh -c '...; exit 0'` wrapper, so this can't silently regress back to a bare
     invocation.
  2. `test_real_systemd_stop_with_missing_target_does_not_fail_unit_or_restart` -- a real,
     private, throwaway systemd --user unit (never collides with a real
     veridian-worker@ instance) whose ExecStopPost is the REAL wrapper line extracted
     mechanically from the real unit file, with only the script path substituted for a
     deliberately nonexistent one -- i.e. it genuinely reproduces the exact 203/EXEC
     failure mode this incident hit, through the real fixed wrapper, and proves the
     unit's Result stays 'success', ActiveState reaches inactive/dead, and no restart is
     ever scheduled.
"""
import os
import re
import subprocess
import sys
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNIT_FILE = os.path.join(REPO_ROOT, "systemd", "veridian-worker@.service")

SYSTEMCTL_BIN = __import__("shutil").which("systemctl")


def _read_unit_file():
    with open(UNIT_FILE) as f:
        return f.read()


def _execstoppost_line():
    contents = _read_unit_file()
    for line in contents.splitlines():
        if line.startswith("ExecStopPost="):
            return line
    raise AssertionError(f"no ExecStopPost= line found in {UNIT_FILE}")


def test_unit_file_execstoppost_is_wrapped_to_always_exit_0():
    line = _execstoppost_line()
    assert line.startswith("ExecStopPost=/bin/sh -c '"), (
        f"ExecStopPost is no longer wrapped in a shell that can swallow an exec "
        f"failure -- a missing/broken worker-exit-status-bridge.py would once again "
        f"hit 203/EXEC and, via Restart=on-failure, re-run already-completed work. "
        f"Real line: {line!r}"
    )
    assert line.rstrip().endswith("exit 0' _ %i"), (
        f"ExecStopPost wrapper no longer unconditionally exits 0 as its last shell "
        f"statement -- real line: {line!r}"
    )
    assert "worker-exit-status-bridge.py" in line, (
        "ExecStopPost wrapper no longer invokes the real exit-status bridge script"
    )
    assert '"$1"' in line and ' _ %i' in line, (
        "ExecStopPost wrapper no longer passes the real task id (%i) through to the "
        "wrapped script"
    )


@pytest.mark.skipif(SYSTEMCTL_BIN is None, reason="systemctl not available")
def test_real_systemd_stop_with_missing_target_does_not_fail_unit_or_restart():
    """Genuinely reproduce the 203/EXEC failure mode this incident hit -- through the
    real, fixed wrapper line lifted mechanically from the real unit file -- and prove it
    no longer fails the unit or triggers Restart=on-failure."""
    real_line = _execstoppost_line()
    missing_script = "/opt/veridian/scripts/worker-exit-status-bridge-DOES-NOT-EXIST-e2e-test.py"
    assert not os.path.exists(missing_script)
    wrapped_line = real_line.replace(
        "/opt/veridian/scripts/worker-exit-status-bridge.py", missing_script)
    assert wrapped_line != real_line

    unit_prefix = "veridian-selftest-execstoppost-wrapper"
    template_name = f"{unit_prefix}@.service"
    unit_dir = os.path.expanduser("~/.config/systemd/user")
    template_path = os.path.join(unit_dir, template_name)
    instance_name = f"{unit_prefix}@203exec-e2e-test.service"

    scratch_unit_contents = (
        "[Unit]\n"
        "Description=VERIDIAN self-test ExecStopPost-never-fails-unit E2E (safe to delete)\n\n"
        "[Service]\n"
        "Type=simple\n"
        "ExecStart=/bin/sh -c 'sleep 0.2; exit 0'\n"
        f"{wrapped_line}\n"
        "Restart=on-failure\n"
        "RestartSec=2\n"
    )
    with open(template_path, "w") as f:
        f.write(scratch_unit_contents)
    # Real wall-clock marker captured before this run's own start, so the journalctl
    # assertions below are scoped to THIS run only -- the instance_name is a fixed,
    # reused string across test runs (same convention test_worker_exit_status_bridge.py's
    # own scratch units use), so without --since a re-run would pick up a prior run's
    # own journal history for the same unit name.
    since_ts = subprocess.run(["date", "+%Y-%m-%d %H:%M:%S"], capture_output=True, text=True,
                               timeout=15, check=True).stdout.strip()
    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, timeout=15)
        r = subprocess.run(["systemctl", "--user", "start", instance_name],
                            capture_output=True, text=True, timeout=15)
        assert r.returncode == 0, f"real scratch unit failed to start: {r.stderr}"

        # Give the real ExecMain + real (broken-target) ExecStopPost time to run, and
        # give systemd a real window in which it WOULD have scheduled a restart if the
        # wrapper had not swallowed the 203/EXEC.
        deadline = time.time() + 8
        show = {}
        while time.time() < deadline:
            out = subprocess.run(
                ["systemctl", "--user", "show", instance_name,
                 "--property=ActiveState,SubState,Result,NRestarts"],
                capture_output=True, text=True, timeout=15,
            ).stdout
            show = dict(line.split("=", 1) for line in out.strip().splitlines() if "=" in line)
            if show.get("ActiveState") == "inactive":
                break
            time.sleep(0.3)

        assert show.get("ActiveState") == "inactive", (
            f"unit did not settle to inactive (still {show.get('ActiveState')}/"
            f"{show.get('SubState')}) -- real properties: {show}"
        )
        assert show.get("SubState") == "dead", f"real properties: {show}"
        assert show.get("Result") == "success", (
            f"a failing ExecStopPost (203/EXEC against a deliberately-missing script) "
            f"still flipped this unit's Result to {show.get('Result')!r} instead of "
            f"'success' -- the wrapper did not swallow the control-process failure. "
            f"real properties: {show}"
        )
        assert show.get("NRestarts") == "0", (
            f"unit was restarted ({show.get('NRestarts')} times) even though ExecMain "
            f"already completed -- exactly the already-completed-work-re-run bug this "
            f"fix closes. real properties: {show}"
        )

        journal = subprocess.run(
            ["journalctl", "--user", "-u", instance_name, "--no-pager", "--since", since_ts],
            capture_output=True, text=True, timeout=15,
        ).stdout
        assert "203/EXEC" not in journal, (
            f"journal shows a real 203/EXEC control-process failure reaching systemd "
            f"despite the wrapper (it should be swallowed inside /bin/sh -c, never "
            f"forked/exec'd by systemd itself): {journal}"
        )
        assert "Failed with result" not in journal, (
            f"journal shows a real unit-level failure despite the wrapper: {journal}"
        )
        assert "Scheduled restart job" not in journal, (
            f"systemd scheduled a restart despite the always-exit-0 wrapper -- "
            f"already-completed work would be re-run. journal: {journal}"
        )
    finally:
        subprocess.run(["systemctl", "--user", "stop", instance_name], capture_output=True, timeout=15)
        subprocess.run(["systemctl", "--user", "reset-failed", instance_name], capture_output=True, timeout=15)
        if os.path.exists(template_path):
            os.remove(template_path)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=15)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
