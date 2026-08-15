#!/usr/bin/env python3
"""Real test for Part B of task-20260815-051128-prevent-register-corruption-
recurrence: resource_governor.py's detect_stuck_writelock() /
--writelock-staleness-scan.

Real incident this closes (2026-08-15, real, resolved): superboss-
register.sqlite's own .writelock file sat stuck for 5+ hours with NO
process actually holding it before anyone/anything noticed -- dispatch
silently degraded the whole time undetected. This file proves the real
fix fires on a synthetic old writelock, both at the function level and via
a real CLI subprocess invocation (the exact command
resource_governor_tick_loop.sh's own 30s loop now runs), and that it does
NOT fire on a fresh writelock or one genuinely held by a live process.
"""
import fcntl
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


class DetectStuckWritelockFunctionTest(unittest.TestCase):
    """Direct function-level coverage (fast, no subprocess)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="writelock_staleness_test_")
        self.db_path = os.path.join(self.tmpdir, "superboss-register.sqlite")
        self.sbr = _load("writelock_test_sbr", SBR_PATH)
        _seed_scratch_db(self.sbr, self.db_path)
        self.rg = _load("writelock_test_rg", RG_PATH)
        self.rg._safe_superboss_register = lambda context: (self.sbr, None)
        self.rg.WRITELOCK_STALE_SECONDS = 300

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_writelock_file_returns_none(self):
        self.assertIsNone(self.rg.detect_stuck_writelock())

    def test_fresh_writelock_file_not_flagged(self):
        with open(self.sbr._WRITE_LOCK_PATH, "w"):
            pass
        self.assertIsNone(self.rg.detect_stuck_writelock())

    def test_old_unheld_writelock_file_flagged_stuck(self):
        """The synthetic-old-writelock done-criteria case: an old file that
        nothing holds must be flagged."""
        lock_path = self.sbr._WRITE_LOCK_PATH
        with open(lock_path, "w"):
            pass
        old_mtime = time.time() - 3600  # 1 hour old -- far past the 5min threshold
        os.utime(lock_path, (old_mtime, old_mtime))

        result = self.rg.detect_stuck_writelock()
        self.assertIsNotNone(result)
        self.assertEqual(result["path"], lock_path)
        self.assertGreaterEqual(result["age_seconds"], 3500)
        self.assertEqual(result["threshold_seconds"], 300)

    def test_old_but_genuinely_held_writelock_not_flagged(self):
        """A real, live flock holder on an old-mtime file must NOT be
        flagged -- that is a legitimate long write, not an abandoned lock."""
        lock_path = self.sbr._WRITE_LOCK_PATH
        holder_script = (
            "import fcntl, time, sys\n"
            f"f = open({lock_path!r}, 'w')\n"
            "fcntl.flock(f, fcntl.LOCK_EX)\n"
            "sys.stdout.write('locked\\n'); sys.stdout.flush()\n"
            "time.sleep(5)\n"
        )
        proc = subprocess.Popen([sys.executable, "-c", holder_script],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            line = proc.stdout.readline()
            self.assertEqual(line.strip(), "locked", "holder subprocess never confirmed it acquired the flock")
            old_mtime = time.time() - 3600
            os.utime(lock_path, (old_mtime, old_mtime))

            result = self.rg.detect_stuck_writelock()
            self.assertIsNone(result, "an old file genuinely held by a live process must never be flagged stuck")
        finally:
            proc.kill()
            proc.wait(timeout=5)


class WritelockStalenessScanCliTest(unittest.TestCase):
    """Real end-to-end CLI subprocess test -- the exact command
    resource_governor_tick_loop.sh's own 30s loop runs."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="writelock_staleness_cli_test_")
        self.db_path = os.path.join(self.tmpdir, "superboss-register.sqlite")
        sbr = _load("writelock_cli_test_sbr_seed", SBR_PATH)
        _seed_scratch_db(sbr, self.db_path)
        self.attention_path = os.path.join(self.tmpdir, "ATTENTION.md")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_scan(self):
        env = dict(os.environ)
        env["SUPERBOSS_REGISTER_DB"] = self.db_path
        env["VERIDIAN_GOVERNOR_ATTENTION_PATH"] = self.attention_path
        env["VERIDIAN_GOVERNOR_WRITELOCK_STALE_SECONDS"] = "5"
        # resource_governor.py's _build_lock_liveness_guard() lazy loader
        # resolves build_lock_liveness_guard.py under SCRIPTS (default
        # /opt/veridian/scripts, the real LIVE deployed checkout) -- pin
        # this to THIS workspace checkout for consistency with the code
        # under test.
        env["VERIDIAN_SCRIPTS_DIR"] = SCRIPTS_DIR
        r = subprocess.run(
            [sys.executable, RG_PATH, "--writelock-staleness-scan"],
            env=env, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr}")
        return json.loads(r.stdout)

    def test_synthetic_old_writelock_fires_real_alert(self):
        lock_path = self.db_path + ".writelock"
        with open(lock_path, "w"):
            pass
        old_mtime = time.time() - 3600
        os.utime(lock_path, (old_mtime, old_mtime))

        out = self._run_scan()
        self.assertIsNotNone(out["stuck_writelock"])
        self.assertEqual(out["stuck_writelock"]["path"], lock_path)

        self.assertTrue(os.path.exists(self.attention_path))
        with open(self.attention_path) as f:
            content = f.read()
        self.assertIn("STUCK-WRITELOCK", content)
        self.assertIn(lock_path, content)

    def test_fresh_writelock_no_alert(self):
        lock_path = self.db_path + ".writelock"
        with open(lock_path, "w"):
            pass

        out = self._run_scan()
        self.assertIsNone(out["stuck_writelock"])
        self.assertFalse(os.path.exists(self.attention_path))

    def test_missing_writelock_no_alert(self):
        out = self._run_scan()
        self.assertIsNone(out["stuck_writelock"])
        self.assertFalse(os.path.exists(self.attention_path))


if __name__ == "__main__":
    unittest.main()
