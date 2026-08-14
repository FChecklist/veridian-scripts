#!/usr/bin/env python3
"""Real regression tests for the NULL-unit_name / shared-unit reconciliation blind spot
(task-20260813-235841-reconciler-blind-spot--4-rows-stuck-runn).

Real, live-confirmed bug: reconcile_stale_running_workers.py's own _fetch_affected_rows()
scoped its query to `unit_name LIKE 'veridian-worker@%'` only. A status='running' row with
NO unit_name at all (never assigned a per-task worker unit), or one bound to a real but
SHARED unit (e.g. veridian-governor-tick.service -- a periodic tick service that runs on
its own schedule for every task, not this row's own worker), was structurally invisible to
that query -- never fetched, never reconciled, stuck at running forever. Live-confirmed
against the real production DB: 4 rows exactly this shape, 5-7 days stale, none
corresponding to any real veridian-worker@* unit on the box.

Same two-layer convention as tests/test_reconcile_stale_running_workers.py (hermetic
decide_and_apply()-level tests with the systemd/task-dir seams monkeypatched, plus one
real end-to-end test against a real scratch SQLite DB and the real `mark-umr-terminal`
CLI subprocess -- proving the actual write, not just a mocked call shape)."""
import datetime
import importlib.util
import os
import sqlite3
import tempfile

import pytest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RECONCILE_SCRIPT = os.path.join(SCRIPTS_DIR, "reconcile_stale_running_workers.py")
SUPERBOSS_REGISTER_LIVE = "/opt/veridian/scripts/superboss-register.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "reconcile_stale_running_workers_no_unit_fallback_test", RECONCILE_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_sbr():
    spec = importlib.util.spec_from_file_location("sbr_for_no_unit_fallback_test", SUPERBOSS_REGISTER_LIVE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def scratch_db():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "scratch.sqlite")
        sbr2 = _load_sbr()
        sbr2.DB_PATH = path
        sbr2.init_db()
        yield path


def _row(umr_id="UMR-20260101-000000-nunt", unit_name=None, ts_dispatched=None, ts_submitted=None,
         last_heartbeat=None, outputs_json="{}"):
    return {
        "umr_id": umr_id, "unit_name": unit_name, "task_identity": "task-no-unit",
        "outputs_json": outputs_json, "status": "running",
        "ts_dispatched": ts_dispatched, "ts_submitted": ts_submitted, "last_heartbeat": last_heartbeat,
    }


def _iso(dt):
    return dt.isoformat()


def _days_ago(days):
    return _iso(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days))


def _seconds_ago(seconds):
    return _iso(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=seconds))


def test_is_per_task_worker_unit_classification():
    mod = _load_module()
    assert mod._is_per_task_worker_unit("veridian-worker@task-20260806-112042-c027.service") is True
    assert mod._is_per_task_worker_unit(None) is False
    assert mod._is_per_task_worker_unit("") is False
    # The real fourth stuck row's real unit_name -- a shared periodic tick service, not a
    # per-task worker instance.
    assert mod._is_per_task_worker_unit("veridian-governor-tick.service") is False
    assert mod._is_per_task_worker_unit("veridian-supervisor@task-x.service") is False


def test_null_unit_name_stale_row_reconciled_to_failed_never_requeued(monkeypatch):
    """Real regression case (3 of the 4 real stuck rows): NULL unit_name, no task
    directory anywhere, stale by ts_dispatched -- must resolve to a real terminal
    'failed', never silently re-queued back into an active status (that would just
    recreate the identical blind spot)."""
    mod = _load_module()
    row = _row(umr_id="UMR-NOUNIT-STALE", unit_name=None, ts_dispatched=_days_ago(7))
    monkeypatch.setattr(mod, "_task_dir_for_row", lambda r: (None, "no task directory found"))
    calls = {"systemctl": 0}
    monkeypatch.setattr(mod, "_unit_active_state", lambda u: calls.__setitem__("systemctl", calls["systemctl"] + 1) or "unknown")

    entry = mod.decide_and_apply(row, execute=False)

    assert entry["has_reliable_unit"] is False
    assert entry["liveness_mechanism"] == "timestamp_staleness_fallback"
    assert entry["decision"] == "would_mark_failed"
    assert entry["decision"] != "would_requeue"
    # NULL unit_name -- systemctl must never even be consulted.
    assert calls["systemctl"] == 0


