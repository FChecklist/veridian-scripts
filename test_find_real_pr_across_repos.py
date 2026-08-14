#!/usr/bin/env python3
"""
Real test for resource_governor.py's find_real_pr_across_repos() /
_umr_cross_repo_pr_check() -- UMR-20260814-092508-8a6b.

Real incident this closes, 2026-08-14: a governance/integration UMR's real
deliverable PR landed in the veridian-scripts repo, but was dispatched
against and tracked under claude-control. Because no existing lookup path
ever searched past the one repo a UMR was dispatched against, multiple
rounds of a PM tier wrongly concluded the real claude-control PR was
fake/orphaned -- wasted significant real investigation time before a human
caught it by manually checking veridian-scripts by hand.

These tests never call the real `gh` CLI -- they replace resource_governor's
own `_run()` subprocess wrapper with a deterministic fake keyed on which
`--repo` a given `gh pr list` invocation names, so the exact real-incident
shape (a match ONLY in a repo other than the one searched/dispatched first)
is reproduced without any network access.
"""
import importlib.util as _ilu
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
RG_PATH = os.path.join(HERE, "resource_governor.py")

_rg_spec = _ilu.spec_from_file_location("find_real_pr_rg_test_mod", RG_PATH)
rg = _ilu.module_from_spec(_rg_spec)
sys.modules["find_real_pr_rg_test_mod"] = rg
_rg_spec.loader.exec_module(rg)


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="[]"):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


def _repo_from_cmd(cmd):
    idx = cmd.index("--repo")
    return cmd[idx + 1].split("/", 1)[1]  # "FChecklist/<repo>" -> "<repo>"


class FindRealPrAcrossReposTest(unittest.TestCase):
    def setUp(self):
        self._orig_run = rg._run
        self._orig_append_attention = rg._append_attention
        self.attention_messages = []
        rg._append_attention = lambda msg: self.attention_messages.append(msg)

    def tearDown(self):
        rg._run = self._orig_run
        rg._append_attention = self._orig_append_attention

    def test_finds_pr_that_exists_only_in_a_different_repo_than_searched_first(self):
        """The core real-incident regression test: claude-control (the repo
        this hypothetical UMR was dispatched against, and the FIRST repo in
        the search order) has NO matching PR at all; the real PR only exists
        in veridian-scripts, a DIFFERENT repo. find_real_pr_across_repos()
        must still find it because it searches every repo, not just the
        first."""
        def fake_run(cmd, **kw):
            self.assertEqual(cmd[0:3], ["gh", "pr", "list"])
            repo = _repo_from_cmd(cmd)
            if repo == "claude-control":
                return _FakeCompletedProcess(0, "[]")  # real repo, no match -- the false trail
            if repo == "veridian-scripts":
                return _FakeCompletedProcess(0, json.dumps([
                    {"number": 371, "title": "governance/integration deliverable",
                     "state": "MERGED", "mergedAt": "2026-08-14T08:00:00Z"},
                ]))
            return _FakeCompletedProcess(0, "[]")

        rg._run = fake_run
        matches = rg.find_real_pr_across_repos(
            "governance/integration deliverable",
            known_repos=["claude-control", "veridian-scripts", "projexa"],
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["repo"], "veridian-scripts")
        self.assertEqual(matches[0]["number"], 371)
        self.assertEqual(matches[0]["state"], "MERGED")

    def test_returns_every_match_not_just_the_first(self):
        """Real matches can legitimately exist in more than one repo (e.g. a
        docs mirror PR); find_real_pr_across_repos() must return ALL of
        them, not stop at the first hit like find_pr_for_task_identity()
        deliberately does for its own (different) duplicate-guard purpose."""
        def fake_run(cmd, **kw):
            repo = _repo_from_cmd(cmd)
            if repo in ("claude-control", "veridian-scripts"):
                return _FakeCompletedProcess(0, json.dumps([
                    {"number": 42, "title": "same real deliverable",
                     "state": "OPEN", "mergedAt": None},
                ]))
            return _FakeCompletedProcess(0, "[]")

        rg._run = fake_run
        matches = rg.find_real_pr_across_repos(
            "same real deliverable",
            known_repos=["claude-control", "veridian-scripts", "projexa"],
        )
        self.assertEqual(len(matches), 2)
        self.assertEqual({m["repo"] for m in matches}, {"claude-control", "veridian-scripts"})

    def test_empty_query_text_short_circuits(self):
        rg._run = lambda cmd, **kw: (_ for _ in ()).throw(AssertionError("must not call gh"))
        self.assertEqual(rg.find_real_pr_across_repos(""), [])
        self.assertEqual(rg.find_real_pr_across_repos(None), [])

    def test_one_repo_gh_timeout_does_not_hide_matches_in_other_repos(self):
        import subprocess

        def fake_run(cmd, **kw):
            repo = _repo_from_cmd(cmd)
            if repo == "claude-control":
                raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
            if repo == "veridian-scripts":
                return _FakeCompletedProcess(0, json.dumps([
                    {"number": 371, "title": "x", "state": "MERGED", "mergedAt": "2026-08-14T08:00:00Z"},
                ]))
            return _FakeCompletedProcess(0, "[]")

        rg._run = fake_run
        matches = rg.find_real_pr_across_repos("x", known_repos=["claude-control", "veridian-scripts"])
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["repo"], "veridian-scripts")
        self.assertTrue(any("claude-control" in m for m in self.attention_messages))

    def test_defaults_to_all_known_repos_when_known_repos_omitted(self):
        seen_repos = []

        def fake_run(cmd, **kw):
            seen_repos.append(_repo_from_cmd(cmd))
            return _FakeCompletedProcess(0, "[]")

        rg._run = fake_run
        rg.find_real_pr_across_repos("anything")
        self.assertEqual(set(seen_repos), set(rg.ALL_KNOWN_REPOS))
        self.assertEqual(len(rg.ALL_KNOWN_REPOS), 8)


