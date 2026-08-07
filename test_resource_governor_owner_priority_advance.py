#!/usr/bin/env python3
"""
Real test for resource_governor.py's real run_tick() wiring of the owner
priority phase advance (amendment to UMR-20260807-070110-5ea7, governed by
UMR-20260806-124055-bc80): confirms run_tick() calls
_advance_owner_priority_phases_safe() BEFORE the dispatch loop, and that it
degrades to a real, non-raising {'error': ...} outcome rather than crashing
run_tick() when the real Superboss Register connection is broken -- same
fail-open contract as every other _safe_superboss_register() call site.

Runs against a real COPY of the live DB (sqlite3 backup API, never a raw
file copy -- same corruption-avoidance reasoning as
test_owner_priority_sequence.py), never the live table. Never calls
systemctl/dispatch_one for real -- max_dispatches=0 short-circuits the
dispatch loop entirely so this test cannot spawn or kill any real worker.
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

_sbr_spec = _ilu.spec_from_file_location("owner_priority_rg_test_sbr_mod", SBR_PATH)
sbr = _ilu.module_from_spec(_sbr_spec)
sys.modules["owner_priority_rg_test_sbr_mod"] = sbr
_sbr_spec.loader.exec_module(sbr)

_rg_spec = _ilu.spec_from_file_location("owner_priority_rg_test_rg_mod", RG_PATH)
rg = _ilu.module_from_spec(_rg_spec)
sys.modules["owner_priority_rg_test_rg_mod"] = rg
_rg_spec.loader.exec_module(rg)


class RunTickOwnerPriorityAdvanceTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="rg_owner_priority_test_")
        self.copy_path = os.path.join(self.tmpdir, "superboss-register.sqlite")
        src = sqlite3.connect(f"file:{LIVE_DB}?mode=ro", uri=True)
        dst = sqlite3.connect(self.copy_path)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
        # Point the dynamically-loaded sbr module at the real COPY, not the
        # real live DB_PATH it resolved at import time.
        self._orig_db_path = sbr.DB_PATH
        sbr.DB_PATH = self.copy_path

        # run_tick()/dispatch_one() reach superboss-register.py through
        # _safe_superboss_register()'s own real importlib loader
        # (_superboss_register()); pin it to this test's sbr instance so
        # both the resource_governor module and this test see the exact
        # same (copy-pointed) module object.
        self._orig_safe = rg._safe_superboss_register
        rg._safe_superboss_register = lambda context: (sbr, None)

    def tearDown(self):
        sbr.DB_PATH = self._orig_db_path
        rg._safe_superboss_register = self._orig_safe
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_run_tick_calls_advance_before_dispatch_and_seeds_real_copy(self):
        # max_dispatches=0: the dispatch while-loop never executes (0 < 0
        # is False), so this can never spawn/kill a real worker -- isolates
        # exactly the owner-priority-advance wiring under test.
        result = rg.run_tick(max_dispatches=0)
        self.assertIn("owner_priority_phase_advance", result)
        self.assertEqual(result["dispatches"], [])
        advance = result["owner_priority_phase_advance"]
        self.assertNotIn("error", advance, advance)
        self.assertEqual(advance["evaluated_phase"], 1)

        conn = sqlite3.connect(self.copy_path)
        conn.row_factory = sqlite3.Row
        phases = [dict(r) for r in conn.execute(
            "SELECT phase_order, status FROM owner_priority_sequence ORDER BY phase_order").fetchall()]
        self.assertEqual(phases, [
            {"phase_order": 1, "status": "active"},
            {"phase_order": 2, "status": "pending"},
            {"phase_order": 3, "status": "pending"},
            {"phase_order": 4, "status": "pending"},
        ])
        conn.close()

    def test_fails_open_never_raises_when_superboss_register_unavailable(self):
        rg._safe_superboss_register = lambda context: (None, f"unavailable ({context})")
        result = rg.run_tick(max_dispatches=0)
        self.assertIn("owner_priority_phase_advance", result)
        self.assertIn("error", result["owner_priority_phase_advance"])


if __name__ == "__main__":
    unittest.main()
