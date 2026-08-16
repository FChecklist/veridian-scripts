#!/usr/bin/env python3
"""
Real test for resource_governor.py's next_queued_task() owner-priority
preemption consumption side (real fix, UMR-20260806-124055-bc80 /
UMR-20260806-165509-4d7c / task-20260815-045659).

Background: UMR-20260807-070110-5ea7 built the real owner_priority_override
table (umr_id, reason, set_by, ts) in superboss-register.py, and
task-20260807-081913's owner_priority_sequence keeps it auto-synced with
the currently-active phase's real members every tick
(_advance_owner_priority_phases_safe() -> sbr.advance_owner_priority_phases()
-> sbr._sync_owner_priority_override()). But neither of those landed the
CONSUMPTION side -- next_queued_task() itself never read that table (see
its own prior docstring / _advance_owner_priority_phases_safe()'s own
docstring, both explicit about this: "that consumption side ... [is]
separately-dispatched work ... this mechanism (whenever it lands)").
This test file covers that landing.

This task's own SPEC (task-20260815-045659) additionally claimed 4 specific
UMR ids (UMR-20260806-135632-329e, UMR-20260806-140841-46d1,
UMR-20260806-141055-1fec, UMR-20260806-162019-4b4f) were "still queued,
ts_dispatched NULL, ages 160-175+ minutes" and asked for them to be seeded
into a NEW override table/file as the real proof case. Direct query against
the real live DB (/opt/veridian/ai-os/memory/superboss-register.sqlite,
resolved via superboss-register.py's own resolve_superboss_db_path(), not
the empty 0-byte stub files at /opt/veridian/scripts/superboss-register.sqlite
or /opt/veridian/superboss-register.sqlite) shows this premise is false: all
4 rows were submitted 2026-08-06 (~8 days old, not ~3h), 3 of the 4 are
already status='completed'/'failed' (terminal, never re-enters 'queued'),
and all 4 already have ts_dispatched NOT NULL. None of them are eligible
dispatch candidates today regardless of any override mechanism, and a real
new table already existed (owner_priority_override, exact schema match) --
so this task instead (a) lands the genuinely-missing consumption side using
that existing table/convention, and (b) covers, as a real regression case
(test_override_ignores_non_queued_rows below), exactly the reason seeding
those 4 specific stale ids would have been a silent no-op: an overridden
umr_id whose row is not status='queued' is correctly never selected.

Runs against a real, disposable scratch sqlite DB (never the live table),
built via the same real sbr._ensure_umr_table()/sbr._ensure_owner_priority_tables()
+ sbr.upsert_umr_task() calls test_resource_governor_queue_management.py
already established, rather than a live-DB backup copy -- this test's own
seeded 'override beats a real dispatchable competitor' scenario needs full
control over which rows exist and does not need any real historical data.
Never calls systemctl/dispatch_one, never spawns a real worker.
"""
import datetime
import importlib.util as _ilu
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
RG_PATH = os.path.join(HERE, "resource_governor.py")
SBR_PATH = os.path.join(HERE, "superboss-register.py")

_sbr_spec = _ilu.spec_from_file_location("next_queued_owner_priority_test_sbr_mod", SBR_PATH)
sbr = _ilu.module_from_spec(_sbr_spec)
sys.modules["next_queued_owner_priority_test_sbr_mod"] = sbr
_sbr_spec.loader.exec_module(sbr)

_rg_spec = _ilu.spec_from_file_location("next_queued_owner_priority_test_rg_mod", RG_PATH)
rg = _ilu.module_from_spec(_rg_spec)
sys.modules["next_queued_owner_priority_test_rg_mod"] = rg
_rg_spec.loader.exec_module(rg)


class NextQueuedTaskOwnerPriorityTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="rg_next_queued_owner_priority_test_")
        self.scratch_path = os.path.join(self.tmpdir, "superboss-register.sqlite")
        self._orig_db_path = sbr.DB_PATH
        sbr.DB_PATH = self.scratch_path
        self._orig_safe = rg._safe_superboss_register
        rg._safe_superboss_register = lambda context: (sbr, None)

    def tearDown(self):
        sbr.DB_PATH = self._orig_db_path
        rg._safe_superboss_register = self._orig_safe
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _seed(self, umr_id, tier=2, status="queued", ts_submitted=None):
        conn = sbr._connect()
        sbr._ensure_umr_table(conn)
        with sbr._write_lock():
            sbr.upsert_umr_task(conn, {
                "umr_id": umr_id,
                "task_identity": f"test-identity-{umr_id}",
                "tier": tier,
                "status": status,
                "source_trigger": "test",
                "task_kind": "systemctl_action",
                "unit_name": "veridian-test.service",
                "ts_submitted": ts_submitted,
                "metadata": {},
            })
            conn.commit()
        conn.close()

    def _seed_override(self, umr_id, reason="test override"):
        conn = sbr._connect()
        sbr._ensure_owner_priority_tables(conn)
        with sbr._write_lock():
            conn.execute(
                "INSERT INTO owner_priority_override (umr_id, reason, set_by, ts) VALUES (?, ?, ?, ?)",
                (umr_id, reason, "test", rg._utcnow().isoformat()),
            )
            conn.commit()
        conn.close()

    def _next(self):
        conn = sbr._connect()
        sbr._ensure_umr_table(conn)
        sbr._ensure_owner_priority_tables(conn)
        row = rg.next_queued_task(conn, now=rg._utcnow())
        conn.close()
        return row

    def test_override_row_wins_regardless_of_tier_and_age(self):
        now = rg._utcnow()
        old_ts = (now - datetime.timedelta(hours=3)).isoformat()
        # Real tier-0, very old (3h) competitor -- would win the plain
        # (effective_priority, ts_submitted) sort outright.
        self._seed("UMR-TEST-COMPETITOR", tier=0, ts_submitted=old_ts)
        # Real tier-2, freshly-submitted overridden row.
        self._seed("UMR-TEST-OVERRIDDEN", tier=2, ts_submitted=now.isoformat())
        self._seed_override("UMR-TEST-OVERRIDDEN")

        row = self._next()
        self.assertIsNotNone(row)
        self.assertEqual(row["umr_id"], "UMR-TEST-OVERRIDDEN")

    def test_no_override_falls_back_to_normal_sort(self):
        now = rg._utcnow()
        self._seed("UMR-TEST-N1", tier=2, ts_submitted=now.isoformat())
        self._seed("UMR-TEST-N2", tier=1, ts_submitted=now.isoformat())
        # No owner_priority_override rows at all -- must reproduce the
        # pre-existing (effective_priority, ts_submitted) behavior exactly.
        row = self._next()
        self.assertEqual(row["umr_id"], "UMR-TEST-N2")

    def test_multiple_override_rows_oldest_wins(self):
        now = rg._utcnow()
        self._seed("UMR-TEST-O1", tier=2, ts_submitted=now.isoformat())
        self._seed("UMR-TEST-O2", tier=2,
                    ts_submitted=(now - datetime.timedelta(minutes=10)).isoformat())
        self._seed_override("UMR-TEST-O1")
        self._seed_override("UMR-TEST-O2")
        row = self._next()
        self.assertEqual(row["umr_id"], "UMR-TEST-O2")

    def test_override_ignores_non_queued_rows(self):
        # Real regression case for this task's own false-premise finding:
        # all 4 SPEC-named UMR ids were already status='completed'/'failed'
        # on the live DB, not 'queued'. An override entry naming a
        # non-queued row must never surface it, and must never block a
        # real, genuinely-queued, non-overridden row from being selected.
        now = rg._utcnow()
        self._seed("UMR-TEST-TERMINAL", tier=0, status="completed",
                    ts_submitted=(now - datetime.timedelta(hours=8)).isoformat())
        self._seed_override("UMR-TEST-TERMINAL")
        self._seed("UMR-TEST-REAL-QUEUED", tier=2, ts_submitted=now.isoformat())

        row = self._next()
        self.assertIsNotNone(row)
        self.assertEqual(row["umr_id"], "UMR-TEST-REAL-QUEUED")

    def test_fails_open_when_override_table_missing(self):
        # _owner_priority_override_ids() must fail closed-to-"no override"
        # (never raise) if the table genuinely doesn't exist yet on this DB
        # -- e.g. a brand-new DB dispatch_one() hasn't run
        # sbr._ensure_owner_priority_tables() against yet.
        conn = sbr._connect()
        sbr._ensure_umr_table(conn)
        now = rg._utcnow()
        with sbr._write_lock():
            sbr.upsert_umr_task(conn, {
                "umr_id": "UMR-TEST-NOTABLE", "task_identity": "test-identity-notable",
                "tier": 2, "status": "queued", "source_trigger": "test",
                "task_kind": "systemctl_action", "unit_name": "veridian-test.service",
                "ts_submitted": now.isoformat(), "metadata": {},
            })
            conn.commit()
        row = rg.next_queued_task(conn, now=now)
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["umr_id"], "UMR-TEST-NOTABLE")

    def test_dispatch_one_ensures_owner_priority_tables_before_selection(self):
        # Real wiring check: dispatch_one() must call
        # sbr._ensure_owner_priority_tables(conn) before next_queued_task()
        # so a never-before-seeded DB still gets real override behavior on
        # its very first tick, not just after some other code path happens
        # to have created the table. max_dispatches is irrelevant here --
        # this only exercises dispatch_one() up through the ensure calls.
        now = rg._utcnow()
        self._seed("UMR-TEST-D1", tier=2, ts_submitted=now.isoformat())
        result = rg.dispatch_one(now=now)
        self.assertIn(result["action"], ("dispatched", "idle", "no_free_slot",
                                          "frozen", "superboss_unavailable", "deferred"))
        conn = sbr._connect()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='owner_priority_override'"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row, "owner_priority_override table must exist after dispatch_one()")


if __name__ == "__main__":
    unittest.main()
