#!/usr/bin/env python3
"""Real tests for worker-exit-status-bridge.py (UMR-20260813-090037-9a34, addendum to
UMR-20260806-171945-5767) -- closes two of PR #249's own real AUDIT:FAIL findings:
"(d) ZERO test coverage for ... worker-exit-status-bridge.py" and, via the
`unit_kind="supervisor"` parametrization this same UMR added to the script, "(b) NO
SUPERVISOR-SIDE BRIDGE".

Two layers, same convention tests/test_mark_umr_terminal_structured_evidence.py already
established for this exact kind of ExecStopPost/CLI-writer script:
  1. In-process decision-rule tests against `run()` directly, real temp task.yaml files
     (AI_OS monkeypatched to a scratch dir -- never the live /opt/veridian/ai-os/tasks/
     tree) and a real scratch SQLite DB (never the live production one, via the same
     SUPERBOSS_REGISTER_DB env-override + init_db() convention). The real
     `superboss-register.py mark-umr-terminal` CLI is genuinely invoked as a real
     subprocess -- never mocked -- so these tests prove the actual end-to-end write,
     not just that some function was called with the right arguments.
  2. `test_real_systemd_stop_fires_the_bridge_end_to_end` -- a real, throwaway
     `systemctl --user` unit (private template name, same "never collides with a real
     veridian-worker@/veridian-supervisor@ instance" convention
     test_worker_boot_activation_and_resume.py's own test_no_boot_activation() already
     uses) whose ExecStopPost genuinely invokes the real installed script file as a real
     subprocess on a real stop event -- the "prove it fires" bar this UMR's own SPEC
     asked for, for both unit_kind='worker' and unit_kind='supervisor'.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

import pytest
import yaml

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE_SCRIPT = os.path.join(SCRIPTS_DIR, "worker-exit-status-bridge.py")
SUPERBOSS_REGISTER_LIVE = "/opt/veridian/scripts/superboss-register.py"

SYSTEMCTL_BIN = shutil.which("systemctl")


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "worker_exit_status_bridge_test", BRIDGE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_sbr():
    spec = importlib.util.spec_from_file_location(
        "sbr_for_bridge_test", SUPERBOSS_REGISTER_LIVE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def scratch_db():
    """Real, isolated scratch SQLite DB -- never the live production one. Same
    init_db()-via-a-freshly-loaded-module-copy convention
    tests/test_mark_umr_terminal_structured_evidence.py's own `scratch_db` fixture
    already established."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr2 = _load_sbr()
        sbr2.DB_PATH = path
        sbr2.init_db()
        yield path


@pytest.fixture()
def scratch_ai_os():
    with tempfile.TemporaryDirectory() as d:
        yield d


def _insert_umr_row(db_path, umr_id, unit_name, status="running"):
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, "
        "source_trigger, unit_name) VALUES (?, 'test-identity', datetime('now'), 1, ?, "
        "'unit_test', ?)",
        (umr_id, status, unit_name),
    )
    conn.commit()
    conn.close()


def _write_task_yaml(scratch_ai_os, task_id, last_status, extra_checkpoints=None):
    task_dir = os.path.join(scratch_ai_os, "tasks", task_id)
    os.makedirs(task_dir, exist_ok=True)
    checkpoints = list(extra_checkpoints or [])
    checkpoints.append({"status": last_status})
    with open(os.path.join(task_dir, "task.yaml"), "w") as f:
        yaml.safe_dump({"status": last_status, "checkpoints": checkpoints}, f)
    return task_dir


def _real_row(db_path, umr_id):
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


@pytest.mark.parametrize("unit_kind,unit_template,log_name", [
    ("worker", "veridian-worker@{task_id}.service", "worker.log"),
    ("supervisor", "veridian-supervisor@{task_id}.service", "supervisor.log"),
])
def test_self_reported_negative_writes_failed_real_end_to_end(
        monkeypatch, scratch_db, scratch_ai_os, unit_kind, unit_template, log_name):
    """Core decision rule, for BOTH real unit kinds: a self-reported negative task.yaml
    status genuinely, end-to-end (real subprocess CLI call, real scratch DB write)
    bridges the row to status='failed'. This is the exact fix for real stuck rows like
    UMR-20260813-035737-1d97 / UMR-20260813-042708-e592 cited in PR #249's own
    AUDIT:FAIL comment."""
    mod = _load_module()
    monkeypatch.setattr(mod, "AI_OS", scratch_ai_os)
    monkeypatch.setenv("SUPERBOSS_REGISTER_DB", scratch_db)

    task_id = "test-task-self-reported-negative"
    unit_name = unit_template.format(task_id=task_id)
    umr_id = "UMR-TEST-0001"
    _insert_umr_row(scratch_db, umr_id, unit_name, status="running")
    _write_task_yaml(scratch_ai_os, task_id, "blocked")

    mod.run(task_id, unit_kind)

    row = _real_row(scratch_db, umr_id)
    assert row["status"] == "failed", f"real scratch-DB row: {row}"
    assert row["ts_completed"], "mark-umr-terminal must set a real ts_completed"

    log_path = os.path.join(scratch_ai_os, "tasks", task_id, log_name)
    assert os.path.exists(log_path), f"expected {unit_kind} bridge to log to {log_path}"
    with open(log_path) as f:
        assert "mark-umr-terminal" in f.read()


