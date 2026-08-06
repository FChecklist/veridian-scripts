#!/usr/bin/env python3
"""Real tests for unified_orchestrator.py's end-to-end run() (2026-08-06
Superboss AUDIT FAIL on PR #207, AGENTS.md Operating Rule 7c): PROGRESS.md
claimed a detailed positive-path (all 10 steps ok:true, agent_id reuse
verified) and negative-path (a deliberately-failing audit command proving
steps 8/9/10 correctly refuse and umr_tasks.status stays queued) end-to-end
test had been run, but no test file existed anywhere in the diff or working
tree to reproduce that claim -- exactly the self-report-vs-diff mismatch
this task's own Gap 2 (independent re-verification, never trust a
self-reported "done") exists to prevent.

This file closes that gap for real: it exercises unified_orchestrator.run()
against the REAL composed modules (plan_generator.check_reuse_before_dispatch,
ai_agent_registry.ensure_agent, agent_work_briefing.assemble_briefing/
record_completion, tight_task_validation.validate_tight_task) -- never
mocked -- pointed at a real, isolated, temp-file SQLite database (same
_seed_scratch_db/_scratch_env/SUPERBOSS_REGISTER_DB-env-override convention
as tests/test_pm_decisions_pending.py / tests/test_capability_graduation.py)
and a real temp workspace directory, so nothing here ever touches the live
production database.

One real, unavoidable, narrowly-scoped side effect: ai_agent_registry.py's
AGENT_MEMORY_DIR (/opt/veridian/ai-os/memory/agents) has no env override
(by that module's own design -- one real UMR permanently owns one real
agent_id/memory file) -- these tests write one small real markdown memory
file per clearly-test-shaped UMR id used below (never deleted, matching
this module's own "insert-only, full history stays queryable" convention
elsewhere), a real but harmless side effect, not a mock.

Covers this task's own second audit finding too: record_task_audit()/
cmd_record_task_audit() (the task_audits table's real writer, exercised
indirectly by every step_reverify() call in the positive/negative tests
below, plus directly here).
"""
import argparse
import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Clearly test-shaped UMR ids (real UMR_ID_RE shape: UMR-YYYYMMDD-HHMMSS-xxxx)
# so ai_agent_registry.py's real memory files land under obviously-disposable
# names, never colliding with a real production UMR.
POSITIVE_UMR_ID = "UMR-20260806-090001-7e51"
NEGATIVE_UMR_ID = "UMR-20260806-090002-7e52"


def _load(name, filename, env=None):
    """Same load-with-env-override convention as
    tests/test_pm_decisions_pending.py's own _load(): resolve_superboss_db_path()
    is evaluated once, at module-exec time -- for a plain superboss-register.py
    load that's during THIS call's own exec_module(), so restoring env right
    after is safe.

    unified_orchestrator.py is different (real, live finding from building
    this test): it never touches superboss-register.py/plan_generator.py/
    ai_agent_registry.py/agent_work_briefing.py at ITS OWN exec_module() time
    -- each is lazily imported the first time one of its step_*() functions
    actually needs it, which happens later, inside run(). Restoring the env
    right after _load()'s own exec_module() call (as this helper does for
    every other module) would silently unset SUPERBOSS_REGISTER_DB again
    before those lazy sub-loads ever ran, sending every real write in
    run() to the live production DB instead of the scratch one. Callers
    that need the env to stay live through run() itself (i.e.
    unified_orchestrator.py) must use env_context() below instead of this
    function's env= param."""
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
                else:
                    os.environ[k] = v


