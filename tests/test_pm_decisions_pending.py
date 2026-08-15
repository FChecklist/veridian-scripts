#!/usr/bin/env python3
"""Real tests for insert_pm_decision_pending()/resolve_pm_decision_pending()
(Deterministic PM Reporting Contract V3, UMR-20260805-181636-32f2, OCID-020;
re-dispatch of UMR-20260805-190440-ebe8, parent UMR-20260805-185000-e94f).

Owner's corrected, narrowed design: these two functions live directly in
superboss-register.py (not a second standalone script), matching this
repo's existing function-library convention -- same signature shape/
docstring style/_connect()-_write_lock() discipline as
record_ocid_master_standard_audit_event()/insert_ocid_artifact_link()/
update_umr_task(). Every test here uses a real, isolated, temp-file SQLite
database seeded with the real schema (same convention as
tests/test_ocid_artifact_links.py) -- never the live production database.

Independent-verification note (found while building this task, not assumed
from the task's own SPEC): pm_decisions_pending's schema was already applied
directly to the live superboss-register.sqlite, but the standalone
migrate_2026-08-05_pm_report_tables.py script that first created it (commit
4797b71) only ever landed on an unmerged branch
(feat/pm-report-v3-schema-umr20260805181636), never on main.
test_ensure_table_matches_live_schema_columns below pins
_ensure_pm_decisions_pending_table()'s CREATE TABLE to the exact column set
already live in production, so this PR's schema can never silently drift
from what generate_pm_report_v3.py (PR #91, already merged) already reads.
"""
import argparse
import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_LIVE_SCHEMA_COLUMNS = [
    "id", "opened_ts", "title", "detail", "options_json", "recommended_option",
    "related_umr", "status", "closed_ts", "closed_by", "closed_note",
    # Owner standing mandate (task-20260806-034817, cites UMR-20260805-185000-e94f):
    # additive columns for the AI-proposes/PM-decides/AI-completes child-UMR
    # proposal lifecycle -- see
    # _migrate_pm_decisions_pending_owner_proposal_columns()'s own docstring.
    "decision_type", "completed_ts", "artifact_path", "commit_sha", "evidence",
]


def _load(name, filename, env=None):
    """Same load-with-env-override convention as
    tests/test_ocid_artifact_links.py's own _load(): resolve_superboss_db_path()
    is evaluated once, at module-exec time, so SUPERBOSS_REGISTER_DB must be
    set in the environment BEFORE exec_module() runs, never after."""
    old_env = {}
    if env:
        for k, v in env.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, filename))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if env:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def _seed_scratch_db(path):
    """Pre-create a real, fully-initialized scratch DB (real `instructions`
    table included, not just umr_tasks/pm_decisions_pending) -- needed
    because cmd_insert_pm_decision_pending()/cmd_resolve_pm_decision_pending()
    both call init_db_silent(), which probes `SELECT 1 FROM instructions
    LIMIT 1` before running _migrate_schema(). resolve_superboss_db_path()'s
    own verification (step 4d) separately requires a real umr_tasks table to
    already exist before it will ever accept SUPERBOSS_REGISTER_DB pointing
    at this path (same real finding tests/test_ocid_artifact_links.py's own
    _seed_scratch_db() documents) -- init_db() covers that too.

    Monkeypatches this freshly-loaded, throwaway module instance's own
    _connect() to point at `path` directly, rather than touching the
    module-global DB_PATH/SUPERBOSS_REGISTER_DB resolution at all: init_db()
    itself always calls the module's own _connect(), which otherwise reads
    the real production DB_PATH resolved at import time -- this is the safe
    way to reuse the real init_db() schema-creation logic against a scratch
    file without ever risking a write to the real, live, production
    database (a real mistake made and caught while building this task)."""
    spec = importlib.util.spec_from_file_location("sbr_seed_pm_dec", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)

    def _scratch_connect():
        conn = sqlite3.connect(path, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row
        return conn

    sbr._connect = _scratch_connect
    # init_db()'s own final print(json.dumps({"ok": True, "db": DB_PATH})) reports the
    # module-global DB_PATH (still the real production path in this throwaway module
    # instance, since only _connect was overridden above) -- silence it here so test
    # output never shows a misleading production path for what is really a scratch-only
    # write.
    _real_stdout, sys.stdout = sys.stdout, io.StringIO()
    try:
        sbr.init_db()
    finally:
        sys.stdout = _real_stdout
    conn = _scratch_connect()
    sbr._ensure_pm_decisions_pending_table(conn)
    conn.close()


def _scratch_env(scratch_db):
    return {"SUPERBOSS_REGISTER_DB": scratch_db}


def test_insert_pm_decision_pending_round_trip():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_insert_round_trip", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        options = [
            {"option": "A", "detail": "do A", "recommended": True},
            {"option": "B", "detail": "do B", "recommended": False},
        ]
        decision_id = sbr.insert_pm_decision_pending(
            conn, "Real test decision", "Real detail text",
            options=options, recommended_option="A", related_umr="UMR-test-0001",
        )
        conn.commit()
        assert isinstance(decision_id, int) and decision_id > 0, decision_id

        row = conn.execute("SELECT * FROM pm_decisions_pending WHERE id=?", (decision_id,)).fetchone()
        assert row is not None
        assert row["title"] == "Real test decision"
        assert row["detail"] == "Real detail text"
        assert json.loads(row["options_json"]) == options
        assert row["recommended_option"] == "A"
        assert row["related_umr"] == "UMR-test-0001"
        assert row["status"] == "open"
        assert row["closed_ts"] is None and row["closed_by"] is None and row["closed_note"] is None
        conn.close()
    print("PASS: test_insert_pm_decision_pending_round_trip")


