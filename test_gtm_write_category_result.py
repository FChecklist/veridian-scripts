#!/usr/bin/env python3
"""Real, isolated tests for gtm_write_category_result.py -- the canonical
writer every gtm_check_*.py script uses to record a GTM certification
category result (SPEC: owner absolute stop-work order, task-20260806-165921,
first real test batch for the ~101-script untested gap,
UMR-20260806-175915-8f47).

Runs the real script as a real subprocess (exactly how every gtm_check_*.py
caller invokes it), against a real, isolated, temp-file SQLite database
seeded with the real gtm_certification_categories + ocid_master_standard_
audit_log schema -- never the live production database. Isolation works
because gtm_write_category_result.py's own load_sbr() re-imports the real,
live superboss-register.py (hardcoded SBR_PATH), whose DB_PATH is resolved
from the SUPERBOSS_REGISTER_DB env var at import time (resolve_superboss_db_
path()) -- so setting that env var on the subprocess redirects every write
this script makes to the temp DB, with zero risk to the real one.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(SCRIPTS_DIR, "gtm_write_category_result.py")


def _seed_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE gtm_certification_categories (
            category_index INTEGER PRIMARY KEY, category_name TEXT, ocid_number TEXT,
            parent_umr_id TEXT, child_umr_id TEXT, passed INTEGER, evidence_summary TEXT,
            evidence_json TEXT, fix_commit TEXT, fix_file_path TEXT, fix_pr_number INTEGER,
            validated_at TEXT, created_at TEXT, last_updated_at TEXT
        );
        CREATE TABLE ocid_master_standard_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ocid_number TEXT, umr_id TEXT, event_type TEXT NOT NULL,
            detail_json TEXT NOT NULL, recorded_at TEXT NOT NULL
        );
        -- minimal real umr_tasks table: resolve_superboss_db_path() in the
        -- real superboss-register.py this script imports refuses to accept
        -- SUPERBOSS_REGISTER_DB unless it verifies a genuine umr_tasks
        -- table exists (OCID-068 decoy-DB guard) -- table only needs to be
        -- present, this test never reads/writes it.
        CREATE TABLE umr_tasks (umr_id TEXT PRIMARY KEY, status TEXT);
        """
    )
    conn.execute(
        "INSERT INTO gtm_certification_categories (category_index, category_name, created_at) "
        "VALUES (2, 'static_code_analysis', '2026-08-06T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()


def _run(db_path, *extra_args):
    args = [
        sys.executable, SCRIPT,
        "--category-index", "2",
        "--script-path", "scripts/gtm_check_fixture_category_writer_test.py",
        "--evidence-summary", "eslint exit 0, tsc exit 0 against origin/main HEAD abc1234",
        "--evidence-json", json.dumps({"eslint_exit_code": 0, "tsc_exit_code": 0, "commit_sha": "abc1234"}),
    ] + list(extra_args)
    env = dict(os.environ)
    env["SUPERBOSS_REGISTER_DB"] = db_path
    return subprocess.run(args, capture_output=True, text=True, timeout=30, env=env)


def test_pass_result_writes_real_row_and_audit_event():
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "scratch.sqlite")
        _seed_db(db_path)

        proc = _run(db_path, "--result", "pass")
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
        assert out == {
            "category_index": 2, "category_name": "static_code_analysis",
            "result": "pass", "passed": 1,
        }, out

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM gtm_certification_categories WHERE category_index=2"
        ).fetchone()
        assert row["passed"] == 1
        assert row["evidence_summary"] == "eslint exit 0, tsc exit 0 against origin/main HEAD abc1234"
        evidence = json.loads(row["evidence_json"])
        assert evidence["eslint_exit_code"] == 0
        assert evidence["commit_sha"] == "abc1234"
        # writer must always self-cite the script that produced the result
        assert evidence["script_path"] == "scripts/gtm_check_fixture_category_writer_test.py"
        assert row["validated_at"] is not None
        assert row["last_updated_at"] is not None

        audit = conn.execute(
            "SELECT * FROM ocid_master_standard_audit_log WHERE event_type='gtm_certification_category_result'"
        ).fetchone()
        assert audit is not None
        detail = json.loads(audit["detail_json"])
        assert detail["category_index"] == 2
        assert detail["result"] == "pass"
        assert detail["category_name"] == "static_code_analysis"
        conn.close()


def test_fail_result_writes_passed_zero_never_null():
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "scratch.sqlite")
        _seed_db(db_path)
        proc = _run(db_path, "--result", "fail")
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
        assert out["passed"] == 0, out

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT passed FROM gtm_certification_categories WHERE category_index=2"
        ).fetchone()
        conn.close()
        assert row[0] == 0


def test_blocked_result_writes_passed_null_and_no_validated_at():
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "scratch.sqlite")
        _seed_db(db_path)
        proc = _run(db_path, "--result", "blocked")
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
        assert out["passed"] is None, out

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT passed, validated_at FROM gtm_certification_categories WHERE category_index=2"
        ).fetchone()
        conn.close()
        assert row[0] is None
        # a genuinely-blocked check never gets a fabricated validation timestamp
        assert row[1] is None


def test_unknown_category_index_errors_without_writing():
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "scratch.sqlite")
        _seed_db(db_path)
        args = [
            sys.executable, SCRIPT,
            "--category-index", "999",
            "--result", "pass",
            "--script-path", "scripts/gtm_check_nonexistent.py",
            "--evidence-summary", "n/a",
            "--evidence-json", "{}",
        ]
        env = dict(os.environ)
        env["SUPERBOSS_REGISTER_DB"] = db_path
        proc = subprocess.run(args, capture_output=True, text=True, timeout=30, env=env)
        assert proc.returncode == 1, proc.stdout
        out = json.loads(proc.stdout)
        assert "error" in out and "999" in out["error"]

        conn = sqlite3.connect(db_path)
        cnt = conn.execute("SELECT COUNT(*) FROM ocid_master_standard_audit_log").fetchone()[0]
        conn.close()
        assert cnt == 0, "no audit event should be recorded when the category row does not exist"


def test_invalid_evidence_json_errors_cleanly():
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "scratch.sqlite")
        _seed_db(db_path)
        proc = _run(db_path, "--result", "pass")  # placeholder to reuse env building below
        args = [
            sys.executable, SCRIPT,
            "--category-index", "2",
            "--result", "pass",
            "--script-path", "scripts/gtm_check_fixture_category_writer_test.py",
            "--evidence-summary", "n/a",
            "--evidence-json", "{not valid json",
        ]
        env = dict(os.environ)
        env["SUPERBOSS_REGISTER_DB"] = db_path
        proc = subprocess.run(args, capture_output=True, text=True, timeout=30, env=env)
        assert proc.returncode == 1
        out = json.loads(proc.stdout)
        assert "error" in out and "not valid JSON" in out["error"]


if __name__ == "__main__":
    test_pass_result_writes_real_row_and_audit_event()
    test_fail_result_writes_passed_zero_never_null()
    test_blocked_result_writes_passed_null_and_no_validated_at()
    test_unknown_category_index_errors_without_writing()
    test_invalid_evidence_json_errors_cleanly()
    print("PASS: all gtm_write_category_result.py tests")
