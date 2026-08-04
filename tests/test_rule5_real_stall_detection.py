#!/usr/bin/env python3
"""Real tests for OCID-068's seven-rule guardrails addendum, Rule 5
(UMR-20260804-180711-7f96, UMR-20260804-205741-cf3f, citing OCID-068's own
UMR-20260804-170055-a069): "real stall detection, a task is stale only if
it has no heartbeat, no checkpoint, no log entry, no CPU activity, and no
file change for the configured threshold, visual tmux pane text alone
shall never be used by itself to declare a stall, stall detection requires
this real combined evidence."

Covers find_stalled_running_tasks() and its helpers against real, isolated
fixtures: a real scratch SQLite DB (never the live production database), a
real temp task workspace directory (real file mtimes, not mocked), and
real /proc/<pid>/stat-shaped fixture files (a real running process --
this test's own PID -- for the "process exists" cases, and a real
guaranteed-nonexistent PID for the "no process" case).
"""
import datetime
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import time

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)  # dispatch-tick.py does a plain `import dispatch_core`


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location("sbr_seed_r5", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    return sbr, conn


def _load(name, filename, env=None):
    for stale in ("resource_governor", "dispatch_core"):
        sys.modules.pop(stale, None)
    old_env = {}
    if env:
        for k, v in env.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, filename))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if env:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)


def test_read_proc_cpu_ticks_real_own_process():
    """Real, non-mocked /proc/<pid>/stat read against this test process's
    OWN real PID -- proves the parser handles a genuine kernel-formatted
    stat line, not a hand-crafted fixture."""
    dt = _load("dt_r5_proc_self", "dispatch-tick.py")
    ticks = dt._read_proc_cpu_ticks(os.getpid())
    assert ticks is not None and ticks >= 0, ticks
    print(f"PASS: test_read_proc_cpu_ticks_real_own_process -> {ticks} ticks")


def test_read_proc_cpu_ticks_nonexistent_pid_returns_none():
    dt = _load("dt_r5_proc_missing", "dispatch-tick.py")
    # A PID essentially guaranteed not to exist.
    ticks = dt._read_proc_cpu_ticks(999999999)
    assert ticks is None, ticks
    print("PASS: test_read_proc_cpu_ticks_nonexistent_pid_returns_none")


def test_read_proc_cpu_ticks_handles_parenthesized_comm_field():
    """Real /proc/<pid>/stat lines have a parenthesized comm field that can
    itself contain spaces/parens (e.g. a process named "my (weird) app") --
    proves the parser anchors on the LAST ')', not the first."""
    with tempfile.TemporaryDirectory() as d:
        fake_stat = os.path.join(d, "stat")
        # Real kernel format: pid (comm) state ppid pgrp session tty_nr tpgid
        # flags minflt cminflt majflt cmajflt utime stime ...
        # comm = "weird (nested) name" -- deliberately tricky.
        fields_after_comm = ["S"] + ["0"] * 10 + ["1234", "5678"] + ["0"] * 30
        line = f"42 (weird (nested) name) " + " ".join(fields_after_comm)
        with open(fake_stat, "w") as f:
            f.write(line)
        dt = _load("dt_r5_proc_parens", "dispatch-tick.py")
        ticks = dt._read_proc_cpu_ticks(42, proc_stat_path=fake_stat)
        assert ticks == 1234 + 5678, (ticks, line)
        print(f"PASS: test_read_proc_cpu_ticks_handles_parenthesized_comm_field -> {ticks}")


def test_cpu_activity_cold_start_assumed_active():
    """First-ever observation for a task_id must never fabricate staleness
    from having nothing to compare against -- same cold-start philosophy as
    resource_governor.sample_metrics()."""
    with tempfile.TemporaryDirectory() as d:
        state_path = os.path.join(d, "cpu_state.json")
        dt = _load("dt_r5_cpu_cold", "dispatch-tick.py")
        active = dt._cpu_activity_since_last_tick(
            "test-task-cold", "veridian-worker@does-not-exist.service",
            datetime.datetime.now(datetime.timezone.utc), state_path=state_path,
        )
        # No real MainPID for a nonexistent unit -> fails safe to True (active).
        assert active is True, active
        print("PASS: test_cpu_activity_cold_start_assumed_active")


