#!/usr/bin/env python3
"""Real tests for audit_ocid_compliance.py's `--report` flag and the
underlying real read-only query_ocid_compliance_state() function in
superboss-register.py (Owner directive UMR-20260805-093138-2bd0's real
report command, citing the anti-fabrication principle of
UMR-20260805-092408-4f97 and the canonical OCID-068 UMR
UMR-20260804-170055-a069).

This is the "third step" of the real deposit/compute/report architecture:
`--report` is a completely separate, read-only path that runs NO audit,
makes NO gh/git subprocess call, and writes NOTHING -- it only reads back
booleans the ocid_compliance_state_derive_ai/_au triggers already computed,
deterministically, from ocid_compliance_audit_log's own real, append-only
evidence at write time (see tests/test_ocid_068_compliance.py for that
trigger's own dedicated coverage; this file does not re-test it, only the
new read-only report path built on top of it, per zero duplication).

Every test uses a real, isolated, temp-file SQLite database seeded with the
real schema -- never the live production database, same convention as every
other OCID-068 test file in this repo.
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_sbr():
    spec = importlib.util.spec_from_file_location(
        "sbr_seed_audit_ocid_compliance_report", os.path.join(SCRIPTS_DIR, "superboss-register.py")
    )
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def _load_report_script():
    spec = importlib.util.spec_from_file_location(
        "audit_ocid_compliance_report_test", os.path.join(SCRIPTS_DIR, "audit_ocid_compliance.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_scratch_db(path):
    sbr = _load_sbr()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_canonical_registry_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    sbr._ensure_ocid_compliance_tables(conn)
    conn.close()
    return sbr


def _insert_umr_task(conn, umr_id, task_identity, ts_submitted, status="completed",
                      ts_completed="2026-08-05T01:00:00+00:00"):
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
        "source_trigger, ts_completed) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (umr_id, task_identity, ts_submitted, 1, status, "owner", ts_completed),
    )


def _seed_one_fully_compliant_pair(sbr, conn, ocid_number, umr_id):
    """Real fixture: one (ocid, umr) pair genuinely satisfying all 7 rules,
    same construction as test_ocid_068_compliance.py's own
    test_full_compliance_audit_all_seven_rules_true_when_genuinely_satisfied,
    reused here rather than reinvented (zero duplication) -- just enough
    real setup for --report to have real, non-trivial stored state to read
    back."""
    _insert_umr_task(conn, umr_id, f"owner-task-{ocid_number.lower()}", ts_submitted="2026-08-05T00:00:00+00:00")
    conn.commit()
    sbr.insert_ocid_artifact_link(conn, ocid_number=ocid_number, umr_id=umr_id,
                                   repo="veridian-scripts", link_kind="closure",
                                   file_path=f"{ocid_number}_TEST.md")
    conn.commit()
    sbr.upsert_ocid_canonical_registry(
        conn, ocid_number, canonical_umr_id=umr_id, status="merged (PR #1000)",
        pr_number=1000, pr_repo="veridian-scripts", all_umr_ids=[umr_id],
        evidence={"gh_pr_search": ocid_number}, merge_status="merged",
        file_path=f"{ocid_number}_TEST.md", evidence_summary=f"{ocid_number} closed by real merged PR #1000.",
        commit_sha="0123456789abcdef0123456789abcdef01234567",
    )
    conn.commit()
    canonical_row = sbr.query_ocid_canonical_registry(conn, ocid_number=ocid_number)[0]
    sbr.run_ocid_compliance_audit(
        conn, ocid_number, umr_id, all_umr_ids=[umr_id], canonical_row=canonical_row,
        audited_by="test_audit_ocid_compliance_report.py",
    )
    conn.commit()


def test_report_reads_back_real_trigger_computed_state_verbatim():
    """--report's own build_compliance_report() must reflect exactly what
    query_ocid_compliance_state() (itself a thin read of trigger-computed
    columns) returns -- no re-derivation, no re-audit."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        report_mod = _load_report_script()
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        _seed_one_fully_compliant_pair(sbr, conn, "OCID-960", "UMR-20260805-000000-a960")
        state_rows = sbr.query_ocid_compliance_state(conn)
        conn.close()

        report = report_mod.build_compliance_report(state_rows, sbr.OCID_COMPLIANCE_STATE_RULE_FIELDS)
        assert report["mode"] == "report"
        assert report["read_only"] is True
        assert report["wrote_to_database"] is False
        assert report["summary"]["total_pairs"] == 1
        assert report["summary"]["audited_pairs"] == 1
        assert report["summary"]["fully_compliant_pairs"] == 1
        row = report["rows"][0]
        assert row["ocid_number"] == "OCID-960"
        assert row["umr_id"] == "UMR-20260805-000000-a960"
        assert row["audit_passed"] is True
        for rule in sbr.OCID_COMPLIANCE_STATE_RULE_FIELDS:
            assert row[rule] is True, f"{rule} expected True on a genuinely fully-compliant fixture: {row}"
        print("PASS: test_report_reads_back_real_trigger_computed_state_verbatim")


