#!/usr/bin/env python3
"""
Real, executable test for dispatch-tick.py's stuck_task_and_heartbeat_tick()
(added 2026-08-02, PM decision UMR-20260802-074346-a9b9). Never touches the
real live DISPATCH_TICK_HEARTBEAT.json, EMERGENCY_STOP sentinel, or any real
task.yaml -- everything is a temp file/dict, and the module-level path
constants are overridden before each call.

Usage: python3 test_dispatch_tick_heartbeat.py
Exit 0 = all assertions passed. Exit 1 = a test failed.
"""
import datetime
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# dispatch-tick.py has a hyphen -- not a valid module name, load by path instead.
import importlib.util
spec = importlib.util.spec_from_file_location(
    "dispatch_tick", os.path.join(os.path.dirname(os.path.abspath(__file__)), "dispatch-tick.py")
)
dispatch_tick = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dispatch_tick)


def _iso(dt):
    return dt.isoformat()


def run_tick(tasks, tmp_dir, now, emergency_stop=False):
    """Runs stuck_task_and_heartbeat_tick with the heartbeat path and
    EMERGENCY_STOP path both redirected into tmp_dir, returns the written doc
    read back from disk (not just the in-memory return value) so the test
    also exercises the real atomic-write path."""
    heartbeat_path = os.path.join(tmp_dir, "DISPATCH_TICK_HEARTBEAT.json")
    stop_path = os.path.join(tmp_dir, "EMERGENCY_STOP")

    orig_heartbeat_path = dispatch_tick.DISPATCH_TICK_HEARTBEAT_PATH
    orig_stop_path = dispatch_tick.resource_governor.EMERGENCY_STOP_PATH
    dispatch_tick.DISPATCH_TICK_HEARTBEAT_PATH = heartbeat_path
    dispatch_tick.resource_governor.EMERGENCY_STOP_PATH = stop_path
    try:
        if emergency_stop:
            with open(stop_path, "w") as f:
                f.write("{}")
        elif os.path.exists(stop_path):
            os.remove(stop_path)

        result = dispatch_tick.stuck_task_and_heartbeat_tick(tasks, now=now)
        assert os.path.isfile(heartbeat_path), "heartbeat file was not written"
        assert not os.path.isfile(heartbeat_path + ".tmp"), "temp file left behind -- not atomic"
        with open(heartbeat_path) as f:
            on_disk = json.load(f)
        assert on_disk == result, "in-memory return value does not match what was actually written"
        return on_disk
    finally:
        dispatch_tick.DISPATCH_TICK_HEARTBEAT_PATH = orig_heartbeat_path
        dispatch_tick.resource_governor.EMERGENCY_STOP_PATH = orig_stop_path