class UmrCrossRepoPrCheckTest(unittest.TestCase):
    """Real test of the --query-umr reporting-path wiring: given a UMR
    dispatched against claude-control with no PR there, the automatic
    fallback must find the real PR that actually landed in
    veridian-scripts -- reproducing the exact real 2026-08-14 incident."""

    def setUp(self):
        self._orig_run = rg._run
        rg._append_attention = lambda msg: None

    def tearDown(self):
        rg._run = self._orig_run

    def test_falls_through_to_other_repos_when_dispatched_repo_has_no_match(self):
        def fake_run(cmd, **kw):
            repo = _repo_from_cmd(cmd)
            if repo == "claude-control":
                return _FakeCompletedProcess(0, "[]")
            if repo == "veridian-scripts":
                return _FakeCompletedProcess(0, json.dumps([
                    {"number": 371, "title": "governance/integration deliverable",
                     "state": "MERGED", "mergedAt": "2026-08-14T08:00:00Z"},
                ]))
            return _FakeCompletedProcess(0, "[]")

        rg._run = fake_run
        result = rg._umr_cross_repo_pr_check(
            "task-20260814-090001-govint", "UMR-20260814-090001-aaaa", "claude-control",
            title="governance/integration deliverable",
        )
        self.assertEqual(result["dispatched_repo"], "claude-control")
        self.assertEqual(result["dispatched_repo_matches"], [])
        self.assertTrue(result["checked_other_repos"])
        self.assertNotIn("claude-control", result["other_repos_checked"])
        self.assertEqual(len(result["other_repos_checked"]), len(rg.ALL_KNOWN_REPOS) - 1)
        self.assertTrue(result["found_in_different_repo_than_dispatched"])
        self.assertEqual(result["other_repo_matches"][0]["repo"], "veridian-scripts")
        self.assertEqual(result["other_repo_matches"][0]["number"], 371)

    def test_does_not_check_other_repos_when_dispatched_repo_already_has_a_match(self):
        calls = []

        def fake_run(cmd, **kw):
            repo = _repo_from_cmd(cmd)
            calls.append(repo)
            if repo == "claude-control":
                return _FakeCompletedProcess(0, json.dumps([
                    {"number": 99, "title": "x", "state": "OPEN", "mergedAt": None},
                ]))
            return _FakeCompletedProcess(0, "[]")

        rg._run = fake_run
        result = rg._umr_cross_repo_pr_check(
            "task-20260814-090002-x", "UMR-20260814-090002-bbbb", "claude-control", title="x",
        )
        self.assertEqual(calls, ["claude-control"])  # never scanned any other repo
        self.assertFalse(result["checked_other_repos"])
        self.assertEqual(result["other_repos_checked"], [])
        self.assertFalse(result["found_in_different_repo_than_dispatched"])


if __name__ == "__main__":
    unittest.main()
