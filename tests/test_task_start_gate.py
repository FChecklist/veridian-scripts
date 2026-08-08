#!/usr/bin/env python3
"""Real tests for UMR-20260808-121334-e122 (Owner-decided Option B, PM
decision cycle UMR-20260808-141807-7f38, 2026-08-08): the shared stop-
work-order + resource-threshold gate resource_governor.py's dispatch_one()
already applies to every queued row, now also reachable by task-gateway.py's
cmd_start via resource_threshold_block_reason() + the new
--check-task-start-gate CLI flag + task-gateway.py's run_task_start_gate().

Real, confirmed gap this closes: cmd_start spawned a real systemd unit
synchronously with zero reference anywhere in task-gateway.py to
resource_governor.py/stop_work -- dispatch-owner-task.sh's OTHER real
channel (resource_governor.py's submit()/dispatch_one()) already got this
real protection, this one didn't.

Same "real, not mocked" convention as tests/test_stop_work_order_gate.py:
a real, isolated temp-file SQLite database and a real, isolated temp `git
init` repo for OWNER_DECISIONS_PATH, never the live production DB/repo.
Metric-threshold tests monkeypatch resource_governor.sample_metrics/
over_threshold_metrics directly (module-level function reassignment, which
resource_threshold_block_reason() -- defined in that same module -- picks
up at call time) rather than reading this host's real, currently volatile
swap/load, matching this session's own documented environmental-flake
lesson (test_stop_work_order_gate.py's swap_backoff flake).
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESOURCE_GOVERNOR = os.path.join(SCRIPTS_DIR, "resource_governor.py")
TASK_GATEWAY = os.path.join(SCRIPTS_DIR, "task-gateway.py")


def _load_rg(name, env=None):
    old_env = {}
    for k, v in (env or {}).items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location(name, RESOURCE_GOVERNOR)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _load_tg(name):
    spec = importlib.util.spec_from_file_location(name, TASK_GATEWAY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(repo_dir, *args):
    return subprocess.run(["git", "-C", repo_dir, *args], capture_output=True, text=True, check=True)


def _init_owner_decisions_repo(tmp_dir, entries_yaml_text, commit=True):
    repo_dir = os.path.join(tmp_dir, "owner_decisions_repo")
    os.makedirs(repo_dir, exist_ok=True)
    subprocess.run(["git", "init", repo_dir], capture_output=True, text=True, check=True)
    _git(repo_dir, "config", "user.email", "test@example.com")
    _git(repo_dir, "config", "user.name", "Test")
    path = os.path.join(repo_dir, "OWNER_DECISIONS_NEEDED_2026-07-23.yaml")
    with open(path, "w") as f:
        f.write(entries_yaml_text)
    if commit:
        _git(repo_dir, "add", "OWNER_DECISIONS_NEEDED_2026-07-23.yaml")
        _git(repo_dir, "commit", "-m", "real committed owner-decisions entry")
    return path


def _base_env(owner_decisions_path):
    return {
        "VERIDIAN_OWNER_DECISIONS_PATH": owner_decisions_path,
        "VERIDIAN_GOVERNOR_STOP_WORK_TRUNK_REF": "HEAD",
    }


# ---------------------------------------------------------------------------
# resource_threshold_block_reason() -- module-level unit tests
# ---------------------------------------------------------------------------

def test_resource_threshold_blocked_when_emergency_stop_present():
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rtbr_1")
        rg.EMERGENCY_STOP_PATH = os.path.join(d, "EMERGENCY_STOP")
        with open(rg.EMERGENCY_STOP_PATH, "w") as f:
            f.write("{}")
        blocked, detail, metrics = rg.resource_threshold_block_reason()
        assert blocked is True
        assert "EMERGENCY_STOP" in detail
        assert metrics is None, "metrics must not be sampled once already blocked by EMERGENCY_STOP"


def test_resource_threshold_blocked_when_metric_over_threshold():
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rtbr_2")
        rg.EMERGENCY_STOP_PATH = os.path.join(d, "EMERGENCY_STOP_never_created")
        rg.sample_metrics = lambda now=None: {"swap_used_pct": 99.9}
        rg.over_threshold_metrics = lambda metrics, threshold=None: ["swap_used_pct"]
        blocked, detail, metrics = rg.resource_threshold_block_reason()
        assert blocked is True
        assert "swap_used_pct" in detail
        assert metrics == {"swap_used_pct": 99.9}


def test_resource_threshold_clear_when_nothing_over():
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rtbr_3")
        rg.EMERGENCY_STOP_PATH = os.path.join(d, "EMERGENCY_STOP_never_created")
        rg.sample_metrics = lambda now=None: {"swap_used_pct": 5.0}
        rg.over_threshold_metrics = lambda metrics, threshold=None: []
        blocked, detail, metrics = rg.resource_threshold_block_reason()
        assert blocked is False
        assert detail is None
        assert metrics == {"swap_used_pct": 5.0}


# ---------------------------------------------------------------------------
# --check-task-start-gate CLI flag -- real subprocess, real exit code
# ---------------------------------------------------------------------------

def test_cli_check_task_start_gate_blocked_by_emergency_stop():
    with tempfile.TemporaryDirectory() as d:
        stop_path = os.path.join(d, "EMERGENCY_STOP")
        with open(stop_path, "w") as f:
            f.write("{}")
        env = dict(os.environ, VERIDIAN_GOVERNOR_EMERGENCY_STOP=stop_path)
        proc = subprocess.run(
            [sys.executable, RESOURCE_GOVERNOR, "--check-task-start-gate",
             "--task-identity", "t1", "--title", "test title"],
            capture_output=True, text=True, timeout=15, env=env,
        )
        assert proc.returncode == 0, proc.stderr
        result = json.loads(proc.stdout)
        assert result["blocked"] is True
        assert result["check"] == "resource_threshold"
        assert "EMERGENCY_STOP" in result["detail"]


def test_cli_check_task_start_gate_blocked_by_stop_work_order():
    with tempfile.TemporaryDirectory() as d:
        owner_decisions_path = _init_owner_decisions_repo(d, "[]\n")
        env = dict(os.environ, **_base_env(owner_decisions_path))
        env["VERIDIAN_GOVERNOR_EMERGENCY_STOP"] = os.path.join(d, "EMERGENCY_STOP_never_created")
        env["VERIDIAN_GOVERNOR_METRIC_THRESHOLD"] = "100000"  # never trip on this host's real metrics
        proc = subprocess.run(
            [sys.executable, RESOURCE_GOVERNOR, "--check-task-start-gate",
             "--task-identity", "t1", "--title", "test title", "--task-kind", "veridian_task_create"],
            capture_output=True, text=True, timeout=15, env=env,
        )
        assert proc.returncode == 0, proc.stderr
        result = json.loads(proc.stdout)
        assert result["blocked"] is True
        assert result["check"] == "stop_work_order"


def test_cli_check_task_start_gate_skips_stop_work_check_for_non_veridian_task_create():
    """Matches dispatch_one()'s own real scoping: the stop-work-order check
    only ever applies to task_kind == 'veridian_task_create'."""
    with tempfile.TemporaryDirectory() as d:
        owner_decisions_path = _init_owner_decisions_repo(d, "[]\n")
        env = dict(os.environ, **_base_env(owner_decisions_path))
        env["VERIDIAN_GOVERNOR_EMERGENCY_STOP"] = os.path.join(d, "EMERGENCY_STOP_never_created")
        env["VERIDIAN_GOVERNOR_METRIC_THRESHOLD"] = "100000"
        proc = subprocess.run(
            [sys.executable, RESOURCE_GOVERNOR, "--check-task-start-gate",
             "--task-identity", "t1", "--title", "test title", "--task-kind", "systemctl_action"],
            capture_output=True, text=True, timeout=15, env=env,
        )
        assert proc.returncode == 0, proc.stderr
        result = json.loads(proc.stdout)
        assert result["blocked"] is False


def test_cli_check_task_start_gate_clear():
    with tempfile.TemporaryDirectory() as d:
        env = dict(os.environ)
        env["VERIDIAN_GOVERNOR_EMERGENCY_STOP"] = os.path.join(d, "EMERGENCY_STOP_never_created")
        # Real host swap/load is volatile during this session (documented
        # environmental flake elsewhere in this test suite) -- push the
        # threshold high enough that this test's "clear" assertion doesn't
        # depend on this host's current real metrics.
        env["VERIDIAN_GOVERNOR_METRIC_THRESHOLD"] = "100000"
        proc = subprocess.run(
            [sys.executable, RESOURCE_GOVERNOR, "--check-task-start-gate",
             "--task-identity", "t1", "--title", "test title"],
            capture_output=True, text=True, timeout=15, env=env,
        )
        assert proc.returncode == 0, proc.stderr
        result = json.loads(proc.stdout)
        assert result == {"blocked": False, "check": None, "detail": None}


# ---------------------------------------------------------------------------
# task-gateway.py's run_task_start_gate() -- unit tests against a stub
# resource_governor.py so this doesn't depend on the real one's own real
# checks (those are covered directly above).
# ---------------------------------------------------------------------------

_STUB_GOVERNOR_TEMPLATE = """
import json, sys
print(json.dumps(%r))
"""


def test_run_task_start_gate_returns_parsed_result_when_clear():
    with tempfile.TemporaryDirectory() as d:
        stub = os.path.join(d, "stub_rg.py")
        with open(stub, "w") as f:
            f.write(_STUB_GOVERNOR_TEMPLATE % {"blocked": False, "check": None, "detail": None})
        tg = _load_tg("tg_gate_1")
        tg.RESOURCE_GOVERNOR = stub
        result = tg.run_task_start_gate("task-key-1", "some title")
        assert result == {"blocked": False, "check": None, "detail": None}


def test_run_task_start_gate_returns_parsed_result_when_blocked():
    with tempfile.TemporaryDirectory() as d:
        stub = os.path.join(d, "stub_rg.py")
        with open(stub, "w") as f:
            f.write(_STUB_GOVERNOR_TEMPLATE % {
                "blocked": True, "check": "stop_work_order", "detail": "real block reason text",
            })
        tg = _load_tg("tg_gate_2")
        tg.RESOURCE_GOVERNOR = stub
        result = tg.run_task_start_gate("task-key-2", "some title")
        assert result["blocked"] is True
        assert result["check"] == "stop_work_order"
        assert result["detail"] == "real block reason text"


def test_run_task_start_gate_passes_umr_id_when_given():
    """Confirms --umr-id is only forwarded to resource_governor.py when the
    caller actually has one (cmd_start's own --umr-id is optional)."""
    with tempfile.TemporaryDirectory() as d:
        stub = os.path.join(d, "stub_rg_argv.py")
        with open(stub, "w") as f:
            f.write(
                "import json, sys\n"
                "print(json.dumps({'blocked': False, 'check': None, 'detail': None, 'argv': sys.argv[1:]}))\n"
            )
        tg = _load_tg("tg_gate_3")
        tg.RESOURCE_GOVERNOR = stub
        result_with = tg.run_task_start_gate("task-key-3", "some title", umr_id="UMR-test-0001")
        assert "--umr-id" in result_with["argv"]
        assert "UMR-test-0001" in result_with["argv"]

        result_without = tg.run_task_start_gate("task-key-3", "some title")
        assert "--umr-id" not in result_without["argv"]