def test_null_unit_name_fresh_row_left_alone(monkeypatch):
    """A freshly-dispatched row with NULL unit_name (well within
    NO_UNIT_STALENESS_TTL_SECONDS) must be skipped, not reconciled -- the timestamp
    fallback must not clobber genuinely fresh/in-flight rows that simply have not been
    assigned a unit_name yet."""
    mod = _load_module()
    row = _row(umr_id="UMR-NOUNIT-FRESH", unit_name=None, ts_dispatched=_seconds_ago(60))
    entry = mod.decide_and_apply(row, execute=False)
    assert entry["decision"] == "skipped_not_settled"


def test_null_unit_name_no_timestamp_at_all_left_alone():
    """A row with NULL unit_name AND no last_heartbeat/ts_dispatched/ts_submitted at
    all -- genuinely no evidence either way -- must never be assumed stale."""
    mod = _load_module()
    row = _row(umr_id="UMR-NOUNIT-NOTS", unit_name=None)
    entry = mod.decide_and_apply(row, execute=False)
    assert entry["decision"] == "skipped_not_settled"
    assert entry["staleness_reference_field"] is None


def test_shared_non_worker_unit_never_consulted_for_liveness(monkeypatch):
    """Real regression case (the 4th real stuck row): unit_name is a real, active,
    SHARED unit (veridian-governor-tick.service -- runs on its own schedule for every
    task, not this row's own worker). Its ActiveState must never be queried at all --
    an active shared unit must never make this row look alive -- and the row must still
    resolve to a real terminal status via the timestamp fallback once stale."""
    mod = _load_module()
    row = _row(umr_id="UMR-SHAREDUNIT-STALE", unit_name="veridian-governor-tick.service",
               ts_dispatched=_days_ago(6))
    monkeypatch.setattr(mod, "_task_dir_for_row", lambda r: (None, "no task directory found"))
    queried_units = []

    def fake_active_state(unit_name):
        queried_units.append(unit_name)
        return "active"  # deliberately "alive" -- if this were ever consulted, the row
        # would incorrectly resolve to skipped_not_settled below.

    monkeypatch.setattr(mod, "_unit_active_state", fake_active_state)

    entry = mod.decide_and_apply(row, execute=False)

    assert entry["has_reliable_unit"] is False
    assert entry["liveness_mechanism"] == "timestamp_staleness_fallback"
    assert entry["decision"] == "would_mark_failed"
    assert queried_units == []  # the shared unit's ActiveState was never queried


def test_per_task_worker_unit_unaffected_still_uses_systemd(monkeypatch):
    """Regression guard: a row WITH a real per-task veridian-worker@<task_id>.service
    unit_name keeps using the exact original systemd ActiveState liveness check,
    unaffected by the new fallback branch."""
    mod = _load_module()
    row = _row(umr_id="UMR-PERTASK-ACTIVE", unit_name="veridian-worker@task-x.service",
               ts_dispatched=_days_ago(7))
    monkeypatch.setattr(mod, "_unit_active_state", lambda u: "active")
    entry = mod.decide_and_apply(row, execute=False)
    assert entry["has_reliable_unit"] is True
    assert entry["liveness_mechanism"] == "systemd_unit"
    assert entry["decision"] == "skipped_not_settled"


def test_null_unit_name_task_dir_found_but_ambiguous_still_fails_not_requeues(monkeypatch):
    """Even when a task directory IS found for a no-reliable-unit row but its own
    task.yaml is genuinely ambiguous (no commit evidence, non-negative status), the
    outcome must still be a real terminal failed -- never a re-queue, since re-queueing a
    row with no established per-task-unit mapping offers no real path back to a reliable
    future liveness check."""
    mod = _load_module()
    row = _row(umr_id="UMR-NOUNIT-AMBIGUOUS", unit_name=None, ts_dispatched=_days_ago(5))
    monkeypatch.setattr(mod, "_task_dir_for_row", lambda r: ("/fake/task-no-unit", "task_identity"))
    monkeypatch.setattr(mod, "_load_task_yaml", lambda d: {
        "checkpoints": [{"status": "in_progress"}], "branch": None, "repo": None,
    })
    entry = mod.decide_and_apply(row, execute=False)
    assert entry["decision"] == "would_mark_failed"
    assert entry["decision"] != "would_requeue"


