#!/usr/bin/env python3
"""Real tests for the critical amendment to UMR-20260806-124327-6ffb / stop
work order UMR-20260806-124055-bc80 (this task's own scoped UMR:
UMR-20260806-124654-a8d6, task-20260806-181146-critical-amendment--every-task-must-sear).

Covers the required deterministic-first task sequence added directly to
superboss-register.py (the one canonical read/write script for this DB, per
its own top-of-file CANONICAL SCRIPT statement -- not a second standalone
script):
  - search_task_precedent()/cmd_search_task_precedent(): steps one+two,
    read-only. Step one -- exact/FTS capability_registry match, no AI. Step
    two -- real cross-history search over umr_tasks + capability_graduation_log
    for similar past work already done, anywhere, not scoped to one UMR.
  - record_capability_graduation()/cmd_record_capability_graduation(): step
    four, the real critical new requirement -- the mandatory, never-skippable
    post-AI-work evaluation, recorded either way (graduated with a real
    registered capability_id+script_path, or judgment_required with a plain
    reason), never silently narrated away.

Every test uses a real, isolated, temp-file SQLite database seeded with the
real schema (same convention as tests/test_pm_decisions_pending.py /
tests/test_ocid_artifact_links.py) -- never the live production database.
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
    """Pre-create a real, fully-initialized scratch DB via the real init_db()
    schema-creation logic, monkeypatching only this throwaway module
    instance's own _connect() -- same safe convention as
    tests/test_pm_decisions_pending.py's own _seed_scratch_db(), never risks a
    write to the real, live, production database."""
    spec = importlib.util.spec_from_file_location("sbr_seed_grad", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
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
    sbr._ensure_capability_registry_table(conn)
    sbr._ensure_capability_graduation_log_table(conn)
    conn.close()


def _scratch_env(scratch_db):
    return {"SUPERBOSS_REGISTER_DB": scratch_db}


def _register_test_capability(sbr, conn, capability_name="test_existing_script"):
    """Registers one real capability_registry row the same way register_capability()
    itself does, via a real --record-file JSON, so step-one lookups in these
    tests exercise the real FTS-backed path, not a hand-inserted row."""
    with tempfile.TemporaryDirectory() as d:
        record_path = os.path.join(d, "record.json")
        with open(record_path, "w") as f:
            json.dump({
                "capability_name": capability_name,
                "inputs": ["x"],
                "business_rules": ["rule 1"],
                "apis": [],
                "permissions": "internal",
                "ai_required": False,
                "confidence": 1.0,
                "version": "1.0",
                "owner": "test-suite",
            }, f)
        args = argparse.Namespace(record_file=record_path)
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            sbr.register_capability(args)
        finally:
            sys.stdout = old_stdout
        return json.loads(captured.getvalue())["capability_id"]


# ---------------------------------------------------------------------------
# Steps one + two: search_task_precedent()
# ---------------------------------------------------------------------------

def test_search_task_precedent_step_one_exact_capability_match():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_step1", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        cap_id = _register_test_capability(sbr, conn, "commission_calculator_v2")
        conn.close()

        conn = sbr._connect()
        result = sbr.search_task_precedent(conn, "commission_calculator_v2")
        conn.close()
        assert result["step"] == 1, result
        assert result["action"] == "exact_script_found_run_it_no_ai_stop", result
        assert result["resolution_stage_used"] == "exact_capability_name_match", result
        assert result["broad_keyword_overlap"] is False, result
        assert any(m["capability_id"] == cap_id for m in result["matches"]), result
    print("PASS: test_search_task_precedent_step_one_exact_capability_match")


def test_search_task_precedent_step_one_keyword_fallback_reports_stage():
    """A task_text that is NOT a real capability_name still finds it via the
    FTS fallback (same domain_scoped_keyword_match stage lookup_capability()
    uses) -- resolution_stage_used must say so honestly, not claim an exact
    match that never happened."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_step1_keyword", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        _register_test_capability(sbr, conn, "trend_analysis_engine_v3")
        conn.close()

        conn = sbr._connect()
        result = sbr.search_task_precedent(conn, "run the trend_analysis_engine_v3 report")
        conn.close()
        assert result["step"] == 1, result
        assert result["resolution_stage_used"] == "domain_scoped_keyword_match", result
    print("PASS: test_search_task_precedent_step_one_keyword_fallback_reports_stage")


