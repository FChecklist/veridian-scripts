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
            status TEXT NOT NULL DEFAULT 'open', closed_ts TEXT, closed_by TEXT, closed_note TEXT
        );
        CREATE TABLE pm_child_umr_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, proposed_ts TEXT NOT NULL, title TEXT NOT NULL,
            issue TEXT NOT NULL, proposed_action TEXT NOT NULL, proposed_by TEXT NOT NULL,
            related_umr TEXT, child_umr_id TEXT, status TEXT NOT NULL DEFAULT 'proposed',
            decided_ts TEXT, decided_by TEXT, decision TEXT, decision_note TEXT,
            completed_ts TEXT, completed_by TEXT, completion_commit TEXT,
            completion_file_path TEXT, completion_evidence TEXT
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
        "INSERT INTO pm_decisions_pending (opened_ts, title, detail, related_umr, status) VALUES "
        "('2026-08-05T00:00:00+00:00', 'synthetic open decision', 'synthetic detail', 'UMR-x', 'open')"
    )
    conn.execute(
        "INSERT INTO pm_child_umr_proposals (proposed_ts, title, issue, proposed_action, proposed_by, "
        "related_umr, child_umr_id, status) VALUES ('2026-08-06T00:00:00+00:00', "
        "'synthetic open proposal', 'synthetic issue text', 'synthetic proposed action text', "
        "'test-agent', 'UMR-x', 'UMR-20260806-000000-fake', 'proposed')"
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

    def _ensure_pm_child_umr_proposals_table(conn):
        pass  # real table already created by _build_fake_db above -- true no-op here

    def get_open_child_umr_proposals(conn):
        rows = conn.execute(
            "SELECT id, proposed_ts, title, issue, proposed_action, proposed_by, related_umr, "
            "child_umr_id, status FROM pm_child_umr_proposals WHERE status IN ('proposed', 'redirected') "
            "ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    mod._connect = _connect
    mod._write_lock = _write_lock
    mod._ensure_pm_child_umr_proposals_table = _ensure_pm_child_umr_proposals_table
    mod.get_open_child_umr_proposals = get_open_child_umr_proposals
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
        "8. PM CHILD-UMR PROPOSALS AWAITING DECISION",
        "PLACEHOLDER",
        "synthetic failing evidence",
        "synthetic open decision",
        "synthetic open proposal",
    ]:
        assert expected in text, f"missing expected section/content: {expected!r}"

    assert report["ocid_020_gtm_section"]["gtm_fail_count"] == 1
    assert report["ocid_020_gtm_section"]["gtm_pass_count"] == 1
    assert report["gtm_readiness"]["is_placeholder"] is True
    assert report["header_status"]["stuck_tasks"]["stuck_task_count"] == 2

    pm.write_report_files(text)
    pm.write_snapshot_row(fake_sbr, report)

    conn = sqlite3.connect(db_path)
    row_count = conn.execute("SELECT COUNT(*) FROM pm_report_snapshots").fetchone()[0]
    conn.close()
    assert row_count == 1
    assert os.path.exists(str(tmp_path / "pm-report-latest.txt"))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
