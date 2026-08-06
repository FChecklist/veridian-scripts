#!/usr/bin/env python3
"""Real tests for reconcile_stale_heartbeats()'s execute gate
(UMR-20260806-141429-f447, proposal 88 priority one).

Before this fix, reconcile_stale_heartbeats() had NO execute gate at all --
it wrote/committed a real terminal status the instant it found any stale
non-NULL last_heartbeat row, unconditionally, on every 30s tick-loop pass.
That the live box never actually wrote anything was an accident of every
real row happening to have last_heartbeat IS NULL, never a structural
safety property.

These tests drive the real reconcile_stale_heartbeats() against a real,
isolated scratch SQLite DB (never the live production database), mocking
only the external `systemctl --user ...` boundary.
"""
import datetime
import importlib.util
import os
import sqlite3
import sys
from contextlib import contextmanager

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location(
        "sbr_seed_reconcile_gate", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    return sbr, conn


def _load_governor(env=None):
    for stale in ("resource_governor", "dispatch_core_governor", "dispatch_core"):
        sys.modules.pop(stale, None)
    old_env = {}
    if env:
        for k, v in env.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location(
            "rg_reconcile_gate", os.path.join(SCRIPTS_DIR, "resource_governor.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if env:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)


@contextmanager
def _live_env(env):
    """_superboss_register() is lazily loaded on first real call INSIDE
    reconcile_stale_heartbeats() -- not at module-exec time -- so the env
    vars must be live again right around the real call, same pattern
    test_backfill_null_heartbeats_task_yaml_crosscheck.py's own
    _run_backfill() already established for this exact "lazy env-gated
    importlib load" shape."""
    old_env = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        yield
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _iso(dt):
    return dt.isoformat()


def _seed_stale_running_row(sbr, conn, umr_id, unit_name, stale_seconds=1200):
    now = datetime.datetime.now(datetime.timezone.utc)
    stale_hb = _iso(now - datetime.timedelta(seconds=stale_seconds))
    row = {
        "umr_id": umr_id, "task_identity": umr_id, "tier": 2, "status": "running",
        "source_trigger": "unit_test", "task_kind": "systemctl_action",
        "unit_name": unit_name, "reason": "seed", "ts_dispatched": stale_hb,
    }
    real_umr_id = sbr.upsert_umr_task(conn, row)
    conn.execute("UPDATE umr_tasks SET last_heartbeat = ? WHERE umr_id = ?", (stale_hb, real_umr_id))
    conn.commit()
    return real_umr_id


def test_dry_run_default_never_writes(tmp_path, monkeypatch):
    db_path = str(tmp_path / "scratch.sqlite")
    sbr, conn = _seed_scratch_db(db_path)
    seeded_id = _seed_stale_running_row(sbr, conn, "UMR-TEST-GATE-0001", "veridian-worker@test1.service")
    conn.close()

    env = {"SUPERBOSS_REGISTER_DB": db_path}
    rg = _load_governor(env=env)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        class R:
            returncode = 1  # inactive
            stdout = ""
        return R()

    monkeypatch.setattr(rg, "_run", fake_run)
    with _live_env(env):
        actions = rg.reconcile_stale_heartbeats()  # execute defaults False

    assert len(actions) == 1
    assert actions[0]["decision"] == "would_reconcile"
    assert actions[0]["umr_id"] == seeded_id
    # Only the read-only is-active/show checks ran -- no `systemctl ... disable` call.
    assert all(c[:3] != ["systemctl", "--user", "disable"] for c in calls)

    conn2 = sqlite3.connect(db_path)
    conn2.row_factory = sqlite3.Row
    row = conn2.execute(
        "SELECT status, ts_completed FROM umr_tasks WHERE umr_id = ?", (seeded_id,)
    ).fetchone()
    conn2.close()
    assert row["status"] == "running"
    assert row["ts_completed"] is None


def test_execute_true_applies_real_write(tmp_path, monkeypatch):
    db_path = str(tmp_path / "scratch.sqlite")
    sbr, conn = _seed_scratch_db(db_path)
    seeded_id = _seed_stale_running_row(sbr, conn, "UMR-TEST-GATE-0002", "veridian-worker@test2.service")
    conn.close()

    env = {"SUPERBOSS_REGISTER_DB": db_path}
    rg = _load_governor(env=env)
    disable_calls = []

    def fake_run(cmd, **kw):
        if cmd[:3] == ["systemctl", "--user", "is-active"]:
            class R:
                returncode = 1  # inactive
                stdout = ""
            return R()
        if cmd[:3] == ["systemctl", "--user", "disable"]:
            disable_calls.append(cmd)
            class R:
                returncode = 0
                stdout = ""
            return R()
        class R:
            returncode = 1
            stdout = ""
        return R()

    monkeypatch.setattr(rg, "_run", fake_run)
    monkeypatch.setattr(rg, "_unit_exit_terminal_status", lambda unit: "failed")
    with _live_env(env):
        actions = rg.reconcile_stale_heartbeats(execute=True)

    assert len(actions) == 1
    assert actions[0]["decision"] == "reconciled"
    assert len(disable_calls) == 1

    conn2 = sqlite3.connect(db_path)
    conn2.row_factory = sqlite3.Row
    row = conn2.execute(
        "SELECT status, ts_completed FROM umr_tasks WHERE umr_id = ?", (seeded_id,)
    ).fetchone()
    conn2.close()
    assert row["status"] == "failed"
    assert row["ts_completed"] is not None


def test_cli_reconcile_stale_without_execute_is_dry_run(tmp_path, monkeypatch, capsys):
    db_path = str(tmp_path / "scratch.sqlite")
    sbr, conn = _seed_scratch_db(db_path)
    _seed_stale_running_row(sbr, conn, "UMR-TEST-GATE-0003", "veridian-worker@test3.service")
    conn.close()

    env = {"SUPERBOSS_REGISTER_DB": db_path}
    rg = _load_governor(env=env)

    def fake_run(cmd, **kw):
        class R:
            returncode = 1
            stdout = ""
        return R()

    monkeypatch.setattr(rg, "_run", fake_run)
    monkeypatch.setattr(sys, "argv", ["resource_governor.py", "--reconcile-stale"])
    with _live_env(env):
        rg.main()
    out = capsys.readouterr().out
    assert "would_reconcile" in out
    assert '"reconciled"' not in out