def test_search_task_precedent_step_two_finds_past_umr_and_graduation():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_step2", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        # Deliberately an unrelated capability_name -- if this test used a name
        # resembling the search text below, step one's own FTS match (which
        # searches utm_term, itself derived from capability_name) would find
        # it and short-circuit before step two is ever reached, defeating the
        # point of this test.
        cap_id = _register_test_capability(sbr, conn, "unrelated_commission_calculator")
        # A real past umr_tasks row for a similar kind of task, done under a
        # DIFFERENT UMR than the one this search will be run under -- proving
        # this is a real cross-history search, not scoped to one UMR's own
        # past.
        conn.execute(
            "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, source_trigger, task_kind) "
            "VALUES (?,?,?,?,?,?,?)",
            ("UMR-test-precedent-0001", "gratuity edge case handling for exit employees",
             sbr._now_iso(), 2, "completed", "owner_dispatch_gateway", "veridian_task_create"),
        )
        conn.commit()
        gid = sbr.record_capability_graduation(
            conn, "UMR-test-precedent-0001", "AGENT-test-0001",
            "built the gratuity edge case handler script", "graduated",
            "purely rule-based, no judgment needed once inputs are structured",
            capability_id=cap_id, script_path="gratuity_calculator.py",
        )
        conn.commit()
        conn.close()

        conn = sbr._connect()
        result = sbr.search_task_precedent(conn, "gratuity edge case handling")
        conn.close()
        assert result["step"] == 2, result
        assert result["action"] == "similar_past_work_found_report_script_or_agent_ids_used", result
        matched = [m for m in result["matches"] if m["umr_id"] == "UMR-test-precedent-0001"]
        assert matched, result
        assert matched[0]["graduation"]["graduation_id"] == gid
        assert matched[0]["graduation"]["capability_id"] == cap_id
        assert matched[0]["graduation"]["agent_id"] == "AGENT-test-0001"
    print("PASS: test_search_task_precedent_step_two_finds_past_umr_and_graduation")


def test_search_task_precedent_step_three_no_script_no_precedent():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_step3", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        result = sbr.search_task_precedent(conn, "wildly novel unprecedented xyzzy task quux")
        conn.close()
        assert result["step"] == 3, result
        assert result["action"] == "no_script_and_no_usable_precedent_ai_work_proceeds_under_umr_scoped_agent_id", result
        assert result["matches"] == []
    print("PASS: test_search_task_precedent_step_three_no_script_no_precedent")


def test_cmd_search_task_precedent_end_to_end():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_cmd_precedent", "superboss-register.py", env=_scratch_env(scratch_db))
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            args = argparse.Namespace(task_text="wildly novel unprecedented xyzzy task quux", limit=10)
            captured = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured
            try:
                sbr.cmd_search_task_precedent(args)
            finally:
                sys.stdout = old_stdout
            result = json.loads(captured.getvalue())
            assert result["step"] == 3, result
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
    print("PASS: test_cmd_search_task_precedent_end_to_end")


# ---------------------------------------------------------------------------
# Step four: record_capability_graduation()
# ---------------------------------------------------------------------------

def test_record_graduation_graduated_requires_capability_id_and_script_path():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_grad_requires", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        try:
            sbr.record_capability_graduation(
                conn, "UMR-test-0002", "AGENT-test-0002", "did some work", "graduated",
                "reason given but no proof it was actually built",
            )
            assert False, "expected ValueError: graduated with no capability_id/script_path"
        except ValueError:
            pass
        conn.close()
    print("PASS: test_record_graduation_graduated_requires_capability_id_and_script_path")


def test_record_graduation_judgment_required_rejects_capability_id():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_grad_no_cap_for_judgment", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        try:
            sbr.record_capability_graduation(
                conn, "UMR-test-0003", "AGENT-test-0003", "did some judgment-heavy work",
                "judgment_required", "genuinely requires human/AI judgment every time",
                capability_id="CAP-should-not-be-here",
            )
            assert False, "expected ValueError: judgment_required must not carry a capability_id"
        except ValueError:
            pass
        conn.close()
    print("PASS: test_record_graduation_judgment_required_rejects_capability_id")


def test_record_graduation_requires_nonempty_reason():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_grad_reason", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        try:
            sbr.record_capability_graduation(
                conn, "UMR-test-0004", "AGENT-test-0004", "did some work", "judgment_required", "   ",
            )
            assert False, "expected ValueError: blank reason"
        except ValueError:
            pass
        conn.close()
    print("PASS: test_record_graduation_requires_nonempty_reason")


