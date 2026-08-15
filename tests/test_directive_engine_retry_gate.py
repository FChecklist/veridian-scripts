#!/usr/bin/env python3
"""Real regression test for directive_engine.py's process_one() retry-once
gate (dispatch-queue-starvation investigation, UMR-20260806-090229-f2a7,
parent UMR-20260806-071025-1d28).

Round 1 real bug (closed here): process_one() used to gate its "retry exactly
once, then hold for Owner review" policy on entry.get("_retried") -- an
in-memory flag on a dict that main()'s own `directive = load_directive()`
recreates fresh from DIRECTIVE.yaml every single outer-loop tick. That flag
never survived to the next tick, so a chronically-failing task_identity was
resubmitted forever instead of once -- confirmed live via task_identity
PHASE-3-BUILD-CALC (DIRECTIVE.yaml), resubmitted 20+ times since 2026-07-29.

Round 2 real bug (closed here, found by real independent Superboss review of
round 1's own fix): round 1 replaced the in-memory flag with a check against
umr_tasks.reason (set by resource_governor.py's submit() to "resubmitted
(reused umr_id, prior status was ...)" on the one real retry). Real review
found that fragile: resource_governor.py's dispatch_one() legitimately
overwrites that SAME `reason` column via update_umr_task(..., reason=reason)
on its own rejected_duplicate paths (the OCID-superseded-evidence check and
the duplicate-PR guard, both scoped to task_kind=='veridian_task_create' --
exactly what this module submits) -- silently erasing the round-1 marker and
reopening the same retry storm whenever a retried task's own retry lands in
one of those two branches. test_retry_marker_survives_reason_being_overwritten_by_dispatch_one
below reproduces exactly that scenario against the real fix.

The real, durable fix is a small local JSON state file
(DIRECTIVE_RETRY_STATE_FILE) written EXCLUSIVELY by directive_engine.py --
nothing in resource_governor.py or any other real caller ever touches it, so
it cannot be silently clobbered by another module's own legitimate row
mutations, unlike a shared umr_tasks column.

These tests use a real, isolated, temp-file SQLite database (never the live
production database) and a real, isolated temp file for
PENDING_OWNER_REVIEW.md/DIRECTIVE_RETRY_STATE.json, via directive_engine.py's
env-var overrides.
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location("sbr_seed_de", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    conn.close()
    return sbr


def _load_directive_engine(env):
    old_env = {}
    for k, v in env.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location(
            f"directive_engine_test_{id(env)}", os.path.join(SCRIPTS_DIR, "directive_engine.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _make_env(scratch_db, review_file, log_file, retry_state_file):
    return {
        "VERIDIAN_DIRECTIVE_SUPERBOSS_DB": scratch_db,
        "VERIDIAN_DIRECTIVE_PENDING_REVIEW_FILE": review_file,
        "VERIDIAN_DIRECTIVE_LOG_PATH": log_file,
        "VERIDIAN_DIRECTIVE_RETRY_STATE_FILE": retry_state_file,
        "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR,
    }


def _paths(d):
    return (
        os.path.join(d, "scratch.sqlite"),
        os.path.join(d, "PENDING_OWNER_REVIEW.md"),
        os.path.join(d, "directive_status.log"),
        os.path.join(d, "DIRECTIVE_RETRY_STATE.json"),
    )


def test_first_terminal_failure_is_allowed_to_retry_and_marks_state():
    """A task_identity with no prior retry-state entry must fall through to a
    real resubmission attempt, and that attempt must durably record itself in
    the retry-state file (not an in-memory flag)."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db, review_file, log_file, retry_state_file = _paths(d)
        sbr = _seed_scratch_db(scratch_db)
        de = _load_directive_engine(_make_env(scratch_db, review_file, log_file, retry_state_file))

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": "test-first-failure-task", "tier": 2, "status": "failed",
            "source_trigger": "DIRECTIVE", "task_kind": "veridian_task_create",
            "inputs": {"repo": "x", "title": "t", "prompt": "p"}, "reason": "queued",
        })
        conn.commit()
        conn.close()

        calls = []
        # Stubbed the same real way submit_task is below: this test exercises the
        # retry-once gate, not run_check_duplicate_battery()'s own real subprocess
        # call (which fail-closed-skips the submission on any failure -- see
        # test_directive_engine_fail_closed_duplicate_check.py for that behavior).
        de.run_check_duplicate_battery = lambda *a, **k: {"duplicate_found": False}
        de.submit_task = lambda *a, **k: (calls.append((a, k)) or {"accepted": True, "umr_id": umr_id})

        result = de.process_one({"task_identity": "test-first-failure-task", "tier": 2,
                                  "title": "t", "prompt": "p", "repo": "x"})
        assert result == "queued", result
        assert len(calls) == 1, "first real terminal failure must be retried exactly once"
        assert not os.path.exists(review_file) or "test-first-failure-task" not in open(review_file).read()

        assert os.path.exists(retry_state_file)
        state = json.load(open(retry_state_file))
        assert "test-first-failure-task" in state
        assert state["test-first-failure-task"]["umr_id"] == umr_id


