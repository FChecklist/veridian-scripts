#!/usr/bin/env python3
"""prune_memory_backups.py -- retention policy for superboss-register.sqlite
backup/copy files living under /opt/veridian/ai-os/memory (both the
canonical .../memory/backups/ directory and the legacy memory/ root, where
several ad-hoc scripts/sessions have historically dropped full-DB copies
directly).

Root cause this fixes (Real Owner directive UMR-20260806-084306-f599 Step 6,
parent UMR-20260806-071025-1d28): nothing on this box ever pruned old
superboss-register.sqlite copies. Every stage/migration/repair/rebuild
script that took a "just in case" snapshot left it there forever. That is
what produced 20GB+ of redundant full-DB copies not once but twice in the
same week (the second accumulation -- 20 more copies -- happened in the
hours between the coordinator's manual cleanup earlier this cycle and this
script being written). This script is the actual fix for "nothing prunes
it", not just another one-off manual cleanup.

POLICY:
  - Keeps at most `--keep` (default 3) most recent VERIFIED backups.
    "Verified" means a real `PRAGMA integrity_check` was run against that
    SPECIFIC file THIS run and returned exactly [('ok',)] -- a backup is
    never trusted just because some earlier run verified it; corruption can
    happen to a backup file at rest (disk error, partial write, truncation)
    just as it can to a live DB.
  - Every other backup-like file found (unverified/corrupt ones, and
    verified ones beyond the newest 3) is a deletion candidate.
  - A backup's -wal/-shm companions (same base name) are grouped with it
    and deleted/kept as one unit.
  - The LIVE database and its own -wal/-shm/.writelock companions are never
    candidates, full stop -- they are excluded from discovery entirely, not
    merely "always kept".

HARD SAFETY GATE: this script refuses to delete anything -- exits non-zero,
prints a real error, makes zero filesystem writes -- unless the LIVE
database itself first passes a real PRAGMA integrity_check. If the live DB
is unhealthy, its backups are the only real recovery path; this script must
never be the thing that deletes the safety net at the exact moment it is
needed most.

Usage:
    prune_memory_backups.py --dry-run          # print the real plan, delete nothing
    prune_memory_backups.py                    # execute the real plan
    prune_memory_backups.py --keep 5            # override keep-count (default 3)
    prune_memory_backups.py --scan-dir DIR ...   # override scan dirs (repeatable)
    prune_memory_backups.py --live-db PATH       # override live DB path (testing)

Exit codes: 0 success (dry-run or real), 1 live DB failed integrity_check
(refused, nothing touched), 2 bad arguments.
"""
import argparse
import json
import os
import sqlite3
import sys
import urllib.parse
from datetime import datetime, timezone

# Env-overridable (same convention as superboss-register.py's own
# SUPERBOSS_REGISTER_DB) -- a real testability seam: task-20260815-051128-
# prevent-register-corruption-recurrence's real CLI-subprocess test for
# resource_governor.py's --daily-backup-check (which reuses
# discover_backup_groups() below to find the newest existing backup) needs
# to redirect these away from the real production paths without ever
# touching them. Every real production caller (including this module's own
# --scan-dir-less default CLI invocation) still gets the same real,
# unchanged default.
MEMORY_DIR = os.environ.get("VERIDIAN_PMB_MEMORY_DIR", "/opt/veridian/ai-os/memory")
BACKUPS_DIR = os.environ.get("VERIDIAN_PMB_BACKUPS_DIR", os.path.join(MEMORY_DIR, "backups"))
LIVE_DB = os.environ.get("VERIDIAN_PMB_LIVE_DB", os.path.join(MEMORY_DIR, "superboss-register.sqlite"))
DB_BASENAME = "superboss-register.sqlite"
KEEP_DEFAULT = 3
_COMPANION_SUFFIXES = ("-wal", "-shm")

# Files that belong to the LIVE database itself -- never discovery
# candidates, regardless of --scan-dir.
LIVE_DB_PROTECTED = {
    DB_BASENAME,
    DB_BASENAME + "-wal",
    DB_BASENAME + "-shm",
    DB_BASENAME + ".writelock",
    ".superboss_write.lock",
}


def real_integrity_check(path, timeout=30):
    """True iff a real `PRAGMA integrity_check` against `path` (opened
    read-only, so this never mutates or locks-for-write anything) returns
    exactly the single row ('ok',). Any exception -- missing file, zero
    bytes, not a database, unreadable, locked -- counts as NOT verified.
    Fails closed by design."""
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        return False
    uri = "file:" + urllib.parse.quote(os.path.abspath(path)) + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=timeout)
        try:
            rows = conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            conn.close()
        return len(rows) == 1 and rows[0][0] == "ok"
    except Exception:
        return False


def _companion_base(fname):
    """Returns (base_name, is_companion) -- strips a trailing -wal/-shm."""
    for suf in _COMPANION_SUFFIXES:
        if fname.endswith(suf):
            return fname[: -len(suf)], True
    return fname, False


