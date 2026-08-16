#!/usr/bin/env python3
"""Real tests for OCID Master Standard v6 Phase 2 -- lifecycle state machine
+ registry integrity checks (Owner directive, this task; parent references
UMR-20260804-170055-a069, canonical OCID-068 UMR, real status completed, and
UMR-20260805-032731-b412, OCID-068 permanent closure record, real status
completed, PR #52 merge commit c46da9b777e2a8a60e15230dacd72f2329e885af).
Every test uses a real, isolated, temp-file SQLite database seeded with the
real schema -- never the live production database -- same convention as
tests/test_ocid_master_standard_phase1.py.

Covers:
  - validate_lifecycle_transition(): legal sequential transitions, illegal
    skips, failure reachable from any active state, rolled_back only from
    failed, closed/rolled_back terminal
  - transition_ocid_lifecycle_state(): real DB writes on legal transitions
    (+ audit event), real refusal (no write) + audit event on illegal
    transitions, refusal when a different umr_id is passed for an OCID that
    already has lifecycle state
  - resume_ocid_lifecycle(): a real resume reuses the same OCID+UMR and
    continues from the last real checkpoint, never restarting from zero
  - check_registry_integrity(): schema_version_ok, checksum_ok (including
    the honest not-yet-established case), foreign_keys_ok, orphan_rows_ok,
    duplicate_index_ok, and a real drift-detected case
  - build_step_result_contract(): every step at/after a real failure point
    is forced False, never omitted
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location(
        "sbr_seed_ocid_master_standard_phase2", os.path.join(SCRIPTS_DIR, "superboss-register.py")
    )
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    sbr._ensure_ocid_canonical_registry_table(conn)
    sbr._ensure_ocid_master_standard_audit_log_table(conn)
    sbr._ensure_ocid_compliance_tables(conn)
    sbr._ensure_ocid_master_standard_phase2_tables(conn)
    conn.close()
    return sbr


def _insert_umr_task(conn, umr_id, task_identity, status="queued", tier=1):
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, source_trigger) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (umr_id, task_identity, "2026-08-05T00:00:00+00:00", tier, status, "owner"),
    )
    conn.commit()


def _open(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


# --- validate_lifecycle_transition() (pure) ---------------------------------

def test_validate_lifecycle_transition_full_sequential_path_is_legal():
    with tempfile.TemporaryDirectory() as d:
        sbr = _seed_scratch_db(os.path.join(d, "scratch.sqlite"))
        # Walk the real, locked main path start to finish.
        sequence = ["created", "registered", "dispatched", "running", "testing",
                    "pull_request_created", "merged", "verified", "closed"]
        prev = None
        for state in sequence:
            ok, reason = sbr.validate_lifecycle_transition(prev, state)
            assert ok, f"expected legal transition {prev} -> {state}, got refused: {reason}"
            prev = state
        print("PASS: test_validate_lifecycle_transition_full_sequential_path_is_legal")


def test_validate_lifecycle_transition_rejects_illegal_skip():
    with tempfile.TemporaryDirectory() as d:
        sbr = _seed_scratch_db(os.path.join(d, "scratch.sqlite"))
        ok, reason = sbr.validate_lifecycle_transition("created", "running")
        assert ok is False
        assert "illegal transition" in reason
        print("PASS: test_validate_lifecycle_transition_rejects_illegal_skip")


def test_validate_lifecycle_transition_failed_reachable_from_any_active_state():
    with tempfile.TemporaryDirectory() as d:
        sbr = _seed_scratch_db(os.path.join(d, "scratch.sqlite"))
        for state in ["created", "registered", "dispatched", "running", "testing",
                      "pull_request_created", "merged", "verified"]:
            ok, reason = sbr.validate_lifecycle_transition(state, "failed")
            assert ok, f"expected '{state}' -> 'failed' to be legal, got refused: {reason}"
        print("PASS: test_validate_lifecycle_transition_failed_reachable_from_any_active_state")


def test_validate_lifecycle_transition_rolled_back_only_from_failed():
    with tempfile.TemporaryDirectory() as d:
        sbr = _seed_scratch_db(os.path.join(d, "scratch.sqlite"))
        ok, _ = sbr.validate_lifecycle_transition("failed", "rolled_back")
        assert ok is True
        ok2, reason2 = sbr.validate_lifecycle_transition("running", "rolled_back")
        assert ok2 is False
        assert "illegal transition" in reason2
        print("PASS: test_validate_lifecycle_transition_rolled_back_only_from_failed")


def test_validate_lifecycle_transition_closed_and_rolled_back_are_terminal():
    with tempfile.TemporaryDirectory() as d:
        sbr = _seed_scratch_db(os.path.join(d, "scratch.sqlite"))
        for terminal in ["closed", "rolled_back"]:
            for target in sbr.OCID_LIFECYCLE_STATES:
                ok, reason = sbr.validate_lifecycle_transition(terminal, target)
                assert ok is False, f"expected terminal state '{terminal}' to reject '{target}'"
        print("PASS: test_validate_lifecycle_transition_closed_and_rolled_back_are_terminal")


def test_validate_lifecycle_transition_initial_transition_must_be_created():
    with tempfile.TemporaryDirectory() as d:
        sbr = _seed_scratch_db(os.path.join(d, "scratch.sqlite"))
        ok, _ = sbr.validate_lifecycle_transition(None, "created")
        assert ok is True
        ok2, reason2 = sbr.validate_lifecycle_transition(None, "registered")
        assert ok2 is False
        assert "initial transition" in reason2
        print("PASS: test_validate_lifecycle_transition_initial_transition_must_be_created")


# --- transition_ocid_lifecycle_state() (real DB writes) ---------------------

def test_transition_ocid_lifecycle_state_writes_row_and_audit_event_on_legal_transition():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = _open(path)
        _insert_umr_task(conn, "UMR-20260805-100000-a001", "OCID-900 lifecycle test")

        result = sbr.transition_ocid_lifecycle_state(conn, "OCID-900", "UMR-20260805-100000-a001", "created")
        assert result["ok"] is True
        row = sbr.get_ocid_lifecycle_state(conn, "OCID-900")
        assert row["current_state"] == "created"
        assert row["umr_id"] == "UMR-20260805-100000-a001"

        events = conn.execute(
            "SELECT * FROM ocid_master_standard_audit_log WHERE event_type='lifecycle_transition' "
            "AND ocid_number='OCID-900'"
        ).fetchall()
        assert len(events) == 1
        conn.close()
        print("PASS: test_transition_ocid_lifecycle_state_writes_row_and_audit_event_on_legal_transition")


def test_transition_ocid_lifecycle_state_illegal_transition_writes_no_row_but_is_audited():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = _open(path)
        _insert_umr_task(conn, "UMR-20260805-100000-a002", "OCID-901 lifecycle test")

        result = sbr.transition_ocid_lifecycle_state(conn, "OCID-901", "UMR-20260805-100000-a002", "running")
        assert result["ok"] is False

        row = sbr.get_ocid_lifecycle_state(conn, "OCID-901")
        assert row is None  # no real write happened

        events = conn.execute(
            "SELECT * FROM ocid_master_standard_audit_log WHERE event_type='lifecycle_transition_refused' "
            "AND ocid_number='OCID-901'"
        ).fetchall()
        assert len(events) == 1
        detail = json.loads(events[0]["detail_json"])
        assert detail["to_state"] == "running"
        conn.close()
        print("PASS: test_transition_ocid_lifecycle_state_illegal_transition_writes_no_row_but_is_audited")


def test_transition_ocid_lifecycle_state_refuses_a_second_umr_for_same_ocid():
    """The real Owner-directive requirement: never mint a second UMR for the
    same unit of work. A transition attempt under a different umr_id for an
    OCID that already has real lifecycle state must be refused, not silently
    allowed to hijack the row."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = _open(path)
        _insert_umr_task(conn, "UMR-20260805-100000-a003", "OCID-902 lifecycle test")
        _insert_umr_task(conn, "UMR-20260805-100000-a004", "OCID-902 duplicate dispatch")

        r1 = sbr.transition_ocid_lifecycle_state(conn, "OCID-902", "UMR-20260805-100000-a003", "created")
        assert r1["ok"] is True

        r2 = sbr.transition_ocid_lifecycle_state(conn, "OCID-902", "UMR-20260805-100000-a004", "registered")
        assert r2["ok"] is False
        assert "mint a second UMR" in r2["reason"]

        row = sbr.get_ocid_lifecycle_state(conn, "OCID-902")
        assert row["current_state"] == "created"  # unchanged
        assert row["umr_id"] == "UMR-20260805-100000-a003"  # unchanged
        conn.close()
        print("PASS: test_transition_ocid_lifecycle_state_refuses_a_second_umr_for_same_ocid")


