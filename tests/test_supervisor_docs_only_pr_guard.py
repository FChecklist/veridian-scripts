#!/usr/bin/env python3
"""UMR-20260816-171513-5901 (Owner directive 2026-08-16): real regression coverage for
supervisor-entrypoint.sh's DOCS-ONLY-PR-GUARD-BLOCK.

Real defect this closes: `gh pr create` (both this script's own unconditional call, and --
confirmed live, not assumed -- the worker's own Claude session directly running `gh pr
create` itself mid-task) fired for ANY branch with commits ahead of its base, including one
whose entire real diff was a single per-task progress/*.md file. Real, directly-observed
evidence: FChecklist/compliance-tracker PRs #1277, #1290, #1291 (each created by the
WORKER's own tool call, per that task's own result.json, with this script's later `gh pr
create` attempt failing "already exists" and falling through to review+audit the same
progress-only diff); 422 open PRs on that repo as of 2026-08-16, 189 with a "docs" title
prefix, against a near-zero real landing rate.

Same real-subprocess testing convention tests/test_supervisor_no_op_branch_guard.py already
established: two real temp git repos (a bare "origin" + real clones), the REAL, installed
supervisor-entrypoint.sh invoked as a real subprocess, with only `claude`/`gh` faked out via
a scratch $HOME/.local/bin -- git, python3, and every real local helper script
(preflight-guard.py, veridian-task.py, docs_only_diff_guard.py, quality-gate.sh,
superboss-register.py) run for real. superboss-register.py's own SUPERBOSS_REGISTER_DB env
override routes every DB write at a scratch sqlite file, never the live production one.
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
    """Real bare 'origin' repo (default branch 'master', matching veridian-scripts' own real
    default branch) plus three real pushed branches: one whose only real commit touches a
    per-task progress/*.md file (docs-only), one whose real commit touches a real .py file
    (code-relevant), and one whose real commit touches both (mixed, still code-relevant)."""
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

    docs_branch = "worker/test-docs-only-guard-docs"
    _git(["checkout", "-b", docs_branch], cwd=seed)
    os.makedirs(seed / "progress", exist_ok=True)
    (seed / "progress" / "test-docs-only-guard.md").write_text("## Completed\n- [x] thing\n")
    _git(["add", "progress/test-docs-only-guard.md"], cwd=seed)
    _git(["commit", "-m", "progress note only"], cwd=seed)
    _git(["push", "origin", docs_branch], cwd=seed)
    docs_sha = _git(["rev-parse", "HEAD"], cwd=seed)
    _git(["checkout", "master"], cwd=seed)

    code_branch = "worker/test-docs-only-guard-code"
    _git(["checkout", "-b", code_branch], cwd=seed)
    (seed / "real_fix.py").write_text("def fix():\n    return 42\n")
    _git(["add", "real_fix.py"], cwd=seed)
    _git(["commit", "-m", "real code fix"], cwd=seed)
    _git(["push", "origin", code_branch], cwd=seed)
    code_sha = _git(["rev-parse", "HEAD"], cwd=seed)
    _git(["checkout", "master"], cwd=seed)

    mixed_branch = "worker/test-docs-only-guard-mixed"
    _git(["checkout", "-b", mixed_branch], cwd=seed)
    os.makedirs(seed / "progress", exist_ok=True)
    (seed / "progress" / "test-docs-only-guard-mixed.md").write_text("## Completed\n- [x] thing\n")
    (seed / "real_fix2.py").write_text("def fix2():\n    return 43\n")
    _git(["add", "progress/test-docs-only-guard-mixed.md", "real_fix2.py"], cwd=seed)
    _git(["commit", "-m", "progress note + real code fix"], cwd=seed)
    _git(["push", "origin", mixed_branch], cwd=seed)
    mixed_sha = _git(["rev-parse", "HEAD"], cwd=seed)

    return {
        "origin": str(origin),
        "docs_branch": docs_branch, "docs_sha": docs_sha,
        "code_branch": code_branch, "code_sha": code_sha,
        "mixed_branch": mixed_branch, "mixed_sha": mixed_sha,
    }


def _make_workspace(tmp_path, origin, branch, name):
    workspace = tmp_path / name
    _git(["clone", origin, str(workspace)], cwd=tmp_path)
    _git(["checkout", branch], cwd=workspace)
    return workspace


# gh's own real `-q` JQ filtering happens INSIDE the real gh binary, which this fake
# replaces entirely -- so the fake must itself decide what to print for `pr list --json url
# -q '.[0].url'` (a bare URL string) vs any other `pr list` invocation (a JSON array). The
# supervisor script only ever calls `pr list` with `-q '.[0].url'` (both this new guard's
# EXISTING_PR_URL lookup and the pre-existing PR-URL-RESOLUTION-GUARD-BLOCK fallback), so
# always emitting the bare URL form is correct for every real call site in this script.
_FAKE_GH = """#!/bin/bash
echo "gh $*" >> "$GH_CALL_LOG"
if [ "$1" = "pr" ] && [ "$2" = "create" ]; then
  if [ "${FAKE_GH_PR_CREATE_FAIL:-0}" = "1" ]; then
    echo "fake gh: simulated real GraphQL failure (no commits between base and branch)" >&2
    exit 1
  fi
  echo "https://github.com/FChecklist/fake-repo/pull/9999"
  exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "list" ]; then
  echo "${FAKE_GH_EXISTING_PR_URL:-}"
  exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "close" ]; then
  exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "comment" ]; then
  exit 0
fi
if [ "$1" = "workflow" ]; then
  echo "[]"
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
    def __init__(self, task_yaml_extra):
        self.task_id = f"test-docs-only-guard-{uuid.uuid4().hex[:12]}"
        self.task_dir = os.path.join(AI_OS, "tasks", self.task_id)
        self._extra = task_yaml_extra

    def __enter__(self):
        os.makedirs(self.task_dir, exist_ok=True)
        task = {
            "id": self.task_id,
            "title": "test: docs-only PR guard regression",
            "status": "pending_review",
            "repo": "veridian-scripts",
            "service": f"veridian-supervisor@{self.task_id}.service",
            "task_dir": self.task_dir,
            "created_at": "2026-08-16T00:00:00+00:00",
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


def test_docs_only_diff_no_pr_created_note_preserved(tmp_path, git_repo_pair, fake_home, scratch_db):
    """The real fix's core behavior: a branch whose real diff is progress/documentation only
    never reaches `gh pr create` -- no code-relevant content, no PR -- but the real work
    (pushed branch/commit + a structured docs_only_completion.json marker) is preserved, not
    discarded, and the task checkpoints a real, distinct terminal status."""
    workspace = _make_workspace(tmp_path, git_repo_pair["origin"], git_repo_pair["docs_branch"], "ws_docs")

    with _RealTaskDir({
        "workspace": str(workspace), "branch": git_repo_pair["docs_branch"],
    }) as task:
        result = _run_supervisor(task.task_id, fake_home, scratch_db)

        assert result.returncode == 0, (
            f"expected exit 0 for a real docs-only diff, got {result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )

        marker_path = os.path.join(task.task_dir, "docs_only_completion.json")
        assert os.path.exists(marker_path), "expected a real docs_only_completion.json marker to be written"
        with open(marker_path) as f:
            marker = json.load(f)
        assert marker["branch_sha"] == git_repo_pair["docs_sha"]
        assert marker["branch"] == git_repo_pair["docs_branch"]
        assert "progress/test-docs-only-guard.md" in marker["files"]
        assert marker["reason"], "reason string must be real and non-empty"

        task_yaml = task.load_task_yaml()
        assert task_yaml["checkpoints"][-1]["status"] == "completed_docs_only", (
            f"expected the real, distinct terminal checkpoint status, got: {task_yaml['checkpoints']}"
        )

        gh_call_log = fake_home / "gh_calls.log"
        calls = gh_call_log.read_text() if gh_call_log.exists() else ""
        assert "pr create" not in calls, f"gh pr create must never be invoked for a real docs-only diff, but calls were: {calls}"


def test_docs_only_diff_closes_pre_existing_pr(tmp_path, git_repo_pair, fake_home, scratch_db):
    """Real, observed loophole this guard also closes: the WORKER's own agentic session may
    already have run `gh pr create` itself before the supervisor ever ran (real evidence:
    FChecklist/compliance-tracker PRs #1277/#1290/#1291). When that pre-existing PR is found,
    the guard closes it rather than letting a real review/audit/merge cycle run against a
    diff with nothing to ship."""
    workspace = _make_workspace(tmp_path, git_repo_pair["origin"], git_repo_pair["docs_branch"], "ws_docs_preexisting")
    existing_pr = "https://github.com/FChecklist/fake-repo/pull/1291"

    with _RealTaskDir({
        "workspace": str(workspace), "branch": git_repo_pair["docs_branch"],
    }) as task:
        result = _run_supervisor(task.task_id, fake_home, scratch_db,
                                  extra_env={"FAKE_GH_EXISTING_PR_URL": existing_pr})

        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

        gh_call_log = fake_home / "gh_calls.log"
        calls = gh_call_log.read_text() if gh_call_log.exists() else ""
        assert f"pr close {existing_pr}" in calls, f"expected the pre-existing docs-only PR to be closed, calls were: {calls}"
        assert "pr create" not in calls


def test_code_touching_diff_still_creates_pr(tmp_path, git_repo_pair, fake_home, scratch_db):
    """The other real half of this fix: a branch with a genuine code change must NOT be
    gated -- it must fall through to the existing, unchanged review + `gh pr create` path."""
    workspace = _make_workspace(tmp_path, git_repo_pair["origin"], git_repo_pair["code_branch"], "ws_code")

    with _RealTaskDir({
        "workspace": str(workspace), "branch": git_repo_pair["code_branch"],
    }) as task:
        result = _run_supervisor(task.task_id, fake_home, scratch_db)

        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

        marker_path = os.path.join(task.task_dir, "docs_only_completion.json")
        assert not os.path.exists(marker_path), "a code-relevant branch must never get a docs_only_completion.json marker"

        gh_call_log = fake_home / "gh_calls.log"
        assert gh_call_log.exists(), "gh pr create must be genuinely attempted for a code-relevant branch"
        assert "pr create" in gh_call_log.read_text()

        task_yaml = task.load_task_yaml()
        # fake claude always rejects -- proves the real review path ran, distinct from the
        # docs-only guard's own early-exit 'completed_docs_only' status.
        assert task_yaml["checkpoints"][-1]["status"] == "blocked"


def test_mixed_diff_still_creates_pr(tmp_path, git_repo_pair, fake_home, scratch_db):
    """A diff with BOTH a progress note and a real code file is code-relevant -- the guard
    must not be fooled by the presence of a progress/*.md file alongside real work."""
    workspace = _make_workspace(tmp_path, git_repo_pair["origin"], git_repo_pair["mixed_branch"], "ws_mixed")

    with _RealTaskDir({
        "workspace": str(workspace), "branch": git_repo_pair["mixed_branch"],
    }) as task:
        result = _run_supervisor(task.task_id, fake_home, scratch_db)

        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
        gh_call_log = fake_home / "gh_calls.log"
        assert gh_call_log.exists() and "pr create" in gh_call_log.read_text()

        marker_path = os.path.join(task.task_dir, "docs_only_completion.json")
        assert not os.path.exists(marker_path)


def test_switch_disabled_reverts_to_unconditional_pr_creation(tmp_path, git_repo_pair, fake_home, scratch_db):
    """The named revert switch: VERIDIAN_GATE_PR_ON_CODE_CHANGE=0 must restore the exact
    prior unconditional behavior (a PR attempted even for a docs-only diff), without any
    code change/redeploy -- proving the switch is real, not decorative."""
    workspace = _make_workspace(tmp_path, git_repo_pair["origin"], git_repo_pair["docs_branch"], "ws_docs_switch_off")

    with _RealTaskDir({
        "workspace": str(workspace), "branch": git_repo_pair["docs_branch"],
    }) as task:
        result = _run_supervisor(task.task_id, fake_home, scratch_db,
                                  extra_env={"VERIDIAN_GATE_PR_ON_CODE_CHANGE": "0"})

        assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"

        marker_path = os.path.join(task.task_dir, "docs_only_completion.json")
        assert not os.path.exists(marker_path), "guard must be fully inert when the switch is off"

        gh_call_log = fake_home / "gh_calls.log"
        assert gh_call_log.exists() and "pr create" in gh_call_log.read_text(), (
            "switch=0 must restore the prior unconditional gh pr create attempt even for a docs-only diff"
        )


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