class env_context:
    """Keeps `env`'s keys set in os.environ for the whole `with` block
    (unlike _load()'s env= param, which only holds them during its own
    exec_module() call) -- needed for unified_orchestrator.py, whose
    sub-module loads are lazy and happen inside run(), well after
    unified_orchestrator.py itself has finished loading."""

    def __init__(self, env):
        self._env = env
        self._old = {}

    def __enter__(self):
        for k, v in self._env.items():
            self._old[k] = os.environ.get(k)
            os.environ[k] = v
        return self

    def __exit__(self, *exc):
        for k, v in self._old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _seed_scratch_db(path):
    """Pre-create a real, fully-initialized scratch DB -- same convention as
    tests/test_pm_decisions_pending.py's own _seed_scratch_db(): init_db()'s
    own real executescript already creates umr_tasks/capability_registry/
    wiring_registry/knowledge_engine/etc. in one pass; task_audits and
    ai_agent_registry are each ensured idempotently by their own real
    callers (step_reverify()/ensure_agent()) the first time they're used, so
    nothing extra is needed for those here."""
    spec = importlib.util.spec_from_file_location("sbr_seed_orch", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)

    def _scratch_connect():
        conn = sqlite3.connect(path, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        conn.row_factory = sqlite3.Row
        return conn

    sbr._connect = _scratch_connect
    _real_stdout, sys.stdout = sys.stdout, io.StringIO()
    try:
        sbr.init_db()
    finally:
        sys.stdout = _real_stdout
    return sbr


def _scratch_env(scratch_db):
    return {"SUPERBOSS_REGISTER_DB": scratch_db}


def _seed_umr_row(sbr_seeded, scratch_db, umr_id, task_identity):
    """Inserts one real, minimal umr_tasks row (status='queued') for
    step_writeback's real cmd_mark_umr_terminal call to act against, and so
    the negative-path test can assert this row's status genuinely stays
    'queued' (never flips to 'completed') when re-verification fails."""
    conn = sqlite3.connect(scratch_db, timeout=30)
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, source_trigger, task_kind) "
        "VALUES (?,?,?,?,?,?,?)",
        (umr_id, task_identity, sbr_seeded._now_iso() if hasattr(sbr_seeded, "_now_iso") else "2026-08-06T09:00:00.000000Z",
         1, "queued", "test_unified_orchestrator", "systemctl_action"),
    )
    conn.commit()
    conn.close()


def _base_task_spec(umr_id, task_identity, intent_text, workspace_dir, success_criteria):
    return {
        "task_identity": task_identity,
        "intent_text": intent_text,
        "umr_id": umr_id,
        "role_label": "orchestrated_worker_test",
        "workspace_dir": workspace_dir,
        "tight_task": {
            "objective": "Write a real one-line PROGRESS.md file into the given real test workspace directory",
            "scope": "Only the real test workspace directory used by this one orchestrator test run",
            "successCriteria": success_criteria,
            "expectedOutput": "A real PROGRESS.md file committed to the real test workspace directory",
            "complexityTier": "mechanical",
        },
    }