def test_record_graduation_graduated_round_trip():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_grad_round_trip", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        cap_id = _register_test_capability(sbr, conn, "graduated_test_script")
        gid = sbr.record_capability_graduation(
            conn, "UMR-test-0005", "AGENT-test-0005", "built a fixed-rule calculator",
            "graduated", "purely deterministic once inputs are structured, no judgment needed",
            capability_id=cap_id, script_path="some_new_script.py",
            metadata={"registered_by_task": "task-test"},
        )
        conn.commit()
        assert gid.startswith("GRAD-"), gid

        row = dict(conn.execute(
            "SELECT * FROM capability_graduation_log WHERE graduation_id=?", (gid,)
        ).fetchone())
        assert row["umr_id"] == "UMR-test-0005"
        assert row["agent_id"] == "AGENT-test-0005"
        assert row["decision"] == "graduated"
        assert row["capability_id"] == cap_id
        assert row["script_path"] == "some_new_script.py"
        assert json.loads(row["metadata_json"]) == {"registered_by_task": "task-test"}
        conn.close()
    print("PASS: test_record_graduation_graduated_round_trip")


def test_record_graduation_judgment_required_round_trip():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_grad_judgment_round_trip", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        gid = sbr.record_capability_graduation(
            conn, "UMR-test-0006", "AGENT-test-0006", "wrote a narrative risk assessment",
            "judgment_required", "genuinely requires judgment: reading free-text context and weighing intent every time",
        )
        conn.commit()

        row = dict(conn.execute(
            "SELECT * FROM capability_graduation_log WHERE graduation_id=?", (gid,)
        ).fetchone())
        assert row["decision"] == "judgment_required"
        assert row["capability_id"] is None
        assert row["script_path"] is None
        assert "judgment" in row["reason"]
        conn.close()
    print("PASS: test_record_graduation_judgment_required_round_trip")


def test_cli_record_graduation_and_list_end_to_end():
    """End-to-end through the real argparse cmd_* wrappers, same
    argparse.Namespace-based in-process convention as
    tests/test_pm_decisions_pending.py's test_cli_insert_and_resolve_end_to_end."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_cli_grad", "superboss-register.py", env=_scratch_env(scratch_db))
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            conn = sbr._connect()
            cap_id = _register_test_capability(sbr, conn, "cli_graduated_script")
            conn.close()

            record_args = argparse.Namespace(
                umr_id="UMR-test-cli-0001", agent_id="AGENT-test-cli-0001",
                task_summary="built a CLI-registered script", decision="graduated",
                reason="deterministic once inputs are structured",
                capability_id=cap_id, script_path="cli_graduated_script.py", metadata=None,
            )
            captured = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured
            try:
                sbr.cmd_record_capability_graduation(record_args)
            finally:
                sys.stdout = old_stdout
            recorded = json.loads(captured.getvalue())
            assert recorded["umr_id"] == "UMR-test-cli-0001"
            assert recorded["decision"] == "graduated"

            list_args = argparse.Namespace(umr_id="UMR-test-cli-0001")
            captured = io.StringIO()
            sys.stdout = captured
            try:
                sbr.list_capability_graduations(list_args)
            finally:
                sys.stdout = old_stdout
            listed = json.loads(captured.getvalue())
            assert listed["count"] == 1, listed
            assert listed["graduations"][0]["graduation_id"] == recorded["graduation_id"]
            assert listed["graduations"][0]["capability_id"] == cap_id
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
    print("PASS: test_cli_record_graduation_and_list_end_to_end")


def test_cli_record_graduation_missing_proof_exits_nonzero():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_cli_grad_bad", "superboss-register.py", env=_scratch_env(scratch_db))
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            record_args = argparse.Namespace(
                umr_id="UMR-test-cli-0002", agent_id="AGENT-test-cli-0002",
                task_summary="claims graduation with no real script", decision="graduated",
                reason="claims to be deterministic", capability_id=None, script_path=None, metadata=None,
            )
            captured = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = captured
            try:
                sbr.cmd_record_capability_graduation(record_args)
                assert False, "expected sys.exit(1)"
            except SystemExit as e:
                assert e.code == 1, e.code
            finally:
                sys.stdout = old_stdout
            result = json.loads(captured.getvalue())
            assert "error" in result, result
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
    print("PASS: test_cli_record_graduation_missing_proof_exits_nonzero")


def test_ensure_table_is_idempotent_and_matches_schema():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_grad_schema", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(capability_graduation_log)").fetchall()}
        assert cols == {
            "graduation_id", "ts", "umr_id", "agent_id", "task_summary",
            "decision", "reason", "capability_id", "script_path", "metadata_json",
        }, cols
        sbr.record_capability_graduation(
            conn, "UMR-test-0007", "AGENT-test-0007", "x", "judgment_required", "y",
        )
        conn.commit()
        sbr._ensure_capability_graduation_log_table(conn)
        conn.commit()
        assert conn.execute("SELECT COUNT(*) AS c FROM capability_graduation_log").fetchone()["c"] == 1
        conn.close()
    print("PASS: test_ensure_table_is_idempotent_and_matches_schema")


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
