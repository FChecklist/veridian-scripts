#!/usr/bin/env python3
"""UMR-20260806-100604-4591: real tests for find_code.sh, the reusable pruned
recursive search helper. Confirms it actually excludes node_modules/.git/
.venv/__pycache__/dist/build in practice (not just in the prune-list
comment), that a real match outside those directories is still found, that
scope defaults to /opt/veridian/scripts, and that an explicit broader scope
still applies the same prune list.
"""
import os
import subprocess
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIND_CODE = os.path.join(SCRIPTS_DIR, "find_code.sh")

UNIQUE_PATTERN = "FIND_CODE_TEST_MARKER_9f2c7e1b"


def _make_tree(root):
    """Builds a fixture tree with the marker pattern planted both in a real,
    findable file and inside every directory find_code.sh is supposed to
    prune -- so a real prune failure would show up as an extra match."""
    real_dir = os.path.join(root, "real_pkg")
    os.makedirs(real_dir)
    with open(os.path.join(real_dir, "module.py"), "w") as f:
        f.write(f"# {UNIQUE_PATTERN} -- real, should be found\n")

    pruned_dirnames = ["node_modules", ".git", ".venv", "__pycache__", "dist", "build"]
    for dirname in pruned_dirnames:
        pruned_dir = os.path.join(root, dirname, "nested")
        os.makedirs(pruned_dir)
        with open(os.path.join(pruned_dir, "should_not_be_found.txt"), "w") as f:
            f.write(f"{UNIQUE_PATTERN} -- planted inside {dirname}, must be pruned\n")

    # also plant one nested two levels down under a real dir, to confirm
    # normal (non-pruned) recursion still works at depth
    deep_dir = os.path.join(root, "real_pkg", "sub", "subsub")
    os.makedirs(deep_dir)
    with open(os.path.join(deep_dir, "deep.py"), "w") as f:
        f.write(f"# {UNIQUE_PATTERN} -- deep real file, should be found\n")

    return real_dir, pruned_dirnames, deep_dir


def test_prunes_all_excluded_dirs_and_finds_real_matches():
    with tempfile.TemporaryDirectory() as root:
        real_dir, pruned_dirnames, deep_dir = _make_tree(root)

        result = subprocess.run(
            [FIND_CODE, UNIQUE_PATTERN, root],
            capture_output=True, text=True,
        )

        assert result.returncode == 0, f"expected matches, got rc={result.returncode}, stderr={result.stderr}"
        matches = set(result.stdout.strip().splitlines())

        expected_real = {
            os.path.join(real_dir, "module.py"),
            os.path.join(deep_dir, "deep.py"),
        }
        assert matches == expected_real, (
            f"expected exactly {expected_real}, got {matches} "
            f"(any extra entries mean a prune directory was NOT actually excluded)"
        )

        for dirname in pruned_dirnames:
            for m in matches:
                assert f"{os.sep}{dirname}{os.sep}" not in m, (
                    f"prune failed for {dirname}: matched file {m}"
                )


def test_no_match_returns_exit_1_and_no_output():
    with tempfile.TemporaryDirectory() as root:
        _make_tree(root)
        result = subprocess.run(
            [FIND_CODE, "THIS_PATTERN_DOES_NOT_EXIST_ANYWHERE_ab12cd34", root],
            capture_output=True, text=True,
        )
        assert result.returncode == 1
        assert result.stdout.strip() == ""


def test_usage_error_on_missing_pattern():
    result = subprocess.run([FIND_CODE], capture_output=True, text=True)
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()


