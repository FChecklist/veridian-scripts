#!/usr/bin/env python3
"""Real tests for reconcile_owner_dispatch_status.py (UMR-20260806-075726-babc,
child of UMR-20260806-071025-1d28). Exercises classify_row()'s real decision
tree in isolation -- systemctl/gh are monkeypatched at the module's own
`_run` seam (same subprocess-capture shape the real functions use), never
the real network/systemd, so these tests are fast and hermetic. The one
real-DB test (test_load_rows_real_db_smoke) only reads (load_rows() issues
a plain SELECT) and asserts on shape, not on specific live row counts,
which change constantly on this project.
"""
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "reconcile_owner_dispatch_status_test",
        os.path.join(SCRIPTS_DIR, "reconcile_owner_dispatch_status.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _row(umr_id="UMR-20260101-000000-aaaa", unit_name="veridian-worker@task-x.service"):
    return {
        "umr_id": umr_id,
        "task_identity": "owner-task-x",
        "status": "running",
        "ts_submitted": "2026-08-05T00:00:00+00:00",
        "unit_name": unit_name,
    }


def test_merged_pr_becomes_completed(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "_task_dir_from_unit", lambda u: "task-x")
    monkeypatch.setattr(mod, "_read_task_yaml", lambda t: {
        "status": "blocked", "repo": "veridian-scripts", "branch": "b1",
        "last_checkpoint_at": "2026-08-05T00:10:00+00:00",
    })
    monkeypatch.setattr(mod, "_systemd_is_active", lambda u: "inactive")
    monkeypatch.setattr(mod, "_fetch_prs", lambda repo, cache: [
        {"number": 1, "state": "MERGED", "headRefName": "b1", "headRefOid": "deadbeef"}
    ])
    monkeypatch.setattr(mod, "_audit_verdict", lambda repo, n: {"verdict": None, "current_head_sha": "deadbeef"})

    ev = mod.classify_row(_row(), datetime.now(timezone.utc), {})
    assert ev["bucket"] == "STALE_LABEL_TERMINAL"
    assert ev["new_status"] == "completed"
    print("PASS: test_merged_pr_becomes_completed")


def test_closed_pr_becomes_failed(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "_task_dir_from_unit", lambda u: "task-x")
    monkeypatch.setattr(mod, "_read_task_yaml", lambda t: {
        "status": "blocked", "repo": "veridian-scripts", "branch": "b1",
        "last_checkpoint_at": "2026-08-05T00:10:00+00:00",
    })
    monkeypatch.setattr(mod, "_systemd_is_active", lambda u: "inactive")
    monkeypatch.setattr(mod, "_fetch_prs", lambda repo, cache: [
        {"number": 1, "state": "CLOSED", "headRefName": "b1", "headRefOid": "deadbeef"}
    ])
    monkeypatch.setattr(mod, "_audit_verdict", lambda repo, n: {"verdict": None, "current_head_sha": "deadbeef"})

    ev = mod.classify_row(_row(), datetime.now(timezone.utc), {})
    assert ev["bucket"] == "STALE_LABEL_TERMINAL"
    assert ev["new_status"] == "failed"
    print("PASS: test_closed_pr_becomes_failed")


def test_no_pr_ever_becomes_killed(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "_task_dir_from_unit", lambda u: "task-x")
    monkeypatch.setattr(mod, "_read_task_yaml", lambda t: {
        "status": "blocked", "repo": None, "branch": None,
        "last_checkpoint_at": "2026-08-05T00:10:00+00:00",
    })
    monkeypatch.setattr(mod, "_systemd_is_active", lambda u: "inactive")

    ev = mod.classify_row(_row(), datetime.now(timezone.utc), {})
    assert ev["bucket"] == "STALE_LABEL_TERMINAL"
    assert ev["new_status"] == "killed"
    print("PASS: test_no_pr_ever_becomes_killed")


def test_open_pr_never_auto_terminalized_even_with_fresh_audit_pass(monkeypatch):
    """Regression test for the real correctness bug caught during this
    script's own build: an OPEN PR carrying a real, current-head-SHA
    AUDIT:PASS must NEVER be silently relabeled 'failed' just because
    task.yaml says 'blocked' and systemd is inactive -- real project
    precedent (batch3_summary_draft.txt, UMR-20260805-034917-33a9) is to
    re-adopt and continue such a row, not fail it."""
    mod = _load_module()
    monkeypatch.setattr(mod, "_task_dir_from_unit", lambda u: "task-x")
    monkeypatch.setattr(mod, "_read_task_yaml", lambda t: {
        "status": "blocked", "repo": "veridian-scripts", "branch": "b1",
        "last_checkpoint_at": "2026-08-05T00:10:00+00:00",
    })
    monkeypatch.setattr(mod, "_systemd_is_active", lambda u: "inactive")
    monkeypatch.setattr(mod, "_fetch_prs", lambda repo, cache: [
        {"number": 1, "state": "OPEN", "headRefName": "b1", "headRefOid": "deadbeef"}
    ])
    monkeypatch.setattr(mod, "_audit_verdict", lambda repo, n: {
        "verdict": {"verdict": "AUDIT: PASS", "createdAt": "2026-08-05T00:05:00Z"},
        "current_head_sha": "deadbeef",
    })

    ev = mod.classify_row(_row(), datetime.now(timezone.utc), {})
    assert ev["bucket"] == "NEEDS_AI_JUDGMENT", ev
    assert "new_status" not in ev
    print("PASS: test_open_pr_never_auto_terminalized_even_with_fresh_audit_pass")


def test_real_active_unit_is_real_running(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "_task_dir_from_unit", lambda u: "task-x")
    monkeypatch.setattr(mod, "_read_task_yaml", lambda t: {
        "status": "in_progress", "repo": "veridian-scripts", "branch": "b1",
        "last_checkpoint_at": datetime.now(timezone.utc).isoformat(),
    })
    monkeypatch.setattr(mod, "_systemd_is_active", lambda u: "active")

    ev = mod.classify_row(_row(), datetime.now(timezone.utc), {})
    assert ev["bucket"] == "REAL_RUNNING"
    print("PASS: test_real_active_unit_is_real_running")


def test_active_unit_no_pr_stale_checkpoint_needs_judgment(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "_task_dir_from_unit", lambda u: "task-x")
    stale_checkpoint = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    monkeypatch.setattr(mod, "_read_task_yaml", lambda t: {
        "status": "in_progress", "repo": None, "branch": None,
        "last_checkpoint_at": stale_checkpoint,
    })
    monkeypatch.setattr(mod, "_systemd_is_active", lambda u: "active")

    ev = mod.classify_row(_row(), datetime.now(timezone.utc), {})
    assert ev["bucket"] == "NEEDS_AI_JUDGMENT", ev
    print("PASS: test_active_unit_no_pr_stale_checkpoint_needs_judgment")


def test_unresolvable_unit_name_needs_judgment(monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "_task_dir_from_unit", lambda u: None)
    monkeypatch.setattr(mod, "_systemd_is_active", lambda u: "inactive")

    ev = mod.classify_row(_row(), datetime.now(timezone.utc), {})
    assert ev["bucket"] == "NEEDS_AI_JUDGMENT"
    print("PASS: test_unresolvable_unit_name_needs_judgment")


class _FakeConnReadOnlySelect:
    """Real conn stub used by the write-path tests below: permits exactly
    the one real SELECT apply_correction() needs to read a row's existing
    metadata_json (the real-project read-merge-write convention,
    annotate_knowledge()'s own pattern), but raises if anything containing
    a real SQL write verb (UPDATE/INSERT/DELETE) is ever executed directly
    -- the actual write must always go through update_umr_task() only."""

    def __init__(self, existing_metadata_json):
        self._existing_metadata_json = existing_metadata_json

    def execute(self, sql, params=()):
        upper = sql.strip().upper()
        if upper.startswith("SELECT"):
            return _FakeCursor({"metadata_json": self._existing_metadata_json})
        raise AssertionError(f"apply_correction() must never write via conn.execute() directly, got: {sql!r}")


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


def test_apply_correction_writes_only_via_update_umr_task(monkeypatch):
    """Confirms apply_correction() never issues a raw SQL UPDATE -- it must
    go through superboss_register.update_umr_task() only, per the real
    Owner directive's own hard requirement. (The one real conn.execute()
    call it does make is a read-only SELECT to fetch existing metadata for
    the merge below -- see test_apply_correction_preserves_prior_metadata.)"""
    mod = _load_module()
    calls = []

    def fake_update(conn, umr_id, **fields):
        calls.append((umr_id, fields))

    monkeypatch.setattr(mod._sbr, "update_umr_task", fake_update)
    ev = {"umr_id": "UMR-x", "new_status": "killed", "db_status": "running"}
    mod.apply_correction(_FakeConnReadOnlySelect(None), ev, dry_run=False)
    assert len(calls) == 1
    assert calls[0][0] == "UMR-x"
    assert calls[0][1]["status"] == "killed"
    assert "metadata" in calls[0][1]
    assert calls[0][1]["metadata"]["reconcile_owner_dispatch_status"]["reconciled_by"] == "reconcile_owner_dispatch_status.py"
    print("PASS: test_apply_correction_writes_only_via_update_umr_task")


def test_apply_correction_preserves_prior_metadata(monkeypatch):
    """Regression test for the real data-loss bug caught by the real
    AUDIT:FAIL on this script's own PR #147: update_umr_task()'s own
    metadata=... kwarg is a blind column replace (confirmed by reading its
    implementation), so apply_correction() must read the row's EXISTING
    metadata_json first and merge into it -- never pass only the new
    reconciliation fields, which would silently destroy real pre-existing
    data such as a correlation_id set by resource_governor.py's submit()
    at original dispatch time."""
    import json as _json
    mod = _load_module()
    calls = []

    def fake_update(conn, umr_id, **fields):
        calls.append((umr_id, fields))

    monkeypatch.setattr(mod._sbr, "update_umr_task", fake_update)
    prior_metadata = {"correlation_id": "real-corr-id-12345", "some_other_real_field": "keep-me"}
    ev = {"umr_id": "UMR-x", "new_status": "completed", "db_status": "running", "pr_number": 999}
    mod.apply_correction(
        _FakeConnReadOnlySelect(_json.dumps(prior_metadata)), ev, dry_run=False
    )
    assert len(calls) == 1
    written_metadata = calls[0][1]["metadata"]
    assert written_metadata["correlation_id"] == "real-corr-id-12345", "prior real metadata must survive"
    assert written_metadata["some_other_real_field"] == "keep-me", "prior real metadata must survive"
    assert "reconcile_owner_dispatch_status" in written_metadata
    print("PASS: test_apply_correction_preserves_prior_metadata")


def test_apply_correction_dry_run_makes_no_calls(monkeypatch):
    mod = _load_module()
    calls = []
    monkeypatch.setattr(mod._sbr, "update_umr_task", lambda *a, **k: calls.append((a, k)))
    ev = {"umr_id": "UMR-x", "new_status": "killed", "db_status": "running"}
    mod.apply_correction(object(), ev, dry_run=True)
    assert calls == []
    print("PASS: test_apply_correction_dry_run_makes_no_calls")


def test_audit_verdict_detects_fresh_pass(monkeypatch):
    """Regression test for the real 'claimed but not implemented' gap the
    real AUDIT:FAIL on PR #147 caught: _audit_verdict() must actually
    compare the audit comment's createdAt against the head commit's own
    committedDate, not just fetch both and never compare them."""
    import json as _json
    mod = _load_module()
    payload = {
        "headRefOid": "deadbeef",
        "comments": [{"body": "AUDIT: PASS\nfoo", "createdAt": "2026-08-06T09:00:00Z"}],
        "commits": [{"committedDate": "2026-08-06T08:00:00Z", "oid": "deadbeef"}],
    }
    monkeypatch.setattr(mod, "_run", lambda cmd, timeout=30: (0, _json.dumps(payload), ""))
    result = mod._audit_verdict("veridian-scripts", 147)
    assert result["stale"] is False, result
    print("PASS: test_audit_verdict_detects_fresh_pass")


def test_audit_verdict_detects_stale_pass(monkeypatch):
    """Same as above, but the audit comment predates the latest commit --
    a new commit landed after review, so the PASS no longer covers what's
    actually on the branch now."""
    import json as _json
    mod = _load_module()
    payload = {
        "headRefOid": "deadbeef",
        "comments": [{"body": "AUDIT: PASS\nfoo", "createdAt": "2026-08-06T07:00:00Z"}],
        "commits": [{"committedDate": "2026-08-06T08:00:00Z", "oid": "deadbeef"}],
    }
    monkeypatch.setattr(mod, "_run", lambda cmd, timeout=30: (0, _json.dumps(payload), ""))
    result = mod._audit_verdict("veridian-scripts", 147)
    assert result["stale"] is True, result
    print("PASS: test_audit_verdict_detects_stale_pass")


def test_load_rows_real_db_smoke():
    """Real, read-only smoke test against the live DB: load_rows() must
    return a list of dicts each carrying the columns the classifier needs,
    and every row's source_trigger-implied filter (status='running') must
    hold. Doesn't assert on a specific count -- this project's live
    umr_tasks table changes every few seconds."""
    mod = _load_module()
    conn = mod._sbr._connect()
    try:
        rows = mod.load_rows(conn)
    finally:
        conn.close()
    assert isinstance(rows, list)
    for r in rows:
        assert set(["umr_id", "task_identity", "status", "ts_submitted", "unit_name"]) <= set(r.keys())
        assert r["status"] == "running"
    print(f"PASS: test_load_rows_real_db_smoke ({len(rows)} real live rows)")


if __name__ == "__main__":
    # Allow `python3 tests/test_reconcile_owner_dispatch_status.py` direct
    # execution (same convention as this repo's other test files), not just
    # pytest collection.
    import types
    mp = types.SimpleNamespace()

    class _MP:
        def setattr(self, obj, name, value):
            setattr(obj, name, value)

    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            import inspect
            if "monkeypatch" in inspect.signature(fn).parameters:
                fn(_MP())
            else:
                fn()
    print("ALL TESTS PASSED")
