#!/usr/bin/env python3
"""Regression test for the real root cause of UMR-20260813-060311-6eea being
RCA-dispatched twice: RCA'd once by UMR-20260813-091810-5045 (2026-08-13,
which wrote a real, evidenced verdict starting "RCA (UMR-..." back into the
row's own `reason`), then RCA-dispatched AGAIN the next day
(UMR-20260814-013850-fd7f) because pm-sentinel-tick.sh's Check 2a scans
`--query-umr --status killed --limit 15` every tick and dispatched a fresh
RCA gap for every row it got back, with no check for whether a prior RCA
already resolved that exact row. Once dispatch-owner-task.sh's own 6h
content-duplicate window lapsed, the already-resolved row resurfaced.

Fix: `query_umr_tasks()` grew an `exclude_rca_complete` kwarg (plumbed
through `resource_governor.py --query-umr` as `--exclude-rca-complete`) that
filters out, at the plain-listing (--status only) path, any row whose
`reason` already starts with the established "RCA (UMR-...)" convention.
pm-sentinel-tick.sh's Check 2a now passes this flag.

Same scratch-DB seeding technique as tests/test_query_umr_by_id.py -- never
touches the live production DB.
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

ALREADY_RCAD_UMR_ID = "UMR-TEST-EXCLRCA-0001"
NEEDS_RCA_UMR_ID = "UMR-TEST-EXCLRCA-0002"
NULL_REASON_UMR_ID = "UMR-TEST-EXCLRCA-0003"


def _seed_scratch_db(path):
    # Same bootstrap technique as tests/test_query_umr_by_id.py's
    # _seed_scratch_db(): resolve_superboss_db_path() only accepts a scratch
    # path if it already exists with a real umr_tasks table, so bootstrap by
    # setting sbr.DB_PATH directly and calling the real sbr.init_db().
    spec = importlib.util.spec_from_file_location("sbr_seed_exclrca", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
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
        sbr.upsert_umr_task(conn, {
            "umr_id": ALREADY_RCAD_UMR_ID,
            "task_identity": "excl-rca-test-already-rcad",
            "tier": 1,
            "status": "killed",
            "source_trigger": "test-seed",
            "task_kind": "veridian_task_create",
            "ts_submitted": "2026-08-13T06:03:11+00:00",
            "reason": "RCA (UMR-20260813-091810-5045): real primary deliverable WAS produced -- "
                      "status remains killed (no claude-control artifact was ever produced).",
        })
        sbr.upsert_umr_task(conn, {
            "umr_id": NEEDS_RCA_UMR_ID,
            "task_identity": "excl-rca-test-needs-rca",
            "tier": 1,
            "status": "killed",
            "source_trigger": "test-seed",
            "task_kind": "veridian_task_create",
            "ts_submitted": "2026-08-13T07:00:00+00:00",
            "reason": "queued",
        })
        sbr.upsert_umr_task(conn, {
            "umr_id": NULL_REASON_UMR_ID,
            "task_identity": "excl-rca-test-null-reason",
            "tier": 1,
            "status": "killed",
            "source_trigger": "test-seed",
            "task_kind": "veridian_task_create",
            "ts_submitted": "2026-08-13T08:00:00+00:00",
        })
        conn.commit()
    finally:
        conn.close()


def test_exclude_rca_complete_filters_only_already_rcad_rows():
    """Direct function-level proof: exclude_rca_complete=True drops the row
    whose reason already starts with "RCA (...", keeps the queued and
    NULL-reason rows (both genuinely still need a first RCA)."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        _seed_rows(sbr, scratch_db)

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        try:
            matches = sbr.query_umr_tasks(conn, status="killed", limit=20, exclude_rca_complete=True)
        finally:
            conn.close()

        ids = {m["umr_id"] for m in matches}
        assert ALREADY_RCAD_UMR_ID not in ids, f"already-RCA'd row should be excluded, got: {ids}"
        assert NEEDS_RCA_UMR_ID in ids, f"queued/never-RCA'd row must still surface, got: {ids}"
        assert NULL_REASON_UMR_ID in ids, f"NULL-reason row must still surface, got: {ids}"
    print("PASS: test_exclude_rca_complete_filters_only_already_rcad_rows")


def test_default_behavior_unchanged_without_flag():
    """exclude_rca_complete defaults to False -- existing callers that don't
    pass it (e.g. any --umr-id/--task-identity lookup) see every row,
    exactly as before this fix."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        _seed_rows(sbr, scratch_db)

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        try:
            matches = sbr.query_umr_tasks(conn, status="killed", limit=20)
        finally:
            conn.close()

        ids = {m["umr_id"] for m in matches}
        assert ids == {ALREADY_RCAD_UMR_ID, NEEDS_RCA_UMR_ID, NULL_REASON_UMR_ID}, ids
    print("PASS: test_default_behavior_unchanged_without_flag")


def test_resource_governor_cli_exclude_rca_complete():
    """End-to-end proof via the real CLI entry point exactly as
    pm-sentinel-tick.sh's Check 2a runs it."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        _seed_rows(sbr, scratch_db)

        env = dict(os.environ, SUPERBOSS_REGISTER_DB=scratch_db)
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "resource_governor.py"),
             "--query-umr", "--status", "killed", "--limit", "15", "--exclude-rca-complete"],
            env=env, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"CLI call failed: stdout={result.stdout!r} stderr={result.stderr!r}"
        printed = json.loads(result.stdout)
        ids = {m["umr_id"] for m in printed["matches"]}
        assert ALREADY_RCAD_UMR_ID not in ids, ids
        assert NEEDS_RCA_UMR_ID in ids, ids
        assert NULL_REASON_UMR_ID in ids, ids
    print("PASS: test_resource_governor_cli_exclude_rca_complete")


if __name__ == "__main__":
    test_exclude_rca_complete_filters_only_already_rcad_rows()
    test_default_behavior_unchanged_without_flag()
    test_resource_governor_cli_exclude_rca_complete()