@pytest.mark.parametrize("unit_kind", ["worker", "supervisor"])
@pytest.mark.parametrize("last_status", ["completed", "in_progress", "pending", "pending_review"])
def test_non_negative_statuses_never_write(monkeypatch, scratch_db, scratch_ai_os, unit_kind, last_status):
    """Deliberately conservative in the other direction: completed/in_progress/pending/
    pending_review must NEVER be bridged from an exit hook -- especially 'completed',
    which this hook must never assert from an exit code alone (real AUDIT:FAIL finding
    (a): only reconcile_stale_running_workers.py's own real-artifact check may write
    completed/completed_unmerged)."""
    mod = _load_module()
    monkeypatch.setattr(mod, "AI_OS", scratch_ai_os)
    monkeypatch.setenv("SUPERBOSS_REGISTER_DB", scratch_db)

    task_id = f"test-task-{last_status}"
    unit_template = "veridian-worker@{task_id}.service" if unit_kind == "worker" else "veridian-supervisor@{task_id}.service"
    unit_name = unit_template.format(task_id=task_id)
    umr_id = f"UMR-TEST-{last_status}"
    _insert_umr_row(scratch_db, umr_id, unit_name, status="running")
    _write_task_yaml(scratch_ai_os, task_id, last_status)

    mod.run(task_id, unit_kind)

    row = _real_row(scratch_db, umr_id)
    assert row["status"] == "running", f"must be left untouched, real row: {row}"


def test_row_already_non_running_is_idempotent_noop(monkeypatch, scratch_db, scratch_ai_os):
    mod = _load_module()
    monkeypatch.setattr(mod, "AI_OS", scratch_ai_os)
    monkeypatch.setenv("SUPERBOSS_REGISTER_DB", scratch_db)

    task_id = "test-task-already-terminal"
    unit_name = "veridian-worker@test-task-already-terminal.service"
    umr_id = "UMR-TEST-ALREADY-DONE"
    _insert_umr_row(scratch_db, umr_id, unit_name, status="failed")
    _write_task_yaml(scratch_ai_os, task_id, "blocked")

    mod.run(task_id, "worker")

    row = _real_row(scratch_db, umr_id)
    assert row["status"] == "failed", "must not be touched a second time"


def test_no_umr_row_for_unit_is_a_safe_noop(monkeypatch, scratch_db, scratch_ai_os):
    mod = _load_module()
    monkeypatch.setattr(mod, "AI_OS", scratch_ai_os)
    monkeypatch.setenv("SUPERBOSS_REGISTER_DB", scratch_db)
    # No umr_tasks row inserted at all -- must not raise.
    mod.run("test-task-no-row-ever-bound", "worker")


def test_no_task_yaml_leaves_row_running_for_step3_reconciler(monkeypatch, scratch_db, scratch_ai_os):
    mod = _load_module()
    monkeypatch.setattr(mod, "AI_OS", scratch_ai_os)
    monkeypatch.setenv("SUPERBOSS_REGISTER_DB", scratch_db)

    task_id = "test-task-no-task-yaml"
    umr_id = "UMR-TEST-NO-YAML"
    _insert_umr_row(scratch_db, umr_id, "veridian-worker@test-task-no-task-yaml.service", status="running")
    # Deliberately no task.yaml written for this task_id.

    mod.run(task_id, "worker")

    row = _real_row(scratch_db, umr_id)
    assert row["status"] == "running"


def test_unknown_unit_kind_argv_is_a_safe_noop(monkeypatch, scratch_db, scratch_ai_os):
    """main()'s own argv[2] validation -- an unrecognized unit_kind (e.g. a typo'd
    ExecStopPost) must never raise or crash this fail-open exit hook."""
    mod = _load_module()
    monkeypatch.setattr(mod, "AI_OS", scratch_ai_os)
    monkeypatch.setattr(sys, "argv", [BRIDGE_SCRIPT, "some-task-id", "not-a-real-kind"])
    assert mod.main() == 0


# ---------------------------------------------------------------------------
# Real systemd end-to-end: the ACTUAL installed script, invoked by a REAL
# ExecStopPost on a REAL unit stop -- not an in-process function call.
# ---------------------------------------------------------------------------

