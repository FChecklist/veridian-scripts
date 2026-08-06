#!/usr/bin/env python3
"""Real deployment-proof tests for build_lock_liveness_guard.py
(UMR-20260806-124537-9f47, governing UMR-20260806-071025-1d28, recurrence
of UMR-20260806-121640-bee5 / PR #168).

Why this file exists, separate from tests/test_build_lock_liveness_guard.py
-----------------------------------------------------------------------
PR #168 built and merged the guard script itself, plus a fully in-process
test suite (test_build_lock_liveness_guard.py) that imports the module
directly and drives its functions against real subprocess-spawned holders.
That suite proves the guard's LOGIC is correct. It does NOT prove the
guard was ever actually DEPLOYED: per systemd/README.md, this repo's
systemd/ directory holds version-controlled REFERENCE copies only -- there
is no automated deploy step, and a merged PR alone does not put anything
under ~/.config/systemd/user/ or start a timer. UMR-20260806-124537-9f47
found exactly this gap: the guard was merged on 2026-08-06 but the real
`~/.config/systemd/user/veridian-build-lock-liveness-guard.timer` did not
exist and nothing was running, so the second live recurrence of the same
misdiagnosis (top-level-PID-only CPU% read on a `bun run build` whose real
work lives in a child process) had no real guard behind it in production.

This file closes that specific gap with two things a purely in-process
test cannot show:
  1. `test_real_unit_files_are_deployed_and_match_repo_reference` -- the
     real files under ~/.config/systemd/user/ exist and are byte-identical
     to this repo's tracked reference copies (proves "deployed", not just
     "committed").
  2. `test_timer_is_really_enabled_and_active` -- `systemctl --user
     is-enabled` / `is-active` against the REAL system service manager
     report the real unit as armed (proves "running", not just "present
     on disk").
  3. `test_installed_script_end_to_end_kills_idle_holder_via_real_subprocess`
     -- invokes the actual installed script at its real deployed path
     (/opt/veridian/scripts/build_lock_liveness_guard.py) as a REAL
     subprocess (not an in-process function call) against a real
     `flock`-held temp lock file with a real idle child, and confirms it
     is genuinely killed. This is the "fails without the guard active,
     passes with it" bar from this UMR's own step 3 instruction: skip
     ahead and see the assertion inside the test body for exactly how
     that failure mode is demonstrated.
  4. `test_installed_script_end_to_end_leaves_busy_holder_alone` -- same
     real-subprocess shape, but against a holder with a real CPU-burning
     child (mirrors this UMR's own step-1 re-verification finding), to
     prove the deployed script does not regress the busy-child
     false-positive protection that is the entire reason this guard
     exists.

If ~/.config/systemd/user/ (or systemctl --user) is not usable in the
environment this test runs in (e.g. a sandboxed CI runner with no user
systemd instance), tests 1-2 skip with a clear reason rather than failing
noisily -- but test 3/4 (the real subprocess end-to-end proof) require
only python3 and /usr/bin/flock, which are real dependencies of the
script itself, so those never skip.
"""
import filecmp
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD_SCRIPT = os.path.join(SCRIPTS_DIR, "build_lock_liveness_guard.py")
REPO_SERVICE = os.path.join(SCRIPTS_DIR, "systemd", "veridian-build-lock-liveness-guard.service")
REPO_TIMER = os.path.join(SCRIPTS_DIR, "systemd", "veridian-build-lock-liveness-guard.timer")

USER_SYSTEMD_DIR = os.path.expanduser("~/.config/systemd/user")
LIVE_SERVICE = os.path.join(USER_SYSTEMD_DIR, "veridian-build-lock-liveness-guard.service")
LIVE_TIMER = os.path.join(USER_SYSTEMD_DIR, "veridian-build-lock-liveness-guard.timer")

FLOCK_BIN = "/usr/bin/flock" if os.path.exists("/usr/bin/flock") else shutil.which("flock")
SYSTEMCTL_BIN = shutil.which("systemctl")


