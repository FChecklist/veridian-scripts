#!/usr/bin/env python3
"""Real regression tests for the over-broad duplicate-PR guard fix
(UMR-20260814-015201, governing chain P1 UMR-20260806-171945-5767).

Real incident this backstops (reproduced live 2026-08-14T01:01:52Z):
UMR-20260814-010152-7981, task_identity=owner-task-20260814-010149-432146 (a
BRAND-NEW identity minted seconds earlier in the same dispatch call -- zero
prior branches), was written straight to status=rejected_duplicate,
ts_dispatched=None, unit_name=None -- it never spawned. The guard's own
reason string: 'existing PR FChecklist/claude-control#185 already open/merged
... (checked worker/owner-task-20260814-010149-432146, prior real branch(es)
[], and any existing PR title referencing the same PR number as this task's
own title)'. Two independent proofs this was a false positive: (a) a
brand-new task_identity cannot have a pre-existing PR, and the guard itself
found "prior real branch(es) []"; (b) claude-control#185 is real, unrelated
work -- the only thing shared with the new task's title is a PR NUMBER that
also appears in #185's own title, in a DIFFERENT repo than this task's
actual target.

Separately, the resume path (dispatch-tick.py's
resume_interrupted_workers_tick(), task_kind='systemctl_action') was writing
10 rejected_duplicate rows every dispatch-tick cycle (e.g.
UMR-20260814-004301-2d07, UMR-20260814-003307-d246, UMR-20260814-002256-bc46)
via reuse_verdict_engine.assess(), verdict=duplication_blocked, score=0.953,
matched against wiring_registry id=file-10d3faee408e, kind='file' -- a
task/resume intent (no descriptive title at all -- these rows carry no
"title" input) cross-type-matched against an unrelated tracked source file.

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


# ---------------------------------------------------------------------------
# (a) brand-new task_identity, PR number in title, no prior branch -> NOT
#     blocked (the real UMR-20260814-010152-7981 shape, with no coincidental
#     cross-repo collision in play at all -- baseline correctness).
# ---------------------------------------------------------------------------

def test_brand_new_identity_with_pr_number_in_title_not_blocked():
    rg = _load_rg("rg_scope_a")

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "pr", "list"] and "--head" in cmd:
            return _FakeCompletedProcess(0, json.dumps([]))  # no branch ever opened
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompletedProcess(0, json.dumps([]))  # no PRs reference this number either
        raise AssertionError(f"unexpected gh call: {cmd}")

    with mock.patch.object(rg, "_run", side_effect=fake_run):
        dup_pr, dup_repo = rg.find_pr_for_task_identity(
            "owner-task-20260814-010149-432146", hint_repo="veridian-scripts",
            extra_task_ids=[],
            title="P0 remediation: reconcile live /opt/veridian/scripts drift (PR 185 scope)")
    assert dup_pr is None, (dup_pr, dup_repo)
    assert dup_repo is None, (dup_pr, dup_repo)


# ---------------------------------------------------------------------------
# (b) cross-repo same-number PR must NOT block -- the real #185 incident.
# ---------------------------------------------------------------------------

def test_cross_repo_same_number_pr_does_not_block():
    rg = _load_rg("rg_scope_b")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "list"] and "--head" in cmd:
            return _FakeCompletedProcess(0, json.dumps([]))  # no branch match in either repo
        if cmd[:3] == ["gh", "pr", "list"]:
            repo_idx = cmd.index("--repo") + 1
            repo = cmd[repo_idx]
            if repo.endswith("/claude-control"):
                # Real, unrelated PR #185 in the OTHER repo that happens to
                # cite the same bare number -- must never be treated as this
                # task's own duplicate.
                return _FakeCompletedProcess(0, json.dumps([
                    {"number": 185, "title": "docs: PR 185 audit notes (unrelated claude-control work)"},
                ]))
            return _FakeCompletedProcess(0, json.dumps([]))  # veridian-scripts: nothing
        raise AssertionError(f"unexpected gh call: {cmd}")

    with mock.patch.object(rg, "_run", side_effect=fake_run):
        dup_pr, dup_repo = rg.find_pr_for_task_identity(
            "owner-task-20260814-010149-432146", hint_repo="veridian-scripts",
            extra_task_ids=[],
            title="P0 remediation: reconcile live /opt/veridian/scripts drift (PR 185 scope)")
    assert dup_pr is None, (dup_pr, dup_repo)
    assert dup_repo is None, (dup_pr, dup_repo)
    # Regression guard on the fix itself: Stage 6 must never even issue the
    # broad `gh pr list` call against a repo other than this task's own
    # hint_repo for a bare (non-repo-qualified) "PR NNN" title reference.
    broad_list_repos = {
        cmd[cmd.index("--repo") + 1] for cmd in calls
        if cmd[:3] == ["gh", "pr", "list"] and "--head" not in cmd
    }
    assert broad_list_repos == {f"{rg.GH_ORG}/veridian-scripts"}, broad_list_repos


# ---------------------------------------------------------------------------
# (c) genuine same-repo same-target duplicate IS still blocked.
# ---------------------------------------------------------------------------

def test_genuine_same_repo_duplicate_still_blocked():
    rg = _load_rg("rg_scope_c")

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "pr", "list"] and "--head" in cmd:
            return _FakeCompletedProcess(0, json.dumps([]))
        if cmd[:3] == ["gh", "pr", "list"]:
            repo_idx = cmd.index("--repo") + 1
            repo = cmd[repo_idx]
            if repo.endswith("/veridian-scripts"):
                return _FakeCompletedProcess(0, json.dumps([
                    {"number": 65, "title": "Resolve fresh conflict on PR #58"},
                ]))
            return _FakeCompletedProcess(0, json.dumps([]))
        raise AssertionError(f"unexpected gh call: {cmd}")

    with mock.patch.object(rg, "_run", side_effect=fake_run):
        dup_pr, dup_repo = rg.find_pr_for_task_identity(
            "DIRECTIVE-002-PR58-CONFLICT", hint_repo="veridian-scripts",
            extra_task_ids=[], title="Fix PR #58 conflict")
    assert dup_pr == 65, (dup_pr, dup_repo)
    assert dup_repo == "veridian-scripts", (dup_pr, dup_repo)


def test_explicit_repo_qualified_reference_still_blocked_same_repo():
    """A title that explicitly names the repo ('veridian-scripts#185') is
    unambiguous -- must still be checked (and can still block) against
    exactly that named repo, never guessed."""
    rg = _load_rg("rg_scope_c2")

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "pr", "list"] and "--head" in cmd:
            return _FakeCompletedProcess(0, json.dumps([]))
        if cmd[:3] == ["gh", "pr", "list"]:
            repo_idx = cmd.index("--repo") + 1
            repo = cmd[repo_idx]
            if repo.endswith("/veridian-scripts"):
                return _FakeCompletedProcess(0, json.dumps([
                    {"number": 185, "title": "fix: real duplicate of PR 185 (veridian-scripts)"},
                ]))
            raise AssertionError(f"must not check unrelated repo for an explicitly-qualified reference: {cmd}")
        raise AssertionError(f"unexpected gh call: {cmd}")

    with mock.patch.object(rg, "_run", side_effect=fake_run):
        dup_pr, dup_repo = rg.find_pr_for_task_identity(
            "owner-task-20260814-020000-abcdef", hint_repo="claude-control",
            extra_task_ids=[], title="Re-submit veridian-scripts#185 (rebased)")
    assert dup_pr == 185, (dup_pr, dup_repo)
    assert dup_repo == "veridian-scripts", (dup_pr, dup_repo)


# ---------------------------------------------------------------------------
# (d) a task/resume intent must never be scored against wiring_registry at
#     all -- the reuse_verdict_engine gate is scoped off for any non
#     veridian_task_create row before it ever builds a candidate set.
# ---------------------------------------------------------------------------

def test_resume_intent_never_calls_reuse_verdict_engine():
    rg = _load_rg("rg_scope_d")

    def _boom():
        raise AssertionError(
            "reuse_verdict_engine must never be loaded/called for a "
            "task_kind='systemctl_action' (resume/restart) row"
        )

    row = {
        "task_kind": "systemctl_action",
        "task_identity": "task-20260813-235841-reconciler-blind-spot--4-rows-stuck-runn",
        "inputs_json": json.dumps({"action": "start", "resumed_after_state": "failed"}),
    }
    with mock.patch.object(rg, "_reuse_verdict_engine", side_effect=_boom):
        blocked, result = rg._orchestrator_reuse_verdict_gate(sbr=None, conn=None, row=row)
    assert blocked is False, (blocked, result)
    assert result is None, (blocked, result)


def test_new_work_intent_still_goes_through_reuse_verdict_engine():
    """Positive control: a genuine veridian_task_create (new-work) row is
    unaffected by the task_kind scoping fix -- it still reaches assess()."""
    rg = _load_rg("rg_scope_d2")

    fake_rve = mock.Mock()
    fake_rve.assess.return_value = {
        "verdict": "duplication_blocked",
        "score": 0.99,
        "best_match": {"id": "capability-real-dup", "source": "capability_registry", "kind": "capability:real_dup"},
    }
    row = {
        "task_kind": "veridian_task_create",
        "task_identity": "owner-task-20260814-030000-999999",
        "inputs_json": json.dumps({"title": "Build a document duplicate detector", "repo": "veridian-scripts"}),
    }
    with mock.patch.object(rg, "_reuse_verdict_engine", return_value=fake_rve):
        blocked, result = rg._orchestrator_reuse_verdict_gate(sbr=mock.Mock(), conn=mock.Mock(), row=row)
    assert blocked is True, (blocked, result)
    assert result["best_match"]["id"] == "capability-real-dup"
    fake_rve.assess.assert_called_once()


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
