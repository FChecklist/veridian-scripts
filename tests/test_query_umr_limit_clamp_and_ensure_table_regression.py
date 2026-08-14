#!/usr/bin/env python3
"""Two real regression/proof tests written for UMR-20260813-225704-6195,
closing out the AUDIT:FAIL posted 2026-08-13T16:50Z on PR #308 (head
34bb70b61ac7456a20845d50df623ce02c87b628):

1. `test_query_umr_tasks_limit_is_hard_clamped_regardless_of_caller_limit`
   proves the task-4 requirement the PR's own PROGRESS.md claims but never
   actually tested: that `MAX_UMR_QUERY_LIMIT` really bounds the number of
   rows `query_umr_tasks()`/`--query-umr` can return, even when a caller
   (CLI or direct kwarg) passes a --limit far above it. Seeds
   MAX_UMR_QUERY_LIMIT + 5 real rows into a scratch DB (never the live
   production DB, same convention as tests/test_query_umr_by_id.py) and
   asserts the returned row count is clamped to exactly MAX_UMR_QUERY_LIMIT,
   both at the function level and through the real CLI subprocess.

2. `test_ensure_umr_table_legacy_gate_without_new_index_does_not_crash` is
   the direct regression test for the real bug the AUDIT:FAIL comment found
   and this task fixed: a pre-existing umr_tasks table that satisfies
   _ensure_umr_table()'s legacy fast-path gate (the 5 ALTER-added columns +
   widened status CHECK) but predates the new idx_umr_tasks_status_ts index
   used to crash by falling through to the full "slow path", which opens
   with a no-op `CREATE TABLE IF NOT EXISTS` against the already-existing
   table and then runs `CREATE INDEX ... ON umr_tasks(tier)` unconditionally
   -- a column this minimal/legacy-shaped table never had. Reproduced
   directly against the exact same minimal schema
   test_full_server_file_registration.py's own
   `_bootstrap_and_point_env_at_tmp_db()` fixture uses (that file's 19 tests
   were the ones the audit found newly broken: 2 failed + 17 errors on the
   PR's original head). Pre-fix: `sqlite3.OperationalError: no such column:
   tier`. Post-fix: returns cleanly, index left un-backfilled (correct,
   since a table this minimal doesn't even have ts_submitted, the column
   the new index is built on) -- exactly the pre-PR fast-path-return
   behavior, now preserved even though index_migrated is part of the gate.
"""
import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def _load_sbr():
    spec = importlib.util.spec_from_file_location(
        "sbr_query_limit_clamp_test", os.path.join(SCRIPTS_DIR, "superboss-register.py")
    )
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def _seed_scratch_db(sbr, path, n_rows):
    sbr.DB_PATH = path
    sbr.init_db()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    for i in range(n_rows):
        sbr.upsert_umr_task(conn, {
            "umr_id": f"UMR-TEST-LIMITCLAMP-{i:05d}",
            "task_identity": f"limit-clamp-test-task-{i}",
            "tier": 2,
            "status": "queued",
            "source_trigger": "test-seed",
            "task_kind": "veridian_task_create",
            "ts_submitted": f"2026-08-13T00:{i % 60:02d}:{i % 60:02d}+00:00",
        })
    conn.commit()
    conn.close()


