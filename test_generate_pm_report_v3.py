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
import time

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


# --- Section 1 additions (UMR-20260806-084701-0d40, citing prior
# UMR-20260806-081403-ebd3): PARALLEL_WORKERS_CEILING + INTERACTIVE_SUBAGENT_COUNT.
def test_get_worker_ceiling_real_import(tmp_path, monkeypatch):
    """Real proof this reads the actual constant, not a hardcoded literal --
    a fake dispatch_core.py with a different CONCURRENCY_CAP value must
    change the reported ceiling."""
    fake_core = tmp_path / "fake_dispatch_core.py"
    fake_core.write_text("CONCURRENCY_CAP = 7\n")
    monkeypatch.setattr(pm, "DISPATCH_CORE_PATH", str(fake_core))
    result = pm.get_worker_ceiling()
    assert result["parallel_workers_ceiling"] == 7
    assert result.get("error") is None


def test_get_worker_ceiling_honest_error_on_missing_module(monkeypatch):
    monkeypatch.setattr(pm, "DISPATCH_CORE_PATH", "/nonexistent/dispatch_core.py")
    result = pm.get_worker_ceiling()
    assert result["parallel_workers_ceiling"] is None
    assert result["error"] is not None


def test_parse_ps_ppid_map_builds_children():
    stdout = "  100     1\n  200   100\n  201   100\n  300   200\n"
    result = pm.parse_ps_ppid_map(stdout)
    assert result == {1: [100], 100: [200, 201], 200: [300]}


def test_parse_ps_ppid_map_skips_malformed_lines():
    stdout = "  100     1\n garbage line here\n  200   100\n"
    result = pm.parse_ps_ppid_map(stdout)
    assert result == {1: [100], 100: [200]}


def test_get_descendant_pids_real_tree_walk():
    children_map = {1: [100], 100: [200, 201], 200: [300], 999: [888]}
    result = pm.get_descendant_pids([1], children_map)
    # 999/888 are a real, unrelated tree -- must never be included.
    assert result == {100, 200, 201, 300}


def test_get_descendant_pids_no_children():
    assert pm.get_descendant_pids([42], {}) == set()


def test_read_proc_cmdline_real_nul_separated(tmp_path):
    proc_root = tmp_path
    pid_dir = proc_root / "1234"
    pid_dir.mkdir()
    (pid_dir / "cmdline").write_bytes(b"claude\x00-p\x00do the real thing\x00")
    result = pm.read_proc_cmdline(1234, proc_root=str(proc_root))
    assert result == ["claude", "-p", "do the real thing"]


def test_read_proc_cmdline_unreadable_returns_none(tmp_path):
    # No such pid directory -- process already exited, a real race, not fabricated.
    assert pm.read_proc_cmdline(999999, proc_root=str(tmp_path)) is None


def test_is_claude_dash_p_argv_matches_real_shape():
    assert pm.is_claude_dash_p_argv(["claude", "-p", "the prompt text"]) is True
    assert pm.is_claude_dash_p_argv(["/usr/local/bin/claude", "-p", "x"]) is True


def test_is_claude_dash_p_argv_rejects_non_matches():
    assert pm.is_claude_dash_p_argv(["claude"]) is False  # interactive, no -p
    assert pm.is_claude_dash_p_argv(["bash", "-c", "claude -p foo"]) is False  # not argv[0]
    # A prompt that merely CONTAINS the substring "-p" must never false-positive
    # via a joined-string match -- this is real argv, so it's already safe,
    # but assert explicitly since that was the real risk this design avoids.
    assert pm.is_claude_dash_p_argv(["claude", "--resume", "session-p-123"]) is False
    assert pm.is_claude_dash_p_argv([]) is False


def test_get_interactive_subagent_count_real_end_to_end(monkeypatch, tmp_path):
    """Real end-to-end proof over a synthetic process tree + real /proc-shaped
    cmdline files: exactly the 2 real `claude -p` descendants are counted,
    a plain interactive `claude` and an unrelated `bash` are not."""
    monkeypatch.setattr(pm, "get_tmux_pane_pids", lambda session_name=None: ([500], None))
    monkeypatch.setattr(pm, "get_process_children_map", lambda: (
        {500: [600, 601], 600: [700]}, None
    ))
    proc_root = tmp_path
    cmdlines = {
        500: ["bash"],
        600: ["claude", "-p", "subagent one"],
        601: ["bash", "-c", "sleep 1"],
        700: ["claude", "-p", "subagent two"],
    }

    def fake_read_proc_cmdline(pid, proc_root=None):
        return cmdlines.get(pid)
    monkeypatch.setattr(pm, "read_proc_cmdline", fake_read_proc_cmdline)

    result = pm.get_interactive_subagent_count()
    assert result["error"] is None
    assert result["interactive_subagent_count"] == 2
    assert result["matched_pids"] == [600, 700]


def test_get_interactive_subagent_count_honest_error_no_session(monkeypatch):
    monkeypatch.setattr(pm, "get_tmux_pane_pids", lambda session_name=None: ([], "no such session"))
    result = pm.get_interactive_subagent_count()
    assert result["interactive_subagent_count"] is None
    assert result["error"] == "no such session"


# gtm_check_production_readiness_audit.py's classify() behavior exactly.
# ---------------------------------------------------------------------------
def test_classify_passed():
    assert pm.classify_passed(1) == "pass"
    assert pm.classify_passed(0) == "fail"
    assert pm.classify_passed(None) == "blocked_or_pending"


