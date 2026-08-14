#!/usr/bin/env python3
"""Real regression test for resource_governor.py's scan_stuck_tasks() --
RCA fix (2026-08-13, UMR-20260813-231624-82d9) for the real incident
UMR-20260808-150937-43d0.

Real incident: a task_kind='systemctl_action' row (action=start,
unit=veridian-superboss-gateway.service, a persistent Restart=on-failure
singleton daemon created the day before) was written status='running' the
instant _perform_spawn()'s blocking `systemctl start` call returned 0 --
that call is a real no-op on an already-active unit. scan_stuck_tasks() then
measured "elapsed" off the TARGET UNIT's own real ActiveEnterTimestamp
(correct for a task_kind='veridian_task_create' row, where the worker unit's
uptime genuinely tracks real in-progress work) -- but for this
systemctl_action row that anchor reflected how long the DAEMON had already
been up, not how long this particular dispatch had been executing. Because
the daemon was already active well past STUCK_TASK_TIMEOUT_SECONDS, the very
next tick saw a healthy, intentionally-always-on service as instantly
"stuck" and SIGTERM'd/SIGKILL'd it -- confirmed live via the row's own
ts_dispatched=15:09:45 / ts_sigterm=15:10:16 (31s later, the next tick) /
ts_completed=15:11:18 (62s after SIGTERM, i.e. exactly
SIGTERM_TO_SIGKILL_GRACE_SECONDS later). No process was ever actually stuck;
the anchor timestamp was simply the wrong one for this task_kind.

Fix: task_kind='systemctl_action' rows are now excluded from
scan_stuck_tasks()'s stuck-task SELECT entirely -- _perform_spawn() already
runs the systemctl action to completion (blocking) before writing
status='running', so there is no further in-progress phase left to monitor,
mirroring the same task_kind carve-out already established for the same
reason in _stop_work_order_block_reason()'s own docstring.

Every test uses a real, isolated, temp-file SQLite database (never the live
production database) and monkeypatches resource_governor._run /
_unit_active_enter_timestamp so no real systemctl process is ever invoked --
same isolation convention as tests/test_flag_stale_queued_tasks.py.
"""
import datetime
import importlib.util
import os
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _schema_helpers():
    spec = importlib.util.spec_from_file_location("sbr_helpers_stuck", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def _new_conn(scratch_db):
    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_scratch_db(scratch_db, sbr):
    conn = _new_conn(scratch_db)
    sbr._ensure_umr_table(conn)
    conn.close()


def _load_rg(name, env):
    old_env = {}
    for k, v in env.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, "resource_governor.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_long_running_systemctl_action_row_not_killed():
    """The real incident, reproduced: a systemctl_action row whose target
    unit has been active far longer than STUCK_TASK_TIMEOUT_SECONDS (a
    healthy persistent daemon, not an actually-stuck task) must never be
    SIGTERM'd/SIGKILL'd, and no `systemctl kill` must ever be invoked for
    it."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_stuck_1", env)

        now = rg._utcnow()
        conn = _new_conn(scratch_db)
        umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": "test-persistent-daemon-start", "tier": 4, "status": "running",
            "source_trigger": "unit_test", "task_kind": "systemctl_action",
            "unit_name": "veridian-fake-persistent-daemon.service",
            "inputs": {"action": "start"}, "ts_submitted": now.isoformat(), "reason": "running",
        })
        conn.commit()
        conn.close()

        real_started = now - datetime.timedelta(hours=6)  # unit up 6h before this row ever dispatched
        rg._unit_active_enter_timestamp = lambda unit: real_started
        kill_calls = []
        rg._run = lambda cmd, **kw: (kill_calls.append(cmd) or _FakeCompleted())

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            actions = rg.scan_stuck_tasks(now=now)
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)

        assert actions == [], actions
        assert kill_calls == [], f"systemctl kill must never be invoked for a systemctl_action row: {kill_calls}"

        conn = _new_conn(scratch_db)
        row = conn.execute("SELECT status FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
        conn.close()
        assert row["status"] == "running", row["status"]


def test_long_running_veridian_task_create_row_still_gets_sigtermed():
    """Sanity/regression guard: the fix must be scoped to task_kind==
    'systemctl_action' only -- a genuinely stuck real worker row
    (task_kind='veridian_task_create') must still be SIGTERM'd exactly as
    before."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_stuck_2", env)

        now = rg._utcnow()
        conn = _new_conn(scratch_db)
        umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": "test-real-stuck-worker", "tier": 1, "status": "running",
            "source_trigger": "unit_test", "task_kind": "veridian_task_create",
            "unit_name": "veridian-worker@test-stuck.service",
            "inputs": {"repo": "x", "title": "t", "prompt": "p"},
            "ts_submitted": now.isoformat(), "reason": "running",
        })
        conn.commit()
        conn.close()

        real_started = now - datetime.timedelta(hours=6)
        rg._unit_active_enter_timestamp = lambda unit: real_started
        kill_calls = []
        rg._run = lambda cmd, **kw: (kill_calls.append(cmd) or _FakeCompleted())

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            actions = rg.scan_stuck_tasks(now=now)
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)

        assert len(actions) == 1 and actions[0]["action"] == "SIGTERM", actions
        assert any(c[:4] == ["systemctl", "--user", "kill", "-s"] and c[4] == "SIGTERM" for c in kill_calls), kill_calls

        conn = _new_conn(scratch_db)
        row = conn.execute("SELECT status FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
        conn.close()
        assert row["status"] == "sigterm_sent", row["status"]


class _FakeCompleted:
    returncode = 0
    stdout = ""
    stderr = ""


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR: {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
