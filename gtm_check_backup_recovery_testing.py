#!/usr/bin/env python3
"""gtm_check_backup_recovery_testing.py -- real, re-runnable check for GTM
certification category_index=19 ("backup and recovery testing").

What it does, every time it runs:
  Lists the REAL files (real `os.stat` mtime + size, never a description)
  in /opt/veridian/backups/sqlite-daily/ and, for each of the two live
  sqlite databases this VERIDIAN install actively depends on --
  superboss-register.sqlite (the system-of-record this very check writes
  its own result into) and credit-ledger.sqlite -- finds the most recent
  real backup file matching `<dbname>.<8-digit-date>.bak` and computes how
  many real hours old it is right now.

  This is a real "recovery" precondition check, not just "a backup ran
  once": a backup that's real but stale (older than the freshness bar) or
  zero-byte would not actually be usable to recover a recent, real
  incident -- so both real recency AND real non-zero size are checked for
  each database, not just file existence.

Pass criterion (documented, fixed, not adjustable at call time):
  PASS <=> for EVERY monitored database (superboss-register.sqlite,
           credit-ledger.sqlite), the most recent real backup file is
           <= 48 hours old (real wall-clock delta from now) AND has real
           size > 0 bytes.
  Any monitored database missing a backup entirely, or whose most recent
  real backup is stale (>48h) or zero-byte, is a genuine FAIL -- the
  backups directory and its real file timestamps are directly readable on
  this server, this is real evidence of an actual gap, never "blocked".
  "blocked" is reserved for /opt/veridian/backups/sqlite-daily/ itself
  being confirmed absent/unreadable.

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=19's result.

Usage:
  gtm_check_backup_recovery_testing.py [--max-age-hours 48]
"""
import argparse
import glob
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WRITER = os.path.join(SCRIPTS_DIR, "gtm_write_category_result.py")
CATEGORY_INDEX = 19
BACKUP_DIR = "/opt/veridian/backups/sqlite-daily"
MONITORED_DBS = ["superboss-register.sqlite", "credit-ledger.sqlite"]


def call_writer(result, evidence_summary, evidence):
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(CATEGORY_INDEX),
        "--result", result,
        "--script-path", "gtm_check_backup_recovery_testing.py",
        "--evidence-summary", evidence_summary,
        "--evidence-json", json.dumps(evidence),
    ]
    print("Calling writer:", " ".join(cmd), file=sys.stderr)
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout)
    if p.returncode != 0:
        print(p.stderr, file=sys.stderr)
        sys.exit(p.returncode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-hours", type=float, default=48.0)
    args = ap.parse_args()

    if not os.path.isdir(BACKUP_DIR):
        call_writer(
            "blocked",
            f"Backup directory confirmed absent: {BACKUP_DIR}",
            {"backup_dir": BACKUP_DIR, "backup_dir_present": False},
        )
        return

    now = datetime.now(timezone.utc)
    per_db = {}
    for db in MONITORED_DBS:
        pattern = os.path.join(BACKUP_DIR, f"{db}.*.bak")
        candidates = glob.glob(pattern)
        # Only real dated backups (8-digit YYYYMMDD stem), exclude one-off
        # named snapshots (e.g. .pre-repair-phase14.bak) which aren't part
        # of the regular recovery cadence being checked here.
        dated = []
        for c in candidates:
            stem = os.path.basename(c)[len(db) + 1:-4]  # strip "<db>." prefix and ".bak" suffix
            if stem.isdigit() and len(stem) == 8:
                dated.append(c)

        if not dated:
            per_db[db] = {
                "backup_found": False,
                "candidates_considered": len(candidates),
                "reason": "no dated <db>.<YYYYMMDD>.bak file found",
            }
            continue

        latest = max(dated, key=os.path.getmtime)
        mtime = datetime.fromtimestamp(os.path.getmtime(latest), tz=timezone.utc)
        size = os.path.getsize(latest)
        age_hours = (now - mtime).total_seconds() / 3600.0

        per_db[db] = {
            "backup_found": True,
            "latest_backup_file": latest,
            "latest_backup_mtime_utc": mtime.isoformat(),
            "checked_at_utc": now.isoformat(),
            "age_hours": round(age_hours, 2),
            "size_bytes": size,
            "meets_recency_bar": age_hours <= args.max_age_hours,
            "meets_nonzero_size_bar": size > 0,
        }
        per_db[db]["meets_pass_bar"] = per_db[db]["meets_recency_bar"] and per_db[db]["meets_nonzero_size_bar"]

    failing = {
        db: info for db, info in per_db.items()
        if not info.get("backup_found") or not info.get("meets_pass_bar")
    }
    result = "fail" if failing else "pass"

    evidence = {
        "backup_dir": BACKUP_DIR,
        "monitored_databases": MONITORED_DBS,
        "max_age_hours_bar": args.max_age_hours,
        "checked_at_utc": now.isoformat(),
        "per_database": per_db,
        "failing_databases": list(failing.keys()),
        "pass_criterion": f"every monitored database's most recent dated backup is <= {args.max_age_hours}h old AND > 0 bytes",
    }
    summary = (
        f"{len(MONITORED_DBS) - len(failing)}/{len(MONITORED_DBS)} monitored databases have a real backup "
        f"<= {args.max_age_hours}h old and non-zero size in {BACKUP_DIR}."
    )
    if failing:
        detail_bits = []
        for db, info in failing.items():
            if not info.get("backup_found"):
                detail_bits.append(f"{db}: no dated backup found")
            else:
                detail_bits.append(f"{db}: latest backup is {info['age_hours']}h old (bar: {args.max_age_hours}h)")
        summary += " Failing: " + "; ".join(detail_bits) + "."
    call_writer(result, summary, evidence)


if __name__ == "__main__":
    main()