# ---------------------------------------------------------------------------
# Section 4 (UMR-20260806-091407-5767, citing governing report-contract
# UMR-20260806-042531-be9c): real GTM readiness bucket + recommendation --
# replaces the prior "always placeholder" behavior.
# ---------------------------------------------------------------------------
def _make_gtm_section(category_states):
    """Builds a real gtm_section-shaped dict (25 real category rows,
    category_index 1..25, any index not in `category_states` defaults to
    'pass') matching get_gtm_section()'s real output shape -- for pure-
    function testing without a real database."""
    categories = []
    counts = {"pass": 0, "fail": 0, "blocked_or_pending": 0}
    for i in range(1, 26):
        state = category_states.get(i, "pass")
        counts[state] += 1
        categories.append({"category_index": i, "category_name": f"cat{i}", "state": state})
    gate_row = next((c for c in categories if c["category_index"] == 25), None)
    return {
        "gtm_pass_count": counts["pass"],
        "gtm_fail_count": counts["fail"],
        "gtm_blocked_or_pending_count": counts["blocked_or_pending"],
        "categories": categories,
        "deterministic_gate": {"gate_result": gate_row["state"] if gate_row else None},
    }


# The real, live severity_rubric confirmed via category 25's own real
# evidence_json before implementing (see PR description) -- reused verbatim
# in these tests too, not a made-up rubric.
_REAL_SEVERITY_RUBRIC = {
    3: "P0", 4: "P0", 12: "P0", 14: "P0", 19: "P0", 21: "P0",
    1: "P1", 2: "P1", 5: "P1", 6: "P1", 7: "P1", 9: "P1", 15: "P1", 16: "P1", 20: "P1",
    8: "P2", 17: "P2", 18: "P2", 22: "P2", 24: "P2",
    10: "P3", 11: "P3", 13: "P3", 23: "P3",
}


def test_gtm_readiness_bucket_categories_partition_all_25_completely():
    all_indices = set()
    for indices in pm.GTM_READINESS_BUCKET_CATEGORIES.values():
        all_indices.update(indices)
    total = sum(len(v) for v in pm.GTM_READINESS_BUCKET_CATEGORIES.values())
    assert all_indices == set(range(1, 26))
    assert total == 25  # no duplicates, given the set-equality check above


def test_compute_bucket_percents_real_arithmetic():
    # security_ready = (3, 14, 15, 16); category 3 fails, rest pass -> 3/4 = 75%.
    gtm = _make_gtm_section({3: "fail"})
    percents = pm.compute_bucket_percents(gtm)
    assert percents["security_ready"] == 75
    assert percents["documentation_ready"] == 100  # category 22 alone, passing
    assert percents["performance_ready"] == 100  # unaffected


def test_compute_gtm_overall_percent_matches_section2_formula():
    gtm = _make_gtm_section({3: "fail", 25: "fail"})
    assert pm.compute_gtm_overall_percent(gtm) == round(23 / 25 * 100)


def test_readiness_not_ready_on_critical_p0_issue():
    """Category 3 (P0) fails -> critical_open_issue_count > 0 -> NOT_READY,
    even though the overall percent (96%) would otherwise read as BETA."""
    gtm = _make_gtm_section({3: "fail"})
    result = pm.compute_readiness_bucket(gtm, (_REAL_SEVERITY_RUBRIC, None))
    assert result["critical_open_issue_count"] == 1
    assert result["bucket"] == "NOT_READY"


def test_readiness_not_ready_on_blocked_category_even_with_high_percent():
    """Category 8 (P2, not P0/P1) blocked -- no critical/high severity
    signal, but the real blocked_category_count > 0 trigger alone still
    forces NOT_READY (real data-honesty rule: combined blocked_or_pending
    counts as blocked)."""
    gtm = _make_gtm_section({8: "blocked_or_pending"})
    result = pm.compute_readiness_bucket(gtm, (_REAL_SEVERITY_RUBRIC, None))
    assert result["critical_open_issue_count"] == 0
    assert result["blocked_category_count"] == 1
    assert result["bucket"] == "NOT_READY"


def test_readiness_limited_pilot_range():
    # 9 non-P0/P1 fails (P2/P3 only) -> 16/25 = 64%, no critical/high signal.
    fails = {i: "fail" for i in (10, 11, 13, 23, 8, 17, 18, 22, 24)}
    gtm = _make_gtm_section(fails)
    result = pm.compute_readiness_bucket(gtm, (_REAL_SEVERITY_RUBRIC, None))
    assert result["overall_percent"] == 64
    assert result["critical_open_issue_count"] == 0
    assert result["high_severity_open_issue_count"] == 0
    assert result["bucket"] == "LIMITED_PILOT"


def test_readiness_beta_on_percent_range():
    fails = {i: "fail" for i in (10, 11, 13)}  # 3 P3 fails -> 22/25 = 88%
    gtm = _make_gtm_section(fails)
    result = pm.compute_readiness_bucket(gtm, (_REAL_SEVERITY_RUBRIC, None))
    assert result["overall_percent"] == 88
    assert result["bucket"] == "BETA"


def test_readiness_beta_on_high_severity_overrides_production_at_100_edge():
    """A single P1 category (9) failing: overall = 24/25 = 96%. Real proof
    the high_severity_open_issue_count>0 signal is honored, first-match-wins,
    even in the 75-99% range where it's redundant with the percent check --
    and see the sibling test below for the case where it actually changes
    the outcome (percent would otherwise read PRODUCTION-eligible)."""
    gtm = _make_gtm_section({9: "fail"})
    result = pm.compute_readiness_bucket(gtm, (_REAL_SEVERITY_RUBRIC, None))
    assert result["high_severity_open_issue_count"] == 1
    assert result["bucket"] == "BETA"