def test_insert_without_options_leaves_options_json_null():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_insert_no_options", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        decision_id = sbr.insert_pm_decision_pending(conn, "No-options decision", "detail")
        conn.commit()
        row = conn.execute("SELECT * FROM pm_decisions_pending WHERE id=?", (decision_id,)).fetchone()
        assert row["options_json"] is None
        assert row["recommended_option"] is None
        assert row["related_umr"] is None
        conn.close()
    print("PASS: test_insert_without_options_leaves_options_json_null")


def test_resolve_pm_decision_pending_closes_open_row():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_resolve_closes", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        decision_id = sbr.insert_pm_decision_pending(conn, "To be resolved", "detail")
        conn.commit()

        resolved = sbr.resolve_pm_decision_pending(
            conn, decision_id, closed_by="pm-agent", closed_note="chose option A",
        )
        conn.commit()
        assert resolved is True

        row = conn.execute("SELECT * FROM pm_decisions_pending WHERE id=?", (decision_id,)).fetchone()
        assert row["status"] == "resolved"
        assert row["closed_by"] == "pm-agent"
        assert row["closed_note"] == "chose option A"
        assert row["closed_ts"] is not None
        conn.close()
    print("PASS: test_resolve_pm_decision_pending_closes_open_row")


def test_resolve_pm_decision_pending_is_idempotent():
    """Resolving an already-closed row a second time must be a real no-op:
    returns False, and never overwrites the first real closed_ts/closed_by/
    closed_note."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_resolve_idempotent", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        decision_id = sbr.insert_pm_decision_pending(conn, "Double resolve", "detail")
        conn.commit()

        first = sbr.resolve_pm_decision_pending(conn, decision_id, closed_by="first-closer", closed_note="first")
        conn.commit()
        assert first is True
        row_after_first = dict(conn.execute(
            "SELECT * FROM pm_decisions_pending WHERE id=?", (decision_id,)
        ).fetchone())

        second = sbr.resolve_pm_decision_pending(conn, decision_id, closed_by="second-closer", closed_note="second")
        conn.commit()
        assert second is False

        row_after_second = dict(conn.execute(
            "SELECT * FROM pm_decisions_pending WHERE id=?", (decision_id,)
        ).fetchone())
        assert row_after_second == row_after_first, (row_after_first, row_after_second)
        conn.close()
    print("PASS: test_resolve_pm_decision_pending_is_idempotent")


def test_resolve_pm_decision_pending_unknown_id_returns_false():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_resolve_unknown", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        resolved = sbr.resolve_pm_decision_pending(conn, 999999, closed_by="nobody")
        conn.commit()
        assert resolved is False
        conn.close()
    print("PASS: test_resolve_pm_decision_pending_unknown_id_returns_false")


def test_ensure_table_matches_live_schema_columns():
    """Pins _ensure_pm_decisions_pending_table()'s CREATE TABLE to the exact
    column set already live in production (independently verified via
    `PRAGMA table_info(pm_decisions_pending)` against
    /opt/veridian/ai-os/memory/superboss-register.sqlite while building this
    task) -- guards against this PR's schema ever silently drifting from
    what generate_pm_report_v3.py already reads."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_schema_columns", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(pm_decisions_pending)").fetchall()]
        assert cols == _LIVE_SCHEMA_COLUMNS, cols
        # calling it again against a DB that already has rows must be a true no-op
        sbr.insert_pm_decision_pending(conn, "pre-existing row", "detail")
        conn.commit()
        sbr._ensure_pm_decisions_pending_table(conn)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) AS c FROM pm_decisions_pending").fetchone()["c"] == 1
        conn.close()
    print("PASS: test_ensure_table_matches_live_schema_columns")


