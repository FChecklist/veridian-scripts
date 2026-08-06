#!/usr/bin/env python3
"""Real tests for resource_governor.py's flag_stale_queued_tasks() -- the
max-queued-age safeguard added by the dispatch-queue-starvation investigation
(UMR-20260806-090229-f2a7, parent UMR-20260806-071025-1d28).

Real incident this backstops: 30 real tier-1 umr_tasks rows sat
status='queued' for ~2 real days with nothing ever surfacing it as an
actionable finding. This is a deliberately separate, generic, deterministic
safeguard (zero AI judgment: a real age threshold against a real timestamp).

Emission-shape fix (UMR-20260806-163738-4323, governing
UMR-20260806-071025-1d28): this function used to open one real, idempotent
pm_decisions_pending row PER stale umr_id, which drove Section 7 of the
standing PM report to 48-of-118 open decisions being the identical STALE-
QUEUED condition repeated (~41%). It now keeps exactly ONE real open
'STALE-QUEUED-AGGREGATE:' row representing the whole condition, updated IN
PLACE (superboss-register.py's own update_pm_decision_pending()) as the real
count/affected-umr_id list changes, and resolves that same row itself
(status='resolved') the moment the real condition genuinely clears (zero
stale rows) -- never a raw UPDATE/DELETE against umr_tasks or against
pm_decisions_pending outside superboss-register.py. The 4h detection
threshold and the underlying per-row age check are UNCHANGED by this fix --
only the pm_decisions_pending emission shape changed. These tests replace
the pre-fix one-row-per-umr_id tests (which asserted a 'STALE-QUEUED:' title
and one row per umr_id) with tests for the new aggregate shape.

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


def _seed_stale_row(sbr, scratch_db, task_identity, tier, hours_old, now):
    old_ts = (now - datetime.timedelta(hours=hours_old)).isoformat()
    conn = _new_conn(scratch_db)
    umr_id = sbr.upsert_umr_task(conn, {
        "task_identity": task_identity, "tier": tier, "status": "queued",
        "source_trigger": "unit_test", "task_kind": "veridian_task_create",
        "inputs": {"repo": "x", "title": "t", "prompt": "p"},
        "ts_submitted": old_ts, "reason": "queued",
    })
    conn.commit()
    conn.close()
    return umr_id


def _open_aggregate_rows(scratch_db):
    conn = _new_conn(scratch_db)
    rows = conn.execute(
        "SELECT id, title, detail, status FROM pm_decisions_pending "
        "WHERE status='open' AND title LIKE 'STALE-QUEUED-AGGREGATE:%'"
    ).fetchall()
    conn.close()
    return rows


def test_stale_row_gets_flagged_into_one_aggregate_row():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_stale_1", env)

        now = rg._utcnow()
        umr_id = _seed_stale_row(sbr, scratch_db, "test-stale-row", 1, 5, now)

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            flagged = rg.flag_stale_queued_tasks(now=now)
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
        assert flagged == [umr_id], flagged

        rows = _open_aggregate_rows(scratch_db)
        assert len(rows) == 1, rows
        assert rows[0]["title"].startswith("STALE-QUEUED-AGGREGATE: 1 "), rows[0]["title"]
        assert umr_id in rows[0]["detail"]


def test_fresh_row_not_flagged():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_stale_2", env)

        now = rg._utcnow()
        _seed_stale_row(sbr, scratch_db, "test-fresh-row", 1, 45 / 60.0, now)

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            flagged = rg.flag_stale_queued_tasks(now=now)
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
        assert flagged == [], flagged
        assert _open_aggregate_rows(scratch_db) == []


def test_idempotent_updates_same_row_in_place_not_a_new_row_per_tick():
    """Calling flag_stale_queued_tasks() twice for the same still-stale row
    (simulating two real ticks 30s apart, same as veridian-governor-tick's
    real cadence) must never open a second pm_decisions_pending row -- it
    must update the SAME real aggregate row id in place."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_stale_3", env)

        now = rg._utcnow()
        umr_id = _seed_stale_row(sbr, scratch_db, "test-repeat-stale-row", 0, 10, now)

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            first = rg.flag_stale_queued_tasks(now=now)
            second = rg.flag_stale_queued_tasks(now=now + datetime.timedelta(seconds=30))
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
        assert first == [umr_id]
        assert second == [umr_id]

        conn = _new_conn(scratch_db)
        count = conn.execute(
            "SELECT COUNT(*) c FROM pm_decisions_pending WHERE title LIKE 'STALE-QUEUED-AGGREGATE:%'"
        ).fetchone()["c"]
        conn.close()
        assert count == 1, "must update the same real aggregate row in place, never insert a second"


