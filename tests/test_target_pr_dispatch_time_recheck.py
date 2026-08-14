#!/usr/bin/env python3
"""Real tests for the dispatch-time target-PR-state re-check
(UMR-20260813-135626, addendum to P1 UMR-20260806-171945-5767, sibling of
UMR-20260813-120054-4e66).

Real incident this backstops: the governing SPEC's own evidence gathering
(PM desktop sentinel, 2026-08-13 ~11:55 UTC) found 3 real queued rows
(ts_dispatched IS NULL) whose own title named a real target PR that GitHub
had already resolved before/shortly after the row was even queued --

  - UMR-20260813-111356-3677 ("...existing PR 249..."): veridian-scripts
    PR #249 MERGED 2026-08-13T10:39:54Z, 34 real minutes BEFORE the row
    queued at 11:13:56.
  - UMR-20260813-101609-9a69 ("Resolve PR 135 conflict..."): claude-control
    PR #135 MERGED 10:40:46Z -- no conflict left to resolve.
  - UMR-20260813-111352-6973 ("...audit-approved PR 136"): PR #136 was real,
    current DIRTY/mergeable=false at dispatch time (not the queue-time
    UNKNOWN state) -- the RIGHT answer there is dispatch a fresh
    rebase+re-audit, NOT a self-reject, which is exactly why
    target_pr_already_resolved() only blocks on a real MERGED state, never
    on OPEN/DIRTY.

UMR-20260814-132703-a1f9 real fix, added later: the guard used to also
block on a bare CLOSED (unmerged) state, which caused a real false
self-rejection -- UMR-20260814-125933-3377 ("Merge real veridian-scripts
PR#298") was submitted believing veridian-scripts PR#298 (real, CLOSED,
mergedAt=null) was an abandoned gap, and this guard rejected that row as a
duplicate because it saw the same CLOSED state its own title already named
as the problem. Bare CLOSED-without-merge is not, by itself, evidence the
work is resolved -- see test_closed_unmerged_target_pr_with_no_known_successor_does_not_block()
below, which replaces the old (incorrect) test_closed_unmerged_target_pr_blocks().
Real follow-up finding (still UMR-20260814-132703-a1f9): PR#298 itself
turned out to have a real merged successor (#299, its own close comment:
"Superseded by #299, which now carries this PR's full content forward as
a strict superset ... verified byte-identical via diff") -- so the guard
must still block PR#298 specifically, just for that real reason instead of
a bare-CLOSED coincidence. See _closed_pr_superseded_by_merged_pr() in
resource_governor.py and test_closed_pr_with_real_merged_successor_still_blocks()
below.

Every real `gh` call is mocked at the `_run()` subprocess boundary (same
convention as this module's other real gh-backed guards) -- never a real
network call. Every DB touch uses a real, isolated, temp-file SQLite
database (never the live production database), same convention as
tests/test_rule2_dispatch_outcomes.py / tests/test_run_tick_continues_past_row_resolved_skip.py.
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
        "sbr_helpers_targetpr", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
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


def _load_rg(name, env):
    old_env = {}
    for k, v in env.items():
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
# 1. target_pr_already_resolved() -- pure unit tests against a mocked _run()
# ---------------------------------------------------------------------------

def test_no_pr_number_in_title_never_blocks():
    rg = _load_rg("rg_targetpr_1", {"VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR})
    with mock.patch.object(rg, "_run") as mock_run:
        blocked, evidence = rg.target_pr_already_resolved("A title with no PR reference at all")
    assert blocked is False
    assert evidence is None
    mock_run.assert_not_called()


def test_merged_target_pr_blocks_with_real_evidence():
    """The real UMR-20260813-111356-3677 shape: title names PR #249, GitHub
    reports it MERGED -- must block and carry real merge evidence."""
    rg = _load_rg("rg_targetpr_2", {"VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR})
    fake_pr = {"number": 249, "state": "MERGED",
               "mergedAt": "2026-08-13T10:39:54Z", "closedAt": None,
               "url": "https://github.com/FChecklist/veridian-scripts/pull/249"}
    with mock.patch.object(rg, "_run", return_value=_FakeCompletedProcess(0, json.dumps(fake_pr))) as mock_run:
        blocked, evidence = rg.target_pr_already_resolved(
            "Post real audit verdict directly on existing PR 249 (fix broken create-PR flow)",
            hint_repo="veridian-scripts")
    assert blocked is True
    assert evidence["repo"] == "veridian-scripts"
    assert evidence["number"] == 249
    assert evidence["state"] == "MERGED"
    assert evidence["merged_at"] == "2026-08-13T10:39:54Z"
    # hint_repo must be checked first (and only, since it resolves) -- one gh call
    mock_run.assert_called_once()
    called_args = mock_run.call_args[0][0]
    assert "249" in called_args and "FChecklist/veridian-scripts" in called_args


def test_closed_unmerged_target_pr_with_no_known_successor_does_not_block():
    """UMR-20260814-132703-a1f9 real fix: a real, confirmed CLOSED-without-
    merge PR, with no "superseded by a real merged PR" evidence found on
    it (see test_closed_pr_with_real_merged_successor_still_blocks() below
    for that case), must NOT block -- a bare abandoned/rejected-without-
    successor PR is a real open gap, not a resolved duplicate. (Note: the
    real veridian-scripts PR#298 that motivated this fix turned out to
    have exactly such a merged successor -- #299 -- so it still correctly
    blocks; this test uses a different, hypothetical PR number for the
    genuinely-no-successor case the old code also got wrong.)"""
    rg = _load_rg("rg_targetpr_3", {"VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR})
    fake_pr = {"number": 400, "state": "CLOSED", "mergedAt": None,
               "closedAt": "2026-08-13T14:09:30Z",
               "url": "https://github.com/FChecklist/veridian-scripts/pull/400"}
    with mock.patch.object(rg, "_run", return_value=_FakeCompletedProcess(0, json.dumps(fake_pr))):
        blocked, evidence = rg.target_pr_already_resolved(
            "Merge real veridian-scripts PR#400 (outstanding gap, closed unmerged, no successor)",
            hint_repo="veridian-scripts")
    assert blocked is False
    assert evidence is None


def test_closed_pr_with_real_merged_successor_still_blocks():
    """PR#298's real, full incident (not the simplified shape above):
    CLOSED, mergedAt=null, close comment 'Superseded by #299, which now
    carries this PR's full content forward as a strict superset ...
    verified byte-identical via diff' -- and #299 is real, MERGED. This
    must still block, for the real reason (a real merged successor), not
    the old bare-CLOSED coincidence."""
    rg = _load_rg("rg_targetpr_superseded_1", {"VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR})
    closed_pr = {"number": 298, "state": "CLOSED", "mergedAt": None,
                 "closedAt": "2026-08-13T14:09:30Z",
                 "url": "https://github.com/FChecklist/veridian-scripts/pull/298"}
    comments_payload = {
        "body": "", "comments": [
            {"body": "Superseded by #299, which now carries this PR's full content "
                     "forward as a strict superset (script + tests + these systemd "
                     "units, verified byte-identical via diff)."}
        ],
    }
    successor_pr = {"number": 299, "state": "MERGED",
                     "mergedAt": "2026-08-13T18:49:15Z", "closedAt": "2026-08-13T18:49:15Z",
                     "url": "https://github.com/FChecklist/veridian-scripts/pull/299"}

    def fake_run(cmd, **kwargs):
        if "--json" in cmd and "body,comments" in cmd:
            return _FakeCompletedProcess(0, json.dumps(comments_payload))
        if "299" in cmd:
            return _FakeCompletedProcess(0, json.dumps(successor_pr))
        return _FakeCompletedProcess(0, json.dumps(closed_pr))

    with mock.patch.object(rg, "_run", side_effect=fake_run):
        blocked, evidence = rg.target_pr_already_resolved(
            "Merge real veridian-scripts PR#298 (outstanding gap, closed unmerged)",
            hint_repo="veridian-scripts")
    assert blocked is True, (blocked, evidence)
    assert evidence["state"] == "CLOSED"
    assert evidence["superseded_by"]["number"] == 299
    assert evidence["superseded_by"]["merged_at"] == "2026-08-13T18:49:15Z"


def test_closed_pr_superseded_claim_citing_unmerged_pr_does_not_block():
    """A 'superseded by #NNN' claim is only real evidence if that PR is
    itself actually MERGED -- citing a PR that never merged (or doesn't
    exist) must not block."""
    rg = _load_rg("rg_targetpr_superseded_2", {"VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR})
    closed_pr = {"number": 500, "state": "CLOSED", "mergedAt": None,
                 "closedAt": "2026-08-13T14:09:30Z",
                 "url": "https://github.com/FChecklist/veridian-scripts/pull/500"}
    comments_payload = {"body": "Superseded by #501, still under review.", "comments": []}
    successor_pr = {"number": 501, "state": "OPEN", "mergedAt": None, "closedAt": None,
                     "url": "https://github.com/FChecklist/veridian-scripts/pull/501"}

    def fake_run(cmd, **kwargs):
        if "--json" in cmd and "body,comments" in cmd:
            return _FakeCompletedProcess(0, json.dumps(comments_payload))
        if "501" in cmd:
            return _FakeCompletedProcess(0, json.dumps(successor_pr))
        return _FakeCompletedProcess(0, json.dumps(closed_pr))

    with mock.patch.object(rg, "_run", side_effect=fake_run):
        blocked, evidence = rg.target_pr_already_resolved(
            "Merge real veridian-scripts PR#500", hint_repo="veridian-scripts")
    assert blocked is False, (blocked, evidence)
    assert evidence is None


def test_closed_pr_with_unrelated_mid_sentence_superseded_mention_does_not_block():
    """PR#376 AUDIT:FAIL lower-severity note: _SUPERSEDED_BY_RE must not
    false-match a comment that merely mentions another PR's supersession
    mid-sentence, unrelated to whether *this* PR was itself superseded --
    e.g. a reviewer comment discussing a different PR's history. Real close
    comments state supersession as their own clause (see PR#298's real
    comment, test_closed_pr_with_real_merged_successor_still_blocks() above);
    a mid-sentence aside about an unrelated PR must not be read the same way."""
    rg = _load_rg("rg_targetpr_superseded_3", {"VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR})
    closed_pr = {"number": 600, "state": "CLOSED", "mergedAt": None,
                 "closedAt": "2026-08-13T14:09:30Z",
                 "url": "https://github.com/FChecklist/veridian-scripts/pull/600"}
    comments_payload = {
        "body": "Closing this as a duplicate effort, unrelated to PR #298, which was "
                "superseded by #299 in a different discussion thread entirely.",
        "comments": [],
    }

    def fake_run(cmd, **kwargs):
        if "--json" in cmd and "body,comments" in cmd:
            return _FakeCompletedProcess(0, json.dumps(comments_payload))
        return _FakeCompletedProcess(0, json.dumps(closed_pr))

    with mock.patch.object(rg, "_run", side_effect=fake_run):
        blocked, evidence = rg.target_pr_already_resolved(
            "Merge real veridian-scripts PR#600", hint_repo="veridian-scripts")
    assert blocked is False, (blocked, evidence)
    assert evidence is None


def test_open_dirty_target_pr_never_blocks():
    """The real UMR-20260813-111352-6973 shape: PR #136 is real, current
    OPEN/DIRTY -- must NOT block (the right answer there is a fresh
    rebase+re-audit dispatch, not a self-reject)."""
    rg = _load_rg("rg_targetpr_4", {"VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR})
    fake_pr = {"number": 136, "state": "OPEN", "mergedAt": None, "closedAt": None,
               "url": "https://github.com/FChecklist/claude-control/pull/136"}
    with mock.patch.object(rg, "_run", return_value=_FakeCompletedProcess(0, json.dumps(fake_pr))):
        blocked, evidence = rg.target_pr_already_resolved(
            "Execute the real merge for audit-approved PR 136", hint_repo="claude-control")
    assert blocked is False
    assert evidence is None


def test_gh_timeout_fails_open():
    """A `gh` timeout must fail open -- never block, never raise. This guard
    tries every real candidate repo (hint_repo first) before giving up, same
    fail-open-per-repo convention as find_pr_for_task_identity() above, so a
    timeout on the hinted repo alone must not be treated as fatal."""
    import subprocess
    rg = _load_rg("rg_targetpr_5", {"VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR})
    with mock.patch.object(rg, "_run", side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=8)), \
         mock.patch.object(rg, "_append_attention") as mock_attn:
        blocked, evidence = rg.target_pr_already_resolved(
            "Execute the real merge for audit-approved PR 136", hint_repo="claude-control")
    assert blocked is False
    assert evidence is None
    assert mock_attn.called, "must log a real WARNING to ATTENTION.md on timeout"
    assert any("timed out" in c.args[0] and "#136" in c.args[0] for c in mock_attn.call_args_list), \
        mock_attn.call_args_list


def test_gh_error_fails_open_not_found_in_hint_repo():
    """A nonzero gh returncode (PR number not found in the hinted repo, or a
    transient gh error) must fail open, never raise, never block."""
    rg = _load_rg("rg_targetpr_6", {"VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR})
    with mock.patch.object(rg, "_run", return_value=_FakeCompletedProcess(1, "")):
        blocked, evidence = rg.target_pr_already_resolved(
            "Execute the real merge for audit-approved PR 999999", hint_repo="claude-control")
    assert blocked is False
    assert evidence is None


# ---------------------------------------------------------------------------
# 2. _dispatch_one_inner()/dispatch_one() end-to-end: the guard must actually
#    reject a real queued row before spawn, and write a real terminal status.
# ---------------------------------------------------------------------------

def test_dispatch_one_end_to_end_rejects_row_whose_target_pr_already_merged():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_targetpr_e2e", env)
        rg.EMERGENCY_STOP_PATH = os.path.join(d, "EMERGENCY_STOP_never_created")
        rg.STOP_WORK_ORDER_TASK_IDS = ()

        conn = _new_conn(scratch_db)
        umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": "test-targetpr-merged-249", "tier": 1, "status": "queued",
            "source_trigger": "unit_test", "task_kind": "veridian_task_create",
            "inputs": {"repo": "veridian-scripts",
                       "title": "Post real audit verdict directly on existing PR 249",
                       "prompt": "p"},
            "reason": "queued",
        })
        conn.commit()
        conn.close()

        fake_pr = {"number": 249, "state": "MERGED",
                   "mergedAt": "2026-08-13T10:39:54Z", "closedAt": None,
                   "url": "https://github.com/FChecklist/veridian-scripts/pull/249"}
        dc_mod = rg._dispatch_core()

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            with mock.patch.object(dc_mod, "has_free_slot_detail", return_value=(True, {"check": "ok"})), \
                 mock.patch.object(rg, "_run", return_value=_FakeCompletedProcess(0, json.dumps(fake_pr))), \
                 mock.patch.object(rg, "_perform_spawn") as mock_spawn:
                result = rg.dispatch_one()
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)

        assert result["action"] == "rejected_target_pr_already_resolved", result
        assert result["outcome"] == "rejected", result
        assert result["pr"]["number"] == 249 and result["pr"]["state"] == "MERGED", result
        # Load-bearing: the real spawn path must never have been reached.
        mock_spawn.assert_not_called()

        conn = _new_conn(scratch_db)
        row = conn.execute(
            "SELECT status, ts_completed, reason, outputs_json FROM umr_tasks WHERE umr_id=?",
            (umr_id,)).fetchone()
        conn.close()
        assert row["status"] == "rejected_duplicate", row["status"]
        assert row["ts_completed"] is not None
        assert "PR" in row["reason"] and "249" in row["reason"] and "MERGED" in row["reason"], row["reason"]
        outputs = json.loads(row["outputs_json"])
        assert outputs["target_pr"]["number"] == 249


def test_dispatch_one_end_to_end_dirty_open_pr_is_not_rejected_by_this_guard():
    """PR #136's real shape: OPEN/DIRTY must NOT be caught by this guard --
    dispatch proceeds to the real spawn path."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_targetpr_e2e_open", env)
        rg.EMERGENCY_STOP_PATH = os.path.join(d, "EMERGENCY_STOP_never_created")
        rg.STOP_WORK_ORDER_TASK_IDS = ()

        conn = _new_conn(scratch_db)
        sbr.upsert_umr_task(conn, {
            "task_identity": "test-targetpr-open-136", "tier": 1, "status": "queued",
            "source_trigger": "unit_test", "task_kind": "veridian_task_create",
            "inputs": {"repo": "claude-control",
                       "title": "Execute the real merge for audit-approved PR 136",
                       "prompt": "p"},
            "reason": "queued",
        })
        conn.commit()
        conn.close()

        fake_pr = {"number": 136, "state": "OPEN", "mergedAt": None, "closedAt": None,
                   "url": "https://github.com/FChecklist/claude-control/pull/136"}
        dc_mod = rg._dispatch_core()

        def fake_run(cmd, **kwargs):
            # `gh pr view <num> ...` (this guard) returns the single-PR dict;
            # `gh pr list ...` (Stage 4/5/6 duplicate-PR guard, which this
            # row also passes through since the target-PR guard above did
            # not block it) returns an empty list -- no duplicate found,
            # same real fail-open shape `gh` itself returns for "no match".
            if "view" in cmd:
                return _FakeCompletedProcess(0, json.dumps(fake_pr))
            return _FakeCompletedProcess(0, json.dumps([]))

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            with mock.patch.object(dc_mod, "has_free_slot_detail", return_value=(True, {"check": "ok"})), \
                 mock.patch.object(rg, "_run", side_effect=fake_run), \
                 mock.patch.object(dc_mod, "record_dispatch_event", return_value=None), \
                 mock.patch.object(rg, "_perform_spawn",
                                    return_value={"status": "running", "unit_name": "veridian-worker@x.service",
                                                  "outputs": {}}) as mock_spawn:
                result = rg.dispatch_one()
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)

        assert result["action"] != "rejected_target_pr_already_resolved", result
        mock_spawn.assert_called_once()


