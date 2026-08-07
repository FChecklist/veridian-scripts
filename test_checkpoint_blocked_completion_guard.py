#!/usr/bin/env python3
"""
Real, executable regression test for the cmd_checkpoint() completed-status
guard added under UMR-20260806-141055-1fec / UMR-20260807-074739-dde3.

Root cause this proves is fixed: task-20260806-193955-deterministic-final-
audit--zero-gap-zero's own task.yaml reached `status: completed` at its top
level while its own last checkpoint's remaining_steps still opened with
"**BLOCKED on gate**..." -- i.e. its self-declared blocker was never
resolved, yet the supervisor's approve+merge flow stamped it completed
anyway. See veridian-task.py's cmd_checkpoint() for the real fix and its
full rationale (including why a blanket "remaining_steps must be empty"
rule was deliberately rejected -- it would break the vast majority of this
platform's own established real completions).

Isolation convention matches this repo's own test_ocid063_handoff_envelope.py
/ test_stuck_task_heartbeat.py: real functions in veridian-task.py exercised
in-process, AI_OS monkey-patched to an isolated scratch dir so nothing here
ever touches the real production /opt/veridian/ai-os/tasks tree or
CONTROLLER.yaml.

Usage: python3 test_checkpoint_blocked_completion_guard.py
Exit 0 = all assertions passed. Exit 1 = a test failed.
"""
import argparse
import importlib.util
import os
import shutil
import sys
import tempfile

import pytest
import yaml

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        failures.append(label)


