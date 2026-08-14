#!/usr/bin/env python3
"""Real tests for the load1-backoff calibration-defect override
(this UMR, 2026-08-14 -- "recalibrate the dispatch load gate that ...").

Real incident this backstops (governing SPEC's own live PM-sentinel
evidence-gathering run, 2026-08-14T07:45-07:52Z): dispatch_core.py's
load1_backoff gate refuses dispatch whenever os.getloadavg()[0] (the
kernel's 1-minute exponentially-decayed load average) exceeds
cpu_count * BACKOFF_UTILIZATION_PCT (0.80). load1 counts every task in D
state (TASK_UNINTERRUPTIBLE -- blocked on I/O, not runnable) identically to
a genuinely CPU-bound RUNNING task. Real evidence: the governor tick logged
slot_detail exactly {check: load1_backoff, load1: 9.4169921875, cpu_count:
8, threshold: 6.4} repeatedly, while a SIMULTANEOUS `vmstat 1 3` showed
runnable r=0-1, blocked b=0, CPU 90-96% idle, and only one node worker at
79% of one core out of 8 -- load1 near 9 was NOT real CPU demand. `free -h`
showed 427Mi free RAM with swap at 3.1Gi/4.0Gi used, and both kswapd0 and
kcompactd0 (swap-reclaim kernel threads) were actively consuming CPU
alongside a D-state systemd-run scope in the veridian-checkpoint-heartbeat
slice. Real measured impact: zero rows dispatched between 07:19:22 and
07:49 UTC while the queued backlog grew to 4 rows, on a machine over 90%
idle.

dispatch_core.py is NOT exempted from the narrow 2026-08-08 stop-work order
-- every real fix under test here lives in resource_governor.py (exempt,
per this UMR's own SPEC) and wraps dispatch_core.has_free_slot_detail()'s
own result; dispatch_core.py itself is untouched by this UMR, same
convention test_stale_swap_ratchet_override.py already established for the
swap_backoff gate.

Every /proc read goes through this module's own env-overridable path
constants (VERIDIAN_GOVERNOR_PROC_STAT / VERIDIAN_GOVERNOR_PROC_LOADAVG)
against real temp fixture files -- never the live host's real /proc. Every
DB touch in the end-to-end section uses a real, isolated, temp-file SQLite
database, same convention as test_stale_swap_ratchet_override.py.
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
        "sbr_helpers_load1idle", os.path.join(SCRIPTS_DIR, "superboss-register.py"))
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


def _write_loadavg(path, load1=9.4169921875, load5=8.1, load15=6.9, nr_running=1, nr_threads=456, last_pid=12345):
    with open(path, "w") as f:
        f.write(f"{load1} {load5} {load15} {nr_running}/{nr_threads} {last_pid}\n")


def _write_proc_stat(path, user, nice, system, idle, iowait=0):
    """Minimal real /proc/stat 'cpu ' line shape -- see read_cpu_times()'s
    own parsing (fields: user nice system idle iowait ...)."""
    with open(path, "w") as f:
        f.write(f"cpu  {user} {nice} {system} {idle} {iowait} 0 0 0 0 0\n")
        f.write("cpu0 0 0 0 0 0 0 0 0 0 0\n")


# Real numbers from the governing SPEC's own live evidence.
REAL_LOAD1 = 9.4169921875
REAL_CPU_COUNT = 8
REAL_THRESHOLD = 6.4
REAL_SLOT_DETAIL = {"check": "load1_backoff", "load1": REAL_LOAD1, "cpu_count": REAL_CPU_COUNT,
                     "threshold": REAL_THRESHOLD}


def _fixture_env(tmpdir):
    return {
        "VERIDIAN_SCRIPTS_DIR": SCRIPTS_DIR,
        "VERIDIAN_GOVERNOR_PROC_STAT": os.path.join(tmpdir, "stat"),
        "VERIDIAN_GOVERNOR_PROC_LOADAVG": os.path.join(tmpdir, "loadavg"),
        "VERIDIAN_GOVERNOR_METRIC_STATE": os.path.join(tmpdir, "metric-state.json"),
    }


# ---------------------------------------------------------------------------
# 1. read_loadavg_runnable() -- pure /proc/loadavg parser
# ---------------------------------------------------------------------------

def test_read_loadavg_runnable_parses_real_shape():
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rg_l1_1", _fixture_env(d))
        loadavg_path = os.path.join(d, "loadavg")
        _write_loadavg(loadavg_path, nr_running=2, nr_threads=456)
        running, total = rg.read_loadavg_runnable(loadavg_path)
        assert (running, total) == (2, 456)


# ---------------------------------------------------------------------------
# 2. _override_load1_backoff_when_cpu_idle() -- the real, narrow override
# ---------------------------------------------------------------------------

def test_overrides_real_spec_evidence_shape():
    """The exact real numbers from the governing SPEC's own live evidence:
    load1=9.4169921875 against threshold=6.4 (stale/inflated), real
    delta-based cpu utilization confirmed low (D-state/swap-reclaim noise,
    not compute demand), and a real /proc/loadavg runnable snapshot
    confirming no run-queue contention (nr_running=1 <= cpu_count=8) --
    must override."""
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rg_l1_2", _fixture_env(d))
        _write_loadavg(os.path.join(d, "loadavg"), nr_running=1, nr_threads=456)
        # Real evidence shape: CPU ~90-96% idle -> metrics["cpu"] ~4-10%.
        metrics = {"cpu": 6.5}
        ok, detail = rg._override_load1_backoff_when_cpu_idle(False, dict(REAL_SLOT_DETAIL), metrics)
        assert ok is True, detail
        assert detail["check"] == "load1_backoff_override_cpu_idle"
        assert detail["original_check"] == "load1_backoff"
        assert detail["load1"] == REAL_LOAD1
        assert detail["cpu_count"] == REAL_CPU_COUNT
        assert detail["nr_running"] == 1


def test_does_not_override_when_real_cpu_utilization_is_high():
    """Same inflated load1, but this tick's own real delta-based CPU
    utilization is NOT confirmed low (genuine compute demand) -- must still
    defer, never dispatch."""
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rg_l1_3", _fixture_env(d))
        _write_loadavg(os.path.join(d, "loadavg"), nr_running=1, nr_threads=456)
        metrics = {"cpu": 92.0}  # real, genuine CPU saturation
        ok, detail = rg._override_load1_backoff_when_cpu_idle(False, dict(REAL_SLOT_DETAIL), metrics)
        assert ok is False
        assert detail["check"] == "load1_backoff"


def test_does_not_override_when_metrics_cpu_missing():
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rg_l1_4", _fixture_env(d))
        _write_loadavg(os.path.join(d, "loadavg"), nr_running=1, nr_threads=456)
        ok, detail = rg._override_load1_backoff_when_cpu_idle(False, dict(REAL_SLOT_DETAIL), {})
        assert ok is False
        assert detail["check"] == "load1_backoff"


def test_does_not_override_when_runnable_queue_exceeds_cpu_count():
    """Real, low delta-based CPU utilization but the LIVE /proc/loadavg
    runnable snapshot shows real run-queue contention right now (nr_running
    > cpu_count) -- the safety backstop must still refuse."""
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rg_l1_5", _fixture_env(d))
        _write_loadavg(os.path.join(d, "loadavg"), nr_running=REAL_CPU_COUNT + 1, nr_threads=456)
        metrics = {"cpu": 6.5}
        ok, detail = rg._override_load1_backoff_when_cpu_idle(False, dict(REAL_SLOT_DETAIL), metrics)
        assert ok is False
        assert detail["check"] == "load1_backoff"


def test_never_overrides_load1_unreadable():
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rg_l1_6", _fixture_env(d))
        _write_loadavg(os.path.join(d, "loadavg"), nr_running=1, nr_threads=456)
        detail = {"check": "load1_unreadable"}
        ok, out = rg._override_load1_backoff_when_cpu_idle(False, detail, {"cpu": 1.0})
        assert ok is False
        assert out is detail


def test_never_overrides_other_real_gates():
    """cap_exhausted / mem_backoff / swap_backoff / mem_hard_ceiling /
    swap_hard_ceiling must all pass through completely unchanged -- this
    override is narrowly scoped to load1_backoff only."""
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rg_l1_7", _fixture_env(d))
        _write_loadavg(os.path.join(d, "loadavg"), nr_running=1, nr_threads=456)
        for other_detail in (
            {"check": "cap_exhausted", "running_worker_count": 5, "cap": 5},
            {"check": "mem_backoff", "mem_used_pct": 0.85, "threshold_pct": 0.8},
            {"check": "swap_backoff", "swap_used_pct": 0.85, "threshold_pct": 0.8},
            {"check": "mem_hard_ceiling", "mem_used_pct": 0.995, "threshold_pct": 0.99},
        ):
            ok, detail = rg._override_load1_backoff_when_cpu_idle(False, other_detail, {"cpu": 1.0})
            assert ok is False
            assert detail is other_detail


def test_passthrough_when_slot_already_ok():
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rg_l1_8", _fixture_env(d))
        ok, detail = rg._override_load1_backoff_when_cpu_idle(True, {"check": "ok"}, {"cpu": 50.0})
        assert ok is True
        assert detail == {"check": "ok"}


def test_never_overrides_when_proc_loadavg_unreadable():
    """Real /proc/loadavg read failure must fail OPEN to the original block
    -- never assume idle when the safety-backstop signal itself can't be
    read."""
    with tempfile.TemporaryDirectory() as d:
        rg = _load_rg("rg_l1_9", _fixture_env(d))
        # Deliberately do not write the loadavg fixture file.
        ok, detail = rg._override_load1_backoff_when_cpu_idle(False, dict(REAL_SLOT_DETAIL), {"cpu": 6.5})
        assert ok is False
        assert detail["check"] == "load1_backoff"


# ---------------------------------------------------------------------------
# 3. dispatch_one() end-to-end -- the override must actually let a real
#    queued row dispatch when CPU is genuinely idle but load1 is inflated by
#    D-state/swap-reclaim, and must NOT when CPU is genuinely saturated.
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


def test_dispatch_one_end_to_end_proceeds_when_cpu_idle_but_load1_inflated():
    """DELIVER: real test proving dispatch proceeds when CPU is idle but
    load1 is inflated by D-state -- the exact real SPEC evidence shape
    (load1=9.4169921875, cpu_count=8, threshold=6.4), with this tick's own
    real /proc/stat sample showing genuine near-idle CPU (90%+ idle) and a
    live /proc/loadavg runnable snapshot confirming no run-queue
    contention."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = dict(_fixture_env(d))
        env["SUPERBOSS_REGISTER_DB"] = scratch_db
        rg = _load_rg("rg_l1_e2e_1", env)
        rg.EMERGENCY_STOP_PATH = os.path.join(d, "EMERGENCY_STOP_never_created")
        rg.STOP_WORK_ORDER_TASK_IDS = ()

        _seed_row(scratch_db, sbr, "test-load1-idle-override-dispatches")

        # Real evidence shape: vmstat showed 90-96% idle -- seed two real
        # /proc/stat samples (cold-start seed, then a real delta) so
        # sample_metrics() computes a genuinely low cpu% for this tick,
        # exactly the way a real live governor tick would.
        stat_path = os.path.join(d, "stat")
        _write_proc_stat(stat_path, user=1000, nice=0, system=200, idle=8000, iowait=100)
        rg.sample_metrics(now=datetime.datetime(2026, 8, 14, 7, 49, 0, tzinfo=datetime.timezone.utc))
        _write_proc_stat(stat_path, user=1050, nice=0, system=220, idle=9270, iowait=140)  # ~93% idle delta

        _write_loadavg(os.path.join(d, "loadavg"), load1=REAL_LOAD1, nr_running=1, nr_threads=456)

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
                    now=datetime.datetime(2026, 8, 14, 7, 49, 30, tzinfo=datetime.timezone.utc))
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)

        assert result["action"] != "deferred", result
        assert result["action"] == "dispatched", result
        mock_spawn.assert_called_once()
        assert any("overrode a load1_backoff calibration defect" in c.args[0] for c in mock_attn.call_args_list), \
            mock_attn.call_args_list


