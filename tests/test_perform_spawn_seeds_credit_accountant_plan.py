#!/usr/bin/env python3
"""Real regression test for UMR-20260814-045316 (P0 fleet-wide report-approval
gate deadlock) -- the CALLER-side half of the fix, see
tests/test_credit_accountant_report_approval.py's own module docstring for
the full root-cause writeup (credit-accountant.py's own matching logic was
always correct; the real defect was that resource_governor.py's
_perform_spawn() -- the real, mechanical spawn path every queued
task_kind='veridian_task_create' row goes through, including
dispatch-owner-task.sh's owner_dispatch_gateway -- never called
`credit-accountant.py propose` for increment 1).

This test proves _perform_spawn()'s veridian_task_create branch now DOES
call `credit-accountant.py propose --task-id <new_task_id> ...` immediately
after minting task_id, and that a real spawn still succeeds/fails on
veridian-task.py's own outcome regardless of the propose call's own verdict
(fail-open on the seed step itself, per this fix's own design -- report-time
is the real enforcement point).

Every subprocess boundary is mocked via resource_governor.py's own `_run()`
(same convention as tests/test_veridian_task_create_stderr_and_reason.py) --
no real `claude -p` / OpenRouter / systemctl call is ever made.
"""
import importlib.util
import json
import os
import sys

from unittest import mock

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)


def _load_rg(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, "resource_governor.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_perform_spawn_calls_credit_accountant_propose_for_new_task_id():
    rg = _load_rg("rg_credit_seed_calls")

    row = {
        "task_kind": "veridian_task_create",
        "unit_name": None,
        "inputs_json": json.dumps({
            "title": "regression test task", "prompt": "## OBJECTIVE\ndo the real thing\n",
            "repo": "veridian-scripts",
        }),
    }

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        joined = " ".join(cmd)
        if "veridian-task.py" in joined:
            return _FakeCompletedProcess(0, "CREATED: task-20260814-fake-new-id\n", "")
        if "credit-accountant.py" in joined:
            assert "propose" in cmd
            assert "--task-id" in cmd
            assert cmd[cmd.index("--task-id") + 1] == "task-20260814-fake-new-id"
            return _FakeCompletedProcess(0, json.dumps({
                "approved": True, "increment_number": 1, "reason": "test stub", "reviewer": "test",
            }), "")
        if cmd and cmd[0] == "systemctl":
            return _FakeCompletedProcess(0, "", "")
        return _FakeCompletedProcess(0, "", "")

    with mock.patch.object(rg, "_run", side_effect=fake_run):
        result = rg._perform_spawn(row)

    assert result["status"] == "running", result
    assert result["outputs"]["new_task_id"] == "task-20260814-fake-new-id"

    propose_calls = [c for c in calls if "credit-accountant.py" in " ".join(c)]
    assert len(propose_calls) == 1, f"expected exactly one credit-accountant.py propose call, got: {calls}"
    assert result["outputs"]["credit_accountant_propose"]["approved"] is True

    # Real ordering requirement: propose must run AFTER task_id exists (it
    # needs a real --task-id) and BEFORE (or at least alongside) the unit
    # start -- never skipped entirely.
    veridian_task_idx = next(i for i, c in enumerate(calls) if "veridian-task.py" in " ".join(c))
    propose_idx = next(i for i, c in enumerate(calls) if "credit-accountant.py" in " ".join(c))
    assert propose_idx > veridian_task_idx


def test_perform_spawn_still_succeeds_when_credit_accountant_propose_is_rejected():
    """Fail-open on the seed step itself, by design (see
    _seed_credit_accountant_plan()'s own module-level comment): a rejected
    or unreachable propose() call must never block the real spawn --
    credit-accountant.py's own report-time check remains the real
    enforcement point."""
    rg = _load_rg("rg_credit_seed_failopen")

    row = {
        "task_kind": "veridian_task_create",
        "unit_name": None,
        "inputs_json": json.dumps({"title": "t", "prompt": "p", "repo": "veridian-scripts"}),
    }

    def fake_run(cmd, **kwargs):
        joined = " ".join(cmd)
        if "veridian-task.py" in joined:
            return _FakeCompletedProcess(0, "CREATED: task-20260814-fake-id-2\n", "")
        if "credit-accountant.py" in joined:
            return _FakeCompletedProcess(1, json.dumps({"approved": False, "reason": "test stub rejection"}), "")
        return _FakeCompletedProcess(0, "", "")

    with mock.patch.object(rg, "_run", side_effect=fake_run):
        result = rg._perform_spawn(row)

    assert result["status"] == "running", result
    assert result["outputs"]["credit_accountant_propose"]["approved"] is False


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
    if failed:
        print(f"{failed}/{len(tests)} FAILED")
        sys.exit(1)
    print(f"ALL {len(tests)} PASSED")