def test_cli_insert_and_resolve_end_to_end():
    """End-to-end through the real argparse cmd_* wrappers (same
    argparse.Namespace-based in-process convention as
    tests/test_rule7_completion_evidence.py's cmd_checkpoint tests), not a
    subprocess -- exercises --options-json file loading, the real
    _write_lock()-guarded write, and the real JSON stdout contract."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_cli_e2e", "superboss-register.py", env=_scratch_env(scratch_db))
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            options_path = os.path.join(d, "options.json")
            with open(options_path, "w") as f:
                json.dump([{"option": "X", "detail": "do X", "recommended": True}], f)

            insert_args = argparse.Namespace(
                title="CLI decision", detail="CLI detail",
                options_json=options_path, recommended_option="X",
                related_umr="UMR-cli-0001",
            )
            captured = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured
            try:
                sbr.cmd_insert_pm_decision_pending(insert_args)
            finally:
                sys.stdout = old_stdout
            inserted = json.loads(captured.getvalue())
            decision_id = inserted["id"]
            assert isinstance(decision_id, int) and decision_id > 0, inserted

            resolve_args = argparse.Namespace(
                decision_id=decision_id, closed_by="cli-tester",
                closed_note="closed via CLI", status="resolved",
            )
            captured = io.StringIO()
            sys.stdout = captured
            try:
                sbr.cmd_resolve_pm_decision_pending(resolve_args)
            finally:
                sys.stdout = old_stdout
            resolved = json.loads(captured.getvalue())
            assert resolved == {"id": decision_id, "resolved": True}, resolved

            conn = sbr._connect()
            row = conn.execute("SELECT * FROM pm_decisions_pending WHERE id=?", (decision_id,)).fetchone()
            assert row["status"] == "resolved"
            assert row["closed_by"] == "cli-tester"
            assert json.loads(row["options_json"]) == [{"option": "X", "detail": "do X", "recommended": True}]
            conn.close()
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
    print("PASS: test_cli_insert_and_resolve_end_to_end")


def test_cli_update_pm_decision_pending_end_to_end():
    """UMR-20260806-163738-4323: real CLI coverage for
    update_pm_decision_pending()/cmd_update_pm_decision_pending() -- the new
    write path that lets an aggregate-condition row (e.g. resource_governor.py's
    flag_stale_queued_tasks()) be refreshed in place instead of resolved and
    reinserted. Same real in-process argparse.Namespace convention as
    test_cli_insert_and_resolve_end_to_end above, never a subprocess."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_cli_update", "superboss-register.py", env=_scratch_env(scratch_db))
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            insert_args = argparse.Namespace(
                title="AGGREGATE: 1 thing", detail="v1 detail",
                options_json=None, recommended_option=None, related_umr=None,
            )
            captured = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured
            try:
                sbr.cmd_insert_pm_decision_pending(insert_args)
            finally:
                sys.stdout = old_stdout
            decision_id = json.loads(captured.getvalue())["id"]

            update_args = argparse.Namespace(
                decision_id=decision_id, title="AGGREGATE: 2 things", detail="v2 detail",
                related_umr=None, recommended_option="investigate",
            )
            captured = io.StringIO()
            sys.stdout = captured
            try:
                sbr.cmd_update_pm_decision_pending(update_args)
            finally:
                sys.stdout = old_stdout
            updated = json.loads(captured.getvalue())
            assert updated == {"id": decision_id, "updated": True}, updated

            conn = sbr._connect()
            row = conn.execute("SELECT * FROM pm_decisions_pending WHERE id=?", (decision_id,)).fetchone()
            assert row["title"] == "AGGREGATE: 2 things", dict(row)
            assert row["detail"] == "v2 detail"
            assert row["recommended_option"] == "investigate"
            assert row["status"] == "open", "update must never itself close the row"
            conn.close()

            # A field intentionally left None on the update call must be
            # left completely unchanged, not nulled out.
            update_args2 = argparse.Namespace(
                decision_id=decision_id, title=None, detail="v3 detail only",
                related_umr=None, recommended_option=None,
            )
            captured = io.StringIO()
            sys.stdout = captured
            try:
                sbr.cmd_update_pm_decision_pending(update_args2)
            finally:
                sys.stdout = old_stdout
            conn = sbr._connect()
            row = conn.execute("SELECT * FROM pm_decisions_pending WHERE id=?", (decision_id,)).fetchone()
            assert row["title"] == "AGGREGATE: 2 things", "untouched field must survive a partial update"
            assert row["detail"] == "v3 detail only"
            conn.close()
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
    print("PASS: test_cli_update_pm_decision_pending_end_to_end")