def test_aggregate_row_carries_real_count_and_grows_with_more_stale_rows():
    """A second real umr_id crossing the threshold must be folded into the
    SAME open aggregate row (real count/detail updated in place), not a
    second row."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_stale_4", env)

        now = rg._utcnow()
        umr_id_a = _seed_stale_row(sbr, scratch_db, "test-multi-a", 1, 5, now)

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            first = rg.flag_stale_queued_tasks(now=now)
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
        assert first == [umr_id_a]
        rows_after_first = _open_aggregate_rows(scratch_db)
        assert len(rows_after_first) == 1
        first_row_id = rows_after_first[0]["id"]
        assert rows_after_first[0]["title"].startswith("STALE-QUEUED-AGGREGATE: 1 ")

        umr_id_b = _seed_stale_row(sbr, scratch_db, "test-multi-b", 1, 8, now)

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            second = rg.flag_stale_queued_tasks(now=now)
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
        assert set(second) == {umr_id_a, umr_id_b}

        rows_after_second = _open_aggregate_rows(scratch_db)
        assert len(rows_after_second) == 1
        assert rows_after_second[0]["id"] == first_row_id, "must update the SAME real row id in place"
        assert rows_after_second[0]["title"].startswith("STALE-QUEUED-AGGREGATE: 2 ")
        assert umr_id_a in rows_after_second[0]["detail"]
        assert umr_id_b in rows_after_second[0]["detail"]


def test_aggregate_row_self_resolves_when_condition_genuinely_clears():
    """Once every real umr_id has left the stale set (e.g. dispatched),
    the aggregate row must be honestly resolved, not left open forever
    showing a stale count that is no longer real."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_stale_5", env)

        now = rg._utcnow()
        old_ts = (now - datetime.timedelta(hours=6)).isoformat()
        conn = _new_conn(scratch_db)
        task_identity = "test-clears-row"
        umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": task_identity, "tier": 1, "status": "queued",
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
        assert len(_open_aggregate_rows(scratch_db)) == 1

        # Real row leaves the stale set (dispatched to a terminal status).
        conn = _new_conn(scratch_db)
        sbr.update_umr_task(conn, umr_id, status="completed")
        conn.commit()
        conn.close()

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            second = rg.flag_stale_queued_tasks(now=now + datetime.timedelta(minutes=5))
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
        assert second == []
        assert _open_aggregate_rows(scratch_db) == [], "aggregate row must self-resolve once real count is 0"

        conn = _new_conn(scratch_db)
        closed = conn.execute(
            "SELECT status, closed_note FROM pm_decisions_pending WHERE title LIKE 'STALE-QUEUED-AGGREGATE:%'"
        ).fetchone()
        conn.close()
        assert closed["status"] == "resolved", closed["status"]
        assert closed["closed_note"], "must record a real, honest closed_note, not a silent close"


def test_re_flags_after_a_prior_flag_was_resolved():
    """If a PM already resolved the prior STALE-QUEUED-AGGREGATE decision
    (real resolve_pm_decision_pending() call) and a real row is SOMEHOW
    still queued past threshold later, a fresh real aggregate decision must
    be allowed to open -- this function only ever checks for an OPEN
    aggregate, never blocks forever."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_stale_6", env)

        now = rg._utcnow()
        umr_id = _seed_stale_row(sbr, scratch_db, "test-reflag-row", 1, 10, now)

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            first = rg.flag_stale_queued_tasks(now=now)
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
        assert first == [umr_id]

        conn = _new_conn(scratch_db)
        row = conn.execute(
            "SELECT id FROM pm_decisions_pending WHERE status='open' AND title LIKE 'STALE-QUEUED-AGGREGATE:%'"
        ).fetchone()
        sbr.resolve_pm_decision_pending(conn, row["id"], closed_by="unit_test", closed_note="held, still investigating")
        conn.commit()
        conn.close()

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            second = rg.flag_stale_queued_tasks(now=now + datetime.timedelta(hours=1))
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
        assert second == [umr_id], "a resolved prior aggregate must not block a fresh real flag"
        rows = _open_aggregate_rows(scratch_db)
        assert len(rows) == 1
        assert rows[0]["id"] != row["id"], "the fresh row must be a real new row, distinct from the resolved one"


def test_non_queued_rows_ignored():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_stale_7", env)

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
        assert _open_aggregate_rows(scratch_db) == []


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
        rg = _load_rg("rg_stale_8", env)
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
