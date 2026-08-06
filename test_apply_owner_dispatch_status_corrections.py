#!/usr/bin/env python3
"""Real tests for apply_owner_dispatch_status_corrections.py
(UMR-20260806-142206). Every test uses a real, isolated, temp-file SQLite
database seeded with the real umr_tasks schema -- never the live
production database."""
import importlib.util
import json
import os
import sqlite3
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SUT_PATH = os.path.join(SCRIPTS_DIR, "apply_owner_dispatch_status_corrections.py")
SBR_PATH = os.path.join(SCRIPTS_DIR, "superboss-register.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_and_load_sut(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    bootstrap = _load("sbr_bootstrap_apply", SBR_PATH)
    bootstrap._ensure_umr_table(conn)
    conn.commit()
    conn.close()
    os.environ["SUPERBOSS_REGISTER_DB"] = db_path
    try:
        return _load("sut_scratch_apply", SUT_PATH)
    finally:
        del os.environ["SUPERBOSS_REGISTER_DB"]


def _insert_row(conn, **overrides):
    row = {
        "umr_id": "UMR-20260805-000000-aaaa",
        "task_identity": "owner-task-test",
        "ts_submitted": "2026-08-05T12:00:00+00:00",
        "tier": 1,
        "status": "failed",
        "source_trigger": "owner_dispatch_gateway",
        "unit_name": "veridian-worker@task-20260805-120000-example.service",
        "reason": "one-time backfill reconciliation: no task.yaml found",
        "metadata_json": "{}",
    }
    row.update(overrides)
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
        "source_trigger, unit_name, reason, metadata_json) VALUES "
        "(:umr_id, :task_identity, :ts_submitted, :tier, :status, :source_trigger, "
        ":unit_name, :reason, :metadata_json)",
        row,
    )


def _scratch_conn():
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tmp.close()
    sut = _seed_and_load_sut(tmp.name)
    conn = sut._sbr._connect()
    return sut, conn, tmp.name


def test_finds_pending_top_level_corrected_status():
    sut, conn, path = _scratch_conn()
    try:
        _insert_row(
            conn, umr_id="UMR-20260805-084033-d904", status="failed",
            metadata_json=json.dumps({
                "reconciliation_UMR-20260806-081403-ebd3_evidence": "real pushed merge commit cc5dea73, blocked on human review",
                "corrected_status": "running",
            }),
        )
        conn.commit()
        pending = sut.find_pending_corrections(conn)
        assert len(pending) == 1, pending
        assert pending[0]["corrected_status"] == "running"
        assert "cc5dea73" in pending[0]["evidence"]
    finally:
        conn.close()
        os.unlink(path)


def test_already_converged_row_is_skipped():
    sut, conn, path = _scratch_conn()
    try:
        _insert_row(
            conn, umr_id="UMR-20260806-064237-8817", status="rejected_duplicate",
            metadata_json=json.dumps({
                "reconciliation_UMR-20260806-081403-ebd3_evidence": "already reconciled",
                "corrected_status": "rejected_duplicate",
            }),
        )
        conn.commit()
        pending = sut.find_pending_corrections(conn)
        assert pending == [], pending
    finally:
        conn.close()
        os.unlink(path)


def test_apply_writes_status_via_update_umr_task_only():
    sut, conn, path = _scratch_conn()
    try:
        _insert_row(
            conn, umr_id="UMR-20260805-084223-3ad7", status="failed",
            metadata_json=json.dumps({
                "reconciliation_UMR-20260806-081403-ebd3_evidence": "blocked by no-second-reviewer gate",
                "corrected_status": "running",
                "unrelated_pre_existing_key": "must-survive",
            }),
        )
        conn.commit()

        pending = sut.find_pending_corrections(conn)
        assert len(pending) == 1

        sut.apply_one(conn, pending[0])
        conn.commit()

        row = dict(conn.execute("SELECT status, reason, metadata_json FROM umr_tasks WHERE umr_id=?",
                                 (pending[0]["umr_id"],)).fetchone())
        assert row["status"] == "running"
        assert "no-second-reviewer" in row["reason"]
        meta = json.loads(row["metadata_json"])
        assert meta["unrelated_pre_existing_key"] == "must-survive"
        assert meta["reconciliation_UMR-20260806-081403-ebd3_evidence"] == "blocked by no-second-reviewer gate"
        assert f"applied_{sut.THIS_SCRIPT}" in meta
    finally:
        conn.close()
        os.unlink(path)


def test_second_run_after_apply_is_idempotent_noop():
    sut, conn, path = _scratch_conn()
    try:
        _insert_row(
            conn, umr_id="UMR-20260805-130213-d627", status="failed",
            metadata_json=json.dumps({
                "reconciliation_UMR-20260806-081403-ebd3_evidence": "PR #965 approved, tier2 needs Owner sign-off",
                "corrected_status": "running",
            }),
        )
        conn.commit()
        pending = sut.find_pending_corrections(conn)
        sut.apply_one(conn, pending[0])
        conn.commit()

        pending_again = sut.find_pending_corrections(conn)
        assert pending_again == [], pending_again
    finally:
        conn.close()
        os.unlink(path)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS: {name}")
