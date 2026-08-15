#!/usr/bin/env python3
"""Real regression tests for the killed-row-resurrection defect (root-cause
evidence handed to UMR-20260806-093654-7566, parent UMR-20260806-071025-1d28).

Real evidence this closes (veridian-directive-engine.service journal, restart
2026-08-06T10:17:50Z): for PHASE-3-BUILD-CALC, "check-duplicate battery call
failed, fail-open, proceeding" was immediately followed by "submitted,
umr_id=UMR-20260730-041943-093a" -- reusing that task_identity's own
already-killed row's umr_id. Same pair for PHASE-4-BUILD-WORKFLOW. Confirmed
independently against the live umr_tasks row itself
(UMR-20260730-041943-093a, reason="resubmitted (reused umr_id, prior status
was 'killed')").

Two distinct real defects, two distinct real tests:

1. directive_engine.py's process_one() used to treat a FAILED duplicate-check
   call (run_check_duplicate_battery() returning None on any subprocess/
   parse/timeout error) exactly the same as a call that ran fine and found no
   duplicate -- silently falling through to submit_task() (fail OPEN). Fixed:
   a failed check now skips the submission and logs a real blocker (fail
   CLOSED), symmetric with the existing duplicate_found=true branch.
   test_failed_duplicate_check_skips_submission_fail_closed below.

2. resource_governor.py's submit() Rule-1 reuse-on-resubmit path used to
   ALWAYS reuse a prior row's umr_id for a given task_identity, regardless of
   whether that prior row was terminal -- correct for its real
   still-non-terminal "resume an interrupted worker" caller
   (dispatch-tick.py), but wrong for directive_engine.py's one real
   resubmission of an already-terminal row: reusing the terminal row's own
   umr_id flips it back to status="queued" in place, resurrecting a
   killed/failed/rejected row under its own old identity. Fixed: submit()'s
   new opt-in task_spec["force_new_umr_id"] flag (set by directive_engine.py's
   submit_task() on exactly that one retry) always mints a fresh umr_id and
   leaves the terminal row completely untouched.
   test_terminal_row_cannot_be_revived_under_its_own_umr_id below.

These tests use a real, isolated, temp-file SQLite database (never the live
production database), same convention as test_directive_engine_retry_gate.py.
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_module(filename, name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_scratch_db(path):
    sbr = _load_module("superboss-register.py", "sbr_seed_fc")
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
            f"directive_engine_test_fc_{id(env)}", os.path.join(SCRIPTS_DIR, "directive_engine.py"))
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


def test_failed_duplicate_check_skips_submission_fail_closed():
    """A duplicate check that cannot verify (run_check_duplicate_battery()
    returns None, e.g. a broken/timed-out task-gateway.py subprocess call)
    must result in a skip, never a submission -- the real defect this closes
    used to fall through to submit_task() on exactly this signal."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db, review_file, log_file, retry_state_file = _paths(d)
        sbr = _seed_scratch_db(scratch_db)
        de = _load_directive_engine(_make_env(scratch_db, review_file, log_file, retry_state_file))

        # No prior umr_tasks row at all -- a genuinely first-ever submission
        # attempt, so the ONLY thing standing between this call and
        # submit_task() is run_check_duplicate_battery()'s real return value.
        de.run_check_duplicate_battery = lambda *a, **k: None  # simulates a real failed call

        calls = []
        de.submit_task = lambda *a, **k: (calls.append((a, k)) or {"accepted": True, "umr_id": "UMR-should-never-be-reached"})

        result = de.process_one({"task_identity": "test-battery-failed-task", "tier": 2,
                                  "title": "t", "prompt": "p", "repo": "x"})

        assert result == "duplicate_check_failed_fail_closed", result
        assert len(calls) == 0, "a failed duplicate check must never fall through to submit_task()"
        assert os.path.exists(review_file), "a failed duplicate check must log a real Owner-review blocker"
        content = open(review_file).read()
        assert "test-battery-failed-task" in content
        assert "fail-closed" in content

        log_content = open(log_file).read()
        assert "check-duplicate battery call failed" in log_content
        assert "fail-closed" in log_content
        assert "fail-open" not in log_content