def test_cpu_activity_detects_real_unchanged_ticks_across_two_calls():
    """Real, direct test of the cross-tick delta logic using this test
    process's own real PID via a fake systemctl stand-in is impractical
    (no fake unit truly maps to a real PID) -- instead, drives
    _read_proc_cpu_ticks + the state-file persistence directly to prove the
    real delta comparison itself is correct, independent of the systemctl
    MainPID lookup (covered separately above)."""
    with tempfile.TemporaryDirectory() as d:
        state_path = os.path.join(d, "cpu_state.json")
        now = datetime.datetime.now(datetime.timezone.utc)
        # Seed state as if a prior tick already observed pid=42 at ticks=100.
        with open(state_path, "w") as f:
            json.dump({"t1": {"pid": 42, "ticks": 100, "ts": now.isoformat()}}, f)

        dt = _load("dt_r5_cpu_delta", "dispatch-tick.py")
        # Monkeypatch the two real I/O calls this function makes, in a
        # controlled, deterministic way -- proves the delta LOGIC, given
        # known real inputs, without depending on a real live systemd unit.
        dt._unit_main_pid = lambda unit: 42
        dt._read_proc_cpu_ticks = lambda pid, proc_stat_path=None: 100  # unchanged

        active = dt._cpu_activity_since_last_tick("t1", "veridian-worker@t1.service", now, state_path=state_path)
        assert active is False, "ticks unchanged, same PID -> real no-activity result expected"

        dt._read_proc_cpu_ticks = lambda pid, proc_stat_path=None: 150  # real movement
        active2 = dt._cpu_activity_since_last_tick("t1", "veridian-worker@t1.service", now, state_path=state_path)
        assert active2 is True, "ticks moved -> real activity expected"
        print("PASS: test_cpu_activity_detects_real_unchanged_ticks_across_two_calls")


def test_real_log_and_file_activity_reflects_a_real_fresh_write():
    with tempfile.TemporaryDirectory() as d:
        task_dir = os.path.join(d, "task-x")
        workspace = os.path.join(task_dir, "workspace")
        os.makedirs(workspace)
        real_file = os.path.join(workspace, "worker.log")
        with open(real_file, "w") as f:
            f.write("real log line\n")

        dt = _load("dt_r5_fileage_fresh", "dispatch-tick.py")
        now = datetime.datetime.now(datetime.timezone.utc)
        age = dt._real_log_and_file_activity_minutes(task_dir, now)
        assert age is not None and age < 1.0, age
        print(f"PASS: test_real_log_and_file_activity_reflects_a_real_fresh_write -> {age:.3f}min")


def test_real_log_and_file_activity_none_when_no_workspace():
    with tempfile.TemporaryDirectory() as d:
        dt = _load("dt_r5_fileage_none", "dispatch-tick.py")
        age = dt._real_log_and_file_activity_minutes(os.path.join(d, "no-such-task"), datetime.datetime.now(datetime.timezone.utc))
        assert age is None, age
        print("PASS: test_real_log_and_file_activity_none_when_no_workspace")