def test_cli_update_unknown_id_exits_nonzero():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_cli_update_unknown", "superboss-register.py", env=_scratch_env(scratch_db))
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            update_args = argparse.Namespace(
                decision_id=424242, title="x", detail=None, related_umr=None, recommended_option=None,
            )
            captured = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured
            try:
                sbr.cmd_update_pm_decision_pending(update_args)
                assert False, "expected sys.exit(1) for an unknown decision id"
            except SystemExit as e:
                assert e.code == 1, e.code
            finally:
                sys.stdout = old_stdout
            result = json.loads(captured.getvalue())
            assert result == {"id": 424242, "updated": False}, result
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
    print("PASS: test_cli_update_unknown_id_exits_nonzero")


def test_cli_resolve_unknown_id_exits_nonzero():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_cli_resolve_unknown", "superboss-register.py", env=_scratch_env(scratch_db))
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            resolve_args = argparse.Namespace(
                decision_id=424242, closed_by="cli-tester", closed_note=None, status="resolved",
            )
            captured = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured
            try:
                sbr.cmd_resolve_pm_decision_pending(resolve_args)
                assert False, "expected sys.exit(1) for an unknown decision id"
            except SystemExit as e:
                assert e.code == 1, e.code
            finally:
                sys.stdout = old_stdout
            result = json.loads(captured.getvalue())
            assert result == {"id": 424242, "resolved": False}, result
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
    print("PASS: test_cli_resolve_unknown_id_exits_nonzero")