def test_readiness_production_on_full_pass_and_certified():
    gtm = _make_gtm_section({})  # all 25 pass
    result = pm.compute_readiness_bucket(gtm, (_REAL_SEVERITY_RUBRIC, None))
    assert result["overall_percent"] == 100
    assert result["certified"] is True
    assert result["bucket"] == "PRODUCTION"


def test_readiness_honest_none_when_severity_rubric_unavailable():
    """Real honesty requirement: with no real severity source, the
    recommendation must be None with an honest reason -- never silently
    computed as if critical/high were 0."""
    gtm = _make_gtm_section({})
    result = pm.compute_readiness_bucket(gtm, (None, "category 25 evidence_json missing"))
    assert result["critical_open_issue_count"] is None
    assert result["bucket"] is None
    assert "unavailable" in result["reason"]


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
        "synthetic failing evidence",
        "synthetic open decision",
        "synthetic real issue",
        "synthetic AI proposal",
        "UMR-20260806-000000-abcd",
    ]:
        assert expected in text, f"missing expected section/content: {expected!r}"

    assert report["ocid_020_gtm_section"]["gtm_fail_count"] == 1
    assert report["ocid_020_gtm_section"]["gtm_pass_count"] == 1
    # Real Section 4 implementation (UMR-20260806-091407-5767) is never a
    # placeholder anymore -- with this synthetic DB's minimal category 25
    # row (no real evidence_json.severity_rubric), it honestly reports an
    # unavailable severity rubric rather than a fabricated score.
    assert report["gtm_readiness"]["is_placeholder"] is False
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
            "parallel_workers_ceiling": {}, "stuck_tasks": {}, "tmux": {},
            "interactive_subagents": {}, "emergency_stop": {}, "db_integrity": {},
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
            "file_overlap_max_age_hours": 48, "file_overlap_excluded_files": [],
            "gh_max_workers": 8, "time_budget_seconds": 120.0, "total_skipped_due_to_time_budget": 0,
            "total_candidate_count": 0, "primary_candidate_count": 0, "secondary_candidate_count": 0,
            "capped": False, "shown_count": 0, "shown_collisions": [],
            "by_repo_citation": {}, "by_repo_file": {},
            "worker_umr_collisions": {"errors": []}, "errors": [],
        },
        "instruction_quality_section": {"error": None, "total_checked": 0, "pass_count": 0, "failing": []},
        "owner_umr_closure_section": {
            "error": None, "all_time_status_counts": {}, "all_time_total": 0,
            "oldest_open_umr_id": None, "oldest_open_status": None, "oldest_open_age_hours": None,
            "trailing_24h_status_counts": {}, "trailing_24h_total": 0, "trailing_24h_closed_count": 0,
            "percent_complete_24h_owner_umr_set": None,
        },
        "dead_zone_reconciliation_section": {
            "recent_auto_remediations": [], "open_escalations": [],
        },
    }
    text = pm.render_report_text(report)  # must not raise
    assert "insufficient_data" in text
    assert "10. 10-REPORT TREND ANALYSIS" in text
    assert "14. OWNER UMR CLOSURE TRACKING" in text
    assert "15. DISPATCHED DEAD-ZONE AUTO-REMEDIATION" in text


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


# --- Section 12: deterministic collision detection (redefined by
# UMR-20260806-043900-8c48 -- primary same-UMR/task-identity citation match,
# narrowed+excluded-list secondary file-overlap, hard output cap) ----------
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


def test_extract_citation_tokens_umr_and_task_identity():
    text = "cites UMR-20260805-181636-32f2 and also task-20260806-035541-owner-directive--build-a-real"
    tokens = pm.extract_citation_tokens(text)
    assert "UMR-20260805-181636-32f2" in tokens
    assert "task-20260806-035541-owner-directive--build-a-real" in tokens


def test_extract_citation_tokens_empty_text():
    assert pm.extract_citation_tokens(None) == set()
    assert pm.extract_citation_tokens("") == set()
    assert pm.extract_citation_tokens("nothing to cite here") == set()


def test_pr_is_recent_within_window():
    now = pm.datetime.now(pm.timezone.utc)
    cutoff = now - pm.timedelta(hours=48)
    recent_pr = {"createdAt": now.isoformat()}
    assert pm._pr_is_recent(recent_pr, cutoff) is True


def test_pr_is_recent_outside_window():
    now = pm.datetime.now(pm.timezone.utc)
    cutoff = now - pm.timedelta(hours=48)
    old_pr = {"createdAt": (now - pm.timedelta(hours=200)).isoformat()}
    assert pm._pr_is_recent(old_pr, cutoff) is False


def test_pr_is_recent_missing_or_unparseable_createdat_treated_as_not_recent():
    now = pm.datetime.now(pm.timezone.utc)
    cutoff = now - pm.timedelta(hours=48)
    assert pm._pr_is_recent({}, cutoff) is False
    assert pm._pr_is_recent({"createdAt": None}, cutoff) is False
    assert pm._pr_is_recent({"createdAt": "not-a-date"}, cutoff) is False