# --- resume_ocid_lifecycle(): real checkpoint resume, same OCID+UMR --------

def test_resume_ocid_lifecycle_continues_from_last_checkpoint_same_ocid_and_umr():
    """A real interrupt-and-resume: an OCID is driven partway through the
    lifecycle, 'interrupted' (this test process simply stops calling
    transition), then a fresh resume_ocid_lifecycle() call must report the
    exact real last checkpoint -- not None, not reset to 'created' -- under
    the SAME real OCID and UMR, and the next transition from there must
    still be legal."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = _open(path)
        ocid, umr_id = "OCID-903", "UMR-20260805-100000-a005"
        _insert_umr_task(conn, umr_id, "OCID-903 resume test")

        for state in ["created", "registered", "dispatched", "running"]:
            r = sbr.transition_ocid_lifecycle_state(conn, ocid, umr_id, state)
            assert r["ok"] is True
        # Simulate an interrupt: no further transitions this "run".

        checkpoint = sbr.resume_ocid_lifecycle(conn, ocid)
        assert checkpoint is not None
        assert checkpoint["ocid_number"] == ocid
        assert checkpoint["umr_id"] == umr_id  # same real OCID+UMR reused, never a second one
        assert checkpoint["current_state"] == "running"  # real last checkpoint, not reset to 'created'

        # Resume genuinely continues from there, not from zero.
        r_resume = sbr.transition_ocid_lifecycle_state(conn, ocid, umr_id, "testing")
        assert r_resume["ok"] is True
        assert r_resume["from_state"] == "running"
        conn.close()
        print("PASS: test_resume_ocid_lifecycle_continues_from_last_checkpoint_same_ocid_and_umr")


def test_resume_ocid_lifecycle_returns_none_when_no_checkpoint_exists():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = _open(path)
        assert sbr.resume_ocid_lifecycle(conn, "OCID-999-NEVER-STARTED") is None
        conn.close()
        print("PASS: test_resume_ocid_lifecycle_returns_none_when_no_checkpoint_exists")


# --- check_registry_integrity() ---------------------------------------------

def test_check_registry_integrity_all_ok_after_establishing_baseline():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = _open(path)
        sbr.establish_ocid_registry_schema_baseline(conn)
        result = sbr.check_registry_integrity(conn)
        assert result["schema_version_ok"] is True
        assert result["checksum_ok"] is True
        assert result["foreign_keys_ok"] is True
        assert result["orphan_rows_ok"] is True
        assert result["duplicate_index_ok"] is True
        assert result["all_ok"] is True
        conn.close()
        print("PASS: test_check_registry_integrity_all_ok_after_establishing_baseline")


def test_check_registry_integrity_checksum_not_ok_when_no_baseline_established():
    """Honest reporting: an unestablished checksum baseline must never be
    silently treated as passing."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = _open(path)
        result = sbr.check_registry_integrity(conn)
        assert result["checksum_ok"] is False
        assert result["all_ok"] is False
        assert result["checksum_detail"]["baseline_checksum"] is None
        conn.close()
        print("PASS: test_check_registry_integrity_checksum_not_ok_when_no_baseline_established")