def test_query_umr_tasks_limit_is_hard_clamped_regardless_of_caller_limit():
    sbr = _load_sbr()
    n_rows = sbr.MAX_UMR_QUERY_LIMIT + 5
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(sbr, scratch_db, n_rows)

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        try:
            # Ask for far more than both the real row count and the clamp.
            matches = sbr.query_umr_tasks(conn, status="queued", limit=999_999)
        finally:
            conn.close()

        assert len(matches) == sbr.MAX_UMR_QUERY_LIMIT, (
            f"expected exactly MAX_UMR_QUERY_LIMIT={sbr.MAX_UMR_QUERY_LIMIT} rows, "
            f"got {len(matches)} (seeded {n_rows} real matching rows, so an "
            f"unclamped query would have returned more than the limit)"
        )

        # Same proof through the real CLI entry point every PM tier actually
        # invokes, not just the Python function. Real fix (live-audit on PR
        # #308 head 4380f7f9): without VERIDIAN_SCRIPTS_DIR pinned here,
        # resource_governor.py's SCRIPTS resolves to VERIDIAN_ROOT/scripts
        # (the live-deployed copy, not this branch's own code), so the
        # subprocess silently tested whatever was deployed instead of the
        # PR's own diff -- same convention as every other subprocess test in
        # this suite (e.g. tests/test_ocid_artifact_links.py).
        env = dict(os.environ, SUPERBOSS_REGISTER_DB=scratch_db, VERIDIAN_SCRIPTS_DIR=SCRIPTS_DIR)
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "resource_governor.py"),
             "--query-umr", "--status", "queued", "--limit", "999999"],
            env=env, capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"CLI call failed: stdout={result.stdout!r} stderr={result.stderr!r}"
        import json as _json
        printed = _json.loads(result.stdout)
        assert printed["count"] == sbr.MAX_UMR_QUERY_LIMIT, (
            f"CLI --limit 999999 returned count={printed['count']}, "
            f"expected the hard clamp of {sbr.MAX_UMR_QUERY_LIMIT}"
        )
    print("PASS: test_query_umr_tasks_limit_is_hard_clamped_regardless_of_caller_limit")


def test_ensure_umr_table_legacy_gate_without_new_index_does_not_crash():
    """Direct regression test for the AUDIT:FAIL 2026-08-13T16:50Z finding
    on PR #308 (head 34bb70b6): _ensure_umr_table() must not fall through to
    its destructive full slow path (which assumes a full base schema, e.g.
    `tier`) merely because the new idx_umr_tasks_status_ts index hasn't been
    backfilled yet onto a table that otherwise already satisfies the legacy
    fast-path gate."""
    sbr = _load_sbr()
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "legacy_no_index.sqlite")
        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        # Same minimal legacy-shaped schema as
        # test_full_server_file_registration.py's own
        # _bootstrap_and_point_env_at_tmp_db(): satisfies the 5-column +
        # widened-status legacy gate, deliberately omits tier/ts_submitted
        # (columns the destructive slow path's unconditional CREATE INDEX
        # statements assume exist).
        conn.execute("""CREATE TABLE umr_tasks (
            umr_id TEXT PRIMARY KEY,
            task_identity TEXT,
            status TEXT DEFAULT 'queued' CHECK(status IN (
                'queued','dispatched','running','completed','completed_unmerged',
                'failed','rejected_duplicate','sigterm_sent','killed')),
            last_heartbeat TEXT,
            tenant_id TEXT,
            utm_source TEXT,
            external_agent_eligible INTEGER,
            ts_relay_attempted TEXT
        )""")
        conn.commit()

        # Pre-fix, this raised sqlite3.OperationalError: no such column: tier.
        sbr._ensure_umr_table(conn)

        # The new index must NOT have been created (this table has no
        # ts_submitted column to build it on) -- proves the fix took the
        # "skip cleanly" path, not a silent partial slow-path run.
        idx = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_umr_tasks_status_ts'"
        ).fetchone()
        assert idx is None, (
            "expected idx_umr_tasks_status_ts to be left un-backfilled on a "
            "table lacking ts_submitted, not silently created/skipped"
        )
        # And the table itself must be untouched/still usable (no partial
        # rebuild left it broken).
        conn.execute("SELECT umr_id FROM umr_tasks").fetchall()
        conn.close()
    print("PASS: test_ensure_umr_table_legacy_gate_without_new_index_does_not_crash")


if __name__ == "__main__":
    test_query_umr_tasks_limit_is_hard_clamped_regardless_of_caller_limit()
    test_ensure_umr_table_legacy_gate_without_new_index_does_not_crash()
