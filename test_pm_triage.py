#!/usr/bin/env python3
"""
Real, executable test for the 2026-08-02 PM triage escalation feature
(PM decision UMR-20260802-090702-c813), extending dispatch-tick.py's
stuck-task/heartbeat surface (PR #14) with a strictly-scoped headless-Claude
triage step.

Everything here is in-process against should_triage_pm()/
_find_fresh_audit_fail_tasks()/_capture_tmux_pending_input()/
_invoke_triage_claude()/append_pm_triage_alert()/pm_triage_tick() directly,
using synthetic task dicts, an injected run_fn stub (NEVER a real `claude`
subprocess call -- no real API budget spent by this test), and a tempdir
alert-file path. Never touches the real live PM_TRIAGE_ALERTS.md, the real
tmux "claude" session's actual content beyond a read-only has-session check,
or any real task.yaml.

Usage: python3 test_pm_triage.py
Exit 0 = all assertions passed. Exit 1 = a test failed.
"""
import datetime
import importlib.util
import json
import os
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

failures = []


def check(label, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        failures.append(label)


def load_dispatch_tick():
    spec = importlib.util.spec_from_file_location(
        "dispatch_tick", os.path.join(SCRIPTS_DIR, "dispatch-tick.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


NOW = datetime.datetime(2026, 8, 2, 12, 0, 0, tzinfo=datetime.timezone.utc)


# ---------------------------------------------------------------------------
# _find_fresh_audit_fail_tasks()
# ---------------------------------------------------------------------------

def test_find_fresh_audit_fail_tasks():
    dt = load_dispatch_tick()
    tasks = {
        "task-real-rejection": {
            "status": "blocked",
            "checkpoints": [{"note": "Superboss rejected: https://github.com/x/y/pull/697 -- see review.json for issues"}],
        },
        "task-audit-fail-verdict": {
            "status": "blocked",
            "checkpoints": [{"note": "AUDIT: FAIL - real findings below"}],
        },
        "task-unrelated-block": {
            "status": "blocked",
            "checkpoints": [{"note": "credit accountant rejected auto-fix attempt 1, no further metered spend"}],
        },
        "task-in-progress-with-fail-text": {
            # even if the note text matched, a non-blocked task must never be flagged
            "status": "in_progress",
            "checkpoints": [{"note": "Superboss rejected an earlier attempt, now retrying"}],
        },
        "task-no-checkpoints": {"status": "blocked"},
    }
    found = dt._find_fresh_audit_fail_tasks(tasks)
    found_ids = {f["task_id"] for f in found}

    check("real 'Superboss rejected' note IS matched",
          "task-real-rejection" in found_ids)
    check("real 'AUDIT: FAIL' note IS matched",
          "task-audit-fail-verdict" in found_ids)
    check("unrelated blocked-task note (credit accountant) is NOT matched",
          "task-unrelated-block" not in found_ids)
    check("non-blocked status is never flagged even if note text matches",
          "task-in-progress-with-fail-text" not in found_ids)
    check("blocked task with no checkpoints at all is safely skipped, not a crash",
          "task-no-checkpoints" not in found_ids)
    check("exactly 2 real matches found, no over/under-matching", len(found_ids) == 2)


# ---------------------------------------------------------------------------
# _capture_tmux_pending_input()
# ---------------------------------------------------------------------------

def test_capture_tmux_pending_input_fails_closed_on_missing_session():
    dt = load_dispatch_tick()
    result = dt._capture_tmux_pending_input(session="a-session-name-that-genuinely-does-not-exist-12345")
    check("nonexistent tmux session -> None, never a fabricated finding", result is None)


def test_capture_tmux_pending_input_parses_real_prompt_glyph():
    dt = load_dispatch_tick()

    class FakeCompleted:
        def __init__(self, returncode, stdout):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if cmd[1] == "has-session":
            return FakeCompleted(0, "")
        if cmd[1] == "capture-pane":
            return FakeCompleted(0, "some earlier output\n❯ Approve refreshing the thing, do option C\n")
        raise AssertionError(f"unexpected command: {cmd}")

    import subprocess as _subprocess
    real_run = _subprocess.run
    _subprocess.run = fake_run
    try:
        result = dt._capture_tmux_pending_input(session="claude")
    finally:
        _subprocess.run = real_run

    check("real prompt-line text after the ❯ glyph is extracted correctly",
          result == "Approve refreshing the thing, do option C")
    check("both has-session and capture-pane were real, distinct calls",
          len(calls) == 2 and calls[0][1] == "has-session" and calls[1][1] == "capture-pane")


def test_capture_tmux_pending_input_empty_prompt_returns_none():
    dt = load_dispatch_tick()

    class FakeCompleted:
        def __init__(self, returncode, stdout):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def fake_run(cmd, **kw):
        if cmd[1] == "has-session":
            return FakeCompleted(0, "")
        return FakeCompleted(0, "❯ \n")  # empty prompt, nothing typed

    import subprocess as _subprocess
    real_run = _subprocess.run
    _subprocess.run = fake_run
    try:
        result = dt._capture_tmux_pending_input(session="claude")
    finally:
        _subprocess.run = real_run

    check("empty prompt line (nothing actually typed) -> None, not a false positive",
          result is None)


# ---------------------------------------------------------------------------
# should_triage_pm() -- the real pre-filter
# ---------------------------------------------------------------------------

def test_should_triage_pm_nothing_notable_skips_entirely():
    dt = load_dispatch_tick()
    tasks = {
        "task-fine": {"status": "in_progress"},
        "task-done": {"status": "completed"},
    }
    # Force the tmux check to find nothing, deterministically, regardless of
    # this test environment's own real tmux state.
    orig = dt._capture_tmux_pending_input
    dt._capture_tmux_pending_input = lambda *a, **kw: None
    try:
        should_invoke, reasons, evidence = dt.should_triage_pm(tasks, [], NOW)
    finally:
        dt._capture_tmux_pending_input = orig

    check("nothing notable -> should_invoke is False", should_invoke is False)
    check("nothing notable -> zero reasons", reasons == [])
    check("nothing notable -> empty evidence dict", evidence == {})


def test_should_triage_pm_triggers_on_stuck_tasks():
    dt = load_dispatch_tick()
    stuck = [{"task_id": "task-x", "blocked_minutes": 90.0, "last_note": "n"}]
    orig = dt._capture_tmux_pending_input
    dt._capture_tmux_pending_input = lambda *a, **kw: None
    try:
        should_invoke, reasons, evidence = dt.should_triage_pm({}, stuck, NOW)
    finally:
        dt._capture_tmux_pending_input = orig

    check("stuck tasks present -> should_invoke True", should_invoke is True)
    check("reason cites the real stuck-task count", "1" in reasons[0])
    check("evidence carries the real stuck_tasks list verbatim",
          evidence.get("stuck_tasks") == stuck)


def test_should_triage_pm_triggers_on_fresh_audit_fail_even_without_stuck_tasks():
    dt = load_dispatch_tick()
    tasks = {
        "task-just-rejected": {
            "status": "blocked",
            "checkpoints": [{"note": "Superboss rejected: https://x/y/pull/1 -- see review.json"}],
        },
    }
    orig = dt._capture_tmux_pending_input
    dt._capture_tmux_pending_input = lambda *a, **kw: None
    try:
        # empty stuck_tasks -- this task hasn't crossed the minutes threshold
        # yet, but a fresh audit rejection must still trigger immediately.
        should_invoke, reasons, evidence = dt.should_triage_pm(tasks, [], NOW)
    finally:
        dt._capture_tmux_pending_input = orig

    check("fresh audit-fail alone (no stuck_tasks) still triggers", should_invoke is True)
    check("evidence carries the real fresh_audit_fail_tasks list",
          len(evidence.get("fresh_audit_fail_tasks", [])) == 1)


def test_should_triage_pm_triggers_on_real_tmux_pending_input():
    dt = load_dispatch_tick()
    orig = dt._capture_tmux_pending_input
    dt._capture_tmux_pending_input = lambda *a, **kw: "Approve X, do Y"
    try:
        should_invoke, reasons, evidence = dt.should_triage_pm({}, [], NOW)
    finally:
        dt._capture_tmux_pending_input = orig

    check("real pending tmux input alone triggers", should_invoke is True)
    check("evidence carries the real captured text",
          evidence.get("tmux_pending_input") == "Approve X, do Y")


def test_should_triage_pm_multiple_reasons_all_included():
    dt = load_dispatch_tick()
    stuck = [{"task_id": "t1", "blocked_minutes": 45.0, "last_note": "n"}]
    orig = dt._capture_tmux_pending_input
    dt._capture_tmux_pending_input = lambda *a, **kw: "pending text"
    try:
        should_invoke, reasons, evidence = dt.should_triage_pm({}, stuck, NOW)
    finally:
        dt._capture_tmux_pending_input = orig

    check("multiple simultaneous real reasons all surface, not just the first",
          len(reasons) == 2 and "stuck_tasks" in evidence and "tmux_pending_input" in evidence)


# ---------------------------------------------------------------------------
# _invoke_triage_claude() -- real invocation shape, stubbed subprocess
# ---------------------------------------------------------------------------

def test_invoke_triage_claude_builds_correctly_scoped_command():
    dt = load_dispatch_tick()
    captured_cmd = {}

    class FakeCompleted:
        returncode = 0
        stdout = json.dumps({"result": "NO -- nothing here needs attention, just a routine tick."})
        stderr = ""

    def fake_run(cmd, **kw):
        captured_cmd["cmd"] = cmd
        captured_cmd["kwargs"] = kw
        return FakeCompleted()

    judgment = dt._invoke_triage_claude(
        reasons=["1 task stuck past 30min"],
        evidence={"stuck_tasks": [{"task_id": "t1"}]},
        run_fn=fake_run,
    )

    cmd = captured_cmd["cmd"]
    check("real judgment text correctly parsed from stubbed JSON response",
          judgment == "NO -- nothing here needs attention, just a routine tick.")
    check("invocation never passes --dangerously-skip-permissions",
          "--dangerously-skip-permissions" not in cmd)
    check("invocation never passes --continue (fresh/stateless every call)",
          "--continue" not in cmd and "-c" not in cmd)
    check("invocation explicitly denies all tool access (--allowedTools \"\")",
          "--allowedTools" in cmd and cmd[cmd.index("--allowedTools") + 1] == "")
    check("invocation carries a real, bounded --max-budget-usd cap",
          "--max-budget-usd" in cmd)
    check("invocation requests --output-format json for reliable parsing",
          "--output-format" in cmd and cmd[cmd.index("--output-format") + 1] == "json")
    check("the real gathered evidence (not a summary/guess) is embedded in the prompt",
          "1 task stuck past 30min" in cmd[2] and "stuck_tasks" in cmd[2])
    check("the prompt explicitly forbids deciding/dispatching/fixing, only yes/no+reason",
          "Do NOT decide" in cmd[2] and "do NOT propose a fix" in cmd[2])


def test_invoke_triage_claude_reports_real_errors_not_silently():
    dt = load_dispatch_tick()

    def failing_run(cmd, **kw):
        class R:
            returncode = 1
            stdout = ""
            stderr = "real auth error"
        return R()

    judgment = dt._invoke_triage_claude(reasons=["x"], evidence={}, run_fn=failing_run)
    check("a real non-zero exit is reported as a real, visible error, never silently swallowed",
          judgment.startswith("INVOCATION_ERROR:") and "real auth error" in judgment)


# ---------------------------------------------------------------------------
# append_pm_triage_alert() -- real, append-only file semantics
# ---------------------------------------------------------------------------

def test_append_pm_triage_alert_is_real_append_not_overwrite():
    dt = load_dispatch_tick()
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "PM_TRIAGE_ALERTS.md")
        dt.append_pm_triage_alert(path, NOW, ["reason one"], {"e": 1}, "YES -- real reason one.")
        later = NOW + datetime.timedelta(minutes=10)
        dt.append_pm_triage_alert(path, later, ["reason two"], {"e": 2}, "NO -- real reason two.")

        with open(path) as f:
            content = f.read()

        check("first entry's real reason text is present", "reason one" in content)
        check("second entry's real reason text is ALSO present (real append, not overwrite)",
              "reason two" in content)
        check("both real timestamps appear in the file",
              NOW.isoformat() in content and later.isoformat() in content)
        check("entries appear in real chronological append order",
              content.index("reason one") < content.index("reason two"))


# ---------------------------------------------------------------------------
# pm_triage_tick() -- end to end, the two cases the PM decision explicitly asked for
# ---------------------------------------------------------------------------

def test_pm_triage_tick_skips_invocation_when_nothing_notable():
    dt = load_dispatch_tick()
    invoke_calls = []

    def stub_invoke(reasons, evidence, run_fn=None):
        invoke_calls.append((reasons, evidence))
        return "should never be called"

    orig = dt._capture_tmux_pending_input
    dt._capture_tmux_pending_input = lambda *a, **kw: None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            alert_path = os.path.join(tmp, "PM_TRIAGE_ALERTS.md")
            real_path = dt.PM_TRIAGE_ALERTS_PATH
            dt.PM_TRIAGE_ALERTS_PATH = alert_path
            try:
                result = dt.pm_triage_tick({"t": {"status": "in_progress"}}, [], NOW, invoke_fn=stub_invoke)
            finally:
                dt.PM_TRIAGE_ALERTS_PATH = real_path

            check("real cost invocation is NEVER called when the pre-filter finds nothing",
                  len(invoke_calls) == 0)
            check("result correctly reports invoked=False", result["invoked"] is False)
            check("no alert file is created at all when nothing was notable",
                  not os.path.exists(alert_path))
    finally:
        dt._capture_tmux_pending_input = orig


def test_pm_triage_tick_invokes_and_writes_real_alert_when_triggered():
    dt = load_dispatch_tick()
    invoke_calls = []

    def stub_invoke(reasons, evidence, run_fn=None):
        invoke_calls.append((reasons, evidence))
        return "YES -- a task has been stuck for 90 minutes with no PM action, per the evidence."

    stuck = [{"task_id": "task-real", "blocked_minutes": 90.0, "last_note": "real evidence note"}]
    orig = dt._capture_tmux_pending_input
    dt._capture_tmux_pending_input = lambda *a, **kw: None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            alert_path = os.path.join(tmp, "PM_TRIAGE_ALERTS.md")
            real_path = dt.PM_TRIAGE_ALERTS_PATH
            dt.PM_TRIAGE_ALERTS_PATH = alert_path
            try:
                result = dt.pm_triage_tick({}, stuck, NOW, invoke_fn=stub_invoke)
            finally:
                dt.PM_TRIAGE_ALERTS_PATH = real_path

            check("invocation IS called exactly once when the pre-filter trips",
                  len(invoke_calls) == 1)
            check("result correctly reports invoked=True with the real judgment",
                  result["invoked"] is True
                  and "90 minutes" in result["judgment"])
            check("a real alert file was actually written to disk", os.path.isfile(alert_path))
            with open(alert_path) as f:
                content = f.read()
            check("the real alert entry contains the real judgment text",
                  "90 minutes" in content)
            check("the real alert entry contains the real evidence (task id), not a summary",
                  "task-real" in content)
    finally:
        dt._capture_tmux_pending_input = orig


if __name__ == "__main__":
    test_find_fresh_audit_fail_tasks()
    test_capture_tmux_pending_input_fails_closed_on_missing_session()
    test_capture_tmux_pending_input_parses_real_prompt_glyph()
    test_capture_tmux_pending_input_empty_prompt_returns_none()
    test_should_triage_pm_nothing_notable_skips_entirely()
    test_should_triage_pm_triggers_on_stuck_tasks()
    test_should_triage_pm_triggers_on_fresh_audit_fail_even_without_stuck_tasks()
    test_should_triage_pm_triggers_on_real_tmux_pending_input()
    test_should_triage_pm_multiple_reasons_all_included()
    test_invoke_triage_claude_builds_correctly_scoped_command()
    test_invoke_triage_claude_reports_real_errors_not_silently()
    test_append_pm_triage_alert_is_real_append_not_overwrite()
    test_pm_triage_tick_skips_invocation_when_nothing_notable()
    test_pm_triage_tick_invokes_and_writes_real_alert_when_triggered()

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S): {failures}")
        sys.exit(1)
    print("All assertions passed.")
    sys.exit(0)
