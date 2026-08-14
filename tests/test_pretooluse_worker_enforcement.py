#!/usr/bin/env python3
"""task-20260814-132651-add-pretooluse-hook-enforcement-layer-fo
(UMR-20260814-131747-420e): real tests for
hooks/pretooluse_worker_enforcement.py, the Claude Code PreToolUse hook that
mechanically enforces (1) git commit/push and Write/Edit/NotebookEdit scope
to a worker's own assigned repo+branch/workspace, (2) a block on raw `tmux
send-keys` and a small set of queue-bypassing script/systemctl invocations
from inside a worker session, and (3) a queryable sqlite audit log of every
tool call this hook observes.

Two layers, matching this repo's existing hooks/find_root_walk_guard.py test
style:
  - Direct-import unit tests against the hook's own pure functions (no
    subprocess, no cgroup/filesystem dependency beyond a real temp git repo
    for the branch-comparison checks) -- fully portable, run anywhere.
  - A small number of real subprocess end-to-end tests that invoke the hook
    exactly the way Claude Code's hook runner does (JSON payload on stdin,
    verdict read from exit code + stderr) against THIS task's own real,
    ambient veridian-worker cgroup identity -- skipped automatically when
    not actually running inside a real veridian-worker@*.service cgroup
    (e.g. a reviewer running pytest outside the dispatched worker sandbox),
    so the suite never spuriously fails outside that real environment.
"""
import importlib.util
import json
import os
import subprocess
import sys
import sqlite3

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK_PATH = os.path.join(SCRIPTS_DIR, "hooks", "pretooluse_worker_enforcement.py")

_spec = importlib.util.spec_from_file_location("pretooluse_worker_enforcement", HOOK_PATH)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


_repo_counter = [0]


def make_repo(tmp_path, branch):
    _repo_counter[0] += 1
    repo = tmp_path / f"repo{_repo_counter[0]}"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", "--initial-branch=" + branch, str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "--allow-empty", "--quiet", "-m", "init"], check=True)
    return str(repo)


def assignment_for(workspace, branch, readable=True):
    return {
        "task_id": "task-test",
        "task_dir": os.path.dirname(workspace),
        "workspace": os.path.normpath(workspace),
        "repo": "veridian-scripts",
        "branch": branch,
        "readable": readable,
    }


# ---------------------------------------------------------------------------
# _under_workspace
# ---------------------------------------------------------------------------
def test_under_workspace_exact_match():
    assert mod._under_workspace("/opt/veridian/ai-os/tasks/x/workspace", "/opt/veridian/ai-os/tasks/x/workspace")


def test_under_workspace_real_subpath():
    assert mod._under_workspace("/opt/veridian/ai-os/tasks/x/workspace/sub/file.py", "/opt/veridian/ai-os/tasks/x/workspace")


def test_under_workspace_outside():
    assert not mod._under_workspace("/tmp/evil.txt", "/opt/veridian/ai-os/tasks/x/workspace")


def test_under_workspace_prefix_collision_not_a_real_subpath():
    # A sibling directory that merely shares a string prefix must NOT count
    # as "under" the workspace -- this is the exact bug class find_root_walk_guard.py
    # and similar guards in this repo are built to avoid.
    assert not mod._under_workspace(
        "/opt/veridian/ai-os/tasks/x/workspace2/file.py", "/opt/veridian/ai-os/tasks/x/workspace"
    )


# ---------------------------------------------------------------------------
# check_write_tool_scope
# ---------------------------------------------------------------------------
def test_write_inside_workspace_allowed():
    a = assignment_for("/opt/veridian/ai-os/tasks/x/workspace", "b")
    verdict, reason = mod.check_write_tool_scope(
        "Write", {"file_path": "/opt/veridian/ai-os/tasks/x/workspace/foo.py"}, "/opt/veridian/ai-os/tasks/x/workspace", a
    )
    assert verdict == "allow"


