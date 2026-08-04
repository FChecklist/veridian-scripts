#!/usr/bin/env python3
"""Real regression test for OCID-068's seven-rule guardrails addendum, Rule 1
(UMR-20260804-180711-7f96, UMR-20260804-194355-be9c, citing OCID-068's own
UMR-20260804-170055-a069): "one logical task shall have exactly one OCID,
exactly one UMR, and one canonical task identity, and any retry, resume,
redispatch, supervisor, worker, executor, or restart shall reuse the existing
UMR rather than minting a new one."

Real, previously-documented gap this closes: resource_governor.py's own
module comment (above find_active_umr_by_identity()) already stated that
function "only rejects a SECOND submission while the FIRST is still active
... it cannot see a prior run that already finished." Before this fix,
submit() always called upsert_umr_task() with no umr_id, which mints a fresh
one every time (superboss-register.py's upsert_umr_task():
`record.get("umr_id") or _new_id("UMR")`). The real caller this matters for
is dispatch-tick.py's resume_interrupted_workers_tick(), which reuses
task_identity=task_id stably across every resume attempt for a given worker
task -- exactly the retry/resume/redispatch case Rule 1 names.

Every test uses a real, isolated, temp-file SQLite database seeded with the
real schema -- never the live production database.
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location("sbr_seed", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
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


def test_terminal_prior_run_is_reused_not_reminted():
    """First submit() call goes to a terminal status ("completed") --
    simulating a worker task that already finished a run. A second submit()
    call with the SAME task_identity (simulating resume_interrupted_workers_
    tick() resubmitting a task whose unit died) must reuse the exact same
    umr_id, not mint a new one."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_reuse_terminal", "resource_governor.py", env=env)
        sbr = _load("sbr_reuse_terminal", "superboss-register.py", env=env)

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            first = rg.submit(
                task_spec={"task_identity": "test-resume-reuse-task-1", "task_kind": "systemctl_action",
                           "unit_name": "veridian-worker@test-resume-reuse-task-1.service",
                           "inputs": {"action": "start"}},
                tier=2, source_trigger="unit_test",
            )
            assert first["accepted"] is True, first
            first_umr_id = first["umr_id"]

            conn = sbr._connect()
            try:
                sbr.update_umr_task(conn, first_umr_id, status="completed", reason="test: simulated real completion")
                conn.commit()
            finally:
                conn.close()

            second = rg.submit(
                task_spec={"task_identity": "test-resume-reuse-task-1", "task_kind": "systemctl_action",
                           "unit_name": "veridian-worker@test-resume-reuse-task-1.service",
                           "inputs": {"action": "start", "resumed_after_state": "inactive"}},
                tier=1, source_trigger="unit_test:resume",
            )
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert second["accepted"] is True, second
        assert second["umr_id"] == first_umr_id, (
            f"Rule 1 violation: resume minted a NEW umr_id {second['umr_id']!r} instead of "
            f"reusing the prior terminal run's {first_umr_id!r}"
        )
        print(f"PASS: test_terminal_prior_run_is_reused_not_reminted -> reused umr_id={first_umr_id}")


def test_active_prior_run_still_rejected_as_duplicate_not_reused():
    """Rule 1's reuse behavior must NOT weaken the existing, distinct
    concurrency guard: two submissions for the same task_identity while the
    FIRST is still active (queued/dispatched/running) must still be rejected
    as rejected_duplicate, exactly as before -- reuse only applies to a
    genuinely terminal prior run."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_reuse_active", "resource_governor.py", env=env)

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            first = rg.submit(
                task_spec={"task_identity": "test-resume-reuse-task-2", "task_kind": "systemctl_action",
                           "unit_name": "veridian-worker@test-resume-reuse-task-2.service",
                           "inputs": {"action": "start"}},
                tier=2, source_trigger="unit_test",
            )
            assert first["accepted"] is True, first

            second = rg.submit(
                task_spec={"task_identity": "test-resume-reuse-task-2", "task_kind": "systemctl_action",
                           "unit_name": "veridian-worker@test-resume-reuse-task-2.service",
                           "inputs": {"action": "start"}},
                tier=2, source_trigger="unit_test",
            )
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert second["accepted"] is False, second
        assert "rejected_duplicate" not in first  # sanity: first accepted, not itself a rejection record
        print(f"PASS: test_active_prior_run_still_rejected_as_duplicate_not_reused -> {second['reason']}")


def test_fresh_task_identity_gets_a_new_umr_as_before():
    """A task_identity never seen before (the normal, overwhelmingly common
    case -- every owner-dispatch call mints a fresh timestamp+pid identity)
    must still get a brand-new umr_id, unaffected by this fix."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_reuse_fresh", "resource_governor.py", env=env)

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            result = rg.submit(
                task_spec={"task_identity": "test-resume-reuse-task-3-never-seen", "task_kind": "systemctl_action",
                           "unit_name": "veridian-worker@test-resume-reuse-task-3.service",
                           "inputs": {"action": "start"}},
                tier=2, source_trigger="unit_test",
            )
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert result["accepted"] is True, result
        assert result["umr_id"], result
        print(f"PASS: test_fresh_task_identity_gets_a_new_umr_as_before -> umr_id={result['umr_id']}")


def test_reused_umr_preserves_original_ts_submitted():
    """A reused UMR's original ts_submitted (the real, first-ever submission
    time for this logical task) must survive the reuse -- upsert_umr_task()'s
    ON CONFLICT DO UPDATE deliberately excludes ts_submitted from its SET
    list, same as tenant_id/utm fields, so this is a real assertion on that
    existing invariant continuing to hold through this new code path."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load("rg_reuse_ts", "resource_governor.py", env=env)
        sbr = _load("sbr_reuse_ts", "superboss-register.py", env=env)

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            first = rg.submit(
                task_spec={"task_identity": "test-resume-reuse-task-4", "task_kind": "systemctl_action",
                           "unit_name": "veridian-worker@test-resume-reuse-task-4.service",
                           "inputs": {"action": "start"}},
                tier=2, source_trigger="unit_test",
            )
            conn = sbr._connect()
            try:
                original_ts = conn.execute(
                    "SELECT ts_submitted FROM umr_tasks WHERE umr_id=?", (first["umr_id"],)
                ).fetchone()["ts_submitted"]
                sbr.update_umr_task(conn, first["umr_id"], status="failed", reason="test: simulated real failure")
                conn.commit()
            finally:
                conn.close()

            second = rg.submit(
                task_spec={"task_identity": "test-resume-reuse-task-4", "task_kind": "systemctl_action",
                           "unit_name": "veridian-worker@test-resume-reuse-task-4.service",
                           "inputs": {"action": "reset_failed_and_start"}},
                tier=1, source_trigger="unit_test:resume",
            )
            conn = sbr._connect()
            try:
                reused_ts = conn.execute(
                    "SELECT ts_submitted FROM umr_tasks WHERE umr_id=?", (second["umr_id"],)
                ).fetchone()["ts_submitted"]
            finally:
                conn.close()
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert second["umr_id"] == first["umr_id"], second
        assert reused_ts == original_ts, f"expected original ts_submitted {original_ts!r} preserved, got {reused_ts!r}"
        print(f"PASS: test_reused_umr_preserves_original_ts_submitted -> ts_submitted={original_ts}")


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
