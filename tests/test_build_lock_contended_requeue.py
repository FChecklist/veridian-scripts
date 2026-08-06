#!/usr/bin/env python3
"""Real regression/proof test for UMR-20260806-123316-cf9f (proposal 62,
child UMR-20260806-121247-a93a): quality-gate.sh's build step now gives up
the host-wide build lock after a short wait instead of blocking in-process,
and requeues its own umr_tasks row via the new
`superboss-register.py requeue-build-lock-contended` CLI command.

Precondition 2 of that UMR requires a REAL test proving a row requeued
through this new path is genuinely re-dispatched by the normal dispatcher
within a bounded window -- not another permanently-queued row of the class
already identified in proposals 50-53 (260-274 duplicate rows each) and not
a duplicate-row explosion. This file exercises the REAL code, not a mock of
it:

  1. The real CLI entry point (`python3 superboss-register.py
     requeue-build-lock-contended ...`, via subprocess -- the exact command
     quality-gate.sh itself runs) against a real, isolated, temp-file SQLite
     database seeded with the real schema -- never the live production
     database (same convention as tests/test_resume_interrupted_workers_no_duplicate_row.py).
  2. The real resource_governor.next_queued_task() / dispatch_one() functions
     -- the actual functions dispatch-tick.py's run_tick() calls every real
     tick -- picking the requeued row back up and spawning it. Only the two
     genuinely orthogonal process boundaries are stubbed (real subprocess
     `systemctl` calls, and the real host's live memory/load headroom check,
     which is unrelated to this fix's own correctness and would otherwise
     make this test's outcome depend on this box's current real load) --
     same "fake the process boundary, keep everything else real" technique
     tests/test_resume_interrupted_workers_no_duplicate_row.py already
     established for `dispatch_tick.run`.
"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def _seed_scratch_db(path):
    # NOTE: resolve_superboss_db_path() (superboss-register.py's single
    # canonical DB_PATH chokepoint) only ever accepts SUPERBOSS_REGISTER_DB
    # as the real candidate if that path ALREADY exists, is non-zero size,
    # AND already contains a real umr_tasks table -- any candidate that
    # fails those checks makes it fall through to the real, live PRODUCTION
    # default path instead (by design: this scratch-DB env var is a real
    # testability seam only, see that function's own docstring). A bare
    # tempfile.TemporaryDirectory() path does not exist yet the first time,
    # so bootstrapping it MUST NOT go through the CLI/env-var path at all
    # (confirmed the hard way while writing this test: an `init` subprocess
    # call with SUPERBOSS_REGISTER_DB pointed at a not-yet-existing scratch
    # file silently fell back and re-ran the real init_db() -- itself a
    # harmless idempotent CREATE TABLE IF NOT EXISTS, no data touched -- but
    # a real touch of the live production DB, against every real test-safety
    # convention this file otherwise follows). Bootstrap directly instead:
    # set sbr.DB_PATH on the already-imported module object (module globals
    # are looked up by name at call time, so this is real, not cosmetic) and
    # call the real sbr.init_db() against exactly that path -- never through
    # resolve_superboss_db_path()'s own env-var fallback logic.
    spec = importlib.util.spec_from_file_location("sbr_seed_lockreq", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    sbr.DB_PATH = path
    sbr.init_db()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    conn.close()
    return sbr


def _load_fresh(modname, filename, env):
    """Same technique as test_resume_interrupted_workers_no_duplicate_row.py's
    _load_dispatch_tick(): pop any cached module first so a plain top-level
    `import` inside the loaded file re-resolves against THIS call's env
    rather than a previous test's now-deleted scratch DB."""
    for m in ("resource_governor", "dispatch_core"):
        sys.modules.pop(m, None)
    old_env = {}
    for k, v in env.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location(modname, os.path.join(SCRIPTS_DIR, filename))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _row_count(scratch_db, task_identity):
    conn = sqlite3.connect(scratch_db)
    try:
        return conn.execute(
            "SELECT count(*) FROM umr_tasks WHERE task_identity=?", (task_identity,)
        ).fetchone()[0]
    finally:
        conn.close()


