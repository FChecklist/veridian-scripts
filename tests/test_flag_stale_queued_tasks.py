#!/usr/bin/env python3
"""Real tests for resource_governor.py's flag_stale_queued_tasks() -- the
max-queued-age safeguard added by the dispatch-queue-starvation investigation
(UMR-20260806-090229-f2a7, parent UMR-20260806-071025-1d28).

Real incident this backstops: 30 real tier-1 umr_tasks rows sat
status='queued' for ~2 real days with nothing ever surfacing it as an
actionable finding. This is a deliberately separate, generic, deterministic
safeguard (zero AI judgment: a real age threshold against a real timestamp)
that opens one real, idempotent pm_decisions_pending row per stale umr_id via
superboss-register.py's own insert_pm_decision_pending() -- never a raw
UPDATE/DELETE against umr_tasks, and never auto-resolves anything itself.

Every test uses a real, isolated, temp-file SQLite database (never the live
production database), same convention as tests/test_umr_reuse_on_resume.py
and tests/test_pm_decisions_pending.py.

IMPORTANT isolation note (found the hard way building this test): every
read/write this test file itself performs against the scratch DB uses an
EXPLICIT raw sqlite3.connect(scratch_db) connection, never module-level
sbr._connect()/DB_PATH. resource_governor.py's own superboss-register.py
import is lazy (first real use, cached in a module-global) and resolves
DB_PATH from SUPERBOSS_REGISTER_DB at THAT moment -- reusing a `sbr` module
object that was imported before the env var was set (or before the scratch
file existed) silently resolves to the real production DB_PATH instead of
raising, because resolve_superboss_db_path() only checks the env var
candidate exists non-empty at import time, then falls back to the real
default path -- exactly the "real, valid DB, wrong one" case its own
docstring does NOT treat as an error. Every DB touch below is therefore a
plain `sqlite3.connect(scratch_db)`, and the function actually under test
(rg.flag_stale_queued_tasks()/rg.run_tick()) resolves its own DB_PATH lazily,
inside the SUPERBOSS_REGISTER_DB-set try block, which is what makes it see
the scratch DB for real.
"""
import datetime
import importlib.util
import os
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _schema_helpers():
    """Loads superboss-register.py purely for its pure, conn-taking helper
    functions (_ensure_umr_table, upsert_umr_task, resolve_pm_decision_pending,
    ...) -- every one of them takes an explicit `conn` argument and never
    calls _connect()/reads DB_PATH itself, so it is always safe to use
    regardless of what SUPERBOSS_REGISTER_DB is set to at import time."""
    spec = importlib.util.spec_from_file_location("sbr_helpers_stale", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
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
    sbr._ensure_pm_decisions_pending_table(conn)
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


def test_stale_row_gets_flagged():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_stale_1", env)

        now = rg._utcnow()
        old_ts = (now - datetime.timedelta(hours=5)).isoformat()
        conn = _new_conn(scratch_db)
        umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": "test-stale-row", "tier": 1, "status": "queued",
            "source_trigger": "unit_test", "task_kind": "veridian_task_create",
            "inputs": {"repo": "x", "title": "t", "prompt": "p"},
            "ts_submitted": old_ts, "reason": "queued",
        })
        conn.commit()
        conn.close()

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            flagged = rg.flag_stale_queued_tasks(now=now)
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
        assert flagged == [umr_id], flagged

        conn = _new_conn(scratch_db)
        row = conn.execute(
            "SELECT title, status, related_umr FROM pm_decisions_pending WHERE related_umr=?", (umr_id,),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["status"] == "open"
        assert row["title"].startswith("STALE-QUEUED:"), row["title"]


def test_fresh_row_not_flagged():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_stale_2", env)

        now = rg._utcnow()
        fresh_ts = (now - datetime.timedelta(minutes=45)).isoformat()
        conn = _new_conn(scratch_db)
        sbr.upsert_umr_task(conn, {
            "task_identity": "test-fresh-row", "tier": 1, "status": "queued",
            "source_trigger": "unit_test", "task_kind": "veridian_task_create",
            "inputs": {"repo": "x", "title": "t", "prompt": "p"},
            "ts_submitted": fresh_ts, "reason": "queued",
        })
        conn.commit()
        conn.close()

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            flagged = rg.flag_stale_queued_tasks(now=now)
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
        assert flagged == [], flagged


def test_idempotent_does_not_double_flag():
    """Calling flag_stale_queued_tasks() twice for the same still-stale row
    (simulating two real ticks 30s apart, same as veridian-governor-tick's
    real cadence) must never open a second pm_decisions_pending row."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_stale_3", env)

        now = rg._utcnow()
        old_ts = (now - datetime.timedelta(hours=10)).isoformat()
        conn = _new_conn(scratch_db)
        umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": "test-repeat-stale-row", "tier": 0, "status": "queued",
            "source_trigger": "unit_test", "task_kind": "veridian_task_create",
            "inputs": {"repo": "x", "title": "t", "prompt": "p"},
            "ts_submitted": old_ts, "reason": "queued",
        })
        conn.commit()
        conn.close()

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            first = rg.flag_stale_queued_tasks(now=now)
            second = rg.flag_stale_queued_tasks(now=now + datetime.timedelta(seconds=30))
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
        assert first == [umr_id]
        assert second == [], "must not re-open a second pending decision for an already-flagged row"

        conn = _new_conn(scratch_db)
        count = conn.execute(
            "SELECT COUNT(*) c FROM pm_decisions_pending WHERE related_umr=?", (umr_id,)
        ).fetchone()["c"]
        conn.close()
        assert count == 1, count


def test_re_flags_after_a_prior_flag_was_resolved():
    """If a PM already resolved the prior STALE-QUEUED decision (real
    resolve_pm_decision_pending() call) and the row is SOMEHOW still queued
    later, a fresh real decision must be allowed to open -- this function
    only ever checks for an OPEN duplicate, never blocks forever."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_stale_4", env)

        now = rg._utcnow()
        old_ts = (now - datetime.timedelta(hours=10)).isoformat()
        conn = _new_conn(scratch_db)
        umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": "test-reflag-row", "tier": 1, "status": "queued",
            "source_trigger": "unit_test", "task_kind": "veridian_task_create",
            "inputs": {"repo": "x", "title": "t", "prompt": "p"},
            "ts_submitted": old_ts, "reason": "queued",
        })
        conn.commit()
        conn.close()

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            first = rg.flag_stale_queued_tasks(now=now)
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
        assert first == [umr_id]

        conn = _new_conn(scratch_db)
        row = conn.execute(
            "SELECT id FROM pm_decisions_pending WHERE related_umr=? AND status='open'", (umr_id,)
        ).fetchone()
        sbr.resolve_pm_decision_pending(conn, row["id"], closed_by="unit_test", closed_note="held, still investigating")
        conn.commit()
        conn.close()

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            second = rg.flag_stale_queued_tasks(now=now + datetime.timedelta(hours=1))
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
        assert second == [umr_id], "a resolved prior flag must not block a fresh real flag"


