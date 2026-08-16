#!/usr/bin/env python3
"""sqlite_daily_backup.py -- real, WAL-safe daily backup generator for
VERIDIAN's live sqlite databases (OCID-020 GTM certification category 19,
UMR-20260805-165909-4d8b, parent UMR-20260802-165606-4413).

Root cause this fixes (already investigated, not re-litigated here): no
active mechanism produces backups for
/opt/veridian/ai-os/memory/superboss-register.sqlite anywhere on this box
-- confirmed via `systemctl --user list-timers` (nothing matches),
`crontab -l` (empty), and a repo-wide grep (only health-check-15min.py's
check_db_integrity_and_backup() references /opt/veridian/backups/sqlite-
daily/, and its once-a-day raw-copy backup is gated behind the ENTIRE live
database passing a full-database PRAGMA integrity_check first -- so while
even one table anywhere in that database is corrupted, no backup is ever
written for it at all, healthy tables included). This script is a
dedicated, standalone replacement/supplement that:

  1. Never does a raw file copy against a live database. Uses SQLite's own
     online backup API (sqlite3.Connection.backup()) instead, which takes
     a consistent page-level snapshot through the pager and is safe under
     concurrent WAL-mode writers -- a raw `cp`/read+write can capture a
     torn, inconsistent snapshot mid-write.
  2. Verifies the BACKUP FILE's own PRAGMA integrity_check after writing
     it -- not the live source database's integrity, and not just "the
     file is non-empty". A backup that is itself corrupt (e.g. the online
     backup API can still produce a bad copy if the source page it reads
     is itself already damaged) must never be silently reported as a
     success. This is the exact failure mode this task exists to prevent
     recurring: the script fails loudly (non-zero exit, clear stderr) and
     quarantines the bad artifact under a name that will never be picked
     up by gtm_check_backup_recovery_testing.py's `<db>.<YYYYMMDD>.bak`
     glob, rather than leaving a corrupt file sitting under the real dated
     backup name where it would look like a real, valid, recent backup.
  3. Is idempotent: if today's dated backup already exists AND
     independently re-verifies as passing its own integrity check right
     now, running the script again the same day is a clean no-op success
     for that database (skip, not re-copy). A pre-existing same-day backup
     that fails re-verification is NOT trusted -- it is quarantined and
     redone fresh, unless --force is passed to always redo unconditionally.

CRITICAL, standing precondition (Hard Rule 8, do not weaken/skip): as of
this script's authorship, superboss-register.sqlite has one real,
confirmed-corrupted table (`file_inventory` -- fails PRAGMA
integrity_check and even DROP TABLE), held for a real Owner recovery
decision. A full-file online backup of that specific live database will
currently FAIL (the backup API will hit the same corrupted pages the live
integrity_check does) until that corruption is resolved. This script is
built and tested against synthetic data only; do not point it at the real
live superboss-register.sqlite, and do not enable the paired systemd timer
against it, until a real PRAGMA integrity_check against the live database
(run separately, deliberately, and only once the Owner recovery decision
has landed) confirms "ok".

Usage:
  sqlite_daily_backup.py [--db PATH ...] [--backup-dir DIR] [--date YYYYMMDD]
                          [--force] [--integrity-retries N] [--retry-delay-s S]

Exit codes: 0 = every requested database backed up (or already had a
verified same-day backup). 1 = at least one database's backup failed
(online backup itself failed, OR the resulting/pre-existing backup file
failed its own integrity check, OR came out zero-byte). 2 = usage error.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

DEFAULT_BACKUP_DIR = "/opt/veridian/backups/sqlite-daily"
DEFAULT_DBS = [
    "/opt/veridian/ai-os/memory/superboss-register.sqlite",
    "/opt/veridian/ai-os/memory/credit-ledger.sqlite",
]


class BackupError(Exception):
    """Raised for any real backup failure -- online-backup failure, a
    backup file that fails its own integrity check, or a zero-byte
    result. Callers must treat this as a loud, non-zero-exit failure,
    never a silently-swallowed warning."""


def utc_today_stamp(now=None):
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y%m%d")


def check_integrity(db_path, retries=3, delay_s=2.0):
    """Run `PRAGMA integrity_check;` against db_path (opened read-only)
    and return (ok: bool, verdict: str). Retries a few times before
    declaring real failure -- the same transient-mid-write-snapshot
    guard health-check-15min.py's existing check_db_integrity_and_backup()
    uses (a single read-only integrity_check under WAL mode can rarely
    see an inconsistent snapshot if it lands mid-write from a concurrent
    process; a same-day flagged "malformed" DB has been observed to pass
    a manual re-check seconds later with no intervention)."""
    verdict = None
    last_err = None
    for attempt in range(retries):
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
            try:
                cur = conn.cursor()
                cur.execute("PRAGMA integrity_check;")
                rows = cur.fetchall()
                verdict = rows[0][0] if rows else "no integrity_check output"
            finally:
                conn.close()
            if verdict == "ok":
                return True, verdict
        except sqlite3.Error as e:
            last_err = str(e)
            verdict = None
        if attempt < retries - 1:
            time.sleep(delay_s)
    return False, verdict or last_err or "unknown integrity_check failure"


def online_backup(src_path, dest_path):
    """Copy src_path -> dest_path using sqlite3.Connection.backup(), the
    safe online backup API. Never a raw file copy. Writes to a
    `.partial` sibling first and only atomically renames it into place
    on success, so a mid-copy failure never leaves a half-written file
    sitting under the real dest_path name."""
    if not os.path.isfile(src_path):
        raise BackupError(f"source database not found: {src_path}")

    tmp_dest = dest_path + ".partial"
    if os.path.isfile(tmp_dest):
        os.remove(tmp_dest)

    src_conn = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True, timeout=30)
    try:
        dest_conn = sqlite3.connect(tmp_dest)
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    except sqlite3.Error as e:
        if os.path.isfile(tmp_dest):
            os.remove(tmp_dest)
        raise BackupError(f"online backup (sqlite3 .backup API) failed for {src_path}: {e}") from e
    finally:
        src_conn.close()

    os.replace(tmp_dest, dest_path)
    return dest_path


def backup_one(db_path, backup_dir, today=None, force=False, integrity_retries=3, retry_delay_s=2.0):
    """Backup a single database. Returns a result dict on success, raises
    BackupError on any real failure. Idempotent per the module docstring."""
    name = os.path.basename(db_path)
    today = today or utc_today_stamp()
    dest_path = os.path.join(backup_dir, f"{name}.{today}.bak")

    os.makedirs(backup_dir, exist_ok=True)

    if not force and os.path.isfile(dest_path):
        ok, verdict = check_integrity(dest_path, retries=integrity_retries, delay_s=retry_delay_s)
        if ok:
            return {
                "db": name,
                "status": "skipped_already_verified",
                "backup_path": dest_path,
                "integrity_check": verdict,
                "size_bytes": os.path.getsize(dest_path),
            }
        # A pre-existing same-day backup that no longer verifies must not
        # keep sitting under the real dated name looking valid -- quarantine
        # it before attempting a fresh one.
        _quarantine(dest_path, reason=f"pre-existing same-day backup failed re-verification: {verdict}")

    online_backup(db_path, dest_path)

    ok, verdict = check_integrity(dest_path, retries=integrity_retries, delay_s=retry_delay_s)
    if not ok:
        quarantine_path = _quarantine(dest_path, reason=f"fresh backup failed its own integrity_check: {verdict}")
        raise BackupError(
            f"backup for {name} FAILED its own PRAGMA integrity_check "
            f"(verdict={verdict!r}) -- quarantined at {quarantine_path}, "
            f"NOT left under the real dated backup name {dest_path}. "
            f"This is a loud failure by design, never a silent success."
        )

    size = os.path.getsize(dest_path)
    if size <= 0:
        quarantine_path = _quarantine(dest_path, reason="backup came out zero-byte")
        raise BackupError(
            f"backup for {name} is zero bytes -- quarantined at {quarantine_path}, "
            f"NOT left under the real dated backup name {dest_path}."
        )

    return {
        "db": name,
        "status": "backed_up",
        "backup_path": dest_path,
        "integrity_check": verdict,
        "size_bytes": size,
    }


def _quarantine(path, reason):
    """Move a bad backup artifact aside under a name that will never match
    gtm_check_backup_recovery_testing.py's `<db>.<8-digit-date>.bak` glob
    (it only accepts an exact 8-digit-YYYYMMDD stem), so a corrupt backup
    can never be mistaken for a real, valid, recent one -- while still
    being kept on disk (not deleted) for forensic follow-up."""
    quarantine_path = f"{path}.CORRUPT-{int(time.time())}"
    if os.path.isfile(path):
        os.replace(path, quarantine_path)
    with open(quarantine_path + ".reason.txt", "w") as f:
        f.write(reason + "\n")
    return quarantine_path


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", action="append", default=None,
                     help="Path to a sqlite database to back up. Repeatable. "
                          "Defaults to the two real live VERIDIAN databases "
                          "(superboss-register.sqlite, credit-ledger.sqlite).")
    ap.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR,
                     help=f"Directory dated backups are written into (default {DEFAULT_BACKUP_DIR}).")
    ap.add_argument("--date", default=None,
                     help="Override the YYYYMMDD stamp used in the backup filename (default: real UTC today). "
                          "Test/determinism hook only.")
    ap.add_argument("--force", action="store_true",
                     help="Always redo the backup even if a same-day one already exists and verifies ok.")
    ap.add_argument("--integrity-retries", type=int, default=3)
    ap.add_argument("--retry-delay-s", type=float, default=2.0)
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    dbs = args.db or DEFAULT_DBS

    results = []
    failures = []
    for db in dbs:
        try:
            r = backup_one(
                db, args.backup_dir, today=args.date, force=args.force,
                integrity_retries=args.integrity_retries, retry_delay_s=args.retry_delay_s,
            )
            results.append(r)
            print(f"OK  {r['db']}: {r['status']} -> {r['backup_path']} "
                  f"(integrity_check={r['integrity_check']!r}, size_bytes={r['size_bytes']})")
        except BackupError as e:
            failures.append({"db": os.path.basename(db), "error": str(e)})
            print(f"FAIL {os.path.basename(db)}: {e}", file=sys.stderr)

    print(json.dumps({"results": results, "failures": failures}, indent=2))

    if failures:
        print(f"\n{len(failures)}/{len(dbs)} database backup(s) FAILED -- see stderr above.", file=sys.stderr)
        return 1
    print(f"\n{len(results)}/{len(dbs)} database backup(s) OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
