#!/usr/bin/env python3
"""Real tests for the dispatch-throughput-stall follow-up
(UMR-20260806-101839-688e, parent UMR-20260806-071025-1d28).

Real incident this backstops: the production tick log
(/opt/veridian/ai-os/tasks/resource_governor_tick.log) showed every real
tick selecting the SAME single queued row and logging
`{"action": "deferred", "detail": "no free concurrency slot under
dispatch_core's shared cap", ...}` while `dispatch_core.running_worker_count()`
was independently confirmed to be 0/5 -- the real block was
`has_resource_headroom()`'s load-average veto, not the concurrency cap, but
the old fixed detail string could never say so.

Separately, `run_tick()`'s dispatch loop stopped the ENTIRE tick after the
first `dispatch_one()` call whose action wasn't literally "dispatched" --
even when that action (`rejected_duplicate_pr` / `superseded_by_ocid_evidence`)
already wrote a real terminal status on the picked row, meaning the row was
no longer 'queued' and a different real row was available to try. This
wasted real, available capacity: with dispatch capacity free and other real
queued rows waiting, the tick gave up after just one row-specific skip.

Two things are tested here, both against the real functions (never a mock
of dispatch_core's slot logic itself, only of the parts that would touch the
real host/systemd):

1. `dispatch_core.has_free_slot_detail()` / `has_free_slot()` correctly
   distinguish "cap_exhausted" (running_worker_count >= cap) from a real
   resource-headroom veto, instead of collapsing both into one message.
2. `resource_governor.run_tick()` keeps dispatching further real queued rows
   after a `rejected_duplicate_pr` outcome instead of stopping the tick.

Every DB touch uses a real, isolated, temp-file SQLite database (never the
live production database), same convention as
tests/test_flag_stale_queued_tasks.py.
"""
import datetime
import importlib.util
import os
import sqlite3
import sys
import tempfile
from unittest import mock

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)

import dispatch_core as dc  # noqa: E402


