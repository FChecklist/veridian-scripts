#!/usr/bin/env python3
"""snapshot_memory_backup.py -- the real, single shared helper for taking an
ad-hoc pre-change snapshot of superboss-register.sqlite (proposal 89, child
UMR-20260806-140500-bkup, governing UMR-20260806-071025-1d28, disk
remediation UMR-20260806-084306-f599).

Root problem this fixes: proposal 89 itself proves agents/sessions really do
take ad-hoc "just in case" snapshots inline (a plain `cp` or equivalent)
directly into the memory root, with no shared helper and no retention
discipline -- two real straggler examples were still sitting in the memory
root at the time this was written:
  superboss-register.sqlite.pre-dedup-constraint-backup-20260731-093417Z
  superboss-register.sqlite.pre-systemd-backup-20260806T133738Z
Neither was ever written by any script in this repo (grepped for both exact
literal strings across veridian-scripts -- zero real hits) -- both are
genuinely hand-rolled, one-off inline copies. This script is the real fix:
one shared, safe, capped, real code path everyone can call instead.

Point 1 -- writes directly into BACKUPS_DIR (.../memory/backups), NEVER the
memory root, full stop.

Point 1 -- reuses prune_memory_backups.py's own real_integrity_check()
(PRAGMA integrity_check against a real, read-only-opened SQLite connection)
by importing that exact function, not reimplementing it -- same "one real
canonical implementation, not a second parallel one" discipline
resolve_superboss_db_path() documents for DB_PATH itself.

Point 2 -- PM decision (2026-08-06, dispatch UMR-20260806-142639-6fc3):
chose the simple hard cap over proposal 89's own per-change reason-tag
alternative, specifically to avoid introducing a second, competing
retention convention alongside prune_memory_backups.py's own real --keep
default. HARD CAP: at most KEEP_DEFAULT (3, same literal default as
prune_memory_backups.py's --keep) ad-hoc snapshots THIS helper has EVER
written for the current real UTC day, counted in total. Once that many
already exist for today, this call refuses -- exits non-zero, writes
nothing -- rather than silently keeping going forever. A new UTC day resets
the count to zero; prune_memory_backups.py (run separately, on its own
schedule) is what eventually reclaims yesterday's-and-older ad-hoc
snapshots via its own real verified-newest-N policy across the whole
directory -- this script's own per-day cap and that script's own
across-the-board retention are two different real safeguards, not the same
one written twice.

SKIP-IF-A-RECENT-VERIFIED-BACKUP-EXISTS: before writing anything, if the
MOST RECENT today's-date ad-hoc snapshot was written within the last
RECENT_WINDOW_SECONDS (default 900s / 15 min) AND still passes a real,
fresh integrity_check right now, this call is a real no-op (exit 0, nothing
written, decision='skipped_recent_verified_exists') -- two calls this close
together are almost always the exact same real pre-change moment (e.g. two
cooperating processes both taking a belt-and-suspenders snapshot around the
same edit), so a second verified copy of the same effectively-current DB
state is just more of the redundant-copy problem prune_memory_backups.py
was written to clean up after, never new real protection. This is
deliberately a SHORT recency window, not "any time today" -- the separate
hard cap below is what bounds the whole day; if "recent" also meant "today"
the two checks would fight each other (the 2nd of 3 genuinely-spaced-out
real pre-change snapshots would always be skipped, and the cap could never
actually be reached). The most-recent snapshot failing a fresh
integrity_check does NOT trigger a skip -- it is corrupt/stale, not a real
safety net, so a fresh one is still taken (subject to the hard cap below).

The real copy itself uses sqlite3's own online backup API
(Connection.backup()), never a raw file copy -- the live DB runs in WAL
mode (superboss-register.sqlite-wal/-shm sit alongside it), and a plain
`cp`/shutil.copy of just the main file while it's open for write can copy a
torn, inconsistent snapshot. Connection.backup() is SQLite's own real,
online-safe mechanism for exactly this. The fresh copy is verified with the
same real_integrity_check() immediately after writing -- an unverified
result deletes the just-written file and reports a real error rather than
leaving a silently-corrupt "backup" behind for prune_memory_backups.py (or
a real recovery attempt) to trip over later.

Usage:
    snapshot_memory_backup.py                    # take a real ad-hoc snapshot (or real no-op if today's cap/skip conditions apply)
    snapshot_memory_backup.py --reason "why"      # real free-text note recorded in the JSON report only (never part of the cap/skip logic)
    snapshot_memory_backup.py --live-db PATH --backups-dir DIR   # override paths (testing)

Exit codes: 0 real snapshot written OR real no-op (skip/cap), 1 live DB
failed integrity_check (refused, nothing touched) or the fresh copy itself
failed verification, 2 bad arguments.
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import prune_memory_backups as _pmb  # real, single shared integrity-check implementation

MEMORY_DIR = _pmb.MEMORY_DIR
BACKUPS_DIR = _pmb.BACKUPS_DIR
LIVE_DB = _pmb.LIVE_DB
DB_BASENAME = _pmb.DB_BASENAME
KEEP_DEFAULT = _pmb.KEEP_DEFAULT  # 3 -- same literal default as prune_memory_backups.py --keep

ADHOC_TAG = "adhoc-pre-change"
RECENT_WINDOW_SECONDS_DEFAULT = 900  # 15 min -- see SKIP-IF-A-RECENT-VERIFIED-BACKUP-EXISTS above


def _today_utc_compact(now):
    return now.strftime("%Y%m%d")


def _adhoc_prefix_for_today(now):
    return f"{DB_BASENAME}.{ADHOC_TAG}-{_today_utc_compact(now)}"


def _adhoc_filename_now(now):
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    return f"{DB_BASENAME}.{ADHOC_TAG}-{ts}.bak"


def _todays_adhoc_snapshots(backups_dir, now):
    """Real filenames (not full paths) of every ad-hoc snapshot THIS helper
    has already written today, found directly in backups_dir -- never a
    guess, a real os.listdir() scoped to today's real UTC date prefix."""
    if not os.path.isdir(backups_dir):
        return []
    prefix = _adhoc_prefix_for_today(now)
    return sorted(f for f in os.listdir(backups_dir) if f.startswith(prefix) and os.path.isfile(os.path.join(backups_dir, f)))


