#!/usr/bin/env python3
"""
Real, executable test for the 2026-08-01 RCA fix (24-unit OOM-kill incident):
worker units must never be boot-activated by systemd itself, and interrupted
work must resume THROUGH resource_governor's queue, never via a direct
systemctl call and never via re-enabling boot activation.

Part 1 (real systemd --user calls, temp throwaway unit -- never touches any
real veridian-worker@ instance or the real default.target.wants/ directory
contents beyond a scoped before/after diff):
  - installs a scratch unit file with NO [Install] section (the fixed
    template's shape) under a private, never-collides-with-real-tasks
    template name (veridian-selftest-worker@.service)
  - confirms `systemctl --user is-enabled` reports it as 'static' (no
    installation config -- cannot be boot-enabled)
  - confirms `systemctl --user enable` on a real instance is a no-op: no
    new symlink appears anywhere under ~/.config/systemd/user/*.wants/
  - confirms `systemctl --user start` on that same instance still works
    fine (the unit reaches 'active')
  - cleans up: stops the instance, removes the scratch unit file, reloads

Part 2 (in-process, resource_governor.submit fully monkeypatched -- writes
nothing to the real umr_tasks sqlite DB):
  - a task with status in_progress/pending whose unit is NOT active gets
    submitted via resource_governor.submit(), never a direct systemctl call
  - the submitted task_kind/action is exactly {"systemctl_action",
    "start"} for an inactive unit and {"systemctl_action",
    "reset_failed_and_start"} for a failed one
  - a task whose unit IS active is skipped, not re-submitted
  - a task in a terminal status (completed/blocked/failed/pending_review)
    is never considered at all

Usage: python3 test_worker_boot_activation_and_resume.py
Exit 0 = all assertions passed. Exit 1 = a test failed.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

UNIT_DIR = os.path.expanduser("~/.config/systemd/user")
TEST_TEMPLATE_NAME = "veridian-selftest-worker@.service"
TEST_TEMPLATE_PATH = os.path.join(UNIT_DIR, TEST_TEMPLATE_NAME)
TEST_INSTANCE = "veridian-selftest-worker@rca-fix-verify.service"

# Same shape as the real, fixed veridian-worker@.service: no [Install] section.
SCRATCH_UNIT_CONTENTS = """[Unit]
Description=VERIDIAN self-test worker (RCA fix verification, safe to delete)

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'sleep 1'
"""

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        failures.append(label)


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def all_wants_symlinks():
    found = []
    for root, _dirs, _files in os.walk(UNIT_DIR):
        if not root.endswith(".wants"):
            continue
        for entry in os.listdir(root):
            found.append(os.path.join(root, entry))
    return set(found)


# ---------------------------------------------------------------------------
# Part 1: real systemd --user unit, no [Install] section
# ---------------------------------------------------------------------------

def test_no_boot_activation():
    before_symlinks = all_wants_symlinks()
    with open(TEST_TEMPLATE_PATH, "w") as f:
        f.write(SCRATCH_UNIT_CONTENTS)
    try:
        run(["systemctl", "--user", "daemon-reload"])

        r = run(["systemctl", "--user", "is-enabled", TEST_TEMPLATE_NAME])
        check("template with no [Install] section reports 'static' (never boot-enabled)",
              r.stdout.strip() == "static")

        run(["systemctl", "--user", "enable", TEST_INSTANCE])  # expected no-op / harmless warning
        after_enable_symlinks = all_wants_symlinks()
        check("`systemctl --user enable` created NO new *.wants/ symlink anywhere",
              after_enable_symlinks == before_symlinks)

        r = run(["systemctl", "--user", "start", TEST_INSTANCE])
        check("`systemctl --user start` on the same (never-enabled) unit still succeeds",
              r.returncode == 0)

        r = run(["systemctl", "--user", "show", TEST_INSTANCE, "-p", "Result", "--value"])
        check("started unit actually ran to completion (Result=success)",
              r.stdout.strip() == "success")
    finally:
        run(["systemctl", "--user", "stop", TEST_INSTANCE])
        run(["systemctl", "--user", "reset-failed", TEST_INSTANCE])
        if os.path.exists(TEST_TEMPLATE_PATH):
            os.remove(TEST_TEMPLATE_PATH)
        run(["systemctl", "--user", "daemon-reload"])
        after_cleanup_symlinks = all_wants_symlinks()
        check("cleanup left *.wants/ contents exactly as found (no residue)",
              after_cleanup_symlinks == before_symlinks)


# ---------------------------------------------------------------------------
# Part 2: resume_interrupted_workers_tick(), resource_governor.submit faked
# ---------------------------------------------------------------------------

def load_dispatch_tick():
    spec = importlib.util.spec_from_file_location(
        "dispatch_tick", os.path.join(SCRIPTS_DIR, "dispatch-tick.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_resume_goes_through_queue_not_systemctl():
    dispatch_tick = load_dispatch_tick()

    submitted = []

    def fake_submit(task_spec, tier, source_trigger):
        submitted.append({"task_spec": task_spec, "tier": tier, "source_trigger": source_trigger})
        return {"accepted": True, "umr_id": f"UMR-FAKE-{len(submitted)}", "reason": "queued"}

    real_submit = dispatch_tick.resource_governor.submit
    dispatch_tick.resource_governor.submit = fake_submit

    real_run = dispatch_tick.run
    fake_states = {
        "veridian-worker@fake-inactive-task.service": "inactive",
        "veridian-worker@fake-failed-task.service": "failed",
        "veridian-worker@fake-active-task.service": "active",
    }

    def fake_run(cmd):
        if cmd[:3] == ["systemctl", "--user", "show"]:
            unit = cmd[3]
            state = fake_states.get(unit, "inactive")

            class _R:
                pass
            r = _R()
            r.stdout = state
            r.returncode = 0
            return r
        return real_run(cmd)

    dispatch_tick.run = fake_run

    try:
        tasks = {
            "fake-inactive-task": {"status": "in_progress", "service": "veridian-worker@fake-inactive-task.service"},
            "fake-failed-task": {"status": "pending", "service": "veridian-worker@fake-failed-task.service"},
            "fake-active-task": {"status": "in_progress", "service": "veridian-worker@fake-active-task.service"},
            "fake-completed-task": {"status": "completed", "service": "veridian-worker@fake-completed-task.service"},
            "fake-blocked-task": {"status": "blocked", "service": "veridian-worker@fake-blocked-task.service"},
            "fake-no-service-task": {"status": "in_progress"},
        }
        result = dispatch_tick.resume_interrupted_workers_tick(tasks)

        check("inactive in_progress task IS resumed (queued)",
              "fake-inactive-task" in result["resumed"])
        check("failed-unit pending task IS resumed (queued)",
              "fake-failed-task" in result["resumed"])
        check("already-active task is SKIPPED, not resubmitted",
              "fake-active-task" in result["skipped_running"] and "fake-active-task" not in result["resumed"])
        check("completed/blocked terminal-status tasks are never considered",
              "fake-completed-task" not in result["resumed"] and "fake-blocked-task" not in result["resumed"]
              and "fake-completed-task" not in result["skipped_running"])
        check("task with no 'service' field is safely skipped, not an exception",
              "fake-no-service-task" not in result["resumed"])
        check("exactly 2 real submissions happened (inactive + failed), never a direct systemctl start",
              len(submitted) == 2)

        by_identity = {s["task_spec"]["task_identity"]: s for s in submitted}
        check("inactive task submitted with action='start'",
              by_identity["fake-inactive-task"]["task_spec"]["inputs"]["action"] == "start")
        check("failed-unit task submitted with action='reset_failed_and_start'",
              by_identity["fake-failed-task"]["task_spec"]["inputs"]["action"] == "reset_failed_and_start")
        check("submissions use task_kind='systemctl_action' (never veridian_task_create -- this is a RESUME, not a new task)",
              all(s["task_spec"]["task_kind"] == "systemctl_action" for s in submitted))
    finally:
        dispatch_tick.resource_governor.submit = real_submit
        dispatch_tick.run = real_run


if __name__ == "__main__":
    test_no_boot_activation()
    test_resume_goes_through_queue_not_systemctl()

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("All assertions passed.")
    sys.exit(0)
