#!/usr/bin/env python3
"""Real tests for propose_child_umr_action()/pm_decide_on_proposal()/
record_proposal_completion() (standing PM-decision-gate workflow,
UMR-20260806-034750-05cf, parent chain UMR-20260805-185000-e94f /
UMR-20260802-165606-4413 / OCID-020: "thinking is by the Project Manager,
execution is by AI agents, AI agents do not think for themselves").

Same real, isolated, temp-file SQLite convention as
tests/test_pm_decisions_pending.py -- never the live production database.
Every test here also verifies the real design decision documented in
superboss-register.py's own comment block above pm_child_umr_proposals:
these three functions never write a live, dispatch-eligible umr_tasks row
(no call to resource_governor.py's submit()) -- see
test_propose_child_umr_action_never_touches_umr_tasks below.
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
    "id", "proposed_ts", "title", "issue", "proposed_action", "proposed_by",
    "related_umr", "child_umr_id", "status", "decided_ts", "decided_by",
    "decision", "decision_note", "completed_ts", "completed_by",
    "completion_commit", "completion_file_path", "completion_evidence",
]


def _load(name, filename, env=None):
    """Same load-with-env-override convention as
    tests/test_pm_decisions_pending.py's own _load(): resolve_superboss_db_path()
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
    """Pre-create a real, fully-initialized scratch DB -- same convention as
    tests/test_pm_decisions_pending.py's own _seed_scratch_db(): monkeypatches
    a freshly-loaded, throwaway module instance's own _connect() to point at
    `path` directly, never touching the module-global DB_PATH/
    SUPERBOSS_REGISTER_DB resolution, so real init_db() schema-creation logic
    is reused without any risk of writing to the real, live, production
    database."""
    spec = importlib.util.spec_from_file_location("sbr_seed_child_umr", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)

    def _scratch_connect():
        conn = sqlite3.connect(path, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row
        return conn

    sbr._connect = _scratch_connect
    _real_stdout, sys.stdout = sys.stdout, io.StringIO()
    try:
        sbr.init_db()
    finally:
        sys.stdout = _real_stdout
    conn = _scratch_connect()
    sbr._ensure_pm_child_umr_proposals_table(conn)
    conn.close()


def _scratch_env(scratch_db):
    return {"SUPERBOSS_REGISTER_DB": scratch_db}


# ---------------------------------------------------------------------------
# Schema pin
# ---------------------------------------------------------------------------
def test_ensure_table_matches_schema_columns():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_schema_columns", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(pm_child_umr_proposals)").fetchall()]
        assert cols == _LIVE_SCHEMA_COLUMNS, cols
        # calling it again against a DB that already has rows must be a true no-op
        sbr.propose_child_umr_action(conn, "pre-existing", "issue", "action", proposed_by="tester")
        conn.commit()
        sbr._ensure_pm_child_umr_proposals_table(conn)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) AS c FROM pm_child_umr_proposals").fetchone()["c"] == 1
        conn.close()
    print("PASS: test_ensure_table_matches_schema_columns")


# ---------------------------------------------------------------------------
# propose_child_umr_action()
# ---------------------------------------------------------------------------
def test_propose_child_umr_action_round_trip():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_propose_round_trip", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        proposal_id, child_umr_id = sbr.propose_child_umr_action(
            conn, "Real test proposal", "Real issue text", "Real proposed action text",
            proposed_by="test-agent", related_umr="UMR-parent-0001",
        )
        conn.commit()
        assert isinstance(proposal_id, int) and proposal_id > 0, proposal_id
        assert child_umr_id.startswith("UMR-"), child_umr_id

        row = dict(conn.execute("SELECT * FROM pm_child_umr_proposals WHERE id=?", (proposal_id,)).fetchone())
        assert row["title"] == "Real test proposal"
        assert row["issue"] == "Real issue text"
        assert row["proposed_action"] == "Real proposed action text"
        assert row["proposed_by"] == "test-agent"
        assert row["related_umr"] == "UMR-parent-0001"
        assert row["child_umr_id"] == child_umr_id
        assert row["status"] == "proposed"
        assert row["decision"] is None and row["decided_ts"] is None and row["decided_by"] is None
        assert row["completed_ts"] is None and row["completion_commit"] is None
        conn.close()
    print("PASS: test_propose_child_umr_action_round_trip")


