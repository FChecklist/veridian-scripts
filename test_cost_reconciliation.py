#!/usr/bin/env python3
"""Real tests for cost-reconciliation.py. No database is involved -- this
script reads a real cost-log JSONL file and a real CONTROLLER.yaml file, both
hardcoded module-level path constants with no env-var override. Per the
established convention, the module is importlib-loaded normally and then the
loaded module's REAL_COST_LOG / CONTROLLER globals are monkeypatched directly
to real temp files (never the live paths under /opt/veridian/ai-os) before
calling any of its functions.

Note: main()'s "repeat failure signature" check uses a hardcoded LOCAL
variable `tasks_dir = "/opt/veridian/ai-os/tasks"` inside main() itself
(not a module-level constant), so it cannot be monkeypatched from a test
without editing the script (which this task forbids). That block only does
a read-only os.listdir/open scan of that real directory -- no writes -- so
it is safe to leave as-is; tests below do not assert on its output either
way, only on the deterministic sections driven by the injected
REAL_COST_LOG/CONTROLLER paths.
"""
import importlib.util
import json
import os

import pytest
import yaml

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SUT_PATH = os.path.join(SCRIPTS_DIR, "cost-reconciliation.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def cr_mod(tmp_path):
    mod = _load("cost_reconciliation_sut", SUT_PATH)
    mod.REAL_COST_LOG = str(tmp_path / "glm-proxy-calls.jsonl")
    mod.CONTROLLER = str(tmp_path / "CONTROLLER.yaml")
    return mod


def _write_jsonl(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


# ---------------------------------------------------------------------------
# load_cost_log
# ---------------------------------------------------------------------------

def test_load_cost_log_parses_valid_lines_and_skips_malformed(cr_mod):
    with open(cr_mod.REAL_COST_LOG, "w") as f:
        f.write(json.dumps({"ts": "2026-07-21T00:00:00", "real_cost_usd": 0.01, "cache_hit": False}) + "\n")
        f.write("{not valid json\n")
        f.write(json.dumps({"ts": "2026-07-21T00:01:00", "real_cost_usd": 0.02, "cache_hit": True}) + "\n")

    rows = cr_mod.load_cost_log()
    assert len(rows) == 2
    assert rows[0]["real_cost_usd"] == 0.01
    assert rows[1]["cache_hit"] is True


def test_load_cost_log_missing_file_returns_empty_list(cr_mod):
    # REAL_COST_LOG points at a temp path that was never created.
    assert cr_mod.load_cost_log() == []


# ---------------------------------------------------------------------------
# load_tasks
# ---------------------------------------------------------------------------

def test_load_tasks_parses_real_yaml(cr_mod):
    with open(cr_mod.CONTROLLER, "w") as f:
        yaml.safe_dump({"tasks": [
            {"id": "t1", "status": "completed"},
            {"id": "t2", "status": "failed"},
        ]}, f)
    tasks = cr_mod.load_tasks()
    assert len(tasks) == 2
    assert {t["id"] for t in tasks} == {"t1", "t2"}
    statuses = {t["id"]: t["status"] for t in tasks}
    assert statuses["t1"] == "completed"
    assert statuses["t2"] == "failed"


def test_load_tasks_missing_file_returns_empty_list(cr_mod):
    assert cr_mod.load_tasks() == []


def test_load_tasks_missing_tasks_key_returns_empty_list(cr_mod):
    with open(cr_mod.CONTROLLER, "w") as f:
        yaml.safe_dump({"other_key": True}, f)
    assert cr_mod.load_tasks() == []


# ---------------------------------------------------------------------------
# main() -- the real entry point, end to end, capturing real printed output
# ---------------------------------------------------------------------------

def test_main_computes_totals_and_since_deployment_cache_hit_rate(cr_mod, capsys):
    # Pre-cache rows (before CACHE_DEPLOYED_AT) must be counted in the
    # all-time total but excluded from the "since deployment" cache-hit-rate
    # window.
    rows = [
        {"ts": "2026-07-01T00:00:00", "real_cost_usd": 0.10, "cache_hit": False},
        {"ts": "2026-07-01T00:01:00", "real_cost_usd": 0.05, "cache_hit": False},
        # since-deployment window (CACHE_DEPLOYED_AT == 2026-07-20T05:34:28)
        {"ts": "2026-07-21T00:00:00", "real_cost_usd": 0.02, "cache_hit": True},
        {"ts": "2026-07-21T00:01:00", "real_cost_usd": 0.02, "cache_hit": True},
        {"ts": "2026-07-21T00:02:00", "real_cost_usd": 0.03, "cache_hit": False},
    ]
    _write_jsonl(cr_mod.REAL_COST_LOG, rows)
    with open(cr_mod.CONTROLLER, "w") as f:
        yaml.safe_dump({"tasks": [
            {"id": "t1", "status": "completed"},
            {"id": "t2", "status": "completed"},
            {"id": "t3", "status": "completed"},
            {"id": "t4", "status": "failed"},
        ]}, f)

    cr_mod.main()
    out = capsys.readouterr().out

    assert "Total logged cost:      $0.2200" in out
    assert "Total calls (all-time): 5  (3 real, 2 cache hits)" in out
    assert "Calls since cache deployed (2026-07-20T05:34:28): 3" in out
    # 2 hits / 3 since-deployment rows = 66.7%
    assert "Cache hit rate (since deployment, the only fair number): 2/3 = 66.7%" in out
    assert "completed            3" in out
    assert "failed               1" in out
    # 1/4 = 25.0% failure rate; below the 20% ALARM threshold? 25 > 20 -> ALARM.
    assert "Failure rate: 1/4 = 25.0%" in out
    assert "ALARM: failure rate above 20%" in out


def test_main_no_alarm_when_failure_rate_at_or_below_20_percent(cr_mod, capsys):
    _write_jsonl(cr_mod.REAL_COST_LOG, [])
    with open(cr_mod.CONTROLLER, "w") as f:
        yaml.safe_dump({"tasks": [
            {"id": "t1", "status": "completed"},
            {"id": "t2", "status": "completed"},
            {"id": "t3", "status": "completed"},
            {"id": "t4", "status": "completed"},
            {"id": "t5", "status": "failed"},
        ]}, f)

    cr_mod.main()
    out = capsys.readouterr().out
    assert "Failure rate: 1/5 = 20.0%" in out
    assert "ALARM: failure rate above 20%" not in out


def test_main_handles_empty_log_and_missing_controller(cr_mod, capsys):
    # Neither REAL_COST_LOG nor CONTROLLER exists on disk at all -- the
    # genuine "nothing logged yet" edge case.
    cr_mod.main()
    out = capsys.readouterr().out
    assert "Total logged cost:      $0.0000" in out
    assert "Total calls (all-time): 0  (0 real, 0 cache hits)" in out
    assert "Calls since cache deployed (2026-07-20T05:34:28): 0" in out
    # since_rows is empty -> the cache-hit-rate line must not be printed at all.
    assert "Cache hit rate" not in out
    # total task count is 0 -> the failure-rate line must not be printed.
    assert "Failure rate" not in out