# ---------------------------------------------------------------------------
# Owner standing mandate (task-20260806-034817, cites UMR-20260805-185000-e94f):
# insert_owner_proposal()/decide_owner_proposal()/record_owner_proposal_completion()
# real end-to-end lifecycle, same real-isolated-temp-DB convention as every
# test above.
# ---------------------------------------------------------------------------
def test_insert_owner_proposal_mints_child_umr_and_opens_row():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_insert_proposal", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        decision_id, child_umr = sbr.insert_owner_proposal(
            conn, "real issue: X is broken", "AI proposes: fix X by doing Y",
        )
        conn.commit()
        assert isinstance(decision_id, int) and decision_id > 0, decision_id
        assert child_umr.startswith("UMR-"), child_umr

        row = dict(conn.execute("SELECT * FROM pm_decisions_pending WHERE id=?", (decision_id,)).fetchone())
        assert row["title"] == "real issue: X is broken"
        assert row["detail"] == "AI proposes: fix X by doing Y"
        assert row["related_umr"] == child_umr
        assert row["status"] == "open"
        assert row["decision_type"] == "owner_proposal"
        assert row["completed_ts"] is None and row["artifact_path"] is None
        conn.close()
    print("PASS: test_insert_owner_proposal_mints_child_umr_and_opens_row")


def test_insert_owner_proposal_accepts_explicit_child_umr():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_insert_proposal_explicit_umr", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        decision_id, child_umr = sbr.insert_owner_proposal(
            conn, "issue", "proposal", child_umr="UMR-20260806-000000-abcd",
        )
        conn.commit()
        assert child_umr == "UMR-20260806-000000-abcd"
        row = conn.execute("SELECT related_umr FROM pm_decisions_pending WHERE id=?", (decision_id,)).fetchone()
        assert row["related_umr"] == "UMR-20260806-000000-abcd"
        conn.close()
    print("PASS: test_insert_owner_proposal_accepts_explicit_child_umr")


def test_owner_proposal_pm_decision_and_ai_completion_full_round_trip():
    """The real end-to-end lifecycle the Owner's directive asks for: AI
    deposits a proposal, PM approves it citing its child UMR, AI records
    real completion evidence back onto that same row."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_proposal_round_trip", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        decision_id, child_umr = sbr.insert_owner_proposal(
            conn, "real issue: no test coverage for X", "AI proposes: add real tests for X",
        )
        conn.commit()

        decided = sbr.decide_owner_proposal(
            conn, decision_id, decision="approved", closed_by="pm-agent",
            closed_note=f"approved, see {child_umr}",
        )
        conn.commit()
        assert decided is True

        row = dict(conn.execute("SELECT * FROM pm_decisions_pending WHERE id=?", (decision_id,)).fetchone())
        assert row["status"] == "approved"
        assert row["closed_by"] == "pm-agent"
        assert child_umr in row["closed_note"]

        recorded = sbr.record_owner_proposal_completion(
            conn, decision_id, artifact_path="tests/test_pm_decisions_pending.py",
            commit_sha="deadbeef", evidence="real tests added and passing, see PR #123",
        )
        conn.commit()
        assert recorded is True

        final_row = dict(conn.execute("SELECT * FROM pm_decisions_pending WHERE id=?", (decision_id,)).fetchone())
        assert final_row["status"] == "completed"
        assert final_row["artifact_path"] == "tests/test_pm_decisions_pending.py"
        assert final_row["commit_sha"] == "deadbeef"
        assert final_row["evidence"] == "real tests added and passing, see PR #123"
        assert final_row["completed_ts"] is not None
        assert final_row["related_umr"] == child_umr
        conn.close()
    print("PASS: test_owner_proposal_pm_decision_and_ai_completion_full_round_trip")


def test_decide_owner_proposal_rejects_unknown_decision():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_decide_bad_decision", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        decision_id, _ = sbr.insert_owner_proposal(conn, "issue", "proposal")
        conn.commit()
        try:
            sbr.decide_owner_proposal(conn, decision_id, decision="rejected", closed_by="pm-agent")
            assert False, "expected ValueError for an invalid decision value"
        except ValueError:
            pass
        conn.close()
    print("PASS: test_decide_owner_proposal_rejects_unknown_decision")


def test_decide_owner_proposal_cannot_resolve_a_plain_pm_decision_row():
    """require_decision_type='owner_proposal' must refuse a real pm_decision
    row even if the numeric id matches -- the two real row shapes share one
    table but never one lifecycle."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_decide_wrong_type", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        plain_id = sbr.insert_pm_decision_pending(conn, "plain PM decision", "detail")
        conn.commit()

        decided = sbr.decide_owner_proposal(conn, plain_id, decision="approved", closed_by="pm-agent")
        conn.commit()
        assert decided is False

        row = conn.execute("SELECT status FROM pm_decisions_pending WHERE id=?", (plain_id,)).fetchone()
        assert row["status"] == "open"
        conn.close()
    print("PASS: test_decide_owner_proposal_cannot_resolve_a_plain_pm_decision_row")


