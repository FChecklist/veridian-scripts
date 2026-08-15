#!/usr/bin/env python3
"""Real regression test for task-20260815-154633 (real starvation incident,
2026-08-15): a real Owner-directed fix sat queued for 1+ real hour despite
real CPU load dropping to trivially low levels and 15+ real worker units
being manually freed.

Root cause, confirmed live against the production superboss-register.sqlite
by this task: dispatch-tick.py's main() calls resume_interrupted_workers_
tick() before module_queue_tick() (see main()'s own real call order), and
both draw on the exact same shared, fixed dispatch_core.CONCURRENCY_CAP
real-slot pool -- resume indirectly (every accepted resource_governor.
submit() row is later dispatched by resource_governor.dispatch_one()'s own
tick, at tier=1, the highest real priority), module_queue_tick() directly
(has_free_slot_with_stale_swap_override() before every real
dispatch_module_item() call). Live evidence at the time this task started:
17 active umr_tasks rows under source_trigger='dispatch-tick:
resume_interrupted_workers' (14 queued + 3 running) against
dispatch_core.CONCURRENCY_CAP=5, real running_worker_count()==5 -- fully
saturated. A resume backlog at or above CONCURRENCY_CAP is a DETERMINISTIC
starvation of module_queue_tick(), not an occasional one: every real slot
resource_governor.dispatch_one() frees gets immediately reclaimed by the
next tier=1 resume row, for as long as the backlog stays >= cap.

The fix: resume_interrupted_workers_tick() now caps its own real concurrent
consumption (queued+dispatched+running umr_tasks rows under
RESUME_SOURCE_TRIGGER) at one less than dispatch_core.CONCURRENCY_CAP, via
_count_active_resume_umr() + a reserved_max_active check inside the loop.
Resume's own real interrupted-task recovery guarantee is unchanged -- every
candidate is still found and still eligible every tick; only how many may
be simultaneously ACTIVE at once is now bounded below the real fixed cap,
guaranteeing module_queue_tick() (or anything else that shares
dispatch_core.has_free_slot()) at least one real slot.

This file proves two things with real code, against a real, isolated,
temp-file SQLite database (never the live production database) and real
resource_governor.submit()/dispatch_core.has_free_slot_detail() logic
(never reimplemented or stubbed out):

1. test_large_resume_backlog_leaves_a_real_slot_for_module_queue_dispatch --
   a simulated backlog at/above CONCURRENCY_CAP never lets resume claim more
   than CONCURRENCY_CAP-1 real active slots, across repeated ticks (i.e. the
   reservation holds indefinitely, not just once) -- and with real
   running_worker_count() mocked to reflect exactly that many real running
   workers (simulating resource_governor.dispatch_one() having turned the
   reserved-under-cap resume rows into real running units), a real fresh
   module-queue item, dispatched through the real module_queue_tick() code
   path (real dispatch_core.has_free_slot_detail() gate, real
   dispatch_module_item(), real queue-file read/write), still gets a real
   slot -- not starved.

2. test_small_resume_backlog_behavior_is_completely_unchanged -- a
   regression test: when the resume backlog is below the reserved limit
   (the common, pre-incident case), the reservation check never trips --
   every real candidate is resumed exactly as it was before this fix, zero
   skipped_reserved_capacity entries.
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile

import yaml

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def _seed_scratch_db(path):
    spec = importlib.util.spec_from_file_location(
        "sbr_seed_resume_reserved_capacity", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    sbr._ensure_resume_dead_letter_table(conn)
    conn.close()
    return sbr


def _load_dispatch_tick(env):
    # Same sys.modules-cache-busting rationale as every other
    # tests/test_resume_interrupted_workers_*.py in this suite: dispatch-
    # tick.py's plain top-level `import dispatch_core` / `import
    # resource_governor` always resolve through the process-wide
    # sys.modules cache otherwise, silently reusing a previous test's
    # module still bound to that test's now-deleted scratch DB / cap.
    sys.modules.pop("resource_governor", None)
    sys.modules.pop("dispatch_core", None)
    old_env = {}
    for k, v in env.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location(
            "dispatch_tick_resume_reserved_capacity", os.path.join(SCRIPTS_DIR, "dispatch-tick.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _fake_run_factory(real_run, unit_states=None, task_manager_stdout=None):
    unit_states = unit_states or {}

    def fake_run(cmd, **kw):
        if cmd[:3] == ["systemctl", "--user", "show"]:
            unit = cmd[3]

            class _R:
                pass
            r = _R()
            r.stdout = unit_states.get(unit, "inactive")
            r.stderr = ""
            r.returncode = 0
            return r
        if task_manager_stdout is not None and "create" in cmd and "--repo" in cmd:
            class _R:
                pass
            r = _R()
            r.stdout = task_manager_stdout
            r.stderr = ""
            r.returncode = 0
            return r
        return real_run(cmd, **kw)
    return fake_run


CAP = 5
RESERVED_MAX_ACTIVE = CAP - 1  # the real number this fix guarantees resume never exceeds


def test_large_resume_backlog_leaves_a_real_slot_for_module_queue_dispatch(tmp_path, monkeypatch):
    scratch_db = os.path.join(str(tmp_path), "scratch.sqlite")
    _seed_scratch_db(scratch_db)
    module_queues_dir = os.path.join(str(tmp_path), "queues")
    os.makedirs(module_queues_dir, exist_ok=True)

    env = {
        "SUPERBOSS_REGISTER_DB": scratch_db,
        "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR,
        "VERIDIAN_DISPATCH_CONCURRENCY_CAP": str(CAP),
        "VERIDIAN_MODULE_QUEUES_DIR": module_queues_dir,
        "VERIDIAN_MODULE_QUEUES_LOCK": os.path.join(str(tmp_path), ".module_queues.lock"),
    }
    dispatch_tick = _load_dispatch_tick(env)
    os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
    try:
        assert dispatch_tick.dispatch_core.CONCURRENCY_CAP == CAP  # real cap this test's math depends on

        # A real, large simulated resume backlog: 3x CAP distinct interrupted
        # task_identities, every one genuinely eligible (in_progress, unit
        # inactive -- the real "was mid-work, unit not auto-restarted" shape).
        backlog_size = CAP * 3
        task_ids = [f"resume-starve-task-{i}" for i in range(backlog_size)]
        tasks = {
            tid: {"status": "in_progress", "service": f"veridian-worker@{tid}.service"}
            for tid in task_ids
        }
        unit_states = {f"veridian-worker@{tid}.service": "inactive" for tid in task_ids}

        real_run = dispatch_tick.run
        dispatch_tick.run = _fake_run_factory(real_run, unit_states=unit_states)
        try:
            tick_results = [dispatch_tick.resume_interrupted_workers_tick(tasks) for _ in range(3)]
        finally:
            dispatch_tick.run = real_run

        sbr_spec = importlib.util.spec_from_file_location(
            "sbr_check_resume_reserved_capacity", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
        sbr = importlib.util.module_from_spec(sbr_spec)
        sbr_spec.loader.exec_module(sbr)
        conn = sbr._connect()
        try:
            placeholders = ",".join("?" * len(sbr.UMR_ACTIVE_STATUSES))
            active_count = conn.execute(
                f"SELECT COUNT(*) AS n FROM umr_tasks WHERE source_trigger=? AND status IN ({placeholders})",
                (dispatch_tick.RESUME_SOURCE_TRIGGER, *sbr.UMR_ACTIVE_STATUSES),
            ).fetchone()["n"]
        finally:
            conn.close()

        # The real, central assertion: however large the backlog, and across
        # repeated ticks (the reservation is not a one-shot check), resume
        # NEVER accumulates more than RESERVED_MAX_ACTIVE real active rows.
        assert active_count == RESERVED_MAX_ACTIVE, (
            f"starvation regression: expected resume to hold exactly {RESERVED_MAX_ACTIVE} "
            f"(CONCURRENCY_CAP-1) real active umr_tasks rows after repeated ticks against a "
            f"{backlog_size}-task backlog, got {active_count} -- a value >= CAP={CAP} means "
            f"resume can once again claim every real slot and starve module_queue_tick()"
        )
        total_resumed = sum(len(r["resumed"]) for r in tick_results)
        assert total_resumed == RESERVED_MAX_ACTIVE, (tick_results, total_resumed)
        # Every tick, the RESERVED_MAX_ACTIVE already-active candidates are
        # reported skipped_duplicate (already queued, a separate real check
        # that runs before the reservation check), never skipped_reserved_
        # capacity -- only the remaining (backlog_size - RESERVED_MAX_ACTIVE)
        # genuinely-new-this-tick candidates hit the reservation skip, and
        # they hit it on every one of the 3 ticks (the backlog never shrinks
        # in this test, same as the real incident's real never-draining
        # backlog).
        total_reserved_skips = sum(len(r["skipped_reserved_capacity"]) for r in tick_results)
        assert total_reserved_skips == 3 * (backlog_size - RESERVED_MAX_ACTIVE), (
            "every candidate beyond the reservation limit must be reported as "
            f"skipped_reserved_capacity, not silently dropped: {tick_results}"
        )

        # Now prove the real payoff: with dispatch_core.running_worker_count()
        # mocked to reflect EXACTLY what real active_count implies (i.e.
        # resource_governor.dispatch_one() has, at worst, turned every one of
        # resume's reserved-under-cap rows into a real running worker -- the
        # most pessimistic real case for a fresh dispatch), a real fresh
        # module-queue item must still get a real slot via the REAL,
        # unmocked module_queue_tick() code path (real has_free_slot_detail()
        # cap_exhausted gate, real dispatch_module_item(), real queue YAML
        # read/write). has_resource_headroom_detail() -- the SECOND, fully
        # independent real memory/swap/load veto has_free_slot_detail() also
        # applies -- is pinned to "ok" here: this test's own real host
        # /proc state is not what task-20260815-154633's fix is about (that
        # veto exists and is exercised for real elsewhere, e.g.
        # tests/test_dispatch_tick_stale_swap_override.py); isolating it here
        # keeps this test's pass/fail tied only to the real cap-reservation
        # math under test, not to this sandbox's incidental real memory
        # pressure at run time.
        monkeypatch.setattr(dispatch_tick.dispatch_core, "running_worker_count", lambda: active_count)
        monkeypatch.setattr(dispatch_tick.dispatch_core, "has_resource_headroom_detail",
                             lambda: (True, {"check": "ok"}))

        module_yaml_path = os.path.join(module_queues_dir, "mod-a.yaml")
        with open(module_yaml_path, "w") as f:
            yaml.safe_dump({
                "module": "mod-a",
                "queue": [{
                    "id": "mod-a-item-1",
                    "module": "mod-a",
                    "objective": "real fresh module-queue item waiting behind a real large resume backlog",
                    "status": "NEW",
                    "dependencies": [],
                    "files_allowed": ["mod-a/**"],
                }],
            }, f)

        real_run2 = dispatch_tick.run
        dispatch_tick.run = _fake_run_factory(
            real_run2, task_manager_stdout="CREATED: fake-module-dispatched-task-1\n")
        try:
            module_result = dispatch_tick.module_queue_tick({})
        finally:
            dispatch_tick.run = real_run2

        assert module_result["dispatched"] == ["fake-module-dispatched-task-1"], (
            f"starvation regression: a real fresh module-queue item behind a "
            f"{backlog_size}-task resume backlog (>= CAP={CAP}) must still get a real "
            f"dispatch slot because resume is capped at {RESERVED_MAX_ACTIVE} real active "
            f"slots -- got {module_result}"
        )
        with open(module_yaml_path) as f:
            saved = yaml.safe_load(f)
        assert saved["queue"][0]["status"] == "RUNNING"
        assert saved["queue"][0]["task_id"] == "fake-module-dispatched-task-1"

        print(
            "PASS: test_large_resume_backlog_leaves_a_real_slot_for_module_queue_dispatch -> "
            f"backlog={backlog_size} tasks, CAP={CAP}, resume held steady at "
            f"{active_count} active (== CAP-1) across 3 ticks, fresh module item still "
            f"dispatched (not starved)"
        )
    finally:
        del os.environ["SUPERBOSS_REGISTER_DB"]


def test_small_resume_backlog_behavior_is_completely_unchanged(tmp_path):
    """Regression: a backlog well below the reserved limit must see zero
    behavior change from this fix -- every real candidate still gets
    resumed, exactly as before, with skipped_reserved_capacity always
    empty."""
    scratch_db = os.path.join(str(tmp_path), "scratch.sqlite")
    _seed_scratch_db(scratch_db)
    env = {
        "SUPERBOSS_REGISTER_DB": scratch_db,
        "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR,
        "VERIDIAN_DISPATCH_CONCURRENCY_CAP": str(CAP),
    }
    dispatch_tick = _load_dispatch_tick(env)
    os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
    try:
        assert dispatch_tick.dispatch_core.CONCURRENCY_CAP == CAP

        # Well below RESERVED_MAX_ACTIVE (CAP-1 == 4): 2 real interrupted tasks.
        task_ids = ["resume-small-task-1", "resume-small-task-2"]
        tasks = {
            tid: {"status": "in_progress", "service": f"veridian-worker@{tid}.service"}
            for tid in task_ids
        }
        unit_states = {f"veridian-worker@{tid}.service": "inactive" for tid in task_ids}

        real_run = dispatch_tick.run
        dispatch_tick.run = _fake_run_factory(real_run, unit_states=unit_states)
        try:
            first = dispatch_tick.resume_interrupted_workers_tick(tasks)
        finally:
            dispatch_tick.run = real_run

        assert sorted(first["resumed"]) == task_ids, (
            f"regression: a small backlog (2 tasks, well under CAP-1={RESERVED_MAX_ACTIVE}) "
            f"must have every real candidate resumed exactly as before this fix, got {first}"
        )
        assert first["skipped_reserved_capacity"] == [], (
            f"regression: the reservation check must never trip for a backlog below the "
            f"reserved limit -- got {first}"
        )
        print(
            "PASS: test_small_resume_backlog_behavior_is_completely_unchanged -> "
            f"2-task backlog under CAP-1={RESERVED_MAX_ACTIVE}, both resumed, "
            f"zero reserved-capacity skips"
        )
    finally:
        del os.environ["SUPERBOSS_REGISTER_DB"]


if __name__ == "__main__":
    import inspect

    class _FakeTmpPath:
        def __init__(self, d):
            self._d = d

        def __str__(self):
            return self._d

    class _FakeMonkeypatch:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, old in reversed(self._undo):
                setattr(obj, name, old)

    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        with tempfile.TemporaryDirectory() as d:
            kwargs = {}
            sig = inspect.signature(t)
            mp = None
            if "tmp_path" in sig.parameters:
                kwargs["tmp_path"] = _FakeTmpPath(d)
            if "monkeypatch" in sig.parameters:
                mp = _FakeMonkeypatch()
                kwargs["monkeypatch"] = mp
            try:
                t(**kwargs)
            except AssertionError as e:
                failed += 1
                print(f"FAIL: {t.__name__} -> {e}")
            except Exception as e:
                failed += 1
                print(f"ERROR: {t.__name__} -> {type(e).__name__}: {e}")
            finally:
                if mp is not None:
                    mp.undo()
    if failed:
        sys.exit(1)
