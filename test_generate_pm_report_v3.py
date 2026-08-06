#!/usr/bin/env python3
"""Real tests for generate_pm_report_v3.py's deterministic parsing/
threshold/delta logic, against synthetic/mocked inputs only. None of these
require a live server -- no real /proc/meminfo, no real systemctl, no real
superboss-register.sqlite. Run with: python3 -m pytest test_generate_pm_report_v3.py -q

Per the parent task's own minimal-bar redirect: this is a real but
deliberately non-exhaustive smoke-level suite (pure-function unit checks on
the parsing/threshold/delta helpers + one end-to-end run against a fully
synthetic temp DB and monkeypatched subprocess calls), not exhaustive
coverage of every branch.
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = importlib.util.spec_from_file_location("generate_pm_report_v3", os.path.join(HERE, "generate_pm_report_v3.py"))
pm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pm)


# ---------------------------------------------------------------------------
# Pure parsing functions
# ---------------------------------------------------------------------------
def test_parse_meminfo_basic():
    text = (
        "MemTotal:       15982916 kB\n"
        "MemFree:          298024 kB\n"
        "MemAvailable:   10896392 kB\n"
        "SwapTotal:       4194300 kB\n"
        "SwapFree:         220000 kB\n"
    )
    result = pm.parse_meminfo(text)
    assert result["mem_total_mb"] == 15982916 // 1024
    assert result["mem_available_mb"] == 10896392 // 1024
    assert result["swap_total_mb"] == 4194300 // 1024
    assert result["swap_free_mb"] == 220000 // 1024
    assert result["swap_free_pct"] == pytest.approx((220000 / 4194300) * 100, abs=0.01)


def test_parse_meminfo_no_swap_configured():
    text = "MemTotal: 1000 kB\nMemAvailable: 500 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n"
    result = pm.parse_meminfo(text)
    # No swap at all should not be treated as "0% free" (which would
    # falsely trip the low-swap open issue on a box with no swap).
    assert result["swap_free_pct"] == 100.0


def test_parse_loadavg():
    result = pm.parse_loadavg("12.63 13.41 14.90 2/1241 3413903\n")
    assert result["load_1min"] == 12.63
    assert result["load_5min"] == 13.41
    assert result["load_15min"] == 14.90


def test_parse_dispatch_tick_timer_active_true():
    stdout = (
        "NEXT ... LAST ... PASSED UNIT ACTIVATES\n"
        "Wed 2026-08-05 18:42:12 UTC 3min veridian-cron-dispatch-tick.timer veridian-cron-dispatch-tick.service\n"
        "1 timers listed.\n"
    )
    assert pm.parse_dispatch_tick_timer_active(stdout, "veridian-cron-dispatch-tick.timer") is True


def test_parse_dispatch_tick_timer_active_false():
    stdout = "NEXT LEFT LAST PASSED UNIT ACTIVATES\n\n0 timers listed.\n"
    assert pm.parse_dispatch_tick_timer_active(stdout, "veridian-cron-dispatch-tick.timer") is False


def test_parse_worker_count_summary_line():
    stdout = (
        "  UNIT LOAD ACTIVE SUB DESCRIPTION\n"
        "  veridian-worker@task-a.service loaded active running foo\n"
        "  veridian-worker@task-b.service loaded active running bar\n"
        "\n2 loaded units listed.\n"
    )
    assert pm.parse_worker_count(stdout) == 2


def test_parse_worker_count_zero():
    stdout = "0 loaded units listed.\n"
    assert pm.parse_worker_count(stdout) == 0


# ---------------------------------------------------------------------------
# classify_passed -- must match the real, already-merged
# gtm_check_production_readiness_audit.py's classify() behavior exactly.
# ---------------------------------------------------------------------------
def test_classify_passed():
    assert pm.classify_passed(1) == "pass"
    assert pm.classify_passed(0) == "fail"
    assert pm.classify_passed(None) == "blocked_or_pending"


# ---------------------------------------------------------------------------
# compute_readiness_bucket -- must ALWAYS be the labeled placeholder,
# regardless of input, and must never silently start returning a real
# looking score.
# ---------------------------------------------------------------------------
def test_readiness_bucket_is_always_placeholder():
    for fake_gtm in (
        {},
        {"gtm_pass_count": 25, "gtm_fail_count": 0, "gtm_blocked_or_pending_count": 0},
        {"gtm_pass_count": 0, "gtm_fail_count": 25, "gtm_blocked_or_pending_count": 0},
    ):
        result = pm.compute_readiness_bucket(fake_gtm)
        assert result["is_placeholder"] is True
        assert result["bucket"].startswith("NOT_READY")
        assert "placeholder" in result["bucket"].lower()


# ---------------------------------------------------------------------------
# compute_deltas -- real +N / -N / unchanged logic
# ---------------------------------------------------------------------------
def test_compute_deltas_no_prior_row():
    current = {f: 1 for f in pm.DELTA_FIELDS}
    deltas = pm.compute_deltas(None, current)
    assert all("new (no prior" in v for v in deltas.values())


def test_compute_deltas_numeric_and_bool():
    prior = {f: 5 for f in pm.DELTA_FIELDS}
    current = dict(prior)
    current["gtm_pass_count"] = 8
    current["gtm_fail_count"] = 2
    current["mem_available_mb"] = 5  # unchanged
    # Realistic types: the prior row comes back from sqlite as a stored
    # INTEGER (0/1), the current value is a real Python bool from the live
    # check -- either being a bool must trigger the boolean formatting path.
    prior["dispatch_tick_active"] = 0
    current["dispatch_tick_active"] = True
    deltas = pm.compute_deltas(prior, current)
    assert deltas["gtm_pass_count"] == "+3"
    assert deltas["gtm_fail_count"] == "-3"
    assert deltas["mem_available_mb"] == "unchanged"
    assert deltas["dispatch_tick_active"] == "False -> True"


# ---------------------------------------------------------------------------
# build_open_issues -- pure threshold/rule logic
# ---------------------------------------------------------------------------
def test_open_issues_gtm_fail_cites_evidence_verbatim():
    gtm_section = {
        "categories": [
            {"category_index": 3, "category_name": "security audit", "ocid_number": "OCID-020",
             "state": "fail", "evidence_summary": "REAL EVIDENCE TEXT HERE"},
            {"category_index": 1, "category_name": "architecture audit", "ocid_number": "OCID-020",
             "state": "pass", "evidence_summary": "fine"},
        ]
    }
    db_integrity = {"db_integrity_ok": True}
    ram_swap = {"swap_free_pct": 50.0}
    load_avg = {"load_1min": 1.0}
    issues = pm.build_open_issues(gtm_section, db_integrity, ram_swap, load_avg)
    assert len(issues) == 1
    assert issues[0]["kind"] == "gtm_category_failed"
    assert issues[0]["root_cause"] == "REAL EVIDENCE TEXT HERE"


def test_open_issues_thresholds():
    gtm_section = {"categories": []}
    db_integrity = {"db_integrity_ok": False, "integrity_check_rows": ["*** in database main ***\nrow 1 missing"]}
    ram_swap = {"swap_free_pct": pm.SWAP_FREE_PCT_WARN_THRESHOLD - 0.1}
    load_avg = {"load_1min": pm.LOAD_1MIN_WARN_THRESHOLD + 0.1}
    issues = pm.build_open_issues(gtm_section, db_integrity, ram_swap, load_avg)
    kinds = {i["kind"] for i in issues}
    assert kinds == {"db_integrity_check_failed", "swap_free_low", "load_average_high"}


def test_open_issues_no_issues_when_all_healthy():
    gtm_section = {"categories": []}
    db_integrity = {"db_integrity_ok": True}
    ram_swap = {"swap_free_pct": 99.0}
    load_avg = {"load_1min": 0.5}
    assert pm.build_open_issues(gtm_section, db_integrity, ram_swap, load_avg) == []


# ---------------------------------------------------------------------------
# End-to-end smoke test: fully synthetic superboss_register-shaped module +
# temp sqlite DB with the real schema, monkeypatched subprocess/paths.
# Proves main()'s wiring runs start-to-finish and produces all 7 sections,
# without touching any real server state.
# ---------------------------------------------------------------------------
def _build_fake_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE gtm_certification_categories (
            category_index INTEGER PRIMARY KEY, category_name TEXT, ocid_number TEXT,
            parent_umr_id TEXT, child_umr_id TEXT, passed INTEGER, evidence_summary TEXT,
            evidence_json TEXT, fix_commit TEXT, fix_file_path TEXT, fix_pr_number INTEGER,
            validated_at TEXT, created_at TEXT, last_updated_at TEXT
        );
        CREATE TABLE ocid_canonical_registry (
            ocid_number TEXT PRIMARY KEY, status TEXT, is_fully_complete INTEGER
        );
        CREATE TABLE umr_tasks (umr_id TEXT PRIMARY KEY, status TEXT);
        CREATE TABLE pm_report_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
            gtm_pass_count INTEGER, gtm_fail_count INTEGER, gtm_blocked_count INTEGER,
            gtm_pending_count INTEGER, mem_available_mb INTEGER, swap_free_pct REAL,
            load_1min REAL, load_5min REAL, load_15min REAL, dispatch_tick_active INTEGER,
            parallel_worker_count INTEGER, stuck_task_count INTEGER, tmux_session_alive INTEGER,
            emergency_stop_present INTEGER, db_integrity_ok INTEGER, umr_tasks_total INTEGER,
            ocid_canonical_registry_total INTEGER, report_json TEXT NOT NULL
        );
        CREATE TABLE pm_decisions_pending (
            id INTEGER PRIMARY KEY AUTOINCREMENT, opened_ts TEXT NOT NULL, title TEXT NOT NULL,
            detail TEXT NOT NULL, options_json TEXT, recommended_option TEXT, related_umr TEXT,
            status TEXT NOT NULL DEFAULT 'open', closed_ts TEXT, closed_by TEXT, closed_note TEXT,
            decision_type TEXT NOT NULL DEFAULT 'pm_decision', completed_ts TEXT,
            artifact_path TEXT, commit_sha TEXT, evidence TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO gtm_certification_categories (category_index, category_name, ocid_number, "
        "parent_umr_id, passed, evidence_summary, created_at, last_updated_at) VALUES "
        "(3, 'security audit', 'OCID-020', 'UMR-x', 0, 'synthetic failing evidence', 't', 't')"
    )
    conn.execute(
        "INSERT INTO gtm_certification_categories (category_index, category_name, ocid_number, "
        "parent_umr_id, passed, evidence_summary, created_at, last_updated_at) VALUES "
        "(1, 'architecture audit', 'OCID-020', 'UMR-x', 1, 'synthetic passing evidence', 't', 't')"
    )
    conn.execute(
        "INSERT INTO ocid_canonical_registry (ocid_number, status, is_fully_complete) VALUES "
        "('OCID-020', 'merged', 1)"
    )
    conn.execute("INSERT INTO umr_tasks (umr_id, status) VALUES ('UMR-x', 'completed')")
    conn.execute(
        "INSERT INTO pm_decisions_pending (opened_ts, title, detail, related_umr, status, decision_type) VALUES "
        "('2026-08-05T00:00:00+00:00', 'synthetic open decision', 'synthetic detail', 'UMR-x', 'open', 'pm_decision')"
    )
    conn.execute(
        "INSERT INTO pm_decisions_pending (opened_ts, title, detail, related_umr, status, decision_type) VALUES "
        "('2026-08-06T00:00:00+00:00', 'synthetic real issue', 'synthetic AI proposal', "
        "'UMR-20260806-000000-abcd', 'open', 'owner_proposal')"
    )
    conn.commit()
    conn.close()


def _make_fake_sbr_module(db_path):
    """Builds a tiny in-memory module object exposing the same _connect() /
    _write_lock() surface generate_pm_report_v3.py depends on, backed by the
    synthetic temp DB -- never touches the real superboss-register.py or
    the real live database."""
    import types
    import contextlib
    import threading

    mod = types.ModuleType("fake_superboss_register")

    def _connect():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    lock = threading.Lock()

    @contextlib.contextmanager
    def _write_lock():
        with lock:
            yield

    mod._connect = _connect
    mod._write_lock = _write_lock
    return mod


def test_end_to_end_smoke_run(tmp_path, monkeypatch):
    db_path = str(tmp_path / "fake_superboss.sqlite")
    _build_fake_db(db_path)
    fake_sbr = _make_fake_sbr_module(db_path)

    meminfo_path = tmp_path / "meminfo"
    meminfo_path.write_text(
        "MemTotal: 1000000 kB\nMemAvailable: 500000 kB\nSwapTotal: 100000 kB\nSwapFree: 50000 kB\n"
    )
    loadavg_path = tmp_path / "loadavg"
    loadavg_path.write_text("1.0 2.0 3.0 1/100 12345\n")

    heartbeat_path = tmp_path / "STUCK_TASKS_HEARTBEAT.json"
    heartbeat_path.write_text(json.dumps({
        "generated_at": "2026-08-05T18:33:17+00:00",
        "stuck_tasks": [{"task_id": "t1"}, {"task_id": "t2"}],
        "stuck_task_threshold_minutes": 30.0,
        "real_task_counts": {"running": 1},
    }))

    monkeypatch.setattr(pm, "PROC_MEMINFO_PATH", str(meminfo_path))
    monkeypatch.setattr(pm, "PROC_LOADAVG_PATH", str(loadavg_path))
    monkeypatch.setattr(pm, "STUCK_TASKS_HEARTBEAT_PATH", str(heartbeat_path))
    monkeypatch.setattr(pm, "REPORT_LATEST_PATH", str(tmp_path / "pm-report-latest.txt"))
    monkeypatch.setattr(pm, "REPORT_HISTORY_PATH", str(tmp_path / "pm-report-history.log"))

    def fake_run_cmd(argv, timeout=30):
        if argv[:3] == ["systemctl", "--user", "list-timers"]:
            return 0, "... veridian-cron-dispatch-tick.timer ...\n1 timers listed.\n", ""
        if argv[:3] == ["systemctl", "--user", "list-units"]:
            return 0, "1 loaded units listed.\n", ""
        if argv[:2] == ["tmux", "has-session"]:
            return 0, "", ""
        return 1, "", "unmocked command"

    monkeypatch.setattr(pm, "run_cmd", fake_run_cmd)

    class FakeGovernor:
        EMERGENCY_STOP_PATH = str(tmp_path / "EMERGENCY_STOP_nonexistent")

    monkeypatch.setattr(pm, "load_module_from_path", lambda name, path: (
        fake_sbr if "superboss" in path else FakeGovernor()
    ))

    report = pm.build_report(fake_sbr)
    text = pm.render_report_text(report)

    for expected in [
        "1. HEADER / STATUS",
        "2. OCID-020 GTM CERTIFICATION SECTION",
        "3. TEST RESULTS + DETERMINISTIC GATE",
        "4. GO-TO-MARKET READINESS SCORE + RECOMMENDATION",
        "5. IMPLEMENTATION SUMMARY",
        "6. OPEN ISSUES",
        "7. PM DECISION REQUIRED",
        "8. AI PROPOSALS AWAITING PM DECISION",
        "PLACEHOLDER",
        "synthetic failing evidence",
        "synthetic open decision",
        "synthetic real issue",
        "synthetic AI proposal",
        "UMR-20260806-000000-abcd",
    ]:
        assert expected in text, f"missing expected section/content: {expected!r}"

    assert report["ocid_020_gtm_section"]["gtm_fail_count"] == 1
    assert report["ocid_020_gtm_section"]["gtm_pass_count"] == 1
    assert report["gtm_readiness"]["is_placeholder"] is True
    assert report["header_status"]["stuck_tasks"]["stuck_task_count"] == 2

    # Section 7 must show only the plain pm_decision row; Section 8 only the
    # owner_proposal row -- never mixed (Owner standing mandate,
    # task-20260806-034817).
    assert len(report["pm_decisions_pending"]) == 1
    assert report["pm_decisions_pending"][0]["title"] == "synthetic open decision"
    assert len(report["owner_proposals_pending"]) == 1
    assert report["owner_proposals_pending"][0]["issue"] == "synthetic real issue"
    assert report["owner_proposals_pending"][0]["child_umr"] == "UMR-20260806-000000-abcd"

    pm.write_report_files(text)
    pm.write_snapshot_row(fake_sbr, report)

    conn = sqlite3.connect(db_path)
    row_count = conn.execute("SELECT COUNT(*) FROM pm_report_snapshots").fetchone()[0]
    conn.close()
    assert row_count == 1
    assert os.path.exists(str(tmp_path / "pm-report-latest.txt"))


def test_pm_decisions_pending_degrades_gracefully_without_decision_type_column(tmp_path):
    """Owner standing mandate (task-20260806-034817): a DB that predates the
    decision_type migration (superboss-register.py's
    _migrate_pm_decisions_pending_owner_proposal_columns()) must never crash
    this read-only script -- get_pm_decisions_pending() falls back to its
    original unfiltered query, and get_owner_proposals_pending() correctly
    reports zero real proposals rather than raising 'no such column'."""
    db_path = str(tmp_path / "legacy.sqlite")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE pm_decisions_pending (
            id INTEGER PRIMARY KEY AUTOINCREMENT, opened_ts TEXT NOT NULL, title TEXT NOT NULL,
            detail TEXT NOT NULL, options_json TEXT, recommended_option TEXT, related_umr TEXT,
            status TEXT NOT NULL DEFAULT 'open', closed_ts TEXT, closed_by TEXT, closed_note TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO pm_decisions_pending (opened_ts, title, detail, status) VALUES "
        "('2026-08-05T00:00:00+00:00', 'legacy decision', 'legacy detail', 'open')"
    )
    conn.commit()
    conn.close()

    fake_sbr = _make_fake_sbr_module(db_path)
    decisions = pm.get_pm_decisions_pending(fake_sbr)
    proposals = pm.get_owner_proposals_pending(fake_sbr)

    assert not (isinstance(decisions, dict) and "error" in decisions), decisions
    assert len(decisions) == 1 and decisions[0]["title"] == "legacy decision", decisions
    assert proposals == []


# ---------------------------------------------------------------------------
# Sections 9-13 (UMR-20260806-041307-0bfd) -- real unit test coverage of the
# deterministic rule/arithmetic logic in each of the 5 new capabilities.
# Live-server dependencies (subprocess/gh/systemctl) are mocked; the actual
# rule logic under test is real.
# ---------------------------------------------------------------------------

# --- Section 9: DB validation fold-in --------------------------------------
def test_prior_failing_ocids_no_prior_row():
    fail_set, available = pm._prior_failing_ocids(None)
    assert fail_set == set()
    assert available is False


def test_prior_failing_ocids_no_report_json():
    fail_set, available = pm._prior_failing_ocids({"id": 1})
    assert fail_set == set()
    assert available is False


def test_prior_failing_ocids_predates_section():
    prior_row = {"report_json": json.dumps({"umr": "UMR-x"})}
    fail_set, available = pm._prior_failing_ocids(prior_row)
    assert fail_set == set()
    assert available is False


def test_prior_failing_ocids_real_baseline():
    prior_row = {"report_json": json.dumps({
        "ocid_compliance_audit_section": {"failing_ocids": ["OCID-001", "OCID-002"]}
    })}
    fail_set, available = pm._prior_failing_ocids(prior_row)
    assert fail_set == {"OCID-001", "OCID-002"}
    assert available is True


def test_get_ocid_compliance_audit_section_parses_real_shaped_json(monkeypatch):
    fake_stdout = json.dumps({
        "mode": "report", "read_only": True,
        "rows": [
            {"ocid_number": "OCID-001", "audit_passed": True},
            {"ocid_number": "OCID-002", "audit_passed": False},
            {"ocid_number": "OCID-003", "audit_passed": False},
        ],
    })
    monkeypatch.setattr(pm, "run_cmd", lambda argv, timeout=30: (0, fake_stdout, ""))
    prior_row = {"report_json": json.dumps({
        "ocid_compliance_audit_section": {"failing_ocids": ["OCID-002"]}
    })}
    result = pm.get_ocid_compliance_audit_section(prior_row)
    assert result["audit_passed_count"] == 1
    assert result["audit_failed_count"] == 2
    assert result["failing_ocids"] == ["OCID-002", "OCID-003"]
    assert result["prior_baseline_available"] is True
    assert result["newly_failing_ocids"] == ["OCID-003"]


def test_get_ocid_compliance_audit_section_subprocess_failure(monkeypatch):
    monkeypatch.setattr(pm, "run_cmd", lambda argv, timeout=30: (1, "", "boom"))
    result = pm.get_ocid_compliance_audit_section(None)
    assert result["error"] == "boom"


# --- Section 10: 10-report trend analysis -----------------------------------
def test_compute_trend_for_series_insufficient_data():
    assert pm.compute_trend_for_series([])["trend"] == "insufficient_data"
    assert pm.compute_trend_for_series([5.0])["trend"] == "insufficient_data"


def test_compute_trend_for_series_stable_within_tolerance():
    # first half avg 10, second half avg 10.3 -> 3% change, under 5% tolerance
    result = pm.compute_trend_for_series([10, 10, 10.3, 10.3])
    assert result["first_half_avg"] == 10.0
    assert result["second_half_avg"] == 10.3
    assert abs(result["pct_change"] - 3.0) < 0.01


def test_compute_trend_for_series_real_directional_change():
    # first half avg 10, second half avg 20 -> +100%, well beyond tolerance
    result = pm.compute_trend_for_series([10, 10, 20, 20])
    assert result["pct_change"] == 100.0


def test_apply_metric_semantics_higher_is_better_up_is_improving():
    raw = {"trend_raw_direction": "up", "rows_used": 4}
    result = pm._apply_metric_semantics("swap_free_pct", dict(raw))
    assert result["trend"] == "improving"


def test_apply_metric_semantics_higher_is_better_down_is_degrading():
    raw = {"trend_raw_direction": "down", "rows_used": 4}
    result = pm._apply_metric_semantics("gtm_pass_count", dict(raw))
    assert result["trend"] == "degrading"


def test_apply_metric_semantics_lower_is_better_up_is_degrading():
    raw = {"trend_raw_direction": "up", "rows_used": 4}
    result = pm._apply_metric_semantics("load_1min", dict(raw))
    assert result["trend"] == "degrading"


def test_apply_metric_semantics_lower_is_better_down_is_improving():
    raw = {"trend_raw_direction": "down", "rows_used": 4}
    result = pm._apply_metric_semantics("load_1min", dict(raw))
    assert result["trend"] == "improving"


def test_apply_metric_semantics_stable_passthrough():
    raw = {"trend_raw_direction": "stable", "rows_used": 4}
    result = pm._apply_metric_semantics("load_1min", dict(raw))
    assert result["trend"] == "stable"


def test_get_trend_analysis_honest_row_count_under_window(tmp_path):
    db_path = str(tmp_path / "snap.sqlite")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE pm_report_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, swap_free_pct REAL,
            load_1min REAL, gtm_pass_count INTEGER
        );
        """
    )
    # Only 3 real rows -- fewer than TREND_WINDOW_SIZE (10). Must be reported
    # honestly, never padded/fabricated up to 10.
    for i, swap in enumerate([50.0, 40.0, 30.0]):
        conn.execute(
            "INSERT INTO pm_report_snapshots (ts, swap_free_pct, load_1min, gtm_pass_count) VALUES (?, ?, ?, ?)",
            (f"t{i}", swap, 1.0, 5),
        )
    conn.commit()
    conn.close()
    fake_sbr = _make_fake_sbr_module(db_path)
    result = pm.get_trend_analysis(fake_sbr)
    assert result["rows_used"] == 3
    assert result["metrics"]["swap_free_pct"]["trend"] == "degrading"  # 50 -> 30, dropping


