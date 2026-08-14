"""Real regression tests for check_live_scripts_drift.py's tracked_tree_clean
and branch_pushed_to_origin fields (UMR-20260814-051532-2ae4).

Real gap this closes: every prior "reconcile live deploy drift" task had to
manually re-derive "is the tracked tree actually dirty" and "would switching
branches lose real unpushed work" via ad hoc `git status`/`git ls-remote`
before it was safe to touch the live checkout. These tests drive the real
git plumbing (temp bare origin + temp clone), not mocks.
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_spec = importlib.util.spec_from_file_location(
    "check_live_scripts_drift", os.path.join(SCRIPTS, "check_live_scripts_drift.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
check_drift = _mod.check_drift


def _run(argv, cwd):
    r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, f"{argv} failed in {cwd}: {r.stderr}"
    return r


def _git_env():
    env = dict(os.environ)
    env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "test"
    env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "test@example.com"
    return env


class CheckLiveScriptsDriftNewFieldsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = self._tmp.name
        self.origin = os.path.join(base, "origin.git")
        self.clone = os.path.join(base, "clone")
        os.makedirs(self.origin)
        _run(["git", "init", "--bare", "-b", "main", "."], cwd=self.origin)

        seed = os.path.join(base, "seed")
        os.makedirs(seed)
        _run(["git", "init", "-b", "main", "."], cwd=seed)
        with open(os.path.join(seed, "f.txt"), "w") as fh:
            fh.write("v1\n")
        _run(["git", "add", "f.txt"], cwd=seed)
        env = _git_env()
        subprocess.run(["git", "commit", "-m", "seed"], cwd=seed, env=env,
                        capture_output=True, text=True, check=True)
        _run(["git", "remote", "add", "origin", self.origin], cwd=seed)
        _run(["git", "push", "origin", "main"], cwd=seed)

        _run(["git", "clone", self.origin, self.clone], cwd=base)

    def test_clean_tree_on_main_reports_clean_and_no_branch_field_conflict(self):
        result, code = check_drift(self.clone)
        self.assertEqual(code, 0)
        self.assertTrue(result["in_sync"])
        self.assertTrue(result["tracked_tree_clean"])
        # main has no "origin/main" *feature-branch* ambiguity here, but the
        # field must still resolve (main is pushed to itself).
        self.assertTrue(result["branch_pushed_to_origin"])

    def test_dirty_tracked_tree_detected(self):
        with open(os.path.join(self.clone, "f.txt"), "w") as fh:
            fh.write("v2 -- uncommitted local edit\n")
        result, _code = check_drift(self.clone)
        self.assertFalse(result["tracked_tree_clean"])

    def test_clean_but_pushed_non_main_branch(self):
        env = _git_env()
        _run(["git", "checkout", "-b", "feature/real-pushed-work"], cwd=self.clone)
        with open(os.path.join(self.clone, "g.txt"), "w") as fh:
            fh.write("real committed work\n")
        _run(["git", "add", "g.txt"], cwd=self.clone)
        subprocess.run(["git", "commit", "-m", "real work"], cwd=self.clone, env=env,
                        capture_output=True, text=True, check=True)
        _run(["git", "push", "-u", "origin", "feature/real-pushed-work"], cwd=self.clone)

        result, code = check_drift(self.clone)
        self.assertEqual(code, 1)  # diverged from origin/main
        self.assertFalse(result["on_main_branch"])
        self.assertTrue(result["tracked_tree_clean"])
        self.assertTrue(result["branch_pushed_to_origin"])

    def test_clean_but_unpushed_non_main_branch(self):
        env = _git_env()
        _run(["git", "checkout", "-b", "feature/unpushed"], cwd=self.clone)
        with open(os.path.join(self.clone, "h.txt"), "w") as fh:
            fh.write("real committed but never pushed\n")
        _run(["git", "add", "h.txt"], cwd=self.clone)
        subprocess.run(["git", "commit", "-m", "unpushed work"], cwd=self.clone, env=env,
                        capture_output=True, text=True, check=True)
        # deliberately never pushed

        result, _code = check_drift(self.clone)
        self.assertFalse(result["on_main_branch"])
        self.assertTrue(result["tracked_tree_clean"])
        self.assertIsNone(result["branch_pushed_to_origin"])


if __name__ == "__main__":
    unittest.main()
