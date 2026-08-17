#!/usr/bin/env python3
"""UMR-20260813-215742-db64: real regression coverage for
supervisor-entrypoint.sh's NO-OP-BRANCH-GUARD-BLOCK.

Real defect this closes: supervisor-entrypoint.sh treated an empty PR_URL as an
unconditional hard failure (exit 1) with no way to distinguish real `gh`/plumbing
breakage from a legitimate no-op -- a worker branch with ZERO commits ahead of its
base because the real deliverable was already merged by a prior task. Real,
directly-observed impact: 44 of 147 task dirs on 2026-08-13 died at exactly this
path (30% of all runs that day); the resulting false 'failed'/'killed' status made
PM tiers auto-dispatch an RCA for each one, itself another worker task whose own
branch also legitimately had zero commits ahead once the RCA concluded there was
nothing left to fix -- an unbounded paid-AI re-dispatch loop (RCA for
UMR-20260807-151622-15cd was dispatched twice; RCA for UMR-20260813-195852-aa85 was
dispatched even though its real fix had already merged as PR #323).

Two real, temp git repos (a bare "origin" + real clones), exactly as this UMR's own
SPEC requires -- never a mocked git. The REAL, installed supervisor-entrypoint.sh is
invoked as a real subprocess, with only the two genuinely external, paid/networked
tools it depends on downstream of the guard (`claude`, `gh`) faked out via a scratch
$HOME/.local/bin (which the script's own `export PATH="$HOME/.local/bin:..."` always
searches first) -- git, python3, and every real local helper script
(preflight-guard.py, veridian-task.py, risk-tier.py, superboss-register.py) run for
real, same convention this codebase's own test_worker_exit_status_bridge.py already
established for testing a real ExecStopPost-invoked entrypoint. superboss-register.py's
own SUPERBOSS_REGISTER_DB env override routes every DB write at a scratch sqlite file,
never the live production one.

The one real thing this test cannot avoid (no env-override exists for it, unlike
AI_OS/SUPERBOSS_REGISTER_DB elsewhere in this codebase): veridian-task.py's checkpoint
command syncs a row into the real, shared, live /opt/veridian/ai-os/CONTROLLER.yaml --
exactly what happens for every real supervisor run today. Task ids are clearly
'test-noop-guard-*' prefixed and their task dirs are removed in a finally block, same
as every other real-throwaway-row test in this suite (e.g.
test_worker_exit_status_bridge.py's own real systemd self-test units).
"""
import json
import os
import shutil
import stat
import subprocess
import tempfile
import uuid

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUPERVISOR_SCRIPT = os.path.join(REPO_ROOT, "supervisor-entrypoint.sh")
AI_OS = "/opt/veridian/ai-os"

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


@pytest.fixture()
def git_repo_pair(tmp_path):
    """Real bare 'origin' repo (default branch explicitly 'master', matching
    veridian-scripts' own real default branch -- never assumed) plus two real pushed
    branches: one with zero commits ahead of master (the no-op case), one with a real
    commit ahead (the genuine-work case)."""
    origin = tmp_path / "origin.git"
    _git(["init", "--bare", str(origin)], cwd=tmp_path)
    _git(["symbolic-ref", "HEAD", "refs/heads/master"], cwd=origin)

    seed = tmp_path / "seed"
    seed.mkdir()
    _git(["init"], cwd=seed)
    _git(["symbolic-ref", "HEAD", "refs/heads/master"], cwd=seed)
    (seed / "README.md").write_text("seed content\n")
    _git(["add", "README.md"], cwd=seed)
    _git(["commit", "-m", "seed commit"], cwd=seed)
    _git(["remote", "add", "origin", str(origin)], cwd=seed)
    _git(["push", "origin", "master"], cwd=seed)
    base_sha = _git(["rev-parse", "HEAD"], cwd=seed)

    noop_branch = "worker/test-no-op-guard-noop"
    _git(["checkout", "-b", noop_branch], cwd=seed)
    _git(["push", "origin", noop_branch], cwd=seed)
    noop_sha = _git(["rev-parse", "HEAD"], cwd=seed)
    assert noop_sha == base_sha
    _git(["checkout", "master"], cwd=seed)

    hascommit_branch = "worker/test-no-op-guard-hascommit"
    _git(["checkout", "-b", hascommit_branch], cwd=seed)
    # .py, not .md (UMR-20260816-171513-5901): supervisor-entrypoint.sh's own
    # DOCS-ONLY-PR-GUARD-BLOCK now runs before this test's own gh-failure
    # path -- a .md file would be (correctly) classified docs-only and take
    # that guard's own early-exit, never reaching the PR-URL-RESOLUTION-
    # GUARD-BLOCK this test exists to exercise. Real code extension keeps
    # this fixture's own stated intent ("a real deliverable") genuinely true
    # under the new classifier too.
    (seed / "REAL_WORK.py").write_text("a real deliverable\n")
    _git(["add", "REAL_WORK.py"], cwd=seed)
    _git(["commit", "-m", "real work commit"], cwd=seed)
    _git(["push", "origin", hascommit_branch], cwd=seed)
    hascommit_sha = _git(["rev-parse", "HEAD"], cwd=seed)

    return {
        "origin": str(origin), "base_sha": base_sha,
        "noop_branch": noop_branch, "noop_sha": noop_sha,
        "hascommit_branch": hascommit_branch, "hascommit_sha": hascommit_sha,
    }