def test_real_end_to_end_null_unit_name_reconciled_via_real_cli(monkeypatch, scratch_db):
    """Real, end-to-end proof (no mocked write): a real scratch umr_tasks row with a
    genuinely NULL unit_name and a stale ts_dispatched, run through the real
    decide_and_apply(execute=True) -- the real `mark-umr-terminal` CLI subprocess
    genuinely writes status='failed' to the real scratch DB. Directly covers this task's
    own SPEC point 6."""
    mod = _load_module()
    monkeypatch.setenv("SUPERBOSS_REGISTER_DB", scratch_db)
    monkeypatch.setattr(mod, "_task_dir_for_row", lambda r: (None, "no task directory found"))

    conn = sqlite3.connect(scratch_db)
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, ts_dispatched, tier, "
        "status, source_trigger, unit_name) VALUES ('UMR-TEST-NOUNIT-E2E', 'task-no-unit-e2e', "
        "?, ?, 1, 'running', 'unit_test', NULL)",
        (_days_ago(7), _days_ago(7)),
    )
    conn.commit()
    conn.close()

    row = _row(umr_id="UMR-TEST-NOUNIT-E2E", unit_name=None, ts_dispatched=_days_ago(7))
    entry = mod.decide_and_apply(row, execute=True)
    assert entry["decision"] == "failed", entry

    conn = sqlite3.connect(scratch_db)
    conn.row_factory = sqlite3.Row
    result = conn.execute("SELECT * FROM umr_tasks WHERE umr_id='UMR-TEST-NOUNIT-E2E'").fetchone()
    conn.close()
    assert dict(result)["status"] == "failed"
    assert dict(result)["status"] != "running"
    assert dict(result)["status"] != "completed"  # never invent a success status


def test_sweep_fetches_all_running_rows_regardless_of_unit_name(monkeypatch):
    """_fetch_affected_rows() must no longer be scoped to `unit_name LIKE
    'veridian-worker@%'` only -- the real, live-confirmed root cause of this whole blind
    spot. sweep() must see (and resolve) a NULL-unit_name row alongside a normal
    per-task-unit row in the same real pass."""
    mod = _load_module()
    fake_rows = [
        _row(umr_id="UMR-A", unit_name="veridian-worker@task-y.service"),
        _row(umr_id="UMR-B", unit_name=None, ts_dispatched=_days_ago(8)),
        _row(umr_id="UMR-C", unit_name="veridian-governor-tick.service", ts_dispatched=_days_ago(8)),
    ]
    monkeypatch.setattr(mod, "_fetch_affected_rows", lambda: fake_rows)
    monkeypatch.setattr(mod, "_unit_active_state", lambda u: "active")
    monkeypatch.setattr(mod, "_task_dir_for_row", lambda r: (None, "no task directory found"))

    report = mod.sweep(execute=False)
    assert report["examined"] == 3
    by_id = {r["umr_id"]: r for r in report["rows"]}
    assert by_id["UMR-A"]["decision"] == "skipped_not_settled"  # real active per-task unit
    assert by_id["UMR-B"]["decision"] == "would_mark_failed"  # NULL unit, stale
    assert by_id["UMR-C"]["decision"] == "would_mark_failed"  # shared unit, stale


if __name__ == "__main__":
    import inspect
    import shutil as _shutil

    class _MP:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name, None), True))
            setattr(obj, name, value)

        def setenv(self, name, value):
            self._undo.append((os.environ, name, os.environ.get(name), name in os.environ))
            os.environ[name] = value

        def undo(self):
            for obj, name, old, had in reversed(self._undo):
                if obj is os.environ:
                    if had:
                        os.environ[name] = old
                    else:
                        os.environ.pop(name, None)
                else:
                    setattr(obj, name, old)

    def _scratch_db():
        d = tempfile.mkdtemp()
        path = os.path.join(d, "scratch.sqlite")
        sbr2 = _load_sbr()
        sbr2.DB_PATH = path
        sbr2.init_db()
        return path, d

    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        params = inspect.signature(fn).parameters
        mp = _MP()
        db_dir = None
        try:
            kwargs = {}
            if "monkeypatch" in params:
                kwargs["monkeypatch"] = mp
            if "scratch_db" in params:
                db_path, db_dir = _scratch_db()
                kwargs["scratch_db"] = db_path
            fn(**kwargs)
            print(f"PASS: {name}")
        finally:
            mp.undo()
            if db_dir:
                _shutil.rmtree(db_dir, ignore_errors=True)
    print("ALL TESTS PASSED")