def test_render_report_text_survives_per_metric_insufficient_data(monkeypatch):
    """Regression test for a real bug caught by independent supervisor
    review (task-20260806-042916, PR #115): render_report_text checked
    `m.get("trend") is None` to detect the insufficient-data case, but
    compute_trend_for_series's insufficient-data dict shape carries
    `"trend": "insufficient_data"` (a string, never None) and omits
    first_half_avg/second_half_avg/pct_change -- so the old check fell into
    the else branch and raised KeyError whenever any one of the 3 tracked
    metrics had fewer than 2 non-null values in its window. A realistic
    near-term production scenario (e.g. shortly after this section first
    ships, or a metric column that is briefly NULL), not a contrived edge
    case.

    Calls the REAL render_report_text() end-to-end (not just the backend
    helpers) against a minimal-but-real report dict shaped exactly like one
    metric hitting insufficient_data, proving the fixed check
    (`m.get("trend") == "insufficient_data"`) never dereferences the
    missing keys."""
    report = {
        "generated_at": "t", "umr": "UMR-x", "parent_umr": "UMR-y", "ocid": "OCID-020",
        "script_version": pm.SCRIPT_VERSION,
        "header_status": {
            "ram_swap": {}, "load_average": {}, "dispatch_tick": {}, "parallel_workers": {},
            "stuck_tasks": {}, "tmux": {}, "emergency_stop": {}, "db_integrity": {},
        },
        "ocid_020_gtm_section": {"categories": []},
        "ocid_canonical_registry_section": {},
        "umr_tasks_section": {},
        "gtm_readiness": {"bucket": "x", "reason": "x", "is_placeholder": True},
        "implementation_summary": {"prior_snapshot_found": False, "deltas": {}},
        "open_issues": [],
        "pm_decisions_pending": [],
        "owner_proposals_pending": [],
        "ocid_compliance_audit_section": {"error": "not exercised in this test"},
        "trend_analysis_section": {
            "error": None,
            "rows_used": 1,
            "window_size_requested": 10,
            "stable_tolerance_pct": 5.0,
            "metrics": {
                # Exactly the real shape compute_trend_for_series() produces
                # for <2 non-null values -- no first_half_avg/second_half_avg/
                # pct_change keys present.
                "swap_free_pct": {"trend": "insufficient_data", "rows_used": 1},
                "load_1min": {"trend": "insufficient_data", "rows_used": 1},
                "gtm_pass_count": {"trend": "insufficient_data", "rows_used": 1},
            },
        },
        "stall_detection_section": {"error": None, "stuck_task_threshold_minutes": 30.0, "tasks": []},
        "collision_detection_section": {
            "collision_detected": False, "tracked_repos": [], "checked_unit_globs": [],
            "all_pr_collision_pairs": [], "all_worker_collision_pairs": [],
            "pr_file_collisions": {"by_repo": {}, "errors": []},
            "worker_umr_collisions": {"errors": []},
        },
        "instruction_quality_section": {"error": None, "total_checked": 0, "pass_count": 0, "failing": []},
    }
    text = pm.render_report_text(report)  # must not raise
    assert "insufficient_data" in text
    assert "10. 10-REPORT TREND ANALYSIS" in text