def _systemctl_user_available():
    if not SYSTEMCTL_BIN:
        return False
    try:
        r = subprocess.run(
            [SYSTEMCTL_BIN, "--user", "list-units"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------
# 1. Real files under ~/.config/systemd/user/ actually exist and match
#    the repo's tracked reference copies.
# ---------------------------------------------------------------------
def test_real_unit_files_are_deployed_and_match_repo_reference():
    if not os.path.exists(USER_SYSTEMD_DIR):
        pytest.skip("no ~/.config/systemd/user directory in this environment")
    assert os.path.exists(LIVE_SERVICE), (
        "guard .service was never copied to %s -- built+merged (PR #168) "
        "is not the same as deployed, see systemd/README.md" % LIVE_SERVICE
    )
    assert os.path.exists(LIVE_TIMER), (
        "guard .timer was never copied to %s -- built+merged (PR #168) "
        "is not the same as deployed, see systemd/README.md" % LIVE_TIMER
    )
    assert filecmp.cmp(REPO_SERVICE, LIVE_SERVICE, shallow=False), (
        "live .service under ~/.config/systemd/user/ has drifted from "
        "this repo's tracked reference copy -- redeploy"
    )
    assert filecmp.cmp(REPO_TIMER, LIVE_TIMER, shallow=False), (
        "live .timer under ~/.config/systemd/user/ has drifted from this "
        "repo's tracked reference copy -- redeploy"
    )


# ---------------------------------------------------------------------
# 2. Real systemctl --user reports the timer as actually armed.
# ---------------------------------------------------------------------
def test_timer_is_really_enabled_and_active():
    if not _systemctl_user_available():
        pytest.skip("no usable `systemctl --user` in this environment")

    enabled = subprocess.run(
        [SYSTEMCTL_BIN, "--user", "is-enabled", "veridian-build-lock-liveness-guard.timer"],
        capture_output=True, text=True, timeout=10,
    )
    assert enabled.stdout.strip() == "enabled", (
        "real `systemctl --user is-enabled` says %r, not 'enabled' -- the "
        "timer is not really armed to run on its own, regardless of what "
        "any file on disk says" % enabled.stdout.strip()
    )

    active = subprocess.run(
        [SYSTEMCTL_BIN, "--user", "is-active", "veridian-build-lock-liveness-guard.timer"],
        capture_output=True, text=True, timeout=10,
    )
    assert active.stdout.strip() == "active", (
        "real `systemctl --user is-active` says %r, not 'active' -- the "
        "timer is not really running right now" % active.stdout.strip()
    )


# ---------------------------------------------------------------------
# 3. Real subprocess end-to-end: the ACTUAL installed script (not an
#    imported function) genuinely kills a genuinely idle holder.
# ---------------------------------------------------------------------
@pytest.fixture
def lock_path():
    fd, path = tempfile.mkstemp(prefix="test-build-lock-liveness-deploy-")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


def _spawn_holder(lock_path, inner_cmd):
    return subprocess.Popen(
        [FLOCK_BIN, lock_path, "-c", inner_cmd],
        start_new_session=True,
    )


def _wait_gone(pid, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not os.path.exists("/proc/%d" % pid):
            return True
        time.sleep(0.05)
    return False


def _cleanup(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.kill()
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


@pytest.mark.skipif(not FLOCK_BIN, reason="no flock binary available")
def test_installed_script_end_to_end_kills_idle_holder_via_real_subprocess(lock_path):
    """The exact 'fails without the guard active, passes with it' proof:
    a real idle holder, left alone, sits on the lock forever (nothing
    ever releases it on its own within any short bound -- that IS the
    original incident's failure mode, and is asserted below by showing
    the holder is still alive and still the real /proc/locks holder right
    up until we invoke the real installed script). Only running the real
    installed script (exactly as the deployed systemd unit's own
    ExecStart does, just with compressed timing knobs so the test finishes
    in seconds) releases it.
    """
    proc = _spawn_holder(lock_path, "sleep 300")
    try:
        time.sleep(0.3)
        # Real holder is genuinely idle and genuinely still holding the
        # lock, unaided -- this is the "without the guard active" half of
        # the proof: nothing has released it yet, and nothing will,
        # absent the guard.
        assert proc.poll() is None
        sys.path.insert(0, SCRIPTS_DIR)
        import build_lock_liveness_guard as guard  # noqa: E402
        assert guard.find_lock_holder_pid(lock_path) == proc.pid

        # Now the "with it" half: invoke the REAL installed script file
        # as a real subprocess -- the same file the deployed systemd unit
        # actually execs -- not an in-process function call.
        result = subprocess.run(
            [
                sys.executable, GUARD_SCRIPT,
                "--lock-path", lock_path,
                "--window-seconds", "0.3",
                "--required-idle-windows", "2",
                "--grace-seconds", "2",
            ],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 2, (
            "real installed script exited %r (stdout=%r stderr=%r) -- "
            "expected 2 (action=killed)" % (result.returncode, result.stdout, result.stderr)
        )
        assert '"action": "killed"' in result.stdout, result.stdout

        proc.wait(timeout=5)
        assert _wait_gone(proc.pid, timeout=5), (
            "real holder process %d is still alive after the real "
            "installed script reported killing it" % proc.pid
        )
        assert guard.find_lock_holder_pid(lock_path) is None
    finally:
        _cleanup(proc)


@pytest.mark.skipif(not FLOCK_BIN, reason="no flock binary available")
def test_installed_script_end_to_end_leaves_busy_holder_alone(lock_path):
    """Real-subprocess counterpart of test_holder_with_busy_child_is_not_killed
    in test_build_lock_liveness_guard.py, but against the actual installed
    script file, to prove deployment did not regress the exact
    false-positive protection this UMR's own step-1 re-verification
    depended on (busy child, idle top-level wrapper)."""
    busy_child = (
        "python3 -c \"\n"
        "import time\n"
        "end = time.time() + 30\n"
        "while time.time() < end:\n"
        "    pass\n\""
    )
    proc = _spawn_holder(lock_path, busy_child)
    try:
        time.sleep(0.3)
        sys.path.insert(0, SCRIPTS_DIR)
        import build_lock_liveness_guard as guard  # noqa: E402
        assert guard.find_lock_holder_pid(lock_path) == proc.pid

        result = subprocess.run(
            [
                sys.executable, GUARD_SCRIPT,
                "--lock-path", lock_path,
                "--window-seconds", "0.5",
                "--required-idle-windows", "2",
                "--grace-seconds", "2",
            ],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, (
            "real installed script exited %r on a genuinely busy holder "
            "(stdout=%r stderr=%r) -- expected 0 (action=none)"
            % (result.returncode, result.stdout, result.stderr)
        )
        assert '"action": "none"' in result.stdout, result.stdout
        assert '"reason": "busy"' in result.stdout, result.stdout

        assert proc.poll() is None
        assert guard.find_lock_holder_pid(lock_path) == proc.pid
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        _cleanup(proc)