def test_unified_orchestrator_positive_path_end_to_end():
    """Real positive path: every one of the 10 real steps returns ok:true,
    step 7 (independent re-verification) really passes because the audit
    command genuinely exits 0, step 9 really writes back status=completed,
    and running the whole sequence a second time for the SAME umr_id proves
    real agent_id reuse (step 3's minted=False, same agent_id both times) --
    exactly the claim PROGRESS.md made with no reproducible test until now."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        workspace_dir = os.path.join(d, "workspace")
        os.makedirs(workspace_dir, exist_ok=True)
        sbr_seeded = _seed_scratch_db(scratch_db)
        env = _scratch_env(scratch_db)
        _seed_umr_row(sbr_seeded, scratch_db, POSITIVE_UMR_ID, "orchestrator positive-path test task")

        # Real output file the submission contract (step 5) declares, and
        # step 8's output validation checks really exists.
        progress_path = os.path.join(workspace_dir, "PROGRESS.md")
        with open(progress_path, "w", encoding="utf-8") as f:
            f.write("# real test progress file\n")

        with env_context(env):
            orch = _load("uo_test_positive", "unified_orchestrator.py")
            task_spec = _base_task_spec(
                POSITIVE_UMR_ID, "orchestrator-positive-path-test", "orchestrator end to end positive path test",
                workspace_dir,
                "- `python3 -c \"import sys; sys.exit(0)\"` really exits 0, proving the real command genuinely runs",
            )
            task_spec["output_file_path"] = progress_path  # real absolute path -- _abs_path()
            # resolves it as-is (it only joins REPO_ROOT for a non-absolute path); a bare
            # "PROGRESS.md" would resolve against unified_orchestrator.py's own REPO_ROOT
            # (the scripts dir), not this test's workspace_dir, and could spuriously match
            # this repo's own real top-level PROGRESS.md -- found live while building this
            # test.

            steps_run1 = orch.run(task_spec)

            for name, result in steps_run1.items():
                assert result.get("ok") is True, f"{name} step did not return ok:true: {result}"

            assert steps_run1["7_reverify"]["passed"] is True, steps_run1["7_reverify"]
            assert steps_run1["7_reverify"]["exit_code"] == 0, steps_run1["7_reverify"]
            assert steps_run1["8_validate_output"]["passed"] is True, steps_run1["8_validate_output"]
            assert steps_run1["9_writeback"]["written"] is True, steps_run1["9_writeback"]
            assert steps_run1["10_graduate_evaluation"]["graduate"] is True, steps_run1["10_graduate_evaluation"]

            first_agent_id = steps_run1["3_resolve_agent_id"]["agent_id"]
            assert steps_run1["3_resolve_agent_id"]["minted"] is True, steps_run1["3_resolve_agent_id"]

            # Real umr_tasks row really flipped to completed.
            conn = sqlite3.connect(scratch_db)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT status FROM umr_tasks WHERE umr_id=?", (POSITIVE_UMR_ID,)).fetchone()
            conn.close()
            assert row["status"] == "completed", dict(row)

            # Real agent_id REUSE check: run the exact same task_spec a second
            # time for the same umr_id -- step 3 must report minted=False and
            # return the SAME real agent_id, never mint a second one.
            steps_run2 = orch.run(task_spec)
            assert steps_run2["3_resolve_agent_id"]["ok"] is True, steps_run2["3_resolve_agent_id"]
            assert steps_run2["3_resolve_agent_id"]["minted"] is False, steps_run2["3_resolve_agent_id"]
            assert steps_run2["3_resolve_agent_id"]["agent_id"] == first_agent_id, (
                steps_run2["3_resolve_agent_id"]["agent_id"], first_agent_id,
            )
    print("PASS: test_unified_orchestrator_positive_path_end_to_end")


def test_unified_orchestrator_negative_path_refuses_on_failed_reverify():
    """Real negative path: a deliberately-failing audit command (real
    `python3 -c "...sys.exit(1)"`, not a stub) must make step 7 genuinely
    fail, which must cascade into step 8 failing, step 9 REFUSING to write
    back (written=False), step 10 refusing to recommend graduation, and the
    real umr_tasks.status must stay exactly 'queued' -- never flipped to
    'completed' on a failed independent re-verification."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        workspace_dir = os.path.join(d, "workspace")
        os.makedirs(workspace_dir, exist_ok=True)
        sbr_seeded = _seed_scratch_db(scratch_db)
        env = _scratch_env(scratch_db)
        _seed_umr_row(sbr_seeded, scratch_db, NEGATIVE_UMR_ID, "orchestrator negative-path test task")

        # Deliberately do NOT write PROGRESS.md -- step 8's output-validation
        # failure reason list should also independently flag the missing
        # declared output file, on top of the failed re-verification.

        with env_context(env):
            orch = _load("uo_test_negative", "unified_orchestrator.py")
            task_spec = _base_task_spec(
                NEGATIVE_UMR_ID, "orchestrator-negative-path-test", "orchestrator end to end negative path test",
                workspace_dir,
                "- `python3 -c \"import sys; sys.exit(1)\"` deliberately exits 1 to prove step 7 really fails closed",
            )
            task_spec["output_file_path"] = os.path.join(workspace_dir, "PROGRESS.md")  # real absolute
            # path, deliberately never written above, so step 8 must really find it missing.

            steps = orch.run(task_spec)

            assert steps["7_reverify"]["ok"] is True, steps["7_reverify"]  # ran successfully...
            assert steps["7_reverify"]["passed"] is False, steps["7_reverify"]  # ...but really failed
            assert steps["7_reverify"]["exit_code"] == 1, steps["7_reverify"]

            assert steps["8_validate_output"]["passed"] is False, steps["8_validate_output"]
            assert any("re-verification" in r for r in steps["8_validate_output"]["reasons"]), steps["8_validate_output"]
            assert any("does not really exist" in r for r in steps["8_validate_output"]["reasons"]), steps["8_validate_output"]

            assert steps["9_writeback"]["ok"] is True, steps["9_writeback"]
            assert steps["9_writeback"]["written"] is False, steps["9_writeback"]

            assert steps["10_graduate_evaluation"]["graduate"] is False, steps["10_graduate_evaluation"]

            # The real umr_tasks row must genuinely still be 'queued' -- step 9
            # must never have called record_completion()/cmd_mark_umr_terminal.
            conn = sqlite3.connect(scratch_db)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT status FROM umr_tasks WHERE umr_id=?", (NEGATIVE_UMR_ID,)).fetchone()
            conn.close()
            assert row["status"] == "queued", dict(row)

            # The real task_audits row from step 7 is really there, verdict='fail'
            # (record_task_audit()/cmd_record_task_audit()'s real write path).
            conn = sqlite3.connect(scratch_db)
            conn.row_factory = sqlite3.Row
            audit_row = conn.execute(
                "SELECT verdict, exit_code, audit_cmd FROM task_audits WHERE audit_id=?",
                (steps["7_reverify"]["audit_id"],),
            ).fetchone()
            conn.close()
            assert audit_row is not None
            assert audit_row["verdict"] == "fail", dict(audit_row)
            assert audit_row["exit_code"] == 1, dict(audit_row)
    print("PASS: test_unified_orchestrator_negative_path_refuses_on_failed_reverify")


