#!/usr/bin/env python3
"""Real tests for pm_lifecycle.py (task-20260814-183228-build-single-command-
full-lifecycle-orch). Covers this task's own SPEC-required regression
coverage -- "at least the polling-to-terminal-state and audit-fail-retry
logic" -- plus the pure decision helpers around them. No real subprocess,
gh, or sqlite calls: every seam (query_fn/sleep_fn/now_fn for polling,
verify_fn/dispatch_fix_fn/poll_fn/query_fn for the retry loop) is
monkeypatched/injected, same "fake the module's own seam, never the real
network/DB" convention tests/test_reconcile_owner_dispatch_status.py and
test_find_real_pr_across_repos.py already use.
"""
import importlib.util
import os
import re
import sys

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "pm_lifecycle_test", os.path.join(SCRIPTS_DIR, "pm_lifecycle.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_plan_generator():
    spec = importlib.util.spec_from_file_location(
        "plan_generator_test", os.path.join(SCRIPTS_DIR, "plan_generator.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# poll_until_terminal
# ---------------------------------------------------------------------------

def test_poll_until_terminal_returns_once_status_leaves_active_set():
    mod = _load_module()
    statuses = iter(["queued", "queued", "running", "completed"])
    rows_by_status = {}

    def fake_query(umr_id):
        status = next(statuses)
        row = {"umr_id": umr_id, "status": status}
        rows_by_status[status] = row
        return row, None

    sleep_calls = []
    clock = iter([0.0, 5.0, 10.0, 15.0, 20.0, 25.0])

    result = mod.poll_until_terminal(
        "UMR-x", poll_interval=5, poll_timeout=300,
        query_fn=fake_query, sleep_fn=lambda s: sleep_calls.append(s),
        now_fn=lambda: next(clock),
    )

    assert result["timed_out"] is False
    assert result["row"]["status"] == "completed"
    assert len(result["samples"]) == 4
    # only slept between samples, never after the terminal one is observed
    assert sleep_calls == [5, 5, 5]
    print("PASS: test_poll_until_terminal_returns_once_status_leaves_active_set")


def test_poll_until_terminal_times_out_while_still_active():
    mod = _load_module()

    def fake_query(umr_id):
        return {"umr_id": umr_id, "status": "running"}, None

    clock = iter([0.0, 100.0, 200.0, 300.0])
    sleep_calls = []

    result = mod.poll_until_terminal(
        "UMR-x", poll_interval=100, poll_timeout=250,
        query_fn=fake_query, sleep_fn=lambda s: sleep_calls.append(s),
        now_fn=lambda: next(clock),
    )

    assert result["timed_out"] is True
    assert result["row"]["status"] == "running"
    # stops polling once elapsed >= poll_timeout, never spins forever
    assert len(result["samples"]) == 3
    print("PASS: test_poll_until_terminal_times_out_while_still_active")


def test_poll_until_terminal_handles_row_never_found():
    """A umr_id that never resolves to a real row (e.g. a query error every
    time) must still terminate on poll_timeout, not raise or loop forever."""
    mod = _load_module()
    clock = iter([0.0, 60.0, 120.0])

    result = mod.poll_until_terminal(
        "UMR-missing", poll_interval=60, poll_timeout=100,
        query_fn=lambda uid: (None, "no real umr_tasks row found"),
        sleep_fn=lambda s: None, now_fn=lambda: next(clock),
    )
    assert result["timed_out"] is True
    assert result["row"] is None
    print("PASS: test_poll_until_terminal_handles_row_never_found")


# ---------------------------------------------------------------------------
# fresh_audit_pass / fresh_audit_fail / should_retry_fix
# ---------------------------------------------------------------------------

def _evidence(verdict_line, stale, repo="veridian-scripts", pr_number=42, branch="b1"):
    return {
        "repo": repo,
        "pr_match": {"number": pr_number, "headRefName": branch, "state": "OPEN"},
        "audit": {
            "verdict": {"verdict": verdict_line, "createdAt": "2026-08-14T10:00:00Z"},
            "current_head_sha": "deadbeef",
            "head_committed_at": "2026-08-14T09:00:00Z",
            "stale": stale,
        },
    }


def test_fresh_audit_pass_true_only_when_pass_and_not_stale():
    mod = _load_module()
    assert mod.fresh_audit_pass(_evidence("AUDIT: PASS", stale=False)) is True
    assert mod.fresh_audit_pass(_evidence("AUDIT: PASS", stale=True)) is False
    assert mod.fresh_audit_pass(_evidence("AUDIT: FAIL", stale=False)) is False
    print("PASS: test_fresh_audit_pass_true_only_when_pass_and_not_stale")


def test_fresh_audit_fail_ignores_stale_fail():
    mod = _load_module()
    # A real FAIL comment that predates the PR's current head reviewed old
    # code -- must never trigger a real redispatch against the fix already
    # on the branch.
    assert mod.fresh_audit_fail(_evidence("AUDIT: FAIL", stale=True)) is False
    assert mod.fresh_audit_fail(_evidence("AUDIT: FAIL", stale=False)) is True
    assert mod.fresh_audit_fail(_evidence("AUDIT: FAIL", stale=None)) is True
    print("PASS: test_fresh_audit_fail_ignores_stale_fail")


def test_should_retry_fix_caps_at_max_retries():
    mod = _load_module()
    fail_ev = _evidence("AUDIT: FAIL", stale=False)
    assert mod.should_retry_fix(fail_ev, retry_count=0, max_retries=2) is True
    assert mod.should_retry_fix(fail_ev, retry_count=1, max_retries=2) is True
    assert mod.should_retry_fix(fail_ev, retry_count=2, max_retries=2) is False
    print("PASS: test_should_retry_fix_caps_at_max_retries")


def test_should_retry_fix_false_on_real_pass():
    mod = _load_module()
    pass_ev = _evidence("AUDIT: PASS", stale=False)
    assert mod.should_retry_fix(pass_ev, retry_count=0, max_retries=2) is False
    print("PASS: test_should_retry_fix_false_on_real_pass")


# ---------------------------------------------------------------------------
# verify_with_retries -- the real end-to-end audit-fail-retry loop
# ---------------------------------------------------------------------------

def test_verify_with_retries_stops_after_real_fresh_pass():
    """FAIL, FAIL, then a real fresh PASS after the 2nd fix lands -- must
    dispatch exactly twice and stop (never a 3rd redispatch once verified
    passing)."""
    mod = _load_module()
    evidences = [
        _evidence("AUDIT: FAIL", stale=False),
        _evidence("AUDIT: FAIL", stale=False),
        _evidence("AUDIT: PASS", stale=False),
    ]
    verify_calls = []

    def fake_verify(row, rodl):
        verify_calls.append(row)
        return evidences[len(verify_calls) - 1]

    dispatch_calls = []

    def fake_dispatch_fix(evidence, tier, medium, repo, no_relay=False):
        dispatch_calls.append(evidence)
        return {"outcome": "dispatched", "umr_id": f"UMR-fix-{len(dispatch_calls)}"}

    poll_calls = []

    def fake_poll(umr_id, poll_interval, poll_timeout):
        poll_calls.append(umr_id)
        return {"row": {"umr_id": umr_id, "status": "completed_unmerged"}, "timed_out": False, "samples": []}

    def fake_query(umr_id):
        return {"umr_id": umr_id, "status": "completed_unmerged"}, None

    result = mod.verify_with_retries(
        row={"umr_id": "UMR-orig", "status": "completed_unmerged"}, umr_id="UMR-orig",
        rodl=None, tier=4, medium="claude_code_cli", repo="veridian-scripts", no_relay=False,
        poll_interval=1, poll_timeout=10, max_fix_retries=2,
        verify_fn=fake_verify, dispatch_fix_fn=fake_dispatch_fix,
        poll_fn=fake_poll, query_fn=fake_query,
    )

    assert result["retries"] == 2
    assert len(dispatch_calls) == 2
    assert len(poll_calls) == 2
    assert result["verify_evidence"]["audit"]["verdict"]["verdict"] == "AUDIT: PASS"
    print("PASS: test_verify_with_retries_stops_after_real_fresh_pass")


def test_verify_with_retries_gives_up_at_cap_never_loops_forever():
    """Real, persistent AUDIT:FAIL every single time -- must stop dispatching
    at max_fix_retries and surface a non-passing verdict, never loop
    forever."""
    mod = _load_module()
    fail_ev = _evidence("AUDIT: FAIL", stale=False)
    dispatch_calls = []

    def fake_dispatch_fix(evidence, tier, medium, repo, no_relay=False):
        dispatch_calls.append(evidence)
        return {"outcome": "dispatched", "umr_id": f"UMR-fix-{len(dispatch_calls)}"}

    def fake_poll(umr_id, poll_interval, poll_timeout):
        return {"row": {"umr_id": umr_id, "status": "completed_unmerged"}, "timed_out": False, "samples": []}

    def fake_query(umr_id):
        return {"umr_id": umr_id, "status": "completed_unmerged"}, None

    result = mod.verify_with_retries(
        row={"umr_id": "UMR-orig", "status": "completed_unmerged"}, umr_id="UMR-orig",
        rodl=None, tier=4, medium="claude_code_cli", repo="veridian-scripts", no_relay=False,
        poll_interval=1, poll_timeout=10, max_fix_retries=2,
        verify_fn=lambda row, rodl: fail_ev,
        dispatch_fix_fn=fake_dispatch_fix, poll_fn=fake_poll, query_fn=fake_query,
    )

    assert result["retries"] == 2
    assert len(dispatch_calls) == 2  # capped, not 3+
    assert mod.fresh_audit_pass(result["verify_evidence"]) is False
    print("PASS: test_verify_with_retries_gives_up_at_cap_never_loops_forever")


def _evidence_no_audit(repo="veridian-scripts", pr_number=42, branch="b1", state="OPEN"):
    return {"repo": repo, "pr_match": {"number": pr_number, "headRefName": branch, "state": state}, "audit": None}


def test_needs_independent_audit_true_only_for_open_pr_with_no_verdict():
    mod = _load_module()
    assert mod.needs_independent_audit(_evidence_no_audit()) is True
    assert mod.needs_independent_audit(_evidence("AUDIT: PASS", stale=False)) is False
    assert mod.needs_independent_audit(_evidence("AUDIT: FAIL", stale=False)) is False
    assert mod.needs_independent_audit(_evidence_no_audit(state="MERGED")) is False
    assert mod.needs_independent_audit({"repo": "x", "pr_match": None, "audit": None}) is False
    print("PASS: test_needs_independent_audit_true_only_for_open_pr_with_no_verdict")


def test_decide_next_action_full_matrix():
    mod = _load_module()
    assert mod.decide_next_action(_evidence("AUDIT: PASS", stale=False), 0, 0, 2) == "proceed"
    assert mod.decide_next_action(_evidence("AUDIT: FAIL", stale=False), 0, 0, 2) == "dispatch_fix"
    assert mod.decide_next_action(_evidence("AUDIT: FAIL", stale=False), 2, 0, 2) == "stop"  # fix cap hit
    assert mod.decide_next_action(_evidence_no_audit(), 0, 0, 2) == "dispatch_audit"
    assert mod.decide_next_action(_evidence_no_audit(), 0, 2, 2) == "stop"  # audit cap hit
    assert mod.decide_next_action(_evidence("AUDIT: FAIL", stale=True), 0, 0, 2) == "stop"  # stale, ambiguous
    print("PASS: test_decide_next_action_full_matrix")


def test_decide_next_action_fix_and_audit_retry_caps_are_independent():
    """Real fix (this task's own SPEC, secondary finding): a dispatch_audit
    round must never eat into the SAME cap a later real dispatch_fix
    redispatch needs, and vice versa -- each is capped independently."""
    mod = _load_module()
    fail_ev = _evidence("AUDIT: FAIL", stale=False)
    no_audit_ev = _evidence_no_audit()
    # fix cap already exhausted, but audit_retry_count is still 0 -- an
    # audit-needed evidence must still dispatch_audit, unaffected by the
    # fix counter being maxed out.
    assert mod.decide_next_action(no_audit_ev, 2, 0, 2) == "dispatch_audit"
    # audit cap already exhausted, but fix_retry_count is still 0 -- a
    # fresh AUDIT:FAIL must still dispatch_fix, unaffected by the audit
    # counter being maxed out.
    assert mod.decide_next_action(fail_ev, 0, 2, 2) == "dispatch_fix"
    print("PASS: test_decide_next_action_fix_and_audit_retry_caps_are_independent")


def test_verify_with_retries_tracks_fix_and_audit_retries_independently():
    """End-to-end: one real dispatch_audit round (no audit posted yet) is
    followed by two real dispatch_fix rounds (fresh AUDIT:FAIL each time)
    before a fresh AUDIT:PASS lands -- with max_fix_retries=2, this must
    NOT stop early just because 3 total dispatches happened; fix_retries
    (2) and audit_retries (1) are each within their own cap."""
    mod = _load_module()
    evidences = [
        _evidence_no_audit(),
        _evidence("AUDIT: FAIL", stale=False),
        _evidence("AUDIT: FAIL", stale=False),
        _evidence("AUDIT: PASS", stale=False),
    ]
    verify_calls = []

    def fake_verify(row, rodl):
        verify_calls.append(row)
        return evidences[len(verify_calls) - 1]

    audit_calls = []
    fix_calls = []

    def fake_dispatch_audit(evidence, tier, medium, repo, no_relay=False):
        audit_calls.append(evidence)
        return {"outcome": "dispatched", "umr_id": f"UMR-audit-{len(audit_calls)}"}

    def fake_dispatch_fix(evidence, tier, medium, repo, no_relay=False):
        fix_calls.append(evidence)
        return {"outcome": "dispatched", "umr_id": f"UMR-fix-{len(fix_calls)}"}

    result = mod.verify_with_retries(
        row={"umr_id": "UMR-orig", "status": "completed_unmerged"}, umr_id="UMR-orig",
        rodl=None, tier=4, medium="claude_code_cli", repo="veridian-scripts", no_relay=False,
        poll_interval=1, poll_timeout=10, max_fix_retries=2,
        verify_fn=fake_verify, dispatch_fix_fn=fake_dispatch_fix, dispatch_audit_fn=fake_dispatch_audit,
        poll_fn=lambda *a, **k: {"row": {"umr_id": "UMR-orig", "status": "completed_unmerged"}, "timed_out": False, "samples": []},
        query_fn=lambda umr_id: ({"umr_id": umr_id, "status": "completed_unmerged"}, None),
    )

    assert len(audit_calls) == 1
    assert len(fix_calls) == 2
    assert result["audit_retries"] == 1
    assert result["fix_retries"] == 2
    assert result["retries"] == 3
    assert mod.fresh_audit_pass(result["verify_evidence"]) is True
    print("PASS: test_verify_with_retries_tracks_fix_and_audit_retries_independently")


def test_verify_with_retries_dispatches_independent_audit_when_none_posted_yet():
    """The tier-3/4 headless-dispatch gap: a real OPEN, real PR with NO
    audit comment at all must get a real independent-audit dispatch, not
    get silently stuck forever."""
    mod = _load_module()
    evidences = [_evidence_no_audit(), _evidence("AUDIT: PASS", stale=False)]
    verify_calls = []

    def fake_verify(row, rodl):
        verify_calls.append(row)
        return evidences[len(verify_calls) - 1]

    audit_dispatch_calls = []
    fix_dispatch_calls = []

    def fake_dispatch_audit(evidence, tier, medium, repo, no_relay=False):
        audit_dispatch_calls.append(evidence)
        return {"outcome": "dispatched", "umr_id": "UMR-audit-1"}

    def fake_dispatch_fix(evidence, tier, medium, repo, no_relay=False):
        fix_dispatch_calls.append(evidence)
        return {"outcome": "dispatched", "umr_id": "UMR-fix-1"}

    result = mod.verify_with_retries(
        row={"umr_id": "UMR-orig", "status": "completed_unmerged"}, umr_id="UMR-orig",
        rodl=None, tier=4, medium="claude_code_cli", repo="veridian-scripts", no_relay=False,
        poll_interval=1, poll_timeout=10, max_fix_retries=2,
        verify_fn=fake_verify, dispatch_fix_fn=fake_dispatch_fix, dispatch_audit_fn=fake_dispatch_audit,
        poll_fn=lambda *a, **k: {"row": {}, "timed_out": False, "samples": []},
        query_fn=lambda umr_id: ({"umr_id": umr_id, "status": "completed_unmerged"}, None),
    )

    assert len(audit_dispatch_calls) == 1
    assert len(fix_dispatch_calls) == 0
    assert mod.fresh_audit_pass(result["verify_evidence"]) is True
    print("PASS: test_verify_with_retries_dispatches_independent_audit_when_none_posted_yet")


def test_verify_with_retries_stops_immediately_when_dispatch_is_refused():
    """If the real fix dispatch itself is refused/rejected (not a timeout,
    not a poll issue), the loop must stop rather than retry the same
    refused dispatch forever."""
    mod = _load_module()
    fail_ev = _evidence("AUDIT: FAIL", stale=False)
    dispatch_calls = []

    def fake_dispatch_fix(evidence, tier, medium, repo, no_relay=False):
        dispatch_calls.append(evidence)
        return {"outcome": "refused", "umr_id": "UMR-fix-1"}

    result = mod.verify_with_retries(
        row={"umr_id": "UMR-orig", "status": "completed_unmerged"}, umr_id="UMR-orig",
        rodl=None, tier=4, medium="claude_code_cli", repo="veridian-scripts", no_relay=False,
        poll_interval=1, poll_timeout=10, max_fix_retries=2,
        verify_fn=lambda row, rodl: fail_ev, dispatch_fix_fn=fake_dispatch_fix,
        poll_fn=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not poll a refused dispatch")),
        query_fn=lambda umr_id: (_ for _ in ()).throw(AssertionError("must not re-query after a refused dispatch")),
    )

    assert len(dispatch_calls) == 1
    assert result["retries"] == 1
    print("PASS: test_verify_with_retries_stops_immediately_when_dispatch_is_refused")


# ---------------------------------------------------------------------------
# Step 7: classify_merge_tier / merge_and_reverify -- the real safety-critical
# tier2 human-sign-off gate (PR #389's own AUDIT:FAIL finding, this task's
# own SPEC)
# ---------------------------------------------------------------------------

def _merge_evidence(repo="veridian-scripts", pr_number=42, branch="b1", state="OPEN"):
    return {"repo": repo, "pr_match": {"number": pr_number, "headRefName": branch, "state": state}}


def test_classify_merge_tier_real_tier2_path_pattern():
    """A real fixture PR touching a migrations/ file must classify as
    tier2 -- reuses policy_decision.classify_risk_tier() directly (same
    TIER2_PATH_PATTERNS risk-tier.py itself uses), fed from a fake
    `gh pr view --json files` response (no real gh/subprocess call)."""
    mod = _load_module()

    def fake_run_json(cmd, timeout=60):
        assert cmd[:3] == ["gh", "pr", "view"]
        return 0, {"files": [{"path": "migrations/0042_add_col.sql", "additions": 10, "deletions": 0}]}, ""

    result = mod.classify_merge_tier(_merge_evidence(), run_json_fn=fake_run_json)
    assert result["tier"] == "tier2"
    print("PASS: test_classify_merge_tier_real_tier2_path_pattern")


def test_classify_merge_tier_real_tier1_for_ordinary_files():
    mod = _load_module()

    def fake_run_json(cmd, timeout=60):
        return 0, {"files": [{"path": "README.md", "additions": 3, "deletions": 1}]}, ""

    result = mod.classify_merge_tier(_merge_evidence(), run_json_fn=fake_run_json)
    assert result["tier"] == "tier1"
    print("PASS: test_classify_merge_tier_real_tier1_for_ordinary_files")


def test_classify_merge_tier_fails_closed_on_unclassifiable_diff():
    """Real safety posture: a gh error/timeout must never be treated as
    tier1-safe-to-merge -- fails closed to tier2."""
    mod = _load_module()

    def fake_run_json(cmd, timeout=60):
        return 1, None, "gh: real API error"

    result = mod.classify_merge_tier(_merge_evidence(), run_json_fn=fake_run_json)
    assert result["tier"] == "tier2"
    print("PASS: test_classify_merge_tier_fails_closed_on_unclassifiable_diff")


class _ModAttrPatch:
    """Real module-attribute monkeypatch/restore, same convention
    tests/test_reconcile_owner_dispatch_status.py already uses for _run/
    _run_json seams that this task's own new merge_and_reverify() code
    doesn't (yet) take as injectable params."""
    def __init__(self, mod, name, value):
        self.mod, self.name, self.value = mod, name, value

    def __enter__(self):
        self.orig = getattr(self.mod, self.name)
        setattr(self.mod, self.name, self.value)
        return self

    def __exit__(self, *exc):
        setattr(self.mod, self.name, self.orig)


def test_merge_and_reverify_holds_real_tier2_pr_never_calls_gh_merge():
    """THE real regression test for PR #389's own AUDIT:FAIL finding: a
    real tier2-classified fixture PR must be held (hold_for_owner_signoff),
    and `gh pr merge` must NEVER be invoked for it -- asserted by making
    the real module-level _run raise if it's ever called with a merge
    subcommand."""
    mod = _load_module()

    def refuse_merge_call(cmd, timeout=60, **kw):
        if "merge" in cmd:
            raise AssertionError("must never call `gh pr merge` on a real tier2 PR")
        return 0, "", ""

    with _ModAttrPatch(mod, "_run", refuse_merge_call):
        result = mod.merge_and_reverify(
            _merge_evidence(), classify_tier_fn=lambda ev: {"tier": "tier2", "reasons": ["security/: x.py"]},
        )

    assert result["merged"] is False
    assert result["hold_for_owner_signoff"] is True
    assert result["tier_classification"]["tier"] == "tier2"
    print("PASS: test_merge_and_reverify_holds_real_tier2_pr_never_calls_gh_merge")


def test_merge_and_reverify_merges_real_tier1_pr_with_passing_checks():
    mod = _load_module()
    calls = []

    def fake_run(cmd, timeout=60, **kw):
        calls.append(cmd)
        return 0, "", ""

    def fake_run_json(cmd, timeout=60):
        if "statusCheckRollup" in cmd and "state,mergedAt,mergeCommit,statusCheckRollup" not in cmd:
            return 0, {"statusCheckRollup": [{"name": "ci", "conclusion": "SUCCESS"}]}, ""
        return 0, {"state": "MERGED", "mergedAt": "2026-08-14T12:00:00Z",
                    "mergeCommit": {"oid": "deadbeef"},
                    "statusCheckRollup": [{"name": "ci", "conclusion": "SUCCESS"}]}, ""

    with _ModAttrPatch(mod, "_run", fake_run), _ModAttrPatch(mod, "_run_json", fake_run_json):
        result = mod.merge_and_reverify(
            _merge_evidence(), classify_tier_fn=lambda ev: {"tier": "tier1", "reasons": []},
        )

    assert result["merged"] is True
    assert any("merge" in c for c in calls)
    print("PASS: test_merge_and_reverify_merges_real_tier1_pr_with_passing_checks")


def test_merge_and_reverify_never_merges_tier1_pr_with_failing_checks():
    """Secondary finding (this task's own SPEC): the merge itself must be
    hard-gated on real passing required checks, not just recorded after
    the fact -- a real FAILURE conclusion must block `gh pr merge`."""
    mod = _load_module()

    def refuse_merge_call(cmd, timeout=60, **kw):
        if "merge" in cmd:
            raise AssertionError("must never call `gh pr merge` with a real failing check")
        return 0, "", ""

    def fake_run_json(cmd, timeout=60):
        return 0, {"statusCheckRollup": [{"name": "ci", "conclusion": "FAILURE"}]}, ""

    with _ModAttrPatch(mod, "_run", refuse_merge_call), _ModAttrPatch(mod, "_run_json", fake_run_json):
        result = mod.merge_and_reverify(
            _merge_evidence(), classify_tier_fn=lambda ev: {"tier": "tier1", "reasons": []},
        )

    assert result["merged"] is False
    assert result["checks_not_passing"] is True
    print("PASS: test_merge_and_reverify_never_merges_tier1_pr_with_failing_checks")


def test_merge_and_reverify_skips_tier_and_checks_gate_for_already_merged_pr():
    """A real ALREADY-MERGED PR must skip tier classification and the
    checks gate entirely -- nothing left to hold, and re-classifying an
    already-merged PR's diff is pointless real work."""
    mod = _load_module()
    classify_calls = []

    def fake_run_json(cmd, timeout=60):
        return 0, {"state": "MERGED", "mergedAt": "2026-08-14T12:00:00Z",
                    "mergeCommit": {"oid": "deadbeef"}, "statusCheckRollup": []}, ""

    def refuse_classify(ev):
        classify_calls.append(ev)
        raise AssertionError("must never classify an already-merged PR")

    with _ModAttrPatch(mod, "_run_json", fake_run_json):
        result = mod.merge_and_reverify(
            _merge_evidence(state="MERGED"), classify_tier_fn=refuse_classify,
        )

    assert classify_calls == []
    assert result["merged"] is True
    assert result["already_merged_by_platform"] is True
    print("PASS: test_merge_and_reverify_skips_tier_and_checks_gate_for_already_merged_pr")


def test_checks_evidence_treats_real_pending_check_as_not_tested():
    """Secondary finding (this task's own SPEC): a real pending check
    (conclusion=None, still running) must NOT be silently treated as
    passing just because it hasn't failed yet."""
    mod = _load_module()
    view = {"statusCheckRollup": [{"name": "ci", "conclusion": None, "state": "IN_PROGRESS"}]}
    result = mod.checks_evidence(view)
    assert result["tested"] is False
    print("PASS: test_checks_evidence_treats_real_pending_check_as_not_tested")


# ---------------------------------------------------------------------------
# build_tightened_prompt / check_deterministic_path -- small structural checks
# ---------------------------------------------------------------------------

def test_build_tightened_prompt_has_labeled_sections():
    mod = _load_module()
    prompt = mod.build_tightened_prompt(
        "do the thing", "only this repo", "run: python3 -m pytest tests/",
        "a real merged PR", known_context="read X first",
    )
    for header in ("## OBJECTIVE", "## SCOPE", "## SUCCESS_CRITERIA",
                   "## EXPECTED_OUTPUT", "## KNOWN_CONTEXT", "## COMPLEXITY_TIER"):
        assert header in prompt
    print("PASS: test_build_tightened_prompt_has_labeled_sections")


def test_check_deterministic_path_never_auto_executes_unlisted_capability():
    mod = _load_module()
    classify = {
        "capability_deterministic_path_available": True,
        "capability_matches": [
            {"capability_name": "some_unreviewed_capability", "ai_required": False, "apis": ["foo"]},
        ],
    }
    det = mod.check_deterministic_path(classify)
    assert det["capability_deterministic_path_available"] is True
    assert len(det["deterministic_capability_matches"]) == 1
    # SAFE_DETERMINISTIC_EXECUTORS is empty by default -- nothing is ever
    # auto-executed just because task-gateway.py submit found a
    # deterministic-looking capability row.
    assert det["auto_executable_matches"] == []
    print("PASS: test_check_deterministic_path_never_auto_executes_unlisted_capability")


# ---------------------------------------------------------------------------
# complexity_tier / plan_generator.VALID_TIERS (task-20260815-225232-reject-
# invalid-complexity-tier-constant) -- real regression for the schema-
# rejection root cause: pm_lifecycle.py used to hardcode complexity_tier=
# "moderate" in four places, but "moderate" is not a member of
# plan_generator.VALID_TIERS, so tight_task_validation.py's own schema gate
# hard-rejected every task minted through pm_lifecycle.py with reason_code
# tight_task_schema_violation before it ever touched a file.
# ---------------------------------------------------------------------------

COMPLEXITY_TIER_LITERAL_RE = re.compile(r'complexity_tier\s*=\s*"([^"]+)"')
COMPLEXITY_TIER_ARGPARSE_DEFAULT_RE = re.compile(
    r'--complexity-tier",\s*default\s*=\s*"([^"]+)"'
)


def test_every_complexity_tier_literal_in_pm_lifecycle_is_a_valid_tier():
    valid_tiers = set(_load_plan_generator().VALID_TIERS)
    with open(os.path.join(SCRIPTS_DIR, "pm_lifecycle.py")) as f:
        source = f.read()

    literals = set(COMPLEXITY_TIER_LITERAL_RE.findall(source))
    literals |= set(COMPLEXITY_TIER_ARGPARSE_DEFAULT_RE.findall(source))
    # sanity check on the regex itself: this file really does hardcode
    # complexity_tier literals -- if this ever goes empty, the scan below
    # would trivially "pass" without checking anything real.
    assert literals, "expected to find real complexity_tier string literals in pm_lifecycle.py"

    invalid = literals - valid_tiers
    assert not invalid, (
        f"pm_lifecycle.py hardcodes complexity_tier literal(s) {sorted(invalid)!r} "
        f"that are NOT members of plan_generator.VALID_TIERS {sorted(valid_tiers)!r} -- "
        "these will be hard-rejected by tight_task_validation.py's schema gate "
        "(reason_code tight_task_schema_violation) at dispatch time"
    )
    print("PASS: test_every_complexity_tier_literal_in_pm_lifecycle_is_a_valid_tier")


def test_build_tightened_prompt_raises_on_invalid_complexity_tier():
    mod = _load_module()
    try:
        mod.build_tightened_prompt(
            "do the thing", "only this repo", "run: python3 -m pytest tests/",
            "a real merged PR", complexity_tier="moderate",
        )
    except ValueError as e:
        assert "moderate" in str(e)
        print("PASS: test_build_tightened_prompt_raises_on_invalid_complexity_tier")
        return
    raise AssertionError("expected ValueError for complexity_tier='moderate' (not a VALID_TIERS member)")


def test_build_tightened_prompt_accepts_every_valid_tier():
    mod = _load_module()
    for tier in _load_plan_generator().VALID_TIERS:
        prompt = mod.build_tightened_prompt(
            "do the thing", "only this repo", "criteria", "output",
            complexity_tier=tier,
        )
        assert prompt.endswith(tier)
    print("PASS: test_build_tightened_prompt_accepts_every_valid_tier")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failures = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            failures += 1
            print(f"FAIL: {t.__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