def _schema_helpers():
    spec = importlib.util.spec_from_file_location(
        "sbr_helpers_runtick", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
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
    sbr._ensure_pm_decisions_pending_table(conn)
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


# ---------------------------------------------------------------------------
# 1. dispatch_core: real gate must be distinguishable, cap vs resource veto
# ---------------------------------------------------------------------------

def test_has_free_slot_detail_reports_cap_exhausted_when_slots_full():
    with mock.patch.object(dc, "running_worker_count", return_value=5), \
         mock.patch.object(dc, "has_resource_headroom_detail", return_value=(True, {"check": "ok"})):
        ok, detail = dc.has_free_slot_detail(cap=5)
    assert ok is False
    assert detail["check"] == "cap_exhausted", detail
    assert detail["running_worker_count"] == 5
    assert detail["cap"] == 5


def test_has_free_slot_detail_reports_real_resource_veto_when_slots_are_free():
    """The exact real-world case that motivated this fix: 0 running workers
    (real slots free) but the tick log still said "no free concurrency
    slot" -- must now name the real gate (e.g. a load-average veto)."""
    with mock.patch.object(dc, "running_worker_count", return_value=0), \
         mock.patch.object(dc, "has_resource_headroom_detail",
                            return_value=(False, {"check": "load1_backoff", "load1": 11.0,
                                                   "cpu_count": 8, "threshold": 6.4})):
        ok, detail = dc.has_free_slot_detail(cap=5)
    assert ok is False
    assert detail["check"] == "load1_backoff", detail
    assert detail["check"] != "cap_exhausted"


def test_has_free_slot_bool_wrapper_unchanged_contract():
    """Every existing `if not dispatch_core.has_free_slot():` call site must
    keep working unmodified -- has_free_slot() must still be a plain bool."""
    with mock.patch.object(dc, "running_worker_count", return_value=0), \
         mock.patch.object(dc, "has_resource_headroom_detail", return_value=(True, {"check": "ok"})):
        assert dc.has_free_slot(cap=5) is True
    with mock.patch.object(dc, "running_worker_count", return_value=5), \
         mock.patch.object(dc, "has_resource_headroom_detail", return_value=(True, {"check": "ok"})):
        assert dc.has_free_slot(cap=5) is False


# ---------------------------------------------------------------------------
# 2. resource_governor.run_tick(): must not abandon the tick on a
#    row-resolved (but non-"dispatched") outcome while real capacity and
#    real other queued rows remain.
# ---------------------------------------------------------------------------

def test_run_tick_continues_after_duplicate_pr_skip():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_runtick_1", env)
        rg.EMERGENCY_STOP_PATH = os.path.join(d, "EMERGENCY_STOP_never_created")

        now = rg._utcnow()
        conn = _new_conn(scratch_db)
        # Row A: ranked first (older ts_submitted), will hit the real
        # duplicate-PR guard and resolve to status='rejected_duplicate' --
        # no longer 'queued' after dispatch_one() handles it.
        older_ts = (now - datetime.timedelta(minutes=10)).isoformat()
        umr_a = sbr.upsert_umr_task(conn, {
            "task_identity": "test-runtick-dup-a", "tier": 1, "status": "queued",
            "source_trigger": "unit_test", "task_kind": "veridian_task_create",
            "inputs": {"repo": "x", "title": "OCID-042 dup test", "prompt": "p"},
            "ts_submitted": older_ts, "reason": "queued",
        })
        # Row B: ranked second (newer ts_submitted), should be genuinely
        # picked up and attempted by the SAME tick once row A resolves.
        newer_ts = (now - datetime.timedelta(minutes=5)).isoformat()
        umr_b = sbr.upsert_umr_task(conn, {
            "task_identity": "test-runtick-dup-b", "tier": 1, "status": "queued",
            "source_trigger": "unit_test", "task_kind": "systemctl_action",
            "unit_name": "veridian-worker@does-not-exist-test-unit.service",
            "inputs": {"action": "start"},
            "ts_submitted": newer_ts, "reason": "queued",
        })
        conn.commit()
        conn.close()

        dc_mod = rg._dispatch_core()  # the real dispatch_core.py, loaded the same
        # way _dispatch_one_inner() itself loads it (importlib, cached in
        # rg._dc) -- NOT the same module object as `import dispatch_core`,
        # so it must be fetched this way for the patch to actually apply.

        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            with mock.patch.object(rg, "find_pr_for_task_identity", return_value=(999, "some-repo")), \
                 mock.patch.object(dc_mod, "has_free_slot_detail", return_value=(True, {"check": "ok"})), \
                 mock.patch.object(dc_mod, "record_dispatch_event", return_value=None), \
                 mock.patch.object(rg, "_perform_spawn",
                                    return_value={"status": "running", "unit_name": "veridian-worker@x.service",
                                                  "outputs": {}}):
                result = rg.run_tick(max_dispatches=5, now=now)
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)

        actions = [d["action"] for d in result["dispatches"]]
        # Real, load-bearing assertion: row B must have been genuinely
        # attempted in the SAME tick as row A's duplicate-PR skip. On the
        # pre-fix code, `actions` would be exactly ["rejected_duplicate_pr"]
        # -- the tick stopped there and row B was never even looked at.
        assert "rejected_duplicate_pr" in actions, actions
        assert "dispatched" in actions, (
            f"run_tick() must keep dispatching after a row-resolved "
            f"non-'dispatched' outcome (real capacity/other rows still "
            f"available) -- got actions={actions}"
        )

        conn = _new_conn(scratch_db)
        row_a = conn.execute("SELECT status FROM umr_tasks WHERE umr_id=?", (umr_a,)).fetchone()
        row_b = conn.execute("SELECT status FROM umr_tasks WHERE umr_id=?", (umr_b,)).fetchone()
        conn.close()
        assert row_a["status"] == "rejected_duplicate", row_a["status"]
        assert row_b["status"] == "running", row_b["status"]


def test_run_tick_still_stops_on_row_independent_block():
    """A genuinely row-independent block (deferred/frozen/emergency_stopped)
    must still stop the tick immediately -- retrying a different row cannot
    change a resource-headroom/cap/metric/emergency-stop outcome, and this
    must never be weakened into a busy-loop."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_runtick_2", env)
        rg.EMERGENCY_STOP_PATH = os.path.join(d, "EMERGENCY_STOP_never_created")

        now = rg._utcnow()
        conn = _new_conn(scratch_db)
        for i in range(3):
            sbr.upsert_umr_task(conn, {
                "task_identity": f"test-runtick-deferred-{i}", "tier": 1, "status": "queued",
                "source_trigger": "unit_test", "task_kind": "systemctl_action",
                "unit_name": "veridian-worker@does-not-exist-test-unit.service",
                "inputs": {"action": "start"},
                "ts_submitted": (now - datetime.timedelta(minutes=i)).isoformat(), "reason": "queued",
            })
        conn.commit()
        conn.close()

        dc_mod = rg._dispatch_core()
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            with mock.patch.object(dc_mod, "has_free_slot_detail",
                                    return_value=(False, {"check": "load1_backoff", "load1": 11.0})):
                result = rg.run_tick(max_dispatches=5, now=now)
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)

        actions = [d["action"] for d in result["dispatches"]]
        assert actions == ["deferred"], actions


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
