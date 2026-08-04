#!/usr/bin/env python3
"""Real tests for OCID-068's seven-rule guardrails addendum, Rule 2
(UMR-20260804-180711-7f96, UMR-20260804-203846-e722, citing OCID-068's own
UMR-20260804-170055-a069): "every dispatch shall return exactly one of five
allowed results, success, failed, blocked, rejected, or cancelled, with no
other state permitted, no silent failure, and no empty output, and on
failure the dispatch must return a real error id, a real root cause, real
evidence, and a real next action, then stop."

Covers classify_dispatch_outcome() directly (pure function, exhaustively
testable against every real dispatch_one() action) and dispatch_one() itself
end-to-end against a real isolated scratch SQLite DB (never the live
production database) to confirm the classification is genuinely merged into
its real return value, not just correct in isolation.
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location("sbr_seed_r2", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    conn.close()


def _load(name, filename, env=None):
    old_env = {}
    if env:
        for k, v in env.items():
            old_env[k] = os.environ.get(k)
            os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, filename))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if env:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)


def _rg():
    return _load("rg_rule2", "resource_governor.py")


def test_idle_and_would_dispatch_are_not_real_dispatch_attempts():
    rg = _rg()
    for action in ("idle", "would_dispatch"):
        result = rg.classify_dispatch_outcome({"action": action, "detail": "x"})
        assert result["outcome"] is None, result
        assert result["error_id"] is None and result["root_cause"] is None, result
        assert result["evidence"] is None and result["next_action"] is None, result
    print("PASS: test_idle_and_would_dispatch_are_not_real_dispatch_attempts")


def test_dispatched_running_is_success_with_no_failure_fields():
    rg = _rg()
    result = rg.classify_dispatch_outcome({
        "action": "dispatched", "umr_id": "UMR-TEST-1",
        "result": {"status": "running", "unit_name": "veridian-worker@x.service", "outputs": {}},
    })
    assert result["outcome"] == "success", result
    assert result["error_id"] is None and result["root_cause"] is None, result
    print("PASS: test_dispatched_running_is_success_with_no_failure_fields")


def test_dispatched_failed_is_failed_with_all_four_required_fields():
    rg = _rg()
    result = rg.classify_dispatch_outcome({
        "action": "dispatched", "umr_id": "UMR-TEST-2",
        "result": {"status": "failed", "unit_name": None,
                   "outputs": {"error": "unknown task_kind 'bogus'"}},
    })
    assert result["outcome"] == "failed", result
    assert result["error_id"] == "DISPATCH-FAILED-UMR-TEST-2", result
    assert result["root_cause"] == "unknown task_kind 'bogus'", result
    assert result["evidence"], "evidence must not be empty on failure"
    assert result["next_action"], "next_action must not be empty on failure"
    print(f"PASS: test_dispatched_failed_is_failed_with_all_four_required_fields -> {result['error_id']}")


def test_every_blocked_action_maps_correctly_with_required_fields():
    rg = _rg()
    for action in ("emergency_stopped", "frozen", "superboss_unavailable", "deferred"):
        result = rg.classify_dispatch_outcome({"action": action, "detail": f"real detail for {action}"})
        assert result["outcome"] == "blocked", (action, result)
        assert result["error_id"] and result["root_cause"] and result["evidence"] and result["next_action"], (action, result)
    print("PASS: test_every_blocked_action_maps_correctly_with_required_fields")


def test_rejected_duplicate_pr_maps_to_rejected():
    rg = _rg()
    result = rg.classify_dispatch_outcome({
        "action": "rejected_duplicate_pr", "umr_id": "UMR-TEST-3",
        "detail": "existing PR already open", "pr": {"repo": "compliance-tracker", "number": 900},
    })
    assert result["outcome"] == "rejected", result
    assert result["error_id"] and result["root_cause"] and result["evidence"] and result["next_action"], result
    print("PASS: test_rejected_duplicate_pr_maps_to_rejected")


def test_unrecognized_action_never_silently_empty():
    """Rule 2's own text: 'no other state permitted... no silent failure, and
    no empty output.' An action this classifier has no mapping for (a real
    future-addition gap) must still produce a fully-populated classification,
    never a None outcome or empty fields."""
    rg = _rg()
    result = rg.classify_dispatch_outcome({"action": "some_future_action_not_yet_mapped", "detail": "x"})
    assert result["outcome"] == "failed", result
    assert result["error_id"] and result["root_cause"] and result["evidence"] and result["next_action"], result
    print(f"PASS: test_unrecognized_action_never_silently_empty -> {result['error_id']}")


def test_dispatch_one_end_to_end_idle_queue_carries_none_outcome():
    """Real end-to-end: dispatch_one() against a real, empty, isolated
    scratch DB must genuinely return outcome=None (queue empty is not a
    dispatch attempt), not just in the pure-function unit tests above."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_rule2_e2e_idle", "resource_governor.py", env=env)

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            result = rg.dispatch_one()
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert result["action"] == "idle", result
        assert result["outcome"] is None, result
        assert "outcome" in result and "error_id" in result and "root_cause" in result, (
            "Rule 2 fields must always be present in dispatch_one()'s real return value, even when None"
        )
        print(f"PASS: test_dispatch_one_end_to_end_idle_queue_carries_none_outcome -> action={result['action']}, outcome={result['outcome']}")


def test_dispatch_one_end_to_end_real_submitted_task_carries_full_classification():
    """Real end-to-end: submit() a real, genuinely queued task, then call the
    real dispatch_one() against this test's real live environment. This
    process shares the real, live dispatch_core concurrency state with
    whatever else is genuinely running on this box right now, so the actual
    action (dispatched/deferred/frozen/etc.) is not deterministic here -- what
    IS asserted, and matters for Rule 2, is that whichever real action comes
    back, the classification is always fully, genuinely present and self-
    consistent (never a silently-missing outcome, never failure fields
    present without an outcome that needs them, or vice versa)."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_rule2_e2e_dispatch", "resource_governor.py", env=env)

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            submit_result = rg.submit(
                task_spec={"task_identity": "test-rule2-e2e-dispatch", "task_kind": "systemctl_action",
                           "unit_name": "veridian-worker@does-not-exist-test-unit.service",
                           "inputs": {"action": "start"}},
                tier=0, source_trigger="unit_test",
            )
            assert submit_result["accepted"] is True, submit_result

            result = rg.dispatch_one()
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert result["action"] in ("dispatched", "deferred", "frozen", "emergency_stopped", "superboss_unavailable"), result
        assert "outcome" in result, "Rule 2 classification key must always be present"
        if result["outcome"] is None:
            assert result["action"] in ("idle", "would_dispatch"), (
                f"outcome=None is only valid for idle/would_dispatch, got action={result['action']!r}"
            )
        else:
            assert result["outcome"] in ("success", "failed", "blocked", "rejected", "cancelled"), result
            if result["outcome"] != "success":
                assert result["error_id"] and result["root_cause"] and result["evidence"] and result["next_action"], result
        print(f"PASS: test_dispatch_one_end_to_end_real_submitted_task_carries_full_classification -> action={result['action']}, outcome={result['outcome']}")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__} -> {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR: {t.__name__} -> {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)
