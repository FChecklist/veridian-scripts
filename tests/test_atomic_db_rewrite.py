#!/usr/bin/env python3
"""Real test for Part A of task-20260815-051128-prevent-register-corruption-
recurrence: superboss-register.py's atomic_replace_live_db() must survive a
process being killed mid-write without corrupting the live file it targets.

Real incident this closes (2026-08-15, real, resolved): superboss-
register.sqlite (2.9GB) was found with a corrupted header; `sqlite3
.recover` confirmed zero salvageable data. Root cause pointed to an
unreleased writelock file carrying the exact same timestamp as the
corruption (stuck 5+ hours, no process holding it) -- consistent with a
process interrupted mid-write directly against the live file path.
Recovery required falling back to an 8-day-stale backup.

This file proves the real fix with a real subprocess kill, not a mock:
build_temp_db() is given real wall-clock time to run (real sqlite inserts +
real sleeps between batches), a real subprocess running it is SIGKILLed
partway through, and the test then confirms the real live file at
db_path is byte-identical to what it was before the kill and still passes
a real PRAGMA integrity_check -- i.e. the kill landed inside build_temp_db,
strictly before atomic_replace_live_db()'s only real write to the live
path (the final os.replace()), so the live file was never touched.
"""
import hashlib
import importlib.util
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def _load_sbr(modname="sbr_atomic_rewrite_test"):
    spec = importlib.util.spec_from_file_location(modname, os.path.join(SCRIPTS_DIR, "superboss-register.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _seed_scratch_db(path):
    """Same bootstrap technique tests/test_build_lock_contended_requeue.py
    already established: set DB_PATH directly on the module object and call
    the real init_db() against exactly that path, never through
    resolve_superboss_db_path()'s own env-var fallback (which only accepts
    a candidate that ALREADY exists -- a not-yet-created scratch path would
    silently fall through to the real, live production DB otherwise)."""
    sbr = _load_sbr()
    sbr.DB_PATH = path
    sbr._WRITE_LOCK_PATH = path + ".writelock"
    sbr.init_db()
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO instructions (instruction_id, ts, utm_source, utm_medium, raw_text) "
        "VALUES ('INS-TEST-0001', '2026-08-15T00:00:00+00:00', 'owner', 'ssh_session', 'seed row')"
    )
    conn.commit()
    conn.close()
    return sbr


def _file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


class AtomicDbRewriteTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="atomic-db-rewrite-test-")
        self.db_path = os.path.join(self.tmpdir, "superboss-register.sqlite")
        _seed_scratch_db(self.db_path)
        self.original_hash = _file_hash(self.db_path)
        self.original_row_count = self._row_count()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _row_count(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return conn.execute("SELECT COUNT(*) FROM instructions").fetchone()[0]
        finally:
            conn.close()

    def _real_integrity_check(self):
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            conn.close()
        return len(rows) == 1 and rows[0][0] == "ok"

    def test_kill_mid_write_leaves_live_file_untouched(self):
        """The real done-criteria test: kill the write mid-way, confirm the
        live file is untouched/still valid."""
        script = textwrap.dedent(f"""
            import importlib.util, sqlite3, time
            spec = importlib.util.spec_from_file_location(
                "sbr_slow_build", {os.path.join(SCRIPTS_DIR, "superboss-register.py")!r})
            sbr = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(sbr)

            def slow_build(tmp_path):
                conn = sqlite3.connect(tmp_path)
                conn.execute("CREATE TABLE t (x INTEGER)")
                for i in range(60):
                    conn.execute("INSERT INTO t VALUES (?)", (i,))
                    conn.commit()
                    time.sleep(0.1)
                conn.close()

            sbr.atomic_replace_live_db(slow_build, db_path={self.db_path!r})
        """)
        script_path = os.path.join(self.tmpdir, "slow_rewrite.py")
        with open(script_path, "w") as f:
            f.write(script)

        proc = subprocess.Popen([sys.executable, script_path],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            time.sleep(1.0)  # partway through the 60*0.1s=6s slow build, well before the final rename
            self.assertIsNone(
                proc.poll(),
                "subprocess exited before we could kill it mid-write -- test timing needs adjustment",
            )
            os.kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)

        # The real, load-bearing assertions: the live file is BYTE-IDENTICAL
        # to what it was before this ever ran, and still passes a real
        # integrity check -- a process killed mid-write never touched it.
        self.assertEqual(
            _file_hash(self.db_path), self.original_hash,
            "live DB file changed even though the writer was killed mid-write -- "
            "atomic_replace_live_db() must never touch the live path before the final atomic rename",
        )
        self.assertTrue(self._real_integrity_check(), "live DB failed a real PRAGMA integrity_check after the kill")
        self.assertEqual(self._row_count(), self.original_row_count)

    def test_successful_rewrite_actually_replaces_the_live_file(self):
        sbr = _load_sbr("sbr_atomic_rewrite_success_test")

        def build(tmp_path):
            conn = sqlite3.connect(tmp_path)
            conn.execute("CREATE TABLE t (x INTEGER)")
            conn.execute("INSERT INTO t VALUES (1)")
            conn.commit()
            conn.close()

        sbr.atomic_replace_live_db(build, db_path=self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(conn.execute("SELECT x FROM t").fetchone()[0], 1)
        finally:
            conn.close()
        self.assertTrue(self._real_integrity_check())

    def test_failed_header_validation_never_touches_live_file(self):
        sbr = _load_sbr("sbr_atomic_rewrite_garbage_test")

        def build_garbage(tmp_path):
            with open(tmp_path, "wb") as f:
                f.write(b"not a real sqlite file")

        with self.assertRaises(RuntimeError):
            sbr.atomic_replace_live_db(build_garbage, db_path=self.db_path)
        self.assertEqual(_file_hash(self.db_path), self.original_hash)

    def test_build_exception_never_touches_live_file(self):
        sbr = _load_sbr("sbr_atomic_rewrite_exception_test")

        def build_that_raises(tmp_path):
            with open(tmp_path, "wb") as f:
                f.write(b"partial garbage")
            raise RuntimeError("simulated build failure")

        with self.assertRaises(RuntimeError):
            sbr.atomic_replace_live_db(build_that_raises, db_path=self.db_path)
        self.assertEqual(_file_hash(self.db_path), self.original_hash)

    def test_vacuum_compact_db_real_compaction(self):
        sbr = _load_sbr("sbr_atomic_rewrite_vacuum_test")
        sbr.DB_PATH = self.db_path
        sbr._WRITE_LOCK_PATH = self.db_path + ".writelock"
        result_path = sbr.vacuum_compact_db(db_path=self.db_path)
        self.assertEqual(result_path, self.db_path)
        conn = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM instructions").fetchone()[0],
                self.original_row_count,
            )
        finally:
            conn.close()
        self.assertTrue(self._real_integrity_check())


if __name__ == "__main__":
    unittest.main()
