#!/usr/bin/env python3
"""Real tests for pm_cycle_precheck.py (UMR-20260806-035541, Owner directive
"real PM cycle scope" -- item 3's new full-cycle gather script). Every test
uses a real, isolated, temp-file SQLite database seeded with the real
schema -- never the live production database. DB_PATH is resolved once at
sbr-module-import time (resolve_superboss_db_path()), so each test seeds its
own scratch DB file BEFORE pointing SUPERBOSS_REGISTER_DB at it and loading a
fresh sbr module instance -- no test shares another's DB_PATH.
"""
import importlib.util
import os
import sqlite3
import subprocess
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
PM_CYCLE_PRECHECK_PATH = os.path.join(SCRIPTS_DIR, "pm_cycle_precheck.py")
SBR_PATH = os.path.join(SCRIPTS_DIR, "superboss-register.py")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_and_load_sbr(db_path):
    """Seeds a real, minimal-but-valid scratch DB (umr_tasks table required
    by resolve_superboss_db_path()'s own check 4d), points
    SUPERBOSS_REGISTER_DB at it, then loads a fresh superboss-register.py
    module instance so its module-level DB_PATH binds to the scratch file."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    bootstrap = _load("sbr_bootstrap", SBR_PATH)
    bootstrap._ensure_umr_table(conn)
    bootstrap._ensure_ocid_canonical_registry_table(conn)
    conn.commit()
    conn.close()
    os.environ["SUPERBOSS_REGISTER_DB"] = db_path
    try:
        return _load("sbr_scratch", SBR_PATH)
    finally:
        del os.environ["SUPERBOSS_REGISTER_DB"]


def test_render_report_text_covers_all_sections_and_regression_flag():
    pcp = _load("pcp_render", PM_CYCLE_PRECHECK_PATH)
    report = {
        "generated_at": "2026-08-06T00:00:00+00:00",
        "server_health": {
            "ram_swap": {}, "load_average": {}, "dispatch_tick": {"dispatch_tick_active": True},
            "workers": {"parallel_worker_count": 2}, "stuck_tasks": {"stuck_task_count": 0},
            "tmux": {"tmux_session_alive": True}, "emergency_stop": {"emergency_stop_present": False},
            "db_integrity": {"db_integrity_ok": True},
        },
        "dispatch_tick_deltas": {
            "prior_cycle_ts": "2026-08-05T23:00:00+00:00", "status_counts": {"queued": 2, "completed": 5},
            "total_tasks_since_last_cycle": 7,
        },
        "duplication_precheck": None,
        "pr_states": None,
        "ocid_068_regression": {
            "resolver_present": True, "canonical_registry_row_count": 69,
            "canonical_registry_row_count_expected": 69, "canonical_registry_row_count_ok": True,
            "guardrail_prs": [{"pr": 26, "rule": "Rule 1", "still_ancestor_of_origin_main": True}],
            "regression_detected": False,
        },
    }
    text = pcp.render_report_text(report)
    assert "PM CYCLE PRECHECK" in text
    assert "Server health" in text
    assert "Dispatch tick results since last PM cycle" in text
    assert "skipped (no --search-term given)" in text
    assert "skipped (no --pr-numbers given)" in text
    assert "OCID-068 regression checks" in text
    assert "REGRESSION DETECTED: False" in text


def test_gather_dispatch_tick_deltas_counts_since_prior_snapshot():
    pmr = _load("pmr_deltas", os.path.join(SCRIPTS_DIR, "generate_pm_report_v3.py"))
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_and_load_sbr(db_path)
        conn = sbr._connect()
        sbr.upsert_umr_task(conn, {"task_identity": "old-task", "tier": 1, "status": "completed", "source_trigger": "t"})
        conn.commit()
        conn.close()

        # No prior pm_report_snapshots row -> counts every real row.
        result = _load("pcp_deltas1", PM_CYCLE_PRECHECK_PATH).gather_dispatch_tick_deltas(pmr, sbr)
        assert result["prior_cycle_found"] is False
        assert result["total_tasks_since_last_cycle"] == 1
        assert result["status_counts"] == {"completed": 1}


def test_gather_ocid_068_regression_checks_flags_row_count_mismatch():
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_and_load_sbr(db_path)
        pcp = _load("pcp_ocid1", PM_CYCLE_PRECHECK_PATH)
        result = pcp.gather_ocid_068_regression_checks(sbr)
        assert result["resolver_present"] is True
        assert result["canonical_registry_row_count"] == 0
        assert result["canonical_registry_row_count_expected"] == 69
        assert result["canonical_registry_row_count_ok"] is False
        assert result["regression_detected"] is True


def test_gather_ocid_068_regression_checks_ok_when_row_count_matches():
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "scratch.sqlite")
        sbr = _seed_and_load_sbr(db_path)
        conn = sbr._connect()
        for i in range(1, 70):
            sbr.upsert_ocid_canonical_registry(
                conn, f"OCID-{i:03d}", canonical_umr_id=None, status="not_applicable",
                all_umr_ids=[], evidence={}, not_found=True,
            )
        conn.commit()
        conn.close()
        pcp = _load("pcp_ocid2", PM_CYCLE_PRECHECK_PATH)
        result = pcp.gather_ocid_068_regression_checks(sbr)
        assert result["canonical_registry_row_count"] == 69
        assert result["canonical_registry_row_count_ok"] is True


def test_resolve_repo_root_prefers_own_git_checkout():
    pcp = _load("pcp_repo_root", PM_CYCLE_PRECHECK_PATH)
    root = pcp.resolve_repo_root()
    assert os.path.exists(os.path.join(root, ".git"))


def test_gather_pr_states_returns_none_when_no_numbers_given():
    pcp = _load("pcp_pr_none", PM_CYCLE_PRECHECK_PATH)
    assert pcp.gather_pr_states(None, "some/repo") is None
    assert pcp.gather_pr_states([], "some/repo") is None


def test_gather_pr_states_reports_error_for_missing_gh_binary():
    pcp = _load("pcp_pr_err", PM_CYCLE_PRECHECK_PATH)
    orig_run = pcp.run_cmd
    pcp.run_cmd = lambda argv, timeout=30: (127, "", "gh: command not found")
    try:
        result = pcp.gather_pr_states(["1"], "some/repo")
    finally:
        pcp.run_cmd = orig_run
    assert result[0]["ok"] is False
    assert "not found" in result[0]["error"]


if __name__ == "__main__":
    test_render_report_text_covers_all_sections_and_regression_flag()
    test_gather_dispatch_tick_deltas_counts_since_prior_snapshot()
    test_gather_ocid_068_regression_checks_flags_row_count_mismatch()
    test_gather_ocid_068_regression_checks_ok_when_row_count_matches()
    test_resolve_repo_root_prefers_own_git_checkout()
    test_gather_pr_states_returns_none_when_no_numbers_given()
    test_gather_pr_states_reports_error_for_missing_gh_binary()
    print("PASS: all pm_cycle_precheck tests")