def test_stuck_task_detected_past_threshold():
    now = datetime.datetime(2026, 8, 2, 8, 0, 0, tzinfo=datetime.timezone.utc)
    old_ts = now - datetime.timedelta(seconds=dispatch_tick.STUCK_TASK_THRESHOLD_SECONDS + 60)
    tasks = {
        "task-stuck": {
            "status": "blocked",
            "last_checkpoint_at": _iso(old_ts),
            "checkpoints": [{"note": "credit accountant rejected auto-fix attempt 1"}],
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        doc = run_tick(tasks, tmp, now)
    assert doc["stuck_tasks"]["count"] == 1, doc
    entry = doc["stuck_tasks"]["tasks"][0]
    assert entry["task_id"] == "task-stuck"
    assert entry["last_note"] == "credit accountant rejected auto-fix attempt 1"
    assert entry["blocked_seconds"] >= dispatch_tick.STUCK_TASK_THRESHOLD_SECONDS
    print("PASS: stuck task past threshold is detected, with real evidence")


def test_recently_blocked_task_not_flagged():
    now = datetime.datetime(2026, 8, 2, 8, 0, 0, tzinfo=datetime.timezone.utc)
    recent_ts = now - datetime.timedelta(seconds=60)  # well under threshold
    tasks = {
        "task-fresh-block": {
            "status": "blocked",
            "last_checkpoint_at": _iso(recent_ts),
            "checkpoints": [{"note": "just blocked"}],
        },
    }
    with tempfile.TemporaryDirectory() as tmp:
        doc = run_tick(tasks, tmp, now)
    assert doc["stuck_tasks"]["count"] == 0, doc
    assert doc["heartbeat"]["counts"]["blocked"] == 1, "still counted in the real blocked total"
    print("PASS: a task blocked for under the threshold is counted but not flagged as stuck")


def test_blocked_task_with_no_checkpoint_timestamp_is_skipped_honestly():
    now = datetime.datetime(2026, 8, 2, 8, 0, 0, tzinfo=datetime.timezone.utc)
    tasks = {
        "task-no-ts": {"status": "blocked"},  # no last_checkpoint_at at all
    }
    with tempfile.TemporaryDirectory() as tmp:
        doc = run_tick(tasks, tmp, now)
    assert doc["stuck_tasks"]["count"] == 0, "must not fabricate a stuck finding with no real evidence"
    assert doc["heartbeat"]["counts"]["blocked"] == 1
    print("PASS: a blocked task with no real checkpoint timestamp is never guessed at")


def test_never_auto_resolves_or_mutates_task_state():
    now = datetime.datetime(2026, 8, 2, 8, 0, 0, tzinfo=datetime.timezone.utc)
    old_ts = now - datetime.timedelta(seconds=dispatch_tick.STUCK_TASK_THRESHOLD_SECONDS + 1)
    task_doc = {
        "status": "blocked",
        "last_checkpoint_at": _iso(old_ts),
        "checkpoints": [{"note": "real block reason"}],
    }
    tasks = {"task-untouched": dict(task_doc)}
    with tempfile.TemporaryDirectory() as tmp:
        run_tick(tasks, tmp, now)
    # The exact same dict object/content must be unchanged -- this function
    # reports, it never mutates the task doc or resolves the block itself.
    assert tasks["task-untouched"] == task_doc, "task doc was mutated -- must be strictly read-only"
    print("PASS: task state is never mutated, resolved, or dispatched by this function")


def test_heartbeat_fields_real():
    now = datetime.datetime(2026, 8, 2, 8, 0, 0, tzinfo=datetime.timezone.utc)
    tasks = {
        "task-running": {"status": "in_progress"},
        "task-blocked": {"status": "blocked"},
        "task-done": {"status": "completed"},
    }
    with tempfile.TemporaryDirectory() as tmp:
        doc_no_stop = run_tick(tasks, tmp, now, emergency_stop=False)
        doc_with_stop = run_tick(tasks, tmp, now, emergency_stop=True)

    hb = doc_no_stop["heartbeat"]
    assert hb["ts"] == now.isoformat()
    assert hb["emergency_stop"] is False
    assert hb["counts"]["blocked"] == 1
    assert hb["counts"]["running_or_in_progress"] == 1
    assert hb["counts"]["total_tasks"] == 3
    assert isinstance(hb["load_average"], list) and len(hb["load_average"]) == 3

    assert doc_with_stop["heartbeat"]["emergency_stop"] is True
    print("PASS: heartbeat reports real timestamp, load average, EMERGENCY_STOP state, and counts")


def test_atomic_write_leaves_no_temp_file_and_is_valid_json():
    now = datetime.datetime(2026, 8, 2, 8, 0, 0, tzinfo=datetime.timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        doc = run_tick({}, tmp, now)
        # run_tick() itself already asserts no .tmp file and that the file
        # round-trips through real json.load -- this test exists to name
        # that guarantee explicitly, not just as a side effect of run_tick.
        assert doc["stuck_tasks"]["count"] == 0
        assert doc["heartbeat"]["counts"]["total_tasks"] == 0
    print("PASS: write is atomic (no .tmp left behind) and produces valid JSON")


if __name__ == "__main__":
    tests = [
        test_stuck_task_detected_past_threshold,
        test_recently_blocked_task_not_flagged,
        test_blocked_task_with_no_checkpoint_timestamp_is_skipped_honestly,
        test_never_auto_resolves_or_mutates_task_state,
        test_heartbeat_fields_real,
        test_atomic_write_leaves_no_temp_file_and_is_valid_json,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR: {t.__name__}: {type(e).__name__}: {e}")
    if failed:
        print(f"\n{failed}/{len(tests)} test(s) failed")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed")
    sys.exit(0)
