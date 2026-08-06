#!/usr/bin/env python3
"""Real tests for build_lock_liveness_guard.py (UMR-20260806-121640-bee5,
governing UMR-20260806-071025-1d28, folding into the already-completed
UMR-20260806-120603-217b / pm_decisions_pending row 62 /
UMR-20260806-121247-a93a).

No mocked /proc, no fake process trees: every test spawns REAL processes
(via the real `flock` binary against a real temp lock file) and drives the
guard's real functions against the real, live /proc filesystem, exactly as
it will run in production against /tmp/veridian-quality-gate-build.lock.
Only the sampling window / idle-window-count / grace-period knobs are
turned down from their real production defaults (90s / 2 windows / 30s) so
these tests finish in a few seconds instead of a few minutes -- the same
mechanism, at the same real thresholds' *shape*, just compressed in time.

Covers exactly the three real scenarios UMR-20260806-121640-bee5 step 7
requires:
  (a) a synthetic genuinely-idle whole-tree holder is detected and
      released within the (compressed) real threshold.
  (b) a synthetic holder with a busy CHILD process (mirroring exactly what
      step 1's live re-verification of the PM-cited PID 3340115 found --
      idle top-level wrapper PID, real CPU-burning child) is correctly
      NOT killed.
  (c) negative control -- a normal, quickly-completing build (lock already
      free, or freed mid-window) is never touched.
Plus one extra real coverage case: SIGTERM-ignoring holder genuinely
escalates to SIGKILL after the grace period (proves the escalation path
itself, not just detection).
"""
import os
import signal
import subprocess
import sys
import tempfile
import time

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)

import build_lock_liveness_guard as guard  # noqa: E402

FLOCK_BIN = "/usr/bin/flock" if os.path.exists("/usr/bin/flock") else "flock"


def _spawn_holder(lock_path, inner_cmd):
    """Real `flock <lock_path> -c '<inner_cmd>'`, in its own real process
    group (mirrors quality-gate.sh's own real invocation shape: a `flock`
    parent that forks/execs the wrapped command as a real child)."""
    return subprocess.Popen(
        [FLOCK_BIN, lock_path, "-c", inner_cmd],
        start_new_session=True,
    )