def test_get_trend_analysis_zero_rows(tmp_path):
    db_path = str(tmp_path / "empty_snap.sqlite")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE pm_report_snapshots (id INTEGER PRIMARY KEY, ts TEXT, swap_free_pct REAL, "
        "load_1min REAL, gtm_pass_count INTEGER)"
    )
    conn.commit()
    conn.close()
    fake_sbr = _make_fake_sbr_module(db_path)
    result = pm.get_trend_analysis(fake_sbr)
    assert result["rows_used"] == 0
    assert result["metrics"] == {}


# --- Section 11: deterministic stall detection ------------------------------
def test_get_stuck_tasks_detail_real_field_names(tmp_path, monkeypatch):
    heartbeat_path = tmp_path / "STUCK_TASKS_HEARTBEAT.json"
    heartbeat_path.write_text(json.dumps({
        "generated_at": "2026-08-06T00:00:00+00:00",
        "stuck_task_threshold_minutes": 30.0,
        "stuck_tasks": [
            {"task_id": "task-a", "blocked_since": "t0", "blocked_minutes": 45.5,
             "last_note": "waiting on review"},
            {"task_id": "task-b", "blocked_since": "t1", "blocked_minutes": 120.0,
             "last_note": "quality gate failing"},
        ],
    }))
    monkeypatch.setattr(pm, "STUCK_TASKS_HEARTBEAT_PATH", str(heartbeat_path))
    result = pm.get_stuck_tasks_detail()
    assert result["error"] is None
    assert result["stuck_task_threshold_minutes"] == 30.0
    assert len(result["tasks"]) == 2
    assert result["tasks"][0] == {"task_id": "task-a", "blocked_minutes": 45.5, "last_note": "waiting on review"}
    assert result["tasks"][1]["task_id"] == "task-b"