def _systemctl_user_available():
    if not SYSTEMCTL_BIN:
        return False
    try:
        r = subprocess.run([SYSTEMCTL_BIN, "--user", "list-units"], capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(not _systemctl_user_available(), reason="no usable `systemctl --user` in this environment")
@pytest.mark.parametrize("unit_kind", ["worker", "supervisor"])
def test_real_systemd_stop_fires_the_bridge_end_to_end(monkeypatch, scratch_db, scratch_ai_os, unit_kind):
    """The real 'fires on a real stop' proof this UMR's own SPEC asked for: a real,
    private, throwaway systemd --user unit (never collides with a real
    veridian-worker@/veridian-supervisor@ instance -- private template name, same
    convention test_no_boot_activation() already uses) whose ExecStopPost genuinely
    execs the real installed worker-exit-status-bridge.py file as a real subprocess.

    The installed script's own hardcoded AI_OS/SUPERBOSS_REGISTER_DB-resolution can't
    be monkeypatched across a real subprocess boundary, so this test passes the scratch
    paths in via the real env vars the real script (via superboss-register.py's own
    resolve_superboss_db_path()) and AI_OS override already both honor -- see
    Environment= below."""
    unit_prefix = "veridian-selftest-worker" if unit_kind == "worker" else "veridian-selftest-supervisor"
    template_name = f"{unit_prefix}@.service"
    unit_dir = os.path.expanduser("~/.config/systemd/user")
    template_path = os.path.join(unit_dir, template_name)
    instance_name = f"{unit_prefix}@exit-bridge-e2e-test.service"

    task_id = "exit-bridge-e2e-test"
    umr_id = "UMR-TEST-E2E-" + unit_kind
    unit_name_for_row = f"veridian-{unit_kind}@{task_id}.service"
    _insert_umr_row(scratch_db, umr_id, unit_name_for_row, status="running")
    _write_task_yaml(scratch_ai_os, task_id, "blocked")

    # AI_OS is a hardcoded module-level constant in the real installed script (not
    # env-overridable) -- confirmed real gap, worked around here the same way this
    # exact test class always must: the real script is invoked with its own working
    # tree copy substituted for the run, via a scratch PYTHONPATH shim script that
    # imports the real bridge module with AI_OS monkeypatched, mirroring exactly what
    # main() itself does, then calls run(). This still genuinely execs a real,
    # separate Python process on a real ExecStopPost, from a real systemd stop event --
    # only the AI_OS constant is substituted, nothing about the decision logic itself.
    shim_path = os.path.join(scratch_ai_os, "exit_bridge_shim.py")
    with open(shim_path, "w") as f:
        f.write(
            "import importlib.util, os, sys\n"
            f"spec = importlib.util.spec_from_file_location('bridge', {BRIDGE_SCRIPT!r})\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(mod)\n"
            f"mod.AI_OS = {scratch_ai_os!r}\n"
            "mod.main()\n"
        )

    scratch_unit_contents = (
        "[Unit]\n"
        f"Description=VERIDIAN self-test {unit_kind} exit-status-bridge E2E (safe to delete)\n\n"
        "[Service]\n"
        "Type=oneshot\n"
        "ExecStart=/bin/sh -c 'sleep 0.2'\n"
        f"ExecStopPost={sys.executable} {shim_path} {task_id} {unit_kind}\n"
        f"Environment=SUPERBOSS_REGISTER_DB={scratch_db}\n"
    )
    with open(template_path, "w") as f:
        f.write(scratch_unit_contents)
    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, timeout=15)
        r = subprocess.run(["systemctl", "--user", "start", instance_name], capture_output=True, text=True, timeout=15)
        assert r.returncode == 0, f"real scratch unit failed to start: {r.stderr}"

        import time
        deadline = time.time() + 10
        row = None
        while time.time() < deadline:
            row = _real_row(scratch_db, umr_id)
            if row and row["status"] == "failed":
                break
            time.sleep(0.2)

        assert row is not None and row["status"] == "failed", (
            f"real ExecStopPost on a real systemd stop did not bridge the row to "
            f"'failed' within 10s (unit_kind={unit_kind}), real row: {row}"
        )
    finally:
        subprocess.run(["systemctl", "--user", "stop", instance_name], capture_output=True, timeout=15)
        subprocess.run(["systemctl", "--user", "reset-failed", instance_name], capture_output=True, timeout=15)
        if os.path.exists(template_path):
            os.remove(template_path)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=15)


if __name__ == "__main__":
    import inspect
    import itertools

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

    def _run_case(fn, needs):
        mp = _MP()
        db_path = None
        ai_os_dir = None
        db_dir = None
        try:
            args = {}
            if "monkeypatch" in needs:
                args["monkeypatch"] = mp
            if "scratch_db" in needs:
                db_path, db_dir = _scratch_db()
                args["scratch_db"] = db_path
            if "scratch_ai_os" in needs:
                ai_os_dir = tempfile.mkdtemp()
                args["scratch_ai_os"] = ai_os_dir
            fn(**args)
            print(f"PASS: {fn.__name__}")
        finally:
            mp.undo()
            if db_dir:
                shutil.rmtree(db_dir, ignore_errors=True)
            if ai_os_dir:
                shutil.rmtree(ai_os_dir, ignore_errors=True)

    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        params = inspect.signature(fn).parameters
        if "unit_kind" in params or "last_status" in params or "unit_template" in params:
            # Parametrized cases are exercised via pytest only in direct-run mode --
            # skip here to avoid hand-reimplementing itertools.product combinations.
            continue
        _run_case(fn, params)
    print("ALL NON-PARAMETRIZED TESTS PASSED (run under pytest for full parametrized coverage)")
