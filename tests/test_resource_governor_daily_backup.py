#!/usr/bin/env python3
"""Real test for Part C of task-20260815-051128-prevent-register-corruption-
recurrence: resource_governor.py's run_daily_backup_check() /
--daily-backup-check.

Real incident this closes (2026-08-15, real, resolved): the real backup
cadence produced only 3 snapshots on a single day (2026-08-06) then
nothing for 8 more days, forcing recovery from an 8-day-stale backup when
the corruption hit. This file proves a real fresh backup is actually
produced by this mechanism (both at the function level and via a real CLI
subprocess invocation -- the exact command resource_governor_tick_loop.sh's
own 30s loop now runs), that it reuses full_server_file_registration.py's
own take_backup() (same superboss-register.sqlite.pre-fullfile-backup-<ts>
naming convention, no new one invented), and that it is genuinely
self-throttled (a fresh existing backup means no new one is taken).
"""
import glob
import importlib.util as _ilu
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RG_PATH = os.path.join(SCRIPTS_DIR, "resource_governor.py")
SBR_PATH = os.path.join(SCRIPTS_DIR, "superboss-register.py")


def _load(name, path):
    spec = _ilu.spec_from_file_location(name, path)
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _seed_scratch_db(sbr, path):
    sbr.DB_PATH = path
    sbr._WRITE_LOCK_PATH = path + ".writelock"
    sbr.init_db()
    conn = sbr._connect()
    sbr._ensure_umr_table(conn)
    conn.close()


class RunDailyBackupCheckFunctionTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="daily_backup_test_")
        self.memory_dir = os.path.join(self.tmpdir, "memory")
        self.backups_dir = os.path.join(self.memory_dir, "backups")
        os.makedirs(self.backups_dir, exist_ok=True)
        self.db_path = os.path.join(self.memory_dir, "superboss-register.sqlite")

        self.sbr = _load("daily_backup_test_sbr", SBR_PATH)
        _seed_scratch_db(self.sbr, self.db_path)

        self.rg = _load("daily_backup_test_rg", RG_PATH)
        self.rg._safe_superboss_register = lambda context: (self.sbr, None)
        self.rg.DAILY_BACKUP_STALE_SECONDS = 24 * 3600

        # Point the reused modules' own MEMORY_DIR/BACKUPS_DIR + DB_PATH at
        # this scratch tree -- same "set module globals directly" technique
        # every other real test in this repo uses for cross-module reuse.
        self.pmb = self.rg._prune_memory_backups()
        self.pmb.MEMORY_DIR = self.memory_dir
        self.pmb.BACKUPS_DIR = self.backups_dir
        self.pmb.LIVE_DB = self.db_path

        self.ffr = self.rg._full_server_file_registration()
        self.ffr.BACKUPS_DIR = self.backups_dir
        self.ffr._sbr = self.sbr  # ffr.sbr() lazy-loader cache -- point it at our seeded scratch module

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _backup_files(self):
        # take_backup()'s destination inherits WAL mode from the live
        # source, so a single real backup can leave -wal/-shm companions
        # alongside the real main file -- exclude those here (same
        # main-file-only counting prune_memory_backups.py's own
        # discover_backup_groups() does) so this counts real BACKUPS, not
        # real FILES.
        all_files = glob.glob(os.path.join(self.backups_dir, "superboss-register.sqlite.pre-fullfile-backup-*"))
        return [p for p in all_files if not (p.endswith("-wal") or p.endswith("-shm"))]

    def test_no_existing_backup_takes_a_real_fresh_one(self):
        self.assertEqual(self._backup_files(), [])
        result = self.rg.run_daily_backup_check()
        self.assertEqual(result["action"], "backed_up")
        self.assertIsNone(result["newest_backup_age_seconds"])

        files = self._backup_files()
        self.assertEqual(len(files), 1)
        self.assertEqual(result["backup_path"], files[0])

        # The real done-criteria check: a real fresh backup was produced,
        # and it is a real, valid, verified sqlite file (not a stub).
        conn = sqlite3.connect(files[0])
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
            self.assertEqual(rows, [("ok",)])
            self.assertIsNotNone(
                conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='umr_tasks'").fetchone()
            )
        finally:
            conn.close()

    def test_fresh_existing_verified_backup_is_skipped(self):
        first = self.rg.run_daily_backup_check()
        self.assertEqual(first["action"], "backed_up")
        self.assertEqual(len(self._backup_files()), 1)

        second = self.rg.run_daily_backup_check()
        self.assertEqual(second["action"], "skipped")
        self.assertLess(second["newest_backup_age_seconds"], 5)
        # Self-throttling confirmed: still exactly one backup file, not two.
        self.assertEqual(len(self._backup_files()), 1)

    def test_stale_existing_backup_triggers_a_new_one(self):
        first = self.rg.run_daily_backup_check()
        first_path = first["backup_path"]
        old_mtime = time.time() - (25 * 3600)  # 25h old -- past the 24h threshold
        os.utime(first_path, (old_mtime, old_mtime))

        # take_backup()'s filename has second-granularity; wait past the
        # second boundary so the second real backup gets a genuinely
        # distinct name instead of overwriting the first (a real, harmless
        # property of the reused naming convention -- not something this
        # test needs to work around beyond a real sleep).
        time.sleep(1.1)

        second = self.rg.run_daily_backup_check()
        self.assertEqual(second["action"], "backed_up")
        self.assertGreaterEqual(second["newest_backup_age_seconds"], 25 * 3600 - 5)
        self.assertEqual(len(self._backup_files()), 2)


