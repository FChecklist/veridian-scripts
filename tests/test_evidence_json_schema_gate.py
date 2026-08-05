#!/usr/bin/env python3
"""Real tests for the OCID Master Standard v6 evidence_json schema
standardization gate (this cycle's Owner directive; citing
UMR-20260804-170055-a069 [canonical OCID-068 UMR] and
UMR-20260805-032326-becc [real OCID canonical roster build]).

Every test uses a real, isolated, temp-file SQLite database seeded with the
real schema -- never the live production database -- same convention as
tests/test_ocid_canonical_registry.py / tests/test_ocid_master_standard_phase1.py.

Covers:
  - _status_claims_verified_or_completed(): real completion-claim detection,
    including the two real false-positive traps found in the live 69-row
    registry ('running, never completed', 'ts_completed=null'/'NOT_VERIFIED')
  - validate_evidence_json_schema(): missing-key detection, empty/blank
    evidence_summary rejection, honest-null values accepted, extra keys
    (e.g. preserved legacy free-text evidence) always allowed
  - refuse_ocid_registry_completion_if_evidence_incomplete(): the pure,
    zero-I/O gate itself
  - upsert_ocid_canonical_registry(): real structural enforcement --
    refuses (raises OcidEvidenceSchemaRefused, writes nothing, records a
    real 'evidence_schema_refused' audit event) for a status that claims
    completion with incomplete evidence_json; allows the same incomplete
    evidence_json when status does not claim completion; allows a
    completion-claiming status once evidence_json is schema-complete
"""
import importlib.util
import os
import sqlite3
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location(
        "sbr_seed_evidence_schema_gate", os.path.join(SCRIPTS_DIR, "superboss-register.py")
    )
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_ocid_canonical_registry_table(conn)
    sbr._ensure_ocid_master_standard_audit_log_table(conn)
    conn.close()
    return sbr


def _full_evidence(sbr, **overrides):
    ev = {k: None for k in sbr.EVIDENCE_JSON_REQUIRED_KEYS}
    ev["evidence_summary"] = "real recovered evidence sentence for this test."
    ev.update(overrides)
    return ev


def test_status_claims_verified_or_completed_real_cases():
    with tempfile.TemporaryDirectory() as d:
        sbr = _seed_scratch_db(os.path.join(d, "scratch.sqlite"))

    # real genuine completion claims (drawn from the live 69-row registry)
    assert sbr._status_claims_verified_or_completed("completed") is True
    assert sbr._status_claims_verified_or_completed(
        "completed (historical, pre-OCID-numbering, no implementation authorized under OCID label)"
    ) is True
    assert sbr._status_claims_verified_or_completed("completed/closed (confirmed clean 2026-08-05)") is True
    assert sbr._status_claims_verified_or_completed("Certification VERIFIED for this row") is True

    # real false-positive traps this detector must NOT flag
    assert sbr._status_claims_verified_or_completed(
        "running, never completed (historical, pre-OCID-numbering)"
    ) is False
    assert sbr._status_claims_verified_or_completed(
        "running (live umr_tasks: status=running, ts_completed=null; "
        "MASTER-TRACKER.yaml's own SEC-07 gate block independently reports status NOT_VERIFIED)"
    ) is False
    assert sbr._status_claims_verified_or_completed("not completed yet") is False
    assert sbr._status_claims_verified_or_completed(None) is False
    assert sbr._status_claims_verified_or_completed("") is False
    print("PASS: test_status_claims_verified_or_completed_real_cases")


def test_validate_evidence_json_schema_missing_keys():
    with tempfile.TemporaryDirectory() as d:
        sbr = _seed_scratch_db(os.path.join(d, "scratch.sqlite"))

    ok, problems, reason = sbr.validate_evidence_json_schema({"commit_sha": "abc123"})
    assert ok is False
    assert "evidence_summary" in problems
    assert "umr_id" in problems
    assert reason is not None
    print("PASS: test_validate_evidence_json_schema_missing_keys")


def test_validate_evidence_json_schema_blank_or_missing_summary_rejected():
    with tempfile.TemporaryDirectory() as d:
        sbr = _seed_scratch_db(os.path.join(d, "scratch.sqlite"))

    ev = _full_evidence(sbr, evidence_summary="   ")
    ok, problems, _reason = sbr.validate_evidence_json_schema(ev)
    assert ok is False
    assert problems == ["evidence_summary"]
    print("PASS: test_validate_evidence_json_schema_blank_or_missing_summary_rejected")


def test_validate_evidence_json_schema_honest_nulls_and_extra_keys_allowed():
    with tempfile.TemporaryDirectory() as d:
        sbr = _seed_scratch_db(os.path.join(d, "scratch.sqlite"))

    # every field honestly null except evidence_summary -- must be VALID:
    # this is exactly the real shape of a not_found / never-dispatched row.
    ev = _full_evidence(sbr)
    ev["legacy_evidence"] = {"method": "prior free-text search note, preserved, not discarded"}
    ok, problems, reason = sbr.validate_evidence_json_schema(ev)
    assert ok is True
    assert problems == []
    assert reason is None
    print("PASS: test_validate_evidence_json_schema_honest_nulls_and_extra_keys_allowed")


