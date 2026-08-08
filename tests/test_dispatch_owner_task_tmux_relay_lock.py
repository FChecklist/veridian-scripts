#!/usr/bin/env python3
"""UMR-20260806-094226-8617: real root-cause fix for the observed
input-line-sticking finding -- two concurrent dispatch-owner-task.sh
invocations relaying into the same tmux session had no mutual exclusion
around their `tmux send-keys` calls, so their literal `-l` text sends could
genuinely interleave in the target pane's input buffer.

This test proves the fix: concurrent invocations against the same fake
tmux session serialize their relay (has-session-check + both send-keys
calls) as one atomic unit -- never interleaved -- via a real flock on a
per-session lock file under LOCKS_DIR.
"""
import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Same fake tmux shim as test_dispatch_owner_task_status_write.py, plus an
# artificial delay inside send-keys so a real race window is wide enough to
# actually observe interleaving if the lock did not exist.
FAKE_TMUX_SLOW = """#!/usr/bin/env bash
set -euo pipefail
echo "BEGIN $$ $*" >> "$TMUX_FAKE_LOG"
if [ "$1" = "has-session" ]; then
  shift
  target=""
  while [ $# -gt 0 ]; do
    if [ "$1" = "-t" ]; then target="$2"; fi
    shift
  done
  if [ -f "$TMUX_FAKE_LIVE_SESSIONS" ] && grep -qxF "$target" "$TMUX_FAKE_LIVE_SESSIONS"; then
    echo "END $$ $*" >> "$TMUX_FAKE_LOG"
    exit 0
  fi
  echo "END $$ $*" >> "$TMUX_FAKE_LOG"
  exit 1
fi
# send-keys: sleep a bit mid-call so two concurrent real invocations would
# genuinely overlap here if nothing serialized them.
sleep 0.3
echo "END $$ $*" >> "$TMUX_FAKE_LOG"
exit 0
"""


def _seed_full_schema(path):
    spec = importlib.util.spec_from_file_location(
        "sbr_seed_tmux_lock_test", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    sbr.DB_PATH = path
    sbr.init_db()


@pytest.fixture()
def relay_env():
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "scratch.sqlite")
        _seed_full_schema(db_path)

        fake_bin = os.path.join(d, "fakebin")
        os.makedirs(fake_bin)
        tmux_path = os.path.join(fake_bin, "tmux")
        with open(tmux_path, "w") as f:
            f.write(FAKE_TMUX_SLOW)
        os.chmod(tmux_path, os.stat(tmux_path).st_mode | stat.S_IEXEC)

        live_sessions = os.path.join(d, "live_sessions.txt")
        with open(live_sessions, "w") as f:
            f.write("relay-lock-test-session\n")

        locks_dir = os.path.join(d, "locks")
        os.makedirs(locks_dir)

        env = dict(os.environ)
        env["PATH"] = fake_bin + os.pathsep + env["PATH"]
        env["TMUX_FAKE_LOG"] = os.path.join(d, "tmux.log")
        env["TMUX_FAKE_LIVE_SESSIONS"] = live_sessions
        env["SUPERBOSS_REGISTER_DB"] = db_path
        env["DISPATCH_TMUX_SESSION"] = "relay-lock-test-session"
        env["VERIDIAN_DISPATCH_LOCK_DIR"] = locks_dir
        # Real issue #980's standing stop-work-order gate is out of scope for
        # this file (the tmux-relay mutual-exclusion lock, not governance) --
        # disabled via the same real env-var override STOP_WORK_ORDER_TASK_IDS
        # itself reads, not a real exemption.
        env["VERIDIAN_GOVERNOR_STOP_WORK_ORDER_TASK_IDS"] = ""
        yield env, os.path.join(d, "tmux.log")


def _run_dispatch(env, title, prompt):
    return subprocess.run(
        ["bash", os.path.join(SCRIPTS_DIR, "dispatch-owner-task.sh"),
         title, prompt, "4", "ssh_session", "veridian-scripts"],
        cwd=SCRIPTS_DIR, env=env, capture_output=True, text=True, timeout=30,
    )


def test_concurrent_relays_do_not_interleave(relay_env):
    env, log_path = relay_env
    results = {}

    def worker(name):
        results[name] = _run_dispatch(env, f"concurrent test {name}", f"real concurrent relay test {name}")

    t1 = threading.Thread(target=worker, args=("A",))
    t2 = threading.Thread(target=worker, args=("B",))
    t1.start()
    time.sleep(0.05)  # stagger slightly so both are genuinely in-flight together
    t2.start()
    t1.join(timeout=20)
    t2.join(timeout=20)

    assert results["A"].returncode == 0, results["A"].stderr
    assert results["B"].returncode == 0, results["B"].stderr

    with open(log_path) as f:
        lines = [l.strip() for l in f if l.strip()]

    # Reconstruct BEGIN/END pairs by PID; verify no two different PIDs' send-keys
    # spans overlap (has-session calls are fast/non-sleeping, exclude those).
    spans = []  # (pid, begin_idx, end_idx) for send-keys-shaped calls only
    open_by_pid = {}
    for idx, line in enumerate(lines):
        parts = line.split(" ", 2)
        marker, pid, rest = parts[0], parts[1], parts[2] if len(parts) > 2 else ""
        if "has-session" in rest:
            continue
        if marker == "BEGIN":
            open_by_pid[pid] = idx
        elif marker == "END" and pid in open_by_pid:
            spans.append((pid, open_by_pid.pop(pid), idx))

    # Overlap check: sort by begin, ensure each span's begin is >= previous span's end.
    spans.sort(key=lambda s: s[1])
    for i in range(1, len(spans)):
        prev_pid, _, prev_end = spans[i - 1]
        cur_pid, cur_begin, _ = spans[i]
        if cur_pid != prev_pid:
            assert cur_begin > prev_end, (
                f"real interleaving detected: pid {cur_pid}'s send-keys began (line {cur_begin}) "
                f"before pid {prev_pid}'s send-keys ended (line {prev_end}) -- the tmux relay lock "
                f"did not serialize these two concurrent invocations, log={lines}"
            )
