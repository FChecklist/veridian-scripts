#!/usr/bin/env python3
"""
test_reconcile_stale_heartbeats.py -- standalone (no pytest required) proof
that the restored reconcile_stale_heartbeats() (task-20260802-074400-pm-
decision--scope-the-reconcile-stale-h) does what its docstring claims against
a real sqlite umr_tasks table:

1. A row with a stale last_heartbeat whose unit is no longer active gets
   reconciled to a real terminal status (completed/failed) via
   _unit_exit_terminal_status()'s systemd Result read.
2. A row with last_heartbeat IS NULL (the documented "brand-new column,
   don't touch on first tick" safety property) is left untouched.
3. A row whose unit is still active (is-active succeeds) is left untouched.

systemctl is monkeypatched (resource_governor._run) rather than skipped --
this exercises the real KEY=VALUE parsing / is-active branch logic, not a
reimplementation of it.

Runs entirely against a throwaway temp DB (SUPERBOSS_REGISTER_DB env var
override) -- never touches the live
/opt/veridian/ai-os/memory/superboss-register.sqlite.

Run: python3 test_reconcile_stale_heartbeats.py
Exits 0 and prints PASS if every check holds; exits 1 and prints the first
failure otherwise.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

SCRIPTS = os.path.dirname(os.path.abspath(__file__))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    tmpdir = tempfile.mkdtemp(prefix="reconcile_stale_heartbeats_test_")
    db_path = os.path.join(tmpdir, "test.sqlite")
    os.environ["SUPERBOSS_REGISTER_DB"] = db_path

    rg = load_module("resource_governor_under_test", os.path.join(SCRIPTS, "resource_governor.py"))

    failures = []

    sbr = rg._superboss_register()
    conn = sbr._connect()
    sbr._ensure_umr_table(conn)

    now = datetime.now(timezone.utc)
    stale_ts = (now - timedelta(seconds=rg.HEARTBEAT_STALE_TTL_SECONDS + 60)).isoformat()
    fresh_ts = (now - timedelta(seconds=30)).isoformat()

    def insert_row(umr_id, unit_name, last_heartbeat, status="running"):
        conn.execute(
            "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
            "source_trigger, task_kind, unit_name, last_heartbeat) "
            "VALUES (?, ?, ?, 2, ?, 'test', 'systemctl_action', ?, ?)",
            (umr_id, f"test-task-{umr_id}", now.isoformat(), status, unit_name, last_heartbeat),
        )
        conn.commit()

    # Row 1: stale heartbeat, unit no longer active, real Result=success -- must reconcile to 'completed'.
    insert_row("TEST-STALE-COMPLETED", "veridian-worker@test-completed.service", stale_ts)
    # Row 2: stale heartbeat, unit no longer active, real Result=failed -- must reconcile to 'failed'.
    insert_row("TEST-STALE-FAILED", "veridian-worker@test-failed.service", stale_ts)
    # Row 3: stale heartbeat, but unit is STILL active -- must be left alone (still 'running').
    insert_row("TEST-STALE-STILL-ACTIVE", "veridian-worker@test-active.service", stale_ts)
    # Row 4: last_heartbeat IS NULL -- must never be touched by reconcile_stale_heartbeats().
    insert_row("TEST-NULL-HEARTBEAT", "veridian-worker@test-null.service", None)
    # Row 5: fresh (non-stale) heartbeat -- must be left alone.
    insert_row("TEST-FRESH", "veridian-worker@test-fresh.service", fresh_ts)

    def fake_run(cmd, **kw):
        if cmd[:3] == ["systemctl", "--user", "is-active"]:
            unit = cmd[-1]
            active = "test-active" in unit
            return subprocess.CompletedProcess(cmd, 0 if active else 3, stdout="", stderr="")
        if cmd[:3] == ["systemctl", "--user", "disable"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["systemctl", "--user", "show"]:
            unit = cmd[3]
            if "test-completed" in unit:
                out = "Result=success\nExecMainStatus=0\nSubState=dead\n"
            else:
                out = "Result=exit-code\nExecMainStatus=1\nSubState=dead\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
        raise AssertionError(f"unexpected command in test: {cmd}")

    rg._run = fake_run

    actions = rg.reconcile_stale_heartbeats(now=now)

    reconciled_ids = {a["umr_id"] for a in actions}
    if reconciled_ids != {"TEST-STALE-COMPLETED", "TEST-STALE-FAILED"}:
        failures.append(f"expected exactly the 2 stale+inactive rows reconciled, got {reconciled_ids}")

    def status_of(umr_id):
        row = conn.execute("SELECT status FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
        return row["status"] if row else None

    if status_of("TEST-STALE-COMPLETED") != "completed":
        failures.append(f"TEST-STALE-COMPLETED expected status=completed, got {status_of('TEST-STALE-COMPLETED')}")
    if status_of("TEST-STALE-FAILED") != "failed":
        failures.append(f"TEST-STALE-FAILED expected status=failed, got {status_of('TEST-STALE-FAILED')}")
    if status_of("TEST-STALE-STILL-ACTIVE") != "running":
        failures.append(
            f"TEST-STALE-STILL-ACTIVE (unit still active) must be left alone, "
            f"got {status_of('TEST-STALE-STILL-ACTIVE')}")
    if status_of("TEST-NULL-HEARTBEAT") != "running":
        failures.append(
            f"TEST-NULL-HEARTBEAT (last_heartbeat IS NULL) must never be touched, "
            f"got {status_of('TEST-NULL-HEARTBEAT')}")
    if status_of("TEST-FRESH") != "running":
        failures.append(f"TEST-FRESH (non-stale) must be left alone, got {status_of('TEST-FRESH')}")

    # _unit_exit_terminal_status direct unit test (order-independent KEY=VALUE parsing).
    def fake_run_swapped_order(cmd, **kw):
        # systemd's real observed behavior: ExecMainStatus before SubState,
        # not the order the -p flags were passed in.
        return subprocess.CompletedProcess(cmd, 0, stdout="ExecMainStatus=0\nResult=success\nSubState=dead\n", stderr="")

    rg._run = fake_run_swapped_order
    terminal = rg._unit_exit_terminal_status("veridian-worker@order-test.service")
    if terminal != "completed":
        failures.append(f"_unit_exit_terminal_status order-independent parse failed, got {terminal!r}")

    conn.close()

    if failures:
        print("FAIL")
        for f in failures:
            print(f" - {f}")
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