def test_check_registry_integrity_detects_real_schema_drift():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = _open(path)
        sbr.establish_ocid_registry_schema_baseline(conn)
        # Real, deliberate drift: add a column to a tracked table after the
        # baseline was established.
        conn.execute("ALTER TABLE ocid_lifecycle_state ADD COLUMN unexpected_column TEXT")
        conn.commit()
        result = sbr.check_registry_integrity(conn)
        assert result["checksum_ok"] is False
        assert result["all_ok"] is False
        conn.close()
        print("PASS: test_check_registry_integrity_detects_real_schema_drift")


def test_check_registry_integrity_detects_real_orphan_row():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = _open(path)
        sbr.establish_ocid_registry_schema_baseline(conn)
        _insert_umr_task(conn, "UMR-20260805-100000-a006", "orphan test")
        # Real orphan: a compliance-state row for an OCID that has no real
        # ocid_canonical_registry row at all.
        conn.execute(
            "INSERT INTO ocid_compliance_state (ocid_number, umr_id) VALUES (?, ?)",
            ("OCID-ORPHAN-777", "UMR-20260805-100000-a006"),
        )
        conn.commit()
        result = sbr.check_registry_integrity(conn)
        assert result["orphan_rows_ok"] is False
        assert result["all_ok"] is False
        assert any(r["ocid_number"] == "OCID-ORPHAN-777" for r in result["orphan_rows"])
        conn.close()
        print("PASS: test_check_registry_integrity_detects_real_orphan_row")