def _make_workspace(tmp_path, origin, branch, name):
    workspace = tmp_path / name
    _git(["clone", origin, str(workspace)], cwd=tmp_path)
    _git(["checkout", branch], cwd=workspace)
    return workspace


_FAKE_GH = """#!/bin/bash
echo "gh $*" >> "$GH_CALL_LOG"
if [ "$1" = "pr" ] && [ "$2" = "create" ]; then
  echo "fake gh: simulated real GraphQL failure (no commits between base and branch)" >&2
  exit 1
fi
if [ "$1" = "pr" ] && [ "$2" = "list" ]; then
  exit 0
fi
exit 0
"""

_FAKE_CLAUDE = """#!/bin/bash
cat > review-verdict.json <<'EOF'
{"verdict": "reject", "tier": "tier2", "summary": "fake stub review for regression test -- not a real review", "issues": ["stub, not real"]}
EOF
echo '{"result": "stub claude cli output for regression test", "total_cost_usd": 0}'
exit 0
"""


@pytest.fixture()
def fake_home(tmp_path):
    """Real, throwaway $HOME whose .local/bin shadows the real `gh`/`claude` binaries
    -- supervisor-entrypoint.sh's own `export PATH="$HOME/.local/bin:...:/usr/bin:$PATH"`
    always searches this directory first, real proof (GH_CALL_LOG) that a fake was (or
    was not) ever invoked, never a guess from absence of a crash."""
    home = tmp_path / "fake_home"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    gh_path = bin_dir / "gh"
    gh_path.write_text(_FAKE_GH)
    claude_path = bin_dir / "claude"
    claude_path.write_text(_FAKE_CLAUDE)
    for p in (gh_path, claude_path):
        p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return home


class _RealTaskDir:
    """Real, throwaway task dir under the live AI_OS tree -- supervisor-entrypoint.sh
    hardcodes TASK_DIR=/opt/veridian/ai-os/tasks/$TASK_ID with no env override (same
    real constraint test_worker_exit_status_bridge.py's own E2E test documents for
    AI_OS), so a genuine end-to-end run of the real script needs a real dir here.
    Always removed in __exit__, task id is unambiguously 'test-noop-guard-' prefixed."""

    def __init__(self, task_yaml_extra):
        self.task_id = f"test-noop-guard-{uuid.uuid4().hex[:12]}"
        self.task_dir = os.path.join(AI_OS, "tasks", self.task_id)
        self._extra = task_yaml_extra

    def __enter__(self):
        os.makedirs(self.task_dir, exist_ok=True)
        task = {
            "id": self.task_id,
            "title": "test: no-op branch guard regression",
            "status": "pending_review",
            "repo": "veridian-scripts",
            "created_at": "2026-08-13T00:00:00+00:00",
            "last_checkpoint_at": None,
            "checkpoints": [{"status": "pending_review"}],
            "hold_for_owner_signoff": False,
        }
        task.update(self._extra)
        with open(os.path.join(self.task_dir, "task.yaml"), "w") as f:
            yaml.safe_dump(task, f, sort_keys=False)
        return self

    def load_task_yaml(self):
        with open(os.path.join(self.task_dir, "task.yaml")) as f:
            return yaml.safe_load(f)

    def __exit__(self, *exc):
        shutil.rmtree(self.task_dir, ignore_errors=True)


