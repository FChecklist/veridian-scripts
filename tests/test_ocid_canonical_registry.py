#!/usr/bin/env python3
"""Real tests for the OCID-001..068 canonical-UMR roster
(UMR-20260805-032326-becc, Owner directive citing UMR-20260802-165606-4413
OCID-020 and UMR-20260802-173631-ca85 OCID-021). Every test uses a real,
isolated, temp-file SQLite database seeded with the real schema -- never the
live production database.
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location("sbr_seed_ocid_canonical", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_canonical_registry_table(conn)
    conn.close()
    return sbr


def test_upsert_and_query_single_canonical_umr():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        sbr.upsert_ocid_canonical_registry(
            conn, "OCID-038", canonical_umr_id="UMR-20260804-090421-c647",
            status="merged (PR #886)", pr_number=886, pr_repo="compliance-tracker",
            all_umr_ids=["UMR-20260804-090421-c647"],
            evidence={"umr_tasks_task_identity_query": "owner-task-20260804-090421", "gh_pr_search": "OCID-038"},
        )
        conn.commit()
        rows = sbr.query_ocid_canonical_registry(conn, ocid_number="OCID-038")
        conn.close()
        assert len(rows) == 1
        row = rows[0]
        assert row["ocid_number"] == "OCID-038"
        assert row["canonical_umr_id"] == "UMR-20260804-090421-c647"
        assert row["pr_number"] == 886
        assert row["not_found"] == 0
        assert row["all_umr_ids"] == ["UMR-20260804-090421-c647"]
        assert row["evidence"]["gh_pr_search"] == "OCID-038"
        print("PASS: test_upsert_and_query_single_canonical_umr")


def test_duplicate_umrs_recorded_with_canonical_choice_and_reason():
    """Real requirement: 'if more than one UMR was ever minted for the same
    OCID due to a real duplicate dispatch, record all of them and mark which
    one is canonical, with the real reason.'"""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        sbr.upsert_ocid_canonical_registry(
            conn, "OCID-068", canonical_umr_id="UMR-20260804-170055-a069",
            status="completed",
            all_umr_ids=["UMR-20260804-170055-a069", "UMR-20260804-184014-9a18"],
            duplicate_reason="UMR-20260804-184014-9a18 was a real duplicate dispatch, correctly "
                              "rejected (status=rejected_duplicate) by the Stage 4/5/6 duplicate-PR "
                              "guard -- UMR-20260804-170055-a069 is canonical, the one real row "
                              "whose own work actually completed.",
            evidence={
                **{k: None for k in sbr.EVIDENCE_JSON_REQUIRED_KEYS},
                "umr_id": "UMR-20260804-170055-a069", "ocid_number": "OCID-068",
                "evidence_summary": "real duplicate dispatch recorded; canonical choice confirmed by grep.",
                "legacy_evidence": {"umr_tasks_dump_grep": "OCID-068"},
            },
        )
        conn.commit()
        row = sbr.query_ocid_canonical_registry(conn, ocid_number="OCID-068")[0]
        conn.close()
        assert row["canonical_umr_id"] == "UMR-20260804-170055-a069"
        assert len(row["all_umr_ids"]) == 2
        assert "UMR-20260804-184014-9a18" in row["all_umr_ids"]
        assert "duplicate dispatch" in row["duplicate_reason"]
        print("PASS: test_duplicate_umrs_recorded_with_canonical_choice_and_reason")


def test_not_found_recorded_honestly_not_left_blank():
    """Real requirement: 'Where no real UMR or real pull request can be
    found for an OCID after a real thorough search, record that fact
    honestly rather than leaving the row blank or guessing.'"""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        sbr.upsert_ocid_canonical_registry(
            conn, "OCID-999-NEVER-DISPATCHED", canonical_umr_id=None,
            status="no real UMR or PR found after thorough search", not_found=True,
            all_umr_ids=[],
            evidence={
                "umr_tasks_task_identity_substring": "zero rows",
                "umr_tasks_full_dump_grep": "zero rows",
                "git_log_compliance_tracker": "zero matches",
                "git_log_veridian_scripts": "zero matches",
                "git_log_projexa": "zero matches",
                "gh_pr_list_search_all_states": "zero matches",
                "master_tracker_yaml_grep": "zero matches",
                "active_claims_yaml_grep": "zero matches",
            },
        )
        conn.commit()
        row = sbr.query_ocid_canonical_registry(conn, ocid_number="OCID-999-NEVER-DISPATCHED")[0]
        conn.close()
        assert row["canonical_umr_id"] is None
        assert row["not_found"] == 1
        assert row["all_umr_ids"] == []
        assert len(row["evidence"]) == 8
        print("PASS: test_not_found_recorded_honestly_not_left_blank")


def test_upsert_is_idempotent_real_rerun_replaces_not_duplicates():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        sbr.upsert_ocid_canonical_registry(
            conn, "OCID-001", canonical_umr_id="UMR-OLD", status="open",
            all_umr_ids=["UMR-OLD"], evidence={"pass": 1},
        )
        conn.commit()
        sbr.upsert_ocid_canonical_registry(
            conn, "OCID-001", canonical_umr_id="UMR-NEW", status="closed",
            all_umr_ids=["UMR-OLD", "UMR-NEW"], evidence={"pass": 2},
        )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM ocid_canonical_registry WHERE ocid_number='OCID-001'").fetchone()[0]
        row = sbr.query_ocid_canonical_registry(conn, ocid_number="OCID-001")[0]
        conn.close()
        assert count == 1
        assert row["canonical_umr_id"] == "UMR-NEW"
        assert row["status"] == "closed"
        print("PASS: test_upsert_is_idempotent_real_rerun_replaces_not_duplicates")


def test_query_with_no_argument_returns_whole_real_roster():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        for n in ["OCID-001", "OCID-002", "OCID-003"]:
            sbr.upsert_ocid_canonical_registry(
                conn, n, canonical_umr_id=None, status="not_found", not_found=True,
                all_umr_ids=[], evidence={},
            )
        conn.commit()
        rows = sbr.query_ocid_canonical_registry(conn)
        conn.close()
        assert [r["ocid_number"] for r in rows] == ["OCID-001", "OCID-002", "OCID-003"]
        print("PASS: test_query_with_no_argument_returns_whole_real_roster")


if __name__ == "__main__":
    test_upsert_and_query_single_canonical_umr()
    test_duplicate_umrs_recorded_with_canonical_choice_and_reason()
    test_not_found_recorded_honestly_not_left_blank()
    test_upsert_is_idempotent_real_rerun_replaces_not_duplicates()
    test_query_with_no_argument_returns_whole_real_roster()
    print("ALL PASS")
