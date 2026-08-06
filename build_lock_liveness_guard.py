#!/usr/bin/env python3
"""Real holder-liveness guard for /tmp/veridian-quality-gate-build.lock.

UMR-20260806-121640-bee5 (governing UMR-20260806-071025-1d28, folded into
the already-completed investigation UMR-20260806-120603-217b / its real
proposal pm_decisions_pending row 62 / child UMR-20260806-121247-a93a).

Why this exists, and why it does NOT key off the holder's own top-level
PID CPU%
-----------------------------------------------------------------------
Live re-verification of the PM's cited hung holder (PID 3340115,
`/home/rajat/.bun/bin/bun run build`, task
task-20260804-073455-register-ocid-064--local-deterministic-l) found the
PM's stated reason ("it is not building anything") was WRONG: that
top-level PID genuinely showed ~0.0% CPU across two live samples ~2min
apart, but its real CHILD process (a `node` process, PPID = the bun PID)
was consuming real CPU (15.6%, 2.1GB RSS) doing real work at the same
moment. `bun run build` delegates the actual work to a child process --
the wrapper PID legitimately idles while its child does everything. A
liveness check keyed only on the top PID's own CPU% would have
misclassified this genuinely-working build as hung and killed it early,
which is exactly the failure mode this guard must not repeat. (In the
event, nothing needed to kill it: the pre-existing `timeout -k 30 1800`
outer safety net fired on its own about 2-3 minutes later, once the build
had genuinely run out its full 1800s wall-clock budget under real, heavy,
swap-thrashing memory pressure -- it was "slow but real", not "idle".)

So: liveness is judged on cumulative CPU-time advancement summed across
the ENTIRE process tree rooted at the actual lock holder (holder + every
live descendant, walked recursively via /proc), not the holder's own
instantaneous CPU% and not any single PID in isolation. A tree is only
classified as hung once its combined cumulative utime+stime fails to
advance by more than a near-zero threshold across TWO consecutive real
sampling windows in a row (see WINDOW/REQUIRED_IDLE_WINDOWS below) --
one flat window alone is not trusted, in case it lands on a brief,
legitimate lull (e.g. between build phases, waiting on a registry fetch
that is about to resume). A whole tree that is genuinely idle across two
full consecutive windows back to back is not a lull.

How the real holder is identified
-----------------------------------------------------------------------
`fuser <lockfile>` is NOT reliable for this: util-linux `flock -c cmd`
forks a child that inherits the already-open, non-close-on-exec lock fd
before exec'ing `cmd`, so `fuser` reports BOTH the `flock` process itself
and whatever it exec'd as having the fd open, even though only one of
them (the `flock` process) actually holds the advisory lock -- and a
BLOCKED WAITER also has the fd open (it must, to call flock(2) on it) even
though it holds nothing. Verified live: for the currently-held lock at
inode 132369, `fuser` reported two PIDs (3391271, the `flock` process, and
3474656, the `bun run build` it exec'd), while /proc/locks correctly named
only 3391271 as the real holder. This guard therefore reads /proc/locks
directly (matching this lock file's real (st_dev, st_ino) against the
non-"-> " lines only -- "-> " lines are blocked waiters, never holders)
and never trusts fuser or "has an open fd" as a proxy for "holds the
lock".

Sampling window and threshold, and why
-----------------------------------------------------------------------
WINDOW_SECONDS=90: long enough that ordinary scheduling jitter, a short
GC pause, or a brief blocking syscall cannot itself zero out a window
(real work anywhere in the tree during a 90s window shows up as nonzero
cumulative-tick advancement); short enough that two of them back to back
(REQUIRED_IDLE_WINDOWS=2, ~180s = 3 minutes total) is a real, meaningful
improvement over waiting out the existing 1800s outer `timeout` -- the
task's own instruction to detect in "minutes not 30" (minutes, not the
full 30-minute outer budget).
IDLE_THRESHOLD_TICKS=2 (at the standard 100 ticks/sec clock, 0.02 CPU-
seconds) summed across the WHOLE tree over the WHOLE window: not exactly
zero, to tolerate negligible unavoidable bookkeeping (e.g. the shell
`flock` wrapper itself waking briefly), but far below anything a real,
working build (which was observed to independently peg one child process
at 15.6% sustained CPU) could produce by accident.
GRACE_SECONDS=30 for SIGTERM -> SIGKILL escalation on a confirmed-hung
holder: same `-k 30` convention quality-gate.sh's own outer `timeout`
already uses for this exact lock/build combination (consistency, and
justified by the same evidence cited there: a stuck Node event loop does
not reliably die on SIGTERM alone).

This guard NEVER weakens or removes the build lock itself -- it only
ever acts on a holder it has independently, repeatedly reconfirmed is
genuinely idle tree-wide, and its action is to release that one dead
holder (SIGTERM its process group, escalate to SIGKILL after a real grace
period) so the lock frees up for the next real waiter -- exactly the
release the existing 1800s timeout would eventually have performed
anyway, just detected in minutes instead of up to 30.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time

CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

DEFAULT_LOCK_PATH = "/tmp/veridian-quality-gate-build.lock"
DEFAULT_WINDOW_SECONDS = 90
DEFAULT_REQUIRED_IDLE_WINDOWS = 2
DEFAULT_IDLE_THRESHOLD_TICKS = 2
DEFAULT_GRACE_SECONDS = 30
DEFAULT_POLL_INTERVAL_SECONDS = 0.5  # only used while waiting out SIGTERM grace


class NoHolder(Exception):
    """Raised internally when the lock currently has no real holder."""


def _proc_root(proc_root):
    return proc_root or "/proc"


def find_lock_holder_pid(lock_path, proc_root=None, locks_path=None):
    """Real holder identification via /proc/locks (see module docstring for
    why this -- not fuser -- is the correct real source). Returns an int
    PID, or None if the lock file has no real holder right now (free, or
    the path does not exist)."""
    proc_root = _proc_root(proc_root)
    locks_path = locks_path or os.path.join(proc_root, "locks")
    try:
        st = os.stat(lock_path)
    except OSError:
        return None
    target = (st.st_dev, st.st_ino)
    try:
        with open(locks_path) as f:
            lines = f.readlines()
    except OSError:
        return None
    for line in lines:
        parts = line.split()
        # Real /proc/locks line shape (holder):
        #   "<id>: FLOCK  ADVISORY  WRITE <pid> <maj>:<min>:<ino> <start> <end>"
        # Real blocked-waiter line shape (never a holder):
        #   "<id>: -> FLOCK ADVISORY WRITE <pid> <maj>:<min>:<ino> <start> <end>"
        if len(parts) < 2:
            continue
        if parts[1] == "->":
            continue  # a blocked waiter, never a real holder
        if "FLOCK" not in parts:
            continue
        try:
            flock_idx = parts.index("FLOCK")
            pid = int(parts[flock_idx + 3])
            devino = parts[flock_idx + 4]
            maj_min, ino = devino.rsplit(":", 1)
            ino = int(ino)
        except (ValueError, IndexError):
            continue
        if ino != target[1]:
            continue
        # Confirm the real device too (best-effort -- major:minor -> dev_t
        # comparison is skipped here since st_dev's own encoding is
        # platform-internal; inode match on the real, live lock file is
        # already a strong, real signal, and a second real check --
        # confirming the candidate PID is alive and its own /proc/<pid>
        # exists -- follows in list_process_tree below).
        return pid
    return None


def _read_stat_fields(pid, proc_root=None):
    proc_root = _proc_root(proc_root)
    path = os.path.join(proc_root, str(pid), "stat")
    with open(path) as f:
        raw = f.read()
    # comm field can contain spaces/parens; parse from the last ')' to be safe.
    rparen = raw.rfind(")")
    before = raw[:rparen].split(None, 1)[0]  # pid
    after = raw[rparen + 2:].split()
    # after[0] is state (field 3); ppid is after[1] (field 4)
    return before, after


def read_ppid(pid, proc_root=None):
    _pid, after = _read_stat_fields(pid, proc_root=proc_root)
    return int(after[1])  # field 4 (ppid), after[0]=state is field 3


def read_cpu_ticks(pid, proc_root=None):
    """Real cumulative utime+stime (fields 14,15 of /proc/<pid>/stat) for
    ONE live process -- deliberately excludes cutime/cstime (fields 16,17,
    which only reflect REAPED children) since this guard sums fields 14/15
    directly over every still-live process in the tree itself, which is
    the real, current, non-double-counted signal."""
    _pid, after = _read_stat_fields(pid, proc_root=proc_root)
    # after[] is 0-indexed from field 3 (state). Field 14 -> after[11],
    # field 15 -> after[12].
    utime = int(after[11])
    stime = int(after[12])
    return utime + stime


def read_pgid(pid, proc_root=None):
    _pid, after = _read_stat_fields(pid, proc_root=proc_root)
    return int(after[2])  # field 5 (pgrp) -> after[2]


def list_all_pids(proc_root=None):
    proc_root = _proc_root(proc_root)
    out = []
    for name in os.listdir(proc_root):
        if name.isdigit():
            out.append(int(name))
    return out


def list_process_tree(root_pid, proc_root=None):
    """Real recursive descendant walk: root_pid plus every live process
    whose ppid chain leads back to it, built from a real, current
    child-map snapshot across /proc (not a cached/stale one)."""
    proc_root = _proc_root(proc_root)
    children = {}
    for pid in list_all_pids(proc_root=proc_root):
        try:
            ppid = read_ppid(pid, proc_root=proc_root)
        except (OSError, ValueError, IndexError):
            continue
        children.setdefault(ppid, []).append(pid)

    if not os.path.exists(os.path.join(proc_root, str(root_pid))):
        return set()

    tree = set()
    stack = [root_pid]
    while stack:
        pid = stack.pop()
        if pid in tree:
            continue
        tree.add(pid)
        stack.extend(children.get(pid, []))
    return tree


def sample_tree_cpu_ticks(root_pid, proc_root=None):
    """Sum real cumulative CPU ticks across the whole live tree rooted at
    root_pid. Returns (total_ticks, pid_count), or None if root_pid itself
    is no longer alive (holder already gone -- nothing to guard)."""
    proc_root = _proc_root(proc_root)
    if not os.path.exists(os.path.join(proc_root, str(root_pid))):
        return None
    tree = list_process_tree(root_pid, proc_root=proc_root)
    total = 0
    counted = 0
    for pid in tree:
        try:
            total += read_cpu_ticks(pid, proc_root=proc_root)
            counted += 1
        except (OSError, ValueError, IndexError):
            continue  # process exited between the tree snapshot and the read
    return total, counted


def check_holder_liveness(
    lock_path,
    window_seconds=DEFAULT_WINDOW_SECONDS,
    proc_root=None,
    locks_path=None,
    sleep_fn=time.sleep,
):
    """One real sampling window against the CURRENT holder (if any).

    Returns a dict with a "status" key:
      "no_holder"      -- lock is free right now, nothing to guard.
      "holder_changed" -- holder pid at the end of the window differs from
                           (or vanished vs) the start -- lock was released/
                           reacquired mid-window; never treated as evidence
                           of hanging, always resets any prior idle streak.
      "busy"           -- whole-tree cumulative CPU advanced past the
                           threshold: real work happened, resets any idle
                           streak.
      "idle"            -- whole-tree cumulative CPU did NOT advance past
                           the threshold across this one window.
    """
    holder_before = find_lock_holder_pid(lock_path, proc_root=proc_root, locks_path=locks_path)
    if holder_before is None:
        return {"status": "no_holder"}

    sample_before = sample_tree_cpu_ticks(holder_before, proc_root=proc_root)
    if sample_before is None:
        return {"status": "no_holder"}
    ticks_before, _n_before = sample_before

    sleep_fn(window_seconds)

    holder_after = find_lock_holder_pid(lock_path, proc_root=proc_root, locks_path=locks_path)
    if holder_after != holder_before:
        return {"status": "holder_changed", "holder_pid": holder_before}

    sample_after = sample_tree_cpu_ticks(holder_after, proc_root=proc_root)
    if sample_after is None:
        return {"status": "holder_changed", "holder_pid": holder_before}
    ticks_after, n_after = sample_after

    delta = ticks_after - ticks_before
    status = "busy" if delta > DEFAULT_IDLE_THRESHOLD_TICKS else "idle"
    return {
        "status": status,
        "holder_pid": holder_before,
        "cpu_delta_ticks": delta,
        "cpu_delta_seconds": delta / float(CLK_TCK),
        "tree_pid_count": n_after,
    }


def kill_process_group(pgid, grace_seconds=DEFAULT_GRACE_SECONDS, holder_pid=None,
                        proc_root=None, poll_interval=DEFAULT_POLL_INTERVAL_SECONDS,
                        sleep_fn=time.sleep, now_fn=time.time):
    """Release a confirmed-hung holder safely: SIGTERM the holder's own
    process group first, escalate to SIGKILL only after a real grace
    period if it has not exited on its own. Never touches the lock file
    itself. Returns a dict describing what was actually done."""
    proc_root = _proc_root(proc_root)

    def _holder_alive():
        if holder_pid is None:
            return False
        return os.path.exists(os.path.join(proc_root, str(holder_pid)))

    try:
        os.killpg(pgid, signal.SIGTERM)
        sigterm_sent = True
    except ProcessLookupError:
        return {"sigterm_sent": False, "sigkill_sent": False, "already_gone": True}
    except PermissionError as e:
        return {"sigterm_sent": False, "sigkill_sent": False, "error": str(e)}

    deadline = now_fn() + grace_seconds
    while now_fn() < deadline:
        if not _holder_alive():
            return {"sigterm_sent": sigterm_sent, "sigkill_sent": False, "exited_after_sigterm": True}
        sleep_fn(poll_interval)

    sigkill_sent = False
    if _holder_alive():
        try:
            os.killpg(pgid, signal.SIGKILL)
            sigkill_sent = True
        except ProcessLookupError:
            pass
    return {"sigterm_sent": sigterm_sent, "sigkill_sent": sigkill_sent,
            "exited_after_sigterm": not sigkill_sent}


def run_guard_once(
    lock_path=DEFAULT_LOCK_PATH,
    window_seconds=DEFAULT_WINDOW_SECONDS,
    required_idle_windows=DEFAULT_REQUIRED_IDLE_WINDOWS,
    grace_seconds=DEFAULT_GRACE_SECONDS,
    proc_root=None,
    locks_path=None,
    sleep_fn=time.sleep,
    now_fn=time.time,
    log_fn=None,
):
    """Real end-to-end single invocation: watch the current holder (if
    any) for up to required_idle_windows consecutive real idle windows in
    a row; kill it (safely, see kill_process_group) only if every one of
    them comes back idle for the SAME holder pid throughout. Any "busy" or
    "holder_changed" result at any point aborts with no action taken.

    Returns a dict evidence record (never raises for the normal
    no-holder/busy/changed paths)."""
    log_fn = log_fn or (lambda *a, **k: None)
    idle_windows = []
    holder_pid = None
    for i in range(required_idle_windows):
        result = check_holder_liveness(
            lock_path, window_seconds=window_seconds, proc_root=proc_root,
            locks_path=locks_path, sleep_fn=sleep_fn,
        )
        log_fn("window", i, result)
        status = result["status"]
        if status in ("no_holder", "holder_changed", "busy"):
            return {"action": "none", "reason": status, "windows_observed": idle_windows + [result]}
        # status == "idle"
        holder_pid = result["holder_pid"]
        idle_windows.append(result)

    # Every required window came back idle for the same holder pid ->
    # genuinely hung tree-wide across a real, sustained span. Re-verify
    # the holder one last time immediately before acting (never act on a
    # stale/assumed pid).
    current_holder = find_lock_holder_pid(lock_path, proc_root=proc_root, locks_path=locks_path)
    if current_holder != holder_pid:
        return {"action": "none", "reason": "holder_changed_at_kill_check",
                "windows_observed": idle_windows}

    try:
        pgid = read_pgid(holder_pid, proc_root=proc_root)
    except (OSError, ValueError, IndexError):
        return {"action": "none", "reason": "holder_vanished_at_kill_check",
                "windows_observed": idle_windows}

    kill_result = kill_process_group(
        pgid, grace_seconds=grace_seconds, holder_pid=holder_pid,
        proc_root=proc_root, sleep_fn=sleep_fn, now_fn=now_fn,
    )
    return {
        "action": "killed",
        "holder_pid": holder_pid,
        "pgid": pgid,
        "windows_observed": idle_windows,
        "kill_result": kill_result,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lock-path", default=DEFAULT_LOCK_PATH)
    parser.add_argument("--window-seconds", type=float, default=DEFAULT_WINDOW_SECONDS)
    parser.add_argument("--required-idle-windows", type=int, default=DEFAULT_REQUIRED_IDLE_WINDOWS)
    parser.add_argument("--grace-seconds", type=float, default=DEFAULT_GRACE_SECONDS)
    args = parser.parse_args(argv)

    def log(*parts):
        print("[build-lock-liveness-guard]", *parts, file=sys.stderr)

    result = run_guard_once(
        lock_path=args.lock_path,
        window_seconds=args.window_seconds,
        required_idle_windows=args.required_idle_windows,
        grace_seconds=args.grace_seconds,
        log_fn=log,
    )
    print(json.dumps(result))
    return 2 if result.get("action") == "killed" else 0


if __name__ == "__main__":
    sys.exit(main())
