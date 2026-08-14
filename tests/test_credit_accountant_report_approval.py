#!/usr/bin/env python3
"""Real regression test for UMR-20260814-045316 (P0 fleet-wide report-approval
gate deadlock, dispatched off the PM-Desktop sentinel's 2026-08-14 04:44-05:00
UTC evidence).

SYMPTOM this backstops: 9 of the 10 most recently dispatched tasks (task.yaml
dated 2026-08-14 03:34-04:21 UTC) terminated status=blocked with every
worker.log carrying the identical line `{"approved": false, "reason": "no
matching approved plan for this task_id/increment -- report rejected"}` --
credit-accountant.py's own cmd_report(), line ~316.

REAL ROOT CAUSE, confirmed live by reading credit-accountant.py lines 280-340
directly: cmd_report()'s task_id/increment MATCHING LOGIC itself (`SELECT
plan_verdict FROM credit_increments WHERE task_id = ? AND increment_number =
?`) is correct and untouched by this fix -- a report for a task_id/increment
that genuinely had an approved propose() row DOES match. The real defect was
one specific caller never inserting that row in the first place:
resource_governor.py's `_perform_spawn()` -- the real, mechanical spawn path
`next_queued_task()`/`dispatch_one()` uses for every queued
task_kind='veridian_task_create' row regardless of submitter (including
dispatch-owner-task.sh's owner_dispatch_gateway, per task-gateway.py's own
UMR171945-0001 docstring enumerating it as a real, direct `resource_governor.py
--submit` caller) -- never called `credit-accountant.py propose` for
increment 1, unlike task-gateway.py's OWN direct/synchronous `cmd_task_start`
spawn path, which was already fixed for this identical bug on 2026-07-26 (see
that call site's own comment). Every task_id minted via the mechanical queue
path therefore had zero credit_increments rows, so worker-entrypoint.sh's own
unconditional `credit-accountant.py report --increment 1` checkpoint call
always hit the real, correct `row is None` branch and rejected.

This test proves BOTH halves directly against the real, unmodified
credit-accountant.py: (1) a task_id/increment that WAS proposed and approved
(the real, correct flow _perform_spawn() now performs, per this UMR's fix in
resource_governor.py) is approved at report time; (2) a task_id/increment
that was NEVER proposed (the real pre-fix shape every one of the 9 blocked
tasks had) is rejected at report time with the real "no matching approved
plan" reason -- proving the gate's own matching logic was never the bug, and
this fix does not disable, no-op, or blanket-approve it.

Every real AI judgment call (`claude -p`, real subscription cost) and every
real network call (OpenRouter balance, superboss-register.py duplicate check)
is mocked at credit-accountant.py's own module-level function boundaries --
same convention as this repo's other end-to-end tests (e.g.
tests/test_veridian_task_create_stderr_and_reason.py mocking resource_governor
._run()). A real, isolated, temp-file SQLite ledger is used throughout --
never the live production credit-ledger.sqlite.
"""
import argparse
import importlib.util
import json
import os
import sys

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)


def _load_credit_accountant(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS_DIR, "credit-accountant.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ca(tmp_path, monkeypatch):
    mod = _load_credit_accountant(f"credit_accountant_report_approval_test_{id(tmp_path)}")
    monkeypatch.setattr(mod, "LEDGER_PATH", str(tmp_path / "credit-ledger.sqlite"))
    # Deterministic checks stubbed to "cannot decide" / "no duplicate" so
    # every propose() call in this test reaches the mocked claude_judgment_call
    # below, exactly like a real increment with no cheaper deterministic
    # answer available.
    monkeypatch.setattr(mod, "get_openrouter_remaining", lambda: None)
    monkeypatch.setattr(mod, "check_existing_capability", lambda terms: (False, None))
    monkeypatch.setattr(mod, "claude_judgment_call", lambda prompt_body: ("PASS", "test stub: real, proportionate work"))
    return mod


def _propose_args(task_id, plan="test plan text", search_terms="test-search-term", repo=None):
    return argparse.Namespace(task_id=task_id, plan=plan, search_terms=search_terms, repo=repo)


def _report_args(task_id, increment, outcome="real progress made in this regression test", actual_spend_usd=0.42):
    return argparse.Namespace(task_id=task_id, increment=increment, outcome=outcome, actual_spend_usd=actual_spend_usd)


def _run_cmd(func, args, capsys):
    with pytest.raises(SystemExit) as exc:
        func(args)
    out = json.loads(capsys.readouterr().out)
    return exc.value.code, out


def test_report_approved_for_a_legitimately_proposed_task(ca, capsys):
    """The real, correct flow: propose() ran first (as _perform_spawn() now
    does for every veridian_task_create row) and was approved -- report() for
    that exact task_id/increment must find the real row and approve."""
    task_id = "task-20260814-regression-legit-seeded"

    code, propose_out = _run_cmd(ca.cmd_propose, _propose_args(task_id), capsys)
    assert code == 0
    assert propose_out["approved"] is True, propose_out

    code, report_out = _run_cmd(ca.cmd_report, _report_args(task_id, 1), capsys)
    assert code == 0
    assert report_out["approved"] is True, report_out


def test_report_rejected_for_a_task_never_proposed(ca, capsys):
    """The exact real shape every one of the 9 blocked 2026-08-14 tasks had:
    no propose() ever ran for this task_id (the pre-fix resource_governor.py
    mechanical spawn path). report() must reject with the real, unchanged
    'no matching approved plan' reason -- proves this fix left the gate's
    own matching/deny logic intact rather than weakening it."""
    task_id = "task-20260814-regression-never-proposed"

    code, report_out = _run_cmd(ca.cmd_report, _report_args(task_id, 1), capsys)
    assert code == 1
    assert report_out["approved"] is False, report_out
    assert "no matching approved plan" in report_out["reason"], report_out


def test_report_rejected_when_prior_propose_was_denied(ca, capsys, monkeypatch):
    """A propose() that was itself rejected (e.g. claude_judgment_call
    returned FAIL) must still leave report() rejecting -- the gate must not
    silently promote a denied plan to approved just because a row exists."""
    task_id = "task-20260814-regression-denied-plan"
    monkeypatch.setattr(ca, "claude_judgment_call", lambda prompt_body: ("FAIL", "test stub: duplicate work"))

    code, propose_out = _run_cmd(ca.cmd_propose, _propose_args(task_id), capsys)
    assert code == 1
    assert propose_out["approved"] is False, propose_out

    code, report_out = _run_cmd(ca.cmd_report, _report_args(task_id, 1), capsys)
    assert code == 1
    assert report_out["approved"] is False, report_out
    assert "no matching approved plan" in report_out["reason"], report_out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
