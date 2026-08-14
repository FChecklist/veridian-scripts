#!/usr/bin/env python3
"""
Real test for resource_governor.py's queue-management operations
(list_queue/stop_task/resume_task/delete_task/set_priority/move_up/
move_down) -- UMR-20260813-211804-b554's RCA + real redispatch of
UMR-20260807-150524-a683's own original spec.

Runs against a real COPY of the live DB (sqlite3 backup API, same
corruption-avoidance convention test_resource_governor_owner_priority_advance.py
already established -- never the live table). Never calls
systemctl/dispatch_one, never spawns a real worker.
"""
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
LIVE_DB = "/opt/veridian/ai-os/memory/superboss-register.sqlite"

_sbr_spec = _ilu.spec_from_file_location("queue_mgmt_rg_test_sbr_mod", SBR_PATH)
sbr = _ilu.module_from_spec(_sbr_spec)
sys.modules["queue_mgmt_rg_test_sbr_mod"] = sbr
_sbr_spec.loader.exec_module(sbr)

_rg_spec = _ilu.spec_from_file_location("queue_mgmt_rg_test_rg_mod", RG_PATH)
rg = _ilu.module_from_spec(_rg_spec)
sys.modules["queue_mgmt_rg_test_rg_mod"] = rg
_rg_spec.loader.exec_module(rg)


def _schema_only_copy(dest_conn, src_conn):
    """Real root-cause fix (UMR-20260814-033442-c885, P0 disk exhaustion):
    this used to be `src.backup(dst)`, a full sqlite3 binary backup of the
    ENTIRE live ~3-4GB superboss-register.sqlite, for every single test
    invocation. Every real test in this file only ever reads back rows it
    seeded itself via _seed() -- it never depends on the live DB's actual
    row data -- so the fix is to clone the schema (every real
    table/index/trigger/view definition from sqlite_master) with ZERO row
    data copied, not to just clean up the 4GB copy after the fact.

    Three passes -- virtual tables (which auto-create their OWN correct
    shadow tables) first, then every other ordinary table, then
    indexes/triggers/views last. See the identical helper in
    test_pm_sentinel_tick.py's own _schema_only_copy for the full,
    real, confirmed-live rationale: raw sqlite_master rowid order is NOT
    reliable creation order for FTS5 shadow tables vs. their owning
    virtual table on this DB, and a naive single-pass rowid-ordered copy
    silently left `umr_tasks_fts` never actually created."""
    rows = src_conn.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    is_virtual_table = lambda t, sql: t == "table" and sql.strip().upper().startswith("CREATE VIRTUAL TABLE")
    for _type, _name, sql in rows:
        if is_virtual_table(_type, sql):
            dest_conn.execute(sql)
    for _type, _name, sql in rows:
        if _type == "table" and not is_virtual_table(_type, sql):
            try:
                dest_conn.execute(sql)
            except sqlite3.OperationalError as e:
                if "already exists" not in str(e):
                    raise
    for _type, _name, sql in rows:
        if _type != "table":
            try:
                dest_conn.execute(sql)
            except sqlite3.OperationalError as e:
                if "already exists" not in str(e):
                    raise


class QueueManagementTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="rg_queue_mgmt_test_")
        # UMR-20260814-033442-c885: registered BEFORE the schema copy below
        # so this real scratch dir is still reclaimed even if that copy
        # itself raises (e.g. disk full mid-copy) -- addCleanup runs even
        # when setUp raises after this point; tearDown alone does not.
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.copy_path = os.path.join(self.tmpdir, "superboss-register.sqlite")
        src = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
        dst = sqlite3.connect(self.copy_path)
        with dst:
            _schema_only_copy(dst, src)
        src.close()
        dst.close()

        self._orig_db_path = sbr.DB_PATH
        sbr.DB_PATH = self.copy_path
        self._orig_safe = rg._safe_superboss_register
        rg._safe_superboss_register = lambda context: (sbr, None)

    def tearDown(self):
        sbr.DB_PATH = self._orig_db_path
        rg._safe_superboss_register = self._orig_safe
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _seed(self, umr_id, tier=2, status="queued", ts_submitted=None, metadata=None):
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
                "metadata": metadata or {},
            })
            conn.commit()
        conn.close()

    def _row(self, umr_id):
        conn = sbr._connect()
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    # -- list_queue -----------------------------------------------------

    def test_list_queue_real_dispatch_order(self):
        # Timestamps deliberately anchored to real "now" (a few seconds
        # apart) rather than a fixed historical date -- effective_priority()
        # ages rows toward higher priority over AGING_PROMOTION_INTERVAL_SECONDS
        # (15min default), so a fixed-in-the-past timestamp would silently
        # accrue real promotions by the time this test actually runs and
        # invalidate the tier ordering this test means to check.
        now = rg._utcnow()
        self._seed("UMR-TEST-Q1", tier=2, ts_submitted=(now).isoformat())
        self._seed("UMR-TEST-Q2", tier=1, ts_submitted=(now).isoformat())
        self._seed("UMR-TEST-Q3", tier=2, ts_submitted=(now - __import__("datetime").timedelta(seconds=5)).isoformat())
        result = rg.list_queue(status="queued", limit=100)
        self.assertTrue(result["ok"])
        ids = [r["umr_id"] for r in result["queue"] if r["umr_id"].startswith("UMR-TEST-")]
        # tier 1 (Q2) outranks tier 2 regardless of submission time; within
        # tier 2, earlier ts_submitted (Q3) outranks later (Q1).
        self.assertEqual(ids, ["UMR-TEST-Q2", "UMR-TEST-Q3", "UMR-TEST-Q1"])

    # -- stop_task / resume_task ----------------------------------------

    def test_stop_task_pauses_and_resume_clears(self):
        self._seed("UMR-TEST-S1", tier=2)
        result = rg.stop_task("UMR-TEST-S1")
        self.assertEqual(result, {"ok": True, "umr_id": "UMR-TEST-S1", "already_paused": False})
        row = self._row("UMR-TEST-S1")
        self.assertEqual(row["status"], "queued")  # status itself untouched
        import json as _json
        self.assertTrue(_json.loads(row["metadata_json"])["queue_paused"])

        # idempotent re-stop
        result2 = rg.stop_task("UMR-TEST-S1")
        self.assertEqual(result2, {"ok": True, "umr_id": "UMR-TEST-S1", "already_paused": True})

        result3 = rg.resume_task("UMR-TEST-S1")
        self.assertEqual(result3, {"ok": True, "umr_id": "UMR-TEST-S1", "was_paused": True})
        row2 = self._row("UMR-TEST-S1")
        self.assertNotIn("queue_paused", _json.loads(row2["metadata_json"]))

    def test_resume_task_idempotent_when_not_paused(self):
        self._seed("UMR-TEST-S2", tier=2)
        result = rg.resume_task("UMR-TEST-S2")
        self.assertEqual(result, {"ok": True, "umr_id": "UMR-TEST-S2", "was_paused": False})

    def test_stop_task_refuses_non_queued_row(self):
        self._seed("UMR-TEST-S3", tier=2, status="running")
        result = rg.stop_task("UMR-TEST-S3")
        self.assertFalse(result["ok"])
        self.assertIn("queued", result["error"])

    def test_stop_task_missing_row(self):
        result = rg.stop_task("UMR-DOES-NOT-EXIST")
        self.assertFalse(result["ok"])

    # -- delete_task ------------------------------------------------------

    def test_delete_task_removes_queued_row(self):
        self._seed("UMR-TEST-D1", tier=3)
        result = rg.delete_task("UMR-TEST-D1")
        self.assertTrue(result["ok"])
        self.assertIsNone(self._row("UMR-TEST-D1"))

    def test_delete_task_refuses_non_queued_row(self):
        self._seed("UMR-TEST-D2", tier=3, status="completed")
        result = rg.delete_task("UMR-TEST-D2")
        self.assertFalse(result["ok"])
        self.assertIsNotNone(self._row("UMR-TEST-D2"))  # never deleted

    # -- set_priority -------------------------------------------------------

    def test_set_priority_updates_tier(self):
        self._seed("UMR-TEST-P1", tier=3)
        result = rg.set_priority("UMR-TEST-P1", 0)
        self.assertEqual(result, {"ok": True, "umr_id": "UMR-TEST-P1", "prior_tier": 3, "tier": 0})
        self.assertEqual(self._row("UMR-TEST-P1")["tier"], 0)

    def test_set_priority_rejects_out_of_range(self):
        self._seed("UMR-TEST-P2", tier=3)
        result = rg.set_priority("UMR-TEST-P2", 5)
        self.assertFalse(result["ok"])
        self.assertEqual(self._row("UMR-TEST-P2")["tier"], 3)  # unchanged

    def test_set_priority_refuses_non_queued_row(self):
        self._seed("UMR-TEST-P3", tier=3, status="failed")
        result = rg.set_priority("UMR-TEST-P3", 0)
        self.assertFalse(result["ok"])

    # -- move_up / move_down ----------------------------------------------

    def test_move_up_swaps_with_higher_ranked_same_tier_neighbor(self):
        self._seed("UMR-TEST-M1", tier=2, ts_submitted="2026-08-13T10:00:00+00:00")
        self._seed("UMR-TEST-M2", tier=2, ts_submitted="2026-08-13T10:00:01+00:00")
        before = rg.list_queue(status="queued", limit=100)
        before_ids = [r["umr_id"] for r in before["queue"] if r["umr_id"].startswith("UMR-TEST-M")]
        self.assertEqual(before_ids, ["UMR-TEST-M1", "UMR-TEST-M2"])

        result = rg.move_up("UMR-TEST-M2")
        self.assertTrue(result["ok"])
        self.assertTrue(result["moved"])
        self.assertEqual(result["swapped_with_umr_id"], "UMR-TEST-M1")

        after = rg.list_queue(status="queued", limit=100)
        after_ids = [r["umr_id"] for r in after["queue"] if r["umr_id"].startswith("UMR-TEST-M")]
        self.assertEqual(after_ids, ["UMR-TEST-M2", "UMR-TEST-M1"])

        # ts_submitted itself is never mutated by a reorder (aging math must
        # keep reading the real original submission time).
        row1 = self._row("UMR-TEST-M1")
        row2 = self._row("UMR-TEST-M2")
        self.assertEqual(row1["ts_submitted"], "2026-08-13T10:00:00+00:00")
        self.assertEqual(row2["ts_submitted"], "2026-08-13T10:00:01+00:00")

    def test_move_up_at_top_of_tier_is_a_no_op(self):
        self._seed("UMR-TEST-M3", tier=2, ts_submitted="2026-08-13T10:00:00+00:00")
        result = rg.move_up("UMR-TEST-M3")
        self.assertTrue(result["ok"])
        self.assertFalse(result["moved"])

    def test_move_down_never_crosses_a_tier_boundary(self):
        self._seed("UMR-TEST-M4", tier=1, ts_submitted="2026-08-13T10:00:00+00:00")
        self._seed("UMR-TEST-M5", tier=3, ts_submitted="2026-08-13T10:00:01+00:00")
        result = rg.move_down("UMR-TEST-M4")
        self.assertTrue(result["ok"])
        self.assertFalse(result["moved"])  # no same-tier-1 neighbor below it
        self.assertEqual(self._row("UMR-TEST-M4")["tier"], 1)  # tier itself never touched


if __name__ == "__main__":
    unittest.main()