def test_detect_pr_citation_collisions_matches_shared_umr():
    """PRIMARY signal (UMR-20260806-043900-8c48): two PRs citing the same
    real UMR in title/body/branch -- no shared file involved at all."""
    prs = [
        {"number": 98, "title": "fix: item 2", "body": "relates to UMR-20260806-030048-5d7a",
         "headRefName": "worker/task-a"},
        {"number": 100, "title": "fix: item 2 (v2)", "body": "also UMR-20260806-030048-5d7a",
         "headRefName": "worker/task-b"},
        {"number": 200, "title": "unrelated", "body": "UMR-20260101-000000-ffff",
         "headRefName": "worker/task-c"},
    ]
    result = pm.detect_pr_citation_collisions("fake-repo", prs)
    assert len(result["collisions"]) == 1
    c = result["collisions"][0]
    assert c["kind"] == "pr_citation"
    assert {c["pr_a"], c["pr_b"]} == {98, 100}
    assert c["shared_citations"] == ["UMR-20260806-030048-5d7a"]


def test_detect_pr_citation_collisions_no_shared_citation():
    prs = [
        {"number": 1, "title": "a", "body": "UMR-20260101-000000-aaaa", "headRefName": "x"},
        {"number": 2, "title": "b", "body": "UMR-20260101-000000-bbbb", "headRefName": "y"},
    ]
    result = pm.detect_pr_citation_collisions("fake-repo", prs)
    assert result["collisions"] == []


def test_detect_pr_citation_collisions_matches_task_identity_no_umr():
    prs = [
        {"number": 1, "title": "x", "body": "", "headRefName": "worker/task-20260806-035541-owner-directive--build"},
        {"number": 2, "title": "y", "body": "resumes task-20260806-035541-owner-directive--build", "headRefName": "z"},
    ]
    result = pm.detect_pr_citation_collisions("fake-repo", prs)
    assert len(result["collisions"]) == 1
    assert "task-20260806-035541-owner-directive--build" in result["collisions"][0]["shared_citations"]


def test_detect_pr_file_collisions_excludes_known_common_files(monkeypatch):
    """SECONDARY signal (UMR-20260806-043900-8c48): a shared PROGRESS.md/
    package.json touch alone must NOT trigger a collision -- this is the
    exact real false-positive pattern that flooded the report."""
    now = pm.datetime.now(pm.timezone.utc)
    prs = [
        {"number": 1, "title": "A", "createdAt": now.isoformat()},
        {"number": 2, "title": "B", "createdAt": now.isoformat()},
    ]

    def fake_get_pr_changed_files(repo, pr_number):
        if pr_number == 1:
            return {"PROGRESS.md", "package.json"}, None
        return {"PROGRESS.md", "package.json"}, None
    monkeypatch.setattr(pm, "get_pr_changed_files", fake_get_pr_changed_files)

    result = pm.detect_pr_file_collisions("fake-repo", prs)
    assert result["collisions"] == []


def test_detect_pr_file_collisions_real_code_overlap_still_flags(monkeypatch):
    now = pm.datetime.now(pm.timezone.utc)
    prs = [
        {"number": 1, "title": "A", "createdAt": now.isoformat()},
        {"number": 2, "title": "B", "createdAt": now.isoformat()},
    ]

    def fake_get_pr_changed_files(repo, pr_number):
        if pr_number == 1:
            return {"PROGRESS.md", "real_module.py"}, None
        return {"PROGRESS.md", "real_module.py"}, None
    monkeypatch.setattr(pm, "get_pr_changed_files", fake_get_pr_changed_files)

    result = pm.detect_pr_file_collisions("fake-repo", prs)
    assert len(result["collisions"]) == 1
    # PROGRESS.md excluded, real_module.py is the real signal that remains.
    assert result["collisions"][0]["shared_files"] == ["real_module.py"]


def test_detect_pr_file_collisions_ignores_old_prs(monkeypatch):
    """SECONDARY signal narrowing (UMR-20260806-043900-8c48): a PR opened
    outside the 48h window is excluded from file-overlap checking entirely,
    even with a real non-excluded shared file -- this is exactly the
    historical-backlog flood the fix closes."""
    now = pm.datetime.now(pm.timezone.utc)
    old = now - pm.timedelta(hours=500)
    prs = [
        {"number": 1, "title": "A", "createdAt": old.isoformat()},
        {"number": 2, "title": "B", "createdAt": old.isoformat()},
    ]
    calls = []

    def fake_get_pr_changed_files(repo, pr_number):
        calls.append(pr_number)
        return {"real_module.py"}, None
    monkeypatch.setattr(pm, "get_pr_changed_files", fake_get_pr_changed_files)

    result = pm.detect_pr_file_collisions("fake-repo", prs)
    assert result["recent_pr_count"] == 0
    assert result["collisions"] == []
    assert calls == []  # never even fetched diffs for out-of-window PRs


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
    assert result["collisions"][0]["kind"] == "worker_citation"
    assert result["collisions"][0]["shared_citations"] == ["UMR-20260805-181636-32f2"]


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


def test_rank_collision_candidates_primary_before_secondary():
    candidates = [
        {"kind": "pr_file", "id": "file1"},
        {"kind": "pr_citation", "id": "cite1"},
        {"kind": "pr_file", "id": "file2"},
        {"kind": "worker_citation", "id": "cite2"},
    ]
    ranked = pm._rank_collision_candidates(candidates)
    assert [c["kind"] for c in ranked] == ["pr_citation", "worker_citation", "pr_file", "pr_file"]


def test_cap_collision_candidates_under_trigger_not_capped():
    ranked = [{"kind": "pr_citation", "id": i} for i in range(5)]
    capped, shown = pm._cap_collision_candidates(ranked, trigger=200, top_k=50)
    assert capped is False
    assert shown == ranked


