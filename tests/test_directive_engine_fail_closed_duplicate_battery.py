#!/usr/bin/env python3
"""Real regression test for directive_engine.py's run_check_duplicate_battery()/
process_one() fail-CLOSED behavior (P0 dispatch-queue-starvation blocker, PM
sentinel cycle 2026-08-06T10:30Z, UMR-20260806-071025-1d28 /
UMR-20260806-090229-f2a7, this fix's own governing UMR-20260806-102737-d780).

REAL BUG (closed here): run_check_duplicate_battery() used to catch every
exception from its task-gateway.py submit subprocess call and return bare
`None` -- indistinguishable from "the battery genuinely ran and found no
duplicate". process_one() then fell through unconditionally to submit_task().
Live incident: veridian-directive-engine.service's own journal recorded
"check-duplicate battery call failed, fail-open, proceeding" immediately
followed by "submitted" on literally every tick, for eight distinct dead
task_identity values (~416 total resubmissions in one 15-minute window). Each
resubmission reused the same umr_id via resource_governor.py submit()'s
Rule-1 reuse-on-resubmit path (ts_submitted never refreshed), permanently
winning next_queued_task()'s ascending-ts_submitted tiebreak and starving
every other real queued row (only the single top-ranked row is evaluated per
tick).

THE FIX: run_check_duplicate_battery() now returns (result, call_failed) --
call_failed=True whenever the subprocess/parse itself failed. process_one()
must check that flag BEFORE ever calling submit_task(), and must skip
submission + flag for Owner review when it's True, exactly the same
fail-closed shape this module's own retry-once gate
(tests/test_directive_engine_retry_gate.py) already established for a
different real gap.

Uses the same real, isolated, temp-file SQLite database +
_load_directive_engine()/_seed_scratch_db() harness as
tests/test_directive_engine_retry_gate.py -- never the live production
database.
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location("sbr_seed_de_fc", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
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


def test_battery_call_failure_returns_call_failed_true_not_bare_none():
    """Real unit-level check on run_check_duplicate_battery() itself: point
    TASK_GATEWAY at a script that always raises inside subprocess (a
    nonexistent interpreter forces a real subprocess.run() exception), and
    confirm the function returns (None, True) -- never (None, False) or a
    bare None, which is exactly the pre-fix shape that looked identical to
    "no duplicate found"."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db, review_file, log_file, retry_state_file = _paths(d)
        _seed_scratch_db(scratch_db)
        de = _load_directive_engine(_make_env(scratch_db, review_file, log_file, retry_state_file))

        de.TASK_GATEWAY = "/nonexistent/path/task-gateway.py"
        result, call_failed = de.run_check_duplicate_battery("test-battery-fail-task", "t", "p")
        assert result is None, result
        assert call_failed is True, "a broken battery call must set call_failed=True, not look like success"


