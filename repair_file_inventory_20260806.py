#!/usr/bin/env python3
"""One-off, explicit, checkpointed repair of the corrupted `file_inventory`
table (+ its autoindex) in the live superboss-register.sqlite, per the
rehearsed rename-swap plan in PROGRESS.md Step 5. Mirrors
superboss-register.py's own _write_lock() (flock on the .writelock file
before opening any write connection) and _connect() (30s busy_timeout)
conventions exactly, so this can never collide with -- or reproduce the
kill-during-write-lock corruption pattern documented in that file's own
_write_lock() docstring -- any other real writer of this DB.

Does NOT touch any table other than file_inventory. Aborts (rolls back,
leaves live untouched) if the recovered-vs-inserted row count doesn't match
exactly, rather than ever committing a partial/uncertain result.
"""
import fcntl
import json
import sqlite3
import sys
import time

LIVE = "/opt/veridian/ai-os/memory/superboss-register.sqlite"
LOCK_PATH = LIVE + ".writelock"
RECOVERED = "/opt/veridian/ai-os/memory/superboss-register.sqlite.recovered-20260806T025938Z"
TS = "20260806T044301Z"  # matches the fresh pre-repair backup taken just before this script
ORIG_RENAMED = f"file_inventory_corrupted_orig_{TS}"

SCHEMA = """CREATE TABLE file_inventory_new (
        path TEXT PRIMARY KEY, size INTEGER, mtime TEXT, hash16 TEXT,
        first_seen TEXT, last_seen TEXT
    , mode TEXT)"""

result = {}

with open(LOCK_PATH, "w") as lockfile:
    fcntl.flock(lockfile, fcntl.LOCK_EX)
    t0 = time.time()
    try:
        conn = sqlite3.connect(LIVE, timeout=30)
        conn.execute(f"ATTACH DATABASE ? AS rec", (RECOVERED,))
        conn.execute("BEGIN IMMEDIATE")
        try:
            src_count = conn.execute("SELECT count(*) FROM rec.file_inventory").fetchone()[0]
            conn.execute(SCHEMA)
            conn.execute(
                "INSERT INTO file_inventory_new "
                "(path,size,mtime,hash16,first_seen,last_seen,mode) "
                "SELECT path,size,mtime,hash16,first_seen,last_seen,mode "
                "FROM rec.file_inventory"
            )
            new_count = conn.execute("SELECT count(*) FROM file_inventory_new").fetchone()[0]
            if new_count != src_count:
                conn.rollback()
                result = {
                    "ok": False,
                    "reason": "row count mismatch, rolled back, live untouched",
                    "src_count": src_count,
                    "new_count": new_count,
                }
            else:
                conn.execute(f"ALTER TABLE file_inventory RENAME TO {ORIG_RENAMED}")
                conn.execute("ALTER TABLE file_inventory_new RENAME TO file_inventory")
                conn.commit()
                result = {
                    "ok": True,
                    "src_count": src_count,
                    "new_count": new_count,
                    "orig_corrupted_table_renamed_to": ORIG_RENAMED,
                }
        except Exception as e:
            conn.rollback()
            result = {"ok": False, "reason": f"exception, rolled back, live untouched: {e!r}"}
        finally:
            conn.close()
    finally:
        elapsed = time.time() - t0
        fcntl.flock(lockfile, fcntl.LOCK_UN)

result["elapsed_seconds"] = round(elapsed, 3)
print(json.dumps(result, indent=2))
if not result.get("ok"):
    sys.exit(1)
