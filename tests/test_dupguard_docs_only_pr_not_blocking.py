#!/usr/bin/env python3
"""Real regression test for the duplicate-PR guard's docs-only false
positive (UMR-20260814-172611).

Real incident this backstops: find_pr_for_task_identity()'s branch-lineage
(Stage 4/5) and title-reference (Stage 6) matches decide "an existing PR
already resolves this dispatch target" purely from a branch/title match,
without ever checking what the matched PR actually changed. A matched PR
whose entire real diff is limited to documentation/progress-notes files
(paths under progress/, or a bare PROGRESS.md-only change, with no other
file touched) does not deliver the real code work a dispatch is asking for.
This caused at least one real, repeated false rejection where a genuine
code PR could not be merged because the guard kept citing an unrelated
docs-only PR as already covering it.

Every real `gh` call is mocked at the `_run()` subprocess boundary, same
convention as tests/test_stage6_duplicate_pr_citation_guard.py -- never a
real network call.
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


def test_branch_match_on_docs_only_pr_does_not_block():
    """A branch-lineage (Stage 4/5) match whose matched PR's entire real
    diff is a single progress/*.md file must NOT be returned as a
    duplicate -- the dispatch must be allowed to proceed."""
    rg = _load_rg("rg_docs_only_1")

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "pr", "list"] and "--head" in cmd:
            return _FakeCompletedProcess(0, json.dumps([{"number": 900, "state": "OPEN"}]))
        if cmd[:3] == ["gh", "pr", "view"]:
            assert cmd[3] == "900", cmd
            return _FakeCompletedProcess(0, json.dumps({
                "files": [{"path": "progress/task-20260814-172611-fix-duplicate-pr-guard--a-matched-pr-wit.md"}],
            }))
        raise AssertionError(f"unexpected gh call: {cmd}")

    with mock.patch.object(rg, "_run", side_effect=fake_run):
        dup_pr, dup_repo = rg.find_pr_for_task_identity(
            "task-20260814-172611-fix-duplicate-pr-guard--a-matched-pr-wit",
            hint_repo="veridian-scripts", title="Fix duplicate-PR guard docs-only false positive")
    assert dup_pr is None, (dup_pr, dup_repo)
    assert dup_repo is None, (dup_pr, dup_repo)


def test_branch_match_on_real_code_pr_still_blocks():
    """The same branch-lineage match, but the matched PR's real diff
    includes a genuine non-docs code file -- must still be caught and
    block the dispatch as a real duplicate."""
    rg = _load_rg("rg_docs_only_2")

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "pr", "list"] and "--head" in cmd:
            return _FakeCompletedProcess(0, json.dumps([{"number": 901, "state": "OPEN"}]))
        if cmd[:3] == ["gh", "pr", "view"]:
            assert cmd[3] == "901", cmd
            return _FakeCompletedProcess(0, json.dumps({
                "files": [
                    {"path": "resource_governor.py"},
                    {"path": "progress/task-20260814-172611-fix-duplicate-pr-guard--a-matched-pr-wit.md"},
                ],
            }))
        raise AssertionError(f"unexpected gh call: {cmd}")

    with mock.patch.object(rg, "_run", side_effect=fake_run):
        dup_pr, dup_repo = rg.find_pr_for_task_identity(
            "task-20260814-172611-fix-duplicate-pr-guard--a-matched-pr-wit",
            hint_repo="veridian-scripts", title="Fix duplicate-PR guard docs-only false positive")
    assert dup_pr == 901, (dup_pr, dup_repo)
    assert dup_repo == "veridian-scripts", (dup_pr, dup_repo)


def test_title_reference_match_on_docs_only_pr_does_not_block():
    """Same waiver for the Stage 6 title-reference match path: a PROGRESS.md-
    only matched PR must not block a genuine code dispatch that merely
    mentions the same PR number in its title."""
    rg = _load_rg("rg_docs_only_3")

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "pr", "list"] and "--head" in cmd:
            return _FakeCompletedProcess(0, json.dumps([]))
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompletedProcess(0, json.dumps([
                {"number": 902, "title": "Fix PR #58 conflict"},
            ]))
        if cmd[:3] == ["gh", "pr", "view"]:
            assert cmd[3] == "902", cmd
            return _FakeCompletedProcess(0, json.dumps({"files": [{"path": "PROGRESS.md"}]}))
        raise AssertionError(f"unexpected gh call: {cmd}")

    with mock.patch.object(rg, "_run", side_effect=fake_run):
        dup_pr, dup_repo = rg.find_pr_for_task_identity(
            "DIRECTIVE-002-PR58-CONFLICT", hint_repo="claude-control",
            title="Fix PR #58 conflict")
    assert dup_pr is None, (dup_pr, dup_repo)
    assert dup_repo is None, (dup_pr, dup_repo)


def test_pr_changed_files_are_docs_only_unit():
    """Direct unit coverage of _pr_changed_files_are_docs_only()'s file-list
    classification, independent of the caller-level guard behavior above."""
    rg = _load_rg("rg_docs_only_4")

    def fake_run_docs(cmd, **kwargs):
        return _FakeCompletedProcess(0, json.dumps({
            "files": [{"path": "progress/some-task.md"}, {"path": "PROGRESS.md"}],
        }))

    def fake_run_code(cmd, **kwargs):
        return _FakeCompletedProcess(0, json.dumps({
            "files": [{"path": "resource_governor.py"}],
        }))

    def fake_run_empty(cmd, **kwargs):
        return _FakeCompletedProcess(0, json.dumps({"files": []}))

    def fake_run_error(cmd, **kwargs):
        return _FakeCompletedProcess(1, "")

    with mock.patch.object(rg, "_run", side_effect=fake_run_docs):
        assert rg._pr_changed_files_are_docs_only(1, "veridian-scripts") is True
    with mock.patch.object(rg, "_run", side_effect=fake_run_code):
        assert rg._pr_changed_files_are_docs_only(2, "veridian-scripts") is False
    with mock.patch.object(rg, "_run", side_effect=fake_run_empty):
        assert rg._pr_changed_files_are_docs_only(3, "veridian-scripts") is False
    with mock.patch.object(rg, "_run", side_effect=fake_run_error):
        assert rg._pr_changed_files_are_docs_only(4, "veridian-scripts") is False


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