def test_get_stuck_tasks_detail_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "STUCK_TASKS_HEARTBEAT_PATH", str(tmp_path / "does_not_exist.json"))
    result = pm.get_stuck_tasks_detail()
    assert result["error"] is not None
    assert result["tasks"] == []


def test_get_stuck_tasks_and_detail_reuse_same_source(tmp_path, monkeypatch):
    """Section 1 summary and Section 11 detail must agree on real
    stuck_task_count from the same real file -- proves shared parsing."""
    heartbeat_path = tmp_path / "STUCK_TASKS_HEARTBEAT.json"
    heartbeat_path.write_text(json.dumps({
        "generated_at": "t", "stuck_task_threshold_minutes": 30.0,
        "stuck_tasks": [{"task_id": "x", "blocked_minutes": 1.0, "last_note": "n"}],
    }))
    monkeypatch.setattr(pm, "STUCK_TASKS_HEARTBEAT_PATH", str(heartbeat_path))
    summary = pm.get_stuck_tasks()
    detail = pm.get_stuck_tasks_detail()
    assert summary["stuck_task_count"] == len(detail["tasks"]) == 1


# --- Section 12: deterministic collision detection --------------------------
def test_parse_running_collision_unit_names():
    stdout = (
        "  UNIT LOAD ACTIVE SUB DESCRIPTION\n"
        "  veridian-worker@task-a.service loaded active running foo\n"
        "  veridian-supervisor@task-b.service loaded active running bar\n"
        "\n2 loaded units listed.\n"
    )
    names = pm.parse_running_collision_unit_names(stdout)
    assert names == ["veridian-worker@task-a.service", "veridian-supervisor@task-b.service"]