def test_write_outside_workspace_denied():
    a = assignment_for("/opt/veridian/ai-os/tasks/x/workspace", "b")
    verdict, reason = mod.check_write_tool_scope(
        "Write", {"file_path": "/tmp/evil.py"}, "/opt/veridian/ai-os/tasks/x/workspace", a
    )
    assert verdict == "deny"
    assert "outside" in reason


def test_write_no_assignment_allowed():
    verdict, reason = mod.check_write_tool_scope("Write", {"file_path": "/tmp/evil.py"}, "/tmp", None)
    assert verdict == "allow"


def test_write_unreadable_assignment_fails_closed():
    a = assignment_for("/opt/veridian/ai-os/tasks/x/workspace", "b", readable=False)
    verdict, reason = mod.check_write_tool_scope(
        "Write", {"file_path": "/opt/veridian/ai-os/tasks/x/workspace/foo.py"}, "/opt/veridian/ai-os/tasks/x/workspace", a
    )
    assert verdict == "deny"


def test_non_write_tool_out_of_scope():
    a = assignment_for("/opt/veridian/ai-os/tasks/x/workspace", "b")
    verdict, reason = mod.check_write_tool_scope("Read", {"file_path": "/tmp/evil.py"}, "/tmp", a)
    assert verdict == "allow"


# ---------------------------------------------------------------------------
# check_git_write -- real temp git repos, real branch checks
# ---------------------------------------------------------------------------
def test_git_commit_on_assigned_branch_allowed(tmp_path):
    repo = make_repo(tmp_path, "worker/task-x")
    a = assignment_for(repo, "worker/task-x")
    segment = ["git", "commit", "--allow-empty", "-m", "x"]
    verdict, reason = mod.check_git_write(segment, repo, a)
    assert verdict == "allow"


def test_git_commit_on_wrong_branch_denied(tmp_path):
    repo = make_repo(tmp_path, "some-other-branch")
    a = assignment_for(repo, "worker/task-x")
    segment = ["git", "commit", "--allow-empty", "-m", "x"]
    verdict, reason = mod.check_git_write(segment, repo, a)
    assert verdict == "deny"
    assert "some-other-branch" in reason
    assert "worker/task-x" in reason


def test_git_push_default_current_branch_matches_allowed(tmp_path):
    repo = make_repo(tmp_path, "worker/task-x")
    a = assignment_for(repo, "worker/task-x")
    segment = ["git", "push", "origin"]
    verdict, reason = mod.check_git_write(segment, repo, a)
    assert verdict == "allow"


def test_git_push_explicit_refspec_mismatch_denied(tmp_path):
    repo = make_repo(tmp_path, "worker/task-x")
    a = assignment_for(repo, "worker/task-x")
    segment = ["git", "push", "origin", "main"]
    verdict, reason = mod.check_git_write(segment, repo, a)
    assert verdict == "deny"
    assert "'main'" in reason


def test_git_dash_c_outside_workspace_denied(tmp_path):
    workspace = make_repo(tmp_path, "worker/task-x")
    other_repo = make_repo(tmp_path, "worker/task-x")  # a second, unrelated real repo
    a = assignment_for(workspace, "worker/task-x")
    segment = ["git", "-C", other_repo, "commit", "--allow-empty", "-m", "x"]
    verdict, reason = mod.check_git_write(segment, "/somewhere/else", a)
    assert verdict == "deny"
    assert "outside" in reason


def test_git_status_readonly_out_of_scope(tmp_path):
    repo = make_repo(tmp_path, "some-other-branch")
    a = assignment_for(repo, "worker/task-x")
    segment = ["git", "status"]
    verdict, reason = mod.check_git_write(segment, repo, a)
    assert verdict == "allow"  # not a write subcommand -- out of this check's scope


def test_git_write_no_assignment_allowed(tmp_path):
    repo = make_repo(tmp_path, "any-branch")
    segment = ["git", "commit", "--allow-empty", "-m", "x"]
    verdict, reason = mod.check_git_write(segment, repo, None)
    assert verdict == "allow"