def test_check_registry_integrity_detects_real_duplicate_index():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = _open(path)
        sbr.establish_ocid_registry_schema_baseline(conn)
        # Real duplicate: a second index covering the exact same column as
        # the existing idx_ocid_lifecycle_state_state index.
        conn.execute(
            "CREATE INDEX idx_ocid_lifecycle_state_state_dup ON ocid_lifecycle_state(current_state)"
        )
        conn.commit()
        result = sbr.check_registry_integrity(conn)
        assert result["duplicate_index_ok"] is False
        assert result["all_ok"] is False
        assert any(dup["table"] == "ocid_lifecycle_state" for dup in result["duplicate_indexes"])
        conn.close()
        print("PASS: test_check_registry_integrity_detects_real_duplicate_index")


# --- build_step_result_contract(): failure forces every later step False ---

def test_build_step_result_contract_forces_every_step_after_failure_to_false():
    step_order = ["bootstrap", "registration", "audit", "implementation", "tests", "pr", "merge", "certify"]
    results = {
        "bootstrap": True, "registration": True, "audit": False,
        "implementation": True,  # a real caller bug: claims success past a real failure point
        "tests": True, "pr": True, "merge": True, "certify": True,
    }
    contract = build_step_result_contract_helper(step_order, results)
    assert contract["bootstrap"] is True
    assert contract["registration"] is True
    assert contract["audit"] is False
    for step in ["implementation", "tests", "pr", "merge", "certify"]:
        assert contract[step] is False, f"expected '{step}' forced False after the real failure at 'audit'"
    assert contract["all_ok"] is False
    print("PASS: test_build_step_result_contract_forces_every_step_after_failure_to_false")


def test_build_step_result_contract_missing_step_treated_as_false_never_omitted():
    step_order = ["a", "b", "c"]
    results = {"a": True}  # "b" and "c" never ran
    contract = build_step_result_contract_helper(step_order, results)
    assert contract["a"] is True
    assert contract["b"] is False
    assert contract["c"] is False
    assert set(contract.keys()) == {"a", "b", "c", "all_ok"}  # never silently omitted
    print("PASS: test_build_step_result_contract_missing_step_treated_as_false_never_omitted")


def test_build_step_result_contract_explicit_failed_at_overrides_later_claimed_success():
    step_order = ["a", "b", "c", "d"]
    results = {"a": True, "b": True, "c": True, "d": True}  # all claim success
    contract = build_step_result_contract_helper(step_order, results, failed_at="b")
    assert contract["a"] is True
    assert contract["b"] is False
    assert contract["c"] is False
    assert contract["d"] is False
    assert contract["all_ok"] is False
    print("PASS: test_build_step_result_contract_explicit_failed_at_overrides_later_claimed_success")


def test_build_step_result_contract_all_true_is_all_ok():
    step_order = ["a", "b", "c"]
    results = {"a": True, "b": True, "c": True}
    contract = build_step_result_contract_helper(step_order, results)
    assert contract["all_ok"] is True
    print("PASS: test_build_step_result_contract_all_true_is_all_ok")


def build_step_result_contract_helper(step_order, results, failed_at=None):
    with tempfile.TemporaryDirectory() as d:
        sbr = _seed_scratch_db(os.path.join(d, "scratch.sqlite"))
        return sbr.build_step_result_contract(step_order, results, failed_at=failed_at)


if __name__ == "__main__":
    test_validate_lifecycle_transition_full_sequential_path_is_legal()
    test_validate_lifecycle_transition_rejects_illegal_skip()
    test_validate_lifecycle_transition_failed_reachable_from_any_active_state()
    test_validate_lifecycle_transition_rolled_back_only_from_failed()
    test_validate_lifecycle_transition_closed_and_rolled_back_are_terminal()
    test_validate_lifecycle_transition_initial_transition_must_be_created()
    test_transition_ocid_lifecycle_state_writes_row_and_audit_event_on_legal_transition()
    test_transition_ocid_lifecycle_state_illegal_transition_writes_no_row_but_is_audited()
    test_transition_ocid_lifecycle_state_refuses_a_second_umr_for_same_ocid()
    test_resume_ocid_lifecycle_continues_from_last_checkpoint_same_ocid_and_umr()
    test_resume_ocid_lifecycle_returns_none_when_no_checkpoint_exists()
    test_check_registry_integrity_all_ok_after_establishing_baseline()
    test_check_registry_integrity_checksum_not_ok_when_no_baseline_established()
    test_check_registry_integrity_detects_real_schema_drift()
    test_check_registry_integrity_detects_real_orphan_row()
    test_check_registry_integrity_detects_real_duplicate_index()
    test_build_step_result_contract_forces_every_step_after_failure_to_false()
    test_build_step_result_contract_missing_step_treated_as_false_never_omitted()
    test_build_step_result_contract_explicit_failed_at_overrides_later_claimed_success()
    test_build_step_result_contract_all_true_is_all_ok()
    print("ALL PASS")
