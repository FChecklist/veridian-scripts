#!/usr/bin/env python3
"""Real regression test for UMR-20260813-235702: dispatch-tick.py's
resume_interrupted_workers_tick() must stop resubmitting a task_identity
once it has been rejected MAX_CONSECUTIVE_RESUME_REJECTIONS times in a row.

Root cause this fixes: the earlier UMR-20260806-103711-bf00 fix
(_existing_active_umr(), see test_resume_interrupted_workers_no_duplicate_row.py)
only short-circuits resource_governor.submit() when a live ACTIVE
(queued/dispatched/running) umr_tasks row already covers the same
task_identity. It never touches the OTHER real rejection path -- submit()'s
own reuse_verdict_engine.assess() content-similarity check, which can reject
a resubmission as duplication_blocked against a wholly unrelated
already-registered entity. A task_identity stuck in THAT state was still
resubmitted, and rejected, forever, once per tick -- confirmed live via the
production superboss-register.sqlite: 10 real task identities (dated
2026-07-18 and 2026-08-07) each carrying 40 consecutive rejected_duplicate
rows from source_trigger='dispatch-tick:resume_interrupted_workers', still
growing every ~10-minute tick as of 2026-08-13T23:52Z.

The fix: superboss-register.py's new resume_dead_letter table + is_resume_dead()/
record_resume_outcome() functions track a CONSECUTIVE rejection streak per
task_identity, independent of WHY resource_governor.submit() rejected it.
Once the streak reaches dispatch-tick.py's own named
MAX_CONSECUTIVE_RESUME_REJECTIONS constant, the identity is marked
permanently dead and _is_permanently_dead_resume() skips it BEFORE
resource_governor.submit() is ever called again -- no new umr_tasks row, no
similarity scan.

Every test here uses a real, isolated, temp-file SQLite database seeded with
the real schema -- never the live production database. resource_governor.submit()
itself is monkey-patched to a deterministic fake (rejecting every call) so
this test exercises dispatch-tick.py's own bounded-retry bookkeeping in
isolation from reuse_verdict_engine's real (expensive, non-deterministic)
similarity scoring -- that scoring is real production code exercised
elsewhere; what's under test here is "N consecutive rejections -> stop
calling submit() on attempt N+1", regardless of the rejection's real cause.
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
    spec = importlib.util.spec_from_file_location("sbr_seed_resume_bounded", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
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
        spec = importlib.util.spec_from_file_location("dispatch_tick_resume_bounded", os.path.join(SCRIPTS_DIR, "dispatch-tick.py"))
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


class _RejectingSubmit:
    """Deterministic stand-in for resource_governor.submit() that always
    rejects, simulating a task_identity permanently stuck against
    reuse_verdict_engine's content-similarity gate -- never writes a real
    umr_tasks row, since what's under test is whether dispatch-tick.py even
    CALLS this again after the bounded-retry cap, not submit()'s own
    row-writing behavior (that's covered elsewhere, e.g.
    test_resource_governor_queue_management.py)."""

    def __init__(self):
        self.call_count = 0

    def __call__(self, task_spec, tier, source_trigger):
        self.call_count += 1
        return {
            "accepted": False,
            "reason": "reuse_verdict_engine.assess() real verdict=duplication_blocked "
                      "(fake, deterministic test double)",
            "umr_id": None,
        }


def test_nth_consecutive_rejection_marks_dead_and_attempt_n_plus_1_skips_submit():
    """The real regression: a task_identity rejected
    MAX_CONSECUTIVE_RESUME_REJECTIONS times in a row must not have submit()
    called on it again on attempt N+1 -- it must be reported as
    skipped_dead instead, with zero additional resource_governor.submit()
    calls."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        dispatch_tick = _load_dispatch_tick(env)
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            real_run = dispatch_tick.run
            dispatch_tick.run = _fake_run_factory(
                real_run, {"veridian-worker@bounded-retry-task-1.service": "inactive"})

            real_submit = dispatch_tick.resource_governor.submit
            fake_submit = _RejectingSubmit()
            dispatch_tick.resource_governor.submit = fake_submit

            tasks = {
                "bounded-retry-task-1": {
                    "status": "in_progress",
                    "service": "veridian-worker@bounded-retry-task-1.service",
                },
            }

            cap = dispatch_tick.MAX_CONSECUTIVE_RESUME_REJECTIONS
            results = []
            try:
                for _ in range(cap):
                    results.append(dispatch_tick.resume_interrupted_workers_tick(tasks))
                # attempt N+1 -- this is the real assertion
                n_plus_1 = dispatch_tick.resume_interrupted_workers_tick(tasks)
            finally:
                dispatch_tick.run = real_run
                dispatch_tick.resource_governor.submit = real_submit
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        for i, r in enumerate(results, start=1):
            assert "bounded-retry-task-1" in r["skipped_duplicate"], (i, r)
        assert fake_submit.call_count == cap, (
            f"expected exactly {cap} real submit() calls across the first {cap} ticks, "
            f"got {fake_submit.call_count}"
        )

        assert "bounded-retry-task-1" in n_plus_1.get("skipped_dead", []), (
            f"UMR-20260813-235702 regression: attempt {cap + 1} must report the task as "
            f"skipped_dead (permanently dead), got {n_plus_1}"
        )
        assert "bounded-retry-task-1" not in n_plus_1["skipped_duplicate"], n_plus_1
        assert fake_submit.call_count == cap, (
            f"UMR-20260813-235702 regression: attempt {cap + 1} must NOT call "
            f"resource_governor.submit() again once the identity is permanently dead -- "
            f"expected call_count to stay at {cap}, got {fake_submit.call_count}"
        )
        print(f"PASS: test_nth_consecutive_rejection_marks_dead_and_attempt_n_plus_1_skips_submit -> "
              f"{cap} consecutive rejections, attempt {cap + 1} skipped_dead with zero new submit() calls")


