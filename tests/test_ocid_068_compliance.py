#!/usr/bin/env python3
"""Real tests for the OCID-068 seven-rule compliance tracking tables
(ocid_compliance_state / ocid_compliance_audit_log -- renamed from the
originally-requested ocid_068_compliance_state/ocid_068_compliance_audit_log
per UMR-20260805-093254-056e's own explicit authorization once real row
coverage became the full OCID-001..069 roster, not OCID-068 alone).
Covers UMR-20260805-093138-2bd0 (build), UMR-20260805-093254-056e (full
roster scope), citing the canonical OCID-068 UMR UMR-20260804-170055-a069.

Every test uses a real, isolated, temp-file SQLite database seeded with the
real schema -- never the live production database, same convention as
tests/test_ocid_canonical_registry.py.
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile
import time

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location(
        "sbr_seed_ocid_068_compliance", os.path.join(SCRIPTS_DIR, "superboss-register.py")
    )
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_canonical_registry_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    sbr._ensure_ocid_compliance_tables(conn)
    conn.close()
    return sbr


def _insert_umr_task(conn, umr_id, task_identity, ts_submitted, status="completed",
                      ts_completed="2026-08-05T01:00:00+00:00", last_heartbeat=None):
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
        "source_trigger, ts_completed, last_heartbeat) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (umr_id, task_identity, ts_submitted, 1, status, "owner", ts_completed, last_heartbeat),
    )


def _state_row(conn, ocid_number, umr_id):
    return dict(conn.execute(
        "SELECT * FROM ocid_compliance_state WHERE ocid_number=? AND umr_id=?", (ocid_number, umr_id)
    ).fetchone())


def test_full_compliance_audit_all_seven_rules_true_when_genuinely_satisfied():
    """A real UMR minted well after all 7 rule mechanisms merged, with no
    duplicate active UMR, a real ocid_artifact_links row, and normal
    terminal completion, must audit all 7 rules true and audit_passed=1."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        _insert_umr_task(conn, "UMR-20260805-000001-real1", "owner-task-ocid-970-real",
                          "2026-08-05T00:00:00+00:00")
        conn.commit()
        sbr.insert_ocid_artifact_link(conn, ocid_number="OCID-970", umr_id="UMR-20260805-000001-real1",
                                       repo="veridian-scripts", link_kind="closure", file_path="OCID_970.md")
        conn.commit()

        result = sbr.run_ocid_compliance_audit(
            conn, "OCID-970", "UMR-20260805-000001-real1",
            all_umr_ids=["UMR-20260805-000001-real1"], canonical_row=None, audited_by="test",
        )
        conn.commit()

        assert result["audit_passed"] is True, result
        for rule in sbr.OCID_COMPLIANCE_STATE_RULE_FIELDS:
            assert result["results"][rule] is True, (rule, result)

        row = _state_row(conn, "OCID-970", "UMR-20260805-000001-real1")
        assert row["audit_done"] == 1
        assert row["audit_passed"] == 1
        conn.close()
        print("PASS: test_full_compliance_audit_all_seven_rules_true_when_genuinely_satisfied")