def test_git_write_unreadable_assignment_fails_closed(tmp_path):
    repo = make_repo(tmp_path, "worker/task-x")
    a = assignment_for(repo, "worker/task-x", readable=False)
    segment = ["git", "commit", "--allow-empty", "-m", "x"]
    verdict, reason = mod.check_git_write(segment, repo, a)
    assert verdict == "deny"


def test_non_git_command_out_of_scope():
    a = assignment_for("/x/workspace", "b")
    verdict, reason = mod.check_git_write(["ls", "-la"], "/x/workspace", a)
    assert verdict == "allow"


# ---------------------------------------------------------------------------
# check_queue_bypass: tmux send-keys / blocked scripts / systemctl
# ---------------------------------------------------------------------------
ANY_ASSIGNMENT = assignment_for("/x/workspace", "b")


def test_tmux_send_keys_denied():
    verdict, reason = mod.check_queue_bypass(["tmux", "send-keys", "-t", "sess", "hi", "Enter"], ANY_ASSIGNMENT)
    assert verdict == "deny"
    assert "send-keys" in reason


def test_tmux_other_subcommand_allowed():
    verdict, reason = mod.check_queue_bypass(["tmux", "list-sessions"], ANY_ASSIGNMENT)
    assert verdict == "allow"


def test_dispatch_core_direct_invocation_denied():
    verdict, reason = mod.check_queue_bypass(
        ["python3", "/opt/veridian/scripts/dispatch_core.py", "--flag"], ANY_ASSIGNMENT
    )
    assert verdict == "deny"
    assert "dispatch_core.py" in reason


def test_dispatch_tick_direct_invocation_denied():
    verdict, reason = mod.check_queue_bypass(
        ["python3", "/opt/veridian/scripts/dispatch-tick.py"], ANY_ASSIGNMENT
    )
    assert verdict == "deny"


def test_unrelated_python_script_allowed():
    verdict, reason = mod.check_queue_bypass(
        ["python3", "/opt/veridian/scripts/some_other_script.py"], ANY_ASSIGNMENT
    )
    assert verdict == "allow"


def test_systemctl_start_worker_unit_denied():
    verdict, reason = mod.check_queue_bypass(
        ["systemctl", "--user", "start", "veridian-worker@task-x.service"], ANY_ASSIGNMENT
    )
    assert verdict == "deny"
    assert "veridian-worker@task-x.service" in reason


def test_systemctl_status_worker_unit_allowed():
    # read-only systemctl action against a worker unit is not a spawn/bypass
    verdict, reason = mod.check_queue_bypass(
        ["systemctl", "--user", "status", "veridian-worker@task-x.service"], ANY_ASSIGNMENT
    )
    assert verdict == "allow"


def test_systemctl_unrelated_unit_allowed():
    verdict, reason = mod.check_queue_bypass(["systemctl", "--user", "start", "some-other.service"], ANY_ASSIGNMENT)
    assert verdict == "allow"


def test_queue_bypass_no_assignment_allowed():
    verdict, reason = mod.check_queue_bypass(["tmux", "send-keys", "-t", "s", "x"], None)
    assert verdict == "allow"


# ---------------------------------------------------------------------------
# evaluate_bash -- full command-string integration (still pure, no subprocess
# except check_git_write's own internal `git rev-parse`)
# ---------------------------------------------------------------------------
def test_evaluate_bash_blocks_tmux_mid_pipeline():
    verdict, reason = mod.evaluate_bash("echo hi && tmux send-keys -t s hi Enter", "/x", ANY_ASSIGNMENT)
    assert verdict == "deny"


def test_evaluate_bash_allows_plain_command():
    verdict, reason = mod.evaluate_bash("ls -la /opt/veridian", "/x", ANY_ASSIGNMENT)
    assert verdict == "allow"


def test_evaluate_bash_empty_command_allowed():
    verdict, reason = mod.evaluate_bash("", "/x", ANY_ASSIGNMENT)
    assert verdict == "allow"


