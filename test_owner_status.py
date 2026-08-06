#!/usr/bin/env python3
"""Real tests for owner_status.py -- the Owner-facing reconciled task status
table (real DB rows cross-checked against real systemd unit state). SPEC:
owner absolute stop-work order, task-20260806-165921, first real test batch
for the ~101-script untested gap, UMR-20260806-175915-8f47.

owner_status.py hardcodes its own DB path as a module-level constant, so
these tests import it via importlib, monkeypatch that constant to a real,
isolated, temp-file SQLite database (built with the real umr_tasks schema
via superboss-register.py's own _ensure_umr_table(), never a hand-rolled
schema), and call its real main() with real argv -- never touching the live
production database. systemctl is genuinely invoked by real_unit_state() when
--verify-live is passed; tests that path with a unit name guaranteed not to
exist on this box, so it exercises the real subprocess call and its
"not systemd, therefore not active" branch without asserting on host-specific
state.
"""
import importlib.util
import io
import os
import sqlite3
import sys
from contextlib import redirect_stdout

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_sbr():
    spec = importlib.util.spec_from_file_location(
        "sbr_owner_status_test", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def _load_owner_status(db_path):
    spec = importlib.util.spec_from_file_location(
        "owner_status_test", os.path.join(SCRIPTS_DIR, "owner_status.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.DB = db_path
    return mod


def _seed(db_path, rows):
    sbr = _load_sbr()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    for umr_id, identity, status, unit_name, ts in rows:
        conn.execute(
            "INSERT INTO umr_tasks (umr_id, task_identity, status, unit_name, "
            "ts_submitted, tier, source_trigger) VALUES (?, ?, ?, ?, ?, 2, 'unit_test')",
            (umr_id, identity, status, unit_name, ts),
        )
    conn.commit()
    conn.close()


def _run(mod, argv):
    old_argv = sys.argv
    sys.argv = ["owner_status.py"] + argv
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            mod.main()
    finally:
        sys.argv = old_argv
    return buf.getvalue()


def test_lists_recent_rows_with_display_identity(tmp_path):
    db_path = str(tmp_path / "scratch.sqlite")
    _seed(db_path, [
        ("UMR-20260806-000001-aaaa", "task-real-identity-1", "queued", None, "2026-08-06T00:00:00Z"),
        ("UMR-20260806-000002-bbbb", "", "completed", None, "2026-08-06T00:05:00Z"),
    ])
    mod = _load_owner_status(db_path)
    out = _run(mod, ["--all"])
    assert "task-real-identity-1" in out
    assert "queued" in out
    # falls back to the raw umr_id as display id when task_identity is empty
    assert "UMR-20260806-000002-bbbb" in out
    assert "completed" in out


def test_hours_filter_excludes_old_rows(tmp_path):
    db_path = str(tmp_path / "scratch.sqlite")
    _seed(db_path, [
        ("UMR-20260101-000001-aaaa", "task-ancient", "completed", None, "2026-01-01T00:00:00Z"),
        ("UMR-20260806-000002-bbbb", "task-recent", "queued", None, "2026-08-06T00:00:00Z"),
    ])
    mod = _load_owner_status(db_path)
    out = _run(mod, ["--hours", "48"])
    assert "task-recent" in out
    assert "task-ancient" not in out


def test_verify_live_flags_mismatch_for_nonexistent_unit(tmp_path):
    db_path = str(tmp_path / "scratch.sqlite")
    _seed(db_path, [
        ("UMR-20260806-000003-cccc", "task-claims-running",
         "running", "veridian-worker@definitely-not-a-real-unit-owner-status-test.service",
         "2026-08-06T00:00:00Z"),
    ])
    mod = _load_owner_status(db_path)
    out = _run(mod, ["--all", "--verify-live"])
    assert "task-claims-running" in out
    assert "MISMATCH" in out
    assert "1 real DB/systemd mismatch(es) found" in out


def test_verify_live_no_mismatch_when_not_running_status(tmp_path):
    db_path = str(tmp_path / "scratch.sqlite")
    _seed(db_path, [
        ("UMR-20260806-000004-dddd", "task-queued-no-unit", "queued", None, "2026-08-06T00:00:00Z"),
    ])
    mod = _load_owner_status(db_path)
    out = _run(mod, ["--all", "--verify-live"])
    assert "0 real DB/systemd mismatch(es) found" in out


def test_real_unit_state_handles_none_and_real_subprocess_call():
    mod = _load_owner_status(str(__import__("tempfile").mkstemp()[1]))
    assert mod.real_unit_state(None) is None
    # a genuinely nonexistent unit: systemctl --user is-active reports
    # 'inactive'/'unknown' (never crashes real_unit_state's subprocess call)
    state = mod.real_unit_state("veridian-worker@definitely-not-a-real-unit-owner-status-test.service")
    assert state != "active"


if __name__ == "__main__":
    import pathlib
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tp = pathlib.Path(d)
        test_lists_recent_rows_with_display_identity(tp)
        test_hours_filter_excludes_old_rows(tp)
        test_verify_live_flags_mismatch_for_nonexistent_unit(tp)
        test_verify_live_no_mismatch_when_not_running_status(tp)
    test_real_unit_state_handles_none_and_real_subprocess_call()
    print("PASS: all owner_status.py tests")
