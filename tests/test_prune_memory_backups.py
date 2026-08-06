#!/usr/bin/env python3
"""Real tests for prune_memory_backups.py (Real Owner directive
UMR-20260806-084306-f599 Step 6, parent UMR-20260806-071025-1d28).

Every test builds real sqlite files (via sqlite3.connect + real DDL, or via
Connection.backup() for a byte-real copy) in a real tempdir and drives the
module's real functions (run()/main()) against those real files -- no
mocking of sqlite3 or the filesystem. Imports the module via the same
importlib-by-file-path pattern used elsewhere in this repo for hyphenated
filenames (this one has underscores so a plain import would also work, but
we keep the pattern consistent and load from an absolute path so tests
never accidentally pick up a stale installed copy).
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import time

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "prune_memory_backups_test", os.path.join(SCRIPTS_DIR, "prune_memory_backups.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_valid_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('x')")
    conn.commit()
    conn.close()


def _make_corrupt_file(path, content=b"not a real sqlite file at all"):
    with open(path, "wb") as f:
        f.write(content)


def _touch_mtime(path, ts):
    os.utime(path, (ts, ts))


def test_real_integrity_check_true_on_real_db():
    m = _load_module()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "real.sqlite")
        _make_valid_db(p)
        assert m.real_integrity_check(p) is True


def test_real_integrity_check_false_on_corrupt_file():
    m = _load_module()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "corrupt.sqlite")
        _make_corrupt_file(p)
        assert m.real_integrity_check(p) is False


def test_real_integrity_check_false_on_missing_file():
    m = _load_module()
    with tempfile.TemporaryDirectory() as d:
        assert m.real_integrity_check(os.path.join(d, "nope.sqlite")) is False


def test_real_integrity_check_false_on_zero_byte_file():
    m = _load_module()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "empty.sqlite")
        open(p, "wb").close()
        assert m.real_integrity_check(p) is False


def test_refuses_when_live_db_fails_integrity_check():
    """Hard safety gate: a corrupt live DB must abort the whole run with a
    real error and delete nothing, even if there are real prunable backups
    sitting right there."""
    m = _load_module()
    with tempfile.TemporaryDirectory() as mem_dir:
        backups_dir = os.path.join(mem_dir, "backups")
        os.makedirs(backups_dir)
        live_db = os.path.join(mem_dir, "superboss-register.sqlite")
        _make_corrupt_file(live_db)

        # A real, valid, prunable-looking backup that must survive untouched.
        bak = os.path.join(backups_dir, "superboss-register.sqlite.20260101T000000Z-verified.bak")
        _make_valid_db(bak)

        report = m.run(live_db, [mem_dir, backups_dir], keep=3, dry_run=False)

        assert report["error"] == "LIVE_DB_INTEGRITY_CHECK_FAILED"
        assert os.path.isfile(bak), "backup must not be deleted when live DB is unhealthy"

    # Also exercise the real CLI/exit-code path.
    with tempfile.TemporaryDirectory() as mem_dir:
        live_db = os.path.join(mem_dir, "superboss-register.sqlite")
        _make_corrupt_file(live_db)
        rc = m.main(["--live-db", live_db, "--scan-dir", mem_dir])
        assert rc == 1


def test_refuses_when_live_db_missing():
    m = _load_module()
    with tempfile.TemporaryDirectory() as mem_dir:
        rc = m.main(["--live-db", os.path.join(mem_dir, "does-not-exist.sqlite"), "--scan-dir", mem_dir])
        assert rc == 1


def test_dry_run_deletes_nothing_but_reports_the_real_plan():
    m = _load_module()
    with tempfile.TemporaryDirectory() as mem_dir:
        live_db = os.path.join(mem_dir, "superboss-register.sqlite")
        _make_valid_db(live_db)

        now = time.time()
        paths = []
        for i in range(5):
            p = os.path.join(mem_dir, f"superboss-register.sqlite.stage{i}-backup")
            _make_valid_db(p)
            _touch_mtime(p, now - i * 1000)  # stage0 newest .. stage4 oldest
            paths.append(p)

        report = m.run(live_db, [mem_dir], keep=3, dry_run=True)

        assert report["mode"] == "dry-run"
        assert len(report["kept"]) == 3
        assert len(report["deleted"]) == 2
        assert all(e["action"] == "would-delete" for e in report["deleted"])
        # Real filesystem check: dry-run must not have touched anything.
        for p in paths:
            assert os.path.isfile(p)
        # The two oldest (stage3, stage4) are the ones planned for deletion.
        deleted_names = {os.path.basename(e["main_path"]) for e in report["deleted"]}
        assert deleted_names == {"superboss-register.sqlite.stage3-backup",
                                  "superboss-register.sqlite.stage4-backup"}


def test_real_run_keeps_3_most_recent_verified_deletes_rest():
    m = _load_module()
    with tempfile.TemporaryDirectory() as mem_dir:
        live_db = os.path.join(mem_dir, "superboss-register.sqlite")
        _make_valid_db(live_db)

        now = time.time()
        kept_expected = []
        for i in range(3):
            p = os.path.join(mem_dir, f"superboss-register.sqlite.keep{i}-backup")
            _make_valid_db(p)
            _touch_mtime(p, now - i * 10)
            kept_expected.append(p)

        deleted_expected = []
        for i in range(4):
            p = os.path.join(mem_dir, f"superboss-register.sqlite.old{i}-backup")
            _make_valid_db(p)
            _touch_mtime(p, now - 100000 - i * 10)
            deleted_expected.append(p)

        report = m.run(live_db, [mem_dir], keep=3, dry_run=False)

        assert "error" not in report
        assert report["reclaimed_bytes"] > 0
        for p in kept_expected:
            assert os.path.isfile(p), f"{p} should have been kept"
        for p in deleted_expected:
            assert not os.path.isfile(p), f"{p} should have been deleted"
        # Live DB itself must never be touched.
        assert os.path.isfile(live_db)
        assert m.real_integrity_check(live_db) is True


def test_unverified_corrupt_backup_never_counts_toward_keep_and_is_deleted():
    m = _load_module()
    with tempfile.TemporaryDirectory() as mem_dir:
        live_db = os.path.join(mem_dir, "superboss-register.sqlite")
        _make_valid_db(live_db)

        now = time.time()
        # Newest file by mtime is CORRUPT -- must not occupy a keep-slot,
        # and must still be deleted as junk (not silently left behind).
        corrupt = os.path.join(mem_dir, "superboss-register.sqlite.CORRUPTED-newest")
        _make_corrupt_file(corrupt)
        _touch_mtime(corrupt, now)

        valid = []
        for i in range(3):
            p = os.path.join(mem_dir, f"superboss-register.sqlite.valid{i}-backup")
            _make_valid_db(p)
            _touch_mtime(p, now - 100 - i * 10)
            valid.append(p)

        report = m.run(live_db, [mem_dir], keep=3, dry_run=False)

        assert not os.path.isfile(corrupt), "corrupt backup must be pruned, never kept"
        for p in valid:
            assert os.path.isfile(p)
        kept_paths = {e["main_path"] for e in report["kept"]}
        assert corrupt not in kept_paths


def test_wal_shm_companions_are_grouped_and_deleted_together():
    m = _load_module()
    with tempfile.TemporaryDirectory() as mem_dir:
        live_db = os.path.join(mem_dir, "superboss-register.sqlite")
        _make_valid_db(live_db)

        now = time.time()
        # 4 groups so at least one (the oldest) is pruned; give it companions.
        for i in range(4):
            base = os.path.join(mem_dir, f"superboss-register.sqlite.group{i}-backup")
            _make_valid_db(base)
            _touch_mtime(base, now - i * 1000)
            if i == 3:  # oldest -- expected to be pruned
                with open(base + "-wal", "wb") as f:
                    f.write(b"fake-wal")
                with open(base + "-shm", "wb") as f:
                    f.write(b"fake-shm")

        oldest_base = os.path.join(mem_dir, "superboss-register.sqlite.group3-backup")
        report = m.run(live_db, [mem_dir], keep=3, dry_run=False)

        assert not os.path.isfile(oldest_base)
        assert not os.path.isfile(oldest_base + "-wal")
        assert not os.path.isfile(oldest_base + "-shm")


def test_orphan_wal_without_main_file_is_always_a_deletion_candidate():
    m = _load_module()
    with tempfile.TemporaryDirectory() as mem_dir:
        live_db = os.path.join(mem_dir, "superboss-register.sqlite")
        _make_valid_db(live_db)

        orphan_wal = os.path.join(mem_dir, "superboss-register.sqlite.rebuild-2026-07-23c-wal")
        with open(orphan_wal, "wb") as f:
            f.write(b"orphan-wal-no-main-file")

        report = m.run(live_db, [mem_dir], keep=3, dry_run=False)
        assert "error" not in report
        assert not os.path.isfile(orphan_wal)


def test_live_db_own_companions_are_never_discovered_or_touched():
    m = _load_module()
    with tempfile.TemporaryDirectory() as mem_dir:
        live_db = os.path.join(mem_dir, "superboss-register.sqlite")
        _make_valid_db(live_db)
        live_wal = live_db + "-wal"
        live_shm = live_db + "-shm"
        live_writelock = live_db + ".writelock"
        hidden_lock = os.path.join(mem_dir, ".superboss_write.lock")
        for p in (live_wal, live_shm, live_writelock, hidden_lock):
            open(p, "wb").close()

        groups = m.discover_backup_groups([mem_dir])
        discovered_paths = set()
        for g in groups:
            discovered_paths.update(m.group_paths(g))

        for protected in (live_db, live_wal, live_shm, live_writelock, hidden_lock):
            assert protected not in discovered_paths

        report = m.run(live_db, [mem_dir], keep=3, dry_run=False)
        assert "error" not in report
        for p in (live_db, live_wal, live_shm, live_writelock, hidden_lock):
            assert os.path.isfile(p)


def test_keep_override():
    m = _load_module()
    with tempfile.TemporaryDirectory() as mem_dir:
        live_db = os.path.join(mem_dir, "superboss-register.sqlite")
        _make_valid_db(live_db)
        now = time.time()
        for i in range(5):
            p = os.path.join(mem_dir, f"superboss-register.sqlite.k{i}-backup")
            _make_valid_db(p)
            _touch_mtime(p, now - i * 10)
        report = m.run(live_db, [mem_dir], keep=1, dry_run=False)
        assert len(report["kept"]) == 1
        assert len(report["deleted"]) == 4


def test_two_scan_dirs_compete_for_the_same_keep_budget():
    """Backups in memory/ root and memory/backups/ must be ranked together
    against one combined keep-3 budget, not 3-per-directory."""
    m = _load_module()
    with tempfile.TemporaryDirectory() as mem_dir:
        backups_dir = os.path.join(mem_dir, "backups")
        os.makedirs(backups_dir)
        live_db = os.path.join(mem_dir, "superboss-register.sqlite")
        _make_valid_db(live_db)

        now = time.time()
        for i in range(3):
            p = os.path.join(mem_dir, f"superboss-register.sqlite.root{i}-backup")
            _make_valid_db(p)
            _touch_mtime(p, now - i * 10)
        for i in range(3):
            p = os.path.join(backups_dir, f"superboss-register.sqlite.b{i}-backup")
            _make_valid_db(p)
            _touch_mtime(p, now - 1000 - i * 10)

        report = m.run(live_db, [mem_dir, backups_dir], keep=3, dry_run=False)
        assert len(report["kept"]) == 3
        assert all("root" in os.path.basename(e["main_path"]) for e in report["kept"])
        assert len(report["deleted"]) == 3
        assert all("b" in os.path.basename(e["main_path"])[len("superboss-register.sqlite."):]
                   for e in report["deleted"])


def test_cli_json_output_is_well_formed():
    m = _load_module()
    with tempfile.TemporaryDirectory() as mem_dir:
        live_db = os.path.join(mem_dir, "superboss-register.sqlite")
        _make_valid_db(live_db)
        import subprocess
        out = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "prune_memory_backups.py"),
             "--dry-run", "--live-db", live_db, "--scan-dir", mem_dir],
            capture_output=True, text=True, timeout=60)
        assert out.returncode == 0
        parsed = json.loads(out.stdout)
        assert parsed["mode"] == "dry-run"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
