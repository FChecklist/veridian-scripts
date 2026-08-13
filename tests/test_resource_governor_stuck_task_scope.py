#!/usr/bin/env python3
"""Real regression test for the RCA on UMR-20260808-150937-43d0 (task
task-20260813-105503-rca--umr-20260808-150937-43d0-killed, UMR
UMR-20260813-101757-f13c): scan_stuck_tasks()'s SIGTERM/SIGKILL stuck-task
protocol was scoped to ANY status='running' row with a unit_name, which
wrongly swept in task_kind='systemctl_action' rows -- those record a
one-time `systemctl start/restart` action against a unit that is often a
persistent, always-on singleton daemon (e.g.
veridian-superboss-gateway.service), not an ephemeral per-task worker unit
expected to exit within STUCK_TASK_TIMEOUT_SECONDS of ITS OWN start.

Live-confirmed incident this closes: UMR-20260808-150937-43d0 was a
registration-only "start veridian-superboss-gateway.service" row.
_perform_spawn() marked it status="running" the instant `systemctl start`
returned 0 (the unit was already active), so its unit's real
ActiveEnterTimestamp reflected whenever it first started -- already older
than STUCK_TASK_TIMEOUT_SECONDS. scan_stuck_tasks() wrongly concluded the
row was "stuck" on its very next tick and SIGTERM'd (31s later) then
SIGKILL'd + disabled (91s later) the real, healthy gateway daemon. It was
still disabled/inactive 5 days later when this RCA ran.

Every test uses a real, isolated, temp-file SQLite database seeded with the
real schema -- never the live production database -- and stubs out `_run`
so no real `systemctl` call is ever made.
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location("sbr_seed_stuck_scope", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    conn.close()


def _load(name, filename, env=None):
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
                else:
                    os.environ[k] = v


def _insert_running_row(conn, umr_id, task_kind, unit_name):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
        "source_trigger, task_kind, unit_name, inputs_json, outputs_json) "
        "VALUES (?, ?, ?, 4, 'running', 'test', ?, ?, '{}', '{}')",
        (umr_id, f"identity-{umr_id}", now, task_kind, unit_name),
    )
    conn.commit()


def test_systemctl_action_running_row_never_signaled_as_stuck():
    """A task_kind='systemctl_action' row (e.g. 'start' on a persistent,
    already-long-running singleton daemon) must be completely invisible to
    scan_stuck_tasks() -- it must never receive SIGTERM/SIGKILL, and its
    status must stay untouched, no matter how old the unit's real
    ActiveEnterTimestamp is."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_test_stuck_scope_systemctl", "resource_governor.py", env=env)
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            conn = sqlite3.connect(scratch_db)
            conn.row_factory = sqlite3.Row
            _insert_running_row(conn, "UMR-TEST-SYSCTL-0001", "systemctl_action",
                                 "veridian-superboss-gateway.service")
            conn.close()

            # Simulate a unit that has genuinely been active far longer than
            # STUCK_TASK_TIMEOUT_SECONDS -- exactly the real, live shape of
            # an already-running persistent daemon.
            very_old = datetime.now(timezone.utc) - timedelta(seconds=rg.STUCK_TASK_TIMEOUT_SECONDS * 10)
            rg._unit_active_enter_timestamp = lambda unit: very_old
            run_calls = []
            rg._run = lambda cmd: (run_calls.append(cmd), _FakeCompletedProcess())[1]

            actions = rg.scan_stuck_tasks()

            assert actions == [], actions
            assert run_calls == [], run_calls

            conn = sqlite3.connect(scratch_db)
            conn.row_factory = sqlite3.Row
            row = dict(conn.execute(
                "SELECT status, ts_sigterm FROM umr_tasks WHERE umr_id='UMR-TEST-SYSCTL-0001'"
            ).fetchone())
            conn.close()
            assert row["status"] == "running", row
            assert row["ts_sigterm"] is None, row
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]
    print("PASS: test_systemctl_action_running_row_never_signaled_as_stuck")


def test_veridian_task_create_running_row_still_gets_sigtermed():
    """Control case -- confirms the fix is scoped correctly, not a blanket
    no-op: an actually-stuck ephemeral veridian_task_create worker row must
    still be SIGTERM'd exactly as before."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_test_stuck_scope_worker", "resource_governor.py", env=env)
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            conn = sqlite3.connect(scratch_db)
            conn.row_factory = sqlite3.Row
            _insert_running_row(conn, "UMR-TEST-WORKER-0001", "veridian_task_create",
                                 "veridian-worker@task-test.service")
            conn.close()

            very_old = datetime.now(timezone.utc) - timedelta(seconds=rg.STUCK_TASK_TIMEOUT_SECONDS * 10)
            rg._unit_active_enter_timestamp = lambda unit: very_old
            run_calls = []
            rg._run = lambda cmd: (run_calls.append(cmd), _FakeCompletedProcess())[1]

            actions = rg.scan_stuck_tasks()

            assert len(actions) == 1, actions
            assert actions[0]["umr_id"] == "UMR-TEST-WORKER-0001", actions
            assert actions[0]["action"] == "SIGTERM", actions
            assert run_calls, "expected a real systemctl kill invocation to have been made (stubbed)"

            conn = sqlite3.connect(scratch_db)
            conn.row_factory = sqlite3.Row
            row = dict(conn.execute(
                "SELECT status, ts_sigterm FROM umr_tasks WHERE umr_id='UMR-TEST-WORKER-0001'"
            ).fetchone())
            conn.close()
            assert row["status"] == "sigterm_sent", row
            assert row["ts_sigterm"] is not None, row
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]
    print("PASS: test_veridian_task_create_running_row_still_gets_sigtermed")


class _FakeCompletedProcess:
    returncode = 0
    stdout = ""
    stderr = ""


if __name__ == "__main__":
    tests = [
        test_systemctl_action_running_row_never_signaled_as_stuck,
        test_veridian_task_create_running_row_still_gets_sigtermed,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__} -> {e}")
    if failed:
        sys.exit(1)
    print(f"All {len(tests)} tests passed.")
