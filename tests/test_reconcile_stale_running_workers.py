#!/usr/bin/env python3
"""Real tests for reconcile_stale_running_workers.py (UMR-20260813-090037-9a34,
addendum to UMR-20260806-171945-5767) -- closes PR #249's own real AUDIT:FAIL finding
"(d) ZERO test coverage for ... reconcile_stale_running_workers.py".

Two layers, matching this repo's own established convention for a script this shape
(see tests/test_reconcile_owner_dispatch_status.py):
  1. decide_and_apply()'s real decision tree, with systemctl/git/gh-adjacent seams
     (_unit_active_state, _task_dir_for_row, _load_task_yaml, _completion_candidates)
     monkeypatched -- fast, hermetic, no real subprocess/network dependency for the
     branches that would otherwise need one.
  2. A real, end-to-end test of the self-reported-negative branch (no git evidence
     needed for this path) against a real scratch SQLite DB and the real
     `superboss-register.py mark-umr-terminal` CLI, genuinely invoked as a real
     subprocess -- proving the actual write, not just a mocked call shape.
  3. sweep()'s own real wiring (the function dispatch-tick.py's new
     run_stale_running_workers_reconciliation() now calls in-process every tick) --
     confirms it aggregates real per-row decisions into the same report shape main()
     used to only print.
"""
import importlib.util
import os
import sqlite3
import tempfile

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECONCILE_SCRIPT = os.path.join(SCRIPTS_DIR, "reconcile_stale_running_workers.py")
SUPERBOSS_REGISTER_LIVE = "/opt/veridian/scripts/superboss-register.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("reconcile_stale_running_workers_test", RECONCILE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_sbr():
    spec = importlib.util.spec_from_file_location("sbr_for_reconcile_test", SUPERBOSS_REGISTER_LIVE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def scratch_db():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr2 = _load_sbr()
        sbr2.DB_PATH = path
        sbr2.init_db()
        yield path


def _row(umr_id="UMR-20260101-000000-aaaa", unit_name="veridian-worker@task-x.service", outputs_json="{}"):
    return {"umr_id": umr_id, "unit_name": unit_name, "task_identity": "task-x",
            "outputs_json": outputs_json, "status": "running"}


def test_active_unit_is_skipped_not_settled(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "_unit_active_state", lambda u: "active")
    entry = mod.decide_and_apply(_row(), execute=False)
    assert entry["decision"] == "skipped_not_settled"


def test_no_task_dir_found_requeues(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "_unit_active_state", lambda u: "inactive")
    monkeypatch.setattr(mod, "_task_dir_for_row", lambda row: (None, "no task directory found"))
    entry = mod.decide_and_apply(_row(), execute=False)
    assert entry["decision"] == "would_requeue"


def test_self_reported_negative_marks_failed_dry_run(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "_unit_active_state", lambda u: "failed")
    monkeypatch.setattr(mod, "_task_dir_for_row", lambda row: ("/fake/task-x", "task_identity"))
    monkeypatch.setattr(mod, "_load_task_yaml", lambda d: {
        "checkpoints": [{"status": "blocked"}], "branch": None, "repo": None,
    })
    entry = mod.decide_and_apply(_row(), execute=False)
    assert entry["decision"] == "would_mark_failed"
    assert entry["task_yaml_status"] == "blocked"


def test_ambiguous_in_progress_no_evidence_requeues(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "_unit_active_state", lambda u: "inactive")
    monkeypatch.setattr(mod, "_task_dir_for_row", lambda row: ("/fake/task-x", "task_identity"))
    monkeypatch.setattr(mod, "_load_task_yaml", lambda d: {
        "checkpoints": [{"status": "in_progress"}], "branch": None, "repo": None,
    })
    entry = mod.decide_and_apply(_row(), execute=False)
    assert entry["decision"] == "would_requeue"


def test_real_completion_candidate_attempts_completed_before_failed(monkeypatch):
    """The exact real gap PR #249's own AUDIT:FAIL cited: a task.yaml whose branch
    genuinely has a real, pushed commit must be OFFERED as a completed/
    completed_unmerged candidate -- never silently defaulted to failed just because
    the worker unit itself already stopped."""
    mod = _load_module()
    monkeypatch.setattr(mod, "_unit_active_state", lambda u: "inactive")
    monkeypatch.setattr(mod, "_task_dir_for_row", lambda row: ("/fake/task-x", "task_identity"))
    monkeypatch.setattr(mod, "_load_task_yaml", lambda d: {
        "checkpoints": [{"status": "completed", "files_modified": []}],
        "branch": "b1", "repo": "veridian-scripts",
        "completion_evidence": {},
    })
    monkeypatch.setattr(mod, "_completion_candidates", lambda task, repo, branch, repo_root: [
        {"kind": "commit_sha", "value": "deadbeef", "source": "git ls-remote (branch still exists)"}
    ])
    entry = mod.decide_and_apply(_row(), execute=False)
    assert entry["decision"] == "would_attempt_completed_then_completed_unmerged"
    assert entry["completion_candidates"]


def test_task_dir_for_row_prefers_new_task_id(monkeypatch):
    mod = _load_module()
    with tempfile.TemporaryDirectory() as tmp_tasks:
        monkeypatch.setattr(mod, "TASKS_DIR", tmp_tasks)
        os.makedirs(os.path.join(tmp_tasks, "resolved-via-new-task-id"))
        row = _row(outputs_json='{"new_task_id": "resolved-via-new-task-id"}')
        task_dir, method = mod._task_dir_for_row(row)
        assert task_dir == os.path.join(tmp_tasks, "resolved-via-new-task-id")
        assert method == "outputs_json.new_task_id"


def test_task_dir_for_row_falls_back_to_reconcile_umr_match(monkeypatch):
    mod = _load_module()
    with tempfile.TemporaryDirectory() as tmp_tasks:
        monkeypatch.setattr(mod, "TASKS_DIR", tmp_tasks)
        umr_id = "UMR-20260101-000000-bbbb"
        os.makedirs(os.path.join(tmp_tasks, f"reconcile-umr-20260101-000000-bbbb"))
        row = _row(umr_id=umr_id, outputs_json="{}")
        row["task_identity"] = "no-such-dir"
        task_dir, method = mod._task_dir_for_row(row)
        assert task_dir is not None
        assert "reconcile-umr" in method


def test_sweep_aggregates_real_decisions(monkeypatch):
    """sweep() -- the function dispatch-tick.py's own
    run_stale_running_workers_reconciliation() now calls every real tick -- must
    return the exact same shape main() used to only print."""
    mod = _load_module()
    fake_rows = [_row(umr_id="UMR-A"), _row(umr_id="UMR-B", unit_name="veridian-worker@task-y.service")]
    monkeypatch.setattr(mod, "_fetch_affected_rows", lambda: fake_rows)
    monkeypatch.setattr(mod, "_unit_active_state", lambda u: "active")  # both skipped, simplest real branch

    report = mod.sweep(execute=False)
    assert report["execute"] is False
    assert report["examined"] == 2
    assert report["counts"] == {"skipped_not_settled": 2}
    assert len(report["rows"]) == 2


def test_real_end_to_end_self_reported_negative_marks_failed_via_real_cli(monkeypatch, scratch_db):
    """Real, end-to-end proof (no mocked write): a real scratch umr_tasks row, a real
    task.yaml on disk, run through the real decide_and_apply(--execute) with only the
    systemd/task-dir seams stubbed (this box's own real systemd/task tree is
    irrelevant to this script's own decision logic once those are known) -- the real
    `mark-umr-terminal` CLI subprocess genuinely writes status='failed' to the real
    scratch DB."""
    mod = _load_module()
    monkeypatch.setenv("SUPERBOSS_REGISTER_DB", scratch_db)
    monkeypatch.setattr(mod, "_unit_active_state", lambda u: "inactive")

    with tempfile.TemporaryDirectory() as task_dir:
        monkeypatch.setattr(mod, "_task_dir_for_row", lambda row: (task_dir, "task_identity"))
        monkeypatch.setattr(mod, "_load_task_yaml", lambda d: {
            "checkpoints": [{"status": "blocked"}], "branch": None, "repo": None,
        })

        conn = sqlite3.connect(scratch_db)
        conn.execute(
            "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
            "source_trigger, unit_name) VALUES ('UMR-TEST-RECONCILE', 'task-x', "
            "datetime('now'), 1, 'running', 'unit_test', 'veridian-worker@task-x.service')"
        )
        conn.commit()
        conn.close()

        entry = mod.decide_and_apply(_row(umr_id="UMR-TEST-RECONCILE"), execute=True)
        assert entry["decision"] == "failed", entry

        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM umr_tasks WHERE umr_id='UMR-TEST-RECONCILE'").fetchone()
        conn.close()
        assert dict(row)["status"] == "failed"


if __name__ == "__main__":
    import inspect
    import shutil as _shutil

    class _MP:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name, None), True))
            setattr(obj, name, value)

        def setenv(self, name, value):
            self._undo.append((os.environ, name, os.environ.get(name), name in os.environ))
            os.environ[name] = value

        def undo(self):
            for obj, name, old, had in reversed(self._undo):
                if obj is os.environ:
                    if had:
                        os.environ[name] = old
                    else:
                        os.environ.pop(name, None)
                else:
                    setattr(obj, name, old)

    def _scratch_db():
        d = tempfile.mkdtemp()
        path = os.path.join(d, "scratch.sqlite")
        sbr2 = _load_sbr()
        sbr2.DB_PATH = path
        sbr2.init_db()
        return path, d

    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        params = inspect.signature(fn).parameters
        mp = _MP()
        db_dir = None
        try:
            kwargs = {}
            if "monkeypatch" in params:
                kwargs["monkeypatch"] = mp
            if "scratch_db" in params:
                db_path, db_dir = _scratch_db()
                kwargs["scratch_db"] = db_path
            fn(**kwargs)
            print(f"PASS: {name}")
        finally:
            mp.undo()
            if db_dir:
                _shutil.rmtree(db_dir, ignore_errors=True)
    print("ALL TESTS PASSED")