# ---------------------------------------------------------------------------
# Audit log (check 3) -- real sqlite write + query
# ---------------------------------------------------------------------------
def test_audit_log_writes_queryable_row(tmp_path, monkeypatch):
    db_path = str(tmp_path / "audit.sqlite")
    monkeypatch.setattr(mod, "AUDIT_DB", db_path)
    mod.log_audit(
        task_id="task-x", session_id="sess-1", cwd="/x", tool_name="Bash",
        tool_input={"command": "ls"}, decision="allow", reason=None,
    )
    mod.log_audit(
        task_id="task-x", session_id="sess-1", cwd="/x", tool_name="Bash",
        tool_input={"command": "tmux send-keys"}, decision="deny", reason="blocked",
    )
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT task_id, tool_name, decision, reason FROM tool_call_audit ORDER BY id").fetchall()
    conn.close()
    assert len(rows) == 2
    assert rows[0] == ("task-x", "Bash", "allow", None)
    assert rows[1] == ("task-x", "Bash", "deny", "blocked")


def test_audit_log_never_raises_on_bad_db_path(monkeypatch):
    # A directory that cannot be created (parent is actually a file) --
    # log_audit must swallow this, never propagate, per its own docstring.
    monkeypatch.setattr(mod, "AUDIT_DB", "/dev/null/cannot/create/this.sqlite")
    mod.log_audit(
        task_id="task-x", session_id="s", cwd="/x", tool_name="Bash",
        tool_input={"command": "ls"}, decision="allow", reason=None,
    )  # must not raise


# ---------------------------------------------------------------------------
# Real subprocess end-to-end tests, run exactly the way Claude Code's hook
# runner invokes this hook -- gated on actually running inside a real
# veridian-worker cgroup (this task's own dispatched worker sandbox), so the
# suite never spuriously fails when run outside it (e.g. a human reviewer's
# own laptop).
# ---------------------------------------------------------------------------
_REAL_TASK_ID = mod._task_id_from_cgroup()


def run_hook(tool_name, tool_input, cwd):
    payload = {
        "session_id": "e2e-test",
        "cwd": cwd,
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
    }
    return subprocess.run(
        [sys.executable, HOOK_PATH], input=json.dumps(payload), capture_output=True, text=True, timeout=10,
    )


@pytest.mark.skipif(_REAL_TASK_ID is None, reason="not running inside a real veridian-worker cgroup")
def test_e2e_tmux_send_keys_blocked_for_real():
    r = run_hook("Bash", {"command": "tmux send-keys -t claude-owner hello Enter"}, SCRIPTS_DIR)
    assert r.returncode == 2
    assert "tmux send-keys" in r.stderr


@pytest.mark.skipif(_REAL_TASK_ID is None, reason="not running inside a real veridian-worker cgroup")
def test_e2e_dispatch_core_direct_invocation_blocked_for_real():
    r = run_hook("Bash", {"command": "python3 /opt/veridian/scripts/dispatch_core.py --flag"}, SCRIPTS_DIR)
    assert r.returncode == 2
    assert "dispatch_core.py" in r.stderr


@pytest.mark.skipif(_REAL_TASK_ID is None, reason="not running inside a real veridian-worker cgroup")
def test_e2e_write_outside_workspace_blocked_for_real():
    r = run_hook("Write", {"file_path": "/tmp/evil-e2e-test.txt", "content": "x"}, SCRIPTS_DIR)
    assert r.returncode == 2
    assert "outside" in r.stderr


@pytest.mark.skipif(_REAL_TASK_ID is None, reason="not running inside a real veridian-worker cgroup")
def test_e2e_plain_read_only_command_allowed_for_real():
    r = run_hook("Bash", {"command": "git status"}, SCRIPTS_DIR)
    assert r.returncode == 0


@pytest.mark.skipif(_REAL_TASK_ID is None, reason="not running inside a real veridian-worker cgroup")
def test_e2e_malformed_payload_allowed_not_crashed():
    r = subprocess.run([sys.executable, HOOK_PATH], input="not json", capture_output=True, text=True, timeout=10)
    assert r.returncode == 0
