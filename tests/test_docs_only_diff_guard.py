#!/usr/bin/env python3
"""Unit-level regression coverage for docs_only_diff_guard.py's own exit-code
contract (UMR-20260816-171513-5901; fixed 2026-08-17 after a real audit
finding on PR #444, head 499d1266).

Real defect this closes: the guard's exit-code contract used to be exit 0 =
code-relevant, exit 1 = "no PR" for BOTH the intentional docs-only trip AND
any unexpected guard failure (a broken `git diff`, or quality-gate.sh's
allowlist regexes moving) -- both callers (supervisor-entrypoint.sh,
dispatch-owner-task.sh) checked only `$? -ne 0`, so a crashed guard was
silently treated exactly like a genuine docs-only diff: no PR opened, and in
supervisor-entrypoint.sh's case, any pre-existing real PR actively closed.

These tests exercise the module directly (no subprocess, no fakes) to prove
the three-way exit code split: 0 (code-relevant), 1 (real docs-only trip),
2 (guard error -- NOT a docs-only signal).
"""
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import docs_only_diff_guard as guard  # noqa: E402


def test_exit_0_when_code_relevant(tmp_path):
    qgate = tmp_path / "quality-gate.sh"
    qgate.write_text("DOCS_ONLY_EXT_PATTERN='\\.(md)$'\nDOCS_ONLY_NAME_PATTERN='^nomatch$'\n")
    assert guard.is_code_relevant(["real_fix.py"], quality_gate_path=str(qgate)) is True


def test_exit_1_docs_only_when_all_files_match_allowlist(tmp_path):
    qgate = tmp_path / "quality-gate.sh"
    qgate.write_text("DOCS_ONLY_EXT_PATTERN='\\.(md)$'\nDOCS_ONLY_NAME_PATTERN='^nomatch$'\n")
    assert guard.is_code_relevant(["progress/task-x.md"], quality_gate_path=str(qgate)) is False


def test_exit_1_docs_only_when_zero_files_changed(tmp_path):
    qgate = tmp_path / "quality-gate.sh"
    qgate.write_text("DOCS_ONLY_EXT_PATTERN='\\.(md)$'\nDOCS_ONLY_NAME_PATTERN='^nomatch$'\n")
    assert guard.is_code_relevant([], quality_gate_path=str(qgate)) is False


def test_changed_files_raises_guard_error_on_broken_git_diff(tmp_path):
    """A `git diff` failure (e.g. an unknown ref) must raise GuardError, never
    silently return an empty file list -- that silent-empty-list path is
    exactly what the 2026-08-17 audit found: it produced a false docs-only
    trip indistinguishable from a real one."""
    with pytest.raises(guard.GuardError):
        guard.changed_files(str(tmp_path), "this-ref-does-not-exist", head_ref="also-does-not-exist")


def test_docs_only_patterns_raises_guard_error_when_regexes_missing(tmp_path):
    """If quality-gate.sh's own allowlist regexes have moved/changed shape,
    the guard must raise GuardError (mapped by main() to exit 2), never
    silently fall back to treating the diff as docs-only."""
    qgate = tmp_path / "quality-gate.sh"
    qgate.write_text("# no DOCS_ONLY_* assignments here at all\n")
    with pytest.raises(guard.GuardError):
        guard._docs_only_patterns(quality_gate_path=str(qgate))


def _run_cli(args, cwd=None):
    return subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "docs_only_diff_guard.py")] + args,
        capture_output=True, text=True, cwd=cwd, timeout=30,
    )


def test_cli_exit_2_on_guard_error_not_1(tmp_path):
    """End-to-end CLI check: a broken git diff must exit 2 (GUARD ERROR),
    bit-for-bit distinct from exit 1 (real docs-only trip) -- this is the
    exact ambiguity the audit found and this fix closes."""
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    result = _run_cli([str(tmp_path), "this-ref-does-not-exist"])
    assert result.returncode == guard.EXIT_GUARD_ERROR == 2, (
        f"expected exit 2 (guard error) for a broken git diff, not exit 1 "
        f"(which callers read as a real docs-only trip): rc={result.returncode}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    assert "GUARD ERROR" in result.stderr


def test_cli_exit_1_on_real_docs_only_diff(tmp_path):
    """Sanity check the real trip path still exits 1 (not 2) so existing
    callers' `-eq 1` branch keeps firing for genuine docs-only diffs."""
    repo = tmp_path / "repo"
    repo.mkdir()
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
    })
    subprocess.run(["git", "init", "--quiet", str(repo)], check=True, env=env)
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/master"], cwd=repo, check=True, env=env)
    (repo / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "checkout", "-q", "-b", "docs-branch"], cwd=repo, check=True, env=env)
    (repo / "progress").mkdir()
    (repo / "progress" / "note.md").write_text("note\n")
    subprocess.run(["git", "add", "progress/note.md"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "progress note"], cwd=repo, check=True, env=env)

    result = _run_cli([str(repo), "master"])
    assert result.returncode == guard.EXIT_DOCS_ONLY == 1, (
        f"expected exit 1 (real docs-only trip): rc={result.returncode}\nstderr={result.stderr}"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
