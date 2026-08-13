#!/usr/bin/env python3
"""Real tests for dispatch-tick.py's owner_dispatch_reconciliation_tick()
(UMR-20260813-103211-e8a1, addendum to UMR-20260813-065157-ba95: "make the
merged write-back reconciler actually run").

Real gap this closes: status-remediation-tick.py's own
run_owner_dispatch_reconciliation() (added under UMR-20260813-065157-ba95)
was real, tested, and audited -- but status-remediation-tick.py itself has
no live caller. Its own unit (veridian-cron-status-remediation-tick.timer)
is disabled per the Owner's real 2026-08-07 standing order
(INS-20260807-042700-a247), which explicitly named
veridian-cron-dispatch-tick.timer as one of the exactly 2 timers that must
stay enabled/active. So the reconciliation call is wired into dispatch-tick.py
(THIS script's own unit) instead of re-enabling status-remediation-tick's
timer, per the ~/.config/systemd/user/README.md STANDING RULE (new periodic
need that fits an existing unit's cadence goes in as a new step inside that
unit/script, not a new unit).

Never touches the real network/DB/filesystem -- _load_status_remediation_tick()
is monkeypatched at the seam so run_owner_dispatch_reconciliation() itself is
a bare fake, same hermetic convention
test_status_remediation_owner_dispatch_reconciliation.py already uses for the
function this one calls.
"""
import builtins
import importlib.util
import os
import sys
import types

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def _load_dispatch_tick():
    # dispatch-tick.py does plain top-level `import dispatch_core` /
    # `import resource_governor` -- pop any cached copies first so this load
    # doesn't silently reuse a previous test's module bound to a different
    # scratch DB (same convention test_resume_interrupted_workers_no_duplicate_row.py
    # already uses).
    sys.modules.pop("resource_governor", None)
    sys.modules.pop("dispatch_core", None)
    spec = importlib.util.spec_from_file_location(
        "dispatch_tick_reconciliation_test", os.path.join(SCRIPTS_DIR, "dispatch-tick.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_calls_run_owner_dispatch_reconciliation_with_apply_true(monkeypatch):
    """dispatch-tick.py has no --apply flag of its own (all its other
    dispatch actions are inherently real) -- this must always call the
    reconciler with apply_=True, never dry-run, so the currently-real
    STALE_LABEL_TERMINAL corrections actually get written on a live tick."""
    mod = _load_dispatch_tick()
    calls = []

    fake_srt = types.SimpleNamespace(
        run_owner_dispatch_reconciliation=lambda apply_: calls.append(apply_) or {
            "ok": True, "apply_mode": apply_, "examined": 3,
            "counts": {"STALE_LABEL_TERMINAL": 1, "REAL_RUNNING": 1, "NEEDS_AI_JUDGMENT": 1},
            "corrected_umr_ids": ["UMR-a"], "would_correct_umr_ids": [],
        })
    monkeypatch.setattr(mod, "_load_status_remediation_tick", lambda: fake_srt)

    result = mod.owner_dispatch_reconciliation_tick()

    assert calls == [True], "must call run_owner_dispatch_reconciliation(apply_=True)"
    assert result["ok"] is True
    assert result["corrected_umr_ids"] == ["UMR-a"]
    print("PASS: test_calls_run_owner_dispatch_reconciliation_with_apply_true")


def test_fails_open_on_real_exception(monkeypatch):
    """A broken status-remediation-tick.py import (or a real reconciler
    exception bubbling up through it) must never raise out of dispatch-tick's
    own tick -- same fail-open convention this script's other best-effort
    sub-ticks already use, so a broken reconciler can never block the real
    dispatch work (supervisor sweep / gap queue / module queue) this script
    exists to run."""
    mod = _load_dispatch_tick()

    def _raise():
        raise RuntimeError("simulated real failure")

    monkeypatch.setattr(mod, "_load_status_remediation_tick", _raise)
    result = mod.owner_dispatch_reconciliation_tick()
    assert result["ok"] is False
    assert "simulated real failure" in result["error"]
    print("PASS: test_fails_open_on_real_exception")


def test_main_includes_reconciliation_in_record_tick_and_json_output(monkeypatch):
    """Real wiring check, not just a unit-level call: main() must surface the
    reconciliation result in both dispatch_core.record_tick()'s extra dict
    and the final printed JSON, same as every other sub-tick main() already
    reports."""
    mod = _load_dispatch_tick()

    monkeypatch.setattr(mod.dispatch_core, "task_status_sync", lambda: [])
    monkeypatch.setattr(mod, "supervisor_sweep_tick", lambda tasks: {"started": []})
    monkeypatch.setattr(mod, "resume_interrupted_workers_tick", lambda tasks: {"resumed": []})
    monkeypatch.setattr(mod, "gap_queue_tick", lambda tasks: {"dispatched": []})
    monkeypatch.setattr(mod, "module_queue_tick", lambda tasks: {"dispatched": []})
    monkeypatch.setattr(mod, "find_stuck_tasks", lambda tasks, now: [])
    monkeypatch.setattr(mod, "find_stalled_running_tasks", lambda tasks, now: [])
    monkeypatch.setattr(mod, "write_stuck_tasks_heartbeat", lambda tasks, stuck, now, stalled_running_tasks=None: {})
    monkeypatch.setattr(mod, "pm_triage_tick", lambda tasks, stuck, now: {"invoked": False, "reasons": []})

    reconciliation_result = {
        "ok": True, "apply_mode": True, "examined": 1,
        "counts": {"STALE_LABEL_TERMINAL": 1}, "corrected_umr_ids": ["UMR-x"], "would_correct_umr_ids": [],
    }
    monkeypatch.setattr(mod, "owner_dispatch_reconciliation_tick", lambda: reconciliation_result)

    record_tick_calls = []
    monkeypatch.setattr(
        mod.dispatch_core, "record_tick",
        lambda name, status, dispatched_this_tick=0, extra=None: record_tick_calls.append((name, extra)))

    printed = []
    monkeypatch.setattr(builtins, "print", lambda *a, **k: printed.append(" ".join(str(x) for x in a)))
    monkeypatch.setattr(sys, "argv", ["dispatch-tick.py"])

    mod.main()

    assert len(record_tick_calls) == 1
    _, extra = record_tick_calls[0]
    assert extra["owner_dispatch_reconciliation_ok"] is True
    assert extra["owner_dispatch_reconciliation_corrected"] == ["UMR-x"]

    printed_json = "\n".join(printed)
    assert "owner_dispatch_reconciliation" in printed_json
    assert "UMR-x" in printed_json
    print("PASS: test_main_includes_reconciliation_in_record_tick_and_json_output")


if __name__ == "__main__":
    import inspect

    class _MP:
        def __init__(self):
            self._sets = []

        def setattr(self, obj, name, value):
            self._sets.append((obj, name))
            setattr(obj, name, value)

    for _name, _fn in list(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _mp = _MP()
            if "monkeypatch" in inspect.signature(_fn).parameters:
                _fn(_mp)
            else:
                _fn()
    print("ALL TESTS PASSED")