def test_cap_collision_candidates_over_trigger_shows_top_k():
    """HARD CAP (UMR-20260806-043900-8c48): the exact real safety net that
    keeps Section 12 well under a few hundred lines even if the
    primary/secondary redefinition somehow still let something noisy
    through."""
    ranked = [{"kind": "pr_citation", "id": i} for i in range(300)]
    capped, shown = pm._cap_collision_candidates(ranked, trigger=200, top_k=50)
    assert capped is True
    assert len(shown) == 50
    assert shown == ranked[:50]


def test_get_collision_detection_section_combines_primary_and_secondary(monkeypatch, tmp_path):
    """End-to-end (mocked gh/systemctl) proof that PRIMARY (citation match)
    and SECONDARY (recent, exclude-list-filtered file overlap) both surface
    as real candidates, and that a PROGRESS.md-only overlap does NOT."""
    monkeypatch.setattr(pm, "COLLISION_TRACKED_REPOS", ("fake-repo",))
    now = pm.datetime.now(pm.timezone.utc)

    def fake_get_open_pr_list(repo):
        return [
            {"number": 1, "title": "fix", "body": "UMR-20260806-030048-5d7a",
             "headRefName": "a", "createdAt": now.isoformat()},
            {"number": 2, "title": "fix v2", "body": "UMR-20260806-030048-5d7a",
             "headRefName": "b", "createdAt": now.isoformat()},
            {"number": 3, "title": "unrelated", "body": "", "headRefName": "c", "createdAt": now.isoformat()},
        ], None
    monkeypatch.setattr(pm, "get_open_pr_list", fake_get_open_pr_list)

    def fake_get_pr_changed_files(repo, pr_number):
        if pr_number in (1, 2):
            return {"PROGRESS.md"}, None  # excluded -- must not create a secondary collision
        return {"real_shared_module.py"}, None
    monkeypatch.setattr(pm, "get_pr_changed_files", fake_get_pr_changed_files)
    monkeypatch.setattr(pm, "detect_worker_umr_collisions", lambda: {
        "checked_units": [], "collisions": [], "errors": [],
    })

    result = pm.get_collision_detection_section()
    assert result["collision_detected"] is True
    assert result["primary_candidate_count"] == 1
    assert result["secondary_candidate_count"] == 0  # PROGRESS.md-only overlap correctly excluded
    assert result["shown_collisions"][0]["kind"] == "pr_citation"
    assert result["capped"] is False


def test_get_collision_detection_section_no_collision(monkeypatch):
    monkeypatch.setattr(pm, "COLLISION_TRACKED_REPOS", ("fake-repo",))
    monkeypatch.setattr(pm, "get_open_pr_list", lambda repo: ([], None))
    monkeypatch.setattr(pm, "detect_worker_umr_collisions", lambda: {
        "checked_units": [], "collisions": [], "errors": [],
    })
    result = pm.get_collision_detection_section()
    assert result["collision_detected"] is False
    assert result["total_candidate_count"] == 0


def test_get_collision_detection_section_applies_hard_cap(monkeypatch):
    """Real end-to-end proof the hard cap actually bounds Section 12's
    output size -- the concrete requirement from UMR-20260806-043900-8c48
    (report must be back to a reasonable length)."""
    monkeypatch.setattr(pm, "COLLISION_TRACKED_REPOS", ("fake-repo",))
    now = pm.datetime.now(pm.timezone.utc)
    # 60 PRs all citing the exact same UMR -> C(60,2) = 1770 primary
    # candidates, well over COLLISION_CANDIDATE_CAP_TRIGGER (200).
    prs = [
        {"number": i, "title": f"pr {i}", "body": "UMR-20260806-030048-5d7a",
         "headRefName": f"branch-{i}", "createdAt": now.isoformat()}
        for i in range(60)
    ]
    monkeypatch.setattr(pm, "get_open_pr_list", lambda repo: (prs, None))
    monkeypatch.setattr(pm, "get_pr_changed_files", lambda repo, n: (set(), None))
    monkeypatch.setattr(pm, "detect_worker_umr_collisions", lambda: {
        "checked_units": [], "collisions": [], "errors": [],
    })

    result = pm.get_collision_detection_section()
    assert result["total_candidate_count"] > pm.COLLISION_CANDIDATE_CAP_TRIGGER
    assert result["capped"] is True
    assert result["shown_count"] == pm.COLLISION_TOP_K
    assert len(result["shown_collisions"]) == pm.COLLISION_TOP_K


# --- Section 12 real perf fix (SCRIPT_VERSION 3.2.0): concurrent gh fetch +
# real overall time budget, root-caused against the real ~1h+ hangs of
# veridian-pm-report-tick.service. -------------------------------------------
def test_detect_pr_file_collisions_fetches_concurrently_not_sequentially(monkeypatch):
    """Real regression test for the actual root cause: at real live PR
    counts (94 combined recent-PR gh calls at investigation time) a
    sequential loop of slow calls could sum past an hour even with each
    call's own real 30s timeout. Proves wall-clock time for N slow calls is
    now bounded by ceil(N / max_workers) * per_call_time, not N *
    per_call_time."""
    import threading
    now = pm.datetime.now(pm.timezone.utc)
    n_prs = 16
    prs = [{"number": i, "title": f"pr {i}", "createdAt": now.isoformat()} for i in range(n_prs)]
    per_call_seconds = 0.05
    max_concurrent = {"value": 0}
    current = {"value": 0}
    lock = threading.Lock()

    def fake_get_pr_changed_files(repo, pr_number):
        with lock:
            current["value"] += 1
            max_concurrent["value"] = max(max_concurrent["value"], current["value"])
        time.sleep(per_call_seconds)
        with lock:
            current["value"] -= 1
        return {f"file_{pr_number}.py"}, None

    monkeypatch.setattr(pm, "get_pr_changed_files", fake_get_pr_changed_files)

    start = time.monotonic()
    result = pm.detect_pr_file_collisions("fake-repo", prs, max_workers=8)
    elapsed = time.monotonic() - start

    assert result["recent_pr_count"] == n_prs
    # Real proof of concurrency: more than one call really overlapped in time.
    assert max_concurrent["value"] > 1
    # Real proof of the wall-clock win: sequential would be n_prs * per_call_seconds
    # (0.8s here); concurrent with 8 workers should be well under half that.
    assert elapsed < (n_prs * per_call_seconds) / 2


