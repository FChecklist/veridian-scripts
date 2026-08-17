"""UMR-20260816-171513-5901 (Owner directive 2026-08-16), second site: real regression
coverage for dispatch-owner-task.sh's tier-3/4 `claude_code_cli_headless` branch's own
unconditional `gh pr create` call (line ~761 at the time this fix landed) -- the same
latent defect supervisor-entrypoint.sh's own DOCS-ONLY-PR-GUARD-BLOCK already closes for
the worker/supervisor path, but this direct headless-execution branch never goes through
supervisor-entrypoint.sh at all, so that guard could not cover it. Governed by the SAME
named switch, VERIDIAN_GATE_PR_ON_CODE_CHANGE (default 1), never a second flag.

Same real-subprocess stubbing convention as test_dispatch_owner_task_complexity_tier.py: a
fake `python3` placed first on PATH forwards `-c` invocations (and the real
docs_only_diff_guard.py invocation under test) to the real python3, and returns canned JSON
for task-gateway.py/resource_governor.py/superboss-register.py by name -- this script's own
real duplicate-check/classification/UMR-registration machinery is not what this test is
about. `claude`/`gh` are faked (real `claude -p` calls a paid model; real `gh pr create`
would hit the real GitHub API). Everything else -- git, the real worktree setup, the real
push, and the real docs_only_diff_guard.py classification of that real diff -- runs for
real, against a real, disposable repo created under /opt/veridian/repos/ (the one real
absolute path this script's own REPO_PATH is hardcoded to; not configurable), torn down at
the end of the test either way.
"""
import json
import os
import shutil
import stat
import subprocess
import uuid

import pytest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(SCRIPT_DIR, "dispatch-owner-task.sh")
REAL_REPOS_ROOT = "/opt/veridian/repos"

GIT_ENV = dict(os.environ)
GIT_ENV.update({
    "GIT_AUTHOR_NAME": "Test Bot", "GIT_AUTHOR_EMAIL": "test-bot@example.com",
    "GIT_COMMITTER_NAME": "Test Bot", "GIT_COMMITTER_EMAIL": "test-bot@example.com",
})


def _git(args, cwd):
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                        env=GIT_ENV, timeout=30)
    assert r.returncode == 0, f"git {args} failed in {cwd}\nstdout={r.stdout}\nstderr={r.stderr}"
    return r.stdout.strip()


FAKE_PYTHON3 = """#!/bin/bash
echo "python3 $*" >> "$CALL_LOG"
if [ "$1" = "-c" ]; then
    exec "$REAL_PYTHON3" "$@"
fi
case "$1" in
  */docs_only_diff_guard.py)
    # The real thing under test -- runs for real against the real worktree diff.
    exec "$REAL_PYTHON3" "$@"
    ;;
  superboss-register.py)
    shift
    sub="$1"
    case "$sub" in
      check-content-duplicate)
        echo '{"content_duplicate_found": false}'
        ;;
      check-target-identifier-duplicate)
        echo '{"target_identifier_duplicate_found": false}'
        ;;
      log-work)
        echo '{"work_item_id": "WI-FAKE-TEST-1"}'
        ;;
      mark-umr-relay-attempted)
        echo '{}'
        ;;
      mark-umr-terminal)
        shift
        echo "mark-umr-terminal $*" >> "$MARK_TERMINAL_LOG"
        echo '{}'
        ;;
      *)
        echo "unexpected superboss-register.py subcommand: $sub" >&2
        exit 1
        ;;
    esac
    ;;
  task-gateway.py)
    shift
    echo "task-gateway.py $*" >> "$CALL_LOG"
    cat "$FAKE_SUBMIT_JSON_FILE"
    ;;
  resource_governor.py)
    shift
    echo "resource_governor.py $*" >> "$CALL_LOG"
    cat "$FAKE_RG_SUBMIT_JSON_FILE"
    ;;
  *)
    echo "unexpected python3 invocation: $*" >&2
    exit 1
    ;;
esac
"""

# Writes $FAKE_CLAUDE_FILE_PATH (relative to cwd, i.e. the real disposable worktree) with
# $FAKE_CLAUDE_FILE_CONTENT and commits it for real -- standing in for a real `claude -p`
# turn that edits + commits its own real work, same real git identity this whole box's
# config already provides (no GIT_AUTHOR/COMMITTER override needed here, matching the real
# script's own safety-net-commit assumption).
_FAKE_CLAUDE = """#!/bin/bash
mkdir -p "$(dirname "$FAKE_CLAUDE_FILE_PATH")"
echo "$FAKE_CLAUDE_FILE_CONTENT" > "$FAKE_CLAUDE_FILE_PATH"
git add "$FAKE_CLAUDE_FILE_PATH"
git commit -q -m "fake claude turn: $FAKE_CLAUDE_FILE_PATH"
echo '{"result": "stub claude cli output for regression test", "total_cost_usd": 0.01, "is_error": false}'
exit 0
"""