def test_propose_child_umr_action_never_touches_umr_tasks():
    """Real design-decision verification: propose_child_umr_action() mints a
    real UMR-formatted child_umr_id but must NEVER write a live umr_tasks
    row for it (that would make it real-dispatch-eligible within ~30 seconds
    via resource_governor.py's live tick loop, before any PM decision --
    see the real reasoning in superboss-register.py's own comment block
    above pm_child_umr_proposals)."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_propose_no_umr_tasks", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        umr_tasks_before = conn.execute("SELECT COUNT(*) AS c FROM umr_tasks").fetchone()["c"]
        proposal_id, child_umr_id = sbr.propose_child_umr_action(
            conn, "No live UMR row", "issue", "action", proposed_by="test-agent",
        )
        conn.commit()
        umr_tasks_after = conn.execute("SELECT COUNT(*) AS c FROM umr_tasks").fetchone()["c"]
        assert umr_tasks_after == umr_tasks_before, "propose_child_umr_action() must never insert into umr_tasks"
        found = conn.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (child_umr_id,)).fetchone()
        assert found is None, "child_umr_id must not exist as a live umr_tasks row"
        conn.close()
    print("PASS: test_propose_child_umr_action_never_touches_umr_tasks")


# ---------------------------------------------------------------------------
# pm_decide_on_proposal()
# ---------------------------------------------------------------------------
def test_pm_decide_on_proposal_approve():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_decide_approve", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        proposal_id, _ = sbr.propose_child_umr_action(conn, "t", "i", "a", proposed_by="agent")
        conn.commit()

        decided = sbr.pm_decide_on_proposal(conn, proposal_id, "approve", "pm-tester", note="looks good")
        conn.commit()
        assert decided is True

        row = dict(conn.execute("SELECT * FROM pm_child_umr_proposals WHERE id=?", (proposal_id,)).fetchone())
        assert row["status"] == "approved"
        assert row["decision"] == "approve"
        assert row["decided_by"] == "pm-tester"
        assert row["decision_note"] == "looks good"
        assert row["decided_ts"] is not None
        conn.close()
    print("PASS: test_pm_decide_on_proposal_approve")


def test_pm_decide_on_proposal_redirect_then_reapprove():
    """Real redirect path: a redirect changes scope/approach (note cites
    what changed) and leaves the row awaiting a fresh decision -- it must
    stay real-decidable afterward (e.g. a later approve)."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_decide_redirect", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        proposal_id, _ = sbr.propose_child_umr_action(conn, "t", "i", "a", proposed_by="agent")
        conn.commit()

        redirected = sbr.pm_decide_on_proposal(
            conn, proposal_id, "redirect", "pm-tester",
            note="scope changed: fix the shared writer instead of the caller",
        )
        conn.commit()
        assert redirected is True
        row = dict(conn.execute("SELECT * FROM pm_child_umr_proposals WHERE id=?", (proposal_id,)).fetchone())
        assert row["status"] == "redirected"
        assert row["decision"] == "redirect"
        assert "scope changed" in row["decision_note"]

        # real re-decision after redirect
        approved = sbr.pm_decide_on_proposal(conn, proposal_id, "approve", "pm-tester", note="now correct")
        conn.commit()
        assert approved is True
        row2 = dict(conn.execute("SELECT * FROM pm_child_umr_proposals WHERE id=?", (proposal_id,)).fetchone())
        assert row2["status"] == "approved"
        assert row2["decision"] == "approve"
        conn.close()
    print("PASS: test_pm_decide_on_proposal_redirect_then_reapprove")


def test_pm_decide_on_proposal_hold():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_decide_hold", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        proposal_id, _ = sbr.propose_child_umr_action(conn, "t", "i", "a", proposed_by="agent")
        conn.commit()

        held = sbr.pm_decide_on_proposal(conn, proposal_id, "hold", "pm-tester", note="waiting on budget")
        conn.commit()
        assert held is True
        row = dict(conn.execute("SELECT * FROM pm_child_umr_proposals WHERE id=?", (proposal_id,)).fetchone())
        assert row["status"] == "held"
        assert row["decision"] == "hold"
        assert row["decision_note"] == "waiting on budget"
        conn.close()
    print("PASS: test_pm_decide_on_proposal_hold")


def test_pm_decide_on_proposal_unknown_id_returns_false():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_decide_unknown", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        decided = sbr.pm_decide_on_proposal(conn, 999999, "approve", "pm-tester")
        conn.commit()
        assert decided is False
        conn.close()
    print("PASS: test_pm_decide_on_proposal_unknown_id_returns_false")


def test_pm_decide_on_proposal_already_completed_is_noop():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_decide_completed_noop", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        proposal_id, _ = sbr.propose_child_umr_action(conn, "t", "i", "a", proposed_by="agent")
        conn.commit()
        sbr.pm_decide_on_proposal(conn, proposal_id, "approve", "pm-tester")
        conn.commit()
        sbr.record_proposal_completion(conn, proposal_id, "abc123", "foo.py", "real evidence", "agent")
        conn.commit()

        decided_again = sbr.pm_decide_on_proposal(conn, proposal_id, "hold", "pm-tester")
        conn.commit()
        assert decided_again is False
        row = dict(conn.execute("SELECT * FROM pm_child_umr_proposals WHERE id=?", (proposal_id,)).fetchone())
        assert row["status"] == "completed"
        conn.close()
    print("PASS: test_pm_decide_on_proposal_already_completed_is_noop")


