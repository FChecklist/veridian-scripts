#!/usr/bin/env python3
"""Real tests for snapshot_memory_backup.py (proposal 89 point 1/2, child
UMR-20260806-140500-bkup).

Every test builds real sqlite files in a real tempdir and drives the
module's real run() against those real files -- no mocking of sqlite3 or
the filesystem. Follows the same real-file, no-mocking convention
test_prune_memory_backups.py already established for this exact module
pair.
"""
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_module():
    for stale in ("snapshot_memory_backup_test", "prune_memory_backups"):
        sys.modules.pop(stale, None)
    spec = importlib.util.spec_from_file_location(
        "snapshot_memory_backup_test", os.path.join(SCRIPTS_DIR, "snapshot_memory_backup.py"))
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


def test_writes_directly_into_backups_dir_never_memory_root():
    m = _load_module()
    with tempfile.TemporaryDirectory() as d:
        memory_root = os.path.join(d, "memory")
        backups_dir = os.path.join(memory_root, "backups")
        os.makedirs(memory_root, exist_ok=True)
        live_db = os.path.join(memory_root, "superboss-register.sqlite")
        _make_valid_db(live_db)

        report = m.run(live_db, backups_dir, keep=3, reason="test")
        assert report["decision"] == "written", report
        assert report["backup_path"].startswith(backups_dir + os.sep)
        # Never written directly into the memory root itself.
        assert os.path.dirname(report["backup_path"]) == backups_dir
        assert not os.path.exists(os.path.join(memory_root, os.path.basename(report["backup_path"])))


def test_fresh_copy_passes_real_integrity_check():
    m = _load_module()
    with tempfile.TemporaryDirectory() as d:
        backups_dir = os.path.join(d, "backups")
        live_db = os.path.join(d, "superboss-register.sqlite")
        _make_valid_db(live_db)

        report = m.run(live_db, backups_dir, keep=3, reason=None)
        assert report["decision"] == "written", report
        assert m._pmb.real_integrity_check(report["backup_path"]) is True


def test_refuses_when_live_db_fails_integrity_check():
    m = _load_module()
    with tempfile.TemporaryDirectory() as d:
        backups_dir = os.path.join(d, "backups")
        live_db = os.path.join(d, "superboss-register.sqlite")
        _make_corrupt_file(live_db)

        report = m.run(live_db, backups_dir, keep=3, reason=None)
        assert report["error"] == "LIVE_DB_INTEGRITY_CHECK_FAILED"
        assert not os.path.isdir(backups_dir) or os.listdir(backups_dir) == []


def test_hard_cap_refuses_a_4th_same_day_snapshot():
    """Three real, distinct, verified snapshots spaced well outside the
    recency window (recent_window_seconds=0 disables that check entirely
    for this test, isolating the hard-cap logic) -- the 4th same-UTC-day
    call is refused."""
    m = _load_module()
    with tempfile.TemporaryDirectory() as d:
        backups_dir = os.path.join(d, "backups")
        live_db = os.path.join(d, "superboss-register.sqlite")
        _make_valid_db(live_db)
        now = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)

        written = []
        for i in range(3):
            call_now = now + timedelta(hours=i)
            report = m.run(live_db, backups_dir, keep=3, reason=None, now=call_now,
                            recent_window_seconds=0)
            assert report["decision"] == "written", report
            written.append(report["backup_path"])
        assert len(set(written)) == 3

        report4 = m.run(live_db, backups_dir, keep=3, reason=None, now=now + timedelta(hours=4),
                         recent_window_seconds=0)
        assert report4["decision"] == "refused_daily_cap_reached", report4
        assert report4["todays_adhoc_count"] == 3
        assert "error" not in report4

        # A new UTC day resets the count to zero.
        next_day = now + timedelta(days=1)
        report5 = m.run(live_db, backups_dir, keep=3, reason=None, now=next_day, recent_window_seconds=0)
        assert report5["decision"] == "written", report5