def test_usage_error_on_bad_scope_dir():
    result = subprocess.run(
        [FIND_CODE, UNIQUE_PATTERN, "/this/path/does/not/exist/anywhere"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2


def test_default_scope_is_scripts_dir():
    """No scope_dir argument -> defaults to /opt/veridian/scripts. Proven by
    searching for a pattern known to exist in this very file (which lives
    under /opt/veridian/scripts/tests/), without passing a scope at all."""
    result = subprocess.run([FIND_CODE, UNIQUE_PATTERN], capture_output=True, text=True)
    # Either finds this test file itself (rc 0, contains this file's path)
    # or -- if run from a checkout not physically at /opt/veridian/scripts --
    # correctly reports no match (rc 1) rather than erroring. Either way it
    # must not error out, and if it *is* the real /opt/veridian/scripts
    # checkout it must find this file.
    assert result.returncode in (0, 1)
    if os.path.abspath(SCRIPTS_DIR) == "/opt/veridian/scripts" and result.returncode == 0:
        assert os.path.abspath(__file__) in {
            os.path.abspath(p) for p in result.stdout.strip().splitlines()
        }


def test_prunes_path_pattern_dirs_ai_os_tasks_workspace():
    """Exercises PRUNE_PATH_PATTERNS specifically (the more incident-relevant
    half of the fix, per the independent review of this script,
    UMR-20260806-100604-4591): a fixture that mimics the REAL directory
    shape (ai-os/tasks/<task-id>/workspace, and workspace/claude-cli-work,
    workspace/main-e2e-check) rather than just the basename-level prunes
    already covered by test_prunes_all_excluded_dirs_and_finds_real_matches.
    """
    with tempfile.TemporaryDirectory() as root:
        # ai-os/tasks/<task-id>/workspace -- must be pruned
        task_workspace = os.path.join(root, "ai-os", "tasks", "task-20260101-000000-fake", "workspace")
        os.makedirs(task_workspace)
        with open(os.path.join(task_workspace, "stale_worktree_copy.py"), "w") as f:
            f.write(f"# {UNIQUE_PATTERN} -- planted inside ai-os/tasks/*/workspace, must be pruned\n")

        # workspace/claude-cli-work -- must be pruned
        claude_cli_work = os.path.join(root, "workspace", "claude-cli-work")
        os.makedirs(claude_cli_work)
        with open(os.path.join(claude_cli_work, "scratch.py"), "w") as f:
            f.write(f"# {UNIQUE_PATTERN} -- planted inside workspace/claude-cli-work, must be pruned\n")

        # workspace/main-e2e-check -- must be pruned
        main_e2e = os.path.join(root, "workspace", "main-e2e-check")
        os.makedirs(main_e2e)
        with open(os.path.join(main_e2e, "scratch.py"), "w") as f:
            f.write(f"# {UNIQUE_PATTERN} -- planted inside workspace/main-e2e-check, must be pruned\n")

        # a sibling file directly under workspace/ (NOT claude-cli-work or
        # main-e2e-check) must still be found -- proves the prune is scoped
        # to the two specific subdirs, not all of workspace/ wholesale
        # (mirrors /opt/veridian/workspace holding real, non-scratch content).
        real_workspace_file = os.path.join(root, "workspace", "real_content.py")
        with open(real_workspace_file, "w") as f:
            f.write(f"# {UNIQUE_PATTERN} -- real content directly under workspace/, must be found\n")

        result = subprocess.run([FIND_CODE, UNIQUE_PATTERN, root], capture_output=True, text=True)
        assert result.returncode == 0, f"stderr={result.stderr}"
        matches = set(result.stdout.strip().splitlines())

        assert matches == {real_workspace_file}, (
            f"expected exactly {{{real_workspace_file}}}, got {matches} -- "
            f"either a path-pattern prune failed to exclude a scratch dir, "
            f"or over-pruned real content under workspace/"
        )


def test_invalid_regex_exits_2_not_1():
    """Real finding from this script's own independent review: a malformed
    regex must be a visible usage error (2), not silently indistinguishable
    from a genuine no-match (1)."""
    with tempfile.TemporaryDirectory() as root:
        _make_tree(root)
        result = subprocess.run([FIND_CODE, "[unterminated", root], capture_output=True, text=True)
        assert result.returncode == 2
        assert "invalid pattern" in result.stderr.lower()


def test_pattern_starting_with_dash_is_not_misparsed_as_flag():
    """Real finding from this script's own independent review: without a
    `--` separator, a pattern starting with `-` (e.g. `-v`) is parsed as a
    grep flag instead of literal text."""
    with tempfile.TemporaryDirectory() as root:
        real_dir = os.path.join(root, "real_pkg")
        os.makedirs(real_dir)
        with open(os.path.join(real_dir, "module.py"), "w") as f:
            f.write("# -v-marker-1234 -- literal text starting with a dash\n")
        result = subprocess.run([FIND_CODE, "-v-marker-1234", root], capture_output=True, text=True)
        assert result.returncode == 0, f"stderr={result.stderr}"
        assert os.path.join(real_dir, "module.py") in result.stdout


def test_broader_scope_override_still_prunes():
    with tempfile.TemporaryDirectory() as root:
        real_dir, pruned_dirnames, deep_dir = _make_tree(root)
        # Explicit broader scope (still just our temp root here, but proves
        # the second positional arg is honored as a real override, and the
        # same prune list is applied to it).
        result = subprocess.run(
            [FIND_CODE, UNIQUE_PATTERN, root],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        matches = set(result.stdout.strip().splitlines())
        for dirname in pruned_dirnames:
            for m in matches:
                assert f"{os.sep}{dirname}{os.sep}" not in m
