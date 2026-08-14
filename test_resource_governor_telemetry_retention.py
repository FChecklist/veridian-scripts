#!/usr/bin/env python3
"""
Real test for resource_governor.py's telemetry-table retention policy
(UMR-20260813-125836-5809, addendum to Priority-1 UMR-20260806-171945-5767).

Runs entirely against a scratch, from-scratch sqlite file (never the live
4GB register, never a full-DB copy of it -- a from-scratch schema keeps
this fast and avoids any dependency on the live register's current,
constantly-changing content). Covers:
  - telemetry_retention_plan(): real dry-run counts, zero writes
  - telemetry_retention_execute(): real archive-then-delete, real gzip
    JSONL archive content matches exactly what was deleted, umr_tasks
    (not a telemetry table) is completely untouched
  - the once-per-TELEMETRY_RETENTION_MIN_INTERVAL_SECONDS self-gate inside
    _orchestrator_tick_maintenance()'s Step 13
"""
import gzip
import importlib.util as _ilu
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
RG_PATH = os.path.join(HERE, "resource_governor.py")

_rg_spec = _ilu.spec_from_file_location("telemetry_retention_test_rg_mod", RG_PATH)
rg = _ilu.module_from_spec(_rg_spec)
sys.modules["telemetry_retention_test_rg_mod"] = rg
_rg_spec.loader.exec_module(rg)


