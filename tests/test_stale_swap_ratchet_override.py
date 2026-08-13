#!/usr/bin/env python3
"""Real tests for the stale-swap-ratchet dispatch override
(UMR-20260813-155201-da76, "unwedge dispatch -- stale swap ratchet blocked
dispatch", addendum to P1 UMR-20260806-171945-5767).

Real incident this backstops (governing SPEC's own live evidence-gathering
run, 2026-08-13): dispatch_core.py's swap_backoff gate computes
swap_used_pct as a STATIC occupancy ratio (1 - SwapFree/SwapTotal from
/proc/meminfo). Linux never proactively reclaims swap pages once written,
so a single past spike can leave that ratio permanently >=
BACKOFF_UTILIZATION_PCT (0.80) forever, even with abundant real
MemAvailable and zero ongoing swap I/O. 5 real /proc/meminfo samples over
15s that tick showed SwapFree byte-frozen at exactly 775980 kB every
sample (swap_used_pct=0.8149 against SwapTotal=4194300 kB) while
MemAvailable held ~11.3GB of 15.6GB genuinely free, and real `vmstat 2 5`
showed so=1079,0,0,0,0 / si tapering to near-zero -- no steady-state swap
activity. 32 real queued rows sat frozen behind this, including a Tier-0
fix and six Tier-1 PR audits.

dispatch_core.py is NOT exempted from the narrow 2026-08-08 stop-work
order -- every real fix under test here lives in resource_governor.py
(exempt) and wraps dispatch_core.has_free_slot_detail()'s own result;
dispatch_core.py itself is untouched by this UMR.

Every /proc read goes through this module's own env-overridable path
constants (VERIDIAN_GOVERNOR_PROC_MEMINFO / VERIDIAN_GOVERNOR_PROC_VMSTAT /
VERIDIAN_GOVERNOR_SWAP_ACTIVITY_STATE) against real temp fixture files --
never the live host's real /proc. Every DB touch in the end-to-end section
uses a real, isolated, temp-file SQLite database (never the live
production database), same convention as
tests/test_target_pr_dispatch_time_recheck.py.
"""
import datetime
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from unittest import mock

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS_DIR)


