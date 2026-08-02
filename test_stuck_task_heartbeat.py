#!/usr/bin/env python3
"""
Real, executable test for the 2026-08-02 PM directive: extend dispatch-tick.py
(same file, same tick, no new script/timer) with (1) stuck-task detection --
any task.yaml with status=blocked and no new checkpoint for longer than a
threshold gets surfaced, never auto-resolved -- and (2) a real heartbeat
(timestamp, real load average, EMERGENCY_STOP state, blocked/running task
counts), both written to one canonical file
(ai-os/STUCK_TASKS_HEARTBEAT.json by default).

Everything here is in-process against find_stuck_tasks()/
write_stuck_tasks_heartbeat() directly, using synthetic task dicts and a
tempdir heartbeat path -- never touches a real task.yaml or the real live
STUCK_TASKS_HEARTBEAT.json.

Usage: python3 test_stuck_task_heartbeat.py
Exit 0 = all assertions passed. Exit 1 = a test failed.
"""
import datetime
import importlib.util
import json
import os
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        failures.append(label)


def load_dispatch_tick():
    spec = importlib.util.spec_from_file_location(
        "dispatch_tick", os.path.join(SCRIPTS_DIR, "dispatch-tick.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def iso(dt):
    return dt.isoformat()


# ---------------------------------------------------------------------------
# find_stuck_tasks()
# ---------------------------------------------------------------------------

def test_find_stuck_tasks():
    dispatch_tick = load_dispatch_tick()
    now = datetime.datetime(2026, 8, 2, 12, 0, 0, tzinfo=datetime.timezone.utc)

    long_blocked = now - datetime.timedelta(minutes=90)
    just_blocked = now - datetime.timedelta(minutes=5)
    exactly_at_threshold = now - datetime.timedelta(minutes=30)

    tasks = {
        "task-long-blocked": {
            "status": "blocked",
            "last_checkpoint_at": iso(long_blocked),
            "checkpoints": [
                {"at": iso(long_blocked), "status": "in_progress", "note": "earlier note"},
                {"at": iso(long_blocked), "status": "blocked", "note": "supervisor failed to produce a review verdict"},
            ],
        },
        "task-just-blocked": {
            "status": "blocked",
            "last_checkpoint_at": iso(just_blocked),
            "checkpoints": [{"at": iso(just_blocked), "status": "blocked", "note": "too recent to be stuck"}],
        },
        "task-at-threshold": {
            "status": "blocked",
            "last_checkpoint_at": iso(exactly_at_threshold),
            "checkpoints": [{"at": iso(exactly_at_threshold), "status": "blocked", "note": "right at 30min"}],
        },
        "task-in-progress": {
            "status": "in_progress",
            "last_checkpoint_at": iso(long_blocked),
            "checkpoints": [{"at": iso(long_blocked), "status": "in_progress", "note": "still running, not blocked"}],
        },
        "task-completed": {
            "status": "completed",
            "last_checkpoint_at": iso(long_blocked),
        },
        "task-blocked-no-timestamp": {
            "status": "blocked",
            "checkpoints": [{"at": None, "status": "blocked", "note": "malformed, no last_checkpoint_at"}],
        },
        "task-blocked-bad-timestamp": {
            "status": "blocked",
            "last_checkpoint_at": "not-a-real-timestamp",
        },
        "task-blocked-no-checkpoints": {
            "status": "blocked",
            "last_checkpoint_at": iso(long_blocked),
        },
    }

    stuck = dispatch_tick.find_stuck_tasks(tasks, now, threshold_minutes=30)
    stuck_ids = {item["task_id"] for item in stuck}

    check("long-blocked task (90min) IS flagged stuck",
          "task-long-blocked" in stuck_ids)
    check("recently-blocked task (5min) is NOT flagged",
          "task-just-blocked" not in stuck_ids)
    check("exactly-at-threshold task (30min) IS flagged (>= threshold, not > threshold)",
          "task-at-threshold" in stuck_ids)
    check("in_progress task is never flagged regardless of checkpoint age",
          "task-in-progress" not in stuck_ids)
    check("completed task is never flagged",
          "task-completed" not in stuck_ids)
    check("blocked task with no parseable timestamp is safely skipped, not an exception",
          "task-blocked-no-timestamp" not in stuck_ids
          and "task-blocked-bad-timestamp" not in stuck_ids)
    check("blocked task with a real last_checkpoint_at but no checkpoints[] list still works",
          "task-blocked-no-checkpoints" in stuck_ids)

    by_id = {item["task_id"]: item for item in stuck}
    long_entry = by_id["task-long-blocked"]
    check("stuck entry reports task_id",
          long_entry["task_id"] == "task-long-blocked")
    check("stuck entry reports blocked_since == last_checkpoint_at",
          long_entry["blocked_since"] == iso(long_blocked))
    check("stuck entry reports blocked_minutes ~= 90",
          abs(long_entry["blocked_minutes"] - 90.0) < 0.1)
    check("stuck entry reports last_note from the LAST checkpoint (not an earlier one)",
          long_entry["last_note"] == "supervisor failed to produce a review verdict")

    no_cp_entry = by_id["task-blocked-no-checkpoints"]
    check("stuck entry with no checkpoints[] reports last_note=None, not a crash/fabrication",
          no_cp_entry["last_note"] is None)

    check("results sorted most-stuck-first",
          all(stuck[i]["blocked_minutes"] >= stuck[i + 1]["blocked_minutes"]
              for i in range(len(stuck) - 1)))

    check("function never mutates the input task dicts (read-only w.r.t. task state)",
          tasks["task-long-blocked"]["status"] == "blocked"
          and "blocked_minutes" not in tasks["task-long-blocked"])


def test_find_stuck_tasks_empty_when_nothing_stuck():
    dispatch_tick = load_dispatch_tick()
    now = datetime.datetime(2026, 8, 2, 12, 0, 0, tzinfo=datetime.timezone.utc)
    tasks = {
        "task-a": {"status": "in_progress", "last_checkpoint_at": iso(now)},
        "task-b": {"status": "pending", "last_checkpoint_at": iso(now)},
    }
    stuck = dispatch_tick.find_stuck_tasks(tasks, now)
    check("no blocked tasks at all -> empty stuck list, not an error", stuck == [])


# ---------------------------------------------------------------------------
# write_stuck_tasks_heartbeat()
# ---------------------------------------------------------------------------

def test_write_stuck_tasks_heartbeat():
    dispatch_tick = load_dispatch_tick()
    now = datetime.datetime(2026, 8, 2, 12, 0, 0, tzinfo=datetime.timezone.utc)

    tasks = {
        "t1": {"status": "blocked"},
        "t2": {"status": "blocked"},
        "t3": {"status": "in_progress"},
        "t4": {"status": "completed"},
    }
    stuck_tasks = [{"task_id": "t1", "blocked_since": "x", "blocked_minutes": 90.0, "last_note": "n"}]

    with tempfile.TemporaryDirectory() as tmpdir:
        heartbeat_path = os.path.join(tmpdir, "STUCK_TASKS_HEARTBEAT.json")
        emergency_stop_path = os.path.join(tmpdir, "resource-governor-EMERGENCY_STOP")

        real_heartbeat_path = dispatch_tick.STUCK_TASKS_HEARTBEAT_PATH
        real_stop_path = dispatch_tick.resource_governor.EMERGENCY_STOP_PATH
        dispatch_tick.STUCK_TASKS_HEARTBEAT_PATH = heartbeat_path
        dispatch_tick.resource_governor.EMERGENCY_STOP_PATH = emergency_stop_path

        try:
            # -- EMERGENCY_STOP absent --
            result = dispatch_tick.write_stuck_tasks_heartbeat(tasks, stuck_tasks, now)
            check("heartbeat file actually created on disk",
                  os.path.isfile(heartbeat_path))
            with open(heartbeat_path) as f:
                on_disk = json.load(f)
            check("on-disk JSON is valid and matches the returned dict",
                  on_disk == result)
            check("generated_at is the real tick timestamp passed in",
                  on_disk["generated_at"] == now.isoformat())
            check("emergency_stop is False when the sentinel file is absent",
                  on_disk["emergency_stop"] is False)
            check("blocked_task_count counts only status=='blocked' tasks (2, not all 4)",
                  on_disk["blocked_task_count"] == 2)
            check("running_task_count counts only status=='in_progress' tasks (1, not all 4)",
                  on_disk["running_task_count"] == 1)
            check("stuck_tasks list is passed through verbatim",
                  on_disk["stuck_tasks"] == stuck_tasks)
            check("stuck_task_threshold_minutes reflects the real configured threshold",
                  on_disk["stuck_task_threshold_minutes"] == dispatch_tick.STUCK_TASK_THRESHOLD_MINUTES)
            load_avg = on_disk["load_average"]
            check("load_average is a real {1m,5m,15m} dict of numbers, not a placeholder",
                  isinstance(load_avg, dict) and set(load_avg) == {"1m", "5m", "15m"}
                  and all(isinstance(v, (int, float)) for v in load_avg.values()))

            # -- EMERGENCY_STOP present --
            with open(emergency_stop_path, "w") as f:
                f.write("{}")
            result2 = dispatch_tick.write_stuck_tasks_heartbeat(tasks, [], now)
            check("emergency_stop is True once the real sentinel file exists",
                  result2["emergency_stop"] is True)
            check("stuck_tasks correctly reflects an empty list on a clean tick",
                  result2["stuck_tasks"] == [])

            # -- atomic write: no leftover .tmp file --
            check("no leftover .tmp file after a successful write",
                  not os.path.exists(f"{heartbeat_path}.tmp"))

            # -- read-only w.r.t. task state --
            check("write_stuck_tasks_heartbeat never mutates the tasks dict passed in",
                  tasks == {
                      "t1": {"status": "blocked"},
                      "t2": {"status": "blocked"},
                      "t3": {"status": "in_progress"},
                      "t4": {"status": "completed"},
                  })
        finally:
            dispatch_tick.STUCK_TASKS_HEARTBEAT_PATH = real_heartbeat_path
            dispatch_tick.resource_governor.EMERGENCY_STOP_PATH = real_stop_path


if __name__ == "__main__":
    test_find_stuck_tasks()
    test_find_stuck_tasks_empty_when_nothing_stuck()
    test_write_stuck_tasks_heartbeat()

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("All assertions passed.")
    sys.exit(0)