def test_extract_umr_ids():
    text = "relates to UMR-20260805-181636-32f2 and also UMR-20260802-165606-4413, see UMR-20260805-181636-32f2 again"
    assert pm.extract_umr_ids(text) == {"UMR-20260805-181636-32f2", "UMR-20260802-165606-4413"}


def test_detect_pr_file_collisions_real_overlap(monkeypatch):
    def fake_run_cmd(argv, timeout=30):
        if argv[:3] == ["gh", "pr", "list"]:
            return 0, json.dumps([{"number": 1, "title": "A"}, {"number": 2, "title": "B"}]), ""
        if argv[:3] == ["gh", "pr", "diff"]:
            pr_num = argv[3]
            if pr_num == "1":
                return 0, "shared_file.py\nonly_in_1.py\n", ""
            if pr_num == "2":
                return 0, "shared_file.py\nonly_in_2.py\n", ""
        return 1, "", "unmocked"
    monkeypatch.setattr(pm, "run_cmd", fake_run_cmd)
    result = pm.detect_pr_file_collisions(repos=("fake-repo",))
    collisions = result["by_repo"]["fake-repo"]["collisions"]
    assert len(collisions) == 1
    assert collisions[0]["shared_files"] == ["shared_file.py"]


def test_detect_pr_file_collisions_no_overlap(monkeypatch):
    def fake_run_cmd(argv, timeout=30):
        if argv[:3] == ["gh", "pr", "list"]:
            return 0, json.dumps([{"number": 1, "title": "A"}, {"number": 2, "title": "B"}]), ""
        if argv[:3] == ["gh", "pr", "diff"]:
            pr_num = argv[3]
            return (0, "only_in_1.py\n", "") if pr_num == "1" else (0, "only_in_2.py\n", "")
        return 1, "", "unmocked"
    monkeypatch.setattr(pm, "run_cmd", fake_run_cmd)
    result = pm.detect_pr_file_collisions(repos=("fake-repo",))
    assert result["by_repo"]["fake-repo"]["collisions"] == []


