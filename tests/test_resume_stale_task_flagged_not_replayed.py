#!/usr/bin/env python3
"""Real regression test for UMR-20260806-151638-48cc (governing
UMR-20260806-071025-1d28): dispatch-tick.py's resume_interrupted_workers_tick()
must not blindly auto-resume a task.yaml that has genuinely sat silent for
hours (real incident: 4 task.yaml rows whose last real activity was hours
old got their original, now-stale prompt text blindly replayed) -- it must
instead flag the task into pm_decisions_pending (RESUME_STALE_HOURS gate,
reusing health-check-15min.py's own BLOCKED_STALE_HOURS=2 convention) and
skip the auto-resume, while a genuinely-recent interruption (real crash
minutes ago) must still resume immediately -- this fix must never weaken
real crash recovery.

Every test here uses a real, isolated, temp-file SQLite database seeded
with the real schema -- never the live production database.
"""
import datetime
import importlib.util
import os
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location(
        "sbr_seed_stale_resume", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    sbr._ensure_pm_decisions_pending_table(conn)
    conn.close()


def _load_dispatch_tick(env):
    # See test_resume_interrupted_workers_no_duplicate_row.py's own
    # _load_dispatch_tick() for why resource_governor/dispatch_core must be
    # popped from sys.modules first -- same reasoning, not duplicated here.
    sys.modules.pop("resource_governor", None)
    sys.modules.pop("dispatch_core", None)
    old_env = {}
    for k, v in env.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location(
            "dispatch_tick_stale_resume", os.path.join(SCRIPTS_DIR, "dispatch-tick.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _fake_run_factory(real_run, states):
    def fake_run(cmd):
        if cmd[:3] == ["systemctl", "--user", "show"]:
            unit = cmd[3]

            class _R:
                pass
            r = _R()
            r.stdout = states.get(unit, "inactive")
            r.returncode = 0
            return r
        return real_run(cmd)
    return fake_run


def _iso(dt):
    return dt.isoformat()


def test_task_silent_for_hours_is_flagged_not_auto_resumed():
    """The real incident, reproduced: a task.yaml last checkpointed 7 hours
    ago (matches the real UMR-20260806-082230-54b8 family's real
    ts_submitted-to-ts_dispatched gap) with its unit inactive must NOT be
    silently resubmitted with its stale prompt -- it must be skipped from
    'resumed', appear in 'flagged_stale', and get a real pm_decisions_pending
    row opened for it."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        dispatch_tick = _load_dispatch_tick(env)
        sbr_spec = importlib.util.spec_from_file_location(
            "sbr_check_stale_resume", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
        sbr = importlib.util.module_from_spec(sbr_spec)
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            sbr_spec.loader.exec_module(sbr)

            real_run = dispatch_tick.run
            dispatch_tick.run = _fake_run_factory(
                real_run, {"veridian-worker@stale-task-1.service": "inactive"})

            now = datetime.datetime.now(datetime.timezone.utc)
            seven_hours_ago = now - datetime.timedelta(hours=7)
            tasks = {
                "stale-task-1": {
                    "status": "in_progress",
                    "service": "veridian-worker@stale-task-1.service",
                    "created_at": _iso(seven_hours_ago),
                    "last_checkpoint_at": _iso(seven_hours_ago),
                },
            }

            try:
                result = dispatch_tick.resume_interrupted_workers_tick(tasks)
            finally:
                dispatch_tick.run = real_run

            conn = sbr._connect()
            try:
                pending = conn.execute(
                    "SELECT title, detail, status FROM pm_decisions_pending "
                    "WHERE title LIKE '%stale-task-1%'"
                ).fetchall()
                row_count = conn.execute(
                    "SELECT count(*) FROM umr_tasks WHERE task_identity=?", ("stale-task-1",)
                ).fetchone()[0]
            finally:
                conn.close()
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert "stale-task-1" not in result["resumed"], (
            f"regression: a task silent for 7h (over RESUME_STALE_HOURS="
            f"{dispatch_tick.RESUME_STALE_HOURS}) was blindly auto-resumed: {result}"
        )
        assert "stale-task-1" in result["flagged_stale"], result
        assert row_count == 0, (
            f"a stale task must never reach resource_governor.submit() at all -- "
            f"expected 0 real umr_tasks rows written, got {row_count}"
        )
        assert len(pending) == 1, (
            f"expected exactly 1 real pm_decisions_pending row opened for the flagged "
            f"stale task, got {len(pending)}"
        )
        assert pending[0]["status"] == "open", pending[0]
        assert "7." in pending[0]["detail"] or "7h" in pending[0]["detail"], (
            f"expected the real ~7h age to be named in the decision detail: {pending[0]['detail']}"
        )
        print(f"PASS: test_task_silent_for_hours_is_flagged_not_auto_resumed -> "
              f"flagged_stale={result['flagged_stale']}, resumed={result['resumed']}, "
              f"umr_tasks rows written={row_count}, pm_decisions_pending rows={len(pending)}")


def test_task_interrupted_minutes_ago_still_resumes_immediately():
    """Never weaken real crash recovery: a task last checkpointed 5 minutes
    ago (genuinely mid-work when the box crashed) with its unit inactive
    must still be auto-resumed immediately, exactly as before this fix."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        dispatch_tick = _load_dispatch_tick(env)
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            real_run = dispatch_tick.run
            dispatch_tick.run = _fake_run_factory(
                real_run, {"veridian-worker@fresh-crash-task-1.service": "inactive"})

            now = datetime.datetime.now(datetime.timezone.utc)
            five_min_ago = now - datetime.timedelta(minutes=5)
            tasks = {
                "fresh-crash-task-1": {
                    "status": "in_progress",
                    "service": "veridian-worker@fresh-crash-task-1.service",
                    "created_at": _iso(now - datetime.timedelta(hours=3)),
                    "last_checkpoint_at": _iso(five_min_ago),
                },
            }

            try:
                result = dispatch_tick.resume_interrupted_workers_tick(tasks)
            finally:
                dispatch_tick.run = real_run
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert "fresh-crash-task-1" in result["resumed"], (
            f"regression: a task genuinely interrupted 5 minutes ago was NOT resumed -- "
            f"this fix must never weaken real crash recovery: {result}"
        )
        assert "fresh-crash-task-1" not in result["flagged_stale"], result
        print(f"PASS: test_task_interrupted_minutes_ago_still_resumes_immediately -> "
              f"resumed={result['resumed']}")


def test_task_with_no_real_timestamp_fails_open_and_still_resumes():
    """A task.yaml with neither last_checkpoint_at nor created_at gives this
    gate nothing real to measure -- must fail OPEN (resume proceeds, same as
    every other real staleness check in this module), never be silently
    dropped for lack of evidence."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        dispatch_tick = _load_dispatch_tick(env)
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            real_run = dispatch_tick.run
            dispatch_tick.run = _fake_run_factory(
                real_run, {"veridian-worker@no-timestamp-task-1.service": "inactive"})

            tasks = {
                "no-timestamp-task-1": {
                    "status": "in_progress",
                    "service": "veridian-worker@no-timestamp-task-1.service",
                },
            }

            try:
                result = dispatch_tick.resume_interrupted_workers_tick(tasks)
            finally:
                dispatch_tick.run = real_run
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert "no-timestamp-task-1" in result["resumed"], (
            f"regression: missing real timestamps must fail OPEN (resume), never silently "
            f"block a real resume for lack of evidence: {result}"
        )
        assert "no-timestamp-task-1" not in result["flagged_stale"], result
        print(f"PASS: test_task_with_no_real_timestamp_fails_open_and_still_resumes -> "
              f"resumed={result['resumed']}")


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