def test_skip_when_a_recent_verified_backup_already_exists():
    m = _load_module()
    with tempfile.TemporaryDirectory() as d:
        backups_dir = os.path.join(d, "backups")
        live_db = os.path.join(d, "superboss-register.sqlite")
        _make_valid_db(live_db)
        now = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)

        first = m.run(live_db, backups_dir, keep=3, reason=None, now=now)
        assert first["decision"] == "written", first

        second = m.run(live_db, backups_dir, keep=3, reason=None, now=now + timedelta(minutes=5))
        assert second["decision"] == "skipped_recent_verified_exists", second
        assert second["existing_backup_age_seconds"] == 300
        # Still exactly one file on disk -- no redundant second copy written.
        assert len(os.listdir(backups_dir)) == 1


def test_outside_recency_window_is_not_skipped_but_still_capped_by_day():
    m = _load_module()
    with tempfile.TemporaryDirectory() as d:
        backups_dir = os.path.join(d, "backups")
        live_db = os.path.join(d, "superboss-register.sqlite")
        _make_valid_db(live_db)
        now = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)

        first = m.run(live_db, backups_dir, keep=3, reason=None, now=now)
        assert first["decision"] == "written", first

        # Well outside the default 900s recency window, same UTC day: a real
        # second snapshot IS taken (not skipped), and counts toward the cap.
        later = now + timedelta(hours=2)
        second = m.run(live_db, backups_dir, keep=3, reason=None, now=later)
        assert second["decision"] == "written", second
        assert second["todays_adhoc_count"] == 2


def test_corrupt_todays_backup_does_not_block_a_fresh_real_one():
    m = _load_module()
    with tempfile.TemporaryDirectory() as d:
        backups_dir = os.path.join(d, "backups")
        os.makedirs(backups_dir, exist_ok=True)
        live_db = os.path.join(d, "superboss-register.sqlite")
        _make_valid_db(live_db)
        now = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)

        # A pre-existing today's-date file that is corrupt -- must not be
        # mistaken for a "recent verified backup" that would cause a skip.
        stale_name = "superboss-register.sqlite.adhoc-pre-change-20260806T000000Z.bak"
        stale_path = os.path.join(backups_dir, stale_name)
        _make_corrupt_file(stale_path)
        ts = (now - timedelta(minutes=1)).timestamp()
        os.utime(stale_path, (ts, ts))

        report = m.run(live_db, backups_dir, keep=3, reason=None, now=now)
        assert report["decision"] == "written", report
        assert report["todays_adhoc_count"] == 2  # the corrupt one + the new real one


def test_fresh_copy_failing_integrity_check_is_deleted_and_reported(monkeypatch):
    m = _load_module()
    with tempfile.TemporaryDirectory() as d:
        backups_dir = os.path.join(d, "backups")
        live_db = os.path.join(d, "superboss-register.sqlite")
        _make_valid_db(live_db)

        def fake_copy(src, dest_path):
            _make_corrupt_file(dest_path)

        monkeypatch.setattr(m, "_real_backup_copy", fake_copy)
        report = m.run(live_db, backups_dir, keep=3, reason=None)
        assert report["error"] == "FRESH_COPY_FAILED_INTEGRITY_CHECK", report
        assert os.listdir(backups_dir) == []


def test_cli_json_output_is_well_formed():
    import subprocess
    with tempfile.TemporaryDirectory() as d:
        backups_dir = os.path.join(d, "backups")
        live_db = os.path.join(d, "superboss-register.sqlite")
        _make_valid_db(live_db)
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "snapshot_memory_backup.py"),
             "--live-db", live_db, "--backups-dir", backups_dir, "--reason", "test"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        out = json.loads(result.stdout)
        assert out["decision"] == "written"
        assert out["reason"] == "test"


def test_cli_bad_keep_argument_exits_2():
    import subprocess
    with tempfile.TemporaryDirectory() as d:
        live_db = os.path.join(d, "superboss-register.sqlite")
        _make_valid_db(live_db)
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "snapshot_memory_backup.py"),
             "--live-db", live_db, "--backups-dir", os.path.join(d, "backups"), "--keep", "0"],
            capture_output=True, text=True,
        )
        assert result.returncode == 2