def test_record_owner_proposal_completion_requires_approved_status():
    """Completion cannot be recorded on a still-open, redirected, or held
    proposal -- only one that the PM has real-approved."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_completion_requires_approved", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        still_open_id, _ = sbr.insert_owner_proposal(conn, "issue", "proposal")
        conn.commit()
        recorded = sbr.record_owner_proposal_completion(
            conn, still_open_id, artifact_path="x", commit_sha="y", evidence="z",
        )
        conn.commit()
        assert recorded is False

        redirected_id, _ = sbr.insert_owner_proposal(conn, "issue2", "proposal2")
        conn.commit()
        sbr.decide_owner_proposal(conn, redirected_id, decision="redirected", closed_by="pm-agent")
        conn.commit()
        recorded2 = sbr.record_owner_proposal_completion(
            conn, redirected_id, artifact_path="x", commit_sha="y", evidence="z",
        )
        conn.commit()
        assert recorded2 is False
        conn.close()
    print("PASS: test_record_owner_proposal_completion_requires_approved_status")


def test_record_owner_proposal_completion_is_idempotent():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_completion_idempotent", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        decision_id, _ = sbr.insert_owner_proposal(conn, "issue", "proposal")
        conn.commit()
        sbr.decide_owner_proposal(conn, decision_id, decision="approved", closed_by="pm-agent")
        conn.commit()

        first = sbr.record_owner_proposal_completion(
            conn, decision_id, artifact_path="first.py", commit_sha="aaa", evidence="first",
        )
        conn.commit()
        assert first is True
        row_after_first = dict(conn.execute("SELECT * FROM pm_decisions_pending WHERE id=?", (decision_id,)).fetchone())

        second = sbr.record_owner_proposal_completion(
            conn, decision_id, artifact_path="second.py", commit_sha="bbb", evidence="second",
        )
        conn.commit()
        assert second is False
        row_after_second = dict(conn.execute("SELECT * FROM pm_decisions_pending WHERE id=?", (decision_id,)).fetchone())
        assert row_after_second == row_after_first, (row_after_first, row_after_second)
        conn.close()
    print("PASS: test_record_owner_proposal_completion_is_idempotent")


def test_get_pm_decisions_pending_excludes_owner_proposals():
    """generate_pm_report_v3.py's Section 7 (plain PM decisions) must never
    show a real owner_proposal row -- those surface only in Section 8 (see
    test_generate_pm_report_v3.py)."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_section7_excludes_proposals", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        sbr.insert_pm_decision_pending(conn, "plain decision", "detail")
        sbr.insert_owner_proposal(conn, "issue", "proposal")
        conn.commit()

        spec = importlib.util.spec_from_file_location(
            "pm_report_v3_for_pm_decisions_test", os.path.join(SCRIPTS_DIR, "generate_pm_report_v3.py")
        )
        pm_report = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pm_report)

        decisions = pm_report.get_pm_decisions_pending(sbr)
        proposals = pm_report.get_owner_proposals_pending(sbr)
        assert len(decisions) == 1 and decisions[0]["title"] == "plain decision", decisions
        assert len(proposals) == 1 and proposals[0]["issue"] == "issue", proposals
        conn.close()
    print("PASS: test_get_pm_decisions_pending_excludes_owner_proposals")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
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
