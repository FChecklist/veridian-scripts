#!/usr/bin/env python3
"""Real regression test for UMR-20260815-105911-a2c9 ("unstarve dispatch
queue, 07-18 retry resurrection"): dispatch-tick.py's
resume_interrupted_workers_tick() must cap how many real
resource_governor.submit() calls it makes in a single tick
(RESUME_SUBMIT_BATCH_LIMIT), rather than submitting every real resumable
task.yaml it finds in one unbounded sweep.

Root cause this closes: a single legitimate credit-unblock sweep
(2026-08-15T03:56-04:15Z, real superboss-register.sqlite evidence) queued 87
real task_identities in ~20 seconds, all at the same hardcoded tier=1 -- see
resource_governor.py's own OWNER_STARVATION_GUARANTEE_SECONDS comment for why
a same-tier batch that large can then structurally outrank any later-arriving
same-tier row (including real, live owner_dispatch_gateway rows) for as long
as it takes the whole batch to drain. This test proves
resume_interrupted_workers_tick() now stops calling resource_governor.submit()
once RESUME_SUBMIT_BATCH_LIMIT real calls have been made in one invocation,
leaving every remaining real candidate untouched (not skipped_dead, not
skipped_duplicate -- just left for a later tick, exactly like the existing
concurrency-cap deferral already works).

Every test here uses a real, isolated, temp-file SQLite database seeded with
the real schema -- never the live production database, same convention
test_resume_interrupted_workers_bounded_retry.py already established.
resource_governor.submit() itself is monkey-patched to a deterministic fake
so this test exercises dispatch-tick.py's own batch-cap bookkeeping in
isolation.
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
    spec = importlib.util.spec_from_file_location("sbr_seed_resume_batch_cap", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    sbr._ensure_resume_dead_letter_table(conn)
    conn.close()


def _load_dispatch_tick(env):
    # Same sys.modules-cache-busting rationale as
    # test_resume_interrupted_workers_no_duplicate_row.py's _load_dispatch_tick().
    sys.modules.pop("resource_governor", None)
    sys.modules.pop("dispatch_core", None)
    old_env = {}
    for k, v in env.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location("dispatch_tick_resume_batch_cap", os.path.join(SCRIPTS_DIR, "dispatch-tick.py"))
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


class _AcceptingSubmit:
    """Deterministic stand-in for resource_governor.submit() that always
    accepts -- what's under test is whether dispatch-tick.py even CALLS this
    more than RESUME_SUBMIT_BATCH_LIMIT times in one tick, not submit()'s own
    row-writing/dedup behavior (covered elsewhere)."""

    def __init__(self):
        self.call_count = 0
        self.seen_identities = []

    def __call__(self, task_spec, tier, source_trigger):
        self.call_count += 1
        self.seen_identities.append(task_spec["task_identity"])
        return {"accepted": True, "umr_id": f"UMR-fake-{self.call_count}", "reason": None}


def test_batch_cap_bounds_real_submit_calls_in_one_tick():
    """The real regression: a tick that finds 25 real resumable task.yaml
    entries must call resource_governor.submit() at most
    RESUME_SUBMIT_BATCH_LIMIT times, and report the rest in
    skipped_batch_cap -- never skipped_dead, never skipped_duplicate (those
    are real distinct outcomes with their own accounting)."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR,
               "VERIDIAN_RESUME_SUBMIT_BATCH_LIMIT": "10"}
        dispatch_tick = _load_dispatch_tick(env)
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            n_tasks = 25
            states = {f"veridian-worker@batch-cap-task-{i:02d}.service": "inactive" for i in range(n_tasks)}
            real_run = dispatch_tick.run
            dispatch_tick.run = _fake_run_factory(real_run, states)

            real_submit = dispatch_tick.resource_governor.submit
            fake_submit = _AcceptingSubmit()
            dispatch_tick.resource_governor.submit = fake_submit

            tasks = {
                f"batch-cap-task-{i:02d}": {
                    "status": "in_progress",
                    "service": f"veridian-worker@batch-cap-task-{i:02d}.service",
                }
                for i in range(n_tasks)
            }

            try:
                result = dispatch_tick.resume_interrupted_workers_tick(tasks)
            finally:
                dispatch_tick.run = real_run
                dispatch_tick.resource_governor.submit = real_submit
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert dispatch_tick.RESUME_SUBMIT_BATCH_LIMIT == 10, dispatch_tick.RESUME_SUBMIT_BATCH_LIMIT
        assert fake_submit.call_count == 10, (
            f"UMR-20260815-105911-a2c9 regression: expected exactly "
            f"RESUME_SUBMIT_BATCH_LIMIT=10 real submit() calls for {n_tasks} real "
            f"candidates in one tick, got {fake_submit.call_count}"
        )
        assert len(result["resumed"]) == 10, result["resumed"]
        assert len(result["skipped_batch_cap"]) == n_tasks - 10, result["skipped_batch_cap"]
        assert result["skipped_dead"] == [], result["skipped_dead"]
        assert result["skipped_duplicate"] == [], result["skipped_duplicate"]
        # Every batch-capped task_identity must be a real candidate that was
        # never actually submitted this tick.
        for tid in result["skipped_batch_cap"]:
            assert tid not in fake_submit.seen_identities, (tid, fake_submit.seen_identities)
        print(f"PASS: test_batch_cap_bounds_real_submit_calls_in_one_tick -> "
              f"{n_tasks} real candidates, {fake_submit.call_count} real submit() calls "
              f"(cap=10), {len(result['skipped_batch_cap'])} left for a later tick")


def test_batch_cap_does_not_affect_a_tick_under_the_limit():
    """Purely additive: a tick whose real candidate count is already under
    RESUME_SUBMIT_BATCH_LIMIT must behave exactly as before -- every real
    candidate submitted, nothing in skipped_batch_cap."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR,
               "VERIDIAN_RESUME_SUBMIT_BATCH_LIMIT": "10"}
        dispatch_tick = _load_dispatch_tick(env)
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            n_tasks = 3
            states = {f"veridian-worker@under-cap-task-{i:02d}.service": "inactive" for i in range(n_tasks)}
            real_run = dispatch_tick.run
            dispatch_tick.run = _fake_run_factory(real_run, states)

            real_submit = dispatch_tick.resource_governor.submit
            fake_submit = _AcceptingSubmit()
            dispatch_tick.resource_governor.submit = fake_submit

            tasks = {
                f"under-cap-task-{i:02d}": {
                    "status": "in_progress",
                    "service": f"veridian-worker@under-cap-task-{i:02d}.service",
                }
                for i in range(n_tasks)
            }

            try:
                result = dispatch_tick.resume_interrupted_workers_tick(tasks)
            finally:
                dispatch_tick.run = real_run
                dispatch_tick.resource_governor.submit = real_submit
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        assert fake_submit.call_count == n_tasks, fake_submit.call_count
        assert len(result["resumed"]) == n_tasks, result["resumed"]
        assert result["skipped_batch_cap"] == [], result["skipped_batch_cap"]
        print(f"PASS: test_batch_cap_does_not_affect_a_tick_under_the_limit -> "
              f"{n_tasks} real candidates, all {fake_submit.call_count} submitted, "
              f"skipped_batch_cap empty")


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
