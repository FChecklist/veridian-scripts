#!/usr/bin/env python3
"""Real tests for recover-failed-workers.py -- the RCA recovery script that
resets veridian-worker@ systemd units stuck 'failed' from the pre-fix
OpenRouter-402-balance bug, but ONLY when the task's own result.json
confirms that exact signature (never a blind reset of an unrelated failure).
SPEC: owner absolute stop-work order, task-20260806-165921, first real test
batch for the ~101-script untested gap, UMR-20260806-175915-8f47.

Real unit tests against the real module functions (imported via importlib,
this script has a hyphenated filename), with subprocess/filesystem
dependencies replaced only at the seam the script itself exposes
(no systemctl calls, no /opt/veridian/ai-os/tasks/ writes) -- is_402_balance_
failure and task_id_from_unit are exercised against real temp files /
real strings, never a reimplementation of the script's own logic.
"""
import importlib.util
import json
import os
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _load():
    spec = importlib.util.spec_from_file_location(
        "recover_failed_workers_test", os.path.join(SCRIPTS_DIR, "recover-failed-workers.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_task_id_from_unit_strips_prefix_and_suffix():
    mod = _load()
    assert mod.task_id_from_unit("veridian-worker@task-20260720-123456-abcd.service") == \
        "task-20260720-123456-abcd"


def test_is_402_balance_failure_true_for_real_signature(monkeypatch):
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        task_id = "task-real-402"
        task_dir = os.path.join(d, task_id)
        os.makedirs(task_dir)
        real_result_path = os.path.join(task_dir, "result.json")
        # the real check is a literal substring match for '"api_error_status":402'
        # (compact, no space) -- write real, compact-separator JSON so this
        # exercises the exact real signature the script matches, not a
        # convenient stand-in.
        with open(real_result_path, "w") as f:
            f.write(json.dumps({"api_error_status": 402, "detail": "insufficient credits"},
                                separators=(",", ":")))

        real_open = open
        hardcoded_path = f"/opt/veridian/ai-os/tasks/{task_id}/result.json"

        def fake_open(path, *a, **kw):
            if path == hardcoded_path:
                path = real_result_path
            return real_open(path, *a, **kw)

        monkeypatch.setattr("builtins.open", fake_open)
        assert mod.is_402_balance_failure(task_id) is True


def test_is_402_balance_failure_false_for_unrelated_failure(monkeypatch):
    mod = _load()
    with tempfile.TemporaryDirectory() as d:
        task_id = "task-real-other-failure"
        task_dir = os.path.join(d, task_id)
        os.makedirs(task_dir)
        real_result_path = os.path.join(task_dir, "result.json")
        with open(real_result_path, "w") as f:
            json.dump({"error": "some other real crash, not a balance issue"}, f)

        real_open = open
        hardcoded_path = f"/opt/veridian/ai-os/tasks/{task_id}/result.json"

        def fake_open(path, *a, **kw):
            if path == hardcoded_path:
                path = real_result_path
            return real_open(path, *a, **kw)

        monkeypatch.setattr("builtins.open", fake_open)
        assert mod.is_402_balance_failure(task_id) is False


def test_is_402_balance_failure_false_when_result_json_missing():
    mod = _load()
    # a task id guaranteed to have no real result.json on this box
    assert mod.is_402_balance_failure("task-does-not-exist-recover-failed-workers-test") is False


def test_list_failed_units_filters_to_veridian_worker_prefix(monkeypatch):
    mod = _load()
    fake_stdout = (
        "failed veridian-worker@task-a.service      failed  failed  \n"
        "failed veridian-docworker@task-b.service    failed  failed  \n"
        "failed veridian-worker@task-c.service      failed  failed  \n"
    )

    class FakeCompleted:
        stdout = fake_stdout

    def fake_run(cmd, **kw):
        assert cmd[0] == "systemctl"
        assert "--state=failed" in cmd
        return FakeCompleted()

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    units = mod.list_failed_units()
    assert units == ["veridian-worker@task-a.service", "veridian-worker@task-c.service"]


def test_main_dry_run_does_not_call_reset_or_start(monkeypatch, capsys):
    mod = _load()
    mod.DRY_RUN = True

    monkeypatch.setattr(mod, "list_failed_units", lambda: ["veridian-worker@task-x.service"])
    monkeypatch.setattr(mod, "task_id_from_unit", lambda u: "task-x")
    monkeypatch.setattr(mod, "is_402_balance_failure", lambda tid: True)

    calls = []
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **kw: calls.append(a))

    mod.main()
    assert calls == [], "DRY_RUN must never invoke systemctl reset-failed/start"
    out = capsys.readouterr().out
    assert "[DRY RUN] would reset-failed + start: veridian-worker@task-x.service" in out
    assert "Recovered (confirmed 402 balance failure, reset+restarted): 1" in out


def test_main_skips_units_without_confirmed_signature(monkeypatch, capsys):
    mod = _load()
    mod.DRY_RUN = True
    monkeypatch.setattr(mod, "list_failed_units", lambda: ["veridian-worker@task-y.service"])
    monkeypatch.setattr(mod, "task_id_from_unit", lambda u: "task-y")
    monkeypatch.setattr(mod, "is_402_balance_failure", lambda tid: False)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("must not touch systemctl for a non-confirmed unit")))

    mod.main()
    out = capsys.readouterr().out
    assert "Recovered (confirmed 402 balance failure, reset+restarted): 0" in out
    assert "Skipped (no confirmed 402 pattern -- needs manual review, NOT touched): 1" in out
    assert "veridian-worker@task-y.service" in out


if __name__ == "__main__":
    test_task_id_from_unit_strips_prefix_and_suffix()
    test_is_402_balance_failure_false_when_result_json_missing()
    print("PASS: core recover-failed-workers.py smoke tests (run pytest for full monkeypatch-based coverage)")
