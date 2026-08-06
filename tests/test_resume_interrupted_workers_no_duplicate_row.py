#!/usr/bin/env python3
"""Real regression test for UMR-20260806-103711-bf00: dispatch-tick.py's
resume_interrupted_workers_tick() must not write a fresh rejected_duplicate
umr_tasks row every tick for a task_identity that already has a real active
(queued/dispatched/running) row -- confirmed live via the production
superboss-register.sqlite before this fix: 6238 rejected_duplicate rows from
source_trigger='dispatch-tick:resume_interrupted_workers' (e.g. 6 written in
the single second 2026-08-06T10:33:19-20Z alone), against only 16 queued / 6
running / 85 failed / 7 killed for that same source_trigger.

Root cause: the unit staying non-active across ticks (e.g. still capped,
not yet dispatched by resource_governor's own gate) is a completely normal,
expected state -- not evidence of a fresh interruption -- but the function
used to call resource_governor.submit() unconditionally every tick anyway,
relying entirely on submit()'s own lock-protected duplicate gate to reject
it (correctly) and log a new stub row (the actual defect: correct rejection,
wasteful row).

The fix adds a read-only pre-check (_existing_active_umr()) before calling
submit(): if a live row already covers this task_identity, skip the submit()
call entirely -- same real outcome (task stays exactly where it already was
in queue), zero new duplicate rows written. submit()'s own gate is completely
untouched and remains the real authority for any genuine race (see
test_umr_reuse_on_resume.py's test_active_prior_run_still_rejected_as_duplicate_not_reused
for that gate's own, still-passing regression coverage).

Every test here uses a real, isolated, temp-file SQLite database seeded with
the real schema -- never the live production database.
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location("sbr_seed_resume_nodup", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    conn.close()


def _load_dispatch_tick(env):
    # dispatch-tick.py does a plain top-level `import resource_governor` (and
    # `import dispatch_core`) -- unlike this test's own _load()-style helpers
    # for resource_governor.py/superboss-register.py (which always load a
    # fresh, uniquely-named module via importlib), a plain `import` statement
    # always resolves through the process-wide sys.modules cache. Popping
    # both here first forces dispatch-tick.py's own import to genuinely
    # reimport a fresh resource_governor (with its own fresh, empty
    # _sbr cache -- see resource_governor.py's _superboss_register()) bound
    # to THIS call's env, rather than silently reusing a previous test's
    # resource_governor module still pointed at that test's now-deleted
    # scratch DB.
    sys.modules.pop("resource_governor", None)
    sys.modules.pop("dispatch_core", None)
    old_env = {}
    for k, v in env.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location("dispatch_tick_resume_nodup", os.path.join(SCRIPTS_DIR, "dispatch-tick.py"))
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


def _row_count(sbr, db_path, task_identity):
    conn = sbr._connect()
    try:
        return conn.execute(
            "SELECT count(*) FROM umr_tasks WHERE task_identity=?", (task_identity,)
        ).fetchone()[0]
    finally:
        conn.close()


def test_repeated_ticks_still_inactive_write_exactly_one_row():
    """The real reported defect, reproduced: a task whose unit stays
    'inactive' tick after tick (e.g. still capped, not a genuine new
    interruption) must be resubmitted-and-accepted exactly once -- every
    later tick must SKIP via the new read-only pre-check, writing zero
    additional rows, while still reporting the task as a real (pre-check)
    skip, never silently dropped."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        dispatch_tick = _load_dispatch_tick(env)
        sbr_spec = importlib.util.spec_from_file_location("sbr_check_resume_nodup", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
        sbr = importlib.util.module_from_spec(sbr_spec)
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            sbr_spec.loader.exec_module(sbr)

            real_run = dispatch_tick.run
            dispatch_tick.run = _fake_run_factory(
                real_run, {"veridian-worker@resume-nodup-task-1.service": "inactive"})

            tasks = {
                "resume-nodup-task-1": {
                    "status": "in_progress",
                    "service": "veridian-worker@resume-nodup-task-1.service",
                },
            }

            try:
                first = dispatch_tick.resume_interrupted_workers_tick(tasks)
                second = dispatch_tick.resume_interrupted_workers_tick(tasks)
                third = dispatch_tick.resume_interrupted_workers_tick(tasks)
            finally:
                dispatch_tick.run = real_run

            row_count = _row_count(sbr, scratch_db, "resume-nodup-task-1")
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert "resume-nodup-task-1" in first["resumed"], first
        assert "resume-nodup-task-1" not in second["resumed"], second
        assert "resume-nodup-task-1" in second["skipped_duplicate"], second
        assert "resume-nodup-task-1" not in third["resumed"], third
        assert "resume-nodup-task-1" in third["skipped_duplicate"], third
        assert row_count == 1, (
            f"UMR-20260806-103711-bf00 regression: expected exactly 1 real umr_tasks row "
            f"for a task resubmitted across 3 ticks while still inactive, got {row_count} "
            f"(pre-fix behavior wrote a fresh rejected_duplicate row every tick)"
        )
        print(f"PASS: test_repeated_ticks_still_inactive_write_exactly_one_row -> "
              f"1st tick resumed={first['resumed']}, 2nd/3rd tick skipped_duplicate (no new row), "
              f"real row_count={row_count}")


def test_legitimate_resume_after_real_terminal_completion_still_dispatches():
    """The pre-check must not block a REAL new resume: once the prior row for
    this task_identity has gone terminal (e.g. completed) and the unit is
    inactive again (a genuine new interruption), resume_interrupted_workers_
    tick() must still submit and get accepted -- _existing_active_umr() only
    ever matches queued/dispatched/running rows, never a terminal one."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        dispatch_tick = _load_dispatch_tick(env)
        sbr_spec = importlib.util.spec_from_file_location("sbr_check_resume_nodup2", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
        sbr = importlib.util.module_from_spec(sbr_spec)
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            sbr_spec.loader.exec_module(sbr)

            real_run = dispatch_tick.run
            dispatch_tick.run = _fake_run_factory(
                real_run, {"veridian-worker@resume-nodup-task-2.service": "inactive"})

            tasks = {
                "resume-nodup-task-2": {
                    "status": "in_progress",
                    "service": "veridian-worker@resume-nodup-task-2.service",
                },
            }

            try:
                first = dispatch_tick.resume_interrupted_workers_tick(tasks)
                first_umr_id = first.get("resumed") and None  # placeholder, real id fetched below

                conn = sbr._connect()
                try:
                    row = conn.execute(
                        "SELECT umr_id FROM umr_tasks WHERE task_identity=? ORDER BY ts_submitted DESC LIMIT 1",
                        ("resume-nodup-task-2",),
                    ).fetchone()
                    real_umr_id = row["umr_id"]
                    sbr.update_umr_task(conn, real_umr_id, status="completed", reason="test: simulated real completion")
                    conn.commit()
                finally:
                    conn.close()

                second = dispatch_tick.resume_interrupted_workers_tick(tasks)
            finally:
                dispatch_tick.run = real_run

            row_count = _row_count(sbr, scratch_db, "resume-nodup-task-2")
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert "resume-nodup-task-2" in first["resumed"], first
        assert "resume-nodup-task-2" in second["resumed"], (
            "regression: a genuine new interruption after a real terminal completion "
            f"must still be resumed, got {second}"
        )
        assert row_count == 1, (
            f"expected the second real resume to REUSE the same row (Rule 1 reuse-on-resume), "
            f"got {row_count} rows"
        )
        print("PASS: test_legitimate_resume_after_real_terminal_completion_still_dispatches -> "
              f"both ticks resumed, real row_count={row_count} (reused, not duplicated)")


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