def test_pm_decide_on_proposal_rejects_invalid_decision():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_decide_invalid", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        proposal_id, _ = sbr.propose_child_umr_action(conn, "t", "i", "a", proposed_by="agent")
        conn.commit()
        try:
            sbr.pm_decide_on_proposal(conn, proposal_id, "not-a-real-decision", "pm-tester")
            assert False, "expected ValueError for an invalid decision value"
        except ValueError:
            pass
        conn.close()
    print("PASS: test_pm_decide_on_proposal_rejects_invalid_decision")


# ---------------------------------------------------------------------------
# record_proposal_completion()
# ---------------------------------------------------------------------------
def test_record_proposal_completion_requires_approved():
    """A proposal still 'proposed' (never approved) must not be completable
    -- completion without a prior real PM approve is exactly the 'AI thinks
    for itself' failure this workflow exists to prevent."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_complete_requires_approved", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        proposal_id, _ = sbr.propose_child_umr_action(conn, "t", "i", "a", proposed_by="agent")
        conn.commit()

        completed = sbr.record_proposal_completion(conn, proposal_id, "abc123", "foo.py", "evidence", "agent")
        conn.commit()
        assert completed is False
        row = dict(conn.execute("SELECT * FROM pm_child_umr_proposals WHERE id=?", (proposal_id,)).fetchone())
        assert row["status"] == "proposed"
        assert row["completion_commit"] is None
        conn.close()
    print("PASS: test_record_proposal_completion_requires_approved")


def test_record_proposal_completion_idempotent():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_complete_idempotent", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        proposal_id, _ = sbr.propose_child_umr_action(conn, "t", "i", "a", proposed_by="agent")
        conn.commit()
        sbr.pm_decide_on_proposal(conn, proposal_id, "approve", "pm-tester")
        conn.commit()

        first = sbr.record_proposal_completion(conn, proposal_id, "sha-first", "first.py", "first evidence", "agent-1")
        conn.commit()
        assert first is True
        row_after_first = dict(conn.execute("SELECT * FROM pm_child_umr_proposals WHERE id=?", (proposal_id,)).fetchone())

        second = sbr.record_proposal_completion(conn, proposal_id, "sha-second", "second.py", "second evidence", "agent-2")
        conn.commit()
        assert second is False
        row_after_second = dict(conn.execute("SELECT * FROM pm_child_umr_proposals WHERE id=?", (proposal_id,)).fetchone())
        assert row_after_second == row_after_first, (row_after_first, row_after_second)
        conn.close()
    print("PASS: test_record_proposal_completion_idempotent")


# ---------------------------------------------------------------------------
# get_open_child_umr_proposals() -- report-section read helper
# ---------------------------------------------------------------------------
def test_get_open_child_umr_proposals_filters_correctly():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_open_proposals_filter", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        p_proposed, _ = sbr.propose_child_umr_action(conn, "still proposed", "i", "a", proposed_by="agent")
        p_redirected, _ = sbr.propose_child_umr_action(conn, "redirected", "i", "a", proposed_by="agent")
        p_held, _ = sbr.propose_child_umr_action(conn, "held", "i", "a", proposed_by="agent")
        p_approved, _ = sbr.propose_child_umr_action(conn, "approved", "i", "a", proposed_by="agent")
        p_completed, _ = sbr.propose_child_umr_action(conn, "completed", "i", "a", proposed_by="agent")
        conn.commit()
        sbr.pm_decide_on_proposal(conn, p_redirected, "redirect", "pm", note="changed")
        sbr.pm_decide_on_proposal(conn, p_held, "hold", "pm")
        sbr.pm_decide_on_proposal(conn, p_approved, "approve", "pm")
        sbr.pm_decide_on_proposal(conn, p_completed, "approve", "pm")
        conn.commit()
        sbr.record_proposal_completion(conn, p_completed, "sha", "f.py", "ev", "agent")
        conn.commit()

        open_proposals = sbr.get_open_child_umr_proposals(conn)
        ids = {p["id"] for p in open_proposals}
        assert ids == {p_proposed, p_redirected}, ids
        conn.close()
    print("PASS: test_get_open_child_umr_proposals_filters_correctly")


# ---------------------------------------------------------------------------
# Full real round-trip: propose -> approve -> complete
# ---------------------------------------------------------------------------
def test_full_round_trip_propose_approve_complete():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_full_round_trip", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        proposal_id, child_umr_id = sbr.propose_child_umr_action(
            conn, "Full round-trip test", "Real issue", "Real proposed action",
            proposed_by="test-agent", related_umr="UMR-parent-9999",
        )
        conn.commit()
        row = dict(conn.execute("SELECT * FROM pm_child_umr_proposals WHERE id=?", (proposal_id,)).fetchone())
        assert row["status"] == "proposed"

        # still visible in the open-proposals report read
        assert proposal_id in {p["id"] for p in sbr.get_open_child_umr_proposals(conn)}

        decided = sbr.pm_decide_on_proposal(conn, proposal_id, "approve", "pm-tester", note="approved for round-trip test")
        conn.commit()
        assert decided is True
        row = dict(conn.execute("SELECT * FROM pm_child_umr_proposals WHERE id=?", (proposal_id,)).fetchone())
        assert row["status"] == "approved"

        # no longer in the open-proposals report read once approved
        assert proposal_id not in {p["id"] for p in sbr.get_open_child_umr_proposals(conn)}

        completed = sbr.record_proposal_completion(
            conn, proposal_id, "deadbeef1234", "tests/test_pm_child_umr_proposals.py",
            "real round-trip test evidence", "test-agent",
        )
        conn.commit()
        assert completed is True

        final_row = dict(conn.execute("SELECT * FROM pm_child_umr_proposals WHERE id=?", (proposal_id,)).fetchone())
        assert final_row["status"] == "completed"
        assert final_row["completion_commit"] == "deadbeef1234"
        assert final_row["completion_file_path"] == "tests/test_pm_child_umr_proposals.py"
        assert final_row["completion_evidence"] == "real round-trip test evidence"
        assert final_row["completed_by"] == "test-agent"
        assert final_row["completed_ts"] is not None
        assert final_row["child_umr_id"] == child_umr_id
        conn.close()
    print("PASS: test_full_round_trip_propose_approve_complete")


# ---------------------------------------------------------------------------
# CLI end-to-end
# ---------------------------------------------------------------------------
def test_cli_propose_decide_complete_end_to_end():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_cli_e2e", "superboss-register.py", env=_scratch_env(scratch_db))
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            propose_args = argparse.Namespace(
                title="CLI proposal", issue="CLI issue", proposed_action="CLI action",
                proposed_by="cli-agent", related_umr="UMR-cli-0001",
            )
            captured = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured
            try:
                sbr.cmd_propose_child_umr_action(propose_args)
            finally:
                sys.stdout = old_stdout
            proposed = json.loads(captured.getvalue())
            proposal_id = proposed["proposal_id"]
            assert isinstance(proposal_id, int) and proposal_id > 0, proposed
            assert proposed["child_umr_id"].startswith("UMR-"), proposed

            decide_args = argparse.Namespace(
                proposal_id=proposal_id, decision="approve", decided_by="cli-pm", note="cli approve",
            )
            captured = io.StringIO()
            sys.stdout = captured
            try:
                sbr.cmd_pm_decide_on_proposal(decide_args)
            finally:
                sys.stdout = old_stdout
            decided = json.loads(captured.getvalue())
            assert decided == {"proposal_id": proposal_id, "decision": "approve", "decided": True}, decided

            complete_args = argparse.Namespace(
                proposal_id=proposal_id, commit_sha="cafef00d", file_path="cli_file.py",
                evidence="cli evidence", completed_by="cli-agent",
            )
            captured = io.StringIO()
            sys.stdout = captured
            try:
                sbr.cmd_record_proposal_completion(complete_args)
            finally:
                sys.stdout = old_stdout
            completed = json.loads(captured.getvalue())
            assert completed == {"proposal_id": proposal_id, "completed": True}, completed

            conn = sbr._connect()
            row = dict(conn.execute("SELECT * FROM pm_child_umr_proposals WHERE id=?", (proposal_id,)).fetchone())
            assert row["status"] == "completed"
            assert row["completion_commit"] == "cafef00d"
            conn.close()
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
    print("PASS: test_cli_propose_decide_complete_end_to_end")


def test_cli_decide_unknown_id_exits_nonzero():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_cli_decide_unknown", "superboss-register.py", env=_scratch_env(scratch_db))
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            decide_args = argparse.Namespace(
                proposal_id=424242, decision="approve", decided_by="cli-pm", note=None,
            )
            captured = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured
            try:
                sbr.cmd_pm_decide_on_proposal(decide_args)
                assert False, "expected sys.exit(1) for an unknown proposal id"
            except SystemExit as e:
                assert e.code == 1, e.code
            finally:
                sys.stdout = old_stdout
            result = json.loads(captured.getvalue())
            assert result == {"proposal_id": 424242, "decision": "approve", "decided": False}, result
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
    print("PASS: test_cli_decide_unknown_id_exits_nonzero")


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