class DailyBackupCheckCliTest(unittest.TestCase):
    """Real end-to-end CLI subprocess test -- the exact command
    resource_governor_tick_loop.sh's own 30s loop runs."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="daily_backup_cli_test_")
        self.memory_dir = os.path.join(self.tmpdir, "memory")
        self.backups_dir = os.path.join(self.memory_dir, "backups")
        os.makedirs(self.backups_dir, exist_ok=True)
        self.db_path = os.path.join(self.memory_dir, "superboss-register.sqlite")
        sbr = _load("daily_backup_cli_test_sbr_seed", SBR_PATH)
        _seed_scratch_db(sbr, self.db_path)
        self.attention_path = os.path.join(self.tmpdir, "ATTENTION.md")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_real_cli_invocation_produces_a_real_backup_file(self):
        env = dict(os.environ)
        env["SUPERBOSS_REGISTER_DB"] = self.db_path
        env["VERIDIAN_GOVERNOR_ATTENTION_PATH"] = self.attention_path
        # resource_governor.py's own lazy loaders (_prune_memory_backups(),
        # _full_server_file_registration()) resolve sibling scripts under
        # SCRIPTS (default /opt/veridian/scripts, the real LIVE deployed
        # checkout) -- pin this to THIS workspace checkout so the
        # subprocess exercises the real code under test, not an unrelated
        # production copy.
        env["VERIDIAN_SCRIPTS_DIR"] = SCRIPTS_DIR
        # full_server_file_registration.py's take_backup() defaults
        # BACKUPS_DIR to the real production path, and
        # prune_memory_backups.py's discover_backup_groups() (reused by
        # newest_backup_mtime() to find the newest EXISTING backup)
        # defaults MEMORY_DIR/BACKUPS_DIR the same way -- redirect all of
        # them via their real env-override seams so this real CLI run
        # never touches /opt/veridian/ai-os/memory or its backups/ dir.
        env["VERIDIAN_FFR_BACKUPS_DIR"] = self.backups_dir
        env["VERIDIAN_PMB_MEMORY_DIR"] = self.memory_dir
        env["VERIDIAN_PMB_BACKUPS_DIR"] = self.backups_dir
        env["VERIDIAN_PMB_LIVE_DB"] = self.db_path
        r = subprocess.run(
            [sys.executable, RG_PATH, "--daily-backup-check"],
            env=env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr}")
        out = json.loads(r.stdout)
        self.assertEqual(out["action"], "backed_up")
        self.assertTrue(os.path.isfile(out["backup_path"]))
        self.assertIn("pre-fullfile-backup-", os.path.basename(out["backup_path"]))

        conn = sqlite3.connect(out["backup_path"])
        try:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