def _schema_helpers():
    spec = importlib.util.spec_from_file_location(
        "sbr_helpers_swapratchet", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
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


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def _write_meminfo(path, mem_total_kb, mem_available_kb, swap_total_kb, swap_free_kb):
    with open(path, "w") as f:
        f.write(f"MemTotal:       {mem_total_kb} kB\n")
        f.write(f"MemAvailable:   {mem_available_kb} kB\n")
        f.write(f"SwapTotal:       {swap_total_kb} kB\n")
        f.write(f"SwapFree:        {swap_free_kb} kB\n")


def _write_vmstat(path, pswpin, pswpout):
    with open(path, "w") as f:
        f.write(f"pswpin {pswpin}\n")
        f.write(f"pswpout {pswpout}\n")
        f.write("nr_free_pages 12345\n")


# Real numbers from the governing SPEC's own live evidence.
REAL_MEM_TOTAL_KB = 15982916
REAL_MEM_AVAILABLE_KB = 11299132
REAL_SWAP_TOTAL_KB = 4194300
REAL_SWAP_FREE_KB = 775980  # -> swap_used_pct = 0.8149...
REAL_SLOT_DETAIL = {"check": "swap_backoff", "swap_used_pct": 0.8149, "threshold_pct": 0.8}


def _fixture_env(tmpdir, min_interval="0"):
    return {
        "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR,
        "VERIDIAN_GOVERNOR_PROC_MEMINFO": os.path.join(tmpdir, "meminfo"),
        "VERIDIAN_GOVERNOR_PROC_VMSTAT": os.path.join(tmpdir, "vmstat"),
        "VERIDIAN_GOVERNOR_SWAP_ACTIVITY_STATE": os.path.join(tmpdir, "swap-activity-state.json"),
        "VERIDIAN_GOVERNOR_SWAP_ACTIVITY_MIN_INTERVAL_S": min_interval,
    }


# ---------------------------------------------------------------------------
# 1. read_swap_page_counters() -- pure /proc/vmstat parser
# ---------------------------------------------------------------------------

def test_read_swap_page_counters_parses_real_vmstat_shape():
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rg_swp_1", _fixture_env(d))
        vmstat_path = os.path.join(d, "vmstat")
        _write_vmstat(vmstat_path, pswpin=501, pswpout=1079)
        pswpin, pswpout = rg.read_swap_page_counters(vmstat_path)
        assert (pswpin, pswpout) == (501, 1079)


# ---------------------------------------------------------------------------
# 2. swap_activity_quiet_detail() -- delta-based activity check
# ---------------------------------------------------------------------------

def test_swap_activity_cold_start_never_quiet():
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rg_swp_2", _fixture_env(d))
        _write_vmstat(os.path.join(d, "vmstat"), pswpin=0, pswpout=0)
        quiet, detail = rg.swap_activity_quiet_detail(now=datetime.datetime(2026, 8, 13, 16, 0, 0, tzinfo=datetime.timezone.utc))
        assert quiet is False
        assert detail["check"] == "swap_activity_cold_start"


def test_swap_activity_interval_too_short_never_quiet():
    with tempfile.TemporaryDirectory() as d:
        env = _fixture_env(d, min_interval="30")
        rg = _load_rg("rg_swp_3", env)
        _write_vmstat(os.path.join(d, "vmstat"), pswpin=0, pswpout=0)
        rg.swap_activity_quiet_detail(now=datetime.datetime(2026, 8, 13, 16, 0, 0, tzinfo=datetime.timezone.utc))
        # Only 1 real second later -- below the 30s min interval.
        quiet, detail = rg.swap_activity_quiet_detail(now=datetime.datetime(2026, 8, 13, 16, 0, 1, tzinfo=datetime.timezone.utc))
        assert quiet is False
        assert detail["check"] == "swap_activity_interval_too_short"


def test_swap_activity_zero_delta_over_real_window_is_quiet():
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rg_swp_4", _fixture_env(d))
        vmstat_path = os.path.join(d, "vmstat")
        _write_vmstat(vmstat_path, pswpin=1000, pswpout=2000)
        rg.swap_activity_quiet_detail(now=datetime.datetime(2026, 8, 13, 16, 0, 0, tzinfo=datetime.timezone.utc))
        # Same counters, 10 real seconds later -- confirmed quiet.
        quiet, detail = rg.swap_activity_quiet_detail(now=datetime.datetime(2026, 8, 13, 16, 0, 10, tzinfo=datetime.timezone.utc))
        assert quiet is True
        assert detail == {"check": "swap_activity_quiet", "pswpin_delta": 0, "pswpout_delta": 0,
                           "dt_seconds": 10.0, "noise_allowance_pages": 0}


def test_swap_activity_real_pswpout_growth_is_sustained_not_quiet():
    """Mirrors the SPEC's own real vmstat evidence: so=1079 then 0,0,0,0 --
    a real prior swap-out spike must still show as sustained/active for the
    window that actually saw it."""
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rg_swp_5", _fixture_env(d))
        vmstat_path = os.path.join(d, "vmstat")
        _write_vmstat(vmstat_path, pswpin=1000, pswpout=2000)
        rg.swap_activity_quiet_detail(now=datetime.datetime(2026, 8, 13, 16, 0, 0, tzinfo=datetime.timezone.utc))
        _write_vmstat(vmstat_path, pswpin=1501, pswpout=3079)  # real si/so growth
        quiet, detail = rg.swap_activity_quiet_detail(now=datetime.datetime(2026, 8, 13, 16, 0, 10, tzinfo=datetime.timezone.utc))
        assert quiet is False
        assert detail["check"] == "swap_activity_sustained"
        assert detail["pswpin_delta"] == 501 and detail["pswpout_delta"] == 1079


# ---------------------------------------------------------------------------
# 3. _real_mem_headroom_bytes()
# ---------------------------------------------------------------------------

def test_real_mem_headroom_bytes_matches_backoff_math():
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rg_swp_6", _fixture_env(d))
        meminfo_path = os.path.join(d, "meminfo")
        _write_meminfo(meminfo_path, REAL_MEM_TOTAL_KB, REAL_MEM_AVAILABLE_KB,
                        REAL_SWAP_TOTAL_KB, REAL_SWAP_FREE_KB)
        dc = rg._dispatch_core()
        mem_total = REAL_MEM_TOTAL_KB * 1024
        mem_available = REAL_MEM_AVAILABLE_KB * 1024
        expected = (mem_total * dc.BACKOFF_UTILIZATION_PCT) - (mem_total - mem_available)
        assert rg._real_mem_headroom_bytes(meminfo_path) == expected
        assert expected > dc.PER_WORKER_MEMORY_BUDGET_BYTES  # real evidence: genuinely abundant


def test_real_mem_headroom_bytes_none_when_memtotal_unreadable():
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rg_swp_7", _fixture_env(d))
        meminfo_path = os.path.join(d, "meminfo")
        with open(meminfo_path, "w") as f:
            f.write("SomeOtherField:   123 kB\n")
        assert rg._real_mem_headroom_bytes(meminfo_path) is None


# ---------------------------------------------------------------------------
# 4. _override_stale_swap_backoff() -- the real, narrow override itself
# ---------------------------------------------------------------------------

def test_overrides_real_spec_evidence_shape():
    """The exact real numbers from the governing SPEC's own live evidence:
    swap_used_pct=0.8149 (stale), MemAvailable~11.3GB abundant, swap I/O
    confirmed quiet across two samples -- must override."""
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rg_swp_8", _fixture_env(d))
        _write_meminfo(os.path.join(d, "meminfo"), REAL_MEM_TOTAL_KB, REAL_MEM_AVAILABLE_KB,
                        REAL_SWAP_TOTAL_KB, REAL_SWAP_FREE_KB)
        _write_vmstat(os.path.join(d, "vmstat"), pswpin=501, pswpout=1079)
        rg._override_stale_swap_backoff(False, REAL_SLOT_DETAIL,
                                         now=datetime.datetime(2026, 8, 13, 16, 0, 0, tzinfo=datetime.timezone.utc))
        ok, detail = rg._override_stale_swap_backoff(
            False, REAL_SLOT_DETAIL, now=datetime.datetime(2026, 8, 13, 16, 0, 15, tzinfo=datetime.timezone.utc))
        assert ok is True, detail
        assert detail["check"] == "swap_backoff_override_stale_ratchet"
        assert detail["original_check"] == "swap_backoff"
        assert detail["swap_used_pct"] == 0.8149


def test_does_not_override_when_swap_out_is_actively_sustained():
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rg_swp_9", _fixture_env(d))
        _write_meminfo(os.path.join(d, "meminfo"), REAL_MEM_TOTAL_KB, REAL_MEM_AVAILABLE_KB,
                        REAL_SWAP_TOTAL_KB, REAL_SWAP_FREE_KB)
        vmstat_path = os.path.join(d, "vmstat")
        _write_vmstat(vmstat_path, pswpin=501, pswpout=1079)
        rg._override_stale_swap_backoff(False, REAL_SLOT_DETAIL,
                                         now=datetime.datetime(2026, 8, 13, 16, 0, 0, tzinfo=datetime.timezone.utc))
        _write_vmstat(vmstat_path, pswpin=520, pswpout=1600)  # real ongoing swap-out
        ok, detail = rg._override_stale_swap_backoff(
            False, REAL_SLOT_DETAIL, now=datetime.datetime(2026, 8, 13, 16, 0, 15, tzinfo=datetime.timezone.utc))
        assert ok is False
        assert detail is REAL_SLOT_DETAIL  # unchanged, original block preserved verbatim


def test_does_not_override_when_real_memory_headroom_is_low():
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rg_swp_10", _fixture_env(d))
        _write_meminfo(os.path.join(d, "meminfo"), REAL_MEM_TOTAL_KB, 1000000,  # tight real MemAvailable
                        REAL_SWAP_TOTAL_KB, REAL_SWAP_FREE_KB)
        _write_vmstat(os.path.join(d, "vmstat"), pswpin=0, pswpout=0)
        rg._override_stale_swap_backoff(False, REAL_SLOT_DETAIL,
                                         now=datetime.datetime(2026, 8, 13, 16, 0, 0, tzinfo=datetime.timezone.utc))
        ok, detail = rg._override_stale_swap_backoff(
            False, REAL_SLOT_DETAIL, now=datetime.datetime(2026, 8, 13, 16, 0, 15, tzinfo=datetime.timezone.utc))
        assert ok is False
        assert detail is REAL_SLOT_DETAIL


def test_never_overrides_swap_hard_ceiling():
    """The Owner's own 0.99 hard ceiling must never be touched by this
    override, even with abundant memory and quiet swap I/O."""
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rg_swp_11", _fixture_env(d))
        _write_meminfo(os.path.join(d, "meminfo"), REAL_MEM_TOTAL_KB, REAL_MEM_AVAILABLE_KB,
                        REAL_SWAP_TOTAL_KB, REAL_SWAP_FREE_KB)
        _write_vmstat(os.path.join(d, "vmstat"), pswpin=0, pswpout=0)
        hard_detail = {"check": "swap_hard_ceiling", "swap_used_pct": 0.995, "threshold_pct": 0.99}
        rg._override_stale_swap_backoff(False, hard_detail,
                                         now=datetime.datetime(2026, 8, 13, 16, 0, 0, tzinfo=datetime.timezone.utc))
        ok, detail = rg._override_stale_swap_backoff(
            False, hard_detail, now=datetime.datetime(2026, 8, 13, 16, 0, 15, tzinfo=datetime.timezone.utc))
        assert ok is False
        assert detail is hard_detail


def test_never_overrides_other_real_gates():
    """cap_exhausted / mem_backoff / load1_backoff must all pass through
    completely unchanged -- this override is narrowly scoped to
    swap_backoff only."""
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rg_swp_12", _fixture_env(d))
        for other_detail in (
            {"check": "cap_exhausted", "running_worker_count": 5, "cap": 5},
            {"check": "mem_backoff", "mem_used_pct": 0.85, "threshold_pct": 0.8},
            {"check": "load1_backoff", "load1": 9.0, "cpu_count": 8, "threshold": 6.4},
        ):
            ok, detail = rg._override_stale_swap_backoff(False, other_detail)
            assert ok is False
            assert detail is other_detail


def test_passthrough_when_slot_already_ok():
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rg_swp_13", _fixture_env(d))
        ok, detail = rg._override_stale_swap_backoff(True, {"check": "ok"})
        assert ok is True
        assert detail == {"check": "ok"}


# ---------------------------------------------------------------------------
# 5. dispatch_one() end-to-end -- the override must actually let a real
#    queued row dispatch when the stale-ratchet conditions hold, and must
#    NOT when they don't.
# ---------------------------------------------------------------------------

def _seed_row(scratch_db, sbr, task_identity):
    conn = _new_conn(scratch_db)
    umr_id = sbr.upsert_umr_task(conn, {
        "task_identity": task_identity, "tier": 0, "status": "queued",
        "source_trigger": "unit_test", "task_kind": "veridian_task_create",
        "inputs": {"repo": "veridian-scripts",
                   "title": "Real background maintenance work, no PR/OCID reference",
                   "prompt": "p"},
        "reason": "queued",
    })
    conn.commit()
    conn.close()
    return umr_id


def _fake_run_no_matches(cmd, **kwargs):
    if "list" in cmd:
        return _FakeCompletedProcess(0, json.dumps([]))
    return _FakeCompletedProcess(0, json.dumps({}))


def test_dispatch_one_end_to_end_overrides_and_dispatches_real_spec_shape():
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = dict(_fixture_env(d))
        env["SUPERBOSS_REGISTER_DB"] = scratch_db
        rg = _load_rg("rg_swp_e2e_1", env)
        rg.EMERGENCY_STOP_PATH = os.path.join(d, "EMERGENCY_STOP_never_created")
        rg.STOP_WORK_ORDER_TASK_IDS = ()

        _seed_row(scratch_db, sbr, "test-swap-ratchet-override-dispatches")
        _write_meminfo(os.path.join(d, "meminfo"), REAL_MEM_TOTAL_KB, REAL_MEM_AVAILABLE_KB,
                        REAL_SWAP_TOTAL_KB, REAL_SWAP_FREE_KB)
        _write_vmstat(os.path.join(d, "vmstat"), pswpin=501, pswpout=1079)
        # Seed a prior swap-activity sample so the very first real
        # dispatch_one() call already has a trustworthy elapsed window
        # (a bare cold start must never itself dispatch -- see
        # test_swap_activity_cold_start_never_quiet above).
        rg.swap_activity_quiet_detail(now=datetime.datetime(2026, 8, 13, 16, 0, 0, tzinfo=datetime.timezone.utc))

        dc_mod = rg._dispatch_core()
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            with mock.patch.object(dc_mod, "has_free_slot_detail", return_value=(False, dict(REAL_SLOT_DETAIL))), \
                 mock.patch.object(rg, "_run", side_effect=_fake_run_no_matches), \
                 mock.patch.object(dc_mod, "record_dispatch_event", return_value=None), \
                 mock.patch.object(rg, "_append_attention") as mock_attn, \
                 mock.patch.object(rg, "_perform_spawn",
                                    return_value={"status": "running", "unit_name": "veridian-worker@x.service",
                                                  "outputs": {}}) as mock_spawn:
                result = rg.dispatch_one(
                    now=datetime.datetime(2026, 8, 13, 16, 0, 15, tzinfo=datetime.timezone.utc))
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)

        assert result["action"] != "deferred", result
        mock_spawn.assert_called_once()
        assert any("overrode a stale swap_backoff ratchet" in c.args[0] for c in mock_attn.call_args_list), \
            mock_attn.call_args_list


