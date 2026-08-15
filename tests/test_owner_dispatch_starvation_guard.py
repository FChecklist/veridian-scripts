#!/usr/bin/env python3
"""
Real regression test for UMR-20260815-105911-a2c9 ("unstarve dispatch queue,
07-18 retry resurrection"). Real, live-confirmed incident this closes: ~20
owner_dispatch_gateway rows sat status='queued'/ts_dispatched=NULL
continuously from 2026-08-15T04:15:49Z through 09:17:55Z (6.5+ hours, zero
real dispatches) while all 4 of the box's occupied concurrency slots stayed
pinned to a same-morning BATCH of tier=1 dispatch-tick:resume_interrupted_workers
rows (2026-07-18 task_identities, all queued within the same ~20-second sweep).

Root cause: effective_priority()'s (tier, age) aging formula ages every
queued row at the same rate, so a same-tier batch arrival can never be
overtaken by a later same-or-lower-tier arrival -- next_queued_task()'s own
ts_submitted tiebreak always favors the older row. This test proves that
defect exists (test_batch_arrival_permanently_outranks_later_owner_row_without_guard
-- exercises next_queued_task() with the starvation guard disabled) and that
the real fix (OWNER_STARVATION_GUARANTEE_SECONDS) closes it.

Runs against a real, schema-only-cloned copy of the live DB (never the live
table), same convention test_resource_governor_queue_management.py already
established. Never calls systemctl/dispatch_one, never spawns a real worker.
"""
import importlib.util as _ilu
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(HERE)
RG_PATH = os.path.join(SCRIPTS_DIR, "resource_governor.py")
SBR_PATH = os.path.join(SCRIPTS_DIR, "superboss-register.py")
LIVE_DB = "/opt/veridian/ai-os/memory/superboss-register.sqlite"

_sbr_spec = _ilu.spec_from_file_location("starvation_guard_test_sbr_mod", SBR_PATH)
sbr = _ilu.module_from_spec(_sbr_spec)
sys.modules["starvation_guard_test_sbr_mod"] = sbr
_sbr_spec.loader.exec_module(sbr)

_rg_spec = _ilu.spec_from_file_location("starvation_guard_test_rg_mod", RG_PATH)
rg = _ilu.module_from_spec(_rg_spec)
sys.modules["starvation_guard_test_rg_mod"] = rg
_rg_spec.loader.exec_module(rg)


def _schema_only_copy(dest_conn, src_conn):
    """Same real, confirmed-live helper as
    test_resource_governor_queue_management.py's own _schema_only_copy() --
    see that file's docstring for the full UMR-20260814-033442-c885 rationale
    (schema-only, zero row data, never a full binary backup of the live
    multi-GB DB)."""
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


class OwnerStarvationGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="rg_starvation_guard_test_")
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

    def _seed(self, umr_id, task_identity, tier, source_trigger, ts_submitted):
        conn = sbr._connect()
        sbr._ensure_umr_table(conn)
        with sbr._write_lock():
            sbr.upsert_umr_task(conn, {
                "umr_id": umr_id,
                "task_identity": task_identity,
                "tier": tier,
                "status": "queued",
                "source_trigger": source_trigger,
                "task_kind": "systemctl_action",
                "unit_name": "veridian-test.service",
                "ts_submitted": ts_submitted.isoformat(),
                "metadata": {},
            })
            conn.commit()
        conn.close()

    def test_large_batch_permanently_outranks_later_owner_row_without_guard(self):
        """Proves the real root cause: a same-tier BATCH (simulating
        dispatch-tick.py's resume_interrupted_workers_tick() resubmitting
        dozens of 2026-07-18 identities in one sweep) beats a LATER-arriving,
        same-tier owner_dispatch_gateway row on every single tick, for as
        long as the batch has any row left -- aging alone never inverts
        that order. This calls next_queued_task() directly with `now` fixed
        just past the batch's own submission time (i.e. with the starvation
        guard's own real trigger condition not yet met), isolating the
        pre-existing (effective_priority, ts_submitted) ranking from the new
        guard added below."""
        now = rg._utcnow()
        batch_start = now - timedelta(minutes=5)
        for i in range(20):
            self._seed(
                f"UMR-TEST-BATCH-{i:02d}", f"test-batch-identity-{i:02d}", tier=1,
                source_trigger="dispatch-tick:resume_interrupted_workers",
                ts_submitted=batch_start + timedelta(seconds=i),
            )
        # Owner row arrives 1 minute AFTER the whole batch, same nominal tier.
        self._seed(
            "UMR-TEST-OWNER-1", "test-owner-identity-1", tier=1,
            source_trigger="owner_dispatch_gateway",
            ts_submitted=batch_start + timedelta(minutes=1),
        )

        conn = sbr._connect()
        try:
            # `now` is only 5 minutes past batch_start -- well under
            # OWNER_STARVATION_GUARANTEE_SECONDS (30min default), so the
            # guard added below must NOT fire yet; this call exercises only
            # the pre-existing ranking.
            picked = rg.next_queued_task(conn, now=now)
        finally:
            conn.close()

        self.assertEqual(picked["umr_id"], "UMR-TEST-BATCH-00",
                          f"expected the oldest batch row to win (real root cause), got {picked['umr_id']!r}")
        self.assertNotEqual(picked["source_trigger"], "owner_dispatch_gateway",
                             "real root cause: a later same-tier owner row can never win against "
                             "an older same-tier batch without the starvation guard")

    def test_starved_owner_row_wins_once_guarantee_elapses(self):
        """The real fix: once an owner_dispatch_gateway row's age reaches
        OWNER_STARVATION_GUARANTEE_SECONDS, next_queued_task() must pick it
        NEXT, unconditionally -- even though 20 lower-numbered-tier (i.e.
        real, freshly-aged-to-tier-0) batch rows are still queued and would
        otherwise win on effective_priority alone."""
        now = rg._utcnow()
        batch_start = now - timedelta(hours=2)
        for i in range(20):
            self._seed(
                f"UMR-TEST-BATCH2-{i:02d}", f"test-batch2-identity-{i:02d}", tier=1,
                source_trigger="dispatch-tick:resume_interrupted_workers",
                ts_submitted=batch_start + timedelta(seconds=i),
            )
        # Owner row's age exceeds the guarantee (default 30min); the batch
        # above is even older, so effective_priority alone would still pick
        # a batch row (both are aged to TIER_MIN=0, and the batch is older).
        owner_ts = now - timedelta(seconds=rg.OWNER_STARVATION_GUARANTEE_SECONDS + 60)
        self._seed(
            "UMR-TEST-OWNER-2", "test-owner-identity-2", tier=1,
            source_trigger="owner_dispatch_gateway", ts_submitted=owner_ts,
        )
        # A SECOND, younger owner row should NOT be picked instead --
        # confirms the guard picks the OLDEST starved owner row, not just any.
        self._seed(
            "UMR-TEST-OWNER-3", "test-owner-identity-3", tier=1,
            source_trigger="owner_dispatch_gateway",
            ts_submitted=now - timedelta(seconds=rg.OWNER_STARVATION_GUARANTEE_SECONDS + 5),
        )

        conn = sbr._connect()
        try:
            without_guard = sorted(
                [dict(r) for r in conn.execute("SELECT * FROM umr_tasks WHERE status='queued'").fetchall()],
                key=lambda r: (rg.effective_priority(r, now), r["ts_submitted"]),
            )[0]
            picked = rg.next_queued_task(conn, now=now)
        finally:
            conn.close()

        self.assertNotEqual(without_guard["umr_id"], "UMR-TEST-OWNER-2",
                             "test setup sanity check: the plain (effective_priority, ts_submitted) "
                             "ranking must NOT already pick the owner row on its own -- otherwise this "
                             "test would not actually exercise the new guard")
        self.assertEqual(picked["umr_id"], "UMR-TEST-OWNER-2",
                          f"real fix regression: the oldest starved owner_dispatch_gateway row must "
                          f"be picked once its age reaches OWNER_STARVATION_GUARANTEE_SECONDS, "
                          f"regardless of a larger/older same-tier batch, got {picked['umr_id']!r}")

    def test_no_starved_owner_row_falls_back_to_normal_ranking(self):
        """Purely additive: with zero owner_dispatch_gateway rows past the
        guarantee, next_queued_task() must behave exactly as before -- plain
        (effective_priority, ts_submitted) order."""
        now = rg._utcnow()
        self._seed("UMR-TEST-NORM-1", "test-norm-identity-1", tier=2,
                    source_trigger="owner_dispatch_gateway", ts_submitted=now)
        self._seed("UMR-TEST-NORM-2", "test-norm-identity-2", tier=1,
                    source_trigger="dispatch-tick:resume_interrupted_workers", ts_submitted=now)

        conn = sbr._connect()
        try:
            picked = rg.next_queued_task(conn, now=now)
        finally:
            conn.close()

        self.assertEqual(picked["umr_id"], "UMR-TEST-NORM-2",
                          "with no starved owner row, real tier ordering must be unchanged")


if __name__ == "__main__":
    unittest.main()
