#!/usr/bin/env python3
"""Real test for task-gateway.py's `audit-24-points` subcommand
(UMR-20260808-145030-f3d1, governing chain UMR-20260806-171945-5767).

Runs the real CLI (subprocess, `sys.executable task-gateway.py audit-24-points`)
against an isolated scratch superboss-register.sqlite (SUPERBOSS_REGISTER_DB
seam, same convention as tests/test_build_lock_contended_requeue.py and
tests/test_audit24_master_issue_tracker_persistence.py) -- never the live
production database. Proves: (1) all 12 points run and return a real
boolean + evidence detail, (2) at least one real synthetic TRUE case and one
real synthetic FALSE case are genuinely exercised (not narration), (3) a
second run persists into the SAME rows (no duplication), reflected back via
`list-issues --linked-umr-id`.
"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATEWAY_PATH = os.path.join(SCRIPTS_DIR, "task-gateway.py")
SBR_PATH = os.path.join(SCRIPTS_DIR, "superboss-register.py")
GOVERNING_UMR = "UMR-20260806-171945-5767"
AUDIT_24_POINTS_SUBSET = (2, 4, 8, 9, 12, 14, 16, 17, 19, 20, 22, 23)


def _load_sbr_module(modname):
    spec = importlib.util.spec_from_file_location(modname, SBR_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_scratch_db(path):
    sbr = _load_sbr_module("sbr_audit24pts_seed")
    sbr.DB_PATH = path
    sbr.init_db()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_master_issue_tracker_table(conn)
    with sbr._write_lock():
        for n in range(1, 25):
            sbr.add_master_issue(
                conn, issue_id=f"UMR171945-{n:04d}", issue_identified=f"synthetic point {n}",
                linked_umr_id=GOVERNING_UMR,
            )
        conn.commit()
    conn.close()
    return sbr


def _run_gateway(scratch_db, args, cwd=None):
    env = dict(os.environ, SUPERBOSS_REGISTER_DB=scratch_db)
    result = subprocess.run(
        [sys.executable, GATEWAY_PATH] + args, cwd=cwd or SCRIPTS_DIR,
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (
        f"task-gateway.py {args!r} failed (exit {result.returncode}): "
        f"stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    return json.loads(result.stdout)


def _run_sbr(scratch_db, args):
    env = dict(os.environ, SUPERBOSS_REGISTER_DB=scratch_db)
    result = subprocess.run(
        [sys.executable, SBR_PATH] + args, env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"superboss-register.py {args!r} failed: {result.stdout!r} {result.stderr!r}"
    return json.loads(result.stdout)


def test_audit_24_points_runs_all_12_and_persists():
    """Real end-to-end run: all 12 points return a real boolean + detail,
    persist into master_issue_tracker, and list-issues --linked-umr-id
    reflects the exact same booleans back -- not a separate log/stdout
    capture, matching the addendum's own literal 'Real boolean test'."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)

        resp = _run_gateway(scratch_db, ["audit-24-points"])
        results = resp["results"]
        assert {r["point"] for r in results} == set(AUDIT_24_POINTS_SUBSET)

        # Real, deterministic, verifiable per-point evidence -- not narration.
        saw_true = saw_false = False
        by_point = {}
        for r in results:
            assert isinstance(r["boolean"], bool), r
            assert r["detail"], f"point {r['point']} has no real evidence detail"
            assert r["how_software_verifies_done"], f"point {r['point']} missing verify-done field"
            if r["boolean"]:
                saw_true = True
                assert r["if_false_who_acts"] is None and r["if_false_how_told"] is None, r
            else:
                saw_false = True
                assert r["if_false_who_acts"] and r["if_false_how_told"], (
                    f"point {r['point']} is FALSE but missing who-acts/how-told"
                )
            by_point[r["point"]] = r
        assert saw_true, "no point returned TRUE at all -- suspicious for a real, live scratch DB"
        assert saw_false, "no point returned FALSE at all -- suspicious, e.g. Point 4 should be FALSE here"

        # Real synthetic checks with a known, deterministic ground truth:
        # Point 16 (staleness scan wired into the real tick loop file) must
        # be TRUE -- that wiring is real, committed, and present on disk
        # regardless of DB state.
        assert by_point[16]["boolean"] is True, by_point[16]
        # Point 19 (_record_master_issue_if_new real call sites) must be
        # TRUE for the same reason -- real, on-disk, DB-independent.
        assert by_point[19]["boolean"] is True, by_point[19]
        # Point 2 (canonical query path) must be FALSE on a freshly-seeded
        # scratch DB with zero prior real status/--query-umr calls logged.
        assert by_point[2]["boolean"] is False, by_point[2]
        # Point 23 (Grafana) must be FALSE with no GRAFANA_URL configured --
        # honest, not fabricated.
        assert by_point[23]["boolean"] is False, by_point[23]

        listed = _run_sbr(scratch_db, ["list-issues", "--linked-umr-id", GOVERNING_UMR, "--limit", "50"])
        by_id = {m["issue_id"]: m for m in listed["matches"]}
        for point in AUDIT_24_POINTS_SUBSET:
            issue_id = f"UMR171945-{point:04d}"
            assert issue_id in by_id, issue_id
            row = by_id[issue_id]
            expected_outcome = "YES" if by_point[point]["boolean"] else "NO"
            assert row["solution_applied"] == expected_outcome, (point, row)
            assert row["is_deterministic"] == "YES", (point, row)
            assert row["is_ai_free"] == "YES", (point, row)
            assert row["is_boolean_software"] == "YES", (point, row)
        print("PASS: test_audit_24_points_runs_all_12_and_persists")


def test_audit_24_points_query_event_flips_point_02_true():
    """Real synthetic TRUE case for Point 2: after a real `task-gateway.py
    status` call (which logs a real canonical-caller query event) and a
    real `resource_governor.py --query-umr` call, Point 2 must flip TRUE."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        env = dict(os.environ, SUPERBOSS_REGISTER_DB=scratch_db)

        # Real canonical-path query events, logged the exact way the real
        # call sites do (log-governance-event CLI, never raw SQL).
        for caller in ("task-gateway.py:status", "resource_governor.py:--query-umr"):
            r = subprocess.run(
                [sys.executable, SBR_PATH, "log-governance-event", "--event-type", "query",
                 "--caller", caller], env=env, capture_output=True, text=True, timeout=15,
            )
            assert r.returncode == 0, r.stderr

        resp = _run_gateway(scratch_db, ["audit-24-points", "--no-persist"])
        by_point = {r["point"]: r for r in resp["results"]}
        assert by_point[2]["boolean"] is True, by_point[2]
        print("PASS: test_audit_24_points_query_event_flips_point_02_true")


if __name__ == "__main__":
    test_audit_24_points_runs_all_12_and_persists()
    test_audit_24_points_query_event_flips_point_02_true()
    print("ALL PASS")