def test_validate_evidence_json_schema_rejects_non_dict():
    with tempfile.TemporaryDirectory() as d:
        sbr = _seed_scratch_db(os.path.join(d, "scratch.sqlite"))

    ok, problems, reason = sbr.validate_evidence_json_schema("not a dict")
    assert ok is False
    assert reason is not None
    print("PASS: test_validate_evidence_json_schema_rejects_non_dict")


def test_refuse_gate_allows_non_completion_status_with_incomplete_evidence():
    with tempfile.TemporaryDirectory() as d:
        sbr = _seed_scratch_db(os.path.join(d, "scratch.sqlite"))

    verdict, reason = sbr.refuse_ocid_registry_completion_if_evidence_incomplete(
        "OCID-900", "open", {"note": "legacy free text, not schema-complete"}
    )
    assert verdict is True
    assert "does not itself claim" in reason
    print("PASS: test_refuse_gate_allows_non_completion_status_with_incomplete_evidence")


def test_refuse_gate_refuses_completion_status_with_incomplete_evidence():
    with tempfile.TemporaryDirectory() as d:
        sbr = _seed_scratch_db(os.path.join(d, "scratch.sqlite"))

    verdict, reason = sbr.refuse_ocid_registry_completion_if_evidence_incomplete(
        "OCID-901", "completed", {"note": "legacy free text, not schema-complete"}
    )
    assert verdict is False
    assert "REFUSED" in reason
    assert "evidence_summary" in reason
    print("PASS: test_refuse_gate_refuses_completion_status_with_incomplete_evidence")


def test_refuse_gate_allows_completion_status_with_complete_evidence():
    with tempfile.TemporaryDirectory() as d:
        sbr = _seed_scratch_db(os.path.join(d, "scratch.sqlite"))

    ev = _full_evidence(sbr, ocid_number="OCID-902", umr_id="UMR-x", pr_number=5, pr_repo="veridian-scripts")
    verdict, reason = sbr.refuse_ocid_registry_completion_if_evidence_incomplete("OCID-902", "completed", ev)
    assert verdict is True
    assert "satisfied" in reason
    print("PASS: test_refuse_gate_allows_completion_status_with_complete_evidence")


def test_upsert_raises_and_writes_nothing_for_incomplete_completed_row():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        raised = False
        try:
            sbr.upsert_ocid_canonical_registry(
                conn, "OCID-910", canonical_umr_id="UMR-x", status="completed",
                all_umr_ids=["UMR-x"], evidence={"note": "legacy free text only"},
            )
        except sbr.OcidEvidenceSchemaRefused as exc:
            raised = True
            assert "OCID-910" in str(exc)
        assert raised, "upsert_ocid_canonical_registry must raise OcidEvidenceSchemaRefused"

        rows = conn.execute("SELECT * FROM ocid_canonical_registry WHERE ocid_number=?", ("OCID-910",)).fetchall()
        assert rows == [], "no row must be written when the evidence schema gate refuses"

        audit_rows = conn.execute(
            "SELECT * FROM ocid_master_standard_audit_log WHERE event_type='evidence_schema_refused'"
        ).fetchall()
        assert len(audit_rows) == 1, "a real, permanent audit event must be recorded for the refusal"
        conn.close()
        print("PASS: test_upsert_raises_and_writes_nothing_for_incomplete_completed_row")


def test_upsert_allows_incomplete_evidence_for_non_completion_status():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        sbr.upsert_ocid_canonical_registry(
            conn, "OCID-911", canonical_umr_id="UMR-y", status="open",
            all_umr_ids=["UMR-y"], evidence={"note": "still in flight, legacy shape is fine"},
        )
        conn.commit()
        rows = sbr.query_ocid_canonical_registry(conn, ocid_number="OCID-911")
        assert len(rows) == 1
        conn.close()
        print("PASS: test_upsert_allows_incomplete_evidence_for_non_completion_status")


def test_upsert_allows_completion_status_with_schema_complete_evidence():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(path)
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row

        ev = _full_evidence(
            sbr, ocid_number="OCID-912", umr_id="UMR-z", pr_number=42, pr_repo="compliance-tracker",
            commit_sha="deadbeef", file_name="OCID_912.md", file_path="ai-os/OCID_912.md",
            merge_status="merged",
        )
        sbr.upsert_ocid_canonical_registry(
            conn, "OCID-912", canonical_umr_id="UMR-z", status="completed",
            all_umr_ids=["UMR-z"], evidence=ev, pr_number=42, pr_repo="compliance-tracker",
        )
        conn.commit()
        rows = sbr.query_ocid_canonical_registry(conn, ocid_number="OCID-912")
        assert len(rows) == 1
        assert rows[0]["evidence"]["commit_sha"] == "deadbeef"
        conn.close()
        print("PASS: test_upsert_allows_completion_status_with_schema_complete_evidence")