def _run_supervisor(task_id, fake_home, scratch_db, extra_env=None, timeout=180):
    env = dict(os.environ)
    env["HOME"] = str(fake_home)
    env["SUPERBOSS_REGISTER_DB"] = scratch_db
    env["GH_CALL_LOG"] = str(fake_home / "gh_calls.log")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [SUPERVISOR_SCRIPT, task_id], capture_output=True, text=True, env=env, timeout=timeout,
    )


@pytest.fixture()
def scratch_db(tmp_path):
    return str(tmp_path / "scratch-superboss-register.sqlite")


def test_zero_commits_ahead_exits_0_writes_marker_and_never_calls_gh(
        tmp_path, git_repo_pair, fake_home, scratch_db):
    """The real fix's core behavior: a branch with 0 commits ahead of its base is a
    real no-op completion, not a hard failure. Real exit 0, a real no_op.json marker
    naming the real base/branch SHAs, and -- proven by the fake gh's own call log never
    existing -- `gh pr create` is never even attempted."""
    workspace = _make_workspace(tmp_path, git_repo_pair["origin"], git_repo_pair["noop_branch"], "ws_noop")

    with _RealTaskDir({
        "workspace": str(workspace), "branch": git_repo_pair["noop_branch"],
    }) as task:
        result = _run_supervisor(task.task_id, fake_home, scratch_db)

        assert result.returncode == 0, (
            f"expected exit 0 for a real zero-commits-ahead branch, got {result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )

        no_op_path = os.path.join(task.task_dir, "no_op.json")
        assert os.path.exists(no_op_path), "expected a real no_op.json marker to be written"
        with open(no_op_path) as f:
            marker = json.load(f)
        assert marker["base_sha"] == git_repo_pair["base_sha"]
        assert marker["branch_sha"] == git_repo_pair["noop_sha"]
        assert marker["base_branch"] == "master"
        assert marker["branch"] == git_repo_pair["noop_branch"]
        assert marker["reason"], "reason string must be real and non-empty"
        assert git_repo_pair["noop_branch"] in marker["reason"]
        assert git_repo_pair["base_sha"] in marker["reason"]

        task_yaml = task.load_task_yaml()
        assert task_yaml["checkpoints"][-1]["status"] == "completed_no_change", (
            f"expected the real, distinct terminal checkpoint status, got: {task_yaml['checkpoints']}"
        )

        gh_call_log = fake_home / "gh_calls.log"
        assert not gh_call_log.exists(), (
            f"gh must never be invoked for a real no-op branch, but it was: {gh_call_log.read_text() if gh_call_log.exists() else ''}"
        )


def test_branch_with_real_commits_still_hard_fails_on_genuine_pr_failure(
        tmp_path, git_repo_pair, fake_home, scratch_db):
    """The other real half of this fix: a branch that genuinely has commits ahead of
    its base must NOT be treated as a no-op -- it must fall through to the existing,
    unchanged PR-creation path, and a real `gh pr create` failure there must still be a
    real hard failure (exit 1), never silently swallowed as a false no-op."""
    workspace = _make_workspace(tmp_path, git_repo_pair["origin"], git_repo_pair["hascommit_branch"], "ws_hascommit")

    with _RealTaskDir({
        "workspace": str(workspace), "branch": git_repo_pair["hascommit_branch"],
    }) as task:
        result = _run_supervisor(task.task_id, fake_home, scratch_db)

        assert result.returncode == 1, (
            f"expected the real, genuine PR-creation failure to still hard-fail with exit 1, got "
            f"{result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
        )

        no_op_path = os.path.join(task.task_dir, "no_op.json")
        assert not os.path.exists(no_op_path), "a branch with real commits ahead must never get a no_op.json marker"

        gh_call_log = fake_home / "gh_calls.log"
        assert gh_call_log.exists(), "gh pr create must be genuinely attempted for a branch with real commits ahead"
        assert "pr create" in gh_call_log.read_text()

        task_yaml = task.load_task_yaml()
        assert task_yaml["checkpoints"][-1]["status"] == "blocked", (
            f"expected the real, pre-existing hard-fail checkpoint status, got: {task_yaml['checkpoints']}"
        )


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
