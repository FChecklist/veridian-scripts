#!/usr/bin/env python3
"""UMR-20260813-235552-dc9a: real regression coverage for gitlink_guard.py
and its wiring into supervisor-entrypoint.sh's GITLINK-GUARD-BLOCK.

Real defect this closes: a worker whose workspace was checked out from the
WRONG repo (its task.yaml's `repo:` field said `claude-control` for a task
whose real target was `veridian-scripts`) improvised a nested `git clone` of
the correct repo inside its own workspace to do its real work (observed
directory names `veridian-scripts-work`, `veridian-scripts-clean`), and
worker-entrypoint.sh's own `git add -A` checkpoint commits swept that nested
`.git` directory in as a bare submodule gitlink (mode 160000). Nothing
downstream caught it: supervisor-entrypoint.sh's `gh pr create` fired
unconditionally and opened a real PR whose entire diff was that one gitlink
entry -- looks like delivered work, contains none. Real, directly observed
evidence: FChecklist/claude-control PRs #146, #170, #191, each "1 file
changed, 1 insertion(+)", each just the gitlink.

Two layers, same real-git convention this codebase already established
(test_supervisor_no_op_branch_guard.py, PR #329):

1. Direct, fast unit coverage of gitlink_guard.py's own functions against
   real temp git repos -- including a real NESTED repo (a second `git init`
   inside the first, exactly reproducing the incident) and a real,
   genuinely pre-existing, `.gitmodules`-declared submodule (via real
   `git submodule add`) as the negative control that must NOT be flagged.
2. One real end-to-end subprocess test of the actual installed
   supervisor-entrypoint.sh, reproducing PR #146/#170/#191 exactly: a real
   pushed branch whose only diff vs. base is a stray gitlink. Asserts the
   guard refuses with the specific offending path, `gh pr create` is never
   invoked, and the task is checkpointed blocked (not silently marked done).
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
import gitlink_guard  # noqa: E402

SUPERVISOR_SCRIPT = os.path.join(REPO_ROOT, "supervisor-entrypoint.sh")
AI_OS = "/opt/veridian/ai-os"

GIT_ENV = dict(os.environ)
GIT_ENV.update({
    "GIT_AUTHOR_NAME": "Test Bot", "GIT_AUTHOR_EMAIL": "test-bot@example.com",
    "GIT_COMMITTER_NAME": "Test Bot", "GIT_COMMITTER_EMAIL": "test-bot@example.com",
    "GIT_ALLOW_PROTOCOL": "file",
})


def _git(args, cwd, check=True):
    r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True,
                        env=GIT_ENV, timeout=30)
    if check:
        assert r.returncode == 0, f"git {args} failed in {cwd}\nstdout={r.stdout}\nstderr={r.stderr}"
    return r.stdout.strip()


# --- Layer 1: direct unit coverage of gitlink_guard.py --------------------

def test_nested_repo_swept_by_add_dash_a_is_flagged_staged(tmp_path):
    """Exact reproduction of the real incident's mechanics: a second, real git
    repo nested inside the first. `git add -A` in the outer repo records it as
    a bare gitlink -- the guard must catch it before commit."""
    outer = tmp_path / "outer"
    outer.mkdir()
    _git(["init", "-b", "main"], cwd=outer)
    (outer / "README.md").write_text("outer repo\n")
    _git(["add", "README.md"], cwd=outer)
    _git(["commit", "-m", "outer seed"], cwd=outer)

    nested = outer / "veridian-scripts-work"
    nested.mkdir()
    _git(["init", "-b", "main"], cwd=nested)
    (nested / "real_fix.py").write_text("print('the real fix lives here')\n")
    _git(["add", "real_fix.py"], cwd=nested)
    _git(["commit", "-m", "real fix, real repo, wrong parent directory"], cwd=nested)

    # This is the exact call worker-entrypoint.sh's old, unguarded checkpoint
    # commits made every time.
    _git(["add", "-A"], cwd=outer)

    violations = gitlink_guard.find_illegitimate_gitlinks_staged(str(outer), "HEAD")
    assert violations == ["veridian-scripts-work"], violations


def test_ordinary_file_changes_are_never_flagged(tmp_path):
    """Negative control: real, ordinary file adds/edits must never trip the
    guard -- it only ever looks at mode 160000 entries."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "main"], cwd=repo)
    (repo / "a.py").write_text("x = 1\n")
    _git(["add", "a.py"], cwd=repo)
    _git(["commit", "-m", "seed"], cwd=repo)

    (repo / "a.py").write_text("x = 2\n")
    (repo / "b.py").write_text("y = 3\n")
    _git(["add", "-A"], cwd=repo)

    assert gitlink_guard.find_illegitimate_gitlinks_staged(str(repo), "HEAD") == []