def test_second_terminal_failure_after_resubmission_is_held_not_reretried():
    """A task_identity already marked retried in the state file, whose umr_tasks
    row is terminal again, must be held for Owner review, never resubmitted a
    second time -- durable across a fresh process/module load."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db, review_file, log_file, retry_state_file = _paths(d)
        sbr = _seed_scratch_db(scratch_db)
        de = _load_directive_engine(_make_env(scratch_db, review_file, log_file, retry_state_file))

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": "test-exhausted-retry-task", "tier": 1, "status": "killed",
            "source_trigger": "DIRECTIVE", "task_kind": "veridian_task_create",
            "inputs": {"repo": "x", "title": "t", "prompt": "p"},
            "reason": "resubmitted (reused umr_id, prior status was 'failed')",
        })
        conn.commit()
        conn.close()
        de._mark_retried("test-exhausted-retry-task", umr_id)

        calls = []
        de.submit_task = lambda *a, **k: (calls.append((a, k)) or {"accepted": True, "umr_id": umr_id})

        result = de.process_one({"task_identity": "test-exhausted-retry-task", "tier": 1,
                                  "title": "t", "prompt": "p", "repo": "x"})
        assert result == "killed", result
        assert len(calls) == 0, "an already-retried, still-terminal task_identity must never be resubmitted again"
        assert os.path.exists(review_file)
        content = open(review_file).read()
        assert "test-exhausted-retry-task" in content


def test_retry_marker_survives_reason_being_overwritten_by_dispatch_one():
    """Real regression for the exact gap found by independent Superboss review
    of round 1: resource_governor.py's dispatch_one() can legitimately
    overwrite umr_tasks.reason to something that does NOT start with
    "resubmitted (reused umr_id" (its own rejected_duplicate paths -- OCID-
    superseded-evidence / duplicate-PR guard -- both scoped to
    task_kind=='veridian_task_create', exactly what this module submits).
    Simulates that exact overwrite (a real, plausible dispatch_one() outcome
    for a retried task whose retry itself lands in one of those two
    branches) and confirms the retry-once gate still correctly holds --
    because the real signal now lives in a file dispatch_one() never
    touches, not in the contended `reason` column."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db, review_file, log_file, retry_state_file = _paths(d)
        sbr = _seed_scratch_db(scratch_db)
        de = _load_directive_engine(_make_env(scratch_db, review_file, log_file, retry_state_file))

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": "test-reason-overwritten-task", "tier": 1, "status": "queued",
            "source_trigger": "DIRECTIVE", "task_kind": "veridian_task_create",
            "inputs": {"repo": "x", "title": "t", "prompt": "p"},
            "reason": "resubmitted (reused umr_id, prior status was 'failed')",
        })
        conn.commit()
        conn.close()
        # Round 1's own retry-once mark: the resubmission that put this row
        # back to 'queued' with the "resubmitted..." reason above.
        de._mark_retried("test-reason-overwritten-task", umr_id)

        # Simulate resource_governor.py's dispatch_one() real duplicate-PR-guard
        # / OCID-superseded-evidence branch: status -> rejected_duplicate, and
        # `reason` overwritten to a completely different real message (exactly
        # what update_umr_task(..., status="rejected_duplicate", reason=reason)
        # does at resource_governor.py ~lines 1330/1371) -- the "resubmitted..."
        # marker is now gone from the DB row entirely.
        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        sbr.update_umr_task(
            conn, umr_id, status="rejected_duplicate",
            reason="duplicate-PR guard (Stage 4/5/6): existing PR real-org/real-repo#42 "
                   "already open/merged for task_identity='test-reason-overwritten-task'",
        )
        conn.commit()
        conn.close()

        calls = []
        de.submit_task = lambda *a, **k: (calls.append((a, k)) or {"accepted": True, "umr_id": umr_id})

        result = de.process_one({"task_identity": "test-reason-overwritten-task", "tier": 1,
                                  "title": "t", "prompt": "p", "repo": "x"})
        assert result == "rejected_duplicate", result
        assert len(calls) == 0, (
            "the retry-once gate must hold even though dispatch_one() overwrote "
            "`reason` to a value that no longer carries the round-1 marker -- "
            "the real signal now lives in the exclusively-owned state file"
        )
        assert os.path.exists(review_file)
        assert "test-reason-overwritten-task" in open(review_file).read()


