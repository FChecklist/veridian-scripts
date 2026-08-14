#!/usr/bin/env python3
"""Real regression test for the Stage 6 duplicate-PR-guard false positive
(UMR-20260813-172606-101a, governing chain UMR-20260813-102459-10c3).

Real incident this backstops: UMR-20260813-145418-3f98 dispatched a task
titled "Merge audit-passed PR 141 (server-native PM integration)"
(claude-control repo). find_pr_for_task_identity()'s Stage 6 extracted the
target PR number ('141') from that title, then scanned ALL existing PRs'
titles for the same number and matched claude-control#142, whose real title
is "docs: real dedup finding for UMR-20260813-092654-326b (already covered
by PR 141, not re-implemented)" -- #142 is real, unrelated work (a
docs-only PR about a different UMR) that only CITES "PR 141" to disclose it
deliberately does NOT re-implement it. The dispatch was wrongly rejected as
`rejected_duplicate_pr` against #142 while the real target, #141, was open
the entire time.

Every real `gh` call is mocked at the `_run()` subprocess boundary, same
convention as tests/test_target_pr_dispatch_time_recheck.py -- never a real
network call.
"""
import importlib.util
import json
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)

from unittest import mock


def _load_rg(name, env=None):
    old_env = {}
    for k, v in (env or {"VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}).items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v
    try:
        spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, "resource_governor.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def test_disclosure_citation_is_not_treated_as_a_duplicate():
    """The real #141/#142 shape verbatim: a title citing 'PR 141' only to
    disclose non-duplication must NOT be returned as the duplicate PR."""
    rg = _load_rg("rg_stage6_1")

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "pr", "list"] and "--head" in cmd:
            return _FakeCompletedProcess(0, json.dumps([]))  # no branch-name match
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompletedProcess(0, json.dumps([
                {"number": 142, "title": "docs: real dedup finding for "
                 "UMR-20260813-092654-326b (already covered by PR 141, "
                 "not re-implemented)"},
            ]))
        raise AssertionError(f"unexpected gh call: {cmd}")

    with mock.patch.object(rg, "_run", side_effect=fake_run):
        dup_pr, dup_repo = rg.find_pr_for_task_identity(
            "owner-task-20260813-145416-2337371", hint_repo="claude-control",
            title="Merge audit-passed PR 141 (server-native PM integration)")
    assert dup_pr is None, (dup_pr, dup_repo)
    assert dup_repo is None, (dup_pr, dup_repo)


def test_genuine_fragmented_duplicate_still_caught():
    """The real #58/#64/#65/#66 shape this stage was built for -- a
    different PR whose title references the same PR number with NO
    disclosure language -- must still be caught as a real duplicate."""
    rg = _load_rg("rg_stage6_2")

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "pr", "list"] and "--head" in cmd:
            return _FakeCompletedProcess(0, json.dumps([]))
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompletedProcess(0, json.dumps([
                {"number": 65, "title": "Resolve fresh conflict on PR #58"},
            ]))
        if cmd[:3] == ["gh", "pr", "view"]:
            # UMR-20260814-172611: matched PR must carry a real (non-docs)
            # file for the guard to still treat it as a genuine duplicate.
            return _FakeCompletedProcess(0, json.dumps({"files": [{"path": "resource_governor.py"}]}))
        raise AssertionError(f"unexpected gh call: {cmd}")

    with mock.patch.object(rg, "_run", side_effect=fake_run):
        dup_pr, dup_repo = rg.find_pr_for_task_identity(
            "DIRECTIVE-002-PR58-CONFLICT", hint_repo="claude-control",
            title="Fix PR #58 conflict")
    assert dup_pr == 65, (dup_pr, dup_repo)
    assert dup_repo == "claude-control", (dup_pr, dup_repo)


def test_disclosure_regex_matches_the_real_142_title_and_common_variants():
    rg = _load_rg("rg_stage6_3")
    assert rg._DISCLOSURE_CITATION_RE.search(
        "docs: real dedup finding for UMR-20260813-092654-326b "
        "(already covered by PR 141, not re-implemented)")
    assert rg._DISCLOSURE_CITATION_RE.search("supersedes PR 12")
    assert rg._DISCLOSURE_CITATION_RE.search("this work is superseded by PR 12")
    assert rg._DISCLOSURE_CITATION_RE.search("not a duplicate of PR 12")
    assert not rg._DISCLOSURE_CITATION_RE.search("Resolve fresh conflict on PR #58")
    assert not rg._DISCLOSURE_CITATION_RE.search("Fix PR #58 conflict")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL: {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR: {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