def test_dispatch_one_end_to_end_still_defers_when_swap_actually_active():
    """Same stale-looking swap_used_pct, but real swap I/O is genuinely
    active this time -- must still defer, never dispatch. Confirms the
    hard-ceiling-style safety this override is required to preserve."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = dict(_fixture_env(d))
        env["SUPERBOSS_REGISTER_DB"] = scratch_db
        rg = _load_rg("rg_swp_e2e_2", env)
        rg.EMERGENCY_STOP_PATH = os.path.join(d, "EMERGENCY_STOP_never_created")
        rg.STOP_WORK_ORDER_TASK_IDS = ()

        umr_id = _seed_row(scratch_db, sbr, "test-swap-ratchet-still-defers-when-active")
        _write_meminfo(os.path.join(d, "meminfo"), REAL_MEM_TOTAL_KB, REAL_MEM_AVAILABLE_KB,
                        REAL_SWAP_TOTAL_KB, REAL_SWAP_FREE_KB)
        vmstat_path = os.path.join(d, "vmstat")
        _write_vmstat(vmstat_path, pswpin=501, pswpout=1079)
        rg.swap_activity_quiet_detail(now=datetime.datetime(2026, 8, 13, 16, 0, 0, tzinfo=datetime.timezone.utc))
        _write_vmstat(vmstat_path, pswpin=571, pswpout=1600)  # real, ongoing swap-out

        dc_mod = rg._dispatch_core()
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            with mock.patch.object(dc_mod, "has_free_slot_detail", return_value=(False, dict(REAL_SLOT_DETAIL))), \
                 mock.patch.object(rg, "_perform_spawn") as mock_spawn:
                result = rg.dispatch_one(
                    now=datetime.datetime(2026, 8, 13, 16, 0, 15, tzinfo=datetime.timezone.utc))
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)

        assert result["action"] == "deferred", result
        assert result["slot_detail"]["check"] == "swap_backoff", result
        mock_spawn.assert_not_called()
        # Row stays queued -- deferred is not a terminal outcome.
        conn = _new_conn(scratch_db)
        row = conn.execute("SELECT status FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
        conn.close()
        assert row["status"] == "queued", row["status"]


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
