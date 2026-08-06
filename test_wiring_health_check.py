#!/usr/bin/env python3
"""test_wiring_health_check.py -- real regression coverage for
wiring_health_check.py. Same conventions test_pm_cycle_precheck.py already
established for this repo: plain test_*() functions (also runnable
standalone via the __main__ block below), a local _load() importlib helper,
and DB isolation via a scratch sqlite file + SUPERBOSS_REGISTER_DB env
override + a FRESH module instance per test -- never the live production DB.

test_check_gateway_pickup_path_real_end_to_end is a real, live functional
probe (real dispatch-owner-task.sh subprocess, real resource_governor.py
--tick subprocess) -- same "live functional probe, not a mock" convention
OCID_020_CYCLE_DECISION_TIER_BUMP_VERIFICATION_2026-08-05.md and this repo's
other test_*.py files (test_worker_boot_activation_and_resume.py,
test_stuck_task_heartbeat.py) already use for exactly this class of
integration seam.
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


whc = _load("wiring_health_check_under_test", os.path.join(SCRIPT_DIR, "wiring_health_check.py"))


def _seed_and_load_sbr(seed_tables=("umr_tasks", "capability_registry", "wiring_registry",
                                     "external_agent_dispatch", "pm_decisions_pending")):
    """Same isolation convention as test_pm_cycle_precheck.py's own
    _seed_and_load_sbr(): scratch sqlite file, SUPERBOSS_REGISTER_DB env
    override, fresh module instance so DB_PATH binds to the scratch file --
    never the live production DB. `seed_tables` controls which real
    _ensure_*_table() DDL functions get called, so a test can exercise the
    "table genuinely missing" failure path too."""
    tmpdir = tempfile.mkdtemp(prefix="wiring-health-check-test-")
    db_path = os.path.join(tmpdir, "scratch.sqlite")
    bootstrap = _load("sbr_bootstrap_for_test", whc.SBR_PATH)
    raw = sqlite3.connect(db_path)
    raw.row_factory = sqlite3.Row
    if "umr_tasks" in seed_tables:
        bootstrap._ensure_umr_table(raw)
    if "capability_registry" in seed_tables:
        bootstrap._ensure_capability_registry_table(raw)
    if "wiring_registry" in seed_tables:
        bootstrap._ensure_wiring_registry_table(raw)
    if "external_agent_dispatch" in seed_tables:
        bootstrap._ensure_external_agent_dispatch_table(raw)
    if "pm_decisions_pending" in seed_tables:
        bootstrap._ensure_pm_decisions_pending_table(raw)
    raw.commit()
    raw.close()

    prior = os.environ.get("SUPERBOSS_REGISTER_DB")
    os.environ["SUPERBOSS_REGISTER_DB"] = db_path
    try:
        sbr = _load(f"sbr_scratch_{os.path.basename(tmpdir)}", whc.SBR_PATH)
    finally:
        if prior is None:
            os.environ.pop("SUPERBOSS_REGISTER_DB", None)
        else:
            os.environ["SUPERBOSS_REGISTER_DB"] = prior
    return sbr, db_path, tmpdir


def _cleanup(tmpdir):
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_run_cmd_never_raises_on_missing_binary():
    rc, out, err = whc.run_cmd(["/no/such/real/binary/here", "--flag"], timeout=5)
    assert rc == 127
    assert out == ""
    assert "no/such/real/binary" in err or err  # some real, non-empty error string


def test_run_cmd_never_raises_on_timeout():
    rc, out, err = whc.run_cmd(["sleep", "5"], timeout=1)
    assert rc == 124


def test_check_registries_reachable_all_present_passes():
    sbr, db_path, tmpdir = _seed_and_load_sbr()
    try:
        result = whc.check_registries_reachable(sbr)
        assert result["name"] == "registries_reachable"
        assert result["passed"] is True
        for table in ("capability_registry", "wiring_registry", "umr_tasks"):
            assert result["detail"]["tables"][table]["exists"] is True
            assert result["detail"]["tables"][table]["error"] is None
        assert "agent_id" in result["detail"]["note"]
    finally:
        _cleanup(tmpdir)


def test_check_registries_reachable_missing_table_fails_honestly():
    sbr, db_path, tmpdir = _seed_and_load_sbr(seed_tables=("umr_tasks",))
    try:
        result = whc.check_registries_reachable(sbr)
        assert result["passed"] is False
        assert result["detail"]["tables"]["capability_registry"]["exists"] is False
        assert result["detail"]["tables"]["wiring_registry"]["exists"] is False
        assert result["detail"]["tables"]["umr_tasks"]["exists"] is True
    finally:
        _cleanup(tmpdir)


def test_check_external_agent_dispatch_table_present_and_eligible_dry_run():
    sbr, db_path, tmpdir = _seed_and_load_sbr()
    try:
        result = whc.check_external_agent_dispatch(sbr)
        assert result["name"] == "external_agent_dispatch_reachable"
        assert result["passed"] is True
        assert result["detail"]["table_exists"] is True
        assert result["detail"]["eligibility_dry_run"]["eligible"] is True
        assert result["detail"]["eligibility_dry_run"]["reasons"] == []
    finally:
        _cleanup(tmpdir)


def test_check_external_agent_dispatch_missing_table_fails_honestly():
    sbr, db_path, tmpdir = _seed_and_load_sbr(seed_tables=("umr_tasks",))
    try:
        result = whc.check_external_agent_dispatch(sbr)
        assert result["passed"] is False
        assert result["detail"]["table_exists"] is False
    finally:
        _cleanup(tmpdir)


def test_check_pm_report_freshness_missing_file(monkeypatch, tmp_path=None):
    missing_path = os.path.join(tempfile.mkdtemp(), "no-such-pm-report-latest.txt")
    monkeypatch.setattr(whc, "REPORT_LATEST_PATH", missing_path)
    result = whc.check_pm_report_freshness()
    assert result["passed"] is False
    assert "does not exist" in result["detail"]["reason"]


def test_check_pm_report_freshness_stale_file_fails(monkeypatch):
    tmpdir = tempfile.mkdtemp()
    path = os.path.join(tmpdir, "pm-report-latest.txt")
    with open(path, "w") as f:
        f.write("generated_at: 2020-01-01T00:00:00+00:00\n")
    monkeypatch.setattr(whc, "REPORT_LATEST_PATH", path)
    result = whc.check_pm_report_freshness()
    assert result["passed"] is False
    assert result["detail"]["generated_at_age_minutes"] > whc.PM_REPORT_FRESHNESS_MAX_MINUTES


def test_has_open_wiring_health_decision_and_record_failures_dedup():
    sbr, db_path, tmpdir = _seed_and_load_sbr()
    try:
        conn = sbr._connect()
        assert whc._has_open_wiring_health_decision(conn, "fake_test") is False
        conn.close()

        fake_results = [{"name": "fake_test", "passed": False, "detail": {"reason": "synthetic"}}]
        first = whc.record_failures(sbr, fake_results)
        assert first == [{"test": "fake_test", "action": "opened", "decision_id": first[0]["decision_id"]}]

        second = whc.record_failures(sbr, fake_results)
        assert second == [{"test": "fake_test", "action": "already_open_skipped"}]

        conn = sbr._connect()
        rows = conn.execute(
            "SELECT title, status, decision_type FROM pm_decisions_pending"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["title"] == "WIRING HEALTH CHECK FAILURE: fake_test"
        assert rows[0]["status"] == "open"
        assert rows[0]["decision_type"] == whc.DECISION_TYPE
    finally:
        _cleanup(tmpdir)


def test_record_failures_no_op_when_all_passed():
    sbr, db_path, tmpdir = _seed_and_load_sbr()
    try:
        recorded = whc.record_failures(sbr, [{"name": "ok_test", "passed": True, "detail": {}}])
        assert recorded == []
    finally:
        _cleanup(tmpdir)


def test_run_all_checks_no_record_pm_decisions_writes_nothing(monkeypatch):
    sbr, db_path, tmpdir = _seed_and_load_sbr()
    try:
        # Force every real gathering function to report a clean failure
        # (not an exception) without doing real subprocess/network work, so
        # this test stays fast and isolated -- the real end-to-end gateway
        # probe is covered separately below.
        monkeypatch.setattr(whc, "check_gateway_pickup_path",
                             lambda sbr: {"name": "gateway_pickup_path", "passed": False, "detail": {}})
        monkeypatch.setattr(whc, "check_pm_report_freshness",
                             lambda: {"name": "pm_report_freshness", "passed": False, "detail": {}})
        report = whc.run_all_checks(sbr=sbr, record_pm_decisions=False)
        assert report["overall_pass"] is False
        assert report["recorded_decisions"] == []
        conn = sbr._connect()
        n = conn.execute("SELECT COUNT(*) AS c FROM pm_decisions_pending").fetchone()["c"]
        conn.close()
        assert n == 0
    finally:
        _cleanup(tmpdir)


def test_check_gateway_pickup_path_real_end_to_end():
    """Real, live functional probe -- exercises dispatch-owner-task.sh and
    `resource_governor.py --tick` as real subprocesses against an isolated
    scratch DB (never the live production queue -- see
    check_gateway_pickup_path()'s own docstring for why). Requires the same
    real environment (python3, bash, a real filesystem under
    VERIDIAN_SCRIPTS_DIR) every other real subprocess-driving test in this
    repo already assumes."""
    sbr = _load("sbr_for_gateway_probe", whc.SBR_PATH)
    result = whc.check_gateway_pickup_path(sbr)
    assert result["name"] == "gateway_pickup_path"
    assert result["passed"] is True, result["detail"]
    assert result["detail"]["outcome"] in ("dispatched", "resource_deferred:deferred", "resource_deferred:frozen")


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in list(globals().items())
              if name.startswith("test_") and callable(fn)]
    failures = 0
    for name, fn in tests:
        try:
            if "monkeypatch" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                print(f"SKIP (needs pytest monkeypatch): {name}")
                continue
            fn()
            print(f"PASS: {name}")
        except Exception as e:
            failures += 1
            print(f"FAIL: {name}: {type(e).__name__}: {e}")
    sys.exit(1 if failures else 0)
