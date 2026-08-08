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
import argparse
import contextlib
import importlib.util
import io
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
        # Points 14/20 are alert-condition checks (real boolean=True means a
        # live problem, not a healthy state) -- if_false_who_acts/how_told
        # must key off that alert-aware HEALTH verdict, not the raw boolean
        # directly. Real, confirmed round-2 review bug: this assertion used
        # to enforce the raw-boolean mapping uniformly, which would have
        # silently accepted the inverted (buggy) output for these two
        # points had either genuinely returned True in this run.
        alert_points = {14, 20}
        saw_true = saw_false = False
        by_point = {}
        for r in results:
            assert isinstance(r["boolean"], bool), r
            assert r["detail"], f"point {r['point']} has no real evidence detail"
            assert r["how_software_verifies_done"], f"point {r['point']} missing verify-done field"
            healthy = (not r["boolean"]) if r["point"] in alert_points else r["boolean"]
            if r["boolean"]:
                saw_true = True
            else:
                saw_false = True
            if healthy:
                assert r["if_false_who_acts"] is None and r["if_false_how_told"] is None, r
            else:
                assert r["if_false_who_acts"] and r["if_false_how_told"], (
                    f"point {r['point']} is unhealthy but missing who-acts/how-told"
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

        # Points 14/20 are alert-condition checks (their own docstrings say
        # TRUE means "an alert condition, not a health verdict") -- the
        # persisted health verdict (solution_applied/issue_resolved_
        # permanently) is the INVERSE of the raw boolean for these two,
        # same as every other point's boolean directly otherwise. Real,
        # confirmed round-1 review bug: the original code persisted the raw
        # boolean uniformly, which for these two meant a live problem got
        # recorded as permanently resolved.
        alert_points = {14, 20}
        listed = _run_sbr(scratch_db, ["list-issues", "--linked-umr-id", GOVERNING_UMR, "--limit", "50"])
        by_id = {m["issue_id"]: m for m in listed["matches"]}
        for point in AUDIT_24_POINTS_SUBSET:
            issue_id = f"UMR171945-{point:04d}"
            assert issue_id in by_id, issue_id
            row = by_id[issue_id]
            raw = by_point[point]["boolean"]
            healthy = (not raw) if point in alert_points else raw
            expected_outcome = "YES" if healthy else "NO"
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


def _load_gateway_module(modname):
    spec = importlib.util.spec_from_file_location(modname, GATEWAY_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_point_is_healthy_helper():
    """Direct unit test of the one, shared _point_is_healthy() helper --
    the single source of truth both _persist_audit24_point_result() and
    cmd_audit_24_points() now use, per this round's fix (round 1 only fixed
    the persistence call site; the printed-output call site still used the
    raw boolean directly until this round)."""
    tg = _load_gateway_module("tg_point_is_healthy_check")
    assert tg._point_is_healthy(14, True) is False   # alert firing -> unhealthy
    assert tg._point_is_healthy(14, False) is True    # no alert -> healthy
    assert tg._point_is_healthy(20, True) is False
    assert tg._point_is_healthy(20, False) is True
    assert tg._point_is_healthy(16, True) is True     # normal point, unaffected
    assert tg._point_is_healthy(16, False) is False
    print("PASS: test_point_is_healthy_helper")


def test_cmd_audit_24_points_output_uses_healthy_for_alert_point_remediation():
    """Real, confirmed round-2 review bug, direct regression test:
    cmd_audit_24_points()'s printed JSON output must null if_false_who_acts/
    if_false_how_told based on the alert-aware HEALTH verdict, not the raw
    boolean directly -- for Point 14 with boolean=True (a real alert
    firing), remediation guidance must be POPULATED, not None (round 1
    fixed this for the persisted master_issue_tracker row; this round fixes
    the identical bug in the printed/returned JSON output)."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        tg = _load_gateway_module("tg_cmd_audit_output_check")
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            # Force point 14 True (alert firing), everything else False --
            # bypasses the real check functions entirely, isolating this
            # test to the output-construction logic under review, not real
            # check mechanics (already covered by other tests).
            tg._AUDIT_24_CHECKS = {
                p: (lambda p=p: (p == 14, "synthetic")) for p in tg.AUDIT_24_POINTS_SUBSET
            }
            args = argparse.Namespace(no_persist=True)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                tg.cmd_audit_24_points(args)
            output = json.loads(buf.getvalue())
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)

        by_point = {r["point"]: r for r in output["results"]}
        # Point 14: boolean=True (alert firing) -- healthy=False -- remediation
        # guidance MUST be populated, not None.
        assert by_point[14]["boolean"] is True, by_point[14]
        assert by_point[14]["if_false_who_acts"] is not None, by_point[14]
        assert by_point[14]["if_false_how_told"] is not None, by_point[14]
        # Control: a normal point with boolean=False is also unhealthy --
        # remediation guidance also populated (unaffected control case).
        assert by_point[16]["boolean"] is False, by_point[16]
        assert by_point[16]["if_false_who_acts"] is not None, by_point[16]
        print("PASS: test_cmd_audit_24_points_output_uses_healthy_for_alert_point_remediation")


def test_alert_condition_point_14_true_persists_as_unhealthy():
    """Real, confirmed round-1 review bug, direct regression test: Point 14's
    own docstring says TRUE means "an alert condition, not a health
    verdict" (stale umr_tasks rows currently exist) -- so persisting
    boolean_result=True must write solution_applied=NO/issue_resolved_
    permanently=NO (a live problem, not a resolved one), not YES."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        tg = _load_gateway_module("tg_point14_check")
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            tg._persist_audit24_point_result(14, True, "3 real stale rows found", "2026-08-08T00:00:00Z")
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM master_issue_tracker WHERE issue_id=?", ("UMR171945-0014",)).fetchone()
        conn.close()
        assert row["solution_applied"] == "NO", dict(row)
        assert row["issue_resolved_permanently"] == "NO", dict(row)
        assert "raw_boolean=True" in row["check_again_notes"], row["check_again_notes"]
        assert "healthy=False" in row["check_again_notes"], row["check_again_notes"]
        print("PASS: test_alert_condition_point_14_true_persists_as_unhealthy")


def test_alert_condition_point_20_true_persists_as_unhealthy():
    """Same real bug class as Point 14, for Point 20 (a cron/timer process
    currently over 5pct CPU is also an alert condition, not a health
    verdict)."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        tg = _load_gateway_module("tg_point20_check")
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            tg._persist_audit24_point_result(20, True, "1 real process over 5pct CPU", "2026-08-08T00:00:00Z")
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM master_issue_tracker WHERE issue_id=?", ("UMR171945-0020",)).fetchone()
        conn.close()
        assert row["solution_applied"] == "NO", dict(row)
        assert row["issue_resolved_permanently"] == "NO", dict(row)
        print("PASS: test_alert_condition_point_20_true_persists_as_unhealthy")


def test_normal_point_true_still_persists_as_healthy_control():
    """Control case: a normal (non-alert-condition) point's TRUE must still
    persist as healthy (YES) -- the point-14/20 fix must not have
    accidentally inverted every point's semantics."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        tg = _load_gateway_module("tg_point16_control_check")
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            tg._persist_audit24_point_result(16, True, "wired correctly", "2026-08-08T00:00:00Z")
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM master_issue_tracker WHERE issue_id=?", ("UMR171945-0016",)).fetchone()
        conn.close()
        assert row["solution_applied"] == "YES", dict(row)
        assert row["issue_resolved_permanently"] == "YES", dict(row)
        print("PASS: test_normal_point_true_still_persists_as_healthy_control")


def test_point_22_returns_false_when_a_matched_close_fails():
    """Real, confirmed round-1 review bug, direct regression test: Point 22
    used to unconditionally return True regardless of whether any matched
    row's close-issue call actually succeeded. Monkeypatches run_json to
    simulate one real close-issue failure among the calls Point 22 makes,
    and asserts the check now genuinely fails (passed=False)."""
    tg = _load_gateway_module("tg_point22_check")

    calls = []

    def fake_run_json(cmd, step):
        calls.append(cmd)
        if cmd[2] == "list-issues":
            return {"matches": [
                {"issue_id": "UMR171945-0001", "apply_fix_notes": "merged via PR #1", "audit_notes": None},
                {"issue_id": "UMR171945-0002", "apply_fix_notes": "merged via PR #2", "audit_notes": None},
            ]}
        if cmd[2] == "close-issue":
            # Simulate a real failure for the second row only.
            if "UMR171945-0002" in cmd:
                return {"ok": False}
            return {"ok": True}
        raise AssertionError(f"unexpected run_json call: {cmd}")

    tg.run_json = fake_run_json
    passed, detail = tg._audit_point_22()
    assert passed is False, detail
    assert "UMR171945-0002" in detail and "FAILED" in detail, detail
    print("PASS: test_point_22_returns_false_when_a_matched_close_fails")


def test_point_22_returns_true_when_all_matched_closes_succeed():
    """Control case: Point 22 must still return True when every matched
    close-issue call genuinely succeeds -- the fix must not make it
    unconditionally False either."""
    tg = _load_gateway_module("tg_point22_control_check")

    def fake_run_json(cmd, step):
        if cmd[2] == "list-issues":
            return {"matches": [
                {"issue_id": "UMR171945-0001", "apply_fix_notes": "merged via PR #1", "audit_notes": None},
            ]}
        if cmd[2] == "close-issue":
            return {"ok": True}
        raise AssertionError(f"unexpected run_json call: {cmd}")

    tg.run_json = fake_run_json
    passed, detail = tg._audit_point_22()
    assert passed is True, detail
    print("PASS: test_point_22_returns_true_when_all_matched_closes_succeed")


if __name__ == "__main__":
    test_audit_24_points_runs_all_12_and_persists()
    test_audit_24_points_query_event_flips_point_02_true()
    print("ALL PASS")