_FAKE_GH = """#!/bin/bash
echo "gh $*" >> "$GH_CALL_LOG"
if [ "$1" = "pr" ] && [ "$2" = "create" ]; then
  echo "https://github.com/FChecklist/fake-repo/pull/8888"
  exit 0
fi
exit 0
"""


@pytest.fixture()
def real_repo(tmp_path):
    """A real, disposable repo under the one real absolute path
    dispatch-owner-task.sh's own REPO_PATH is hardcoded to (`/opt/veridian/repos/$REPO`,
    not configurable) -- created and torn down by this fixture, never left behind."""
    repo_name = f"test-docs-only-guard-{uuid.uuid4().hex[:12]}"
    repo_path = os.path.join(REAL_REPOS_ROOT, repo_name)

    origin_bare = tmp_path / "origin.git"
    _git(["init", "--bare", str(origin_bare)], cwd=tmp_path)
    _git(["symbolic-ref", "HEAD", "refs/heads/main"], cwd=origin_bare)

    os.makedirs(repo_path)
    _git(["init"], cwd=repo_path)
    _git(["symbolic-ref", "HEAD", "refs/heads/main"], cwd=repo_path)
    with open(os.path.join(repo_path, "README.md"), "w") as f:
        f.write("seed content\n")
    _git(["add", "README.md"], cwd=repo_path)
    _git(["commit", "-m", "seed commit"], cwd=repo_path)
    _git(["remote", "add", "origin", str(origin_bare)], cwd=repo_path)
    _git(["push", "-u", "origin", "main"], cwd=repo_path)
    # Real refs/remotes/origin/HEAD symbolic ref -- the script's own
    # `git symbolic-ref refs/remotes/origin/HEAD` read requires this to already exist;
    # `git clone` sets it automatically, a bare `remote add` + `fetch` does not.
    _git(["remote", "set-head", "origin", "-a"], cwd=repo_path)

    try:
        yield {"repo_name": repo_name, "repo_path": repo_path, "origin_bare": str(origin_bare)}
    finally:
        shutil.rmtree(repo_path, ignore_errors=True)


@pytest.fixture()
def stub_env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, content in (("python3", FAKE_PYTHON3), ("claude", _FAKE_CLAUDE), ("gh", _FAKE_GH)):
        p = bin_dir / name
        p.write_text(content)
        p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    call_log = tmp_path / "python_calls.log"
    call_log.write_text("")
    mark_terminal_log = tmp_path / "mark_terminal_calls.log"
    mark_terminal_log.write_text("")
    gh_call_log = tmp_path / "gh_calls.log"

    fake_submit_json_file = tmp_path / "fake_submit.json"
    fake_submit_json_file.write_text(json.dumps({"instruction_id": "INS-FAKE-TEST-1", "attachment": None}))
    fake_rg_submit_json_file = tmp_path / "fake_rg_submit.json"
    fake_rg_submit_json_file.write_text(json.dumps({"accepted": True, "umr_id": "UMR-FAKE-TEST-1"}))

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["CALL_LOG"] = str(call_log)
    env["MARK_TERMINAL_LOG"] = str(mark_terminal_log)
    env["GH_CALL_LOG"] = str(gh_call_log)
    env["REAL_PYTHON3"] = shutil.which("python3")
    env["FAKE_SUBMIT_JSON_FILE"] = str(fake_submit_json_file)
    env["FAKE_RG_SUBMIT_JSON_FILE"] = str(fake_rg_submit_json_file)
    env["VERIDIAN_DISPATCH_LOCK_DIR"] = str(tmp_path / "locks")
    env.pop("VERIDIAN_GATE_PR_ON_CODE_CHANGE", None)

    return {
        "env": env, "gh_call_log": gh_call_log, "mark_terminal_log": mark_terminal_log,
    }


def _run(stub_env, args, extra_env=None):
    env = dict(stub_env["env"])
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", SCRIPT, *args],
        env=env, cwd=SCRIPT_DIR, capture_output=True, text=True, timeout=60,
    )