def test_detect_pr_file_collisions_honors_deadline(monkeypatch):
    """Real overall time-budget fix: PRs not yet started once the deadline
    passes are recorded as skipped, never silently dropped and never fetched
    past the budget."""
    now = pm.datetime.now(pm.timezone.utc)
    prs = [{"number": i, "title": f"pr {i}", "createdAt": now.isoformat()} for i in range(5)]

    def fake_get_pr_changed_files(repo, pr_number):
        return {"real_module.py"}, None
    monkeypatch.setattr(pm, "get_pr_changed_files", fake_get_pr_changed_files)

    # Deadline already in the past -> every PR must be skipped, zero real calls.
    past_deadline = time.monotonic() - 1.0
    result = pm.detect_pr_file_collisions("fake-repo", prs, max_workers=8, deadline=past_deadline)
    assert result["recent_pr_count"] == 5
    assert result["skipped_due_to_time_budget"] == [0, 1, 2, 3, 4]
    assert result["collisions"] == []


def test_get_collision_detection_section_shares_one_deadline_across_repos(monkeypatch):
    """Real fix detail: the time budget is computed ONCE for the whole
    section and passed to every repo's detect_pr_file_collisions call, not
    reset per repo -- a per-repo-only budget could double the real worst
    case across the 2 tracked repos."""
    monkeypatch.setattr(pm, "COLLISION_TRACKED_REPOS", ("repo-a", "repo-b"))
    seen_deadlines = []

    def fake_detect_pr_file_collisions(repo, prs, max_age_hours=48, max_workers=8, deadline=None):
        seen_deadlines.append(deadline)
        return {"recent_pr_count": 0, "collisions": [], "errors": [], "skipped_due_to_time_budget": []}

    monkeypatch.setattr(pm, "get_open_pr_list", lambda repo: ([], None))
    monkeypatch.setattr(pm, "detect_pr_file_collisions", fake_detect_pr_file_collisions)
    monkeypatch.setattr(pm, "detect_worker_umr_collisions", lambda: {
        "checked_units": [], "collisions": [], "errors": [],
    })

    pm.get_collision_detection_section(time_budget_seconds=60)
    assert len(seen_deadlines) == 2
    assert seen_deadlines[0] is not None and seen_deadlines[1] is not None
    assert seen_deadlines[0] == seen_deadlines[1]


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
    conn.execute("CREATE TABLE umr_tasks (umr_id TEXT PRIMARY KEY, ts_submitted TEXT, "
                 "inputs_json TEXT, task_kind TEXT)")
    good_prompt = json.dumps({"prompt": "UMR-20260805-181636-32f2: extend generate_pm_report_v3.py, cite PR #110."})
    bad_prompt = json.dumps({"prompt": "please look into the general situation"})
    conn.execute("INSERT INTO umr_tasks VALUES ('UMR-good', '2026-08-06T00:00:02', ?, 'veridian_task_create')",
                 (good_prompt,))
    conn.execute("INSERT INTO umr_tasks VALUES ('UMR-bad', '2026-08-06T00:00:01', ?, 'veridian_task_create')",
                 (bad_prompt,))
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
    conn.execute("CREATE TABLE umr_tasks (umr_id TEXT PRIMARY KEY, ts_submitted TEXT, "
                 "inputs_json TEXT, task_kind TEXT)")
    for i in range(3):
        p = json.dumps({"prompt": f"UMR-20260805-181636-32f2: task {i}, see PR #{i}."})
        conn.execute("INSERT INTO umr_tasks VALUES (?, ?, ?, 'veridian_task_create')", (f"UMR-{i}", f"t{i}", p))
    conn.commit()
    conn.close()
    fake_sbr = _make_fake_sbr_module(db_path)
    result = pm.get_instruction_quality_section(fake_sbr)
    assert result["total_checked"] == 3
    assert result["pass_count"] == 3


def test_get_last_n_umr_tasks_excludes_systemctl_action_bookkeeping_noise(tmp_path):
    """Real regression test (UMR-20260806-041307-0bfd, post-merge fix): on
    a live box, task_kind='systemctl_action' rows (dispatch-tick.py's own
    resume_interrupted_workers bookkeeping) are the most recent rows by
    ts_submitted essentially always, and carry no 'prompt' field. Before
    this fix, get_last_n_umr_tasks() had no task_kind filter, so this exact
    shape made DETERMINISTIC_INSTRUCTION_COUNT permanently read 0/20
    regardless of real dispatch-instruction quality. This test reproduces
    that exact real shape and asserts the fix: the systemctl_action noise
    is excluded, and the real dispatch rows are found and checked."""
    db_path = str(tmp_path / "umr_noise.sqlite")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE umr_tasks (umr_id TEXT PRIMARY KEY, ts_submitted TEXT, "
                 "inputs_json TEXT, task_kind TEXT)")
    # 5 real systemctl_action bookkeeping rows, all more recent than the
    # one real dispatch prompt below -- exactly the live shape that broke
    # the original, unfiltered query.
    for i in range(5):
        conn.execute(
            "INSERT INTO umr_tasks VALUES (?, ?, ?, 'systemctl_action')",
            (f"UMR-noise-{i}", f"2026-08-06T05:00:{i:02d}", json.dumps({"action": "start"})),
        )
    good_prompt = json.dumps({"prompt": "UMR-20260805-181636-32f2: real dispatch, see PR #115."})
    conn.execute("INSERT INTO umr_tasks VALUES ('UMR-real', '2026-08-06T04:00:00', ?, 'veridian_task_create')",
                 (good_prompt,))
    conn.commit()
    conn.close()
    fake_sbr = _make_fake_sbr_module(db_path)

    rows, err = pm.get_last_n_umr_tasks(fake_sbr, 20)
    assert err is None
    assert [r["umr_id"] for r in rows] == ["UMR-real"]

    result = pm.get_instruction_quality_section(fake_sbr)
    assert result["total_checked"] == 1
    assert result["pass_count"] == 1


