#!/usr/bin/env python3
"""Real end-to-end verification for task-20260816-041054 (tier-aware Haiku
4.5 routing, corrected design -- see
OWNER_DECISION_2026-08-16_HAIKU_TIER_ROUTING_MECHANICAL_EXCEPTION.md and
FINDING_haiku_tier_routing_premise_false_2026-08-15.md for why the prior
umr_tasks.tier-based design was rejected).

Exercises the REAL pipe end to end, one real boundary at a time, same
"mock only the real subprocess/systemd/network boundary, run everything
else for real" convention tests/test_veridian_task_create_stderr_and_reason.py
and test_dispatch_owner_task_attach.py already establish in this repo:

1. resource_governor.py's _perform_spawn() -- real function, `_run()`
   mocked (the real subprocess boundary) -- proves a real complexity_tier
   in inputs_json becomes a real `--complexity-tier` arg to
   `veridian-task.py create`, and proves its total ABSENCE (the safe
   default for every dispatch that doesn't set one) means no such arg is
   ever added.
2. veridian-task.py's real cmd_create() -- run against a real, disposable
   local git repo (a real `git worktree add` happens, exactly as
   production does), with only systemctl (no live user systemd session in
   a test sandbox) and the fail-open app-sync/controller-sync/event-log
   helpers stubbed -- proves --complexity-tier genuinely reaches a written
   task.yaml, and that omitting it leaves task.yaml with NO complexity_tier
   key at all (never a null placeholder).
3. worker-entrypoint.sh's real, unmodified model-routing snippet (extracted
   verbatim from the live script text via regex, not re-implemented here)
   -- run against real fixture task.yaml files -- proves
   complexity_tier=='mechanical' resolves to --model haiku and every other
   real case (absent, empty, 'integrative', 'judgment') resolves to
   --model sonnet.

No file outside a pytest tmp_path is ever created or modified, no real
systemctl/network call is made, and the live production
/opt/veridian/ai-os/CONTROLLER.yaml is never touched.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
from unittest import mock

import pytest
import yaml

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)


def _load_module(name, path, env=None):
    old_env = {}
    env = env or {}
    for k, v in env.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ---------------------------------------------------------------------------
# 1. resource_governor.py's _perform_spawn(): real complexity_tier threading
#    to `veridian-task.py create`'s real argv.
# ---------------------------------------------------------------------------

def _load_rg(name, tmp_path):
    scratch_db = os.path.join(str(tmp_path), "scratch.sqlite")
    import importlib.util as _ilu
    sbr_spec = _ilu.spec_from_file_location("sbr_for_" + name, os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = _ilu.module_from_spec(sbr_spec)
    sbr_spec.loader.exec_module(sbr)
    import sqlite3
    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    conn.close()
    return _load_module(name, os.path.join(SCRIPTS_DIR, "resource_governor.py"),
                         {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR})


def test_perform_spawn_threads_real_complexity_tier_into_create_argv(tmp_path):
    rg = _load_rg("rg_complexity_thread", tmp_path)
    row = {
        "task_kind": "veridian_task_create",
        "unit_name": None,
        "inputs_json": json.dumps({
            "title": "t", "prompt": "p", "repo": "veridian-scripts",
            "complexity_tier": "mechanical",
        }),
    }
    calls = []

    def fake_run(cmd):
        calls.append(cmd)
        return _FakeCompletedProcess(returncode=0, stdout="CREATED: task-fake-1\n", stderr="")

    with mock.patch.object(rg, "_run", side_effect=fake_run), \
         mock.patch.object(rg, "_seed_credit_accountant_plan", return_value=None):
        result = rg._perform_spawn(row)

    assert result["status"] == "running", result
    create_cmd = next(c for c in calls if "veridian-task.py" in c[1])
    assert "--complexity-tier" in create_cmd, create_cmd
    idx = create_cmd.index("--complexity-tier")
    assert create_cmd[idx + 1] == "mechanical", create_cmd


def test_perform_spawn_omits_complexity_tier_arg_when_absent(tmp_path):
    """The safe default: no complexity_tier in inputs (every real dispatch
    path that doesn't go through pm_lifecycle.py's `run` with a real
    --complexity-tier) must never add the flag at all."""
    rg = _load_rg("rg_complexity_absent", tmp_path)
    row = {
        "task_kind": "veridian_task_create",
        "unit_name": None,
        "inputs_json": json.dumps({"title": "t", "prompt": "p", "repo": "veridian-scripts"}),
    }
    calls = []

    def fake_run(cmd):
        calls.append(cmd)
        return _FakeCompletedProcess(returncode=0, stdout="CREATED: task-fake-2\n", stderr="")

    with mock.patch.object(rg, "_run", side_effect=fake_run), \
         mock.patch.object(rg, "_seed_credit_accountant_plan", return_value=None):
        result = rg._perform_spawn(row)

    assert result["status"] == "running", result
    create_cmd = next(c for c in calls if "veridian-task.py" in c[1])
    assert "--complexity-tier" not in create_cmd, create_cmd


# ---------------------------------------------------------------------------
# 2. veridian-task.py's real cmd_create(): real git worktree, real task.yaml
#    write, only systemctl / fail-open telemetry helpers stubbed.
# ---------------------------------------------------------------------------

def _make_real_origin_repo(tmp_path, name="veridian-scripts"):
    bare = os.path.join(str(tmp_path), f"{name}-bare.git")
    subprocess.run(["git", "init", "--bare", "-b", "main", bare], check=True, capture_output=True)
    seed = os.path.join(str(tmp_path), "seed-clone")
    subprocess.run(["git", "clone", bare, seed], check=True, capture_output=True)
    subprocess.run(["git", "-C", seed, "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", seed, "config", "user.name", "Test"], check=True)
    with open(os.path.join(seed, "README.md"), "w") as f:
        f.write("seed\n")
    subprocess.run(["git", "-C", seed, "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", seed, "commit", "-m", "seed"], check=True, capture_output=True)
    subprocess.run(["git", "-C", seed, "push", "origin", "main"], check=True, capture_output=True)

    repos_dir = os.path.join(str(tmp_path), "repos")
    os.makedirs(repos_dir, exist_ok=True)
    checkout = os.path.join(repos_dir, name)
    subprocess.run(["git", "clone", bare, checkout], check=True, capture_output=True)
    subprocess.run(["git", "-C", checkout, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"],
                    check=True, capture_output=True)
    return repos_dir


def _load_vt(name, tmp_path, ai_os_dir, repos_dir):
    mod = _load_module(name, os.path.join(SCRIPTS_DIR, "veridian-task.py"))
    # Real dynamic-global override (save_task/cmd_create read these module
    # globals by name at call time, not at import time) -- redirects every
    # real file write this test triggers into tmp_path, never the live
    # /opt/veridian/ai-os tree.
    mod.AI_OS = ai_os_dir
    mod.REPOS = repos_dir
    # Fail-open telemetry/controller-sync helpers (network / real
    # CONTROLLER.yaml) -- stubbed out entirely for test isolation, same as
    # this repo's other cmd_create-adjacent tests do for the systemd/network
    # boundary. NOT stubbing sync_controller_entry would write into this
    # module's own CONTROLLER constant, which is a plain string frozen to
    # the REAL /opt/veridian/ai-os/CONTROLLER.yaml at import time (unlike
    # AI_OS/REPOS, reassigning mod.AI_OS after import does NOT change it) --
    # so it must never run for real here.
    mod.sync_controller_entry = lambda task: None
    mod._auto_log_task_event = lambda *a, **k: None
    mod._sync_to_app = lambda *a, **k: None
    return mod


def _run_cmd_create(mod, title, repo, prompt, complexity_tier):
    args = argparse_namespace(
        title=title, repo=repo, prompt=prompt,
        hold_for_owner_signoff=False, complexity_tier=complexity_tier,
    )
    real_run = subprocess.run

    def fake_run(cmd, *a, **k):
        if cmd[0] == "systemctl":
            return _FakeCompletedProcess(returncode=0, stdout="", stderr="")
        return real_run(cmd, *a, **k)

    with mock.patch.object(mod.subprocess, "run", side_effect=fake_run):
        mod.cmd_create(args)


class argparse_namespace:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_cmd_create_writes_real_complexity_tier_into_task_yaml(tmp_path):
    repos_dir = _make_real_origin_repo(tmp_path)
    ai_os_dir = os.path.join(str(tmp_path), "ai-os")
    mod = _load_vt("vt_complexity_present", tmp_path, ai_os_dir, repos_dir)

    captured_id = {}
    real_print = print

    def capture_print(*a, **k):
        text = " ".join(str(x) for x in a)
        m = re.match(r"CREATED: (\S+)", text)
        if m:
            captured_id["id"] = m.group(1)
        real_print(*a, **k)

    with mock.patch("builtins.print", side_effect=capture_print):
        _run_cmd_create(mod, "Mechanical fixup", "veridian-scripts", "do a mechanical thing", "mechanical")

    task_id = captured_id["id"]
    task_yaml_path = os.path.join(ai_os_dir, "tasks", task_id, "task.yaml")
    with open(task_yaml_path) as f:
        written = yaml.safe_load(f)
    assert written.get("complexity_tier") == "mechanical", written


def test_cmd_create_omits_complexity_tier_key_when_not_provided(tmp_path):
    """Safe default: no --complexity-tier at all -> task.yaml has NO
    complexity_tier key (never a null placeholder) -- proves
    worker-entrypoint.sh's `.get('complexity_tier') or ''` sees a genuinely
    absent field for every dispatch path that never classifies complexity."""
    repos_dir = _make_real_origin_repo(tmp_path)
    ai_os_dir = os.path.join(str(tmp_path), "ai-os")
    mod = _load_vt("vt_complexity_absent", tmp_path, ai_os_dir, repos_dir)

    captured_id = {}
    real_print = print

    def capture_print(*a, **k):
        text = " ".join(str(x) for x in a)
        m = re.match(r"CREATED: (\S+)", text)
        if m:
            captured_id["id"] = m.group(1)
        real_print(*a, **k)

    with mock.patch("builtins.print", side_effect=capture_print):
        _run_cmd_create(mod, "Some judgment task", "veridian-scripts", "do a judgment-tier thing", None)

    task_id = captured_id["id"]
    task_yaml_path = os.path.join(ai_os_dir, "tasks", task_id, "task.yaml")
    with open(task_yaml_path) as f:
        written = yaml.safe_load(f)
    assert "complexity_tier" not in written, written


# ---------------------------------------------------------------------------
# 3. worker-entrypoint.sh's real, unmodified model-routing snippet: extracted
#    verbatim from the live script (never re-implemented), run against real
#    fixture task.yaml files.
# ---------------------------------------------------------------------------

def _extract_model_routing_snippet():
    script_path = os.path.join(SCRIPTS_DIR, "worker-entrypoint.sh")
    with open(script_path) as f:
        text = f.read()
    m = re.search(
        r"(COMPLEXITY_TIER=\$\(python3.*?\n(?:if \[ \"\$COMPLEXITY_TIER\".*?\nfi\n))",
        text, re.S,
    )
    assert m, "could not locate the real COMPLEXITY_TIER/CLAUDE_MODEL block in worker-entrypoint.sh -- script shape changed"
    return m.group(1)


def _resolve_model_for_task_yaml(tmp_path, task_yaml_contents):
    task_dir = tmp_path / "task-fixture"
    task_dir.mkdir()
    (task_dir / "task.yaml").write_text(yaml.safe_dump(task_yaml_contents))
    snippet = _extract_model_routing_snippet()
    script = f'TASK_DIR="{task_dir}"\n{snippet}\necho "$CLAUDE_MODEL"\n'
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


@pytest.mark.parametrize("complexity_tier,expected_model", [
    ("mechanical", "haiku"),
    ("integrative", "sonnet"),
    ("judgment", "sonnet"),
    (None, "sonnet"),
    ("", "sonnet"),
])
def test_worker_entrypoint_real_snippet_routes_model_correctly(tmp_path, complexity_tier, expected_model):
    task_yaml = {"workspace": "/tmp/x", "branch": "worker/x"}
    if complexity_tier is not None:
        task_yaml["complexity_tier"] = complexity_tier
    resolved = _resolve_model_for_task_yaml(tmp_path, task_yaml)
    assert resolved == expected_model, (complexity_tier, resolved)


# ---------------------------------------------------------------------------
# 4. pm_lifecycle.py's dispatch_task(): the first real hop, from the
#    --complexity-tier CLI arg to dispatch-owner-task.sh's own argv.
# ---------------------------------------------------------------------------

def _load_pm_lifecycle():
    return _load_module("pm_lifecycle_complexity", os.path.join(SCRIPTS_DIR, "pm_lifecycle.py"))


def test_dispatch_task_threads_complexity_tier_to_dispatch_owner_task_argv():
    pm = _load_pm_lifecycle()
    captured = {}

    def fake_run(cmd, timeout=60, **kw):
        captured["cmd"] = cmd
        return 0, "DISPATCHED: umr_id=U instruction_id=I work_item_id=W task_identity=T", ""

    with mock.patch.object(pm, "_run", side_effect=fake_run):
        result = pm.dispatch_task("Title", "Prompt", 2, "claude_code_cli", "some-repo",
                                   complexity_tier="mechanical")

    assert result["outcome"] == "dispatched", result
    cmd = captured["cmd"]
    assert cmd[0] == "./dispatch-owner-task.sh"
    assert "--complexity-tier" in cmd, cmd
    assert cmd[cmd.index("--complexity-tier") + 1] == "mechanical", cmd


def test_dispatch_task_omits_complexity_tier_when_not_given():
    pm = _load_pm_lifecycle()
    captured = {}

    def fake_run(cmd, timeout=60, **kw):
        captured["cmd"] = cmd
        return 0, "DISPATCHED: umr_id=U instruction_id=I work_item_id=W task_identity=T", ""

    with mock.patch.object(pm, "_run", side_effect=fake_run):
        pm.dispatch_task("Title", "Prompt", 2, "claude_code_cli", "some-repo")

    assert "--complexity-tier" not in captured["cmd"], captured["cmd"]