def test_accepted_resume_clears_prior_rejection_streak():
    """A task_identity that racked up rejections but then genuinely gets
    ACCEPTED (real forward progress) must have its streak cleared -- it must
    not be marked dead by a later run of stale rejections that happened
    before the acceptance."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        dispatch_tick = _load_dispatch_tick(env)
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            real_run = dispatch_tick.run
            dispatch_tick.run = _fake_run_factory(
                real_run, {"veridian-worker@bounded-retry-task-2.service": "inactive"})

            real_submit = dispatch_tick.resource_governor.submit
            cap = dispatch_tick.MAX_CONSECUTIVE_RESUME_REJECTIONS

            class _FlakySubmit:
                def __init__(self):
                    self.call_count = 0

                def __call__(self, task_spec, tier, source_trigger):
                    self.call_count += 1
                    # Reject every call except the last one before the cap would
                    # be reached -- real forward progress interrupts the streak.
                    if self.call_count == cap - 1:
                        return {"accepted": True, "umr_id": f"UMR-fake-{self.call_count}", "reason": None}
                    return {"accepted": False, "reason": "fake rejection", "umr_id": None}

            fake_submit = _FlakySubmit()
            dispatch_tick.resource_governor.submit = fake_submit

            tasks = {
                "bounded-retry-task-2": {
                    "status": "in_progress",
                    "service": "veridian-worker@bounded-retry-task-2.service",
                },
            }

            results = []
            try:
                # cap-1 rejections, then 1 acceptance, then re-run enough more
                # rejections to prove the streak restarted from zero rather
                # than resuming from where it left off.
                for _ in range(cap):
                    results.append(dispatch_tick.resume_interrupted_workers_tick(tasks))
            finally:
                dispatch_tick.run = real_run
                dispatch_tick.resource_governor.submit = real_submit
        finally:
            del os.environ["SUPERBOSS_REGISTER_DB"]

        accepted_ticks = [i for i, r in enumerate(results) if "bounded-retry-task-2" in r["resumed"]]
        assert accepted_ticks == [cap - 2], (
            f"expected exactly one real accepted resume at tick index {cap - 2}, got {accepted_ticks}"
        )
        last = results[-1]
        assert "bounded-retry-task-2" not in last.get("skipped_dead", []), (
            f"UMR-20260813-235702 regression: a genuine accepted resume must clear the prior "
            f"rejection streak, not leave it primed to falsely mark the identity dead -- got {last}"
        )
        print("PASS: test_accepted_resume_clears_prior_rejection_streak -> "
              f"acceptance at tick {cap - 1} correctly reset the streak, final tick not marked dead")


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