def _real_backup_copy(live_db, dest_path):
    """Real, online-safe copy via sqlite3's own Connection.backup() API --
    safe against a live WAL-mode writer, unlike a raw file copy."""
    src = sqlite3.connect(f"file:{os.path.abspath(live_db)}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dest_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def run(live_db, backups_dir, keep, reason, now=None, recent_window_seconds=RECENT_WINDOW_SECONDS_DEFAULT):
    """Real entry point (importable for tests). Returns a real report dict;
    never raises for the normal refuse/skip cases -- those are signalled via
    report['error'] or report['decision'] so callers/tests can inspect
    without catching SystemExit."""
    now = now or datetime.now(timezone.utc)
    report = {
        "timestamp_utc": now.isoformat(),
        "live_db": live_db,
        "backups_dir": backups_dir,
        "keep_cap": keep,
        "reason": reason,
    }

    # Hard safety gate (reused convention, prune_memory_backups.py's own):
    # refuse -- zero writes -- unless the LIVE database itself first passes
    # a real PRAGMA integrity_check.
    if not _pmb.real_integrity_check(live_db):
        report["error"] = "LIVE_DB_INTEGRITY_CHECK_FAILED"
        report["message"] = (
            f"Refusing to snapshot: live database at {live_db} failed PRAGMA "
            "integrity_check (or is missing/zero-byte/unreadable). Writing a "
            "snapshot of an already-unhealthy DB is not a real safety net."
        )
        return report

    os.makedirs(backups_dir, exist_ok=True)

    # SKIP-IF-A-RECENT-VERIFIED-BACKUP-EXISTS: only the MOST RECENT today's
    # snapshot is eligible, and only if it is within recent_window_seconds of
    # `now` -- a short recency window, deliberately NOT "any time today" (see
    # module docstring for why that distinction is required for the hard cap
    # below to ever actually be reachable).
    todays = _todays_adhoc_snapshots(backups_dir, now)
    if todays:
        most_recent = todays[-1]  # filenames sort lexicographically == chronologically (YYYYMMDDTHHMMSSZ)
        most_recent_path = os.path.join(backups_dir, most_recent)
        age_seconds = (now - datetime.fromtimestamp(os.path.getmtime(most_recent_path), tz=timezone.utc)).total_seconds()
        if age_seconds <= recent_window_seconds and _pmb.real_integrity_check(most_recent_path):
            report["decision"] = "skipped_recent_verified_exists"
            report["existing_verified_backup"] = most_recent_path
            report["existing_backup_age_seconds"] = age_seconds
            report["todays_adhoc_count"] = len(todays)
            return report

    # HARD CAP: at most `keep` ad-hoc snapshots for today, counted in total
    # (verified or not -- an unverified one still occupies a real cap slot;
    # it is prune_memory_backups.py's job, not this helper's, to ever delete
    # one).
    if len(todays) >= keep:
        report["decision"] = "refused_daily_cap_reached"
        report["todays_adhoc_count"] = len(todays)
        report["todays_adhoc_files"] = todays
        report["message"] = (
            f"Refusing to write a new ad-hoc snapshot: {len(todays)} already exist for "
            f"today (UTC {_today_utc_compact(now)}), at or above the hard cap of {keep}. "
            "Run prune_memory_backups.py, or wait for the next UTC day, before taking another."
        )
        return report

    dest_name = _adhoc_filename_now(now)
    dest_path = os.path.join(backups_dir, dest_name)
    _real_backup_copy(live_db, dest_path)
    # Stamp mtime to `now` explicitly -- keeps the recency-window check above
    # consistent with an injected `now` in tests, and is a real, correct
    # description of "when this snapshot was taken" in production too (a
    # `Connection.backup()` call, unlike a plain file copy, does not always
    # leave the OS's own real "last write" mtime exactly at call time).
    ts = now.timestamp()
    os.utime(dest_path, (ts, ts))

    if not _pmb.real_integrity_check(dest_path):
        try:
            os.remove(dest_path)
        except FileNotFoundError:
            pass
        report["error"] = "FRESH_COPY_FAILED_INTEGRITY_CHECK"
        report["message"] = (
            f"The snapshot just written to {dest_path} failed its own real "
            "PRAGMA integrity_check immediately after copying -- deleted rather "
            "than left behind as a silently-corrupt 'backup'."
        )
        return report

    report["decision"] = "written"
    report["backup_path"] = dest_path
    report["backup_bytes"] = os.path.getsize(dest_path)
    report["todays_adhoc_count"] = len(todays) + 1
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Take one real, capped, verified ad-hoc pre-change snapshot of "
                     "superboss-register.sqlite directly into memory/backups.")
    ap.add_argument("--live-db", default=LIVE_DB, help="path to the live database to snapshot")
    ap.add_argument("--backups-dir", default=BACKUPS_DIR, help="directory the snapshot is written into")
    ap.add_argument("--keep", type=int, default=KEEP_DEFAULT,
                     help=f"hard cap on same-UTC-day ad-hoc snapshots (default {KEEP_DEFAULT}, "
                          "matching prune_memory_backups.py's own --keep default)")
    ap.add_argument("--reason", default=None, help="free-text note recorded in the JSON report only")
    ap.add_argument("--recent-window-seconds", type=int, default=RECENT_WINDOW_SECONDS_DEFAULT,
                     help=f"skip a new snapshot if the most recent today's one is still verified-fresh "
                          f"within this many seconds (default {RECENT_WINDOW_SECONDS_DEFAULT})")
    args = ap.parse_args(argv)

    if args.keep < 1:
        print(json.dumps({"error": "BAD_ARGUMENT", "message": "--keep must be >= 1"}, indent=2))
        return 2

    report = run(args.live_db, args.backups_dir, args.keep, args.reason,
                 recent_window_seconds=args.recent_window_seconds)
    print(json.dumps(report, indent=2))
    return 1 if "error" in report else 0


if __name__ == "__main__":
    sys.exit(main())
