#!/usr/bin/env python3
"""Real tests for insert_pm_decision_pending()/resolve_pm_decision_pending()
(Owner standing SOP, UMR-20260806-031558-4dbd -- re-dispatch of
UMR-20260805-190440-ebe8 after its worker crashed on an unrelated real
Anthropic weekly usage-limit 429, root cause fixed by veridian-scripts PR
#98). Corrected, narrowed design: these two functions live directly in
superboss-register.py, NOT a separate script -- exactly one deterministic
script is the canonical read/write surface for superboss-register.sqlite.

Every test uses a real, isolated, temp-file SQLite database, PRE-CREATED
before SUPERBOSS_REGISTER_DB ever points at it -- same real finding
test_ocid_artifact_links.py's own _seed_scratch_db() docstring already
documents: resolve_superboss_db_path() falls through to the real live
default path when the override path does not already exist as a real,
non-zero, schema-verified file, rather than lazily bootstrapping a fresh
DB at that path. (This module's own author hit that exact trap live while
writing this file's first draft -- confirmed independently, and confirmed
the resulting stray test row was fully removed from the live DB before
this file was written; see this task's own PROGRESS.md.) Never touches the
live production database.
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _seed_scratch_db(path):
    """Real, pre-existing, schema'd scratch DB, created BEFORE
    SUPERBOSS_REGISTER_DB ever points at it -- same real finding
    test_ocid_artifact_links.py's own _seed_scratch_db() documents:
    resolve_superboss_db_path() falls through to the real live default path
    when the override path does not already exist as a real, non-zero,
    schema-verified file, rather than lazily bootstrapping a fresh DB there.

    Loads superboss-register.py once (module functions operate on whatever
    connection they're given, independent of that copy's own already-
    resolved DB_PATH) to build the real table(s) via its own real
    _ensure_umr_table()/_ensure_pm_decisions_pending_table() -- never a
    hand-rolled CREATE TABLE stub that could silently drift from the real
    schema (a real umr_tasks built this way has every real column, so a
    caller that then runs this script's own `init` CLI subcommand against
    the same path -- e.g. the CLI round-trip test below -- hits no
    column-mismatch migration error)."""
    spec = importlib.util.spec_from_file_location("sbr_seed", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    sbr._ensure_pm_decisions_pending_table(conn)
    conn.close()


def _load(name, env):
    old_env = {}
    for k, v in env.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, "superboss-register.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_insert_opens_a_real_row_with_status_open():
    """A real insert must create exactly one real row, status='open',
    opened_ts stamped, every real field readable back exactly -- including a
    real JSON-encoded options list round-tripping through json.loads()."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_insert", {"SUPERBOSS_REGISTER_DB": scratch_db})

        conn = sbr._connect()
        sbr._ensure_pm_decisions_pending_table(conn)
        options = [{"option": "A", "detail": "do A", "recommended": True},
                   {"option": "B", "detail": "do B", "recommended": False}]
        decision_id = sbr.insert_pm_decision_pending(
            conn, title="real test decision", detail="a real detail string",
            options=options, recommended_option="A", related_umr="UMR-TEST-0001",
        )
        conn.commit()

        row = conn.execute("SELECT * FROM pm_decisions_pending WHERE id=?", (decision_id,)).fetchone()
        conn.close()

        assert row is not None, "expected a real row to exist"
        assert row["status"] == "open", row
        assert row["title"] == "real test decision", row
        assert row["detail"] == "a real detail string", row
        assert row["recommended_option"] == "A", row
        assert row["related_umr"] == "UMR-TEST-0001", row
        assert row["opened_ts"], row
        assert row["closed_ts"] is None, row
        import json
        assert json.loads(row["options_json"]) == options, row
        print(f"PASS: test_insert_opens_a_real_row_with_status_open -> id={decision_id}")


def test_insert_with_no_options_stores_null():
    """options=None (the default) must store a real NULL options_json, not
    the string 'null' or an empty-list JSON encoding -- callers with no
    enumerated options must not need to know or care about this table's
    internal JSON representation."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_insert_no_opts", {"SUPERBOSS_REGISTER_DB": scratch_db})

        conn = sbr._connect()
        sbr._ensure_pm_decisions_pending_table(conn)
        decision_id = sbr.insert_pm_decision_pending(conn, title="t", detail="d")
        conn.commit()
        row = conn.execute("SELECT * FROM pm_decisions_pending WHERE id=?", (decision_id,)).fetchone()
        conn.close()

        assert row["options_json"] is None, row
        assert row["recommended_option"] is None, row
        assert row["related_umr"] is None, row
        print(f"PASS: test_insert_with_no_options_stores_null -> id={decision_id}")


def test_resolve_closes_a_real_open_row():
    """A real resolve must flip status away from 'open', stamp closed_ts,
    and record closed_by/closed_note exactly -- and must not touch any
    other real row's data."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_resolve", {"SUPERBOSS_REGISTER_DB": scratch_db})

        conn = sbr._connect()
        sbr._ensure_pm_decisions_pending_table(conn)
        decision_id = sbr.insert_pm_decision_pending(conn, title="t", detail="d")
        other_id = sbr.insert_pm_decision_pending(conn, title="other", detail="other detail")
        conn.commit()

        sbr.resolve_pm_decision_pending(
            conn, decision_id, closed_by="pm-directive", closed_note="real closing note",
        )
        conn.commit()

        row = conn.execute("SELECT * FROM pm_decisions_pending WHERE id=?", (decision_id,)).fetchone()
        other = conn.execute("SELECT * FROM pm_decisions_pending WHERE id=?", (other_id,)).fetchone()
        conn.close()

        assert row["status"] == "resolved", row
        assert row["closed_ts"], row
        assert row["closed_by"] == "pm-directive", row
        assert row["closed_note"] == "real closing note", row
        assert other["status"] == "open", other
        assert other["closed_ts"] is None, other
        print(f"PASS: test_resolve_closes_a_real_open_row -> id={decision_id}")