def test_genuine_preexisting_declared_submodule_is_not_flagged(tmp_path):
    """The guard must never false-positive on a real, intentional submodule
    this repo already had -- only on a NEW/undeclared gitlink. Uses a real
    `git submodule add` against a real local repo (never a fake .gitmodules
    stub), then advances the submodule to a new real commit and confirms that
    ordinary submodule-bump workflow still passes clean."""
    sub_origin = tmp_path / "sub_origin"
    sub_origin.mkdir()
    _git(["init", "-b", "main"], cwd=sub_origin)
    (sub_origin / "f.txt").write_text("v1\n")
    _git(["add", "f.txt"], cwd=sub_origin)
    _git(["commit", "-m", "v1"], cwd=sub_origin)

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "main"], cwd=repo)
    (repo / "README.md").write_text("has a real submodule\n")
    _git(["add", "README.md"], cwd=repo)
    _git(["commit", "-m", "seed"], cwd=repo)
    _git(["-c", "protocol.file.allow=always", "submodule", "add", str(sub_origin), "vendor/sub"], cwd=repo)
    _git(["commit", "-m", "add real submodule"], cwd=repo)
    base_ref = "HEAD"

    # Real submodule commit bump (ordinary, legitimate workflow) -- must not
    # be flagged as illegitimate relative to its own prior state.
    (sub_origin / "f.txt").write_text("v2\n")
    _git(["add", "f.txt"], cwd=sub_origin)
    _git(["commit", "-m", "v2"], cwd=sub_origin)
    _git(["-c", "protocol.file.allow=always", "fetch"], cwd=repo / "vendor" / "sub")
    _git(["-c", "protocol.file.allow=always", "-C", "vendor/sub", "checkout", "origin/main"], cwd=repo)
    _git(["add", "-A"], cwd=repo)

    violations = gitlink_guard.find_illegitimate_gitlinks_staged(str(repo), base_ref)
    assert violations == [], violations


def test_new_undeclared_gitlink_at_committed_range_is_flagged(tmp_path):
    """The other real call site: a nested repo that was already committed and
    pushed on some branch (not just staged) -- the range-based check
    supervisor-entrypoint.sh uses right before `gh pr create`."""
    base = tmp_path / "base_repo"
    base.mkdir()
    _git(["init", "-b", "master"], cwd=base)
    (base / "README.md").write_text("base\n")
    _git(["add", "README.md"], cwd=base)
    _git(["commit", "-m", "seed"], cwd=base)
    base_sha = _git(["rev-parse", "HEAD"], cwd=base)

    _git(["checkout", "-b", "worker/test"], cwd=base)
    nested = base / "veridian-scripts-clean"
    nested.mkdir()
    _git(["init", "-b", "main"], cwd=nested)
    (nested / "fix.py").write_text("real fix\n")
    _git(["add", "fix.py"], cwd=nested)
    _git(["commit", "-m", "real fix in the wrong place"], cwd=nested)
    _git(["add", "-A"], cwd=base)
    _git(["commit", "-m", "Worker: automated checkpoint commit"], cwd=base)

    violations = gitlink_guard.find_illegitimate_gitlinks_in_range(str(base), base_sha, "HEAD")
    assert violations == ["veridian-scripts-clean"], violations


def test_gitlink_removal_is_never_flagged(tmp_path):
    """Deleting a gitlink is never the fake-PR risk this guard exists for --
    must pass clean even though the diff still contains a mode-160000 line."""
    sub_origin = tmp_path / "sub_origin"
    sub_origin.mkdir()
    _git(["init", "-b", "main"], cwd=sub_origin)
    (sub_origin / "f.txt").write_text("v1\n")
    _git(["add", "f.txt"], cwd=sub_origin)
    _git(["commit", "-m", "v1"], cwd=sub_origin)

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "main"], cwd=repo)
    (repo / "README.md").write_text("will remove its submodule\n")
    _git(["add", "README.md"], cwd=repo)
    _git(["commit", "-m", "seed"], cwd=repo)
    _git(["-c", "protocol.file.allow=always", "submodule", "add", str(sub_origin), "vendor/sub"], cwd=repo)
    _git(["commit", "-m", "add real submodule"], cwd=repo)
    base_sha = _git(["rev-parse", "HEAD"], cwd=repo)

    _git(["rm", "-f", "vendor/sub"], cwd=repo)
    _git(["commit", "-m", "remove submodule"], cwd=repo)

    violations = gitlink_guard.find_illegitimate_gitlinks_in_range(str(repo), base_sha, "HEAD")
    assert violations == [], violations


def test_cli_exit_code_and_output(tmp_path):
    """The real CLI contract worker-entrypoint.sh/supervisor-entrypoint.sh
    depend on: non-zero exit with the offending path on stdout when a
    violation exists, zero exit and empty stdout when clean."""
    outer = tmp_path / "outer"
    outer.mkdir()
    _git(["init", "-b", "main"], cwd=outer)
    (outer / "README.md").write_text("x\n")
    _git(["add", "README.md"], cwd=outer)
    _git(["commit", "-m", "seed"], cwd=outer)
    nested = outer / "veridian-scripts-work"
    nested.mkdir()
    _git(["init", "-b", "main"], cwd=nested)
    (nested / "f.py").write_text("x = 1\n")
    _git(["add", "f.py"], cwd=nested)
    _git(["commit", "-m", "real fix"], cwd=nested)
    _git(["add", "-A"], cwd=outer)

    r = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "gitlink_guard.py"), str(outer), "HEAD", "--staged"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 1
    assert r.stdout.strip() == "veridian-scripts-work"
    assert "GITLINK GUARD" in r.stderr
    assert "UMR-20260813-235552-dc9a" in r.stderr

    _git(["reset", "--", "veridian-scripts-work"], cwd=outer)
    r2 = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "gitlink_guard.py"), str(outer), "HEAD", "--staged"],
        capture_output=True, text=True, timeout=30,
    )
    assert r2.returncode == 0
    assert r2.stdout.strip() == ""


