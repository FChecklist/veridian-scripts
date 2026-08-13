#!/usr/bin/env python3
"""Regression test for UMR-20260813-042207 (addendum to P1 UMR-20260806-171945-5767):
`resource_governor.py --query-umr --umr-id X` returned the WRONG (most
recent) row regardless of X.

Real root cause: resource_governor.py's CLI parsed `--umr-id` into
`args.umr_id` but never threaded it into `query_umr_tasks(...)` -- the call
only ever passed limit/status/task_identity/query_text. `query_umr_tasks()`
itself had no `umr_id` parameter at all, so any --umr-id call fell through
to the final plain-listing `else` branch (filtered only by --status/--tier,
sorted `ORDER BY ts_submitted DESC`), silently ignoring X and returning the
newest row in the table instead. `--search` (FTS5) was unaffected and
worked as a workaround, which is why this went unnoticed.

Fix: `query_umr_tasks()` now takes a real `umr_id` kwarg and, when given,
runs `SELECT * FROM umr_tasks WHERE umr_id=?` (umr_id is the table's real
PRIMARY KEY) as its own first-priority branch -- ahead of task_identity/
query_text/plain-listing. resource_governor.py's --query-umr handler now
passes `umr_id=args.umr_id` through.

Both tests below seed a scratch DB (never the live production DB, same
convention as tests/test_build_lock_contended_requeue.py) with several
umr_tasks rows sharing the same status/tier, where the NEWEST row
(highest ts_submitted) is deliberately a DIFFERENT umr_id than the one
requested -- proving the fix is a real WHERE-clause fix, not just
"happens to return the right row when it's also the newest one".
"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

TARGET_UMR_ID = "UMR-TEST-QUERYBYID-0002"
OTHER_UMR_IDS = ["UMR-TEST-QUERYBYID-0001", "UMR-TEST-QUERYBYID-0003"]  # -0003 is newest


def _seed_scratch_db(path):
    # Same bootstrap technique as tests/test_build_lock_contended_requeue.py's
    # _seed_scratch_db(): resolve_superboss_db_path() only accepts
    # SUPERBOSS_REGISTER_DB as a real candidate if the path already exists,
    # is non-zero size, AND already has a real umr_tasks table -- so a
    # not-yet-existing scratch path must be bootstrapped by setting
    # sbr.DB_PATH directly and calling the real sbr.init_db(), never through
    # the CLI/env-var fallback path (which would silently touch the live
    # production DB instead).
    spec = importlib.util.spec_from_file_location("sbr_seed_queryumr", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    sbr.DB_PATH = path
    sbr.init_db()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    conn.close()
    return sbr


def _seed_rows(sbr, scratch_db):
    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    try:
        # -0001 oldest, -0002 (TARGET) middle, -0003 newest -- all same
        # status/tier, so the old (buggy) plain-listing fallback would
        # return -0003 for ANY --umr-id value.
        for i, umr_id in enumerate(["UMR-TEST-QUERYBYID-0001", TARGET_UMR_ID, "UMR-TEST-QUERYBYID-0003"]):
            sbr.upsert_umr_task(conn, {
                "umr_id": umr_id,
                "task_identity": f"query-umr-by-id-test-task-{i}",
                "tier": 2,
                "status": "running",
                "source_trigger": "test-seed",
                "task_kind": "veridian_task_create",
                "ts_submitted": f"2026-08-1{3 + i}T00:00:00+00:00",
            })
        conn.commit()
    finally:
        conn.close()


def test_query_umr_tasks_by_umr_id_returns_only_matching_row():
    """Direct function-level proof: query_umr_tasks(conn, umr_id=X) returns
    exactly the row where umr_tasks.umr_id=X, never the newest row."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        _seed_rows(sbr, scratch_db)

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        try:
            matches = sbr.query_umr_tasks(conn, umr_id=TARGET_UMR_ID)
        finally:
            conn.close()

        assert len(matches) == 1, f"expected exactly 1 row, got {len(matches)}: {matches}"
        assert matches[0]["umr_id"] == TARGET_UMR_ID
        for other in OTHER_UMR_IDS:
            assert all(m["umr_id"] != other for m in matches)
    print("PASS: test_query_umr_tasks_by_umr_id_returns_only_matching_row")


def test_query_umr_tasks_unknown_umr_id_returns_empty():
    """A umr_id with no matching row returns an empty list, not the newest
    row as a fallback (same real-PRIMARY-KEY-exact-match contract)."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        _seed_rows(sbr, scratch_db)

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        try:
            matches = sbr.query_umr_tasks(conn, umr_id="UMR-DOES-NOT-EXIST-0000")
        finally:
            conn.close()

        assert matches == []
    print("PASS: test_query_umr_tasks_unknown_umr_id_returns_empty")


def test_resource_governor_cli_query_umr_by_id():
    """End-to-end proof via the real CLI entry point exactly as an operator
    runs it: `resource_governor.py --query-umr --umr-id X`."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        _seed_rows(sbr, scratch_db)

        env = dict(os.environ, SUPERBOSS_REGISTER_DB=scratch_db)
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "resource_governor.py"),
             "--query-umr", "--umr-id", TARGET_UMR_ID],
            env=env, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"CLI call failed: stdout={result.stdout!r} stderr={result.stderr!r}"
        printed = json.loads(result.stdout)
        assert printed["count"] == 1, f"expected count=1, got {printed}"
        assert printed["matches"][0]["umr_id"] == TARGET_UMR_ID, printed["matches"]
    print("PASS: test_resource_governor_cli_query_umr_by_id")


if __name__ == "__main__":
    test_query_umr_tasks_by_umr_id_returns_only_matching_row()
    test_query_umr_tasks_unknown_umr_id_returns_empty()
    test_resource_governor_cli_query_umr_by_id()