def test_resolve_accepts_a_custom_status():
    """A real caller must be able to record a more specific real closing
    status (e.g. 'declined') -- resolve_pm_decision_pending() must not force
    the literal string 'resolved'."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_resolve_custom_status", {"SUPERBOSS_REGISTER_DB": scratch_db})

        conn = sbr._connect()
        sbr._ensure_pm_decisions_pending_table(conn)
        decision_id = sbr.insert_pm_decision_pending(conn, title="t", detail="d")
        conn.commit()

        sbr.resolve_pm_decision_pending(conn, decision_id, closed_by="pm", status="declined")
        conn.commit()
        row = conn.execute("SELECT status FROM pm_decisions_pending WHERE id=?", (decision_id,)).fetchone()
        conn.close()

        assert row["status"] == "declined", row
        print(f"PASS: test_resolve_accepts_a_custom_status -> status={row['status']}")


def test_resolve_raises_on_unknown_id():
    """A bad decision_id must raise ValueError, not silently update 0 rows
    -- a silent no-op here would be a real, hard-to-notice caller bug."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_resolve_unknown", {"SUPERBOSS_REGISTER_DB": scratch_db})

        conn = sbr._connect()
        sbr._ensure_pm_decisions_pending_table(conn)
        raised = False
        try:
            sbr.resolve_pm_decision_pending(conn, 999999, closed_by="pm")
        except ValueError as e:
            raised = True
            assert "999999" in str(e), e
        conn.close()
        assert raised, "expected ValueError for an unknown decision_id"
        print("PASS: test_resolve_raises_on_unknown_id")


def test_ensure_table_is_idempotent_and_standalone_callable():
    """_ensure_pm_decisions_pending_table() must be safely callable multiple
    times, and must work standalone against a DB that never ran init_db()
    -- same defensiveness convention as _ensure_ocid_artifact_links_table."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_ensure_table", {"SUPERBOSS_REGISTER_DB": scratch_db})

        conn = sbr._connect()
        sbr._ensure_pm_decisions_pending_table(conn)
        sbr._ensure_pm_decisions_pending_table(conn)  # 2nd call must not raise
        decision_id = sbr.insert_pm_decision_pending(conn, title="t", detail="d")
        conn.commit()
        conn.close()
        assert decision_id == 1, f"expected the first real row in a fresh table to be id=1, got {decision_id}"
        print("PASS: test_ensure_table_is_idempotent_and_standalone_callable")


def test_cli_insert_then_resolve_round_trip():
    """Real end-to-end CLI round trip: `insert-pm-decision-pending` followed
    by `resolve-pm-decision-pending`, exactly as an operator/PM dispatch
    would invoke this script -- covers the argparse wiring, not just the
    underlying library functions."""
    import json
    import subprocess

    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = dict(os.environ)
        env["SUPERBOSS_REGISTER_DB"] = scratch_db
        script = os.path.join(SCRIPTS_DIR, "superboss-register.py")

        options_path = os.path.join(d, "options.json")
        with open(options_path, "w") as f:
            json.dump([{"option": "X", "recommended": True}], f)

        init = subprocess.run(
            [sys.executable, script, "init"], capture_output=True, text=True, env=env, timeout=30,
        )
        assert init.returncode == 0, init.stdout + init.stderr

        ins = subprocess.run(
            [sys.executable, script, "insert-pm-decision-pending",
             "--title", "cli test decision", "--detail", "cli real detail",
             "--options-json", options_path, "--recommended-option", "X",
             "--related-umr", "UMR-TEST-CLI-0001"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        assert ins.returncode == 0, ins.stdout + ins.stderr
        decision_id = json.loads(ins.stdout)["id"]
        assert json.loads(ins.stdout)["status"] == "open"

        res = subprocess.run(
            [sys.executable, script, "resolve-pm-decision-pending",
             "--id", str(decision_id), "--closed-by", "cli-test", "--closed-note", "cli close note"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        assert res.returncode == 0, res.stdout + res.stderr
        resolved = json.loads(res.stdout)
        assert resolved["status"] == "resolved", resolved
        assert resolved["closed_by"] == "cli-test", resolved
        assert resolved["closed_note"] == "cli close note", resolved

        bad = subprocess.run(
            [sys.executable, script, "resolve-pm-decision-pending",
             "--id", "999999", "--closed-by", "cli-test"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        assert bad.returncode != 0, bad.stdout + bad.stderr
        assert "999999" in bad.stdout, bad.stdout

        print(f"PASS: test_cli_insert_then_resolve_round_trip -> id={decision_id}")


if __name__ == "__main__":
    tests = [
        test_insert_opens_a_real_row_with_status_open,
        test_insert_with_no_options_stores_null,
        test_resolve_closes_a_real_open_row,
        test_resolve_accepts_a_custom_status,
        test_resolve_raises_on_unknown_id,
        test_ensure_table_is_idempotent_and_standalone_callable,
        test_cli_insert_then_resolve_round_trip,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__} -> {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR: {t.__name__} -> {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)
