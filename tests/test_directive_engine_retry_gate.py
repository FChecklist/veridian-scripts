#!/usr/bin/env python3
"""Real regression test for directive_engine.py's process_one() retry-once
gate (dispatch-queue-starvation investigation, UMR-20260806-090229-f2a7,
parent UMR-20260806-071025-1d28).

Real, previously-confirmed-live bug this closes: process_one() used to gate
its "retry exactly once, then hold for Owner review" policy on
entry.get("_retried") -- an in-memory flag on a dict that main()'s own
`directive = load_directive()` recreates fresh from DIRECTIVE.yaml every
single outer-loop tick. That flag never survived to the next tick, so a
chronically-failing task_identity was resubmitted forever instead of once --
confirmed live via task_identity PHASE-3-BUILD-CALC (DIRECTIVE.yaml),
resubmitted 20+ times since 2026-07-29. Combined with resource_governor.py's
umr_id-reuse-on-resubmit (Rule 1) never refreshing ts_submitted, that one row
permanently won next_queued_task()'s aging+ts_submitted tiebreak against every
other real queued row, including 30 genuinely distinct tier-1 rows that were
never even attempted for ~2 real days.

The fix reads the row's own persisted `reason` column (real, durable state in
umr_tasks) instead of the ephemeral in-memory flag. These tests use a real,
isolated, temp-file SQLite database (never the live production database) and
a real, isolated temp file for PENDING_OWNER_REVIEW.md, via directive_engine.py's
new env-var overrides.
"""
import importlib.util
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


def _make_env(scratch_db, review_file, log_file):
    return {
        "VERIDIAN_DIRECTIVE_SUPERBOSS_DB": scratch_db,
        "VERIDIAN_DIRECTIVE_PENDING_REVIEW_FILE": review_file,
        "VERIDIAN_DIRECTIVE_LOG_PATH": log_file,
        "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR,
    }


def test_first_terminal_failure_is_allowed_to_retry():
    """A task_identity whose latest umr_tasks row is a genuine FIRST-ever
    terminal failure (reason does not start with the resubmitted-reuse
    prefix) must fall through to a real resubmission attempt, not be held."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        review_file = os.path.join(d, "PENDING_OWNER_REVIEW.md")
        log_file = os.path.join(d, "directive_status.log")
        sbr = _seed_scratch_db(scratch_db)
        de = _load_directive_engine(_make_env(scratch_db, review_file, log_file))

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
        de.submit_task = lambda *a, **k: (calls.append((a, k)) or {"accepted": True, "umr_id": umr_id})

        result = de.process_one({"task_identity": "test-first-failure-task", "tier": 2,
                                  "title": "t", "prompt": "p", "repo": "x"})
        assert result == "queued", result
        assert len(calls) == 1, "first real terminal failure must be retried exactly once"
        assert not os.path.exists(review_file) or "test-first-failure-task" not in open(review_file).read()


def test_second_terminal_failure_after_resubmission_is_held_not_reretried():
    """A task_identity whose latest row already carries the real
    'resubmitted (reused umr_id...' reason AND is terminal again must be held
    for Owner review, never resubmitted a second time -- durable across a
    fresh process/module load, unlike the old in-memory flag."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        review_file = os.path.join(d, "PENDING_OWNER_REVIEW.md")
        log_file = os.path.join(d, "directive_status.log")
        sbr = _seed_scratch_db(scratch_db)
        de = _load_directive_engine(_make_env(scratch_db, review_file, log_file))

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

        calls = []
        de.submit_task = lambda *a, **k: (calls.append((a, k)) or {"accepted": True, "umr_id": umr_id})

        result = de.process_one({"task_identity": "test-exhausted-retry-task", "tier": 1,
                                  "title": "t", "prompt": "p", "repo": "x"})
        assert result == "killed", result
        assert len(calls) == 0, "an already-retried, still-terminal task_identity must never be resubmitted again"
        assert os.path.exists(review_file)
        content = open(review_file).read()
        assert "test-exhausted-retry-task" in content


def test_exhausted_retry_reprocessed_across_a_fresh_module_reload_still_holds():
    """The real regression: simulate main()'s own behavior of reloading
    directive_engine's module-level state fresh every outer-loop tick (here,
    a brand-new module import, the strongest possible proof no in-memory
    state survives) and re-run process_one() a second time on the SAME
    already-exhausted task_identity. Must still be held, not resubmitted --
    this is exactly the scenario the old entry["_retried"] flag failed at,
    since a fresh dict/module has no memory of the first pass."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        review_file = os.path.join(d, "PENDING_OWNER_REVIEW.md")
        log_file = os.path.join(d, "directive_status.log")
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

        env = _make_env(scratch_db, review_file, log_file)
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
        scratch_db = os.path.join(d, "scratch.sqlite")
        review_file = os.path.join(d, "PENDING_OWNER_REVIEW.md")
        log_file = os.path.join(d, "directive_status.log")
        _seed_scratch_db(scratch_db)
        de = _load_directive_engine(_make_env(scratch_db, review_file, log_file))

        de.note_needs_review("test-idempotent-task", "first real reason")
        de.note_needs_review("test-idempotent-task", "a different real reason, same task")
        content = open(review_file).read()
        assert content.count("- test-idempotent-task:") == 1, content


def test_completed_and_active_statuses_unaffected():
    """Sanity: the retry-gate change must not touch the completed/active
    early-return branches above it."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        review_file = os.path.join(d, "PENDING_OWNER_REVIEW.md")
        log_file = os.path.join(d, "directive_status.log")
        sbr = _seed_scratch_db(scratch_db)
        de = _load_directive_engine(_make_env(scratch_db, review_file, log_file))

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
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