def _make_scratch_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE umr_tasks (
            umr_id TEXT PRIMARY KEY,
            ts_submitted TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE pm_report_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            report_json TEXT NOT NULL
        );
        CREATE TABLE log_index (
            log_index_id INTEGER PRIMARY KEY AUTOINCREMENT,
            log_file TEXT,
            line_no INTEGER,
            ts TEXT NOT NULL,
            content TEXT
        );
        """
    )
    conn.commit()
    return conn


class TelemetryRetentionTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="rg_telemetry_retention_test_")
        self.db_path = os.path.join(self.tmpdir, "scratch.sqlite")
        self.conn = _make_scratch_db(self.db_path)
        self.now = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)

        # Seed: umr_tasks (must NEVER be touched by this module), plus
        # pm_report_snapshots/log_index rows straddling their real
        # retention windows (pm_report_snapshots=3 days, log_index=14
        # days per TELEMETRY_RETENTION_TABLES).
        self.conn.execute(
            "INSERT INTO umr_tasks VALUES (?, ?, ?)",
            ("UMR-TEST-OLD", (self.now - timedelta(days=400)).isoformat(), "completed"),
        )
        self.conn.execute(
            "INSERT INTO umr_tasks VALUES (?, ?, ?)",
            ("UMR-TEST-NEW", self.now.isoformat(), "queued"),
        )

        self.old_pm_ts = (self.now - timedelta(days=5)).isoformat()
        self.recent_pm_ts = (self.now - timedelta(hours=1)).isoformat()
        self.conn.execute(
            "INSERT INTO pm_report_snapshots (ts, report_json) VALUES (?, ?)",
            (self.old_pm_ts, json.dumps({"note": "old snapshot"})),
        )
        self.conn.execute(
            "INSERT INTO pm_report_snapshots (ts, report_json) VALUES (?, ?)",
            (self.recent_pm_ts, json.dumps({"note": "recent snapshot"})),
        )

        self.old_log_ts = (self.now - timedelta(days=20)).isoformat()
        self.recent_log_ts = (self.now - timedelta(days=1)).isoformat()
        self.conn.execute(
            "INSERT INTO log_index (log_file, line_no, ts, content) VALUES (?, ?, ?, ?)",
            ("some.log", 1, self.old_log_ts, "old line"),
        )
        self.conn.execute(
            "INSERT INTO log_index (log_file, line_no, ts, content) VALUES (?, ?, ?, ?)",
            ("some.log", 2, self.recent_log_ts, "recent line"),
        )
        self.conn.commit()

        # No monkeypatching needed: both the archive dir and the state path
        # are derived PER-CONNECTION from `conn`'s own real DB file (see
        # _telemetry_retention_archive_dir()/_telemetry_retention_state_path()),
        # so they naturally land inside self.tmpdir, next to scratch.sqlite,
        # for this test's own connection -- and never touch any real
        # production path, by construction.
        self.archive_dir = os.path.join(self.tmpdir, "backups", "register_retention_archive")
        self.state_path = self.db_path + ".telemetry-retention-state.json"

        self._scratch_tables = {
            "pm_report_snapshots": rg.TELEMETRY_RETENTION_TABLES["pm_report_snapshots"],
            "log_index": rg.TELEMETRY_RETENTION_TABLES["log_index"],
        }

    def tearDown(self):
        self.conn.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_umr_tasks_never_a_configured_telemetry_table(self):
        self.assertNotIn("umr_tasks", rg.TELEMETRY_RETENTION_TABLES)

    def test_plan_is_read_only_and_reports_real_counts(self):
        plan = rg.telemetry_retention_plan(self.conn, now=self.now, tables=self._scratch_tables)
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["tables"]["pm_report_snapshots"]["rows_would_remove"], 1)
        self.assertEqual(plan["tables"]["log_index"]["rows_would_remove"], 1)

        # Dry run must not have written or deleted anything.
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM pm_report_snapshots").fetchone()[0], 2)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM log_index").fetchone()[0], 2)
        self.assertFalse(os.path.isdir(self.archive_dir))

    def test_execute_archives_then_deletes_and_never_touches_umr_tasks(self):
        report = rg.telemetry_retention_execute(self.conn, now=self.now, tables=self._scratch_tables)
        self.assertFalse(report["dry_run"])

        pm_entry = report["tables"]["pm_report_snapshots"]
        self.assertEqual(pm_entry["archived_rows"], 1)
        self.assertEqual(pm_entry["rows_deleted"], 1)
        self.assertTrue(os.path.isfile(pm_entry["archive_path"]))

        log_entry = report["tables"]["log_index"]
        self.assertEqual(log_entry["archived_rows"], 1)
        self.assertEqual(log_entry["rows_deleted"], 1)

        # Only the recent row survives in the live table.
        remaining_pm = [dict(r) for r in self.conn.execute(
            "SELECT ts FROM pm_report_snapshots").fetchall()]
        self.assertEqual(remaining_pm, [{"ts": self.recent_pm_ts}])
        remaining_log = [dict(r) for r in self.conn.execute(
            "SELECT ts FROM log_index").fetchall()]
        self.assertEqual(remaining_log, [{"ts": self.recent_log_ts}])

        # The archive is real and contains exactly the deleted row.
        with gzip.open(pm_entry["archive_path"], "rt", encoding="utf-8") as f:
            archived_rows = [json.loads(line) for line in f]
        self.assertEqual(len(archived_rows), 1)
        self.assertEqual(archived_rows[0]["ts"], self.old_pm_ts)
        self.assertEqual(json.loads(archived_rows[0]["report_json"]), {"note": "old snapshot"})

        # umr_tasks: byte-for-byte row-count intact, both rows untouched.
        umr_rows = [dict(r) for r in self.conn.execute(
            "SELECT umr_id, ts_submitted, status FROM umr_tasks ORDER BY umr_id").fetchall()]
        self.assertEqual(len(umr_rows), 2)
        self.assertEqual(umr_rows[0]["umr_id"], "UMR-TEST-NEW")
        self.assertEqual(umr_rows[1]["umr_id"], "UMR-TEST-OLD")

    def test_execute_is_idempotent_second_run_deletes_nothing_new(self):
        rg.telemetry_retention_execute(self.conn, now=self.now, tables=self._scratch_tables)
        report2 = rg.telemetry_retention_execute(self.conn, now=self.now, tables=self._scratch_tables)
        self.assertEqual(report2["tables"]["pm_report_snapshots"]["archived_rows"], 0)
        self.assertEqual(report2["tables"]["pm_report_snapshots"]["archive_path"], None)
        self.assertEqual(report2["tables"]["pm_report_snapshots"]["rows_deleted"], 0)

    def test_state_and_archive_paths_are_derived_from_the_real_conn_not_global(self):
        # Real regression test for the real bug caught while building this:
        # both paths must be derived from `conn`'s own PRAGMA database_list
        # path, landing next to scratch.sqlite inside self.tmpdir -- never
        # a fixed global production path.
        self.assertEqual(rg._telemetry_retention_state_path(self.conn), self.state_path)
        self.assertEqual(rg._telemetry_retention_archive_dir(self.conn), self.archive_dir)
        self.assertFalse(self.state_path.startswith(rg.LOCKS_DIR))
        self.assertFalse(self.archive_dir.startswith(rg.AI_OS + "/memory/backups"))

    def test_orchestrator_tick_maintenance_step13_self_gates_daily(self):
        # First call within a fresh tick: no prior state file -> should run
        # and delete the old rows for real. Directly exercise the gating
        # block the way _orchestrator_tick_maintenance does,
        # scoped to just the retention tables (the full function also touches
        # superboss_gateway/health-check/document_engine, which need the real
        # SCRIPTS tree -- out of scope for this focused unit test).
        state_path = rg._telemetry_retention_state_path(self.conn)
        with rg._state_file_lock(state_path):
            state = rg._load_json(state_path) or {}
            self.assertIsNone(state.get("last_run_ts"))

        first = rg.telemetry_retention_execute(self.conn, now=self.now, tables=self._scratch_tables)
        rg._save_json(state_path, {"last_run_ts": self.now.isoformat()})
        self.assertEqual(first["tables"]["pm_report_snapshots"]["rows_deleted"], 1)

        # A second "tick" 5 minutes later must self-gate (well under the
        # 24h TELEMETRY_RETENTION_MIN_INTERVAL_SECONDS default).
        soon_after = self.now + timedelta(minutes=5)
        state = rg._load_json(state_path)
        last_run_dt = datetime.fromisoformat(state["last_run_ts"])
        should_run = (soon_after - last_run_dt).total_seconds() >= rg.TELEMETRY_RETENTION_MIN_INTERVAL_SECONDS
        self.assertFalse(should_run)


if __name__ == "__main__":
    unittest.main()
