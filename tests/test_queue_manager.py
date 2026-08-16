#!/usr/bin/env python3
"""
Real test for queue-manager.py's pre-dispatch-backlog visibility fix
(task-20260815-231659): `list --status queued` used to read ONLY per-task
task.yaml files under TASKS_DIR, which structurally do not exist until a
worker is actually dispatched -- so it always returned an empty result
while real pre-dispatch umr_tasks rows existed, giving a false "nothing
queued" impression. Fixed by delegating to resource_governor.py's own real
--list-queue (and --stop-task/--resume-task/--set-priority for the
*-pending commands) rather than reimplementing a second reader/writer
against umr_tasks.

These tests never shell out to a real resource_governor.py or touch the
real filesystem TASKS_DIR -- subprocess.run and TASKS_DIR/RESOURCE_GOVERNOR_PY
are monkeypatched with fixed fakes, so assertions are deterministic
regardless of live backlog state.
"""
import importlib.util as _ilu
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
QUEUE_MANAGER_PATH = os.path.join(os.path.dirname(HERE), "queue-manager.py")

_spec = _ilu.spec_from_file_location("queue_manager_under_test", QUEUE_MANAGER_PATH)
queue_manager = _ilu.module_from_spec(_spec)
sys.modules["queue_manager_under_test"] = queue_manager
_spec.loader.exec_module(queue_manager)


class FakeCompletedProcess:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class FetchPreDispatchQueueTest(unittest.TestCase):
    def setUp(self):
        self._real_run = queue_manager.subprocess.run
        self._real_rg_path = queue_manager.RESOURCE_GOVERNOR_PY
        queue_manager.RESOURCE_GOVERNOR_PY = "/fake/resource_governor.py"
        self._orig_exists = os.path.exists
        os.path.exists = lambda p: True if p == "/fake/resource_governor.py" else self._orig_exists(p)

    def tearDown(self):
        queue_manager.subprocess.run = self._real_run
        queue_manager.RESOURCE_GOVERNOR_PY = self._real_rg_path
        os.path.exists = self._orig_exists

    def test_real_pre_dispatch_rows_surfaced_not_empty(self):
        """The core bug this fixes: real umr_tasks rows sitting in the
        pre-dispatch backlog must be visible through this CLI."""
        fake_rows = [
            {"position": 0, "umr_id": "UMR-1", "task_identity": "owner-task-1", "tier": 0,
             "status": "queued", "ts_submitted": "2026-08-15T00:00:00+00:00", "paused": False},
            {"position": 1, "umr_id": "UMR-2", "task_identity": "owner-task-2", "tier": 1,
             "status": "queued", "ts_submitted": "2026-08-15T00:01:00+00:00", "paused": False},
        ]
        queue_manager.subprocess.run = lambda *a, **k: FakeCompletedProcess(
            stdout=json.dumps({"ok": True, "status": "queued", "count": 2, "queue": fake_rows}))
        result = queue_manager.fetch_pre_dispatch_queue(status="queued", limit=100)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["queue"]), 2)
        self.assertEqual(result["queue"][0]["umr_id"], "UMR-1")

    def test_resource_governor_error_fails_open_not_crash(self):
        queue_manager.subprocess.run = lambda *a, **k: FakeCompletedProcess(
            stdout="", stderr="db locked", returncode=1)
        result = queue_manager.fetch_pre_dispatch_queue()
        self.assertFalse(result["ok"])
        self.assertIn("db locked", result["error"])

    def test_missing_resource_governor_reports_error_not_crash(self):
        os.path.exists = self._orig_exists  # nothing exists now
        result = queue_manager.fetch_pre_dispatch_queue()
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"])

    def test_bad_json_output_fails_open(self):
        queue_manager.subprocess.run = lambda *a, **k: FakeCompletedProcess(stdout="not json")
        result = queue_manager.fetch_pre_dispatch_queue()
        self.assertFalse(result["ok"])