def test_dispatch_one_end_to_end_still_defers_when_cpu_actually_saturated():
    """Same inflated load1 shape, but real CPU utilization this tick is
    genuinely high (a real compute-bound burst, not D-state noise) -- must
    still defer, never dispatch. Confirms the real safety backstop this
    override is required to preserve."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = dict(_fixture_env(d))
        env["SUPERBOSS_REGISTER_DB"] = scratch_db
        rg = _load_rg("rg_l1_e2e_2", env)
        rg.EMERGENCY_STOP_PATH = os.path.join(d, "EMERGENCY_STOP_never_created")
        rg.STOP_WORK_ORDER_TASK_IDS = ()

        umr_id = _seed_row(scratch_db, sbr, "test-load1-still-defers-when-saturated")

        stat_path = os.path.join(d, "stat")
        _write_proc_stat(stat_path, user=1000, nice=0, system=200, idle=8000, iowait=100)
        rg.sample_metrics(now=datetime.datetime(2026, 8, 14, 7, 49, 0, tzinfo=datetime.timezone.utc))
        # Real, genuine CPU-bound delta this time: idle barely advances.
        _write_proc_stat(stat_path, user=2500, nice=0, system=1200, idle=8050, iowait=100)

        _write_loadavg(os.path.join(d, "loadavg"), load1=REAL_LOAD1, nr_running=1, nr_threads=456)

        dc_mod = rg._dispatch_core()
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            with mock.patch.object(dc_mod, "has_free_slot_detail", return_value=(False, dict(REAL_SLOT_DETAIL))), \
                 mock.patch.object(rg, "_perform_spawn") as mock_spawn:
                result = rg.dispatch_one(
                    now=datetime.datetime(2026, 8, 14, 7, 49, 30, tzinfo=datetime.timezone.utc))
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)

        assert result["action"] == "deferred", result
        assert result["slot_detail"]["check"] == "load1_backoff", result
        mock_spawn.assert_not_called()
        conn = _new_conn(scratch_db)
        row = conn.execute("SELECT status FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
        conn.close()
        assert row["status"] == "queued", row["status"]


def test_dispatch_one_end_to_end_still_defers_when_runnable_queue_contended():
    """Same inflated load1 shape and low delta-based cpu%, but the LIVE
    /proc/loadavg runnable snapshot shows real contention right now -- must
    still defer. Confirms the independent second backstop signal."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _schema_helpers()
        _seed_scratch_db(scratch_db, sbr)
        env = dict(_fixture_env(d))
        env["SUPERBOSS_REGISTER_DB"] = scratch_db
        rg = _load_rg("rg_l1_e2e_3", env)
        rg.EMERGENCY_STOP_PATH = os.path.join(d, "EMERGENCY_STOP_never_created")
        rg.STOP_WORK_ORDER_TASK_IDS = ()

        umr_id = _seed_row(scratch_db, sbr, "test-load1-still-defers-when-runnable-contended")

        stat_path = os.path.join(d, "stat")
        _write_proc_stat(stat_path, user=1000, nice=0, system=200, idle=8000, iowait=100)
        rg.sample_metrics(now=datetime.datetime(2026, 8, 14, 7, 49, 0, tzinfo=datetime.timezone.utc))
        _write_proc_stat(stat_path, user=1050, nice=0, system=220, idle=9270, iowait=140)  # ~93% idle delta

        _write_loadavg(os.path.join(d, "loadavg"), load1=REAL_LOAD1, nr_running=REAL_CPU_COUNT + 3, nr_threads=456)

        dc_mod = rg._dispatch_core()
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        try:
            with mock.patch.object(dc_mod, "has_free_slot_detail", return_value=(False, dict(REAL_SLOT_DETAIL))), \
                 mock.patch.object(rg, "_perform_spawn") as mock_spawn:
                result = rg.dispatch_one(
                    now=datetime.datetime(2026, 8, 14, 7, 49, 30, tzinfo=datetime.timezone.utc))
        finally:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)

        assert result["action"] == "deferred", result
        assert result["slot_detail"]["check"] == "load1_backoff", result
        mock_spawn.assert_not_called()
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