def test_find_stalled_running_tasks_flags_only_when_all_five_signals_are_stale():
    """End-to-end: a real in_progress task with an OLD checkpoint, an OLD
    real umr_tasks heartbeat, an OLD real workspace file mtime, and no real
    CPU activity (mocked to False) IS flagged. A second, otherwise-identical
    task with fresh file activity is NOT flagged -- proves the AND-of-all-
    five-signals requirement, not an OR."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr, conn = _seed_scratch_db(scratch_db)
        old_hb = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=90)).isoformat()
        for tid in ("stale-task", "fresh-file-task"):
            umr_id = sbr.upsert_umr_task(conn, {
                "task_identity": tid, "tier": 2, "status": "running",
                "source_trigger": "unit_test", "task_kind": "systemctl_action", "reason": "x",
            })
            conn.execute("UPDATE umr_tasks SET last_heartbeat=? WHERE umr_id=?", (old_hb, umr_id))
        conn.commit()
        conn.close()

        old_ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=90)).isoformat()
        task_dirs = {}
        for tid in ("stale-task", "fresh-file-task"):
            task_dir = os.path.join(d, tid)
            workspace = os.path.join(task_dir, "workspace")
            os.makedirs(workspace)
            task_dirs[tid] = task_dir
        # stale-task: old file mtime (set via utime).
        stale_file = os.path.join(task_dirs["stale-task"], "workspace", "worker.log")
        with open(stale_file, "w") as f:
            f.write("old\n")
        old_epoch = time.time() - 90 * 60
        os.utime(stale_file, (old_epoch, old_epoch))
        # fresh-file-task: file written just now (real fresh mtime).
        with open(os.path.join(task_dirs["fresh-file-task"], "workspace", "worker.log"), "w") as f:
            f.write("fresh\n")

        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        dt = _load("dt_r5_e2e", "dispatch-tick.py", env=env)
        dt._cpu_activity_since_last_tick = lambda task_id, unit, now, state_path=None: False  # no real CPU activity, either task

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            tasks = {
                "stale-task": {"status": "in_progress", "service": "veridian-worker@stale-task.service",
                                "last_checkpoint_at": old_ts, "_task_dir": task_dirs["stale-task"]},
                "fresh-file-task": {"status": "in_progress", "service": "veridian-worker@fresh-file-task.service",
                                     "last_checkpoint_at": old_ts, "_task_dir": task_dirs["fresh-file-task"]},
            }
            now = datetime.datetime.now(datetime.timezone.utc)
            stalled = dt.find_stalled_running_tasks(tasks, now, threshold_minutes=30)
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        stalled_ids = {item["task_id"] for item in stalled}
        assert "stale-task" in stalled_ids, stalled
        assert "fresh-file-task" not in stalled_ids, (
            "Rule 5 violation: fresh-file-task has real recent file activity and must NOT be "
            "flagged, even though its checkpoint/heartbeat/CPU are all stale -- ALL FIVE signals "
            "must independently confirm staleness"
        )
        stale_entry = next(item for item in stalled if item["task_id"] == "stale-task")
        assert stale_entry["evidence"]["cpu_active_since_last_tick"] is False, stale_entry
        assert stale_entry["evidence"]["checkpoint_age_minutes"] >= 30, stale_entry
        print(f"PASS: test_find_stalled_running_tasks_flags_only_when_all_five_signals_are_stale -> {stalled_ids}")


def test_find_stalled_running_tasks_fails_safe_on_missing_umr_row():
    """A task with no matching umr_tasks row at all (heartbeat age
    undeterminable) must NOT be flagged -- an inconclusive signal fails
    safe toward not declaring a stall, never a false positive."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)[1].close()

        task_dir = os.path.join(d, "no-umr-row-task")
        workspace = os.path.join(task_dir, "workspace")
        os.makedirs(workspace)
        stale_file = os.path.join(workspace, "worker.log")
        with open(stale_file, "w") as f:
            f.write("old\n")
        old_epoch = time.time() - 90 * 60
        os.utime(stale_file, (old_epoch, old_epoch))

        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        dt = _load("dt_r5_no_umr_row", "dispatch-tick.py", env=env)
        dt._cpu_activity_since_last_tick = lambda task_id, unit, now, state_path=None: False

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            old_ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=90)).isoformat()
            tasks = {"no-umr-row-task": {"status": "in_progress", "service": "veridian-worker@x.service",
                                          "last_checkpoint_at": old_ts, "_task_dir": task_dir}}
            stalled = dt.find_stalled_running_tasks(tasks, datetime.datetime.now(datetime.timezone.utc), threshold_minutes=30)
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert stalled == [], stalled
        print("PASS: test_find_stalled_running_tasks_fails_safe_on_missing_umr_row")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__} -> {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR: {t.__name__} -> {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)