def test_dispatch_one_end_to_end_closed_unmerged_pr_is_not_rejected_by_this_guard():
    """A CLOSED-without-merge PR with no known real merged successor must
    NOT be caught by this guard (dispatch proceeds to the real spawn
    path). Regression test for the real false self-rejection
    UMR-20260814-132703-a1f9 fixed (see the module docstring above: the
    real veridian-scripts PR#298 that motivated this fix turned out to
    have a real merged successor, #299, so it still correctly blocks --
    covered separately by
    test_dispatch_one_end_to_end_closed_pr_with_merged_successor_still_rejects()
    below; this test uses a different, hypothetical no-successor PR for
    the genuinely-still-open-gap case)."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_targetpr_e2e_closed", env)
        rg.EMERGENCY_STOP_PATH = os.path.join(d, "EMERGENCY_STOP_never_created")
        rg.STOP_WORK_ORDER_TASK_IDS = ()

        conn = _new_conn(scratch_db)
        sbr.upsert_umr_task(conn, {
            "task_identity": "test-targetpr-closed-400", "tier": 1, "status": "queued",
            "source_trigger": "unit_test", "task_kind": "veridian_task_create",
            "inputs": {"repo": "veridian-scripts",
                       "title": "Merge real veridian-scripts PR#400 (outstanding gap, closed unmerged)",
                       "prompt": "p"},
            "reason": "queued",
        })
        conn.commit()
        conn.close()

        fake_pr = {"number": 400, "state": "CLOSED", "mergedAt": None,
                   "closedAt": "2026-08-13T14:09:30Z",
                   "url": "https://github.com/FChecklist/veridian-scripts/pull/400"}
        dc_mod = rg._dispatch_core()

        def fake_run(cmd, **kwargs):
            if "view" in cmd:
                return _FakeCompletedProcess(0, json.dumps(fake_pr))
            return _FakeCompletedProcess(0, json.dumps([]))

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            with mock.patch.object(dc_mod, "has_free_slot_detail", return_value=(True, {"check": "ok"})), \
                 mock.patch.object(rg, "_run", side_effect=fake_run), \
                 mock.patch.object(dc_mod, "record_dispatch_event", return_value=None), \
                 mock.patch.object(rg, "_perform_spawn",
                                    return_value={"status": "running", "unit_name": "veridian-worker@x.service",
                                                  "outputs": {}}) as mock_spawn:
                result = rg.dispatch_one()
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)

        assert result["action"] != "rejected_target_pr_already_resolved", result
        assert result.get("action") != "rejected_duplicate_pr" or "400" not in str(result), result
        mock_spawn.assert_called_once()


def test_dispatch_one_end_to_end_closed_pr_with_merged_successor_still_rejects():
    """UMR-20260814-125933-3377's real shape verbatim: veridian-scripts
    PR#298, real, CLOSED, mergedAt=null, close comment naming real MERGED
    successor #299. dispatch_one() must still reject this row (correctly,
    for the real reason) -- proves the CLOSED-without-merge fix did not
    regress this exact real incident into a false non-rejection."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_targetpr_e2e_superseded", env)
        rg.EMERGENCY_STOP_PATH = os.path.join(d, "EMERGENCY_STOP_never_created")
        rg.STOP_WORK_ORDER_TASK_IDS = ()

        conn = _new_conn(scratch_db)
        umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": "test-targetpr-superseded-298", "tier": 1, "status": "queued",
            "source_trigger": "unit_test", "task_kind": "veridian_task_create",
            "inputs": {"repo": "veridian-scripts",
                       "title": "Merge real veridian-scripts PR#298 (outstanding gap, closed unmerged)",
                       "prompt": "p"},
            "reason": "queued",
        })
        conn.commit()
        conn.close()

        closed_pr = {"number": 298, "state": "CLOSED", "mergedAt": None,
                     "closedAt": "2026-08-13T14:09:30Z",
                     "url": "https://github.com/FChecklist/veridian-scripts/pull/298"}
        comments_payload = {"body": "", "comments": [
            {"body": "Superseded by #299, which now carries this PR's full content forward "
                     "as a strict superset, verified byte-identical via diff."}]}
        successor_pr = {"number": 299, "state": "MERGED",
                         "mergedAt": "2026-08-13T18:49:15Z", "closedAt": "2026-08-13T18:49:15Z",
                         "url": "https://github.com/FChecklist/veridian-scripts/pull/299"}
        dc_mod = rg._dispatch_core()

        def fake_run(cmd, **kwargs):
            if "--json" in cmd and "body,comments" in cmd:
                return _FakeCompletedProcess(0, json.dumps(comments_payload))
            if "299" in cmd:
                return _FakeCompletedProcess(0, json.dumps(successor_pr))
            if "view" in cmd:
                return _FakeCompletedProcess(0, json.dumps(closed_pr))
            return _FakeCompletedProcess(0, json.dumps([]))

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            with mock.patch.object(dc_mod, "has_free_slot_detail", return_value=(True, {"check": "ok"})), \
                 mock.patch.object(rg, "_run", side_effect=fake_run), \
                 mock.patch.object(rg, "_perform_spawn") as mock_spawn:
                result = rg.dispatch_one()
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)

        assert result["action"] == "rejected_target_pr_already_resolved", result
        mock_spawn.assert_not_called()

        conn = _new_conn(scratch_db)
        row = conn.execute(
            "SELECT status, reason FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
        conn.close()
        assert row["status"] == "rejected_duplicate", row["status"]
        assert "299" in row["reason"], row["reason"]


# ---------------------------------------------------------------------------
# 3. UMR-20260813-165620-aac7 (addendum to P1 UMR-20260806-171945-5767):
#    repo-qualified target-PR resolution. Real incident this backstops: the
#    old code resolved a BARE "PR NNN" title reference by scanning
#    GH_PR_CHECK_REPOS in a fixed, arbitrary order and blocking on the FIRST
#    repo where that bare number resolved to MERGED/CLOSED -- even though
#    PR numbers are per-repo. 3 real confirmed rows (UMR-20260813-135121-9da6
#    / -135059-3b92 / -135034-fef1) named veridian-scripts PR #297/#300/#301
#    (each real, MERGEABLE+CLEAN, zero posted audit verdict) and were wrongly
#    rejected because the same bare number resolved first against an
#    unrelated, month-old, already-MERGED compliance-tracker PR.
# ---------------------------------------------------------------------------

def test_bare_pr_number_no_repo_no_hint_fails_open_without_any_gh_call():
    """The core of the real fix: a bare 'PR NNN' title with NO repo
    anywhere in it and NO usable hint_repo must fail open WITHOUT even
    attempting a scan -- never guess a repo by trying GH_PR_CHECK_REPOS in
    arbitrary order (the exact mechanism that caused the real #297/#300/#301
    collisions)."""
    rg = _load_rg("rg_targetpr_qualify_1", {"VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR})
    with mock.patch.object(rg, "_run") as mock_run:
        blocked, evidence = rg.target_pr_already_resolved(
            "Bare PR 297 needs a second look, no repo named anywhere")
    assert blocked is False
    assert evidence is None
    mock_run.assert_not_called()


def test_bare_pr_number_with_hint_repo_checks_only_the_hinted_repo_not_a_collision():
    """Real regression shape for the #297 collision: a bare 'PR 297' with
    hint_repo='veridian-scripts' must resolve ONLY against veridian-scripts
    (OPEN, correctly not blocked) and must NEVER even look at
    compliance-tracker, where an unrelated PR #297 is MERGED."""
    rg = _load_rg("rg_targetpr_qualify_2", {"VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR})

    def fake_run(cmd, **kwargs):
        assert "compliance-tracker" not in " ".join(cmd), cmd
        assert "FChecklist/veridian-scripts" in cmd
        return _FakeCompletedProcess(0, json.dumps(
            {"number": 297, "state": "OPEN", "mergedAt": None, "closedAt": None,
             "url": "https://github.com/FChecklist/veridian-scripts/pull/297"}))

    with mock.patch.object(rg, "_run", side_effect=fake_run) as mock_run:
        blocked, evidence = rg.target_pr_already_resolved(
            "Bare PR 297 needs a second look, no repo named anywhere",
            hint_repo="veridian-scripts")
    assert blocked is False
    assert evidence is None
    mock_run.assert_called_once()


def test_repo_qualified_hash_form_merged_blocks_ignoring_collision():
    """'veridian-scripts#297' is a repo-qualified reference and must resolve
    ONLY against veridian-scripts and block on its real MERGED state --
    regardless of what an unrelated compliance-tracker#297 resolves to, and
    even with no hint_repo at all."""
    rg = _load_rg("rg_targetpr_qualify_3", {"VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR})
    fake_pr = {"number": 297, "state": "MERGED",
               "mergedAt": "2026-07-13T21:05:24Z", "closedAt": None,
               "url": "https://github.com/FChecklist/veridian-scripts/pull/297"}

    def fake_run(cmd, **kwargs):
        assert "FChecklist/veridian-scripts" in cmd
        return _FakeCompletedProcess(0, json.dumps(fake_pr))

    with mock.patch.object(rg, "_run", side_effect=fake_run) as mock_run:
        blocked, evidence = rg.target_pr_already_resolved(
            "Fix real dedup bug tracked at veridian-scripts#297")
    assert blocked is True
    assert evidence["repo"] == "veridian-scripts"
    assert evidence["number"] == 297
    mock_run.assert_called_once()


def test_repo_qualified_title_convention_merged_blocks_overriding_wrong_hint_repo():
    """Real UMR-20260813-135121-9da6 shape verbatim: the real dispatch-title
    convention 'audit of <repo> PR NNN ...' is a repo-qualified reference
    and must be preferred over a WRONG hint_repo (here 'claude-control', the
    real dispatch/runner repo the row's own inputs.repo carried in the
    actual incident -- NOT the repo the target PR lives in)."""
    rg = _load_rg("rg_targetpr_qualify_4", {"VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR})
    fake_pr = {"number": 297, "state": "MERGED",
               "mergedAt": "2026-07-13T21:05:24Z", "closedAt": None,
               "url": "https://github.com/FChecklist/veridian-scripts/pull/297"}

    def fake_run(cmd, **kwargs):
        assert "FChecklist/veridian-scripts" in cmd
        assert "FChecklist/claude-control" not in cmd
        return _FakeCompletedProcess(0, json.dumps(fake_pr))

    with mock.patch.object(rg, "_run", side_effect=fake_run) as mock_run:
        blocked, evidence = rg.target_pr_already_resolved(
            "Real Tier-1 independent audit of veridian-scripts PR 297 "
            "(deterministic target-identifier dedup) at head c3ee2b2d",
            hint_repo="claude-control")
    assert blocked is True
    assert evidence["repo"] == "veridian-scripts"
    assert evidence["number"] == 297
    mock_run.assert_called_once()


def test_word_before_pr_that_is_not_a_known_repo_is_not_treated_as_repo_qualified():
    """Regression guard for the real UMR-20260813-111352-6973 title shape
    ('...audit-approved PR 136') that this function's own docstring already
    documents: the word immediately before 'PR' is NOT a repo name, so it
    must fall through to the bare-PR-number + hint_repo path, not be
    misparsed as a repo-qualified reference naming a nonexistent
    'audit-approved' repo."""
    rg = _load_rg("rg_targetpr_qualify_5", {"VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR})
    fake_pr = {"number": 136, "state": "OPEN", "mergedAt": None, "closedAt": None,
               "url": "https://github.com/FChecklist/claude-control/pull/136"}

    def fake_run(cmd, **kwargs):
        assert "FChecklist/claude-control" in cmd
        assert "audit-approved" not in " ".join(cmd)
        return _FakeCompletedProcess(0, json.dumps(fake_pr))

    with mock.patch.object(rg, "_run", side_effect=fake_run):
        blocked, evidence = rg.target_pr_already_resolved(
            "Execute the real merge for audit-approved PR 136", hint_repo="claude-control")
    assert blocked is False
    assert evidence is None


def test_default_gh_pr_check_repos_includes_veridian_scripts_and_claude_control():
    """UMR-20260813-165620-aac7 item (b): the live default (no env override)
    must include veridian-scripts and claude-control -- the two repos where
    essentially all real infrastructure work lands -- alongside the
    pre-existing compliance-tracker and projexa."""
    os.environ.pop("VERIDIAN_GOVERNOR_GH_PR_CHECK_REPOS", None)
    rg = _load_rg("rg_targetpr_qualify_6", {"VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR})
    assert "veridian-scripts" in rg.GH_PR_CHECK_REPOS, rg.GH_PR_CHECK_REPOS
    assert "claude-control" in rg.GH_PR_CHECK_REPOS, rg.GH_PR_CHECK_REPOS
    assert "compliance-tracker" in rg.GH_PR_CHECK_REPOS, rg.GH_PR_CHECK_REPOS
    assert "projexa" in rg.GH_PR_CHECK_REPOS, rg.GH_PR_CHECK_REPOS


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