def test_successful_duplicate_check_with_no_duplicate_still_submits():
    """Sanity/contrast: a duplicate check that runs successfully and reports
    no duplicate must still reach submit_task() -- only a FAILED check
    (battery is None) is fail-closed, not every non-duplicate result."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db, review_file, log_file, retry_state_file = _paths(d)
        sbr = _seed_scratch_db(scratch_db)
        de = _load_directive_engine(_make_env(scratch_db, review_file, log_file, retry_state_file))

        de.run_check_duplicate_battery = lambda *a, **k: {"duplicate_found": False}

        calls = []
        de.submit_task = lambda *a, **k: (calls.append((a, k)) or {"accepted": True, "umr_id": "UMR-real-fresh"})

        result = de.process_one({"task_identity": "test-battery-clean-task", "tier": 2,
                                  "title": "t", "prompt": "p", "repo": "x"})
        assert result == "queued", result
        assert len(calls) == 1


def test_terminal_row_cannot_be_revived_under_its_own_umr_id():
    """A resubmission of a task_identity whose most recent umr_tasks row is
    already terminal (killed) must mint a genuinely NEW umr_id -- never reuse
    the terminal row's own umr_id (which would flip it back to status=
    'queued' in place, resurrecting a closed row under its own old
    identity). Exercises the real resource_governor.py submit() entrypoint
    directly (not a mock), through directive_engine.py's submit_task()
    force_new_umr_id wiring."""
    rg = _load_module("resource_governor.py", "rg_test_fc")
    with tempfile.TemporaryDirectory() as d:
        scratch_db, review_file, log_file, retry_state_file = _paths(d)
        sbr = _seed_scratch_db(scratch_db)

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        terminal_umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": "test-terminal-row-task", "tier": 2, "status": "killed",
            "source_trigger": "DIRECTIVE", "task_kind": "veridian_task_create",
            "inputs": {"repo": "x", "title": "t", "prompt": "p"},
            "reason": "killed by supervisor",
        })
        conn.commit()
        conn.close()

        old_env = os.environ.get("SUPERBOSS_REGISTER_DB")
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            # directive_engine.py's real resubmission-of-a-terminal-row shape:
            # same task_identity, force_new_umr_id=True (exactly what
            # submit_task(..., force_new_umr_id=True) constructs).
            result = rg.submit(
                {
                    "task_identity": "test-terminal-row-task",
                    "task_kind": "veridian_task_create",
                    "inputs": {"repo": "x", "title": "t", "prompt": "p"},
                    "force_new_umr_id": True,
                },
                tier=2, source_trigger="DIRECTIVE",
            )
        finally:
            if old_env is None:
                os.environ.pop("SUPERBOSS_REGISTER_DB", None)
            else:
                os.environ["SUPERBOSS_REGISTER_DB"] = old_env

        assert result.get("accepted") is True, result
        new_umr_id = result.get("umr_id")
        assert new_umr_id, result
        assert new_umr_id != terminal_umr_id, (
            "resubmission must mint a NEW umr_id, never reuse the terminal row's own umr_id"
        )

        # The prior terminal row itself must be completely untouched -- still
        # its own row, still killed, never flipped back to queued/running.
        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        prior_row = conn.execute(
            "SELECT status, reason FROM umr_tasks WHERE umr_id=?", (terminal_umr_id,)
        ).fetchone()
        new_row = conn.execute(
            "SELECT status, task_identity FROM umr_tasks WHERE umr_id=?", (new_umr_id,)
        ).fetchone()
        conn.close()

        assert prior_row is not None
        assert prior_row["status"] == "killed", (
            f"the terminal row must never be resurrected in place, got status={prior_row['status']!r}"
        )
        assert new_row is not None
        assert new_row["status"] == "queued"
        assert new_row["task_identity"] == "test-terminal-row-task"


def test_resubmission_without_force_flag_still_reuses_umr_id_for_other_callers():
    """Contrast/regression guard: omitting force_new_umr_id (every real
    caller except directive_engine.py's terminal-retry path, notably
    dispatch-tick.py's resume-an-interrupted-worker use case) must keep
    today's Rule-1 reuse-on-resubmit behavior completely unchanged."""
    rg = _load_module("resource_governor.py", "rg_test_fc2")
    with tempfile.TemporaryDirectory() as d:
        scratch_db, review_file, log_file, retry_state_file = _paths(d)
        sbr = _seed_scratch_db(scratch_db)

        # Rule-1's real reuse-on-resubmit path only runs once
        # find_active_umr_by_identity() has already cleared the row (it only
        # blocks queued/dispatched/running) -- dispatch-tick.py's real
        # resume_interrupted_workers_tick() caller hits this AFTER a stale
        # worker's row has already been reconciled to a terminal status
        # (e.g. 'failed'), same as this module's own Rule-1 docstring
        # ("after its prior umr_tasks row already went terminal"). 'failed'
        # here, not 'running', for exactly that reason.
        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        prior_umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": "test-resume-task", "tier": 1, "status": "failed",
            "source_trigger": "resource_governor_resume", "task_kind": "systemctl_action",
            "unit_name": "veridian-worker@test-resume-task.service",
            "inputs": {"action": "start"},
            "reason": "reconciled by heartbeat sweep: unit inactive, real exit status=failed",
        })
        conn.commit()
        conn.close()

        old_env = os.environ.get("SUPERBOSS_REGISTER_DB")
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            result = rg.submit(
                {
                    "task_identity": "test-resume-task",
                    "task_kind": "systemctl_action",
                    "unit_name": "veridian-worker@test-resume-task.service",
                    "inputs": {"action": "reset_failed_and_start"},
                },
                tier=1, source_trigger="resource_governor_resume",
            )
        finally:
            if old_env is None:
                os.environ.pop("SUPERBOSS_REGISTER_DB", None)
            else:
                os.environ["SUPERBOSS_REGISTER_DB"] = old_env

        assert result.get("accepted") is True, result
        assert result.get("umr_id") == prior_umr_id, (
            "without force_new_umr_id, a real resume caller must keep reusing the prior umr_id exactly as before"
        )


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
