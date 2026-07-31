#!/usr/bin/env python3
"""
migrate_2026-07-31_dedup_constraints.py -- one-time, idempotent, safe-to-re-run
migration for task-20260731-074406-structural-duplicate-task-constraint-in.

Adds a real hard constraint against duplicate knowledge_engine artifact rows
(replacing reliance on fuzzy FTS search alone -- see check-content-duplicate/
check-duplicate in superboss-register.py, which only ever advise, never
block) and a task_claims lease table so task-gateway.py's cmd_start can
reject a concurrent duplicate dispatch loudly instead of silently
duplicating it. Motivated by two real duplicate-task incidents this session:
PR #634 vs #639 (both Stage-12 dispatch-persistent-memory) and PR #641
(duplicate of already-merged #629).

REAL SCHEMA INVESTIGATION (PRAGMA table_info + direct data queries against
the live DB -- not assumed) this migration is based on:

  - knowledge_engine.content_hash is NOT a valid bare-UNIQUE candidate: 15
    rows legitimately share content_hash='n/a' (this table's documented
    no-hash-computed placeholder) and 13 distinct, legitimate artifacts
    share the sha256-of-empty-string hash (empty files). A bare
    UNIQUE(content_hash) would have rejected all of that real, valid data.

  - The real uniqueness key, confirmed against live data: (content_hash,
    artifact_path). Querying `GROUP BY content_hash, artifact_path HAVING
    COUNT(*) > 1` found exactly 2 pre-existing violations, both genuine
    accidental re-registrations of the exact same artifact (identical
    content_hash + artifact_path + artifact_type + secondary_path, ts a few
    minutes apart -- not a legitimate versioning case):
      - /opt/veridian/ai-os/WIRING_ENGINE_PHASE_PLAN_2026-07-25.yaml
        (KE-20260725-034805-3bb5, KE-20260725-060359-eb2c)
      - .../task-20260727-094843.../VERIDIAN_V2_DSPY_TECH_DECISION_2026-07-27.md
        (KE-20260727-100007-5d21, KE-20260727-100048-b8fe)
    Two OTHER same-artifact_path repeats also exist in the table
    (/opt/veridian/scripts/superboss-register.py,
    ai-os-scripts/audit_pipeline_architecture.py) but have DIFFERENT
    content_hash each time -- legitimate re-registration after the file's
    real content changed. (content_hash, artifact_path) uniqueness correctly
    leaves those alone; a bare UNIQUE(artifact_path) would have wrongly
    blocked them.

  - instructions.content_hash was also checked and legitimately repeats up
    to 961 times (common boilerplate instruction text) -- confirmed NOT a
    candidate for any unique constraint, consistent with this task's SCOPE
    explicitly naming only knowledge_engine.content_hash.

  - work_items has no single stable per-task column: software_task_id/
    ai_task_id both repeat multiple times per real task (one row per
    lifecycle event -- pending, DONE, etc.), so a lease/claim concept needed
    a new, separate table rather than a constraint bolted onto work_items.

SAFE-ON-LIVE-DB APPROACH (per task CONSTRAINTS -- do not lock the DB for an
extended period, do not ALTER TABLE / rebuild the table):
  1. Online `.backup` snapshot first (sqlite3's backup API, safe under WAL,
     does not require stopping writers).
  2. Acquire the exact same OS-level flock every other write path in
     superboss-register.py uses (_write_lock, root-caused against the
     2026-07-23 corruption incidents) so this cannot race a concurrent
     worker's write.
  3. Delete only the 2 confirmed-exact-duplicate rows (keep the earliest of
     each pair) -- generic GROUP BY query, not hardcoded to those 2 paths,
     so this is safe to re-run and self-heals if a future DB rebuild from an
     older backup ever reintroduces old duplicates.
  4. CREATE UNIQUE INDEX (not ALTER TABLE / table rebuild) -- SQLite builds
     this without recreating the table.
  5. CREATE TABLE IF NOT EXISTS task_claims + its own UNIQUE index.
  6. Commit promptly after each step; total lock hold time is a handful of
     real DML statements against a 365-row table, not a long-running scan.

Run: python3 migrate_2026-07-31_dedup_constraints.py
(Respects SUPERBOSS_REGISTER_DB env var override, same as superboss-register.py
-- used by test_dedup_constraints_2026-07-31.py to run this against a
throwaway temp DB instead of the live one.)
"""
import contextlib
import fcntl
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH = os.environ.get("SUPERBOSS_REGISTER_DB", "/opt/veridian/ai-os/memory/superboss-register.sqlite")
_WRITE_LOCK_PATH = DB_PATH + ".writelock"


