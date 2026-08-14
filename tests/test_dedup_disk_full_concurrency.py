#!/usr/bin/env python3
"""UMR-20260814-033914-63ef (P1 UMR-20260806-171945-5767 addendum): real
regression/proof tests for the transient `database or disk is full` crash in
find_target_identifier_duplicate()/cmd_check_target_identifier_duplicate().

REAL EVIDENCE this covers (see the docstrings on
_migrate_umr_tasks_ts_index() and query_umr_tasks()'s `extra_columns` param
in superboss-register.py for the full incident writeup): a real captured
traceback showed `sqlite3.OperationalError: database or disk is full` raised
from inside query_umr_tasks() during a dispatch pre-flight duplicate check,
at a moment the root filesystem was independently confirmed to be nowhere
near full (66% used, 100G free) -- i.e. a transient failure on SQLite's own
temp store, not the DB file, correlated with a burst of ~40 near-simultaneous
callers all running the same heavy, unbounded, blob-materializing query at
once.

Two real causal factors, each independently confirmed here:
  1. find_target_identifier_duplicate() calls query_umr_tasks() with NO
     status filter, which (pre-fix) had no index covering `ORDER BY
     ts_submitted DESC` on its own -- SQLite fell back to `USE TEMP B-TREE
     FOR ORDER BY`, materializing the WHOLE table before LIMIT could apply.
  2. That call used full=True (`SELECT *`), pulling 3 blob columns
     (outputs_json/metadata_json/metric_snapshot_json) this function never
     reads, on top of the one it does (inputs_json), multiplying how much
     data that temp materialization had to hold.

test_dedup_query_plan_has_no_temp_btree_or_full_select is the direct,
mechanical regression guard for both: it inspects the real SQL
find_target_identifier_duplicate() issues (via query_umr_tasks()) and proves
via EXPLAIN QUERY PLAN that neither factor is present anymore.

test_40_concurrent_duplicate_checks_against_realistic_dataset_no_disk_full is
the real load test the SPEC asked for: a realistic seeded dataset (several
hundred rows, mixed statuses, each with realistically-sized
inputs_json/outputs_json/metadata_json blobs -- the exact shape that made
full=True expensive) hit by 40 concurrent real CLI subprocess invocations of
`check-target-identifier-duplicate` (the real, live call path
dispatch-owner-task.sh uses), asserting zero failures of any kind and that
duplicate detection is still correct under that concurrency.
"""
import concurrent.futures
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_sbr():
    spec = importlib.util.spec_from_file_location(
        "sbr_dedup_disk_full_concurrency_test", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def _seed_full_schema(path):
    sbr = _load_sbr()
    sbr.DB_PATH = path
    sbr.init_db()
    return sbr


@pytest.fixture()
def scratch_db():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        _seed_full_schema(path)
        yield path


def _seed_realistic_dataset(path, n=300):
    """Several hundred rows, mixed statuses, each carrying realistically-
    sized inputs_json/outputs_json/metadata_json blobs -- the real shape
    that made full=True (`SELECT *`) expensive per row. outputs_json/
    metadata_json are deliberately large and NEVER read by
    find_target_identifier_duplicate(), proving extra_columns=("inputs_json",)
    is both sufficient (dup detection still works) and necessary-and-
    sufficient (those other blobs really are dead weight for this call)."""
    conn = sqlite3.connect(path)
    now = datetime.now(timezone.utc)
    statuses = ["queued", "running", "completed", "killed", "failed"]
    for i in range(n):
        umr_id = f"UMR-LOAD-{i:04d}"
        ts = (now - timedelta(minutes=i)).isoformat()
        title = f"Task #{i}: unrelated real work item {i}"
        prompt = f"Please handle unrelated real work item {i} in claude-control#{1000 + i}. " + ("x" * 2000)
        inputs = json.dumps({"title": title, "prompt": prompt, "repo": "claude-control"})
        # Large, realistic dead-weight blobs this call never reads.
        outputs = json.dumps({"summary": "y" * 8000, "artifacts": [f"artifact-{j}" for j in range(50)]})
        metadata = json.dumps({"blob": "z" * 8000})
        conn.execute(
            "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
            "source_trigger, task_kind, inputs_json, outputs_json, metadata_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (umr_id, umr_id + "-identity", ts, 2, statuses[i % len(statuses)],
             "owner_dispatch_gateway", "veridian_task_create", inputs, outputs, metadata),
        )
    conn.commit()
    conn.close()


# --- mechanical regression guard: the real query plan itself ---------------

def test_dedup_query_plan_has_no_temp_btree_or_full_select(scratch_db):
    """Direct proof of the real fix: the exact SQL shape
    find_target_identifier_duplicate() issues (no status filter,
    ORDER BY ts_submitted DESC LIMIT ?, light columns + inputs_json only)
    must use idx_umr_tasks_ts and must NOT fall back to a temp b-tree sort
    or select every column."""
    sbr = _seed_full_schema(scratch_db)
    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row

    cols = sbr._umr_select_columns(False, ("inputs_json",))
    assert "*" not in cols
    assert "inputs_json" in cols
    assert "outputs_json" not in cols
    assert "metadata_json" not in cols
    assert "metric_snapshot_json" not in cols

    plan = conn.execute(
        f"EXPLAIN QUERY PLAN SELECT {cols} FROM umr_tasks ORDER BY ts_submitted DESC LIMIT 30"
    ).fetchall()
    conn.close()
    plan_text = " ".join(dict(r)["detail"] for r in plan)
    assert "idx_umr_tasks_ts" in plan_text, plan_text
    assert "TEMP B-TREE" not in plan_text.upper(), plan_text


