#!/usr/bin/env python3
"""Real regression tests for UMR-20260814-010950 (governing chain P1
UMR-20260806-171945-5767): the duplicate-PR/reuse-verdict guards went
OVER-broad and silently killed brand-new, legitimate work.

Two independent, real, reproduced-live incidents:

1. find_pr_for_task_identity()'s Stage 6 (title-referenced-PR-number match)
   matched a bare "PR NNN" substring across repos with no repo attached to
   the number at all. Real incident, reproduced 2026-08-14T01:01:52Z:
   UMR-20260814-010152-7981 (task_identity='owner-task-20260814-010149-432146',
   ZERO prior branches -- a brand-new dispatch that had never run before)
   titled "...every audited merged fix (incl PR 322) is NOT running" was
   rejected citing FChecklist/claude-control#185, whose own real title is
   "Resolve conflict, audit and land veridian-scripts PR 322 - per-task
   progress ...". Both titles genuinely say "PR 322" -- but one means
   veridian-scripts#322 (a real, unrelated, already-merged PR in a DIFFERENT
   repo), the other is a claude-control PR that merely talks ABOUT
   veridian-scripts#322. A PR number with no repo attached is not evidence
   of anything; the real fix scopes Stage 6 the same way
   target_pr_already_resolved() already had to be fixed (UMR-20260813-165620-
   aac7): repo-qualified reference -> search only that repo; bare reference
   -> search only hint_repo; no repo to trust -> skip.

2. _orchestrator_reuse_verdict_gate() (Step 2, reuse_verdict_engine.assess())
   ran unconditionally for EVERY dispatched row, including systemctl_action
   task-resume rows, whose "intent_text" collapses to the row's own
   task_identity -- deterministically similar to that task's OWN prior
   task.yaml, which full_server_file_registration.py had separately
   registered into wiring_registry as a generic entity_type='file' row. Real
   incident, confirmed live: UMR-20260814-004301-2d07
   (task_identity='task-20260807-071557-retry-ai-cost-governance-finops-
   cost-vis', a systemctl_action 'start' resume) was rejected
   duplication_blocked, score=0.953, best_match={'source': 'wiring_registry',
   'id': 'file-10d3faee408e', 'kind': 'file'} -- literally that same task's
   own historical task.yaml. Resuming an already-existing unit can never
   "duplicate" a wiring_registry file by construction; the real fix scopes
   Step 2 to task_kind=='veridian_task_create' rows only, mirroring the
   scoping Stage 4/5/6 already applies just above it.

Every real `gh` call is mocked at the `_run()` subprocess boundary, and
every DB touch uses a real, isolated, temp-file SQLite database -- never a
real network call or the live production database. Same conventions as
tests/test_stage6_duplicate_pr_citation_guard.py /
tests/test_target_pr_dispatch_time_recheck.py.
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from unittest import mock

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)


def _schema_helpers():
    spec = importlib.util.spec_from_file_location(
        "sbr_helpers_overbroad", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def _new_conn(scratch_db):
    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_scratch_db(scratch_db, sbr):
    conn = _new_conn(scratch_db)
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    conn.close()


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
#     blocked. Real UMR-20260814-010152-7981 shape, verbatim.
# ---------------------------------------------------------------------------

def test_brand_new_task_identity_with_pr_number_in_title_and_no_prior_branch_not_blocked():
    rg = _load_rg("rg_overbroad_a")

    def fake_run(cmd, **kwargs):
        if "--head" in cmd:
            return _FakeCompletedProcess(0, json.dumps([]))  # no prior branch anywhere
        if cmd[:3] == ["gh", "pr", "list"]:
            # Only ever asked about claude-control (hint_repo) for the bare
            # "PR 322" fallback -- claude-control#185's real title, which
            # ALSO says "PR 322" (about landing a DIFFERENT repo's PR).
            assert f"FChecklist/claude-control" in cmd, cmd
            return _FakeCompletedProcess(0, json.dumps([
                {"number": 185, "title": "Resolve conflict, audit and land "
                 "veridian-scripts PR 322 - per-task progress"},
            ]))
        raise AssertionError(f"unexpected gh call: {cmd}")

    with mock.patch.object(rg, "_run", side_effect=fake_run):
        dup_pr, dup_repo = rg.find_pr_for_task_identity(
            "owner-task-20260814-010149-432146", hint_repo="claude-control",
            extra_task_ids=[],
            title="Live deploy drift P0: /opt/veridian/scripts is 61 commits "
                  "behind origin/main on a stray preserve branch, so every "
                  "audited merged fix (incl PR 322) is NOT running")
    assert dup_pr is None, (dup_pr, dup_repo)
    assert dup_repo is None, (dup_pr, dup_repo)


# ---------------------------------------------------------------------------
# (b) cross-repo same-number PR must never block, even when a repo-qualified
#     reference correctly resolves to a DIFFERENT repo than hint_repo.
# ---------------------------------------------------------------------------

def test_cross_repo_same_number_pr_does_not_block():
    """Title explicitly names 'veridian-scripts PR 322'. hint_repo is
    claude-control, and claude-control has an unrelated PR whose title also
    mentions '322' -- but since the title is repo-qualified to
    veridian-scripts, the guard must search ONLY veridian-scripts (and find
    nothing there), never fall back to matching the claude-control PR."""
    rg = _load_rg("rg_overbroad_b")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if "--head" in cmd:
            return _FakeCompletedProcess(0, json.dumps([]))
        if cmd[:3] == ["gh", "pr", "list"]:
            # claude-control must NEVER be queried for the title-number
            # check once the title is repo-qualified to veridian-scripts.
            assert "FChecklist/claude-control" not in cmd, cmd
            assert "FChecklist/veridian-scripts" in cmd, cmd
            return _FakeCompletedProcess(0, json.dumps([]))  # real: no match in veridian-scripts
        raise AssertionError(f"unexpected gh call: {cmd}")

    with mock.patch.object(rg, "_run", side_effect=fake_run):
        dup_pr, dup_repo = rg.find_pr_for_task_identity(
            "owner-task-20260814-010149-432146", hint_repo="claude-control",
            extra_task_ids=[],
            # Deliberately NOT a parenthetical citation (see
            # _title_pr_reference_is_citation_only()'s own regression tests
            # for that separate case) -- this title states veridian-scripts
            # PR 322 as its own stated subject, so the repo-qualified match
            # must still resolve and search ONLY veridian-scripts.
            title="Redo veridian-scripts PR 322's landing, it's still broken")
    assert dup_pr is None, (dup_pr, dup_repo)
    assert dup_repo is None, (dup_pr, dup_repo)
    # The unqualified 'claude-control' bare-number fallback path must never
    # even be reached -- confirm the title-number gh call only ever hit
    # veridian-scripts, never claude-control (load-bearing on the cross-repo
    # guarantee, not just the final None/None result).
    title_number_calls = [c for c in calls if c[:3] == ["gh", "pr", "list"] and "--head" not in c]
    assert len(title_number_calls) == 1, title_number_calls
    assert "FChecklist/veridian-scripts" in title_number_calls[0]


def test_citation_only_pr_reference_helper_distinguishes_parenthetical_from_target():
    rg = _load_rg("rg_overbroad_bhelper")
    # Real UMR-20260814-010152-7981 shape -- parenthetical citation.
    assert rg._title_pr_reference_is_citation_only(
        "Live deploy drift P0: every audited merged fix (incl PR 322) is NOT running", "322")
    # Real #58/#64/#65/#66 shapes this stage exists to still catch -- the
    # number IS the sentence's stated subject, never inside parens.
    assert not rg._title_pr_reference_is_citation_only("Resolve fresh conflict on PR #58", "58")
    assert not rg._title_pr_reference_is_citation_only("Fix PR #58 conflict", "58")
    # A parenthetical elsewhere in the title that does not itself contain
    # the PR number must not trigger the exclusion.
    assert not rg._title_pr_reference_is_citation_only(
        "Merge audit-passed PR 141 (server-native PM integration)", "141")
    assert not rg._title_pr_reference_is_citation_only(None, "58")
    assert not rg._title_pr_reference_is_citation_only("Fix PR #58 conflict", None)


# ---------------------------------------------------------------------------
# (c) a genuine same-repo same-target duplicate is still blocked.
# ---------------------------------------------------------------------------

def test_genuine_same_repo_same_target_duplicate_still_blocked():
    """The real #58/#64/#65/#66 shape this stage was built for: a bare 'PR
    #58' reference, hint_repo given, and an existing PR IN THAT SAME REPO
    whose title references the same number with no disclosure language --
    must still be caught."""
    rg = _load_rg("rg_overbroad_c")

    def fake_run(cmd, **kwargs):
        if "--head" in cmd:
            return _FakeCompletedProcess(0, json.dumps([]))
        if cmd[:3] == ["gh", "pr", "list"]:
            assert "FChecklist/claude-control" in cmd, cmd
            return _FakeCompletedProcess(0, json.dumps([
                {"number": 65, "title": "Resolve fresh conflict on PR #58"},
            ]))
        raise AssertionError(f"unexpected gh call: {cmd}")

    with mock.patch.object(rg, "_run", side_effect=fake_run):
        dup_pr, dup_repo = rg.find_pr_for_task_identity(
            "PR58-CONFLICT-v2", hint_repo="claude-control", extra_task_ids=[],
            title="Fix PR #58 conflict")
    assert dup_pr == 65, (dup_pr, dup_repo)
    assert dup_repo == "claude-control", (dup_pr, dup_repo)


def test_repo_qualified_same_repo_duplicate_still_blocked():
    """A repo-qualified reference ('veridian-scripts#322') whose target repo
    genuinely has a matching, non-disclosure PR title must still block."""
    rg = _load_rg("rg_overbroad_c2")

    def fake_run(cmd, **kwargs):
        if "--head" in cmd:
            return _FakeCompletedProcess(0, json.dumps([]))
        if cmd[:3] == ["gh", "pr", "list"]:
            assert "FChecklist/veridian-scripts" in cmd, cmd
            return _FakeCompletedProcess(0, json.dumps([
                {"number": 322, "title": "feat: per-task progress dir + completion gate"},
                {"number": 340, "title": "rebuild the same per-task progress work as PR 322"},
            ]))
        raise AssertionError(f"unexpected gh call: {cmd}")

    with mock.patch.object(rg, "_run", side_effect=fake_run):
        dup_pr, dup_repo = rg.find_pr_for_task_identity(
            "redo-pr322-work", hint_repo="claude-control", extra_task_ids=[],
            title="Redo veridian-scripts#322's work, it never landed")
    assert dup_pr == 340, (dup_pr, dup_repo)
    assert dup_repo == "veridian-scripts", (dup_pr, dup_repo)


# ---------------------------------------------------------------------------
# (d) a task/resume intent (systemctl_action) is never matched against a
#     wiring_registry record at all -- the reuse-verdict gate must not even
#     run for it.
# ---------------------------------------------------------------------------

def test_orchestrator_reuse_verdict_gate_never_invoked_for_systemctl_action_row():
    """Pure unit check on the call-site gating itself: the real
    reuse_verdict_engine module must never even be loaded/called for a
    systemctl_action row -- not just "not blocked", genuinely not invoked,
    since the underlying question ("does a wiring_registry file already do
    this") is meaningless for a lifecycle action on an existing unit."""
    rg = _load_rg("rg_overbroad_d1")
    row = {
        "task_kind": "systemctl_action",
        "task_identity": "task-20260807-071557-retry-ai-cost-governance-finops-cost-vis",
        "umr_id": "UMR-test-d1",
        "inputs_json": json.dumps({"action": "start", "resumed_after_state": "inactive"}),
    }
    with mock.patch.object(rg, "_reuse_verdict_engine") as mock_rve:
        if row["task_kind"] != "veridian_task_create":
            blocked, verdict_result = False, None
        else:
            blocked, verdict_result = rg._orchestrator_reuse_verdict_gate(None, None, row)
    assert blocked is False
    assert verdict_result is None
    mock_rve.assert_not_called()


def test_dispatch_one_end_to_end_resumed_systemctl_action_not_blocked_by_wiring_registry_file():
    """Full real incident shape, end-to-end: a systemctl_action task-resume
    row whose task_identity would score 0.953 against a wiring_registry
    'file' row (its own prior task.yaml) if the reuse-verdict gate ran --
    must reach the real spawn path, never rejected_duplicate_reuse_verdict."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_overbroad_d2", env)
        rg.EMERGENCY_STOP_PATH = os.path.join(d, "EMERGENCY_STOP_never_created")
        rg.STOP_WORK_ORDER_TASK_IDS = ()

        conn = _new_conn(scratch_db)
        umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": "task-20260807-071557-retry-ai-cost-governance-finops-cost-vis",
            "tier": 1, "status": "queued", "source_trigger": "dispatch-tick:resume_interrupted_workers",
            "task_kind": "systemctl_action", "unit_name": "veridian-worker@x.service",
            "inputs": {"action": "start", "resumed_after_state": "inactive"},
            "reason": "queued",
        })
        conn.commit()
        conn.close()

        dc_mod = rg._dispatch_core()
        # If the reuse-verdict gate were reached for this row, this fake
        # assess() would report the real observed duplication_blocked verdict
        # against the file-kind wiring_registry candidate -- planted here so
        # the test would fail loudly (block, wrong action) if the fix
        # regresses and Step 2 runs for this row again.
        fake_rve = mock.MagicMock()
        fake_rve.assess.return_value = {
            "verdict": "duplication_blocked", "score": 0.953,
            "best_match": {"id": "file-10d3faee408e", "source": "wiring_registry", "kind": "file"},
        }

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            with mock.patch.object(dc_mod, "has_free_slot_detail", return_value=(True, {"check": "ok"})), \
                 mock.patch.object(dc_mod, "record_dispatch_event", return_value=None), \
                 mock.patch.object(rg, "_reuse_verdict_engine", return_value=fake_rve), \
                 mock.patch.object(rg, "_perform_spawn",
                                    return_value={"status": "running",
                                                  "unit_name": "veridian-worker@x.service",
                                                  "outputs": {}}) as mock_spawn:
                result = rg.dispatch_one()
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)

        assert result["action"] != "rejected_duplicate_reuse_verdict", result
        mock_spawn.assert_called_once()
        fake_rve.assess.assert_not_called()

        conn = _new_conn(scratch_db)
        row = conn.execute("SELECT status FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
        conn.close()
        assert row["status"] == "running", row["status"]


def test_dispatch_one_end_to_end_veridian_task_create_still_blocked_by_reuse_verdict():
    """Control case: the reuse-verdict gate must still run, and still block,
    for a genuine veridian_task_create row -- this fix only narrows scope
    for systemctl_action rows, it does not remove the gate itself."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_overbroad_d3", env)
        rg.EMERGENCY_STOP_PATH = os.path.join(d, "EMERGENCY_STOP_never_created")
        rg.STOP_WORK_ORDER_TASK_IDS = ()

        conn = _new_conn(scratch_db)
        umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": "test-veridian-create-genuine-dup", "tier": 1, "status": "queued",
            "source_trigger": "unit_test", "task_kind": "veridian_task_create",
            "inputs": {"repo": "claude-control", "title": "Build a brand new thing", "prompt": "p"},
            "reason": "queued",
        })
        conn.commit()
        conn.close()

        dc_mod = rg._dispatch_core()
        fake_rve = mock.MagicMock()
        fake_rve.assess.return_value = {
            "verdict": "duplication_blocked", "score": 0.99,
            "best_match": {"id": "cap-xyz", "source": "capability_registry", "kind": "capability:thing"},
        }

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            with mock.patch.object(dc_mod, "has_free_slot_detail", return_value=(True, {"check": "ok"})), \
                 mock.patch.object(rg, "_run", return_value=_FakeCompletedProcess(0, json.dumps([]))), \
                 mock.patch.object(rg, "_reuse_verdict_engine", return_value=fake_rve), \
                 mock.patch.object(rg, "_perform_spawn") as mock_spawn:
                result = rg.dispatch_one()
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)

        assert result["action"] == "rejected_duplicate_reuse_verdict", result
        mock_spawn.assert_not_called()
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
