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
import sys

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "pm_lifecycle_test", os.path.join(SCRIPTS_DIR, "pm_lifecycle.py"),
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