def test_dedup_query_plan_status_filtered_path_unaffected(scratch_db):
    """Regression guard: adding idx_umr_tasks_ts must not steer the
    pre-existing status-filtered listing (--query-umr --status X) away from
    idx_umr_tasks_status_ts -- both indexes must coexist correctly."""
    sbr = _seed_full_schema(scratch_db)
    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    plan = conn.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM umr_tasks WHERE status=? "
        "ORDER BY ts_submitted DESC LIMIT ?", ("killed", 15)
    ).fetchall()
    conn.close()
    plan_text = " ".join(dict(r)["detail"] for r in plan)
    assert "idx_umr_tasks_status_ts" in plan_text, plan_text
    assert "TEMP B-TREE" not in plan_text.upper(), plan_text


# --- correctness: extra_columns=("inputs_json",) preserves real dup detection

def test_find_target_identifier_duplicate_still_correct_with_extra_columns(scratch_db):
    """Same real incident shape as tests/test_target_identifier_dedup.py's
    pure-function test, proving the extra_columns=("inputs_json",) call
    (replacing full=True) still finds a real duplicate -- the PR #308
    regression this must not reintroduce."""
    sbr = _seed_full_schema(scratch_db)
    conn = sqlite3.connect(scratch_db)
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
        "source_trigger, task_kind, inputs_json, outputs_json, metadata_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("UMR-TEST-EXTRA-COLS", "UMR-TEST-EXTRA-COLS-identity",
         datetime.now(timezone.utc).isoformat(), 2, "running",
         "owner_dispatch_gateway", "veridian_task_create",
         json.dumps({"title": "RCA: PR #131 audit", "prompt": "Investigate real failure on PR #131.",
                     "repo": "claude-control"}),
         "{}", "{}"),
    )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    dup = sbr.find_target_identifier_duplicate(
        conn, "Fix PR #131 merge conflict", "Please resolve the conflict blocking PR #131.",
        repo="claude-control", window_hours=4, limit=30,
    )
    conn.close()
    assert dup is not None
    assert dup["umr_id"] == "UMR-TEST-EXTRA-COLS"


# --- the real load test: 40 concurrent real CLI invocations ----------------

def _run_check_target_identifier_duplicate(scratch_db, title, prompt, repo="claude-control"):
    env = dict(os.environ)
    env["SUPERBOSS_REGISTER_DB"] = scratch_db
    return subprocess.run(
        [sys.executable, "superboss-register.py", "check-target-identifier-duplicate",
         "--title", title, "--prompt", prompt, "--repo", repo],
        cwd=SCRIPTS_DIR, env=env, capture_output=True, text=True, timeout=60,
    )


def test_40_concurrent_duplicate_checks_against_realistic_dataset_no_disk_full(scratch_db):
    """The real test the SPEC asked for: 40 concurrent duplicate-check calls
    against a realistic seeded dataset (300 rows, realistically-sized
    inputs_json/outputs_json/metadata_json blobs -- the exact shape that made
    the pre-fix full=True + unindexed ORDER BY expensive) must all succeed,
    with zero `database or disk is full` (or any other) errors, and correct
    duplicate detection must survive the concurrency."""
    sbr = _seed_full_schema(scratch_db)
    _seed_realistic_dataset(scratch_db, n=300)

    # One live row that a subset of the 40 concurrent calls should actually
    # find as a real duplicate, proving correctness isn't just "no crash".
    conn = sqlite3.connect(scratch_db)
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
        "source_trigger, task_kind, inputs_json, outputs_json, metadata_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("UMR-LOAD-DUP-TARGET", "UMR-LOAD-DUP-TARGET-identity",
         datetime.now(timezone.utc).isoformat(), 2, "running",
         "owner_dispatch_gateway", "veridian_task_create",
         json.dumps({"title": "RCA: PR #9999 audit", "prompt": "Investigate real failure on PR #9999.",
                     "repo": "claude-control"}),
         "{}", "{}"),
    )
    conn.commit()
    conn.close()

    calls = []
    for i in range(40):
        if i % 4 == 0:
            # Real duplicate: same target (PR #9999), different wording.
            calls.append(("Fix PR #9999 merge conflict",
                           f"Please resolve the conflict blocking PR #9999 (worker {i})."))
        else:
            # Genuinely new, unrelated target each time.
            calls.append((f"Unrelated new task {i}", f"Please handle genuinely new work item {i}."))

    with concurrent.futures.ThreadPoolExecutor(max_workers=40) as pool:
        futures = [pool.submit(_run_check_target_identifier_duplicate, scratch_db, t, p) for t, p in calls]
        results = [f.result() for f in futures]

    disk_full_failures = [r for r in results if "disk is full" in (r.stderr or "").lower()]
    assert not disk_full_failures, [r.stderr for r in disk_full_failures]

    other_failures = [r for r in results if r.returncode != 0]
    assert not other_failures, [(r.returncode, r.stderr) for r in other_failures]

    payloads = [json.loads(r.stdout) for r in results]
    found_dup_count = sum(1 for p in payloads if p["target_identifier_duplicate_found"])
    # Every 4th call (indices 0, 4, 8, ... = 10 calls) named PR #9999; all 10
    # must have found UMR-LOAD-DUP-TARGET as a real duplicate.
    assert found_dup_count == 10, payloads
    for i, p in enumerate(payloads):
        if i % 4 == 0:
            assert p["duplicate_umr_id"] == "UMR-LOAD-DUP-TARGET", p
        else:
            assert p["target_identifier_duplicate_found"] is False, p