def test_detect_worker_umr_collisions_real_overlap(tmp_path, monkeypatch):
    unit_a_dir = tmp_path / "unit_a"
    unit_b_dir = tmp_path / "unit_b"
    unit_a_dir.mkdir()
    unit_b_dir.mkdir()
    (unit_a_dir / "prompt.txt").write_text("relates to UMR-20260805-181636-32f2")
    (unit_b_dir / "prompt.txt").write_text("also relates to UMR-20260805-181636-32f2 and UMR-x-only-in-b")

    monkeypatch.setattr(pm, "get_running_collision_units",
                         lambda: ["veridian-worker@a.service", "veridian-worker@b.service"])

    def fake_wd(unit_name):
        return str(unit_a_dir) if "a.service" in unit_name else str(unit_b_dir)
    monkeypatch.setattr(pm, "get_unit_working_directory", fake_wd)

    result = pm.detect_worker_umr_collisions()
    assert len(result["collisions"]) == 1
    assert result["collisions"][0]["shared_umrs"] == ["UMR-20260805-181636-32f2"]


def test_detect_worker_umr_collisions_no_overlap(tmp_path, monkeypatch):
    unit_a_dir = tmp_path / "unit_a"
    unit_b_dir = tmp_path / "unit_b"
    unit_a_dir.mkdir()
    unit_b_dir.mkdir()
    (unit_a_dir / "prompt.txt").write_text("relates to UMR-20260805-181636-32f2")
    (unit_b_dir / "prompt.txt").write_text("relates to UMR-20260802-165606-4413")

    monkeypatch.setattr(pm, "get_running_collision_units",
                         lambda: ["veridian-worker@a.service", "veridian-worker@b.service"])
    monkeypatch.setattr(pm, "get_unit_working_directory",
                         lambda u: str(unit_a_dir) if "a.service" in u else str(unit_b_dir))

    result = pm.detect_worker_umr_collisions()
    assert result["collisions"] == []