class ListTasksIntegrationTest(unittest.TestCase):
    """Verifies the merged `list` output clearly labels both real sources,
    so an empty post-dispatch task.yaml scan is never mistaken for an empty
    queue overall."""

    def setUp(self):
        self.tmp_tasks_dir = tempfile.mkdtemp()
        self._real_tasks_dir = queue_manager.TASKS_DIR
        queue_manager.TASKS_DIR = self.tmp_tasks_dir
        self._real_run = queue_manager.subprocess.run
        self._real_rg_path = queue_manager.RESOURCE_GOVERNOR_PY
        queue_manager.RESOURCE_GOVERNOR_PY = "/fake/resource_governor.py"
        self._orig_exists = os.path.exists
        os.path.exists = lambda p: True if p == "/fake/resource_governor.py" else self._orig_exists(p)

    def tearDown(self):
        shutil.rmtree(self.tmp_tasks_dir, ignore_errors=True)
        queue_manager.TASKS_DIR = self._real_tasks_dir
        queue_manager.subprocess.run = self._real_run
        queue_manager.RESOURCE_GOVERNOR_PY = self._real_rg_path
        os.path.exists = self._orig_exists

    def test_empty_post_dispatch_with_real_pre_dispatch_rows_shows_both(self):
        fake_rows = [{"position": 0, "umr_id": "UMR-9", "task_identity": "owner-task-9", "tier": 0,
                      "status": "queued", "ts_submitted": "2026-08-15T00:00:00+00:00", "paused": False}]
        queue_manager.subprocess.run = lambda *a, **k: FakeCompletedProcess(
            stdout=json.dumps({"ok": True, "status": "queued", "count": 1, "queue": fake_rows}))
        buf = io.StringIO()
        with redirect_stdout(buf):
            queue_manager.list_tasks(status="queued", fmt="table", source="all", limit=100)
        out = buf.getvalue()
        self.assertIn("UMR-9", out)
        self.assertIn("PRE-DISPATCH BACKLOG", out)
        self.assertIn("POST-DISPATCH TASKS", out)
        self.assertIn("(none)", out)  # post-dispatch is genuinely empty here

    def test_json_format_separates_sources(self):
        queue_manager.subprocess.run = lambda *a, **k: FakeCompletedProcess(
            stdout=json.dumps({"ok": True, "status": "queued", "count": 0, "queue": []}))
        buf = io.StringIO()
        with redirect_stdout(buf):
            queue_manager.list_tasks(status="queued", fmt="json", source="all", limit=100)
        data = json.loads(buf.getvalue())
        self.assertIn("pre_dispatch", data)
        self.assertIn("post_dispatch", data)


class RunResourceGovernorDelegationTest(unittest.TestCase):
    """*-pending commands must delegate to resource_governor.py's real
    functions, never reimplement a second writer against umr_tasks."""

    def setUp(self):
        self._real_run = queue_manager.subprocess.run
        self._real_rg_path = queue_manager.RESOURCE_GOVERNOR_PY
        queue_manager.RESOURCE_GOVERNOR_PY = "/fake/resource_governor.py"
        self._orig_exists = os.path.exists
        os.path.exists = lambda p: True if p == "/fake/resource_governor.py" else self._orig_exists(p)

    def tearDown(self):
        queue_manager.subprocess.run = self._real_run
        queue_manager.RESOURCE_GOVERNOR_PY = self._real_rg_path
        os.path.exists = self._orig_exists

    def test_stop_pending_delegates_with_umr_id(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return FakeCompletedProcess(stdout=json.dumps({"ok": True, "umr_id": "UMR-1"}))

        queue_manager.subprocess.run = fake_run
        with redirect_stdout(io.StringIO()):
            queue_manager._run_resource_governor(["--stop-task", "--umr-id", "UMR-1"])
        self.assertEqual(len(calls), 1)
        self.assertIn("--stop-task", calls[0])
        self.assertIn("UMR-1", calls[0])

    def test_priority_pending_passes_tier(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return FakeCompletedProcess(stdout=json.dumps({"ok": True}))

        queue_manager.subprocess.run = fake_run
        with redirect_stdout(io.StringIO()):
            queue_manager._run_resource_governor(["--set-priority", "--umr-id", "UMR-1", "--tier", "2"])
        self.assertIn("--tier", calls[0])
        self.assertIn("2", calls[0])


if __name__ == "__main__":
    unittest.main()