def _wait_gone(pid, proc_root, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not os.path.exists(os.path.join(proc_root, str(pid))):
            return True
        time.sleep(0.05)
    return False


def _cleanup(proc, pgid=None):
    try:
        if pgid is not None:
            os.killpg(pgid, signal.SIGKILL)
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


@pytest.fixture
def lock_path():
    fd, path = tempfile.mkstemp(prefix="test-build-lock-liveness-")
    os.close(fd)
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------
# (a) genuinely idle whole-tree holder -> detected and released
# ---------------------------------------------------------------------
def test_genuinely_idle_holder_is_detected_and_killed(lock_path):
    # Real holder: flock parent + a real child (`sleep`) that does
    # genuinely zero CPU work for the whole test -- the whole-tree-idle
    # case this guard exists to catch.
    proc = _spawn_holder(lock_path, "sleep 300")
    try:
        # Real holder identification must resolve to the real flock PID,
        # not fuser-style "any PID with the fd open".
        time.sleep(0.2)
        holder_pid = guard.find_lock_holder_pid(lock_path)
        assert holder_pid == proc.pid

        tree = guard.list_process_tree(holder_pid)
        assert holder_pid in tree
        assert len(tree) >= 2  # flock + its real sleep child

        result = guard.run_guard_once(
            lock_path=lock_path,
            window_seconds=0.3,
            required_idle_windows=2,
            grace_seconds=2,
        )
        assert result["action"] == "killed", result
        assert result["holder_pid"] == proc.pid
        assert len(result["windows_observed"]) == 2
        assert all(w["status"] == "idle" for w in result["windows_observed"])

        # Real process is actually gone afterward -- the lock is released,
        # not just "reported as killed". Our own test harness is the
        # direct parent of the real `flock` process (no double-fork), so
        # (exactly like a real init/service-manager parent would) it must
        # reap the exited child itself before the kernel fully drops its
        # /proc entry -- a killed-but-unreaped child is still a real,
        # functionally-dead zombie, not evidence the kill failed.
        proc.wait(timeout=5)
        assert _wait_gone(proc.pid, "/proc", timeout=5)
        assert guard.find_lock_holder_pid(lock_path) is None
    finally:
        _cleanup(proc)


# ---------------------------------------------------------------------
# (b) busy CHILD, idle top-level wrapper -> must NOT be killed
# ---------------------------------------------------------------------
def test_holder_with_busy_child_is_not_killed(lock_path):
    # Mirrors step 1's real finding exactly: the flock/wrapper process
    # itself does ~nothing (it is just waiting on its own child), while
    # the real child burns real CPU. A guard keyed on the wrapper's own
    # CPU% alone would misclassify this as hung; the whole-tree sum must
    # not.
    busy_child = "python3 -c \"\nimport time\nend = time.time() + 30\nwhile time.time() < end:\n    pass\n\""
    proc = _spawn_holder(lock_path, busy_child)
    try:
        time.sleep(0.3)
        holder_pid = guard.find_lock_holder_pid(lock_path)
        assert holder_pid == proc.pid

        result = guard.run_guard_once(
            lock_path=lock_path,
            window_seconds=0.5,
            required_idle_windows=2,
            grace_seconds=2,
        )
        assert result["action"] == "none", result
        assert result["reason"] == "busy"
        assert result["windows_observed"][0]["status"] == "busy"
        assert result["windows_observed"][0]["cpu_delta_ticks"] > guard.DEFAULT_IDLE_THRESHOLD_TICKS

        # Real process is still alive and still the real holder -- the
        # guard took no action against real, live work.
        assert proc.poll() is None
        assert guard.find_lock_holder_pid(lock_path) == proc.pid
    finally:
        _cleanup(proc, pgid=proc.pid)


# ---------------------------------------------------------------------
# (c) negative control -- normal, quickly-completing build
# ---------------------------------------------------------------------
def test_quickly_completing_build_never_touched(lock_path):
    proc = _spawn_holder(lock_path, "true")
    proc.wait(timeout=5)  # real completion, lock already released by now

    result = guard.run_guard_once(
        lock_path=lock_path,
        window_seconds=0.3,
        required_idle_windows=2,
        grace_seconds=2,
    )
    assert result["action"] == "none"
    assert result["reason"] == "no_holder"


def test_build_that_completes_mid_window_never_touched(lock_path):
    # Real holder still running when the first window starts, but it
    # exits partway through the window -- must be read as "holder
    # changed/gone", never counted as an idle window.
    proc = _spawn_holder(lock_path, "sleep 0.2")
    result = guard.check_holder_liveness(lock_path, window_seconds=1.5)
    assert result["status"] in ("holder_changed", "no_holder")
    proc.wait(timeout=5)


# ---------------------------------------------------------------------
# Extra real coverage: SIGTERM-ignoring holder genuinely escalates to
# SIGKILL after the real grace period (proves the escalation path, not
# just detection).
# ---------------------------------------------------------------------
def test_sigterm_ignoring_holder_escalates_to_sigkill(lock_path):
    ignore_term = "trap '' TERM; sleep 300"
    proc = _spawn_holder(lock_path, "bash -c \"%s\"" % ignore_term)
    try:
        time.sleep(0.3)
        holder_pid = guard.find_lock_holder_pid(lock_path)
        # The real holder is the flock parent, which does NOT trap TERM,
        # so it dies from the initial SIGTERM -- but its trapping child
        # (also in the real process group) must still be reaped by the
        # real SIGKILL escalation once the grace period elapses.
        assert holder_pid is not None
        pgid = guard.read_pgid(holder_pid)
        tree_before = guard.list_process_tree(holder_pid)
        assert len(tree_before) >= 2

        result = guard.run_guard_once(
            lock_path=lock_path,
            window_seconds=0.3,
            required_idle_windows=2,
            grace_seconds=1.5,
        )
        assert result["action"] == "killed", result
        assert result["kill_result"]["sigterm_sent"] is True
        # The whole point of this test: SIGTERM alone was not enough (the
        # child traps it), so real escalation to SIGKILL must have fired.
        assert result["kill_result"]["sigkill_sent"] is True

        # Real: the flock parent (our direct child, reaped here same as
        # the other tests) is actually gone. Its trapping grandchild is
        # reparented on exit (we never double-forked it either); the real
        # SIGKILL already reached it via the real process group, so it is
        # dead even if not yet reaped by whichever process now owns it --
        # poll its /proc state rather than requiring a /proc disappearance
        # we are not positioned to guarantee here.
        proc.wait(timeout=5)
        assert _wait_gone(proc.pid, "/proc", timeout=5)

        def _dead_or_zombie(pid):
            stat_path = "/proc/%d/stat" % pid
            if not os.path.exists(stat_path):
                return True
            try:
                with open(stat_path) as f:
                    raw = f.read()
            except OSError:
                return True
            rparen = raw.rfind(")")
            state = raw[rparen + 2:].split()[0]
            return state == "Z"

        deadline = time.time() + 5
        remaining = set(tree_before) - {proc.pid}
        while time.time() < deadline and remaining:
            remaining = {p for p in remaining if not _dead_or_zombie(p)}
            if not remaining:
                break
            time.sleep(0.1)
        assert not remaining, "real process group members still alive and running: %s" % remaining
    finally:
        _cleanup(proc)