def test_progress_only_task_no_pr_created_note_preserved(real_repo, stub_env):
    """The real fix's core behavior at this second site: a tier-3/4 headless task whose
    entire real diff is a per-task progress/*.md file must never reach `gh pr create` --
    but the real commit is still genuinely pushed to origin (preserved, not discarded), and
    the terminal record's own --reason notes the docs-only guard, not a silent drop."""
    proc = _run(stub_env, [
        "Test docs-only headless task", "Test prompt text", "3", "claude_code_cli",
        real_repo["repo_name"],
    ], extra_env={
        "FAKE_CLAUDE_FILE_PATH": "progress/test-docs-only-headless.md",
        "FAKE_CLAUDE_FILE_CONTENT": "## Completed\\n- [x] thing\\n",
    })
    assert "DISPATCHED" in proc.stdout, proc.stdout + proc.stderr
    assert proc.returncode == 0, proc.stdout + proc.stderr

    gh_calls = stub_env["gh_call_log"].read_text() if stub_env["gh_call_log"].exists() else ""
    assert "pr create" not in gh_calls, f"gh pr create must never be invoked for a real docs-only diff, calls were: {gh_calls}"

    mark_calls = stub_env["mark_terminal_log"].read_text()
    assert "--status completed_unmerged" in mark_calls
    assert "Docs-only diff" in mark_calls, f"expected the real docs-only reason recorded, got: {mark_calls}"

    # Real work preserved: the branch is genuinely on origin, even with no PR. (Reading
    # directly from the bare "origin" repo itself -- `git branch` there, not `-r`, since a
    # bare repo has no remotes of its own; its real branches ARE what a client sees as
    # "origin/...".)
    branches = _git(["branch"], cwd=real_repo["origin_bare"])
    assert "cli-headless-task-" in branches, branches


def test_code_touching_task_still_creates_pr(real_repo, stub_env):
    """The other real half of this fix: a tier-3/4 headless task with a genuine code change
    must NOT be gated -- it must fall through to the existing, unchanged `gh pr create`
    path, unaffected by this guard."""
    proc = _run(stub_env, [
        "Test code-touching headless task", "Test prompt text", "3", "claude_code_cli",
        real_repo["repo_name"],
    ], extra_env={
        "FAKE_CLAUDE_FILE_PATH": "real_fix.py",
        "FAKE_CLAUDE_FILE_CONTENT": "def fix():\\n    return 42\\n",
    })
    assert "DISPATCHED" in proc.stdout, proc.stdout + proc.stderr
    assert proc.returncode == 0, proc.stdout + proc.stderr

    gh_calls = stub_env["gh_call_log"].read_text() if stub_env["gh_call_log"].exists() else ""
    assert "pr create" in gh_calls, f"gh pr create must genuinely be attempted for a code-relevant diff, calls were: {gh_calls}"

    mark_calls = stub_env["mark_terminal_log"].read_text()
    assert "--status completed_unmerged" in mark_calls
    assert "Docs-only diff" not in mark_calls, f"a real code change must never be marked docs-only, got: {mark_calls}"
    assert "pull/8888" in mark_calls, f"expected the real fake PR URL recorded in --reason, got: {mark_calls}"


def test_switch_disabled_reverts_to_unconditional_pr_creation(real_repo, stub_env):
    """The named revert switch (same one supervisor-entrypoint.sh's own guard uses, not a
    second flag): VERIDIAN_GATE_PR_ON_CODE_CHANGE=0 must restore the exact prior
    unconditional behavior at THIS site too -- a PR attempted even for a docs-only diff --
    without any code change/redeploy."""
    proc = _run(stub_env, [
        "Test docs-only headless task, switch off", "Test prompt text", "3", "claude_code_cli",
        real_repo["repo_name"],
    ], extra_env={
        "FAKE_CLAUDE_FILE_PATH": "progress/test-docs-only-headless-switch-off.md",
        "FAKE_CLAUDE_FILE_CONTENT": "## Completed\\n- [x] thing\\n",
        "VERIDIAN_GATE_PR_ON_CODE_CHANGE": "0",
    })
    assert "DISPATCHED" in proc.stdout, proc.stdout + proc.stderr
    assert proc.returncode == 0, proc.stdout + proc.stderr

    gh_calls = stub_env["gh_call_log"].read_text() if stub_env["gh_call_log"].exists() else ""
    assert "pr create" in gh_calls, (
        f"switch=0 must restore the prior unconditional gh pr create attempt even for a "
        f"docs-only diff, calls were: {gh_calls}"
    )


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