def test_cmd_record_task_audit_end_to_end():
    """Real end-to-end test through the argparse cmd_record_task_audit()
    CLI wrapper itself (the task_audits table's real writer,
    UMR-20260806-125524-720c GAP 2) -- this task's own second audit finding:
    'no tests were added for the new task_audits table / record_task_audit()
    / cmd_record_task_audit() write path in superboss-register.py'."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_record_task_audit", "superboss-register.py", env=_scratch_env(scratch_db))

        args = argparse.Namespace(
            work_item_id=None, software_task_id="task-test-record-audit-0001",
            audit_cmd="python3 -c \"import sys; sys.exit(0)\"", exit_code=0,
            stdout_tail="ok", stderr_tail="", verdict="pass",
        )
        captured = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            sbr.cmd_record_task_audit(args)
        finally:
            sys.stdout = old_stdout
        recorded = json.loads(captured.getvalue())
        assert recorded["verdict"] == "pass", recorded
        assert recorded["audit_id"].startswith("AUDIT-"), recorded

        conn = sbr._connect()
        row = dict(conn.execute(
            "SELECT * FROM task_audits WHERE audit_id=?", (recorded["audit_id"],)
        ).fetchone())
        conn.close()
        assert row["software_task_id"] == "task-test-record-audit-0001"
        assert row["exit_code"] == 0
        assert row["verdict"] == "pass"
    print("PASS: test_cmd_record_task_audit_end_to_end")


def test_record_task_audit_rejects_invalid_verdict():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)
        sbr = _load("sbr_test_record_task_audit_bad_verdict", "superboss-register.py", env=_scratch_env(scratch_db))

        conn = sbr._connect()
        sbr._ensure_task_audits_table(conn)
        try:
            sbr.record_task_audit(
                conn, work_item_id=None, software_task_id="task-test-bad-verdict", audit_cmd="true",
                exit_code=0, stdout_tail="", stderr_tail="", verdict="maybe",
            )
            assert False, "expected ValueError: verdict must be exactly 'pass' or 'fail'"
        except ValueError:
            pass
        conn.close()
    print("PASS: test_record_task_audit_rejects_invalid_verdict")


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