def test_requeue_cli_resets_same_row_no_duplicate():
    """The real CLI command, called exactly as quality-gate.sh calls it: same
    row flips to status='queued'/reason='build_lock_contended', task_kind is
    forced to 'systemctl_action' with inputs.action='start' regardless of
    its original task_kind, and the row COUNT never changes (no fresh
    row minted)."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _seed_scratch_db(scratch_db)
        task_identity = "build-lock-test-task-1"
        unit_name = f"veridian-worker@{task_identity}.service"

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        try:
            sbr.upsert_umr_task(conn, {
                "umr_id": "UMR-TEST-0001",
                "task_identity": task_identity,
                "tier": 2,
                "status": "running",
                "source_trigger": "test-seed",
                "task_kind": "veridian_task_create",
                "unit_name": unit_name,
                "inputs": {"title": "t", "prompt": "p", "repo": "claude-control"},
            })
            conn.commit()
        finally:
            conn.close()

        env = dict(os.environ, SUPERBOSS_REGISTER_DB=scratch_db)
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "superboss-register.py"),
             "requeue-build-lock-contended", "--task-identity", task_identity,
             "--unit-name", unit_name],
            env=env, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"CLI call failed: stdout={result.stdout!r} stderr={result.stderr!r}"
        printed = json.loads(result.stdout)
        assert printed["status"] == "queued"
        assert printed["reason"] == "build_lock_contended"

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM umr_tasks WHERE task_identity=?", (task_identity,)
            ).fetchone()
        finally:
            conn.close()
        assert row["status"] == "queued"
        assert row["reason"] == "build_lock_contended"
        assert row["task_kind"] == "systemctl_action"
        assert row["ts_dispatched"] is None
        inputs = json.loads(row["inputs_json"])
        assert inputs["action"] == "start"
        assert _row_count(scratch_db, task_identity) == 1, (
            "requeue-build-lock-contended must reset the EXISTING row, never mint "
            "a fresh one -- this is the exact duplicate-row-explosion failure mode "
            "proposals 50-53 already found (260-274 duplicate rows each)"
        )
        print("PASS: test_requeue_cli_resets_same_row_no_duplicate")


def test_requeue_then_real_dispatcher_picks_it_up_bounded_no_duplicates():
    """Precondition 2, satisfied against the REAL dispatch path: after the
    real CLI requeue, resource_governor.dispatch_one() -- the actual
    function dispatch-tick.py's run_tick() calls every real tick -- must
    genuinely pick this exact row back up (queued -> dispatched/running) and
    call the real spawn logic (systemctl start on the real unit_name),
    inside ONE synchronous call (a real bounded window, not a hang / not
    left permanently queued), with the row count still exactly 1 throughout
    -- never a duplicate."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        task_identity = "build-lock-test-task-2"
        unit_name = f"veridian-worker@{task_identity}.service"

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        try:
            spec = importlib.util.spec_from_file_location("sbr_seed_lockreq2", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
            sbr = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sbr)
            sbr.upsert_umr_task(conn, {
                "umr_id": "UMR-TEST-0002",
                "task_identity": task_identity,
                "tier": 2,
                "status": "running",
                "source_trigger": "test-seed",
                "task_kind": "veridian_task_create",
                "unit_name": unit_name,
                "inputs": {"title": "t", "prompt": "p", "repo": "claude-control"},
            })
            conn.commit()
        finally:
            conn.close()

        env = dict(os.environ, SUPERBOSS_REGISTER_DB=scratch_db)
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "superboss-register.py"),
             "requeue-build-lock-contended", "--task-identity", task_identity,
             "--unit-name", unit_name],
            env=env, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"CLI call failed: stdout={result.stdout!r} stderr={result.stderr!r}"
        assert _row_count(scratch_db, task_identity) == 1

        rg_env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        resource_governor = _load_fresh("resource_governor_lockreq_test", "resource_governor.py", rg_env)

        # _superboss_register()/_dispatch_core() inside resource_governor.py
        # are lazily loaded on first real use (same importlib-by-file-path
        # pattern as dispatch_tick.py -- see that module's own docstring),
        # which happens INSIDE dispatch_one() below, i.e. after _load_fresh's
        # own env restore already ran. SUPERBOSS_REGISTER_DB must still be
        # set in the real environment at that point or the lazy loader would
        # resolve DB_PATH against the live production database instead of
        # this test's scratch one -- same reason
        # test_resume_interrupted_workers_no_duplicate_row.py keeps it set
        # for its whole test body, not just around its own _load_* call.
        old_sbr_db = os.environ.get("SUPERBOSS_REGISTER_DB")
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            dispatch_core = resource_governor._dispatch_core()

            real_run = resource_governor._run
            real_dc_run = dispatch_core._run
            started_units = []

            def fake_rg_run(cmd, **kw):
                if cmd[:3] == ["systemctl", "--user", "start"]:
                    started_units.append(cmd[3])

                    class _R:
                        returncode = 0
                        stdout = ""
                        stderr = ""
                    return _R()
                return real_run(cmd, **kw)

            def fake_dc_run(cmd, **kw):
                if cmd[:3] == ["systemctl", "--user", "list-units"]:
                    class _R:
                        returncode = 0
                        stdout = ""
                        stderr = ""
                    return _R()
                return real_dc_run(cmd, **kw)

            resource_governor._run = fake_rg_run
            dispatch_core._run = fake_dc_run
            # Orthogonal to this fix: stub the live-host resource veto so
            # this test's outcome depends on the real requeue+dispatch
            # logic, not on this box's current real memory/load state at the
            # moment the test happens to run.
            dispatch_core.has_resource_headroom_detail = lambda: (True, {"check": "ok"})

            try:
                t0 = time.monotonic()
                result = resource_governor.dispatch_one()
                elapsed = time.monotonic() - t0
            finally:
                resource_governor._run = real_run
                dispatch_core._run = real_dc_run
        finally:
            if old_sbr_db is None:
                os.environ.pop("SUPERBOSS_REGISTER_DB", None)
            else:
                os.environ["SUPERBOSS_REGISTER_DB"] = old_sbr_db

        assert elapsed < 30, f"real dispatcher pickup took {elapsed}s -- not a bounded window"
        assert result["action"] == "dispatched", result
        assert result["umr_id"] == "UMR-TEST-0002", result
        assert started_units == [unit_name], (
            f"expected the real dispatcher to call the real spawn path "
            f"(systemctl --user start {unit_name}), got {started_units}"
        )
        assert result["result"]["status"] == "running", result

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM umr_tasks WHERE task_identity=?", (task_identity,)
            ).fetchone()
        finally:
            conn.close()
        assert row["status"] == "running", (
            f"requeued row was not genuinely re-dispatched -- still {row['status']!r}, "
            f"this is exactly the 'permanently-queued row' failure mode Precondition 2 "
            f"guards against"
        )
        assert row["ts_dispatched"] is not None
        assert _row_count(scratch_db, task_identity) == 1, (
            f"real dispatch pickup must not create a duplicate row -- "
            f"got {_row_count(scratch_db, task_identity)} rows for {task_identity!r}"
        )
        print(f"PASS: test_requeue_then_real_dispatcher_picks_it_up_bounded_no_duplicates "
              f"-> real dispatch_one() picked the row up in {elapsed:.3f}s, "
              f"started_units={started_units}, final row_count=1")


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
            import traceback
            traceback.print_exc()
            print(f"ERROR: {t.__name__} -> {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)