def test_get_collision_detection_section_combines_both_sources(monkeypatch):
    monkeypatch.setattr(pm, "detect_pr_file_collisions", lambda repos=None: {
        "by_repo": {"r": {"open_pr_count": 2, "collisions": [{"repo": "r", "pr_a": 1, "pr_b": 2,
                                                                "pr_a_title": "x", "pr_b_title": "y",
                                                                "shared_files": ["f.py"]}]}},
        "errors": [],
    })
    monkeypatch.setattr(pm, "detect_worker_umr_collisions", lambda: {
        "checked_units": [], "collisions": [], "errors": [],
    })
    result = pm.get_collision_detection_section()
    assert result["collision_detected"] is True
    assert len(result["all_pr_collision_pairs"]) == 1
    assert result["all_worker_collision_pairs"] == []


def test_get_collision_detection_section_no_collision(monkeypatch):
    monkeypatch.setattr(pm, "detect_pr_file_collisions", lambda repos=None: {"by_repo": {}, "errors": []})
    monkeypatch.setattr(pm, "detect_worker_umr_collisions", lambda: {
        "checked_units": [], "collisions": [], "errors": [],
    })
    result = pm.get_collision_detection_section()
    assert result["collision_detected"] is False


# --- Section 13: deterministic instruction quality check --------------------
def test_extract_prompt_text_present():
    assert pm.extract_prompt_text(json.dumps({"prompt": "do the thing"})) == "do the thing"