def test_battery_call_success_returns_call_failed_false():
    """Sanity: a real, successful battery call (subprocess exits 0 with
    parseable JSON) must return call_failed=False so process_one() proceeds
    normally -- the fix must not accidentally fail closed on the happy
    path too."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db, review_file, log_file, retry_state_file = _paths(d)
        _seed_scratch_db(scratch_db)
        de = _load_directive_engine(_make_env(scratch_db, review_file, log_file, retry_state_file))

        fake_gateway = os.path.join(d, "fake_gateway.py")
        with open(fake_gateway, "w") as f:
            f.write(
                "import sys, json\n"
                "print(json.dumps({'accepted': True, 'duplicate_found': False}))\n"
            )
        de.TASK_GATEWAY = fake_gateway
        result, call_failed = de.run_check_duplicate_battery("test-battery-ok-task", "t", "p")
        assert call_failed is False, call_failed
        assert result == {"accepted": True, "duplicate_found": False}, result


def test_process_one_does_not_submit_when_battery_call_fails():
    """The real end-to-end regression: process_one() must NOT call
    submit_task() when the duplicate-check battery call itself failed --
    this is the exact live-incident shape (battery raised -> 'submitted'
    logged anyway on every tick)."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db, review_file, log_file, retry_state_file = _paths(d)
        sbr = _seed_scratch_db(scratch_db)
        de = _load_directive_engine(_make_env(scratch_db, review_file, log_file, retry_state_file))

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        sbr.upsert_umr_task(conn, {
            "task_identity": "test-poison-pill-task", "tier": 2, "status": "killed",
            "source_trigger": "DIRECTIVE", "task_kind": "veridian_task_create",
            "inputs": {"repo": "x", "title": "t", "prompt": "p"}, "reason": "queued",
        })
        conn.commit()
        conn.close()

        de.run_check_duplicate_battery = lambda *a, **k: (None, True)
        calls = []
        de.submit_task = lambda *a, **k: (calls.append((a, k)) or {"accepted": True, "umr_id": "should-never-be-reached"})

        result = de.process_one({"task_identity": "test-poison-pill-task", "tier": 2,
                                  "title": "t", "prompt": "p", "repo": "x"})
        assert result == "battery_call_failed", result
        assert len(calls) == 0, (
            "submit_task() must never be called when the duplicate-check battery "
            "call itself failed -- this is the real fail-open->resubmission-storm "
            "bug this fix closes"
        )
        assert os.path.exists(review_file)
        content = open(review_file).read()
        assert "test-poison-pill-task" in content
        assert "fail" in content.lower()


def test_process_one_still_submits_when_battery_call_succeeds_and_finds_no_duplicate():
    """Sanity: the fix must not regress the real happy path -- a genuinely
    successful battery call reporting no duplicate must still result in a
    real submission."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db, review_file, log_file, retry_state_file = _paths(d)
        sbr = _seed_scratch_db(scratch_db)
        de = _load_directive_engine(_make_env(scratch_db, review_file, log_file, retry_state_file))

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        sbr.upsert_umr_task(conn, {
            "task_identity": "test-healthy-task", "tier": 2, "status": "killed",
            "source_trigger": "DIRECTIVE", "task_kind": "veridian_task_create",
            "inputs": {"repo": "x", "title": "t", "prompt": "p"}, "reason": "queued",
        })
        conn.commit()
        conn.close()

        de.run_check_duplicate_battery = lambda *a, **k: ({"accepted": True, "duplicate_found": False}, False)
        calls = []
        de.submit_task = lambda *a, **k: (calls.append((a, k)) or {"accepted": True, "umr_id": "real-umr-id"})

        result = de.process_one({"task_identity": "test-healthy-task", "tier": 2,
                                  "title": "t", "prompt": "p", "repo": "x"})
        assert result == "queued", result
        assert len(calls) == 1, "a genuinely healthy battery call finding no duplicate must still submit"


def test_process_one_still_flags_when_battery_call_succeeds_and_finds_duplicate():
    """Sanity: the pre-existing duplicate_found=True path (battery call
    itself succeeded) must be unaffected by this fix -- still flags for
    review, still does not submit."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db, review_file, log_file, retry_state_file = _paths(d)
        sbr = _seed_scratch_db(scratch_db)
        de = _load_directive_engine(_make_env(scratch_db, review_file, log_file, retry_state_file))

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        sbr.upsert_umr_task(conn, {
            "task_identity": "test-real-duplicate-task", "tier": 2, "status": "killed",
            "source_trigger": "DIRECTIVE", "task_kind": "veridian_task_create",
            "inputs": {"repo": "x", "title": "t", "prompt": "p"}, "reason": "queued",
        })
        conn.commit()
        conn.close()

        de.run_check_duplicate_battery = lambda *a, **k: (
            {"accepted": False, "duplicate_found": True, "duplicate_evidence": ["real-pr#1"]}, False)
        calls = []
        de.submit_task = lambda *a, **k: (calls.append((a, k)) or {"accepted": True, "umr_id": "should-never-be-reached"})

        result = de.process_one({"task_identity": "test-real-duplicate-task", "tier": 2,
                                  "title": "t", "prompt": "p", "repo": "x"})
        assert result == "duplicate_flagged_by_gateway_battery", result
        assert len(calls) == 0


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
