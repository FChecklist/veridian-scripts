#!/usr/bin/env python3
"""UMR-20260806-171945-5767, second amendment
(task-20260807-053232-second-amendment-to-umr-20260806-171945): real tests
for derive_umr_output_contract() and its wiring into cmd_mark_umr_terminal
(superboss-register.py), the platform's one real single-exit-point for
umr_tasks terminal-completion output.

Layer 1: derive_umr_output_contract() unit tests -- pure function, no I/O.
Layer 2: real CLI, real isolated scratch SQLite DB (never the live
          production one) -- proves the contract actually lands in
          outputs_json, additively, alongside the pre-existing real
          evidence fields (pr_number/commit_sha/file_path/repo) that
          test_mark_umr_terminal_structured_evidence.py and
          umr_completion_percentage.py already depend on.
Layer 3: real restart-survival proof -- writes via one subprocess, then
          re-queries the same on-disk scratch DB from a SECOND, independent
          subprocess (no shared Python state) to prove the contract is real
          persisted DB state, never in-process-only memory.
Layer 4: real timing measurement over N real CLI invocations.
"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "sbr_output_contract_test", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


sbr = _load_module()


# --- Layer 1: derive_umr_output_contract(), pure function -----------------

def test_bare_status_flip_is_honestly_non_deterministic():
    """No evidence dict, no reason -> deterministic must be False, never
    hardcoded True."""
    contract = sbr.derive_umr_output_contract("UMR-TEST-oc-1", "failed", None, {})
    assert contract["meta"]["deterministic"] is False
    assert contract["meta"]["work_id"] == "UMR-TEST-oc-1"
    assert contract["meta"]["boolean"] is True
    assert "UMR-TEST-oc-1" in contract["data"]


def test_real_reason_alone_makes_it_deterministic():
    contract = sbr.derive_umr_output_contract("UMR-TEST-oc-2", "killed", "real specific reason", {})
    assert contract["meta"]["deterministic"] is True


def test_real_evidence_alone_makes_it_deterministic():
    contract = sbr.derive_umr_output_contract(
        "UMR-TEST-oc-3", "completed", None, {"file_path": "/real/path.py"})
    assert contract["meta"]["deterministic"] is True


def test_completed_unmerged_is_honestly_not_close_ended():
    contract = sbr.derive_umr_output_contract(
        "UMR-TEST-oc-4", "completed_unmerged", "real", {"commit_sha": "abc123"})
    assert contract["meta"]["close_ended"] is False


@pytest.mark.parametrize("status", ["completed", "failed", "killed"])
def test_terminal_non_unmerged_statuses_are_close_ended(status):
    contract = sbr.derive_umr_output_contract("UMR-TEST-oc-5", status, "real", {})
    assert contract["meta"]["close_ended"] is True


def test_work_id_is_the_real_umr_id_never_a_fresh_uuid():
    contract = sbr.derive_umr_output_contract("UMR-REAL-EXISTING-ID", "completed", "r", {"pr_number": 42})
    assert contract["meta"]["work_id"] == "UMR-REAL-EXISTING-ID"


def test_data_is_a_plain_field_interpolated_string_not_ai_narration():
    contract = sbr.derive_umr_output_contract(
        "UMR-TEST-oc-6", "completed", "shipped", {"pr_number": 7})
    assert isinstance(contract["data"], str)
    assert "UMR-TEST-oc-6" in contract["data"]
    assert "completed" in contract["data"]
    assert "shipped" in contract["data"]
    # Never contains stock AI-narrator phrasing.
    lowered = contract["data"].lower()
    for banned in ("i have", "as an ai", "certainly", "i'll", "let's"):
        assert banned not in lowered


# --- Layer 2: real CLI, real isolated scratch DB ---------------------------

def _insert_queued_row(path, umr_id):
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
        "source_trigger, task_kind, inputs_json, outputs_json, metadata_json) "
        "VALUES (?,?,datetime('now'),2,'queued','owner_dispatch_gateway','veridian_task_create',"
        "'{}','{}','{}')",
        (umr_id, umr_id + "-identity"),
    )
    conn.commit()
    conn.close()


def _row(path, umr_id):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


@pytest.fixture()
def scratch_db():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr2 = _load_module()
        sbr2.DB_PATH = path
        sbr2.init_db()
        yield path


def _run_sbr(args, scratch_db):
    env = dict(os.environ)
    env["SUPERBOSS_REGISTER_DB"] = scratch_db
    return subprocess.run(
        [sys.executable, "superboss-register.py"] + args,
        cwd=SCRIPTS_DIR, env=env, capture_output=True, text=True,
    )


def test_cli_write_carries_output_contract_additively_alongside_real_evidence(scratch_db, tmp_path):
    umr_id = "UMR-TEST-oc-cli-1"
    _insert_queued_row(scratch_db, umr_id)
    real_file = tmp_path / "real_artifact.py"
    real_file.write_text("# real\n")

    out = _run_sbr(["mark-umr-terminal", "--umr-id", umr_id, "--status", "completed",
                     "--file-path", str(real_file), "--reason", "real work landed"], scratch_db)
    assert out.returncode == 0, out.stderr

    outputs = json.loads(_row(scratch_db, umr_id)["outputs_json"])
    # Pre-existing flat evidence keys untouched (backward compatible).
    assert outputs["file_path"] == str(real_file)
    # New additive contract key.
    contract = outputs["output_contract"]
    assert contract["meta"]["work_id"] == umr_id
    assert contract["meta"]["deterministic"] is True
    assert contract["meta"]["close_ended"] is True
    assert contract["meta"]["boolean"] is True


def test_cli_write_with_zero_evidence_records_honest_false_deterministic(scratch_db):
    """failed/killed are never evidence-gated -- a bare mark with no
    --reason and no evidence must now honestly record deterministic=False,
    not silently omit the contract or claim it True."""
    umr_id = "UMR-TEST-oc-cli-2"
    _insert_queued_row(scratch_db, umr_id)

    out = _run_sbr(["mark-umr-terminal", "--umr-id", umr_id, "--status", "failed"], scratch_db)
    assert out.returncode == 0, out.stderr

    outputs = json.loads(_row(scratch_db, umr_id)["outputs_json"])
    assert outputs["output_contract"]["meta"]["deterministic"] is False
    assert outputs["output_contract"]["meta"]["work_id"] == umr_id


def test_refused_write_never_gets_a_contract_row_untouched(scratch_db):
    """The pre-existing structural-evidence gate still wins first -- a
    refused completed claim must still leave outputs_json exactly '{}',
    same as before this amendment (no contract for a write that never
    happened)."""
    umr_id = "UMR-TEST-oc-cli-3"
    _insert_queued_row(scratch_db, umr_id)
    out = _run_sbr(["mark-umr-terminal", "--umr-id", umr_id, "--status", "completed"], scratch_db)
    assert out.returncode != 0
    assert _row(scratch_db, umr_id)["outputs_json"] == "{}"


# --- Layer 3: real restart-survival proof (independent subprocess re-read) -

def test_state_survives_process_exit_reread_from_a_second_independent_process(scratch_db):
    """Writer subprocess exits completely; a SECOND, independent subprocess
    (no shared Python object, no in-memory cache) re-opens the same on-disk
    scratch DB and must see the exact same contract -- real persisted state,
    never in-process-only."""
    umr_id = "UMR-TEST-oc-restart-survival"
    _insert_queued_row(scratch_db, umr_id)

    # failed/killed are never evidence-gated (no real commit/file needed),
    # so this exercises the write path without fabricating a fake commit sha.
    writer = _run_sbr(["mark-umr-terminal", "--umr-id", umr_id, "--status", "failed",
                        "--reason", "real, independently re-read after exit"], scratch_db)
    assert writer.returncode == 0, writer.stderr
    # Writer process has now fully exited (subprocess.run already joined it).

    reread = subprocess.run(
        [sys.executable, "-c",
         "import sqlite3,json,sys; "
         "c=sqlite3.connect(sys.argv[1]); "
         "row=c.execute(\"SELECT outputs_json FROM umr_tasks WHERE umr_id=?\", (sys.argv[2],)).fetchone(); "
         "print(row[0])",
         scratch_db, umr_id],
        capture_output=True, text=True, check=True,
    )
    outputs = json.loads(reread.stdout)
    assert outputs["output_contract"]["meta"]["deterministic"] is True
    assert outputs["output_contract"]["meta"]["close_ended"] is True
    assert outputs["output_contract"]["meta"]["work_id"] == umr_id


# --- Layer 4: real timing evidence -----------------------------------------

def test_real_timing_ten_terminal_writes_stay_fast(scratch_db):
    """Real wall-clock proof this stays fast: 10 real end-to-end CLI
    invocations (full Python subprocess startup + sqlite write +
    output-contract derivation each) must complete, in aggregate, well
    under a generous 20s ceiling -- deterministic dict/string logic, no
    network/LLM call in the path."""
    start = time.monotonic()
    for i in range(10):
        umr_id = f"UMR-TEST-oc-timing-{i}"
        _insert_queued_row(scratch_db, umr_id)
        out = _run_sbr(["mark-umr-terminal", "--umr-id", umr_id, "--status", "failed",
                         "--reason", f"real timing run {i}"], scratch_db)
        assert out.returncode == 0, out.stderr
    elapsed = time.monotonic() - start
    per_call_ms = (elapsed / 10) * 1000
    print(f"\nreal timing: 10 mark-umr-terminal CLI invocations in {elapsed:.3f}s "
          f"({per_call_ms:.1f}ms/call, full subprocess+sqlite+contract each)")
    assert elapsed < 20.0