def test_non_queued_rows_ignored():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_stale_5", env)

        now = rg._utcnow()
        old_ts = (now - datetime.timedelta(days=3)).isoformat()
        conn = _new_conn(scratch_db)
        for status in ("completed", "failed", "running", "killed", "rejected_duplicate"):
            sbr.upsert_umr_task(conn, {
                "task_identity": f"test-nonqueued-{status}", "tier": 1, "status": status,
                "source_trigger": "unit_test", "task_kind": "veridian_task_create",
                "inputs": {"repo": "x", "title": "t", "prompt": "p"},
                "ts_submitted": old_ts, "reason": "queued",
            })
        conn.commit()
        conn.close()

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            flagged = rg.flag_stale_queued_tasks(now=now)
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
        assert flagged == [], flagged


def test_run_tick_wires_in_stale_queued_flagged_key():
    """run_tick() (the real function called by --tick, itself called every
    30s by veridian-governor-tick.service) must include this safeguard on
    every real pass -- confirms the wiring, not just the function in
    isolation."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_stale_6", env)
        # Force emergency-stop path to a path that will never exist, so
        # run_tick()'s real dispatch loop is exercised (max_dispatches=0
        # below keeps it from actually spawning anything) without depending
        # on this box's own real EMERGENCY_STOP sentinel state.
        rg.EMERGENCY_STOP_PATH = os.path.join(d, "EMERGENCY_STOP_never_created")

        now = rg._utcnow()
        old_ts = (now - datetime.timedelta(hours=6)).isoformat()
        conn = _new_conn(scratch_db)
        umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": "test-run-tick-stale", "tier": 1, "status": "queued",
            "source_trigger": "unit_test", "task_kind": "systemctl_action",
            "unit_name": "veridian-worker@does-not-exist-test-unit.service",
            "inputs": {"action": "start"},
            "ts_submitted": old_ts, "reason": "queued",
        })
        conn.commit()
        conn.close()

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            result = rg.run_tick(max_dispatches=0, now=now)
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
        assert "stale_queued_flagged" in result
        assert result["stale_queued_flagged"] == [umr_id], result["stale_queued_flagged"]
        assert result["dispatches"] == []  # max_dispatches=0 -> real dispatch loop never entered


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