def discover_backup_groups(scan_dirs):
    """Scans scan_dirs for superboss-register.sqlite.* files (excluding the
    live DB's own protected files) and groups each backup's main file with
    its -wal/-shm companions found in the SAME directory. Returns a list of
    dicts: {base, dir, main_path, companions:[...], mtime}. A group with no
    main_path (orphan -wal/-shm left over from an already-deleted main
    file) is real and included -- it is never verifiable and always a
    deletion candidate."""
    groups = {}
    for d in scan_dirs:
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if fname in LIVE_DB_PROTECTED:
                continue
            if not fname.startswith(DB_BASENAME + "."):
                continue
            full = os.path.join(d, fname)
            if not os.path.isfile(full):
                continue
            base, is_companion = _companion_base(fname)
            key = (d, base)
            g = groups.setdefault(key, {"base": base, "dir": d, "main_path": None, "companions": []})
            if is_companion:
                g["companions"].append(full)
            else:
                g["main_path"] = full

    result = []
    for g in groups.values():
        if g["main_path"]:
            g["mtime"] = os.path.getmtime(g["main_path"])
        elif g["companions"]:
            g["mtime"] = max(os.path.getmtime(c) for c in g["companions"])
        else:
            continue
        result.append(g)
    return result


def verify_group(g):
    """A group is only ever verified via its main_path. Orphan companions
    with no main file are never verified."""
    if g["main_path"] is None:
        return False
    return real_integrity_check(g["main_path"])


def plan_prune(scan_dirs, keep):
    groups = discover_backup_groups(scan_dirs)
    for g in groups:
        g["verified"] = verify_group(g)
    verified_sorted = sorted((g for g in groups if g["verified"]), key=lambda g: g["mtime"], reverse=True)
    keep_keys = {(g["dir"], g["base"]) for g in verified_sorted[:keep]}
    to_keep = [g for g in groups if (g["dir"], g["base"]) in keep_keys]
    to_delete = [g for g in groups if (g["dir"], g["base"]) not in keep_keys]
    return to_keep, to_delete


def group_paths(g):
    paths = list(g["companions"])
    if g["main_path"]:
        paths.append(g["main_path"])
    return paths


def group_size(g):
    return sum(os.path.getsize(p) for p in group_paths(g) if os.path.isfile(p))


def _group_report(g):
    return {
        "main_path": g["main_path"],
        "companions": g["companions"],
        "verified": g["verified"],
        "mtime_utc": datetime.fromtimestamp(g["mtime"], tz=timezone.utc).isoformat(),
        "bytes": group_size(g),
    }


def run(live_db, scan_dirs, keep, dry_run):
    """Real entry point (importable for tests). Returns the report dict.
    Raises no exceptions for the normal refuse-to-run case -- that is
    signalled via report["error"] so callers/tests can inspect it without
    catching SystemExit."""
    live_verified = real_integrity_check(live_db)
    if not live_verified:
        return {
            "error": "LIVE_DB_INTEGRITY_CHECK_FAILED",
            "live_db": live_db,
            "message": (
                f"Refusing to prune: live database at {live_db} failed "
                "PRAGMA integrity_check (or is missing/zero-byte/unreadable). "
                "Backups are the only real recovery path while the live DB is "
                "unhealthy -- deleting nothing."
            ),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }

    to_keep, to_delete = plan_prune(scan_dirs, keep)

    report = {
        "mode": "dry-run" if dry_run else "real-delete",
        "live_db": live_db,
        "live_db_verified": True,
        "scan_dirs": list(scan_dirs),
        "keep_count": keep,
        "kept": [_group_report(g) for g in sorted(to_keep, key=lambda g: g["mtime"], reverse=True)],
        "deleted": [],
        "reclaimed_bytes": 0,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }

    for g in sorted(to_delete, key=lambda g: g["mtime"], reverse=True):
        entry = _group_report(g)
        size = entry["bytes"]
        if dry_run:
            entry["action"] = "would-delete"
        else:
            deleted_paths = []
            for p in group_paths(g):
                try:
                    os.remove(p)
                    deleted_paths.append(p)
                except FileNotFoundError:
                    pass
            entry["action"] = "deleted"
            entry["deleted_paths"] = deleted_paths
        report["deleted"].append(entry)
        report["reclaimed_bytes"] += size

    return report


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Prune redundant superboss-register.sqlite backup copies, keeping only "
                     "the N most recent that pass a real PRAGMA integrity_check.")
    ap.add_argument("--dry-run", action="store_true",
                     help="Print exactly what would be deleted; delete nothing.")
    ap.add_argument("--keep", type=int, default=KEEP_DEFAULT,
                     help=f"Number of most-recent verified backups to keep (default {KEEP_DEFAULT}).")
    ap.add_argument("--live-db", default=LIVE_DB,
                     help="Path to the live database whose integrity_check gates this whole run.")
    ap.add_argument("--scan-dir", action="append", default=None,
                     help="Directory to scan for backup files (repeatable). "
                          "Default: memory dir root + memory/backups.")
    args = ap.parse_args(argv)

    if args.keep < 1:
        print(json.dumps({"error": "BAD_ARGUMENT", "message": "--keep must be >= 1"}, indent=2))
        return 2

    scan_dirs = args.scan_dir or [MEMORY_DIR, BACKUPS_DIR]
    report = run(args.live_db, scan_dirs, args.keep, args.dry_run)
    print(json.dumps(report, indent=2))
    return 1 if "error" in report else 0


if __name__ == "__main__":
    sys.exit(main())
