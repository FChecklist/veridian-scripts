#!/usr/bin/env python3
"""Real tests for dispatch_core.classify_blocking_category() /
log_dispatch_decision() (UMR-20260813-120054-4e66, addendum to
UMR-20260806-171945-5767 / UMR-20260813-100854-e8a1: "restore the stalled
dispatch pipeline").

Real gap this backstops: resource_governor.py's dispatch_one() already
computes a real, detailed blocking reason every tick
(dispatch_core.has_free_slot_detail() -- the UMR-20260806-101839-688e fix
for "cap_exhausted" vs a real resource-headroom veto being indistinguishable
in the tick log), but that detail only ever reached
/opt/veridian/ai-os/tasks/resource_governor_tick.log, a flat file
resource_governor_tick_loop.sh's own `>> "$LOG" 2>&1` redirect keeps
entirely out of the systemd journal -- live-confirmed 2026-08-13:
`journalctl --user` on both veridian-cron-dispatch-tick.service (the wrong
unit -- it never touches umr_tasks queued rows) AND
veridian-governor-tick.service (the real, always-on unit that runs
dispatch_one() every 30s) showed nothing about WHY a given tick dispatched
nothing.

Three things are tested here, against the real functions (dispatch_core.py
itself, never a reimplementation):

1. classify_blocking_category() is a real, total, deterministic mapping
   from every real dispatch_one() `action` (plus, for "deferred", the real
   `slot_detail.check`) to one of the small, closed set of real blocking
   categories a human can grep journalctl for.
2. log_dispatch_decision() pipes a real, structured line to `systemd-cat`
   (never a raw file write -- the whole point is landing in the journal),
   and is fail-open (never raises) when systemd-cat is unavailable/fails.
3. resource_governor.run_tick() calls log_dispatch_decision() exactly once
   per real dispatch_one() call it makes, every tick -- not only on a
   blocked/deferred outcome.
"""
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
        "sbr_helpers_journal_log", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


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
# 1. classify_blocking_category() -- exhaustive, real mapping
# ---------------------------------------------------------------------------

def test_cap_exhausted_is_distinguished_from_resource_headroom_veto():
    """The exact real ambiguity this whole instrumentation exists to
    surface: two different real `deferred` causes must map to two different
    real categories, never collapsed into one generic string."""
    cap = {"action": "deferred", "umr_id": "UMR-1",
           "slot_detail": {"check": "cap_exhausted", "running_worker_count": 5, "cap": 5}}
    swap = {"action": "deferred", "umr_id": "UMR-2",
            "slot_detail": {"check": "swap_backoff", "swap_used_pct": 0.82, "threshold_pct": 0.8}}
    load = {"action": "deferred", "umr_id": "UMR-3",
            "slot_detail": {"check": "load1_backoff", "load1": 18.2, "cpu_count": 8, "threshold": 6.4}}
    assert dc.classify_blocking_category(cap) == "cap_exhausted"
    assert dc.classify_blocking_category(swap) == "resource_headroom_veto"
    assert dc.classify_blocking_category(load) == "resource_headroom_veto"
    assert dc.classify_blocking_category(cap) != dc.classify_blocking_category(swap)


def test_unrecognized_deferred_check_still_reports_the_real_gate():
    """has_free_slot_detail() only ever returns "cap_exhausted" or a real
    headroom_detail dict for a "deferred" action -- a future, not-yet-
    enumerated headroom check name must still land in
    resource_headroom_veto (the real gate it came from), never crash and
    never silently become cap_exhausted."""
    result = {"action": "deferred", "slot_detail": {"check": "some_future_check"}}
    assert dc.classify_blocking_category(result) == "resource_headroom_veto"


def test_resource_threshold_gate_covers_frozen_and_emergency_stopped():
    assert dc.classify_blocking_category({"action": "frozen"}) == "resource_threshold_gate"
    assert dc.classify_blocking_category({"action": "emergency_stopped"}) == "resource_threshold_gate"


def test_stop_work_gate():
    assert dc.classify_blocking_category({"action": "blocked_stop_work_order"}) == "stop_work_gate"