def load_veridian_task():
    spec = importlib.util.spec_from_file_location(
        "veridian_task", os.path.join(SCRIPTS_DIR, "veridian-task.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def vt():
    return load_veridian_task()


def _make_task(task_id, remaining_steps, prev_checkpoint_remaining_steps):
    """A real, minimal task dict shaped like a genuine task.yaml, already
    past the pending_review guard (a pending_review checkpoint exists) and
    with a deliberately-nonexistent workspace path so cmd_checkpoint's
    git/parse_progress_md block is skipped entirely (os.path.isdir is
    False) -- the loaded remaining_steps/completed_steps are used exactly
    as given, not re-derived, which is all this guard's own logic needs."""
    return {
        "id": task_id,
        "title": "regression fixture",
        "status": "pending_review",
        "repo": "veridian-scripts",
        "branch": f"worker/{task_id}",
        "workspace": "/nonexistent/scratch-workspace-for-this-test-only",
        "task_dir": f"/nonexistent/{task_id}",
        "service": f"veridian-worker@{task_id}.service",
        "created_at": "2026-08-06T00:00:00+00:00",
        "last_checkpoint_at": "2026-08-06T00:01:00+00:00",
        "files_modified": [],
        "completed_steps": ["Gate check: independently queried the register"],
        "remaining_steps": remaining_steps,
        "checkpoints": [
            {
                "at": "2026-08-06T00:00:00+00:00",
                "status": "in_progress",
                "files_modified": [],
                "completed_steps": [],
                "remaining_steps": ["Not started"],
                "recent_commits": [],
                "note": "worker started",
            },
            {
                "at": "2026-08-06T00:00:30+00:00",
                "status": "pending_review",
                "files_modified": [],
                "completed_steps": ["Gate check: independently queried the register"],
                "remaining_steps": prev_checkpoint_remaining_steps,
                "recent_commits": [],
                "note": "quality gates passed, pushed, awaiting review",
            },
        ],
        "execution_seconds": 60,
        "restart_count": 0,
    }


def _run_checkpoint_in_scratch(vt, task_id, task_dict):
    real_ai_os = vt.AI_OS
    scratch_dir = tempfile.mkdtemp(prefix="checkpoint-blocked-guard-test-")
    task_dir = os.path.join(scratch_dir, "tasks", task_id)
    os.makedirs(task_dir)
    with open(os.path.join(task_dir, "task.yaml"), "w") as f:
        yaml.safe_dump(task_dict, f, sort_keys=False, default_flow_style=False)
    try:
        vt.AI_OS = scratch_dir
        args = argparse.Namespace(
            task_id=task_id, status="completed", note=None, auto=False,
            handoff_envelope=None, evidence_json=None,
        )
        vt.cmd_checkpoint(args)
        return "succeeded", None
    except SystemExit as e:
        return "system_exit", e.code
    finally:
        vt.AI_OS = real_ai_os
        shutil.rmtree(scratch_dir, ignore_errors=True)


def test_persisted_unresolved_blocked_marker_is_refused(vt):
    """The exact real defect: remaining_steps' first entry is identical,
    still-open "BLOCKED" text at both the pending_review checkpoint and the
    attempted completed checkpoint -- must be refused via sys.exit(1), never
    silently accepted."""
    blocked_steps = [
        "**BLOCKED on gate**: SPEC requires BOTH sibling UMRs to show `status=completed` before any",
        "Once gate clears: check `capability_registry` + past `umr_tasks` for an existing",
    ]
    outcome, code = _run_checkpoint_in_scratch(
        vt, "regression-persisted-blocked",
        _make_task("regression-persisted-blocked", blocked_steps, blocked_steps),
    )
    check("a persisted, unresolved leading BLOCKED marker is refused via SystemExit(1)",
          outcome == "system_exit" and code == 1)


def test_resolved_blocker_is_allowed(vt):
    """Once the blocker is actually lifted (remaining_steps changed from the
    prior checkpoint -- e.g. gate cleared, or reworded to the established
    'None -- ...' closing convention), completion must proceed normally."""
    prev_steps = [
        "**BLOCKED on gate**: SPEC requires BOTH sibling UMRs to show `status=completed` before any",
    ]
    resolved_steps = ["None -- gate cleared, audit ran for real, ALL_CLEAR verdict posted."]
    outcome, code = _run_checkpoint_in_scratch(
        vt, "regression-resolved-blocker",
        _make_task("regression-resolved-blocker", resolved_steps, prev_steps),
    )
    check("a resolved/reworded blocker (remaining_steps changed) is NOT refused by this guard",
          outcome != "system_exit" or code != 1)


def test_freshly_discovered_blocker_is_allowed(vt):
    """A BLOCKED marker that appears for the FIRST time only in the final
    checkpoint (not yet persisted across a prior checkpoint) is a fresh
    finding, not a proven-unresolved one -- must not be refused by this
    narrow guard (it may still be caught by other review, but that's a
    separate concern from this specific regression)."""
    prev_steps = ["Not started"]
    fresh_blocked_steps = ["**BLOCKED: newly discovered external dependency**"]
    outcome, code = _run_checkpoint_in_scratch(
        vt, "regression-fresh-blocker",
        _make_task("regression-fresh-blocker", fresh_blocked_steps, prev_steps),
    )
    check("a freshly-discovered (not yet persisted) BLOCKED marker is NOT refused by this guard",
          outcome != "system_exit" or code != 1)


def test_nontrivial_non_blocked_remaining_steps_allowed(vt):
    """The established, legitimate convention (non-empty, non-BLOCKED
    remaining_steps used as a closing note) must keep working exactly as
    before -- this guard must never regress the dominant real pattern."""
    steps = ["None -- task complete, see PROGRESS.md for full evidence."]
    outcome, code = _run_checkpoint_in_scratch(
        vt, "regression-legit-closing-note",
        _make_task("regression-legit-closing-note", steps, steps),
    )
    check("a legitimate non-BLOCKED closing note (unchanged or not) is NOT refused by this guard",
          outcome != "system_exit" or code != 1)


if __name__ == "__main__":
    veridian_task = load_veridian_task()

    test_persisted_unresolved_blocked_marker_is_refused(veridian_task)
    test_resolved_blocker_is_allowed(veridian_task)
    test_freshly_discovered_blocker_is_allowed(veridian_task)
    test_nontrivial_non_blocked_remaining_steps_allowed(veridian_task)

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("All assertions passed.")
    sys.exit(0)
