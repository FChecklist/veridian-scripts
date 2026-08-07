#!/usr/bin/env python3
"""
Real, self-contained test for the owner_priority_sequence / advance
mechanism built under this task (amendment to UMR-20260807-070110-5ea7,
governed by UMR-20260806-124055-bc80).

Runs against a real COPY of the live superboss-register.sqlite (shutil.copy
of the actual file, opened via its own sqlite3 connection) -- never the
live table itself, matching the SPEC's own explicit requirement ("tested
against a real copy not the live table"). The copy is a real, byte-for-byte
snapshot of the live umr_tasks/ocid_canonical_registry data at test time,
so phase seeding runs the real discovery queries against real (not
synthetic/fabricated) rows; only the completion-evidence mutations in
test_advances_phase_1_to_phase_2 below are synthetic (needed to prove the
advance transition deterministically, without waiting on real, still-live
work to finish).

Same dynamic-import convention as this repo's other test_*.py files for a
hyphenated module (see test_ai_agent_registry.py).
"""
import importlib.util as _ilu
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SBR_PATH = os.path.join(HERE, "superboss-register.py")
LIVE_DB = "/opt/veridian/ai-os/memory/superboss-register.sqlite"

_spec = _ilu.spec_from_file_location("owner_priority_sequence_test_mod", SBR_PATH)
sbr = _ilu.module_from_spec(_spec)
sys.modules["owner_priority_sequence_test_mod"] = sbr
_spec.loader.exec_module(sbr)


class OwnerPrioritySequenceTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="owner_priority_seq_test_")
        self.copy_path = os.path.join(self.tmpdir, "superboss-register.sqlite")
        self.assertNotEqual(os.path.abspath(self.copy_path), os.path.abspath(LIVE_DB))
        # Real copy of the real live file, taken via sqlite3's own backup
        # API rather than a raw shutil.copy2 -- this DB is WAL-mode and
        # actively written to by real live concurrent workers/dispatch
        # ticks, so a plain byte-level file copy can race a WAL checkpoint
        # mid-copy and land a genuinely corrupt ("database disk image is
        # malformed") snapshot (confirmed live: shutil.copy2 hit exactly
        # this on one real run of this test). conn.backup() is SQLite's own
        # documented mechanism for a consistent live snapshot without
        # blocking the source.
        src = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
        dst = sqlite3.connect(self.copy_path)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        self.conn = sqlite3.connect(self.copy_path)
        self.conn.row_factory = sqlite3.Row

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_seeds_all_4_phases_with_real_discovered_umr_ids(self):
        result = sbr.seed_owner_priority_sequence(self.conn)
        self.assertTrue(result["seeded"])
        phases = result["phases"]
        self.assertEqual([p["phase_order"] for p in phases], [1, 2, 3, 4])

        # Phase 1/2: exact explicit SPEC-named members.
        self.assertEqual(sorted(phases[0]["real_member_umrs"]),
                          sorted(sbr.OWNER_PRIORITY_PHASE1_MEMBERS))
        self.assertEqual(sorted(phases[1]["real_member_umrs"]),
                          sorted(sbr.OWNER_PRIORITY_PHASE2_MEMBERS))

        # Phase 3/4: real governing_umr from ocid_canonical_registry, real
        # non-empty discovered member set, governing UMR itself included.
        ocid_020_umr = sbr._lookup_ocid_governing_umr(self.conn, "OCID-020")
        ocid_021_umr = sbr._lookup_ocid_governing_umr(self.conn, "OCID-021")
        self.assertIsNotNone(ocid_020_umr)
        self.assertIsNotNone(ocid_021_umr)
        self.assertEqual(phases[2]["governing_umr"], ocid_020_umr)
        self.assertEqual(phases[3]["governing_umr"], ocid_021_umr)
        self.assertIn(ocid_020_umr, phases[2]["real_member_umrs"])
        self.assertIn(ocid_021_umr, phases[3]["real_member_umrs"])
        self.assertGreater(len(phases[2]["real_member_umrs"]), 1,
                            "expected real discovered children beyond the governing UMR itself")
        self.assertGreater(len(phases[3]["real_member_umrs"]), 1,
                            "expected real discovered children beyond the governing UMR itself")

        # Every single seeded id is a real row in umr_tasks (bounded to
        # real explicit ids, never a wildcard/fabrication).
        for phase in phases:
            for umr_id in phase["real_member_umrs"]:
                row = self.conn.execute(
                    "SELECT 1 FROM umr_tasks WHERE umr_id = ?", (umr_id,)
                ).fetchone()
                self.assertIsNotNone(row, f"{umr_id} (phase {phase['phase_order']}) is not a real umr_tasks row")

        # Re-running seed is a real no-op (idempotent).
        result2 = sbr.seed_owner_priority_sequence(self.conn)
        self.assertFalse(result2["seeded"])

    def test_starts_with_only_phase_1_active(self):
        sbr.seed_owner_priority_sequence(self.conn)
        rows = [dict(r) for r in self.conn.execute(
            "SELECT phase_order, status FROM owner_priority_sequence ORDER BY phase_order").fetchall()]
        self.assertEqual(rows, [
            {"phase_order": 1, "status": "active"},
            {"phase_order": 2, "status": "pending"},
            {"phase_order": 3, "status": "pending"},
            {"phase_order": 4, "status": "pending"},
        ])

        override_umrs = sorted(dict(r)["umr_id"] for r in self.conn.execute(
            "SELECT umr_id FROM owner_priority_override").fetchall())
        self.assertEqual(override_umrs, sorted(sbr.OWNER_PRIORITY_PHASE1_MEMBERS))

    def test_advances_phase_1_to_phase_2_once_members_genuinely_completed(self):
        sbr.seed_owner_priority_sequence(self.conn)

        # Sanity: a tick BEFORE members are genuinely completed makes no
        # transition (live state has ae93 'running', f432
        # 'completed_unmerged' -- neither is genuinely complete).
        pre = sbr.advance_owner_priority_phases(self.conn)
        self.assertFalse(pre["transitioned"])
        active = dict(self.conn.execute(
            "SELECT phase_order FROM owner_priority_sequence WHERE status='active'").fetchone())
        self.assertEqual(active["phase_order"], 1)

        # Real evidence, written into the COPY only: an absolute file_path
        # that genuinely exists on disk -- the same real artifact
        # validate_umr_terminal_completion_evidence()'s own status=completed
        # gate requires, not a bare status-label flip.
        real_evidence_file = SBR_PATH
        self.assertTrue(os.path.isfile(real_evidence_file))
        for umr_id in sbr.OWNER_PRIORITY_PHASE1_MEMBERS:
            self.conn.execute(
                "UPDATE umr_tasks SET status='completed', ts_completed=?, outputs_json=? WHERE umr_id=?",
                (sbr._now_iso(), json.dumps({"file_path": real_evidence_file, "repo": "veridian-scripts"}), umr_id),
            )
        self.conn.commit()

        # Re-verify the evidence check itself, standalone, before trusting
        # the full advance call.
        for umr_id in sbr.OWNER_PRIORITY_PHASE1_MEMBERS:
            ok, reason = sbr._umr_genuinely_completed(self.conn, umr_id)
            self.assertTrue(ok, reason)

        result = sbr.advance_owner_priority_phases(self.conn)
        self.assertTrue(result["transitioned"], result)
        self.assertEqual(result["new_active_phase"], 2)

        rows = [dict(r) for r in self.conn.execute(
            "SELECT phase_order, status FROM owner_priority_sequence ORDER BY phase_order").fetchall()]
        self.assertEqual(rows, [
            {"phase_order": 1, "status": "complete"},
            {"phase_order": 2, "status": "active"},
            {"phase_order": 3, "status": "pending"},
            {"phase_order": 4, "status": "pending"},
        ])

        # owner_priority_override now holds ONLY phase 2's real members --
        # phase 1's prior entries were removed, never left stale.
        override_umrs = sorted(dict(r)["umr_id"] for r in self.conn.execute(
            "SELECT umr_id FROM owner_priority_override").fetchall())
        self.assertEqual(override_umrs, sorted(sbr.OWNER_PRIORITY_PHASE2_MEMBERS))

        # A second tick with nothing newly completed makes no further
        # transition (phase 2's members are all still real, live,
        # incomplete rows) -- never activates more than one phase.
        result2 = sbr.advance_owner_priority_phases(self.conn)
        self.assertFalse(result2["transitioned"])
        active_rows = [dict(r) for r in self.conn.execute(
            "SELECT phase_order FROM owner_priority_sequence WHERE status='active'").fetchall()]
        self.assertEqual(len(active_rows), 1)
        self.assertEqual(active_rows[0]["phase_order"], 2)

    def test_never_more_than_one_active_phase_after_full_run(self):
        sbr.seed_owner_priority_sequence(self.conn)
        for umr_id in sbr.OWNER_PRIORITY_PHASE1_MEMBERS:
            self.conn.execute(
                "UPDATE umr_tasks SET status='completed', ts_completed=?, outputs_json=? WHERE umr_id=?",
                (sbr._now_iso(), json.dumps({"file_path": SBR_PATH, "repo": "veridian-scripts"}), umr_id),
            )
        self.conn.commit()
        sbr.advance_owner_priority_phases(self.conn)
        active_rows = self.conn.execute(
            "SELECT phase_order FROM owner_priority_sequence WHERE status='active'").fetchall()
        self.assertEqual(len(active_rows), 1)

    def test_live_db_untouched(self):
        """The copy-based tests above must never have written to the real
        live DB -- confirm no owner_priority_sequence table exists there
        as a side effect of importing/running this test module."""
        live_conn = sqlite3.connect(LIVE_DB)
        try:
            row = live_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='owner_priority_sequence'"
            ).fetchone()
            self.assertIsNone(row, "owner_priority_sequence must not exist on the LIVE db from this test run")
        finally:
            live_conn.close()


if __name__ == "__main__":
    unittest.main()