def test_dedup_rejection_covers_all_three_real_dedup_guards():
    for action in ("rejected_duplicate_pr", "rejected_duplicate_reuse_verdict", "superseded_by_ocid_evidence"):
        assert dc.classify_blocking_category({"action": action}) == "dedup_rejection", action


def test_dispatched_idle_would_dispatch_and_superboss_unavailable():
    assert dc.classify_blocking_category({"action": "dispatched"}) == "dispatched"
    assert dc.classify_blocking_category({"action": "idle"}) == "queue_empty"
    assert dc.classify_blocking_category({"action": "would_dispatch"}) == "would_dispatch"
    assert dc.classify_blocking_category({"action": "superboss_unavailable"}) == "superboss_unavailable"


def test_unenumerated_action_falls_into_open_other_bucket_never_raises():
    assert dc.classify_blocking_category({"action": "some_brand_new_action"}) == "other"
    assert dc.classify_blocking_category({}) == "other"
    assert dc.classify_blocking_category(None) == "other"


# ---------------------------------------------------------------------------
# 2. log_dispatch_decision() -- real systemd-cat call, real fail-open
# ---------------------------------------------------------------------------

def test_log_dispatch_decision_invokes_systemd_cat_with_real_category_payload():
    result = {"action": "deferred", "umr_id": "UMR-9",
              "detail": "no free dispatch slot -- real gate: swap_backoff",
              "slot_detail": {"check": "swap_backoff", "swap_used_pct": 0.82, "threshold_pct": 0.8}}
    with mock.patch.object(dc.subprocess, "run") as mock_run:
        mock_run.return_value = mock.Mock(returncode=0)
        dc.log_dispatch_decision(result, tag="test-tag")

    assert mock_run.called
    args, kwargs = mock_run.call_args
    argv = args[0]
    assert argv[0] == "systemd-cat"
    assert "-t" in argv and "test-tag" in argv
    assert "swap_backoff" in kwargs["input"]
    assert "resource_headroom_veto" in kwargs["input"]
    assert "UMR-9" in kwargs["input"]


def test_log_dispatch_decision_is_fail_open_when_systemd_cat_missing():
    """A missing/broken systemd-cat must never propagate -- this is a purely
    observational side call, never load-bearing for the real dispatch tick
    it wraps (same convention as record_tick()/record_dispatch_event())."""
    with mock.patch.object(dc.subprocess, "run", side_effect=FileNotFoundError("no systemd-cat")):
        dc.log_dispatch_decision({"action": "dispatched", "umr_id": "UMR-1"})  # must not raise


def test_log_dispatch_decision_handles_none_result_without_raising():
    with mock.patch.object(dc.subprocess, "run") as mock_run:
        mock_run.return_value = mock.Mock(returncode=0)
        dc.log_dispatch_decision(None)
    assert mock_run.called


# ---------------------------------------------------------------------------
# 3. resource_governor.run_tick() -- real, per-tick wiring
# ---------------------------------------------------------------------------

def test_run_tick_logs_one_journal_decision_per_dispatch_one_call():
    """Empty queue -> exactly one real dispatch_one() call (action='idle'),
    so log_dispatch_decision() must be called exactly once, with that same
    real result dict -- proving run_tick() logs on EVERY tick, not only on
    a blocked/deferred outcome."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        conn = sqlite3.connect(scratch_db)
        conn.row_factory = sqlite3.Row
        sbr._ensure_umr_table(conn)
        sbr._ensure_pm_decisions_pending_table(conn)
        sbr._ensure_ocid_artifact_links_table(conn)
        conn.close()

        env = {"SUPERBOSS_REGISTER_DB": scratch_db, "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR}
        rg = _load_rg("rg_journal_logging", env)
        rg.EMERGENCY_STOP_PATH = os.path.join(d, "EMERGENCY_STOP_never_created")

        dc_mod = rg._dispatch_core()
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            with mock.patch.object(dc_mod, "log_dispatch_decision") as mock_log:
                result = rg.run_tick(max_dispatches=5, now=rg._utcnow())
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)

        assert len(result["dispatches"]) == 1
        assert result["dispatches"][0]["action"] == "idle"
        assert mock_log.call_count == 1
        (logged_result,), _ = mock_log.call_args
        assert logged_result is result["dispatches"][0]