# --- Layer 2: real end-to-end supervisor-entrypoint.sh subprocess ---------

_FAKE_GH = """#!/bin/bash
echo "gh $*" >> "$GH_CALL_LOG"
exit 0
"""

_FAKE_CLAUDE = """#!/bin/bash
cat > review-verdict.json <<'EOF'
{"verdict": "approve", "tier": "tier1", "summary": "stub -- should never run, guard must exit first", "issues": []}
EOF
echo '{"result": "stub claude cli output", "total_cost_usd": 0}'
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


@pytest.fixture()
def scratch_db(tmp_path):
    return str(tmp_path / "scratch-superboss-register.sqlite")


class _RealTaskDir:
    def __init__(self, task_yaml_extra):
        self.task_id = f"test-gitlink-guard-{uuid.uuid4().hex[:12]}"
        self.task_dir = os.path.join(AI_OS, "tasks", self.task_id)
        self._extra = task_yaml_extra

    def __enter__(self):
        os.makedirs(self.task_dir, exist_ok=True)
        task = {
            "id": self.task_id,
            "title": "test: gitlink guard regression",
            "status": "pending_review",
            "repo": "claude-control",
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


def _run_supervisor(task_id, fake_home, scratch_db, timeout=120):
    env = dict(os.environ)
    env["HOME"] = str(fake_home)
    env["SUPERBOSS_REGISTER_DB"] = scratch_db
    env["GH_CALL_LOG"] = str(fake_home / "gh_calls.log")
    return subprocess.run(
        [SUPERVISOR_SCRIPT, task_id], capture_output=True, text=True, env=env, timeout=timeout,
    )


def test_supervisor_refuses_gitlink_only_branch_exact_pr146_170_191_repro(
        tmp_path, fake_home, scratch_db):
    """End-to-end reproduction of the real PR #146/#170/#191 shape: a pushed
    branch whose ONLY diff vs. base is a stray gitlink for a nested checkout.
    The real installed supervisor-entrypoint.sh must refuse to open a PR,
    must never call `gh pr create`, and must checkpoint the task blocked with
    the offending path named in the note -- not silently ship an empty PR."""
    origin = tmp_path / "origin.git"
    _git(["init", "--bare", str(origin)], cwd=tmp_path)
    _git(["symbolic-ref", "HEAD", "refs/heads/master"], cwd=origin)

    seed = tmp_path / "seed"
    seed.mkdir()
    _git(["init", "-b", "master"], cwd=seed)
    (seed / "README.md").write_text("seed\n")
    _git(["add", "README.md"], cwd=seed)
    _git(["commit", "-m", "seed"], cwd=seed)
    _git(["remote", "add", "origin", str(origin)], cwd=seed)
    _git(["push", "origin", "master"], cwd=seed)

    branch = "worker/test-gitlink-only"
    _git(["checkout", "-b", branch], cwd=seed)
    nested = seed / "veridian-scripts-work"
    nested.mkdir()
    _git(["init", "-b", "main"], cwd=nested)
    (nested / "real_fix.py").write_text("this is where the real work actually happened\n")
    _git(["add", "real_fix.py"], cwd=nested)
    _git(["commit", "-m", "real fix, wrong repo boundary"], cwd=nested)
    _git(["add", "-A"], cwd=seed)  # exactly what the OLD, unguarded checkpoint commit did
    _git(["commit", "-m", "Worker: automated checkpoint commit"], cwd=seed)
    _git(["push", "origin", branch], cwd=seed)

    workspace = tmp_path / "ws"
    _git(["clone", str(origin), str(workspace)], cwd=tmp_path)
    _git(["checkout", branch], cwd=workspace)

    with _RealTaskDir({"workspace": str(workspace), "branch": branch}) as task:
        result = _run_supervisor(task.task_id, fake_home, scratch_db)

        assert result.returncode == 1, (
            f"expected the gitlink guard to hard-refuse, got exit {result.returncode}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )

        gh_call_log = fake_home / "gh_calls.log"
        assert not gh_call_log.exists(), (
            "gh pr create must never be invoked once the gitlink guard trips -- "
            f"but it was: {gh_call_log.read_text() if gh_call_log.exists() else ''}"
        )

        task_yaml = task.load_task_yaml()
        note = task_yaml["checkpoints"][-1].get("note", "")
        assert task_yaml["checkpoints"][-1]["status"] == "blocked", task_yaml["checkpoints"]
        assert "veridian-scripts-work" in note, note
        assert "160000" in note or "gitlink" in note.lower(), note


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
