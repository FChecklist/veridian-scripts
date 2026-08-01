#!/usr/bin/env python3
"""
Real, executable test for ADVTEST6-CONTROL-OK (UMR-20260728-224529-df61):
resource_governor.py's _perform_spawn(), given a systemctl_action row whose
unit_name is None/empty, must return a clean {"status": "failed", ...} dict
instead of crashing with TypeError (expected str, bytes or os.PathLike
object, not NoneType) from subprocess.run(["systemctl", ..., None]).

A malformed queue row should fail its own task, not take down the whole
dispatch tick that's processing every other queued row too.

Usage: python3 test_perform_spawn_none_unit_name.py
Exit 0 = all assertions passed. Exit 1 = a test failed.
"""
import importlib.util
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        failures.append(label)


def load_resource_governor():
    spec = importlib.util.spec_from_file_location(
        "resource_governor_test", os.path.join(SCRIPTS_DIR, "resource_governor.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_none_unit_name_fails_clean_not_crash():
    rg = load_resource_governor()
    row = {"task_kind": "systemctl_action", "unit_name": None, "inputs_json": {"action": "start"}}
    try:
        result = rg._perform_spawn(row)
    except TypeError as e:
        check(f"None unit_name does not raise TypeError (got: {e})", False)
        return
    check("None unit_name returns a dict, not a raise", isinstance(result, dict))
    check("None unit_name -> status is 'failed'", result.get("status") == "failed")
    check("None unit_name -> error message present in outputs", "unit_name" in result.get("outputs", {}).get("error", ""))


def test_empty_string_unit_name_fails_clean_not_crash():
    rg = load_resource_governor()
    row = {"task_kind": "systemctl_action", "unit_name": "", "inputs_json": {"action": "restart"}}
    try:
        result = rg._perform_spawn(row)
    except TypeError as e:
        check(f"Empty-string unit_name does not raise TypeError (got: {e})", False)
        return
    check("Empty-string unit_name -> status is 'failed'", result.get("status") == "failed")


def test_real_unit_name_still_works():
    rg = load_resource_governor()
    real_run = rg._run
    calls = []

    def fake_run(cmd):
        calls.append(cmd)

        class _R:
            returncode = 0
            stderr = ""
        return _R()

    rg._run = fake_run
    try:
        row = {"task_kind": "systemctl_action", "unit_name": "veridian-worker@some-task.service",
               "inputs_json": {"action": "start"}}
        result = rg._perform_spawn(row)
        check("Real unit_name still dispatches via systemctl", any("veridian-worker@some-task.service" in c for c in calls[-1]))
        check("Real unit_name -> status is 'running' on returncode 0", result.get("status") == "running")
    finally:
        rg._run = real_run


if __name__ == "__main__":
    test_none_unit_name_fails_clean_not_crash()
    test_empty_string_unit_name_fails_clean_not_crash()
    test_real_unit_name_still_works()

    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("All tests passed.")
    sys.exit(0)