def test_exhausted_retry_reprocessed_across_a_fresh_module_reload_still_holds():
    """The real round-1 regression, re-verified against the round-2 fix:
    simulate main()'s own behavior of reloading directive_engine's
    module-level state fresh every outer-loop tick (here, a brand-new module
    import, the strongest possible proof no in-memory state survives) and
    re-run process_one() a second time on the SAME already-exhausted
    task_identity. Must still be held, not resubmitted."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db, review_file, log_file, retry_state_file = _paths(d)
        sbr = _seed_scratch_db(scratch_db)

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": "test-reload-exhausted-task", "tier": 1, "status": "failed",
            "source_trigger": "DIRECTIVE", "task_kind": "veridian_task_create",
            "inputs": {"repo": "x", "title": "t", "prompt": "p"},
            "reason": "resubmitted (reused umr_id, prior status was 'killed')",
        })
        conn.commit()
        conn.close()

        env = _make_env(scratch_db, review_file, log_file, retry_state_file)
        de0 = _load_directive_engine(env)
        de0._mark_retried("test-reload-exhausted-task", umr_id)

        total_calls = []
        for _ in range(3):  # 3 separate fresh module loads = 3 separate "ticks"
            de = _load_directive_engine(env)
            de.submit_task = lambda *a, **k: (total_calls.append((a, k)) or {"accepted": True, "umr_id": umr_id})
            result = de.process_one({"task_identity": "test-reload-exhausted-task", "tier": 1,
                                      "title": "t", "prompt": "p", "repo": "x"})
            assert result == "failed", result

        assert len(total_calls) == 0, (
            "an exhausted-retry task_identity must never be resubmitted again, "
            "no matter how many fresh ticks/reloads re-encounter it"
        )
        # Idempotency: only ONE real review line, not one per tick.
        content = open(review_file).read()
        assert content.count("test-reload-exhausted-task") == 1, (
            f"note_needs_review() must not spam a fresh line every tick, got: {content!r}"
        )


def test_note_needs_review_is_idempotent():
    with tempfile.TemporaryDirectory() as d:
        scratch_db, review_file, log_file, retry_state_file = _paths(d)
        _seed_scratch_db(scratch_db)
        de = _load_directive_engine(_make_env(scratch_db, review_file, log_file, retry_state_file))

        de.note_needs_review("test-idempotent-task", "first real reason")
        de.note_needs_review("test-idempotent-task", "a different real reason, same task")
        content = open(review_file).read()
        assert content.count("- test-idempotent-task:") == 1, content


def test_retry_state_file_is_corruption_tolerant():
    """A corrupt/unreadable retry-state file must fail open (treated as no
    prior retry), never crash a real tick."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db, review_file, log_file, retry_state_file = _paths(d)
        sbr = _seed_scratch_db(scratch_db)
        with open(retry_state_file, "w") as f:
            f.write("{not valid json")
        de = _load_directive_engine(_make_env(scratch_db, review_file, log_file, retry_state_file))

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": "test-corrupt-state-task", "tier": 2, "status": "failed",
            "source_trigger": "DIRECTIVE", "task_kind": "veridian_task_create",
            "inputs": {"repo": "x", "title": "t", "prompt": "p"}, "reason": "queued",
        })
        conn.commit()
        conn.close()

        calls = []
        de.run_check_duplicate_battery = lambda *a, **k: {"duplicate_found": False}
        de.submit_task = lambda *a, **k: (calls.append((a, k)) or {"accepted": True, "umr_id": umr_id})
        result = de.process_one({"task_identity": "test-corrupt-state-task", "tier": 2,
                                  "title": "t", "prompt": "p", "repo": "x"})
        assert result == "queued", result
        assert len(calls) == 1


def test_completed_and_active_statuses_unaffected():
    """Sanity: the retry-gate change must not touch the completed/active
    early-return branches above it."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db, review_file, log_file, retry_state_file = _paths(d)
        sbr = _seed_scratch_db(scratch_db)
        de = _load_directive_engine(_make_env(scratch_db, review_file, log_file, retry_state_file))

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        sbr.upsert_umr_task(conn, {
            "task_identity": "test-completed-task", "tier": 2, "status": "completed",
            "source_trigger": "DIRECTIVE", "task_kind": "veridian_task_create",
            "inputs": {"repo": "x", "title": "t", "prompt": "p"}, "reason": "queued",
        })
        sbr.upsert_umr_task(conn, {
            "task_identity": "test-queued-task", "tier": 2, "status": "queued",
            "source_trigger": "DIRECTIVE", "task_kind": "veridian_task_create",
            "inputs": {"repo": "x", "title": "t", "prompt": "p"}, "reason": "queued",
        })
        conn.commit()
        conn.close()

        assert de.process_one({"task_identity": "test-completed-task"}) == "completed"
        assert de.process_one({"task_identity": "test-queued-task"}) == "queued"


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR: {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