def test_rule_honestly_false_when_mechanism_predates_its_own_pr():
    """A real UMR minted BEFORE a rule's own real mechanism PR merged must
    record that rule honestly false, with a real raw_output explanation
    naming the real PR and merge date -- never true, never silently null."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        # 2026-08-01 is before ALL 7 real rule-mechanism merge dates (all 2026-08-04)
        _insert_umr_task(conn, "UMR-20260801-000000-old2", "owner-task-ocid-971-old",
                          "2026-08-01T00:00:00+00:00")
        conn.commit()

        result = sbr.run_ocid_compliance_audit(
            conn, "OCID-971", "UMR-20260801-000000-old2",
            all_umr_ids=["UMR-20260801-000000-old2"], canonical_row=None, audited_by="test",
        )
        conn.commit()

        for rule in sbr.OCID_COMPLIANCE_STATE_RULE_FIELDS:
            assert result["results"][rule] is False, (rule, result)
        assert result["audit_passed"] is False

        logs = conn.execute(
            "SELECT rule_or_field_name, result, raw_output FROM ocid_compliance_audit_log "
            "WHERE ocid_number='OCID-971' AND rule_or_field_name='rule_1_umr_reuse_verified'"
        ).fetchall()
        assert len(logs) == 1
        assert logs[0]["result"] == 0
        assert "mechanism did not exist yet" in logs[0]["raw_output"]
        assert "PR #26" in logs[0]["raw_output"]
        conn.close()
        print("PASS: test_rule_honestly_false_when_mechanism_predates_its_own_pr")


def test_direct_sql_fabrication_of_compliance_state_is_overridden_by_trigger():
    """The real core anti-fabrication proof: a bare, hand-typed INSERT
    directly into ocid_compliance_state (bypassing run_ocid_compliance_audit/
    record_ocid_compliance_audit entirely), claiming full compliance for a
    pair with ZERO real ocid_compliance_audit_log rows, must have every one
    of its 13 real boolean columns AND audit_done/audit_passed overridden
    back to 0 by the derive trigger -- not merely rejected, the trigger's
    own real audit-log-derived recompute must win."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        cols = ", ".join(sbr.OCID_COMPLIANCE_STATE_BOOLEAN_FIELDS)
        placeholders = ", ".join("1" for _ in sbr.OCID_COMPLIANCE_STATE_BOOLEAN_FIELDS)
        conn.execute(
            f"INSERT INTO ocid_compliance_state (ocid_number, umr_id, {cols}, audit_done, audit_passed, last_audit_timestamp) "
            f"VALUES ('OCID-FAKE-972', 'UMR-FAKE-972', {placeholders}, 1, 1, '2026-01-01T00:00:00Z')"
        )
        conn.commit()

        row = _state_row(conn, "OCID-FAKE-972", "UMR-FAKE-972")
        assert row["audit_done"] == 0, f"GATE BYPASSED: audit_done fabricated true with zero real audit_log rows: {row}"
        assert row["audit_passed"] == 0, f"GATE BYPASSED: audit_passed fabricated true: {row}"
        for field in sbr.OCID_COMPLIANCE_STATE_BOOLEAN_FIELDS:
            assert row[field] == 0, f"GATE BYPASSED on {field}: {row}"
        conn.close()
        print("PASS: test_direct_sql_fabrication_of_compliance_state_is_overridden_by_trigger")


def test_direct_sql_update_on_real_row_still_derives_from_real_audit_log():
    """A real, already-audited row: a direct hand-set UPDATE attempt on its
    boolean columns must be overridden back to whatever the real,
    already-stored ocid_compliance_audit_log evidence actually says --
    proving the derivation is re-applied on every UPDATE, not only at
    INSERT time."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        _insert_umr_task(conn, "UMR-20260805-000002-real2", "owner-task-ocid-973-real",
                          "2026-08-05T00:00:00+00:00")
        conn.commit()
        result = sbr.run_ocid_compliance_audit(
            conn, "OCID-973", "UMR-20260805-000002-real2",
            all_umr_ids=["UMR-20260805-000002-real2"], canonical_row=None, audited_by="test",
        )
        conn.commit()
        # rule_7 (structured evidence) is genuinely False here -- no ocid_artifact_links row was ever inserted
        assert result["results"]["rule_7_structured_evidence_verified"] is False

        # attacker tries to hand-flip rule_7 to true directly
        conn.execute(
            "UPDATE ocid_compliance_state SET rule_7_structured_evidence_verified=1 WHERE ocid_number='OCID-973'"
        )
        conn.commit()
        row = _state_row(conn, "OCID-973", "UMR-20260805-000002-real2")
        assert row["rule_7_structured_evidence_verified"] == 0, f"GATE BYPASSED: {row}"
        conn.close()
        print("PASS: test_direct_sql_update_on_real_row_still_derives_from_real_audit_log")


def test_transactional_pairing_every_write_has_matching_audit_log_rows():
    """Every real write via run_ocid_compliance_audit() must produce exactly
    one real ocid_compliance_audit_log row per field (7 rules + 6 file
    fields = 13), all sharing the same real audit_timestamp as the
    resulting ocid_compliance_state.last_audit_timestamp -- current state
    and full history provably in sync."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        _insert_umr_task(conn, "UMR-20260805-000003-real3", "owner-task-ocid-974-real",
                          "2026-08-05T00:00:00+00:00")
        conn.commit()
        sbr.run_ocid_compliance_audit(
            conn, "OCID-974", "UMR-20260805-000003-real3",
            all_umr_ids=["UMR-20260805-000003-real3"], canonical_row=None, audited_by="test",
        )
        conn.commit()

        row = _state_row(conn, "OCID-974", "UMR-20260805-000003-real3")
        log_rows = conn.execute(
            "SELECT rule_or_field_name, audit_timestamp FROM ocid_compliance_audit_log "
            "WHERE ocid_number='OCID-974' AND umr_id='UMR-20260805-000003-real3'"
        ).fetchall()
        assert len(log_rows) == 13, f"expected 13 real audit_log rows (7 rules + 6 file fields), got {len(log_rows)}"
        assert all(r["audit_timestamp"] == row["last_audit_timestamp"] for r in log_rows)
        field_names = {r["rule_or_field_name"] for r in log_rows}
        assert field_names == set(sbr.OCID_COMPLIANCE_STATE_BOOLEAN_FIELDS)
        conn.close()
        print("PASS: test_transactional_pairing_every_write_has_matching_audit_log_rows")


