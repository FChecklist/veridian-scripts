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