def test_report_never_fabricates_pass_for_unaudited_pair():
    """A real (ocid, umr) pair registered in ocid_canonical_registry but
    never actually audited must report audit_done=False, audit_passed=False,
    and every rule_* as False -- never a silent/guessed True -- exactly the
    same honest-zero-by-COALESCE guarantee
    _ensure_ocid_compliance_state_derive_triggers() already documents,
    re-confirmed here from the real read-only report path specifically."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        report_mod = _load_report_script()
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        conn.execute(
            "INSERT INTO ocid_compliance_state (ocid_number, umr_id) VALUES (?, ?)",
            ("OCID-961", "UMR-20260805-000000-a961"),
        )
        conn.commit()
        state_rows = sbr.query_ocid_compliance_state(conn, ocid_number="OCID-961")
        conn.close()

        report = report_mod.build_compliance_report(state_rows, sbr.OCID_COMPLIANCE_STATE_RULE_FIELDS)
        row = report["rows"][0]
        assert row["audit_done"] is False
        assert row["audit_passed"] is False
        for rule in sbr.OCID_COMPLIANCE_STATE_RULE_FIELDS:
            assert row[rule] is False, f"GATE BYPASSED: {rule} read True for a never-audited pair: {row}"
        print("PASS: test_report_never_fabricates_pass_for_unaudited_pair")


def test_compute_step_identical_result_on_two_separate_runs_against_unchanged_data():
    """Owner directive's own explicit requirement: the real compute step
    (query_ocid_compliance_state(), reading purely trigger-derived columns)
    must produce the identical real result on two separate real runs against
    the same real unchanged data. Runs the real read TWICE, from two
    independent connections, with no write in between, and asserts
    byte-identical structured output -- and separately re-confirms the same
    determinism at the higher --report layer."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        report_mod = _load_report_script()
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        _seed_one_fully_compliant_pair(sbr, conn, "OCID-962", "UMR-20260805-000000-a962")
        conn.close()

        conn1 = sqlite3.connect(path)
        conn1.row_factory = sqlite3.Row
        state_rows_1 = sbr.query_ocid_compliance_state(conn1)
        conn1.close()

        conn2 = sqlite3.connect(path)
        conn2.row_factory = sqlite3.Row
        state_rows_2 = sbr.query_ocid_compliance_state(conn2)
        conn2.close()

        assert state_rows_1 == state_rows_2, (
            "real compute step (query_ocid_compliance_state) is NOT deterministic "
            f"across two separate runs against unchanged data:\n{state_rows_1}\nvs\n{state_rows_2}"
        )

        report_1 = report_mod.build_compliance_report(state_rows_1, sbr.OCID_COMPLIANCE_STATE_RULE_FIELDS)
        report_2 = report_mod.build_compliance_report(state_rows_2, sbr.OCID_COMPLIANCE_STATE_RULE_FIELDS)
        assert report_1 == report_2, "real --report output is NOT deterministic across two identical runs"
        print("PASS: test_compute_step_identical_result_on_two_separate_runs_against_unchanged_data")


if __name__ == "__main__":
    test_report_reads_back_real_trigger_computed_state_verbatim()
    test_report_never_fabricates_pass_for_unaudited_pair()
    test_compute_step_identical_result_on_two_separate_runs_against_unchanged_data()
    print("ALL PASS")
