#!/usr/bin/env python3
"""Real tests for dispatch-tick.py's run_stale_running_workers_reconciliation()
(UMR-20260813-090037-9a34, addendum to UMR-20260806-171945-5767): the new periodic
wiring for reconcile_stale_running_workers.py's own sweep() -- real fix for PR #249's
own AUDIT:FAIL finding "(c) reconcile_stale_running_workers.py ... is a ONE-SHOT and is
not wired to run periodically", confirmed (see this task's own PROGRESS.md) to still be
a live gap even once PR #290's status-remediation-tick.py periodic reconciliation lands
(that one is scoped to source_trigger='owner_dispatch_gateway' only; this script's own
_fetch_affected_rows() has no such filter).

Never touches the real network/DB/systemd -- the loaded reconciler module's own sweep()
is monkeypatched at the module-object seam, same hermetic convention
tests/test_status_remediation_owner_dispatch_reconciliation.py already uses for the
analogous PR #290 wiring.
"""
import importlib.util
import os
import sys
import types

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)  # dispatch-tick.py does plain `import dispatch_core` / `import resource_governor`


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "dispatch_tick_stale_running_test", os.path.join(SCRIPTS_DIR, "dispatch-tick.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_calls_sweep_with_execute_true(monkeypatch):
    """dispatch-tick.py has no dry-run mode anywhere else in this script -- every other
    tick function performs real actions unconditionally each run -- so this call must
    always pass execute=True, never a silent dry-run default that would leave the real
    gap this UMR exists to close still open."""
    mod = _load_module()
    calls = []
    fake = types.SimpleNamespace(sweep=lambda execute: calls.append(execute) or {
        "execute": execute, "examined": 3, "counts": {"failed": 1, "would_requeue": 2}, "rows": [],
    })
    monkeypatch.setattr(mod, "_load_reconcile_stale_running_workers", lambda: fake)

    result = mod.run_stale_running_workers_reconciliation()

    assert calls == [True]
    assert result == {"ok": True, "examined": 3, "counts": {"failed": 1, "would_requeue": 2}}
    print("PASS: test_calls_sweep_with_execute_true")


def test_fails_open_on_real_exception(monkeypatch):
    """Same fail-open convention run_owner_dispatch_reconciliation() (PR #290) already
    uses -- a real failure loading/running the reconciler must never raise out of this
    function, so it can never block the rest of a real tick's own dispatch/resume work."""
    mod = _load_module()

    def _raise():
        raise RuntimeError("simulated real failure")

    monkeypatch.setattr(mod, "_load_reconcile_stale_running_workers", _raise)
    result = mod.run_stale_running_workers_reconciliation()
    assert result == {"ok": False, "error": "simulated real failure"}
    print("PASS: test_fails_open_on_real_exception")


if __name__ == "__main__":
    import inspect

    class _MP:
        def setattr(self, obj, name, value):
            setattr(obj, name, value)

    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            if "monkeypatch" in inspect.signature(fn).parameters:
                fn(_MP())
            else:
                fn()
    print("ALL TESTS PASSED")
