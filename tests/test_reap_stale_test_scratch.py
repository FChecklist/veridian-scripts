#!/usr/bin/env python3
"""Real test for reap_stale_test_scratch.py (UMR-20260814-033442-c885, P0
disk exhaustion RCA's own periodic-reaper backstop). Runs against a real,
isolated scratch tmp-root (never the real live /tmp) via --tmp-dir."""
import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(HERE)
REAPER = os.path.join(SCRIPTS_DIR, "reap_stale_test_scratch.py")


class ReapStaleTestScratchTest(unittest.TestCase):
    def setUp(self):
        self.scratch_root = tempfile.mkdtemp(prefix="reaper_test_root_")
        self.addCleanup(__import__("shutil").rmtree, self.scratch_root, ignore_errors=True)

    def _make(self, name, age_hours, is_dir=True, size_bytes=0):
        path = os.path.join(self.scratch_root, name)
        if is_dir:
            os.makedirs(path)
            with open(os.path.join(path, "payload.bin"), "wb") as f:
                f.write(b"x" * size_bytes)
        else:
            with open(path, "wb") as f:
                f.write(b"x" * size_bytes)
        old_time = time.time() - age_hours * 3600
        os.utime(path, (old_time, old_time))
        return path

    def _run(self, *extra_args):
        return subprocess.run(
            [sys.executable, REAPER, "--tmp-dir", self.scratch_root, *extra_args],
            capture_output=True, text=True, timeout=30,
        )

    def test_removes_stale_matching_prefix_dirs_and_leaves_young_and_unmatched_alone(self):
        stale_pm = self._make("pm_sentinel_tick_test_abc123", age_hours=3, size_bytes=1024)
        stale_rule7 = self._make("rule7-real-persist-test-xyz", age_hours=5)
        young_pm = self._make("pm_sentinel_tick_test_fresh", age_hours=0.1)
        stale_sqlite = self._make("sbr-test-copy.sqlite", age_hours=10, is_dir=False, size_bytes=2048)
        unrelated = self._make("totally-unrelated-dir", age_hours=100)

        result = self._run()
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

        self.assertFalse(os.path.exists(stale_pm), "stale pm_sentinel_tick_ dir must be reaped")
        self.assertFalse(os.path.exists(stale_rule7), "stale rule7- dir must be reaped")
        self.assertFalse(os.path.exists(stale_sqlite), "stale standalone sqlite file must be reaped")
        self.assertTrue(os.path.exists(young_pm), "young (<2h) dir must NOT be reaped")
        self.assertTrue(os.path.exists(unrelated), "non-matching name must NEVER be touched")
        self.assertIn("3 removed", result.stdout)
        print("PASS: test_removes_stale_matching_prefix_dirs_and_leaves_young_and_unmatched_alone")

    def test_dry_run_deletes_nothing(self):
        stale_pm = self._make("pm_sentinel_tick_test_dryrun", age_hours=5)
        result = self._run("--dry-run")
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertTrue(os.path.exists(stale_pm), "dry-run must not delete anything")
        self.assertIn("DRY RUN", result.stdout)
        print("PASS: test_dry_run_deletes_nothing")

    def test_min_age_hours_override_respected(self):
        borderline = self._make("rg_queue_mgmt_test_borderline", age_hours=1.5)
        result = self._run("--min-age-hours", "1")
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertFalse(os.path.exists(borderline), "1.5h-old entry must be reaped with --min-age-hours 1")
        print("PASS: test_min_age_hours_override_respected")


if __name__ == "__main__":
    unittest.main()
