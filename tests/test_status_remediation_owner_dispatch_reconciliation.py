#!/usr/bin/env python3
"""Real tests for status-remediation-tick.py's run_owner_dispatch_reconciliation()
(UMR-20260813-065157-ba95, "close the success half of the umr_tasks write-back
gap"): the new periodic wiring for reconcile_owner_dispatch_status.py. Never
touches the real network/DB -- the loaded reconciler module's own load_rows/
classify_row/apply_correction/_sbr are monkeypatched at the module-object
seam, same hermetic convention test_reconcile_owner_dispatch_status.py
already uses for that module directly.
"""
import importlib.util
import os
import sys
import types

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)  # status-remediation-tick.py does a plain `import dispatch_core`


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "status_remediation_tick_test",
        os.path.join(SCRIPTS_DIR, "status-remediation-tick.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeConn:
    def close(self):
        pass

    def commit(self):
        pass


class _FakeWriteLock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_reconciler_module(rows, buckets_by_umr_id):
    """A stand-in for the real reconcile_owner_dispatch_status module, with
    just enough real shape (load_rows/classify_row/apply_correction/_sbr/
    datetime/timezone) for run_owner_dispatch_reconciliation() to drive."""
    import datetime as _dt

    fake = types.SimpleNamespace()
    fake.datetime = _dt.datetime
    fake.timezone = _dt.timezone
    fake.load_rows = lambda conn: rows
    fake.classify_row = lambda row, now, pr_cache: {
        "umr_id": row["umr_id"], "bucket": buckets_by_umr_id[row["umr_id"]],
        "new_status": "completed",
    }
    fake.apply_correction_calls = []

    def _apply_correction(conn, ev, dry_run):
        fake.apply_correction_calls.append((ev, dry_run))

    fake.apply_correction = _apply_correction

    sbr = types.SimpleNamespace()
    sbr._connect = lambda: _FakeConn()
    sbr._write_lock = lambda: _FakeWriteLock()
    fake._sbr = sbr
    return fake


def test_dry_run_classifies_but_never_applies(monkeypatch):
    mod = _load_module()
    rows = [{"umr_id": "UMR-a"}, {"umr_id": "UMR-b"}]
    fake = _fake_reconciler_module(rows, {"UMR-a": "STALE_LABEL_TERMINAL", "UMR-b": "NEEDS_AI_JUDGMENT"})
    monkeypatch.setattr(mod, "_load_reconcile_owner_dispatch_status", lambda: fake)

    result = mod.run_owner_dispatch_reconciliation(apply_=False)

    assert result["ok"] is True
    assert result["examined"] == 2
    assert result["counts"] == {"STALE_LABEL_TERMINAL": 1, "NEEDS_AI_JUDGMENT": 1}
    assert result["would_correct_umr_ids"] == ["UMR-a"]
    assert result["corrected_umr_ids"] == []
    assert fake.apply_correction_calls == [], "dry run (apply_=False) must never call apply_correction()"
    print("PASS: test_dry_run_classifies_but_never_applies")


def test_apply_mode_corrects_only_stale_label_terminal_rows(monkeypatch):
    mod = _load_module()
    rows = [{"umr_id": "UMR-a"}, {"umr_id": "UMR-b"}, {"umr_id": "UMR-c"}]
    fake = _fake_reconciler_module(rows, {
        "UMR-a": "STALE_LABEL_TERMINAL", "UMR-b": "NEEDS_AI_JUDGMENT", "UMR-c": "REAL_RUNNING",
    })
    monkeypatch.setattr(mod, "_load_reconcile_owner_dispatch_status", lambda: fake)

    result = mod.run_owner_dispatch_reconciliation(apply_=True)

    assert result["ok"] is True
    assert result["corrected_umr_ids"] == ["UMR-a"]
    assert result["would_correct_umr_ids"] == []
    assert len(fake.apply_correction_calls) == 1
    assert fake.apply_correction_calls[0][0]["umr_id"] == "UMR-a"
    assert fake.apply_correction_calls[0][1] is False, "apply mode must call apply_correction with dry_run=False"
    print("PASS: test_apply_mode_corrects_only_stale_label_terminal_rows")


def test_fails_open_on_real_exception(monkeypatch):
    """A broken reconciler import/DB connect must never raise out of this
    function -- same fail-open convention refresh_wiring_registry() already
    uses, so a real failure here can never block the rest of this tick's own
    scan/remediate work."""
    mod = _load_module()

    def _raise():
        raise RuntimeError("simulated real failure")

    monkeypatch.setattr(mod, "_load_reconcile_owner_dispatch_status", _raise)
    result = mod.run_owner_dispatch_reconciliation(apply_=True)
    assert result["ok"] is False
    assert "simulated real failure" in result["error"]
    print("PASS: test_fails_open_on_real_exception")


if __name__ == "__main__":
    import inspect
    import types as _types

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