@contextlib.contextmanager
def _write_lock():
    """Same OS-level flock pattern as superboss-register.py's own
    _write_lock() -- reimplemented here (not imported) because that module's
    filename contains a hyphen and isn't importable as `import
    superboss-register`; every write-path CLI invocation of that script goes
    through the identical file + fcntl.LOCK_EX, so this is safe to run
    concurrently with any of them."""
    os.makedirs(os.path.dirname(_WRITE_LOCK_PATH), exist_ok=True)
    with open(_WRITE_LOCK_PATH, "w") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)


def _backup_db():
    if not os.path.isfile(DB_PATH):
        return None
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    backup_path = f"{DB_PATH}.pre-dedup-constraint-backup-{ts}"
    src = sqlite3.connect(DB_PATH, timeout=30)
    dst = sqlite3.connect(backup_path)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return backup_path


def main():
    log = []
    backup_path = _backup_db()
    if backup_path:
        log.append({"step": "backup", "backup_path": backup_path})

    with _write_lock():
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row

        table_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_engine'"
        ).fetchone()

        deleted = []
        if table_exists:
            dup_groups = conn.execute(
                "SELECT content_hash, artifact_path, COUNT(*) c FROM knowledge_engine "
                "GROUP BY content_hash, artifact_path HAVING c > 1"
            ).fetchall()
            for g in dup_groups:
                rows = conn.execute(
                    "SELECT * FROM knowledge_engine WHERE content_hash=? AND artifact_path=? ORDER BY ts ASC",
                    (g["content_hash"], g["artifact_path"]),
                ).fetchall()
                # Keep the earliest (first-registered) row; delete the rest.
                for r in rows[1:]:
                    row_dict = dict(r)
                    conn.execute("DELETE FROM knowledge_engine WHERE artifact_id=?", (r["artifact_id"],))
                    deleted.append(row_dict)
            conn.commit()
        log.append({"step": "dedupe_knowledge_engine_exact_duplicates", "deleted_rows": deleted})

        if table_exists:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_engine_content_hash_path "
                "ON knowledge_engine(content_hash, artifact_path)"
            )
            conn.commit()
            log.append({
                "step": "create_unique_index",
                "index": "idx_knowledge_engine_content_hash_path",
                "table": "knowledge_engine",
                "columns": ["content_hash", "artifact_path"],
            })
        else:
            log.append({"step": "skip_unique_index", "reason": "knowledge_engine table does not exist yet in this DB"})

        conn.execute("""CREATE TABLE IF NOT EXISTS task_claims (
            claim_id TEXT PRIMARY KEY,
            task_key TEXT NOT NULL,
            ts TEXT NOT NULL,
            task_id TEXT,
            title TEXT,
            utm_source TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )""")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_task_claims_task_key ON task_claims(task_key)"
        )
        conn.commit()
        log.append({"step": "create_task_claims_table", "table": "task_claims", "unique_column": "task_key"})

        conn.close()

    result = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "db_path": DB_PATH,
        "log": log,
    }
    print(json.dumps(result, indent=2, default=str))
    return result


if __name__ == "__main__":
    main()