def test_extract_prompt_text_missing_key():
    assert pm.extract_prompt_text(json.dumps({"action": "resume"})) is None


def test_extract_prompt_text_unparseable():
    assert pm.extract_prompt_text("not json") is None
    assert pm.extract_prompt_text(None) is None
    assert pm.extract_prompt_text("") is None


def test_check_instruction_quality_all_pass():
    text = ("Real directive citing UMR-20260805-181636-32f2. Extend generate_pm_report_v3.py "
            "and confirm PR #110 merges.")
    result = pm.check_instruction_quality(text)
    assert result["passed"] is True
    assert result["rule_a_umr_citation_present"] is True
    assert result["rule_b_vague_verbs_absent"] is True
    assert result["rule_c_concrete_completion_present"] is True
    assert result["reasons"] == []


def test_check_instruction_quality_fails_no_umr_citation():
    text = "Extend generate_pm_report_v3.py and confirm PR #110 merges."
    result = pm.check_instruction_quality(text)
    assert result["passed"] is False
    assert result["rule_a_umr_citation_present"] is False


def test_check_instruction_quality_fails_vague_verb():
    text = "UMR-20260805-181636-32f2: please look into generate_pm_report_v3.py."
    result = pm.check_instruction_quality(text)
    assert result["passed"] is False
    assert result["rule_b_vague_verbs_absent"] is False
    assert "look into" in result["reasons"][0]


def test_check_instruction_quality_fails_no_concrete_completion():
    text = "UMR-20260805-181636-32f2: please handle the situation well."
    result = pm.check_instruction_quality(text)
    assert result["passed"] is False
    assert result["rule_c_concrete_completion_present"] is False


def test_check_instruction_quality_empty_prompt():
    result = pm.check_instruction_quality(None)
    assert result["passed"] is False
    assert "no string 'prompt' field" in result["reasons"][0]


def test_check_instruction_quality_pr_reference_satisfies_rule_c_without_file_path():
    text = "UMR-20260805-181636-32f2: confirm pull request #42 is merged before reporting done."
    result = pm.check_instruction_quality(text)
    assert result["rule_c_concrete_completion_present"] is True


def test_get_instruction_quality_section_counts_and_lists_failures(tmp_path):
    db_path = str(tmp_path / "umr.sqlite")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE umr_tasks (umr_id TEXT PRIMARY KEY, ts_submitted TEXT, inputs_json TEXT)")
    good_prompt = json.dumps({"prompt": "UMR-20260805-181636-32f2: extend generate_pm_report_v3.py, cite PR #110."})
    bad_prompt = json.dumps({"prompt": "please look into the general situation"})
    conn.execute("INSERT INTO umr_tasks VALUES ('UMR-good', '2026-08-06T00:00:02', ?)", (good_prompt,))
    conn.execute("INSERT INTO umr_tasks VALUES ('UMR-bad', '2026-08-06T00:00:01', ?)", (bad_prompt,))
    conn.commit()
    conn.close()
    fake_sbr = _make_fake_sbr_module(db_path)
    result = pm.get_instruction_quality_section(fake_sbr)
    assert result["total_checked"] == 2
    assert result["pass_count"] == 1
    assert len(result["failing"]) == 1
    assert result["failing"][0]["umr_id"] == "UMR-bad"


def test_get_instruction_quality_section_honest_denominator_under_20(tmp_path):
    """Only 3 real rows exist -- denominator must honestly be 3, not 20."""
    db_path = str(tmp_path / "umr_small.sqlite")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE umr_tasks (umr_id TEXT PRIMARY KEY, ts_submitted TEXT, inputs_json TEXT)")
    for i in range(3):
        p = json.dumps({"prompt": f"UMR-20260805-181636-32f2: task {i}, see PR #{i}."})
        conn.execute("INSERT INTO umr_tasks VALUES (?, ?, ?)", (f"UMR-{i}", f"t{i}", p))
    conn.commit()
    conn.close()
    fake_sbr = _make_fake_sbr_module(db_path)
    result = pm.get_instruction_quality_section(fake_sbr)
    assert result["total_checked"] == 3
    assert result["pass_count"] == 3


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