def test_audit_log_is_append_only_across_repeated_real_audits():
    """Re-running a real audit for the same (ocid, umr) pair must ADD new
    real audit_log rows (append-only, full history preserved) while the
    current-state row stays exactly one row per pair (upsert, not a new
    row)."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        _insert_umr_task(conn, "UMR-20260805-000004-real4", "owner-task-ocid-975-real",
                          "2026-08-05T00:00:00+00:00")
        conn.commit()
        for _ in range(3):
            sbr.run_ocid_compliance_audit(
                conn, "OCID-975", "UMR-20260805-000004-real4",
                all_umr_ids=["UMR-20260805-000004-real4"], canonical_row=None, audited_by="test",
            )
            conn.commit()

        state_count = conn.execute(
            "SELECT COUNT(*) c FROM ocid_compliance_state WHERE ocid_number='OCID-975'"
        ).fetchone()["c"]
        log_count = conn.execute(
            "SELECT COUNT(*) c FROM ocid_compliance_audit_log WHERE ocid_number='OCID-975'"
        ).fetchone()["c"]
        assert state_count == 1, f"expected exactly 1 real current-state row, got {state_count}"
        assert log_count == 39, f"expected 3 real runs x 13 real fields = 39 real audit_log rows, got {log_count}"
        conn.close()
        print("PASS: test_audit_log_is_append_only_across_repeated_real_audits")


def test_no_infinite_trigger_recursion_or_hang():
    """Real, timing-bounded proof: repeated real audit runs (each firing
    both derive triggers) complete near-instantly, not hang."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        assert conn.execute("PRAGMA recursive_triggers").fetchone()[0] == 0

        start = time.monotonic()
        for i in range(40):
            _insert_umr_task(conn, f"UMR-20260805-0001{i:02d}-loop", f"owner-task-ocid-loop2-{i}",
                              "2026-08-05T00:00:00+00:00")
            sbr.run_ocid_compliance_audit(
                conn, f"OCID-LOOP2-{i}", f"UMR-20260805-0001{i:02d}-loop",
                all_umr_ids=[f"UMR-20260805-0001{i:02d}-loop"], canonical_row=None, audited_by="test",
            )
        conn.commit()
        elapsed = time.monotonic() - start
        assert elapsed < 15, f"40 real audit runs took {elapsed:.2f}s -- possible recursion/hang"
        conn.close()
        print(f"PASS: test_no_infinite_trigger_recursion_or_hang ({elapsed:.3f}s for 40 audit runs)")


if __name__ == "__main__":
    test_full_compliance_audit_all_seven_rules_true_when_genuinely_satisfied()
    test_rule_honestly_false_when_mechanism_predates_its_own_pr()
    test_direct_sql_fabrication_of_compliance_state_is_overridden_by_trigger()
    test_direct_sql_update_on_real_row_still_derives_from_real_audit_log()
    test_transactional_pairing_every_write_has_matching_audit_log_rows()
    test_audit_log_is_append_only_across_repeated_real_audits()
    test_no_infinite_trigger_recursion_or_hang()
    print("ALL PASS")