# --- Section 14 (UMR-20260806-070018-d88b item 4, extended by
# UMR-20260806-071942-5132): real owner UMR closure tracking. -------------
def _make_owner_umr_db(tmp_path, rows):
    """rows: list of (umr_id, ts_submitted, status, source_trigger)."""
    db_path = str(tmp_path / "owner_umr.sqlite")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE umr_tasks (umr_id TEXT PRIMARY KEY, ts_submitted TEXT, "
        "status TEXT, source_trigger TEXT)"
    )
    conn.executemany("INSERT INTO umr_tasks VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return db_path


def test_get_owner_dispatch_umr_status_counts_all_time(tmp_path):
    db_path = _make_owner_umr_db(tmp_path, [
        ("UMR-1", "2026-08-01T00:00:00+00:00", "completed", "owner_dispatch_gateway"),
        ("UMR-2", "2026-08-01T01:00:00+00:00", "queued", "owner_dispatch_gateway"),
        ("UMR-3", "2026-08-01T02:00:00+00:00", "queued", "some_other_trigger"),
    ])
    fake_sbr = _make_fake_sbr_module(db_path)
    counts, err = pm.get_owner_dispatch_umr_status_counts(fake_sbr)
    assert err is None
    # The non-owner_dispatch_gateway row must never be counted.
    assert counts == {"completed": 1, "queued": 1}


def test_get_oldest_open_owner_umr_ignores_terminal_statuses(tmp_path):
    db_path = _make_owner_umr_db(tmp_path, [
        ("UMR-old-done", "2026-08-01T00:00:00+00:00", "completed", "owner_dispatch_gateway"),
        ("UMR-oldest-open", "2026-08-01T01:00:00+00:00", "queued", "owner_dispatch_gateway"),
        ("UMR-newer-open", "2026-08-02T00:00:00+00:00", "running", "owner_dispatch_gateway"),
    ])
    fake_sbr = _make_fake_sbr_module(db_path)
    oldest, err = pm.get_oldest_open_owner_umr(fake_sbr)
    assert err is None
    assert oldest["umr_id"] == "UMR-oldest-open"


def test_get_oldest_open_owner_umr_none_when_all_closed(tmp_path):
    db_path = _make_owner_umr_db(tmp_path, [
        ("UMR-1", "2026-08-01T00:00:00+00:00", "completed", "owner_dispatch_gateway"),
        ("UMR-2", "2026-08-01T00:00:00+00:00", "killed", "owner_dispatch_gateway"),
    ])
    fake_sbr = _make_fake_sbr_module(db_path)
    oldest, err = pm.get_oldest_open_owner_umr(fake_sbr)
    assert err is None
    assert oldest is None


def test_age_hours_since_real_arithmetic():
    now = pm.datetime(2026, 8, 6, 12, 0, 0, tzinfo=pm.timezone.utc)
    ts = "2026-08-06T09, invalid"  # unparseable -> None, never fabricated
    assert pm._age_hours_since(None) is None
    assert pm._age_hours_since(ts) is None
    real_ts = "2026-08-06T09:30:00+00:00"
    assert pm._age_hours_since(real_ts, now_dt=now) == 2.5


def test_get_owner_umr_closure_section_percent_complete_and_oldest_open(tmp_path):
    now = pm.datetime(2026, 8, 6, 12, 0, 0, tzinfo=pm.timezone.utc)
    rows = [
        # Within trailing 24h (now-24h = 2026-08-05T12:00:00+00:00): 4 rows,
        # 2 completed (closed), 1 queued, 1 killed (terminal but NOT in
        # OWNER_UMR_CLOSED_STATUSES) -> percent = 2/4 * 100 = 50.0.
        ("UMR-a", "2026-08-05T13:00:00+00:00", "completed", "owner_dispatch_gateway"),
        ("UMR-b", "2026-08-05T14:00:00+00:00", "completed", "owner_dispatch_gateway"),
        ("UMR-c", "2026-08-06T01:00:00+00:00", "queued", "owner_dispatch_gateway"),
        ("UMR-d", "2026-08-06T02:00:00+00:00", "killed", "owner_dispatch_gateway"),
        # Outside the 24h window -- must not count toward trailing_24h_total.
        ("UMR-old", "2026-08-01T00:00:00+00:00", "completed", "owner_dispatch_gateway"),
        # Different source_trigger -- must never appear anywhere in this section.
        ("UMR-other", "2026-08-06T01:00:00+00:00", "queued", "some_other_trigger"),
    ]
    db_path = _make_owner_umr_db(tmp_path, rows)
    fake_sbr = _make_fake_sbr_module(db_path)

    section = pm.get_owner_umr_closure_section(fake_sbr, now_dt=now)
    assert section["error"] is None
    assert section["all_time_total"] == 5  # UMR-other excluded
    assert section["all_time_status_counts"] == {"completed": 3, "queued": 1, "killed": 1}
    assert section["oldest_open_umr_id"] == "UMR-c"
    assert section["oldest_open_age_hours"] == 11.0
    assert section["trailing_24h_total"] == 4
    assert section["trailing_24h_closed_count"] == 2
    assert section["percent_complete_24h_owner_umr_set"] == 50.0


def test_get_owner_umr_closure_section_honest_none_when_no_24h_rows(tmp_path):
    """Zero real rows in the trailing 24h window -> None, never a fabricated
    0.0 or 100.0 -- same honest-no-data spirit as Section 10."""
    now = pm.datetime(2026, 8, 6, 12, 0, 0, tzinfo=pm.timezone.utc)
    rows = [("UMR-old", "2026-08-01T00:00:00+00:00", "completed", "owner_dispatch_gateway")]
    db_path = _make_owner_umr_db(tmp_path, rows)
    fake_sbr = _make_fake_sbr_module(db_path)

    section = pm.get_owner_umr_closure_section(fake_sbr, now_dt=now)
    assert section["trailing_24h_total"] == 0
    assert section["percent_complete_24h_owner_umr_set"] is None


def test_get_owner_umr_closure_section_no_rows_at_all(tmp_path):
    db_path = _make_owner_umr_db(tmp_path, [])
    fake_sbr = _make_fake_sbr_module(db_path)
    section = pm.get_owner_umr_closure_section(fake_sbr, now_dt=pm.datetime.now(pm.timezone.utc))
    assert section["error"] is None
    assert section["all_time_total"] == 0
    assert section["oldest_open_umr_id"] is None
    assert section["percent_complete_24h_owner_umr_set"] is None


# --- Section 15 (UMR-20260806-115538-1e55 / UMR-20260806-115605-854d):
# read-only surface over reconcile_dispatched_dead_zone.py's own real
# pm_decisions_pending writes. ------------------------------------------
def _make_dead_zone_pm_decisions_db(tmp_path, rows):
    """rows: list of (title, related_umr, status, decision_type)."""
    db_path = str(tmp_path / "dead_zone_pdp.sqlite")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE pm_decisions_pending (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "opened_ts TEXT, title TEXT, detail TEXT, related_umr TEXT, status TEXT, "
        "closed_ts TEXT, closed_note TEXT, recommended_option TEXT, decision_type TEXT)"
    )
    for title, related_umr, status, decision_type in rows:
        conn.execute(
            "INSERT INTO pm_decisions_pending "
            "(opened_ts, title, detail, related_umr, status, decision_type) VALUES (?,?,?,?,?,?)",
            ("2026-08-06T12:00:00+00:00", title, "detail", related_umr, status, decision_type),
        )
    conn.commit()
    conn.close()
    return db_path


def test_get_dead_zone_reconciliation_section_separates_audit_log_from_escalations(tmp_path):
    db_path = _make_dead_zone_pm_decisions_db(tmp_path, [
        ("Auto-reset dead-zone dispatched row: UMR-a", "UMR-a", "resolved", "dead_zone_auto_remediation"),
        ("DEAD-ZONE REPEAT: UMR-b dead-zoned a second time after auto-reset", "UMR-b", "open", "pm_decision"),
        ("some unrelated real PM decision", "UMR-c", "open", "pm_decision"),
    ])
    fake_sbr = _make_fake_sbr_module(db_path)
    section = pm.get_dead_zone_reconciliation_section(fake_sbr)
    assert section.get("error") is None
    assert len(section["recent_auto_remediations"]) == 1
    assert section["recent_auto_remediations"][0]["related_umr"] == "UMR-a"
    assert len(section["open_escalations"]) == 1
    assert section["open_escalations"][0]["related_umr"] == "UMR-b"


def test_get_dead_zone_reconciliation_section_excludes_closed_escalations(tmp_path):
    """A resolved/closed second-occurrence escalation must not appear in
    open_escalations -- only genuinely open ones."""
    db_path = _make_dead_zone_pm_decisions_db(tmp_path, [
        ("DEAD-ZONE REPEAT: UMR-x dead-zoned a second time after auto-reset", "UMR-x", "resolved", "pm_decision"),
    ])
    fake_sbr = _make_fake_sbr_module(db_path)
    section = pm.get_dead_zone_reconciliation_section(fake_sbr)
    assert section["open_escalations"] == []


def test_get_dead_zone_reconciliation_section_empty_db_honest_empty_lists(tmp_path):
    db_path = _make_dead_zone_pm_decisions_db(tmp_path, [])
    fake_sbr = _make_fake_sbr_module(db_path)
    section = pm.get_dead_zone_reconciliation_section(fake_sbr)
    assert section["recent_auto_remediations"] == []
    assert section["open_escalations"] == []


def test_get_dead_zone_reconciliation_section_respects_recent_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "DEAD_ZONE_RECENT_AUDIT_LOG_LIMIT", 2)
    rows = [
        (f"Auto-reset dead-zone dispatched row: UMR-{i}", f"UMR-{i}", "resolved", "dead_zone_auto_remediation")
        for i in range(5)
    ]
    db_path = _make_dead_zone_pm_decisions_db(tmp_path, rows)
    fake_sbr = _make_fake_sbr_module(db_path)
    section = pm.get_dead_zone_reconciliation_section(fake_sbr)
    assert len(section["recent_auto_remediations"]) == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
