#!/usr/bin/env python3
"""
Superboss Register -- three searchable trees, server-side, SQLite+FTS5.
Deployed 2026-07-20 per Owner directive: no session starts from zero again.

SCOPE (deliberately distinct from the AI-work cost-control system built
earlier the same night -- that governs dispatched WORKER tasks; this
governs the OWNER<->SUPERBOSS operational dialogue itself, which nothing
else in this codebase tracks. Not a duplicate of `conversations`/`messages`
in schema.ts either -- those are VERI Chat's end-customer product tables,
a different population/purpose entirely, confirmed by direct inspection
before building this.

THREE TREES:
  instructions -- one row per distinct request/instruction (the INPUT side:
                  what was asked, by whom, when, tagged UTM-style).
  work_items   -- one row per unit of work registered in response (the
                  OUTPUT side). software_task_id XOR ai_task_id populated
                  depending on route (§1 triage in
                  AI_CACHE_AND_TRIAGE_ARCHITECTURE.md). Links back to the
                  instruction(s) that spawned it.
  actions      -- finest-grained audit trail: one row per individual action
                  by ANY actor (owner, end_user, org, ai_agent, software).
                  Links to the work_item and/or instruction it serves.

STORAGE FORMAT: structured records (typed columns + a JSON metadata blob),
not narrative text -- per Owner directive, this store is for AI/software
consumption, not human reading. Raw instruction/action text is still
stored (full-text search needs it), but every record is tag-indexed so a
query never requires re-reading raw prose to find what's relevant.

ID SCHEMES (all sortable-by-construction, timestamp-prefixed):
  INS-YYYYMMDD-HHMMSS-<4hex>   instruction_id
  SFT-YYYYMMDD-HHMMSS-<4hex>   software_task_id  (work done with zero AI calls)
  (existing CONTROLLER.yaml task_id reused verbatim as ai_task_id when work
   routes through the existing AI worker fleet -- NOT reissued, avoids a
   second ID for the same real task)
  CCH-<16 hex of the real cache key>   cache_id / ai_cache_id (references
   the existing L1 exact-match cache in glm-response-cache.sqlite by its
   own key, not a new ID space -- avoids yet another duplicate index)
  ACT-YYYYMMDD-HHMMSS-<4hex>   action_id

UTM-STYLE TAGS (literal UTM parameter names, since that's the vocabulary
the Owner specified): utm_source (who: owner|end_user|org|ai_agent|software),
utm_medium (channel: ssh_session|claude_code_cli|chat_ui|api|cron),
utm_campaign (initiative/project grouping, freeform slug),
utm_content (short structured label of what, not a sentence),
utm_term (comma-separated search keywords).
NOTE (UMR171945-0024, real, disclosed divergence found in independent PR
review 2026-08-08, deliberately NOT silently reconciled): task-gateway.py's
own `submit --source` uses a second, purpose-specific 5-class caller-
identity vocabulary for real-time request LABELING (not proof of
liveness) -- owner|ai_agent|trusted_executor|end_user|external_integration
-- which also flows into this column (utm_source) via log-instruction.
This is not the same 5 values as this docstring's own owner|end_user|org|
ai_agent|software. No column-level CHECK constraint enforces either list as
a strict whitelist, so nothing breaks today, but a caller/reader relying on
one list should be aware the other is real and also writes here. A real
reconciliation (or an explicit decision to keep them intentionally
distinct -- "software"/"org" describe write-path/event classes, not live
caller identity) is real, disclosed follow-up work, not done here.

CANONICAL SCRIPT (Owner directive, UMR-20260806-031211-64de / UMR-20260806-033108-9839
/ UMR-20260806-033709-82d7): this is the one real canonical script for every real read
and every real write against superboss-register.sqlite -- umr_tasks, ocid_canonical_registry,
ocid_master_standard_audit_log, gtm_certification_categories, pm_decisions_pending,
pm_report_snapshots, and every other table in this file. Real raw SQL against this file
from outside this script (a one-off sqlite3.connect() in another script, an ad hoc
migration, a bare INSERT/UPDATE) is NOT the standard procedure -- extend the function
library here instead (see insert_pm_decision_pending()/resolve_pm_decision_pending()/
update_pm_decision_pending()/insert_owner_proposal()/decide_owner_proposal()/record_owner_proposal_completion()/
record_ocid_master_standard_audit_event()/insert_ocid_artifact_link()/update_umr_task()
for the established convention: module-level _connect()/_write_lock(), caller-owned
commit, an idempotent _ensure_<table>_table() at the top of anything that creates
schema) and wire in a CLI subcommand if one's needed, rather than writing a second
parallel script. This statement exists so the rule is discoverable at the source, not
only in a report.
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import random
import re
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

# 2026-08-07 (UMR-20260807-035145-aa45): vector_similarity.py is a plain
# same-directory sibling module (no hyphen in its filename, unlike this
# script), so a normal import works as long as this script's own directory is
# on sys.path -- true whenever this file is invoked directly (python3
# .../superboss-register.py ...), which is its only real call convention.
# Wrapped defensively so a missing/broken sibling module degrades the two
# vector-column-population call sites below to a no-op rather than breaking
# every other command this file serves.
try:
    import vector_similarity as _vector_similarity
except ImportError:
    _vector_similarity = None

class SuperbossDbPathError(Exception):
    """Raised by resolve_superboss_db_path() below when the real Superboss
    Register database path fails a real, deterministic verification check.
    Never a silent fallback -- a failed check stops execution rather than
    letting any caller operate against an unverified database."""


def resolve_superboss_db_path(default_path="/opt/veridian/ai-os/memory/superboss-register.sqlite"):
    """Deterministic, close-ended, auditable resolution of the real
    Superboss Register database path (OCID-068 real requirement, Owner
    directive UMR-20260804-180210-9e2c, following the structured-traceability
    addendum UMR-20260804-170055-a069, itself under OCID-068's own real UMR
    UMR-20260804-164106-3fb8 -- the standing hard-rule-7 implementation lock
    was given a fresh, explicit, real-time Owner override scoped narrowly to
    this one change; independently re-confirmed by the "full spec" follow-up
    directive UMR-20260804-194230, itself a follow-on to
    UMR-20260804-180142-676d, which had carried only a short summary of this
    same real 5-step algorithm -- both re-verify, neither changes, the real
    behavior already implemented and merged below). This is the single
    canonical chokepoint: the one real
    place DB_PATH is computed anywhere in this codebase. resource_governor.py
    (via its own _superboss_register() importlib loader) and every other
    real caller of this module reads the module-level DB_PATH this function
    sets below, at import time -- none of them recompute their own path, so
    this replaces the prior plain `os.environ.get(..., "<default>")`
    assignment without creating a second, parallel implementation anywhere.

    Real, strict order:
      1. Read SUPERBOSS_REGISTER_DB from the environment.
      2. If it is set AND the path it names exists on disk AND is non-zero
         size, that path is the candidate.
      3. Otherwise the fixed default,
         /opt/veridian/ai-os/memory/superboss-register.sqlite, is the
         candidate.
      4. Before any read/write/register/update against the candidate,
         verify in order: (a) the file exists; (b) it is non-zero size;
         (c) it begins with the real SQLite file header magic bytes
         (b"SQLite format 3\\x00", 16 bytes -- rejects a non-SQLite file);
         (d) it contains a real umr_tasks table, checked via a direct
         sqlite_master query, never assumed.
      5. Only once every check in step 4 passes does this function return
         the candidate as the real DB_PATH.

    Any failure raises SuperbossDbPathError naming exactly which check
    failed, the exact path checked, the exact file size found, and the
    specific reason -- never a silent fallback to a different path. This
    deliberately, permanently keeps failing against the real, confirmed
    stale decoy file at /opt/veridian/ai-os/superboss-register.sqlite
    (independently confirmed 0 bytes, dated 2026-07-31, a completely
    different file one directory shallower than the real, live database --
    the exact same class of confusable-decoy-artifact hazard as the
    separately-found, separately-flagged ai-os/umr_tasks.db, see the OCID-068
    owner review package's own §4d): that path fails check (b) unconditionally
    by construction, and this function must never be changed to accept it.

    default_path exists purely as a real testability seam (real tests, not
    production callers, are the only real reason to ever pass it) -- its
    default value IS the exact real fixed path named in step 3 above, so
    every real caller (this module's own DB_PATH assignment below, and
    everything that imports/execs this file) gets byte-identical behavior to
    calling this with zero arguments. This exists specifically so real tests
    can exercise steps 4/5's failure paths (missing file, zero-byte file,
    wrong schema) without ever touching the real, live, production database
    this whole platform's dispatch loop reads/writes every 30 seconds."""
    env_path = os.environ.get("SUPERBOSS_REGISTER_DB")
    if env_path and os.path.exists(env_path) and os.path.getsize(env_path) > 0:
        candidate = env_path
    else:
        candidate = default_path

    if not os.path.exists(candidate):
        raise SuperbossDbPathError(
            "Superboss Register DB path verification FAILED at check 'file exists': "
            f"path_checked={candidate!r}"
        )

    size = os.path.getsize(candidate)
    if size == 0:
        raise SuperbossDbPathError(
            "Superboss Register DB path verification FAILED at check 'non-zero size' "
            f"(known stale-decoy signature): path_checked={candidate!r} size_found=0"
        )

    with open(candidate, "rb") as f:
        header = f.read(16)
    if header != b"SQLite format 3\x00":
        raise SuperbossDbPathError(
            "Superboss Register DB path verification FAILED at check 'SQLite file header magic bytes' "
            f"(not a real SQLite database): path_checked={candidate!r} size_found={size} "
            f"header_found={header!r}"
        )

    conn = sqlite3.connect(candidate)
    try:
        table_row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='umr_tasks'"
        ).fetchone()
    finally:
        conn.close()
    if table_row is None:
        raise SuperbossDbPathError(
            "Superboss Register DB path verification FAILED at check 'contains umr_tasks table' "
            f"(real SQLite file, wrong/incomplete database): path_checked={candidate!r} size_found={size}"
        )

    return candidate


DB_PATH = resolve_superboss_db_path()
_WRITE_LOCK_PATH = DB_PATH + ".writelock"

# UMR-20260806-130914-e7f1: process-local reentrancy depth counter for
# _write_lock() below -- see that function's own docstring addendum for the
# real, concrete deadlock this fixes. Not a threading.Lock/RLock: every
# real caller of this script runs single-threaded per process invocation, so
# a plain single-element list (mutable closure cell, avoids a `global`
# statement) is the real, sufficient mechanism -- never shared across
# processes, so it has zero effect on this lock's real cross-process mutual
# exclusion guarantee.
_write_lock_depth = [0]

# Same VERIDIAN_ROOT-relative resolution convention used by
# verify_registry_file_paths.py's resolve_path()/generate_wiring_registry.py's
# normalize_path(): absolute paths used as-is, root-relative paths (e.g.
# "scripts/foo.py") resolved under this root. Used by
# record_capability_graduation()'s real script_path existence check.
VERIDIAN_ROOT = "/opt/veridian"


@contextlib.contextmanager
def _write_lock():
    """Serializes every write-path CLI invocation of this script across
    processes -- same proven pattern as veridian-task.py's controller_lock()
    (built for the 2026-07-18 CONTROLLER.yaml corruption). Root cause found
    2026-07-23 for this DB's same-day repeated corruption (3 distinct
    signatures): concurrent writers contend for SQLite's own write lock
    inside their 30s busy_timeout (_connect() below), but the outer caller
    -- veridian-task.py's _log_to_register(), fired every 5 minutes by every
    active worker's background checkpoint loop, plus ad-hoc log-work/
    log-action calls -- only waited 10s before SIGKILLing a still-blocked
    child (see veridian-task.py _log_to_register). A kill landing while a
    process holds the SQLite write lock mid-transaction/mid-WAL-checkpoint
    (each INSERT here also fires an FTS5 AFTER INSERT trigger, widening
    that window) leaves partially-written b-tree pages -- matching exactly
    the page-truncation/tree-damage/freelist-mismatch signatures seen today.
    Acquiring this OS file lock BEFORE opening any sqlite write connection
    means a losing process blocks entirely outside any sqlite transaction,
    so killing it while it waits can never corrupt the file; flock is also
    auto-released if the holder itself is killed, so this cannot deadlock.

    UMR-20260806-130914-e7f1 addendum: made safely re-entrant WITHIN the
    same process via the plain _write_lock_depth counter above. flock() is
    NOT reentrant across distinct open file descriptions even within one
    process (confirmed against `man 2 flock`: "If a process uses open(2)...
    to obtain more than one file descriptor for the same file, these file
    descriptors are treated independently by flock()... An attempt to lock
    the file using one of these file descriptors may be denied by a lock
    that the calling process has already placed via another file
    descriptor") -- so a naive nested `with _write_lock(): ... with
    _write_lock(): ...` would self-deadlock. Real, concrete trigger this
    fixes: the `init` CLI command already wraps init_db() in one
    _write_lock(), and init_db() calls _migrate_schema() ->
    _ensure_umr_table(), which now (see _migrate_umr_tasks_status_widen())
    also needs its own real _write_lock() around its actual rebuild -- the
    same real corruption-prevention reason documented above, since a
    full-table rebuild is a much larger real write than the cheap ALTER
    TABLE ADD COLUMN migrations that pattern already covered safely. Only
    the OUTERMOST acquisition takes/releases the real OS flock; a nested
    call is a pure no-op that still yields -- mutual exclusion against every
    OTHER process is completely unaffected; this only ever prevents THIS
    process from blocking on a lock it already holds itself."""
    if _write_lock_depth[0] > 0:
        _write_lock_depth[0] += 1
        try:
            yield
        finally:
            _write_lock_depth[0] -= 1
        return
    os.makedirs(os.path.dirname(_WRITE_LOCK_PATH), exist_ok=True)
    with open(_WRITE_LOCK_PATH, "w") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        _write_lock_depth[0] = 1
        try:
            yield
        finally:
            _write_lock_depth[0] = 0
            fcntl.flock(lockfile, fcntl.LOCK_UN)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    rand = secrets.token_hex(2)
    return f"{prefix}-{ts}-{rand}"


# 2026-08-14 (real fix, addendum to P1 UMR-20260806-171945-5767 / owner-approved,
# OWNER_DECISIONS_NEEDED_2026-07-23.yaml id=crontab-drift-approved-2026-08-14): the
# `PRAGMA busy_timeout=30000` below already makes SQLite itself retry lock acquisition
# for up to 30s per attempt, but a real stuck process was found earlier holding the
# write lock for 17+ minutes -- far past that 30s budget -- which crashed dispatch
# attempts outright with "database is locked" instead of retrying. RetryConnection
# wraps every write operation (execute/executemany/executescript/commit) that goes
# through this script's one real `_connect()` choke point (see the CANONICAL SCRIPT
# note at the top of this file) with real retry-with-backoff on top of the PRAGMA
# busy_timeout, so a long lock hold degrades to a slow retry instead of a crashed
# dispatch attempt. Only "database is locked"/"database is busy" OperationalErrors
# are retried -- any other error (schema error, constraint violation, etc.) still
# raises immediately, unretried.
_SQLITE_LOCKED_RETRY_MAX_TOTAL_WAIT = 1200.0  # seconds (20 min) -- real incident held
# the lock 17+ minutes; this ceiling is set to comfortably exceed that, not just the
# observed case. Bounded, not infinite: once the total wait budget is exhausted the
# real OperationalError is raised (a stuck lock is a real incident to surface, not to
# retry forever).
_SQLITE_LOCKED_RETRY_BASE_DELAY = 0.5   # seconds, first backoff delay
_SQLITE_LOCKED_RETRY_MAX_DELAY = 20.0   # seconds, backoff cap per attempt


def _is_locked_error(exc):
    msg = str(exc).lower()
    return "database is locked" in msg or "database is busy" in msg


def _retry_on_locked(fn, *args, **kwargs):
    start = time.monotonic()
    attempt = 0
    while True:
        try:
            return fn(*args, **kwargs)
        except sqlite3.OperationalError as e:
            elapsed = time.monotonic() - start
            if not _is_locked_error(e) or elapsed >= _SQLITE_LOCKED_RETRY_MAX_TOTAL_WAIT:
                raise
            delay = min(
                _SQLITE_LOCKED_RETRY_MAX_DELAY,
                _SQLITE_LOCKED_RETRY_BASE_DELAY * (2 ** attempt),
            ) + random.uniform(0, _SQLITE_LOCKED_RETRY_BASE_DELAY)
            delay = min(delay, max(0.0, _SQLITE_LOCKED_RETRY_MAX_TOTAL_WAIT - elapsed))
            if delay <= 0:
                raise
            time.sleep(delay)
            attempt += 1


class RetryConnection(sqlite3.Connection):
    """sqlite3.Connection whose write-shaped methods retry-with-backoff on a real
    'database is locked'/'database is busy' OperationalError, on top of (not instead
    of) the PRAGMA busy_timeout set on every connection this script opens. See the
    2026-08-14 note above _connect() for why this exists."""

    def execute(self, *args, **kwargs):
        return _retry_on_locked(super().execute, *args, **kwargs)

    def executemany(self, *args, **kwargs):
        return _retry_on_locked(super().executemany, *args, **kwargs)

    def executescript(self, *args, **kwargs):
        return _retry_on_locked(super().executescript, *args, **kwargs)

    def commit(self, *args, **kwargs):
        return _retry_on_locked(super().commit, *args, **kwargs)


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30, factory=RetryConnection)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Atomic full-file rewrite pattern (Part A, 2026-08-15 register-corruption
# incident, task-20260815-051128-prevent-register-corruption-recurrence).
#
# Real incident: this DB (2.9GB at the time) was found with a corrupted
# header; `sqlite3 .recover` confirmed zero salvageable data. Root cause
# pointed to an unreleased writelock file carrying the exact same timestamp
# as the corruption (stuck 5+ hours, no process holding it) -- consistent
# with a process that was interrupted mid-write directly against the live
# file path. Recovery required falling back to an 8-day-stale backup (real
# cost: ~8 days of register history lost).
#
# Real fix: any code path that must produce an entirely new copy of this
# file (a migration too large for in-place ALTER TABLE, a compaction/VACUUM
# pass, a restore) must never write into DB_PATH directly. It builds the new
# file at a temp path on the SAME filesystem, validates the temp file's
# real SQLite header + a real PRAGMA integrity_check, and only THEN
# atomically replaces the live file via os.replace() (a POSIX rename --
# either the old inode or the new inode is visible at DB_PATH at every
# instant; there is no window where a reader/writer can observe a
# partially-written file). If the process building the temp file is
# killed/crashes at any point before that final os.replace(), DB_PATH is
# left completely untouched -- exactly the property that would have
# prevented this incident.
#
# A repo-wide grep for direct writes to DB_PATH outside a normal
# transactional sqlite3 connection (2026-08-15) found no existing violator:
# every in-place table rebuild elsewhere in this file (e.g.
# _migrate_wiring_registry_entity_types's CREATE-TABLE-AS-SELECT-then-RENAME)
# already goes through the ordinary _connect()/_write_lock() path, which is
# a normal transactional connection against the live file, not a file-level
# rewrite -- out of this pattern's scope by the SPEC's own carve-out.
# Compaction/VACUUM -- named explicitly in the incident follow-up -- did not
# exist anywhere in this codebase before this fix; it is added below using
# this pattern from day one, so it can never become the next occurrence of
# this same incident.
# ---------------------------------------------------------------------------

def atomic_replace_live_db(build_temp_db, db_path=None):
    """Builds a brand-new sqlite file by calling build_temp_db(tmp_path),
    then touches the real live `db_path` (default DB_PATH) with exactly one
    atomic os.replace() -- see the module note above for the full real
    incident this closes. Caller must hold _write_lock() around this call
    (every real caller below does) so no concurrent writer can be
    mid-transaction against db_path when the final rename happens.

    build_temp_db(tmp_path) must create a real, complete sqlite file AT
    tmp_path (tmp_path itself does not exist yet when it is called -- e.g.
    `VACUUM INTO` requires this). Any exception raised inside
    build_temp_db, or a temp file that fails the real header/integrity
    validation below, leaves db_path completely untouched (and removes the
    temp file) -- never a partial/corrupt file left in place of the one a
    real recovery path would look for.

    Returns db_path on success."""
    db_path = db_path or DB_PATH
    tmp_dir = os.path.dirname(db_path) or "."
    os.makedirs(tmp_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(db_path) + ".atomic-rewrite-", suffix=".tmp", dir=tmp_dir)
    os.close(fd)
    os.remove(tmp_path)  # build_temp_db (e.g. VACUUM INTO) requires the target not exist yet
    try:
        build_temp_db(tmp_path)

        if not os.path.isfile(tmp_path) or os.path.getsize(tmp_path) == 0:
            raise RuntimeError(
                f"atomic_replace_live_db: build_temp_db produced no real file at {tmp_path!r} "
                "-- refusing to touch the live file")
        with open(tmp_path, "rb") as f:
            header = f.read(16)
        if header != b"SQLite format 3\x00":
            raise RuntimeError(
                f"atomic_replace_live_db: temp file at {tmp_path!r} failed the real SQLite "
                f"header check (found {header!r}) -- refusing to touch the live file")

        check_conn = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)
        try:
            rows = check_conn.execute("PRAGMA integrity_check").fetchall()
        finally:
            check_conn.close()
        if not (len(rows) == 1 and rows[0][0] == "ok"):
            raise RuntimeError(
                f"atomic_replace_live_db: temp file at {tmp_path!r} failed a real "
                f"PRAGMA integrity_check ({rows!r}) -- refusing to touch the live file")

        os.replace(tmp_path, db_path)  # atomic same-filesystem rename: the one real write to db_path
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
    return db_path


def vacuum_compact_db(db_path=None):
    """Real compaction, added 2026-08-15 as the first real use of
    atomic_replace_live_db() above: `VACUUM INTO` writes a brand-new,
    compacted copy of db_path to a temp path (never touching db_path itself
    while it runs), which atomic_replace_live_db then validates and swaps
    in with one atomic rename. Held under the same _write_lock() every
    other real write path in this file uses, so no concurrent writer can be
    mid-transaction when the swap happens."""
    db_path = db_path or DB_PATH

    def _build(tmp_path):
        src_conn = sqlite3.connect(db_path, timeout=30)
        try:
            src_conn.execute("VACUUM INTO ?", (tmp_path,))
        finally:
            src_conn.close()

    with _write_lock():
        return atomic_replace_live_db(_build, db_path=db_path)


def init_db():
    conn = _connect()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS instructions (
        instruction_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        session_id TEXT,
        utm_source TEXT NOT NULL,
        utm_medium TEXT NOT NULL,
        utm_campaign TEXT,
        utm_content TEXT,
        utm_term TEXT,
        raw_text TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        response_summary TEXT
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS instructions_fts USING fts5(
        instruction_id UNINDEXED, raw_text, utm_content, utm_term, response_summary,
        content='instructions', content_rowid='rowid'
    );
    CREATE TRIGGER IF NOT EXISTS instructions_ai AFTER INSERT ON instructions BEGIN
        INSERT INTO instructions_fts(rowid, instruction_id, raw_text, utm_content, utm_term, response_summary)
        VALUES (new.rowid, new.instruction_id, new.raw_text, new.utm_content, new.utm_term, new.response_summary);
    END;

    CREATE TABLE IF NOT EXISTS work_items (
        work_item_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        instruction_id TEXT,
        software_task_id TEXT,
        ai_task_id TEXT,
        cache_id TEXT,
        ai_cache_id TEXT,
        utm_source TEXT NOT NULL,
        utm_medium TEXT NOT NULL,
        utm_campaign TEXT,
        utm_content TEXT,
        utm_term TEXT,
        status TEXT NOT NULL DEFAULT 'open',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY (instruction_id) REFERENCES instructions(instruction_id)
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS work_items_fts USING fts5(
        work_item_id UNINDEXED, utm_content, utm_term,
        content='work_items', content_rowid='rowid'
    );
    CREATE TRIGGER IF NOT EXISTS work_items_ai AFTER INSERT ON work_items BEGIN
        INSERT INTO work_items_fts(rowid, work_item_id, utm_content, utm_term)
        VALUES (new.rowid, new.work_item_id, new.utm_content, new.utm_term);
    END;

    CREATE TABLE IF NOT EXISTS actions (
        action_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        work_item_id TEXT,
        instruction_id TEXT,
        utm_source TEXT NOT NULL,
        utm_medium TEXT NOT NULL,
        utm_campaign TEXT,
        utm_content TEXT NOT NULL,
        utm_term TEXT,
        result TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY (work_item_id) REFERENCES work_items(work_item_id),
        FOREIGN KEY (instruction_id) REFERENCES instructions(instruction_id)
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS actions_fts USING fts5(
        action_id UNINDEXED, utm_content, utm_term, result,
        content='actions', content_rowid='rowid'
    );
    CREATE TRIGGER IF NOT EXISTS actions_ai AFTER INSERT ON actions BEGIN
        INSERT INTO actions_fts(rowid, action_id, utm_content, utm_term, result)
        VALUES (new.rowid, new.action_id, new.utm_content, new.utm_term, new.result);
    END;

    CREATE INDEX IF NOT EXISTS idx_instructions_campaign ON instructions(utm_campaign);
    CREATE INDEX IF NOT EXISTS idx_work_items_instruction ON work_items(instruction_id);
    CREATE INDEX IF NOT EXISTS idx_work_items_campaign ON work_items(utm_campaign);
    CREATE INDEX IF NOT EXISTS idx_actions_work_item ON actions(work_item_id);
    CREATE INDEX IF NOT EXISTS idx_actions_campaign ON actions(utm_campaign);

    -- 4th tree (2026-07-20, Owner directive: "indexation of everything we
    -- do is missing... that's why wrong files/scripts/tables keep getting
    -- picked"). Catalogs every real mechanism (script/service/table) found
    -- during this session's audits, not the work-event history above --
    -- this answers "does X already exist and where" BEFORE building
    -- anything, which the other 3 trees cannot (they log what happened,
    -- not what exists).
    CREATE TABLE IF NOT EXISTS system_index (
        index_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        path TEXT NOT NULL,
        category TEXT NOT NULL,
        layer TEXT NOT NULL,
        status TEXT NOT NULL,
        purpose TEXT NOT NULL,
        utm_term TEXT,
        calls TEXT,
        called_by TEXT,
        verified_ts TEXT,
        tags TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS system_index_fts USING fts5(
        index_id UNINDEXED, path, purpose, utm_term, calls, called_by,
        content='system_index', content_rowid='rowid'
    );
    CREATE TRIGGER IF NOT EXISTS system_index_ai AFTER INSERT ON system_index BEGIN
        INSERT INTO system_index_fts(rowid, index_id, path, purpose, utm_term, calls, called_by)
        VALUES (new.rowid, new.index_id, new.path, new.purpose, new.utm_term, new.calls, new.called_by);
    END;
    -- 2026-07-23 fix (sqlite-corruption diagnosis): index_add() upserts via
    -- ON CONFLICT(path) DO UPDATE, but this external-content FTS5 table only
    -- had an AFTER INSERT sync trigger -- every re-verification of an
    -- already-indexed path (the documented, intended, common case) silently
    -- desynced system_index_fts from system_index (stale search results, not
    -- what today's page/freelist corruption looked like, but a real bug in
    -- the same area named for scrutiny). IF NOT EXISTS makes this additive
    -- for pre-existing DBs too, no separate migration needed.
    CREATE TRIGGER IF NOT EXISTS system_index_au AFTER UPDATE ON system_index BEGIN
        INSERT INTO system_index_fts(system_index_fts, rowid, index_id, path, purpose, utm_term, calls, called_by)
        VALUES ('delete', old.rowid, old.index_id, old.path, old.purpose, old.utm_term, old.calls, old.called_by);
        INSERT INTO system_index_fts(rowid, index_id, path, purpose, utm_term, calls, called_by)
        VALUES (new.rowid, new.index_id, new.path, new.purpose, new.utm_term, new.calls, new.called_by);
    END;
    CREATE INDEX IF NOT EXISTS idx_system_index_category ON system_index(category);
    CREATE INDEX IF NOT EXISTS idx_system_index_status ON system_index(status);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_system_index_path ON system_index(path);

    -- 5th tree (2026-07-23, ai-os/EXECUTION_RULES_AUDIT_2026-07-23.yaml Part 40 --
    -- pre_execution_checklist_automation phase). Per-task machine-readable
    -- Pre-Execution Log / Post-Execution Log: one row per (task, phase), each
    -- row holding every checklist field's real YES/NO + evidence, not a
    -- fabricated claim. Additive new table, same pattern as task_audits in
    -- postflight_audit_gate.py -- not a parallel store to the 4 trees above,
    -- which log work-events/system-inventory, not per-field checklist compliance.
    -- 6th tree (2026-07-23, governance item 52, searchable_indexed_logs):
    -- ai-os/logs/*.log(.jsonl) are plain-text/JSONL, grep-only -- no
    -- index/search service existed. Same FTS5 CREATE VIRTUAL TABLE + AFTER
    -- INSERT trigger convention as instructions/actions/system_index above,
    -- populated by scripts/index-logs.py (idempotent per-file line-tracking,
    -- same pattern as index_transcript()'s own state file).
    CREATE TABLE IF NOT EXISTS log_index (
        log_index_id TEXT PRIMARY KEY,
        log_file TEXT NOT NULL,
        line_no INTEGER NOT NULL,
        ts TEXT,
        content TEXT NOT NULL
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS log_index_fts USING fts5(
        log_index_id UNINDEXED, log_file, content,
        content='log_index', content_rowid='rowid'
    );
    CREATE TRIGGER IF NOT EXISTS log_index_ai AFTER INSERT ON log_index BEGIN
        INSERT INTO log_index_fts(rowid, log_index_id, log_file, content)
        VALUES (new.rowid, new.log_index_id, new.log_file, new.content);
    END;
    CREATE UNIQUE INDEX IF NOT EXISTS idx_log_index_file_line ON log_index(log_file, line_no);

    CREATE TABLE IF NOT EXISTS execution_log (
        execution_log_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        phase TEXT NOT NULL,
        work_item_id TEXT,
        software_task_id TEXT,
        source_script TEXT NOT NULL,
        fields_json TEXT NOT NULL,
        yes_count INTEGER NOT NULL,
        no_count INTEGER NOT NULL,
        total_fields INTEGER NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_execution_log_software_task ON execution_log(software_task_id);
    CREATE INDEX IF NOT EXISTS idx_execution_log_work_item ON execution_log(work_item_id);

    -- 2026-07-23, task-20260723-142643 (veridian-task-watchdog.py): per-
    -- error-signature auto-recovery memory. A signature is the first 60
    -- chars of a stalled/looping task's checkpoint note -- deliberately
    -- coarse (not a full hash) so near-identical recurrences of the same
    -- failure (e.g. same exception, different task_id/timestamp suffix)
    -- still match. One row per signature (PRIMARY KEY, not autoincrement):
    -- the watchdog's step_2 looks up a signature and, if a fix_action is
    -- already on file, re-applies the SAME action instead of re-escalating
    -- to a fresh RCA task every time the identical failure recurs.
    CREATE TABLE IF NOT EXISTS known_fixes (
        signature TEXT PRIMARY KEY,
        fix_action TEXT NOT NULL,
        last_applied TEXT,
        success_count INTEGER NOT NULL DEFAULT 0
    );

    -- 7th tree (2026-07-23, Knowledge Engine Phase 1, task-20260723-181151).
    -- Builds the real table proposed in
    -- ai-os/KNOWLEDGE_ENGINE_SCHEMA_DESIGN_2026-07-23.yaml's proposed_table
    -- (verbatim create_statement -- Phase 0 already reviewed this design,
    -- this phase builds it, does not redesign it), extending system_index's
    -- own proven "path + metadata, not a content copy" contract. One row per
    -- real knowledge/rules/constitution artifact found in
    -- ai-os/KNOWLEDGE_ENGINE_INVENTORY_2026-07-23.yaml -- artifact_path +
    -- content_hash let a query detect drift without ever storing the bytes.
    CREATE TABLE IF NOT EXISTS knowledge_engine (
        artifact_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        artifact_path TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        artifact_type TEXT NOT NULL CHECK(artifact_type IN ('canonical','derived')),
        secondary_path TEXT,
        exists_on_disk INTEGER NOT NULL DEFAULT 1,
        purpose TEXT NOT NULL,
        tags TEXT,
        entity_relationships TEXT NOT NULL DEFAULT '[]',
        last_verified_ts TEXT NOT NULL,
        verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED'
            CHECK(verification_status IN ('VERIFIED_MATCH','HASH_DRIFTED','PATH_MISSING','UNVERIFIED')),
        metadata_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_engine_fts USING fts5(
        artifact_path, purpose, tags, entity_relationships,
        content='knowledge_engine', content_rowid='rowid'
    );
    CREATE TRIGGER IF NOT EXISTS knowledge_engine_ai AFTER INSERT ON knowledge_engine BEGIN
        INSERT INTO knowledge_engine_fts(rowid, artifact_path, purpose, tags, entity_relationships)
        VALUES (new.rowid, new.artifact_path, new.purpose, new.tags, new.entity_relationships);
    END;
    CREATE TRIGGER IF NOT EXISTS knowledge_engine_au AFTER UPDATE ON knowledge_engine BEGIN
        INSERT INTO knowledge_engine_fts(knowledge_engine_fts, rowid, artifact_path, purpose, tags, entity_relationships)
        VALUES ('delete', old.rowid, old.artifact_path, old.purpose, old.tags, old.entity_relationships);
        INSERT INTO knowledge_engine_fts(rowid, artifact_path, purpose, tags, entity_relationships)
        VALUES (new.rowid, new.artifact_path, new.purpose, new.tags, new.entity_relationships);
    END;
    CREATE TRIGGER IF NOT EXISTS knowledge_engine_ad AFTER DELETE ON knowledge_engine BEGIN
        INSERT INTO knowledge_engine_fts(knowledge_engine_fts, rowid, artifact_path, purpose, tags, entity_relationships)
        VALUES ('delete', old.rowid, old.artifact_path, old.purpose, old.tags, old.entity_relationships);
    END;
    CREATE INDEX IF NOT EXISTS idx_knowledge_engine_type ON knowledge_engine(artifact_type);
    CREATE INDEX IF NOT EXISTS idx_knowledge_engine_path ON knowledge_engine(artifact_path);

    -- 8th tree (2026-07-24, VERIDIAN 20-ENGINE/10-GATEWAY architecture Phase 1,
    -- task-20260724-083420, closes_engines: [3]). Wires
    -- ai-os/CAPABILITY_REGISTRY_SCHEMA_2026-07-24.yaml's capability_record_schema
    -- live -- one row per real VERIDIAN capability, the PART4 field set
    -- (business_rules/workflow/automation/documents/reports/apis/ui_screens/
    -- permissions/ai_required/confidence/version/owner) that
    -- capability-registry-service.ts's embedding index and
    -- capability-tree-service.ts's CapabilityNode tree do not carry as
    -- structured columns. Same table/FTS5/upsert-on-conflict convention as
    -- knowledge_engine above, not a new pattern.
    --
    -- Stage 7 pilot (2026-07-29, task-20260729, VERIDIAN_CONSOLIDATED_COMPLETION,
    -- Option B system-wide UTM adoption): utm_source/utm_medium/utm_campaign/
    -- utm_content/utm_term added -- the smallest, lowest-stakes of the 3 bespoke
    -- tables targeted for the UTM metadata standard already used by
    -- instructions/work_items/actions above. register_capability() populates all
    -- five on every insert/update going forward; see
    -- _derive_capability_utm_fields()/_migrate_capability_registry_utm() for the
    -- real backfill of the 11 pre-existing rows.
    CREATE TABLE IF NOT EXISTS capability_registry (
        capability_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        capability_name TEXT NOT NULL,
        inputs TEXT NOT NULL DEFAULT '[]',
        business_rules TEXT NOT NULL DEFAULT '[]',
        workflow TEXT,
        automation TEXT,
        documents TEXT,
        reports TEXT,
        apis TEXT NOT NULL DEFAULT '[]',
        ui_screens TEXT,
        permissions TEXT NOT NULL,
        ai_required INTEGER NOT NULL DEFAULT 0,
        confidence REAL NOT NULL DEFAULT 0.0,
        version TEXT NOT NULL DEFAULT 'unversioned',
        owner TEXT NOT NULL,
        last_verified_ts TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        utm_source TEXT NOT NULL DEFAULT 'superboss-register.py',
        utm_medium TEXT NOT NULL DEFAULT 'register-capability',
        utm_campaign TEXT,
        utm_content TEXT,
        utm_term TEXT
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS capability_registry_fts USING fts5(
        capability_name, owner, apis, ui_screens, workflow, utm_source, utm_campaign, utm_term,
        content='capability_registry', content_rowid='rowid'
    );
    CREATE TRIGGER IF NOT EXISTS capability_registry_ai AFTER INSERT ON capability_registry BEGIN
        INSERT INTO capability_registry_fts(rowid, capability_name, owner, apis, ui_screens, workflow, utm_source, utm_campaign, utm_term)
        VALUES (new.rowid, new.capability_name, new.owner, new.apis, new.ui_screens, new.workflow, new.utm_source, new.utm_campaign, new.utm_term);
    END;
    CREATE TRIGGER IF NOT EXISTS capability_registry_au AFTER UPDATE ON capability_registry BEGIN
        INSERT INTO capability_registry_fts(capability_registry_fts, rowid, capability_name, owner, apis, ui_screens, workflow, utm_source, utm_campaign, utm_term)
        VALUES ('delete', old.rowid, old.capability_name, old.owner, old.apis, old.ui_screens, old.workflow, old.utm_source, old.utm_campaign, old.utm_term);
        INSERT INTO capability_registry_fts(rowid, capability_name, owner, apis, ui_screens, workflow, utm_source, utm_campaign, utm_term)
        VALUES (new.rowid, new.capability_name, new.owner, new.apis, new.ui_screens, new.workflow, new.utm_source, new.utm_campaign, new.utm_term);
    END;
    CREATE TRIGGER IF NOT EXISTS capability_registry_ad AFTER DELETE ON capability_registry BEGIN
        INSERT INTO capability_registry_fts(capability_registry_fts, rowid, capability_name, owner, apis, ui_screens, workflow, utm_source, utm_campaign, utm_term)
        VALUES ('delete', old.rowid, old.capability_name, old.owner, old.apis, old.ui_screens, old.workflow, old.utm_source, old.utm_campaign, old.utm_term);
    END;
    CREATE UNIQUE INDEX IF NOT EXISTS idx_capability_registry_name ON capability_registry(capability_name);
    CREATE INDEX IF NOT EXISTS idx_capability_registry_ai_required ON capability_registry(ai_required);
    CREATE INDEX IF NOT EXISTS idx_capability_registry_campaign ON capability_registry(utm_campaign);

    -- 9th tree (2026-07-24, Testing Engine / IRVF Phase 3, task-20260724-115924,
    -- TESTING_ENGINE_PHASE_PLAN_2026-07-24.yaml phase_3_route_replay_storage_and_diff).
    -- Reuses knowledge_engine's own artifact_path/content_hash pattern above --
    -- per this session's 'extend, don't duplicate' rule -- rather than a new
    -- database: one row per real captured/replayed route execution, insert-only
    -- (a route accumulates a capture row and, later, one replay row per replay
    -- run -- never UPDATEd in place, so the full replay history for a route_id
    -- is queryable, not just its latest state). request_payload/response_payload
    -- are the real JSON call args / return value for the route's dispatch-target
    -- function (see ai-os-scripts/generate_route_tests.py's REGISTERED_FIXTURES
    -- for where request_payload's values come from); *_hash are sha256 over the
    -- same bytes, same drift-detection convention as knowledge_engine.content_hash.
    -- The per-route replay_status STATE (not_yet_captured -> captured ->
    -- replayed_match/replayed_diff) itself lives on
    -- ROUTE_REGISTRY_SCHEMA_2026-07-24.yaml's populated_routes[].replay_status
    -- field (this table is the evidence backing that field, same relationship
    -- test_status already has to ai-os/testing_engine_evidence/phase1/).
    CREATE TABLE IF NOT EXISTS route_replay (
        replay_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        route_id TEXT NOT NULL,
        capability_name TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK(event_type IN ('capture','replay')),
        request_payload TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        response_payload TEXT NOT NULL,
        response_hash TEXT NOT NULL,
        baseline_replay_id TEXT,
        diff_result TEXT CHECK(diff_result IN ('match','diff')),
        diff_detail TEXT,
        artifact_path TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY (baseline_replay_id) REFERENCES route_replay(replay_id)
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS route_replay_fts USING fts5(
        route_id, capability_name, diff_detail,
        content='route_replay', content_rowid='rowid'
    );
    CREATE TRIGGER IF NOT EXISTS route_replay_ai AFTER INSERT ON route_replay BEGIN
        INSERT INTO route_replay_fts(rowid, route_id, capability_name, diff_detail)
        VALUES (new.rowid, new.route_id, new.capability_name, new.diff_detail);
    END;
    CREATE INDEX IF NOT EXISTS idx_route_replay_route_id ON route_replay(route_id);
    CREATE INDEX IF NOT EXISTS idx_route_replay_event_type ON route_replay(event_type);

    -- 10th tree (2026-07-26, VERIDIAN WIRING ENGINE Phase 3,
    -- task-20260726-162252-extend-wiring-engine-to-full-system--ser,
    -- ai-os/WIRING_ENGINE_PHASE_PLAN_2026-07-25.yaml phase_3_wiring_registry_live_wiring).
    -- Wires ai-os/WIRING_ENGINE_SCHEMA_2026-07-25.yaml's entity_record_schema live -- one
    -- row per real entity in the wiring engine's cross-source graph (engine/gateway/
    -- supabase_table/function/route/file/script/cron_job/ai_role/vercel_project/
    -- github_repo/browser_component). Same table/FTS5/upsert-on-conflict convention as
    -- capability_registry above, not a new pattern. Populated by
    -- scripts/generate_wiring_registry.py's bulk upsert (direct sqlite3, same
    -- bypass-the-CLI-for-bulk-writes convention scripts/batch-import-conversation-log.py
    -- already established), not one register-entity CLI call per entity -- that CLI
    -- subcommand exists for a single ad hoc row, not a ~7600-row batch.
    CREATE TABLE IF NOT EXISTS wiring_registry (
        entity_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        entity_type TEXT NOT NULL CHECK(entity_type IN (
            'engine','gateway','supabase_table','function','route','file','script','cron_job',
            'ai_role','vercel_project','github_repo','browser_component','dispatch_event',
            'governance_doc'
        )),
        source_system TEXT NOT NULL CHECK(source_system IN ('server','vercel','supabase','github')),
        path TEXT,
        relationships TEXT NOT NULL DEFAULT '[]',
        last_verified_ts TEXT NOT NULL,
        verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED'
            CHECK(verification_status IN ('VERIFIED_MATCH','HASH_DRIFTED','PATH_MISSING','UNVERIFIED')),
        source_ref TEXT NOT NULL DEFAULT '[]',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        -- 2026-07-27 (task-20260727-025248): real sha256 over the entity's own live
        -- file bytes at generation time (multi-path engine/gateway entities: sorted
        -- concatenation, see generate_wiring_registry.py's compute_content_hash()) --
        -- same drift-detection contract knowledge_engine.content_hash already uses,
        -- extended here so re-running the generator can tell "content changed" apart
        -- from "path still exists" (verification_status alone only ever checked the
        -- latter). NULL for entity types with no single real file (ai_role,
        -- vercel_project, dispatch_event, ...) -- never a required field.
        content_hash TEXT
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS wiring_registry_fts USING fts5(
        path, entity_type, source_ref,
        content='wiring_registry', content_rowid='rowid'
    );
    CREATE TRIGGER IF NOT EXISTS wiring_registry_ai AFTER INSERT ON wiring_registry BEGIN
        INSERT INTO wiring_registry_fts(rowid, path, entity_type, source_ref)
        VALUES (new.rowid, new.path, new.entity_type, new.source_ref);
    END;
    CREATE TRIGGER IF NOT EXISTS wiring_registry_au AFTER UPDATE ON wiring_registry BEGIN
        INSERT INTO wiring_registry_fts(wiring_registry_fts, rowid, path, entity_type, source_ref)
        VALUES ('delete', old.rowid, old.path, old.entity_type, old.source_ref);
        INSERT INTO wiring_registry_fts(rowid, path, entity_type, source_ref)
        VALUES (new.rowid, new.path, new.entity_type, new.source_ref);
    END;
    CREATE TRIGGER IF NOT EXISTS wiring_registry_ad AFTER DELETE ON wiring_registry BEGIN
        INSERT INTO wiring_registry_fts(wiring_registry_fts, rowid, path, entity_type, source_ref)
        VALUES ('delete', old.rowid, old.path, old.entity_type, old.source_ref);
    END;
    CREATE INDEX IF NOT EXISTS idx_wiring_registry_entity_type ON wiring_registry(entity_type);
    CREATE INDEX IF NOT EXISTS idx_wiring_registry_source_system ON wiring_registry(source_system);

    -- 11th tree (2026-08-06, critical amendment to UMR-20260806-124327-6ffb /
    -- UMR-20260806-124055-bc80, task-20260806-181146-critical-amendment--every-task-must-sear,
    -- UMR-20260806-124654-a8d6). Step four of the required deterministic-first
    -- task sequence (step one: exact capability_registry match, no AI; step two:
    -- search past umr_tasks for reusable precedent; step three: only then does AI
    -- work proceed, under a UMR-scoped agent_id): the moment real AI work
    -- completes, this table records the mandatory graduation evaluation -- can
    -- this exact piece of work become a permanent deterministic script. One row
    -- per evaluation, insert-only (never UPDATEd -- a UMR that is re-evaluated
    -- gets a second row, so the full history stays queryable, same convention as
    -- route_replay above). decision='graduated' rows MUST carry a real
    -- capability_id (the actual capability_registry row this graduated into,
    -- registered via register-capability in the same breath -- never a claim
    -- without a registered artifact backing it) and script_path; decision=
    -- 'judgment_required' rows carry neither, only a plain reason -- this table
    -- is where "no, this genuinely needs judgment every time" gets recorded
    -- instead of silently narrated away.
    CREATE TABLE IF NOT EXISTS capability_graduation_log (
        graduation_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        umr_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        task_summary TEXT NOT NULL,
        decision TEXT NOT NULL CHECK(decision IN ('graduated', 'judgment_required')),
        reason TEXT NOT NULL,
        capability_id TEXT,
        script_path TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY (capability_id) REFERENCES capability_registry(capability_id)
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS capability_graduation_log_fts USING fts5(
        umr_id, agent_id, task_summary, reason,
        content='capability_graduation_log', content_rowid='rowid'
    );
    CREATE TRIGGER IF NOT EXISTS capability_graduation_log_ai AFTER INSERT ON capability_graduation_log BEGIN
        INSERT INTO capability_graduation_log_fts(rowid, umr_id, agent_id, task_summary, reason)
        VALUES (new.rowid, new.umr_id, new.agent_id, new.task_summary, new.reason);
    END;
    CREATE INDEX IF NOT EXISTS idx_capability_graduation_log_umr_id ON capability_graduation_log(umr_id);
    CREATE INDEX IF NOT EXISTS idx_capability_graduation_log_decision ON capability_graduation_log(decision);
    """)
    conn.commit()
    _migrate_schema(conn)
    _migrate_knowledge_engine_fts(conn)
    conn.close()
    print(json.dumps({"ok": True, "db": DB_PATH}))


def _ensure_execution_log_table(conn):
    """Standalone idempotent create, mirrors postflight_audit_gate.py's own
    ensure_tables() defensiveness -- works even if init_db() was never run
    against this DB (e.g. an older DB re-created outside init_db)."""
    conn.execute("""CREATE TABLE IF NOT EXISTS execution_log (
        execution_log_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        phase TEXT NOT NULL,
        work_item_id TEXT,
        software_task_id TEXT,
        source_script TEXT NOT NULL,
        fields_json TEXT NOT NULL,
        yes_count INTEGER NOT NULL,
        no_count INTEGER NOT NULL,
        total_fields INTEGER NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_execution_log_software_task ON execution_log(software_task_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_execution_log_work_item ON execution_log(work_item_id)")
    conn.commit()


def _migrate_schema(conn):
    """Additive, idempotent migrations for DBs created before a column existed.
    CREATE TABLE IF NOT EXISTS above only covers brand-new DBs; a pre-existing
    system_index needs ALTER TABLE to pick up new columns (2026-07-23:
    nullable `tags`, JSON-encoded list, per ai-os/EXECUTION_RULES_AUDIT_2026-07-23.yaml
    Part 14/15/20 -- zero-duplication extension of the existing table, not a
    second tags store)."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(system_index)").fetchall()}
    if "tags" not in cols:
        conn.execute("ALTER TABLE system_index ADD COLUMN tags TEXT")
        conn.commit()
    _migrate_wiring_registry_content_hash(conn)
    _migrate_wiring_registry_entity_types(conn)
    _ensure_umr_table(conn)
    _ensure_ocid_artifact_links_table(conn)
    _ensure_resume_dead_letter_table(conn)
    _ensure_ocid_canonical_registry_table(conn)
    _ensure_ocid_master_standard_audit_log_table(conn)
    _ensure_ocid_compliance_tables(conn)
    _ensure_pm_decisions_pending_table(conn)
    _ensure_registry_taxonomy_notes_table(conn)
    _seed_registry_taxonomy_notes(conn)
    _migrate_instructions_content_hash(conn)
    _migrate_capability_registry_utm(conn)
    _migrate_wiring_registry_umr_and_version(conn)
    _migrate_wiring_registry_vector(conn)
    _migrate_capability_registry_vector(conn)
    _ensure_external_agent_dispatch_table(conn)
    _ensure_master_issue_tracker_table(conn)
    _ensure_governance_cycle_log_table(conn)
    _ensure_search_cache_table(conn)


def _migrate_wiring_registry_content_hash(conn):
    """2026-07-27 (task-20260727-025248, knowledge-engine/wiring-registry integration):
    additive ALTER TABLE ADD COLUMN for wiring_registry.content_hash, same pattern as the
    system_index.tags column above -- no CHECK constraint involved, so (unlike
    _migrate_wiring_registry_entity_types below) this never needs a full table rebuild.
    Called both from _migrate_schema() AND from the top of
    _migrate_wiring_registry_entity_types() itself, because dispatch_core._upsert_wiring_row
    calls that function directly, bypassing _migrate_schema() by its own design (see that
    function's docstring) -- so its rebuild's SELECT ..., content_hash FROM wiring_registry
    must never run against a table that doesn't have the column yet. No-op once migrated."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='wiring_registry'"
    ).fetchone()
    if row is None:
        return  # table doesn't exist yet; the next CREATE TABLE IF NOT EXISTS covers it
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(wiring_registry)").fetchall()}
    if "content_hash" not in cols:
        conn.execute("ALTER TABLE wiring_registry ADD COLUMN content_hash TEXT")
        conn.commit()


def _migrate_wiring_registry_umr_and_version(conn):
    """2026-08-06 (task-20260806-035541, Owner directive "real PM cycle script
    registry"): additive ALTER TABLE ADD COLUMN for wiring_registry.originating_umr
    and .script_version, same no-CHECK-constraint-involved pattern as
    _migrate_wiring_registry_content_hash above -- never needs the full-table
    rebuild _migrate_wiring_registry_entity_types needs. No-op once migrated,
    safe to call on every startup."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='wiring_registry'"
    ).fetchone()
    if row is None:
        return  # table doesn't exist yet; the next CREATE TABLE IF NOT EXISTS covers it
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(wiring_registry)").fetchall()}
    if "originating_umr" not in cols:
        conn.execute("ALTER TABLE wiring_registry ADD COLUMN originating_umr TEXT")
        conn.commit()
    if "script_version" not in cols:
        conn.execute("ALTER TABLE wiring_registry ADD COLUMN script_version TEXT")
        conn.commit()


def _migrate_wiring_registry_vector(conn):
    """2026-08-07 (UMR-20260807-035145-aa45, amendment to UMR-20260806-171945-5767):
    additive ALTER TABLE ADD COLUMN for wiring_registry.vector_json/vector_updated_ts,
    same no-CHECK-constraint-involved pattern as _migrate_wiring_registry_umr_and_version
    above -- never needs the full-table rebuild _migrate_wiring_registry_entity_types
    needs. No-op once migrated, safe to call on every startup. See that CREATE TABLE's
    own comment in _ensure_wiring_registry_table for what these two columns hold."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='wiring_registry'"
    ).fetchone()
    if row is None:
        return  # table doesn't exist yet; the next CREATE TABLE IF NOT EXISTS covers it
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(wiring_registry)").fetchall()}
    if "vector_json" not in cols:
        conn.execute("ALTER TABLE wiring_registry ADD COLUMN vector_json TEXT")
        conn.commit()
    if "vector_updated_ts" not in cols:
        conn.execute("ALTER TABLE wiring_registry ADD COLUMN vector_updated_ts TEXT")
        conn.commit()


def _migrate_capability_registry_vector(conn):
    """2026-08-07 (UMR-20260807-035145-aa45, amendment to UMR-20260806-171945-5767):
    same additive vector_json/vector_updated_ts ADD COLUMN pair as
    _migrate_wiring_registry_vector above, for capability_registry. No-op once migrated."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='capability_registry'"
    ).fetchone()
    if row is None:
        return  # table doesn't exist yet; the next CREATE TABLE IF NOT EXISTS covers it
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(capability_registry)").fetchall()}
    if "vector_json" not in cols:
        conn.execute("ALTER TABLE capability_registry ADD COLUMN vector_json TEXT")
        conn.commit()
    if "vector_updated_ts" not in cols:
        conn.execute("ALTER TABLE capability_registry ADD COLUMN vector_updated_ts TEXT")
        conn.commit()


def _migrate_wiring_registry_entity_types(conn):
    """2026-07-27, dispatch-script consolidation: widens wiring_registry's entity_type
    CHECK constraint to allow 'dispatch_event' (see WIRING_ENTITY_TYPES above), extended
    2026-07-27 (task-20260727-025248) to also allow 'governance_doc'. SQLite has
    no ALTER TABLE for CHECK constraints, so a pre-existing table (this DB has one, created
    before this addition, with 7000+ real rows) needs a real rebuild: create a new table with
    the widened CHECK, copy every row across unchanged, drop the FTS5 index + its triggers
    (they reference the table by name and do not survive a swap), swap the new table into
    place, then recreate + fully rebuild the FTS5 index exactly like _ensure_wiring_registry_table
    does for a fresh DB. No-op (checked via sqlite_master's own stored CREATE TABLE text) once
    already migrated, so this is safe to call on every startup, same as the ADD COLUMN check
    above. The "already migrated" check tests for EVERY current WIRING_ENTITY_TYPES member
    (not one hardcoded literal) so the next entity_type addition after this one only needs to
    append to that tuple -- this function re-runs its (idempotent) rebuild exactly once more
    to pick up the new member, the same way this run picks up 'governance_doc' on a DB that
    already has 'dispatch_event' from the prior migration. Also runs
    _migrate_wiring_registry_umr_and_version() first, same reason as content_hash below --
    the rebuild's own SELECT/CREATE TABLE must carry originating_umr/script_version across
    too, or a future rebuild triggered by a new entity_type addition would silently drop
    them despite both being additive, no-CHECK-constraint columns themselves."""
    _migrate_wiring_registry_content_hash(conn)
    _migrate_wiring_registry_umr_and_version(conn)
    _migrate_wiring_registry_vector(conn)
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='wiring_registry'"
    ).fetchone()
    if row is None or all(f"'{t}'" in row["sql"] for t in WIRING_ENTITY_TYPES):
        return  # table doesn't exist yet (a later CREATE TABLE IF NOT EXISTS covers that) or already migrated

    conn.execute("DROP TRIGGER IF EXISTS wiring_registry_ai")
    conn.execute("DROP TRIGGER IF EXISTS wiring_registry_au")
    conn.execute("DROP TRIGGER IF EXISTS wiring_registry_ad")
    conn.execute("DROP TABLE IF EXISTS wiring_registry_fts")

    entity_types_sql = ",".join("'" + t + "'" for t in WIRING_ENTITY_TYPES)
    conn.execute(f"""CREATE TABLE wiring_registry__migrate (
        entity_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        entity_type TEXT NOT NULL CHECK(entity_type IN ({entity_types_sql})),
        source_system TEXT NOT NULL CHECK(source_system IN ({",".join("'" + s + "'" for s in WIRING_SOURCE_SYSTEMS)})),
        path TEXT,
        relationships TEXT NOT NULL DEFAULT '[]',
        last_verified_ts TEXT NOT NULL,
        verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED'
            CHECK(verification_status IN ('VERIFIED_MATCH','HASH_DRIFTED','PATH_MISSING','UNVERIFIED')),
        source_ref TEXT NOT NULL DEFAULT '[]',
        metadata_json TEXT NOT NULL DEFAULT '{{}}',
        content_hash TEXT,
        originating_umr TEXT,
        script_version TEXT,
        vector_json TEXT,
        vector_updated_ts TEXT
    )""")
    conn.execute(
        "INSERT INTO wiring_registry__migrate (entity_id, ts, entity_type, source_system, path, "
        "relationships, last_verified_ts, verification_status, source_ref, metadata_json, content_hash, "
        "originating_umr, script_version, vector_json, vector_updated_ts) "
        "SELECT entity_id, ts, entity_type, source_system, path, relationships, last_verified_ts, "
        "verification_status, source_ref, metadata_json, content_hash, originating_umr, script_version, "
        "vector_json, vector_updated_ts "
        "FROM wiring_registry"
    )
    conn.execute("DROP TABLE wiring_registry")
    conn.execute("ALTER TABLE wiring_registry__migrate RENAME TO wiring_registry")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wiring_registry_entity_type ON wiring_registry(entity_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wiring_registry_source_system ON wiring_registry(source_system)")

    _ensure_wiring_registry_table(conn)  # recreates the FTS5 table + its 3 triggers (IF NOT EXISTS, safe)
    conn.execute("INSERT INTO wiring_registry_fts(wiring_registry_fts) VALUES ('rebuild')")
    conn.commit()


def _derive_capability_utm_fields(record, campaign_override=None):
    """Real, deterministic UTM field derivation for one capability_registry row
    (Stage 7 pilot, task-20260729, VERIDIAN_CONSOLIDATED_COMPLETION, Option B
    system-wide UTM adoption). Every value is pulled from data already present
    on the row/record itself -- never a placeholder:

    utm_source   -- constant 'superboss-register.py': the one real mechanism
                     that has ever written to capability_registry
                     (register_capability() is the only INSERT/UPDATE call
                     site against this table in this file).
    utm_medium   -- constant 'register-capability': the actual CLI subcommand
                     name (sub.add_parser("register-capability") below) every
                     row is written through, whether by a live caller or a
                     batch backfill script shelling out to it.
    utm_campaign -- the real initiative/phase/task this capability was
                     registered under. Prefers metadata_json's own
                     'registered_by_phase' key when the caller supplied one
                     (e.g. the Mother Router phase_9 registrations), else
                     falls back to the owner field itself when it already IS
                     a task id (owner.startswith('task-')), else
                     `campaign_override` (used by the one-time backfill below
                     to propagate a same-batch sibling's resolved campaign),
                     else 'unclassified' -- never a fabricated phase name for
                     a row that genuinely doesn't carry one.
    utm_content  -- the real, specific mechanism differentiator for this
                     exact capability: workflow, else automation, else the
                     first documents entry, else the first apis entry, else
                     'n/a' if the record genuinely has none of those.
    utm_term     -- capability_name itself, so FTS keyword continuity
                     survives even if capability_name is ever renamed in
                     place (register_capability()'s ON CONFLICT DO UPDATE
                     keeps capability_id stable but capability_name can
                     legitimately change).

    `record` accepts either a live capability_registry row (dict/sqlite3.Row,
    JSON-encoded TEXT columns as stored) or a --record-file dict
    (already-parsed Python lists/dicts) -- same field names either way.
    """
    def _first_of(v):
        if v is None:
            return None
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except (ValueError, TypeError):
                return v.strip() or None
        if isinstance(v, list):
            return v[0] if v else None
        return v or None

    metadata = record.get("metadata_json") if record.get("metadata_json") is not None else record.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata) if metadata else {}
        except (ValueError, TypeError):
            metadata = {}
    metadata = metadata or {}

    owner = record.get("owner") or ""
    campaign = (
        metadata.get("registered_by_phase")
        or (owner if owner.startswith("task-") else None)
        or campaign_override
        or "unclassified"
    )
    content = (
        _first_of(record.get("workflow"))
        or _first_of(record.get("automation"))
        or _first_of(record.get("documents"))
        or _first_of(record.get("apis"))
        or "n/a"
    )
    return {
        "utm_source": "superboss-register.py",
        "utm_medium": "register-capability",
        "utm_campaign": campaign,
        "utm_content": content,
        "utm_term": record.get("capability_name"),
    }


def _migrate_capability_registry_utm(conn):
    """Stage 7 pilot (task-20260729, VERIDIAN_CONSOLIDATED_COMPLETION, Option B
    system-wide UTM adoption, per the evidence-based options memo): additive
    ALTER TABLE ADD COLUMN for capability_registry's five UTM fields, same
    no-CHECK-constraint / no-base-table-rebuild pattern as
    _migrate_wiring_registry_content_hash / _migrate_instructions_content_hash
    above -- capability_registry has never had a CHECK constraint that would
    force the full-table-rebuild path _migrate_wiring_registry_entity_types
    uses instead. Columns are added nullable (not NOT NULL) because SQLite's
    ALTER TABLE ADD COLUMN cannot add a NOT NULL column to a non-empty table
    without a constant DEFAULT -- same real constraint _migrate_instructions_
    content_hash's content_hash column works around the same way. No-op once
    the columns exist (checked via PRAGMA table_info, same convention as
    every other migration in this file) -- safe to call on every startup.

    Backfill: the real, live rows this pilot ran against (11 total, well
    within hand-verifiable range) must not carry NULL utm_term forever (a
    NULL term can never match capability_registry_fts's utm_term column,
    silently defeating the FTS-continuity purpose it exists for). Re-checked
    on every subsequent call (cheap indexed SELECT, normally 0 rows) so an
    interrupted backfill self-heals on the next startup instead of leaving
    stragglers NULL forever -- same self-healing convention as
    _migrate_instructions_content_hash.

    Real campaign backfill nuance: 5 of the 11 pre-existing rows (the
    2026-07-24T08:56:34 VCEL-engine-audit batch) carry no metadata_json
    phase key and no task-id-shaped owner field, so their utm_campaign is
    honestly 'unclassified' -- not guessed. The other 6 (the
    2026-07-24T13:47:04 document-pipeline batch, 5 rows, plus the standalone
    2026-07-27 Mother Router row) DO resolve for real: one row in the
    document-pipeline batch (document_duplicate_detection) has its owner
    field literally set to the real task id that registered the whole
    batch (task-20260724-133622-phase4-unify-document-pipeline-pdf-gener) --
    since every row in that batch shares the same wall-clock registration
    second (a real, verifiable fact, not an assumption) and none contradicts
    it with a metadata phase of its own, that resolved id is propagated to
    its same-second siblings via `campaign_override` below rather than left
    unclassified. This propagation runs once per real distinct row set, not
    hardcoded per capability_name, so it re-applies correctly to any future
    DB carrying the same historical rows.

    FTS5 rebuild: capability_registry_fts must gain utm_source/utm_campaign/
    utm_term as real searchable columns (Stage 6's check_duplicate()/
    _fts_query() regression target). FTS5 has no ALTER-TABLE-ADD-COLUMN path
    usable here without losing the external-content 'rebuild' semantics, so
    this mirrors _migrate_wiring_registry_entity_types's proven mechanism
    exactly: drop the 3 triggers, drop the shadow table, recreate both via
    the idempotent _ensure_capability_registry_table() (which already carries
    the widened column list), then re-populate via the fts5 'rebuild'
    command. Checked for need via the shadow table's own stored CREATE TABLE
    text (same idiom as the entity_type widening check above) so this half
    only runs once, is a no-op afterward, and self-heals if a prior run
    added the base columns but was interrupted before the FTS rebuild."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='capability_registry'"
    ).fetchone()
    if row is None:
        return  # table doesn't exist yet; the next CREATE TABLE IF NOT EXISTS covers it

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(capability_registry)").fetchall()}
    if "utm_source" not in cols:
        for col in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"):
            conn.execute(f"ALTER TABLE capability_registry ADD COLUMN {col} TEXT")
        conn.commit()

    backfill_rows = conn.execute(
        "SELECT * FROM capability_registry WHERE utm_term IS NULL"
    ).fetchall()
    if backfill_rows:
        # Pass 1: resolve every row independently from its own real data.
        resolved = {r["capability_id"]: _derive_capability_utm_fields(dict(r)) for r in backfill_rows}
        # Pass 2: propagate a resolved same-second sibling's campaign to any
        # row in the same real registration batch that couldn't resolve one
        # on its own (see docstring above -- a real, verifiable grouping by
        # wall-clock second, not a guess).
        by_second = {}
        for r in backfill_rows:
            by_second.setdefault(r["ts"][:19], []).append(r["capability_id"])
        for cids in by_second.values():
            batch_campaign = next(
                (resolved[cid]["utm_campaign"] for cid in cids if resolved[cid]["utm_campaign"] != "unclassified"),
                None,
            )
            if batch_campaign:
                for cid in cids:
                    if resolved[cid]["utm_campaign"] == "unclassified":
                        row_for_cid = next(r for r in backfill_rows if r["capability_id"] == cid)
                        resolved[cid] = _derive_capability_utm_fields(dict(row_for_cid), campaign_override=batch_campaign)
        for cid, fields in resolved.items():
            conn.execute(
                "UPDATE capability_registry SET utm_source=?, utm_medium=?, utm_campaign=?, utm_content=?, utm_term=? "
                "WHERE capability_id=?",
                (fields["utm_source"], fields["utm_medium"], fields["utm_campaign"], fields["utm_content"], fields["utm_term"], cid),
            )
        conn.commit()

    fts_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='capability_registry_fts'"
    ).fetchone()
    if fts_row is not None and "utm_term" not in fts_row["sql"]:
        conn.execute("DROP TRIGGER IF EXISTS capability_registry_ai")
        conn.execute("DROP TRIGGER IF EXISTS capability_registry_au")
        conn.execute("DROP TRIGGER IF EXISTS capability_registry_ad")
        conn.execute("DROP TABLE IF EXISTS capability_registry_fts")
        _ensure_capability_registry_table(conn)  # recreates the FTS5 table + its 3 triggers (IF NOT EXISTS, safe)
        conn.execute("INSERT INTO capability_registry_fts(capability_registry_fts) VALUES ('rebuild')")
        conn.commit()


def _migrate_instructions_content_hash(conn):
    """Stage 2 (task-20260729, VERIDIAN_CONSOLIDATED_COMPLETION Phase 3, content-hash
    dedup for same-text chat resubmission): additive ALTER TABLE ADD COLUMN for
    instructions.content_hash, same no-CHECK-constraint / no-rebuild-needed pattern as
    _migrate_wiring_registry_content_hash above -- instructions has never had a CHECK
    constraint that would force the full-table-rebuild path
    _migrate_wiring_registry_entity_types uses instead. No-op once the column exists
    (checked via PRAGMA table_info, same convention as every other migration in this
    file) -- safe to call on every startup.

    Backfill: hundreds of pre-existing rows must not carry a NULL content_hash
    forever (a NULL hash can never match in check_content_duplicate below, silently
    defeating dedup for anything resembling their text). Runs in the same pass as
    the ADD COLUMN, and also re-checked on every subsequent call (cheap indexed
    SELECT, normally 0 rows) so an interrupted backfill self-heals on the next
    startup instead of leaving stragglers NULL forever.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='instructions'"
    ).fetchone()
    if row is None:
        return  # table doesn't exist yet; the next CREATE TABLE IF NOT EXISTS covers it
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(instructions)").fetchall()}
    if "content_hash" not in cols:
        conn.execute("ALTER TABLE instructions ADD COLUMN content_hash TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_instructions_content_hash ON instructions(content_hash)")
        conn.commit()

    backfill_rows = conn.execute(
        "SELECT instruction_id, raw_text FROM instructions WHERE content_hash IS NULL"
    ).fetchall()
    if backfill_rows:
        for r in backfill_rows:
            conn.execute(
                "UPDATE instructions SET content_hash = ? WHERE instruction_id = ?",
                (_content_hash_for_text(r["raw_text"]), r["instruction_id"]),
            )
        conn.commit()


def _normalize_instruction_text_for_hash(text):
    """Content-hash dedup normalization (Stage 2, task-20260729). Strips the real,
    consistent OWNER_ENGINE wrapper that prompt_gateway/gateway.py's
    _build_final_output() prepends/appends to every gated chat before it ever
    reaches log-instruction: a per-submission '[VERIDIAN:<chat_id>]' header (a
    different chat_id every single call -- hashing it in would mean identical
    resubmitted text NEVER matches, defeating this feature entirely), a
    '[CAT:...|INTENT:...]' line, optional '[ENTS:...]' / '[SNIPS:...]' lines, and a
    trailing '---\\n[CONTEXT]\\n...' block (prior-turn context that legitimately
    differs between two submissions of the same real instruction -- the dedup
    question is "is the core instruction the same", not "was the surrounding chat
    session identical"). Each piece is optional/independent, matching how
    _build_final_output builds them, so text that never went through the gate
    (ai_agent/software submissions, transcript auto-index rows) simply has nothing
    to strip here and falls through untouched to the plain whitespace/case
    normalization every input gets regardless.
    """
    t = text
    # The [CONTEXT] block is anchored at the end -- strip it first so the header
    # strips below don't have to worry about it reappearing mid-text.
    t = re.split(r"\n-{3,}\n\[CONTEXT\]\n", t, maxsplit=1)[0]
    t = re.sub(r"^\[VERIDIAN:[^\]\n]*\]\n?", "", t)
    t = re.sub(r"^\[CAT:[^\]\n]*\]\n?", "", t)
    t = re.sub(r"^\[ENTS:[^\]\n]*\]\n?", "", t)
    # [SNIPS:...] is emitted right after machine_prompt (not at the very top), so
    # strip it wherever it appears as its own line rather than assuming position.
    t = re.sub(r"\n\[SNIPS:[^\]\n]*\]", "", t)
    # Same-text-regardless-of-formatting normalization every input gets, gated or not.
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def _content_hash_for_text(text):
    """sha256 of the normalized text -- see _normalize_instruction_text_for_hash's
    docstring for exactly what is/isn't included."""
    return hashlib.sha256(_normalize_instruction_text_for_hash(text).encode("utf-8")).hexdigest()


def check_content_duplicate(text, window_hours=24):
    """Stage 2 (task-20260729, VERIDIAN_CONSOLIDATED_COMPLETION Phase 3): the
    content-hash counterpart to check_duplicate() above. That function catches
    "does a mechanism like this already exist" (system_index, category/keyword
    search); this one catches "has THIS EXACT instruction text already been
    submitted recently" (instructions, content_hash exact match) -- a real,
    confirmed gap: every gateway.py "start" action minted a fresh instruction_id
    even for byte-identical repeated instruction text, with nothing catching it.

    window_hours bounds the match to recent submissions only (default 24h) so a
    deliberate, legitimate resubmission days later is never silently blocked --
    this is a dedup guard against accidental resubmission, not a permanent ban on
    ever repeating yourself.

    Returns the prior matching instruction_id (str) if a real match exists within
    the window, else None. Callable directly (as here, e.g. for tests or other
    Python callers within this process) or via the check-content-duplicate CLI
    subcommand below, which is how task-gateway.py's cmd_submit reaches it
    (subprocess, same convention as its existing check-duplicate call)."""
    init_db_silent()
    conn = _connect()
    target_hash = _content_hash_for_text(text)
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    row = conn.execute(
        "SELECT instruction_id FROM instructions WHERE content_hash = ? AND ts >= ? "
        "ORDER BY ts ASC LIMIT 1",
        (target_hash, cutoff),
    ).fetchone()
    conn.close()
    return row["instruction_id"] if row else None


def cmd_check_content_duplicate(args):
    prior_id = check_content_duplicate(args.text, window_hours=args.window_hours)
    print(json.dumps({
        "content_duplicate_found": prior_id is not None,
        "duplicate_instruction_id": prior_id,
    }))


# ---------------------------------------------------------------------------
# Short-TTL search-result cache (task-20260814-181008): a real, confirmed gap
# -- task-gateway.py's cmd_submit re-runs check-duplicate/search/query-
# knowledge (this file's own FTS5 lookups) AND run_zoekt_search (a live HTTP
# call to the real Zoekt webserver) on every single dispatch, even when a
# near-identical query text just ran minutes earlier from a different
# dispatch (the real, common case: e.g. the Desktop sentinel and an owner-
# engine-gated chat submitting overlapping instructions within the same
# short window -- see check_target_identifier_duplicate's own docstring
# above for a real prior incident of exactly this shape). No caching existed
# for any of these four calls before this.
#
# TTL = 5 minutes (SEARCH_CACHE_TTL_SECONDS, env-overridable like every other
# TTL constant in this codebase -- see EXTERNAL_AGENT_DISPATCH_TTL_HOURS
# below / resource_governor.py's HEARTBEAT_STALE_TTL_SECONDS for the same
# convention). Chosen because: (1) it comfortably covers the real "different
# dispatch minutes later" overlap window this feature targets -- short-lived
# duplicate bursts, not hours-old staleness; (2) it stays well under
# check_content_duplicate's own 24h window and check_target_identifier_
# duplicate's 4h window above, so this cache is never the reason a genuinely
# new instruction/knowledge-fragment/capability registered in between two
# dispatches gets masked for long -- new rows in instructions/
# knowledge_engine/capability_registry are common on this box, so a longer
# TTL would risk serving stale search/knowledge/duplicate/zoekt results well
# past the point they stopped being accurate.
#
# Reuses the existing superboss-register.sqlite file (search_cache table,
# created via _ensure_search_cache_table() below, same idempotent-CREATE-
# TABLE convention as every other table in this file) -- no new database.
# Keyed on a normalized, order-insensitive hash of the query text (see
# _search_cache_key() below) so "foo bar" and "bar foo" -- the same keyword
# set extracted in a different order by two different dispatches -- hit the
# same cache entry.
# ---------------------------------------------------------------------------
SEARCH_CACHE_TTL_SECONDS = int(os.environ.get("VERIDIAN_SEARCH_CACHE_TTL_S", str(5 * 60)))


def _search_cache_key(query_text):
    """Order-insensitive, case/whitespace-normalized sha256 of query_text --
    same normalize-then-hash shape as _content_hash_for_text() above, but
    token-sorted first so re-ordered keyword extraction (e.g. two different
    dispatches producing "foo bar" vs "bar foo" from the same underlying
    text) still hits the same cache entry."""
    normalized = " ".join(sorted(re.sub(r"\s+", " ", (query_text or "")).strip().lower().split()))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _ensure_search_cache_table(conn):
    """Idempotent CREATE TABLE IF NOT EXISTS, same convention as
    _ensure_governance_cycle_log_table() below."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS search_cache (
            cache_key TEXT PRIMARY KEY,
            query_text TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_ts TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_search_cache_created_ts ON search_cache(created_ts)"
    )


def get_search_cache(query_text, ttl_seconds=None):
    """Real cache lookup: returns {"hit": bool, "result": <cached dict or
    None>, "age_seconds": <float or None>, "cache_key": <str>}. A row older
    than ttl_seconds (default SEARCH_CACHE_TTL_SECONDS) is treated exactly
    like a miss -- expired rows are left in place (not deleted here); the
    next put_search_cache() call for the same key overwrites it (INSERT OR
    REPLACE), so no separate sweep/GC job is needed for this short-TTL,
    self-healing cache."""
    ttl = SEARCH_CACHE_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    init_db_silent()
    conn = _connect()
    _ensure_search_cache_table(conn)
    cache_key = _search_cache_key(query_text)
    row = conn.execute(
        "SELECT result_json, created_ts FROM search_cache WHERE cache_key = ?",
        (cache_key,),
    ).fetchone()
    conn.close()
    if row is None:
        return {"hit": False, "result": None, "age_seconds": None, "cache_key": cache_key, "ttl_seconds": ttl}
    created = datetime.fromisoformat(row["created_ts"])
    age_seconds = (datetime.now(timezone.utc) - created).total_seconds()
    if age_seconds > ttl:
        return {"hit": False, "result": None, "age_seconds": age_seconds, "cache_key": cache_key, "ttl_seconds": ttl}
    return {
        "hit": True,
        "result": json.loads(row["result_json"]),
        "age_seconds": age_seconds,
        "cache_key": cache_key,
        "ttl_seconds": ttl,
    }


def put_search_cache(query_text, result_obj):
    """Real INSERT OR REPLACE -- upserts the cache entry for query_text's
    key, always stamped with the current time (so a stale row that had aged
    past TTL, then got a fresh live search run behind it, starts a new TTL
    window from now rather than reusing its old created_ts)."""
    init_db_silent()
    conn = _connect()
    _ensure_search_cache_table(conn)
    cache_key = _search_cache_key(query_text)
    with _write_lock():
        conn.execute(
            "INSERT OR REPLACE INTO search_cache (cache_key, query_text, result_json, created_ts) "
            "VALUES (?, ?, ?, ?)",
            (cache_key, query_text, json.dumps(result_obj, default=str), _now_iso()),
        )
        conn.commit()
    conn.close()
    return cache_key


def cmd_get_search_cache(args):
    result = get_search_cache(args.query_text, ttl_seconds=args.ttl_seconds)
    print(json.dumps(result, indent=2, default=str))


def cmd_put_search_cache(args):
    try:
        result_obj = json.loads(args.result_json)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"put-search-cache: --result-json is not valid JSON: {e}"}))
        sys.exit(1)
        return
    cache_key = put_search_cache(args.query_text, result_obj)
    print(json.dumps({"ok": True, "cache_key": cache_key}, indent=2, default=str))


# ---------------------------------------------------------------------------
# Deterministic target-identifier dedup (addendum to UMR-20260813-102459-10c3,
# itself addendum to UMR-20260813-084321-2962 / P1 UMR-20260806-171945-5767).
# Real incident this fixes (2026-08-13): the Desktop sentinel dispatched
# UMR-...-a248 (targeting PR #131) and UMR-...-1489 (targeting PR #135), then
# the Desktop session independently dispatched UMR-...-bd10 (same PR #131)
# and UMR-...-9a69 (same PR #135) minutes later -- resource_governor.py
# --search on the exact PR text returned nothing (FTS5 MATCH is fuzzy
# token-overlap ranking, not an exact-substring guarantee, and missed an
# exact recent duplicate whose wording differed from the first dispatch), so
# both duplicate pairs ran concurrently against the same PR branches,
# wasting real tokens and risking a git collision. check_content_duplicate()
# above only catches BYTE-IDENTICAL (normalized) prompt text -- two
# dispatches phrased differently about the same real target sail straight
# past it too.
#
# The fix: a real, deterministic (not fuzzy, not hash-exact) check. Pulls
# the most recent umr_tasks rows via query_umr_tasks(limit=30, no status
# filter, newest first -- the exact shape this incident's own fix
# requirement specifies), and for every row still queued/running within the
# last `window_hours` (default 4h), extracts the same class of "target
# identifier" (UMR id, PR number+repo, exact file path, or exact script
# name) from ITS OWN real prompt/title text and checks for an exact
# intersection with the target identifiers of the dispatch about to happen.
# This is deliberately orthogonal to check_content_duplicate()'s exact-hash
# match and to --search's fuzzy FTS5 match: three independent, complementary
# dedup layers, not one widened to try to cover all three cases.
#
# UMR-20260813-220216-2e2b real addendum: the UMR-id extraction above was
# added after this dedup layer itself missed two more real duplicate-spend
# incidents on 2026-08-13 that named their target purely by UMR id (an
# RCA "for" a UMR, not a PR/file/script) -- see extract_target_identifiers()'s
# own docstring for the concrete evidence. This is still target-identifier
# matching, not governing-chain matching: a UMR id only counts as a target
# identifier when it is what the dispatch is ABOUT (e.g. "RCA: UMR-X
# killed"), which is exactly what a plain substring match against title+
# prompt text captures -- it says nothing about, and is not confused by, an
# unrelated "addendum to UMR-Y" governing-chain citation elsewhere in the
# same text meaning something different.
# ---------------------------------------------------------------------------

_TARGET_ID_PR_EXPLICIT_REPO_RE = re.compile(r'\b([A-Za-z0-9_.-]+)#(\d+)\b')
_TARGET_ID_PR_BARE_RE = re.compile(r'\bPR\s*#\s*(\d+)\b', re.IGNORECASE)
_TARGET_ID_FILE_PATH_RE = re.compile(
    r'\b[A-Za-z0-9_][\w./-]*/[\w.-]+\.(?:py|sh|md|yaml|yml|json|ts|tsx|js|jsx|txt|sql|cfg|ini|toml)\b')
_TARGET_ID_SCRIPT_NAME_RE = re.compile(r'(?<![\w/.-])([A-Za-z0-9_-]+\.(?:py|sh))\b')
# Reuse the same canonical UMR-id pattern _extract_umr_ids() (OCID canonical
# resolution, above) already matches PR bodies against -- one real regex for
# "what does a UMR id look like", not two independently-drifting ones.

# UMR-20260814-010802-b566 real fix (live-deploy-drift-p0 reconciliation):
# real incident, live 2026-08-14T01:16:15Z tick, veridian-pm-sentinel-tick-
# cron.log -- this task's OWN dispatch row (a long, real evidence-dump
# prompt that, like nearly every prompt this whole pipeline generates,
# instructs the worker to "query resource_governor.py --query-umr ...
# yourself first") was still `status=running` inside find_target_identifier_
# duplicate()'s 4h/limit=30 window. Because _TARGET_ID_SCRIPT_NAME_RE
# matches ANY bare `word.py`/`word.sh` token anywhere in free text with no
# regard for whether it names the dispatch's real work TARGET or is just
# instructional boilerplate citing this repo's own standing tooling, three
# unrelated same-tick dispatches (the tick's own deploy-drift self-dispatch,
# plus two independent compliance-tracker RCA dispatches that also cite
# "resource_governor.py --query-umr" per pm-sentinel-tick.sh's own template)
# were wrongly REFUSED as "duplicates" of this task, purely because both
# sides' prompts happened to mention the same meta-tooling script name.
# resource_governor.py and superboss-register.py are cited as the real,
# standing query/mutation front doors in essentially every dispatch prompt
# this codebase's own dispatch-owner-task.sh/pm-sentinel-tick.sh/dispatch-
# tick.py pipeline generates (see e.g. pm-sentinel-tick.sh's own RCA
# template) -- on their own, bare mentions of either name are never a real,
# distinguishing work target the way a PR number, a real nested file path,
# or another script's name is; a dispatch that genuinely targets one of
# these two files' own code is still caught by _TARGET_ID_FILE_PATH_RE
# (e.g. "scripts/resource_governor.py") or a cited PR number, so this is a
# narrowing of one specific false-positive-prone signal, not a removal of
# real coverage. Same "narrow, evidenced, real-incident-driven exclusion"
# precedent already established by _DISCLOSURE_CITATION_RE above (Stage 6's
# duplicate-PR guard).
_TARGET_ID_SCRIPT_NAME_BOILERPLATE_EXCLUDED = {"resource_governor.py", "superboss-register.py"}

# UMR-20260815-052932-e80b real fix (task-20260815-145619, three consecutive
# real refusals, 2026-08-15): task-20260815-135327-d6ad ("reject invalid
# complexity_tier constant in pm_lifecycle so minted tasks pass schema
# validation", a real, genuine edit of pm_lifecycle.py) was refused as a
# duplicate of the already-queued UMR-20260815-044235-a5e1 ("PM-in-Server:
# add real Part3+4 GTM-cert completion tracking to pm-sentinel-tick.sh"),
# which does not touch pm_lifecycle.py at all. a5e1's real prompt mentions
# "pm_lifecycle.py" exactly twice -- "check whether a real pm_lifecycle.py
# orchestrator run ... is already in flight" and "dispatch exactly one real
# pm_lifecycle.py run via this file's own dispatch_gap()" -- both naming it
# as something a5e1's OWN real target (pm-sentinel-tick.sh) dispatches/runs,
# never as something a5e1 edits. a5e1's prompt declares no TARGET:/SCOPE:
# section (this false-positive class was not yet known when it was
# written), so the UMR-20260814-034424-ded4 scope-aware restriction above
# never engages on its side: the whole text is scanned in fallback mode and
# both "orchestrator run"/"run" citations are extracted exactly like a real
# edit target, producing a false script:pm_lifecycle.py overlap against
# d6ad's genuine, unrelated one. This is the real, one-directional gap that
# incident exposed: today's scope-aware restriction only ever narrows the
# NEW dispatch's own identifiers (via its own declared TARGET:/SCOPE:
# section); a historical stored row that predates the TARGET:/SCOPE:
# convention -- essentially every row in the table -- gets no equivalent
# narrowing at all, no matter how clearly its own prose distinguishes "what
# I run" from "what I edit".
#
# Real, deterministic, narrow fix for this one well-evidenced shape: a
# script/path identifier immediately followed by "run", "orchestrator run",
# or bare "orchestrator" names that occurrence as something being INVOKED,
# not edited, and is excluded from contributing an identifier -- this
# applies at the individual regex-match level, so it works both inside a
# declared TARGET:/SCOPE: section and (the actual fix) in fallback
# full-text-scan mode, on either side of a find_target_identifier_duplicate()
# comparison, with no dependency on whether that particular text ever
# declares a section of its own. A name that also appears elsewhere in the
# same text in a genuine, non-citation context is still extracted normally
# from that occurrence -- this excludes only the specific citation
# occurrence, never blanket-excludes the identifier by name (unlike
# _TARGET_ID_SCRIPT_NAME_BOILERPLATE_EXCLUDED above, which does, and stays
# unchanged). Same "narrow, evidenced, real-incident-driven exclusion"
# precedent as that set and as _DISCLOSURE_CITATION_RE; deliberately does
# NOT touch umr:/pr: extraction, which has its own separate real-incident
# history and no false-positive of this shape.
_TARGET_ID_INVOCATION_CITATION_TRAILING_RE = re.compile(
    r'\s*(?:orchestrator\s+)?(?:run|orchestrator|invocation|execution)\b',
    re.IGNORECASE,
)

# UMR-20260814-034424-ded4 real fix (PM Sentinel first-hand reproduction,
# 2026-08-14T03:38-03:42Z UTC): three consecutive real refusals of
# legitimate, non-duplicate P0 dispatches, all racked up against the SAME
# already-running duplicate_umr_id (a disk-fix task). extract_target_
# identifiers() scanned the entire title+prompt as one undifferentiated
# blob, with no way to distinguish an identifier that names the dispatch's
# real work TARGET from one merely CITED AS EVIDENCE or explicitly marked
# OUT OF SCOPE / PRIOR CONTEXT:
#   1. a live-checkout-drift dispatch was refused because its prompt cited
#      the disk-fix UMR id purely to say the disk cause was already owned
#      elsewhere;
#   2. the same dispatch, with that UMR id removed, was refused AGAIN --
#      this time on the shared worker task-directory name both prompts
#      referenced only as the location of evidence files;
#   3. an invocation-accounting dispatch was refused because it named the
#      worker entrypoint script, which the disk-fix prompt had mentioned
#      only as the file a line number of evidence lived in (the disk task
#      never modifies that script at all).
# Each was worked around only by deleting true, useful evidence from the
# prompt until the matcher stopped firing -- training dispatchers toward
# vaguer, less verifiable prompts, exactly backwards from the intent.
#
# Two real, additive mechanisms fix this without weakening genuine
# same-target detection (the guard's whole reason for existing: two PM
# tiers really did dispatch colliding work against the same PR branches on
# 2026-08-13 -- see the module comment above, none of those real prompts
# used any of this structure, so the fallback path below leaves them
# caught exactly as before):
#
#   (a) Explicit TARGET:/SCOPE: section. A prompt that declares one is
#       stating its own exhaustive target list -- extraction is then
#       restricted to that section (plus the title, always scanned in
#       full, since the title *is* the field that names the work). Any
#       other prose in the prompt -- including a long evidentiary
#       appendix citing other UMRs/paths/scripts -- is excluded even
#       without any additional marking. This is the direct fix for
#       scenario 2 above (a shared evidence-file path cited outside the
#       declared TARGET: section never counts).
#   (b) Fallback for prompts with no such section (this is the "at
#       minimum" bar): a whole section explicitly headed OUT OF SCOPE: /
#       PRIOR CONTEXT: / EVIDENCE: / NOT-A-TARGET: is stripped before
#       extraction (fixes scenario 1 -- a disk-fix UMR id cited only to
#       say its cause is owned elsewhere), and the explicit, machine-
#       readable inline escape hatch `[NOT-A-TARGET: ...]` /
#       `[EVIDENCE-ONLY: ...]` (etc.) lets a dispatcher neutralize one
#       specific citation without restructuring the whole prompt (fixes
#       scenario 3 -- a script name cited only for a line number of
#       evidence). The escape hatch is stripped unconditionally, in both
#       modes, since it is always an explicit dispatcher statement that
#       "this identifier is evidence, not my target."
_TARGET_ID_ESCAPE_HATCH_RE = re.compile(
    r'\[\s*(?:NOT-A-TARGET|NOT-TARGET|EVIDENCE-ONLY|EVIDENCE|OUT-OF-SCOPE|PRIOR-CONTEXT)'
    r'\s*:[^\]]*\]',
    re.IGNORECASE,
)
_TARGET_ID_SECTION_HEADER_RE = re.compile(
    r'(?im)^[ \t]*(TARGET(?:[ -]IDENTIFIERS?)?|SCOPE|OUT[ -]OF[ -]SCOPE|PRIOR[ -]CONTEXT|'
    r'EVIDENCE(?:[ -]ONLY)?|NOT[ -](?:A[ -])?TARGET)[ \t]*:'
)
_TARGET_ID_SECTION_LABELS = {"TARGET", "TARGET IDENTIFIER", "TARGET IDENTIFIERS", "SCOPE"}
_TARGET_ID_EXCLUDED_SECTION_LABELS = {
    "OUT OF SCOPE", "PRIOR CONTEXT", "EVIDENCE", "EVIDENCE ONLY",
    "NOT TARGET", "NOT A TARGET",
}
_TARGET_ID_BLANK_LINE_RE = re.compile(r'\n[ \t]*\n')


def _truncate_excluded_section_at_blank_line(content):
    """Independent-audit fix (post UMR-20260814-034424-ded4, same UMR):
    `_split_labeled_sections()` gives an excluded-label section
    (OUT OF SCOPE:/PRIOR CONTEXT:/EVIDENCE(-ONLY):/NOT-(A-)TARGET:) an
    unbounded span -- from right after its own `LABEL:` to the *next
    recognized header or end of text*. In an unstructured fallback-mode
    prompt with no closing header, that silently swallows every genuine
    target identifier that happens to be written *after* the excluded
    citation with no header of its own -- e.g. "PRIOR CONTEXT: <UMR
    cited only as evidence>\\n\\nNow the actual work: land PR #500."
    dropped PR #500 entirely, a real false negative (the guard fails to
    flag a genuine duplicate). Only the immediate cited paragraph is
    truly "the citation" -- bound the exclusion to the first blank line
    (paragraph break) and return the (included) remainder separately, so
    unrelated trailing prose is scanned exactly as if the excluded
    citation were never there. Deliberately conservative in the same
    direction as the rest of this function: when in doubt, keep scanning
    (a spurious "possible duplicate" flag is cheap; a missed real
    duplicate is not)."""
    m = _TARGET_ID_BLANK_LINE_RE.search(content)
    if not m:
        return content[:0]
    return content[m.start():]


def _split_labeled_sections(text):
    """Split `text` at recognized start-of-line `LABEL:` headers
    (TARGET:/SCOPE:/OUT OF SCOPE:/PRIOR CONTEXT:/EVIDENCE(-ONLY):/
    NOT-(A-)TARGET:, case-insensitive, hyphen/space-insensitive) into a
    list of (label, content) pairs. `label` is None for any text before
    the first recognized header (a prompt's normal opening prose, which
    counts under the no-section fallback but is excluded once an explicit
    TARGET:/SCOPE: section exists elsewhere in the same text -- see
    extract_target_identifiers()). A section's content runs from right
    after its own `LABEL:` to the start of the next recognized header (or
    end of text), so `LABEL: inline content on the same line` works
    exactly like a `LABEL:` header followed by content on subsequent
    lines."""
    matches = list(_TARGET_ID_SECTION_HEADER_RE.finditer(text))
    if not matches:
        return [(None, text)]
    segments = []
    if matches[0].start() > 0:
        segments.append((None, text[:matches[0].start()]))
    for i, m in enumerate(matches):
        label = re.sub(r'[\s-]+', ' ', m.group(1).strip()).upper()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        segments.append((label, text[start:end]))
    return segments


def extract_target_identifiers(text, default_repo=None):
    """Real, deterministic (regex, no fuzziness) extraction of "target
    identifiers" from free text -- UMR ids, PR number+repo, exact file
    paths, and exact script names -- see the module comment above this
    function for why this exists (the exact recent-incident dedup gap
    --search / check_content_duplicate() both missed). Returns a sorted
    list of normalized identifier strings, e.g. ["pr:claude-control#131",
    "path:scripts/resource_governor.py", "umr:UMR-20260807-151622-15cd"].
    Deliberately conservative: a bare "PR #131" with no repo anywhere
    (neither an explicit "<repo>#131" in the text nor a `default_repo`
    passed in) is skipped rather than guessed at -- a repo-less PR number
    is not a real target identifier on its own, and the caller always has
    (and must pass) its own real target repo.

    UMR-20260813-220216-2e2b real fix: the original version of this
    function only extracted PR numbers, file paths, and script names --
    it never recognized a UMR id itself as a target identifier. That gap
    is exactly what let two real duplicate-spend incidents slip past this
    same dedup layer on 2026-08-13: UMR-20260807-151622-15cd got a
    "RCA: UMR-20260807-151622-15cd killed" dispatch TWICE (UMR-...-4bcc at
    20:18, UMR-...-7615 at 21:17, worded differently enough that
    check_content_duplicate()'s exact-hash match also missed it), and
    UMR-20260813-195852-aa85 got an RCA dispatched (UMR-...-b0cc) for a
    target whose real fix had already merged as PR #323. Both incidents'
    dispatch text names the target purely by UMR id, with no PR number, no
    file path -- extract_target_identifiers() returned an empty set for
    them and find_target_identifier_duplicate() never even got a chance to
    compare. Extracting `umr:<id>` here closes that.

    UMR-20260814-034424-ded4 real fix (scope-aware matching, see the
    _TARGET_ID_ESCAPE_HATCH_RE / _TARGET_ID_SECTION_HEADER_RE module
    comment above for the real incident): before scanning, (1) any
    `[NOT-A-TARGET: ...]`/`[EVIDENCE-ONLY: ...]` (etc.) inline escape
    hatch is stripped unconditionally, then (2) if the text declares an
    explicit TARGET:/SCOPE: section, extraction is restricted to that
    section's content only (everything else -- including a long
    evidentiary appendix citing unrelated UMRs/paths/scripts -- is
    ignored); otherwise every OUT OF SCOPE:/PRIOR CONTEXT:/EVIDENCE(-
    ONLY):/NOT-(A-)TARGET: section is stripped and the remaining text is
    scanned in full, exactly as before this fix (the real 2026-08-13
    same-PR-branch collisions this guard exists for used none of this
    structure and are unaffected).

    UMR-20260815-052932-e80b real fix (see the
    _TARGET_ID_INVOCATION_CITATION_TRAILING_RE module comment above for the
    real incident): a script/path identifier immediately followed by "run",
    "orchestrator run", or bare "orchestrator" is excluded -- that specific
    occurrence names the identifier as something being invoked/dispatched,
    not edited. Unlike the TARGET:/SCOPE: section restriction above, this
    applies with no dependency on whether the surrounding text declares any
    section at all, so it is the real fix for the historical/stored-row
    side of a find_target_identifier_duplicate() comparison too -- a row
    written before this false-positive class was known, with no
    TARGET:/SCOPE: framing of its own, no longer has a bare "X orchestrator
    run" citation mistaken for a real edit target just because its text
    falls into fallback (no-section) scanning."""
    ids = set()
    text = text or ""

    text = _TARGET_ID_ESCAPE_HATCH_RE.sub(" ", text)
    segments = _split_labeled_sections(text)
    if any(label in _TARGET_ID_SECTION_LABELS for label, _ in segments):
        scan_text = " ".join(
            content for label, content in segments if label in _TARGET_ID_SECTION_LABELS)
    else:
        kept = []
        for label, content in segments:
            if label in _TARGET_ID_EXCLUDED_SECTION_LABELS:
                # Only the cited paragraph is excluded; trailing prose
                # past the first blank line is genuine dispatch text and
                # must still be scanned (see
                # _truncate_excluded_section_at_blank_line docstring).
                kept.append(_truncate_excluded_section_at_blank_line(content))
            else:
                kept.append(content)
        scan_text = " ".join(kept)
    text = scan_text

    for m in _UMR_ID_RE.finditer(text):
        ids.add(f"umr:{m.group(0)}")

    for m in _TARGET_ID_PR_EXPLICIT_REPO_RE.finditer(text):
        repo, num = m.group(1).lower(), m.group(2)
        ids.add(f"pr:{repo}#{num}")

    if default_repo:
        for m in _TARGET_ID_PR_BARE_RE.finditer(text):
            ids.add(f"pr:{default_repo.lower()}#{m.group(1)}")

    for m in _TARGET_ID_FILE_PATH_RE.finditer(text):
        if _TARGET_ID_INVOCATION_CITATION_TRAILING_RE.match(text, m.end()):
            continue
        ids.add(f"path:{m.group(0)}")

    for m in _TARGET_ID_SCRIPT_NAME_RE.finditer(text):
        name = m.group(1)
        if name in _TARGET_ID_SCRIPT_NAME_BOILERPLATE_EXCLUDED:
            continue
        if _TARGET_ID_INVOCATION_CITATION_TRAILING_RE.match(text, m.end()):
            continue
        ids.add(f"script:{name}")

    return sorted(ids)


def _target_identifiers_for_title_and_prompt(title, prompt, default_repo=None):
    """UMR-20260814-034424-ded4 real fix: title and prompt are extracted
    SEPARATELY and unioned, not concatenated into one blob first. The
    title is always the field that declares the dispatch's real work
    target (e.g. "Fix PR #131"), so it is always scanned in full; the
    prompt is scanned via extract_target_identifiers()'s scope-aware
    logic (an explicit TARGET:/SCOPE: section, if present, restricts
    extraction to just that section; otherwise OUT OF SCOPE:/PRIOR
    CONTEXT:/EVIDENCE:-labeled spans and `[NOT-A-TARGET: ...]`-style
    inline escape hatches are excluded). Concatenating first (the
    original shape) would let a TARGET:/SCOPE: section anywhere in the
    prompt silently swallow the title too, since prose before the first
    header is dropped in that mode -- title identifiers must never be at
    the mercy of how the prompt happens to be structured."""
    return (set(extract_target_identifiers(title or "", default_repo=default_repo))
            | set(extract_target_identifiers(prompt or "", default_repo=default_repo)))


def find_target_identifier_duplicate(conn, title, prompt, repo=None, window_hours=4, limit=30):
    """The real check itself: pulls query_umr_tasks(conn, limit=limit) --
    deliberately no status filter, newest-first, exactly the shape this
    incident's own fix requirement specifies (never --search alone) --
    and returns the first row (dict) within `window_hours` whose own real
    prompt/title (from inputs_json) shares an exact target identifier with
    (title, prompt), and whose status is still 'queued' or 'running' (a row
    that already finished/failed/was rejected is not a live duplicate to
    skip against). Returns None if there is no real match."""
    my_ids = _target_identifiers_for_title_and_prompt(title, prompt, default_repo=repo)
    if not my_ids:
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    # full=True (real regression fix, live-audit on PR #308 head 4380f7f9,
    # independently reproduced): query_umr_tasks()'s default light column
    # set (UMR-20260813-125756-9221) excludes inputs_json, so without this
    # every row.get("inputs_json") below silently returned None -- inputs
    # collapsed to {}, row_ids was always empty, and this dedup guard never
    # matched a real duplicate no matter how exact. limit defaults to 30
    # (hard-capped at MAX_UMR_QUERY_LIMIT=2000 by query_umr_tasks itself)
    # and every CLI invocation is already wrapped by
    # install_cli_resource_guard()'s wall-clock/RSS watchdog, so fetching
    # the blob columns for this bounded a real duplicate-dispatch check is
    # safe and necessary -- this function cannot do its one job without them.
    rows = query_umr_tasks(conn, limit=limit, full=True)
    for row in rows:
        if row.get("status") not in ("queued", "running"):
            continue
        ts_submitted = row.get("ts_submitted")
        if not ts_submitted:
            continue
        if isinstance(ts_submitted, str):
            try:
                ts_submitted = datetime.fromisoformat(ts_submitted)
            except ValueError:
                continue
        if ts_submitted.tzinfo is None:
            ts_submitted = ts_submitted.replace(tzinfo=timezone.utc)
        if ts_submitted < cutoff:
            continue

        inputs = row.get("inputs_json") or {}
        if isinstance(inputs, str):
            try:
                inputs = json.loads(inputs)
            except (TypeError, ValueError):
                inputs = {}
        row_repo = inputs.get("repo") or repo
        row_ids = _target_identifiers_for_title_and_prompt(
            inputs.get("title", ""), inputs.get("prompt", ""), default_repo=row_repo)
        if my_ids & row_ids:
            return row

    return None


def cmd_check_target_identifier_duplicate(args):
    conn = _connect()
    _ensure_umr_table(conn)
    row = find_target_identifier_duplicate(
        conn, args.title, args.prompt, repo=args.repo,
        window_hours=args.window_hours, limit=args.limit,
    )
    conn.close()
    print(json.dumps({
        "target_identifier_duplicate_found": row is not None,
        "duplicate_umr_id": row["umr_id"] if row else None,
        "duplicate_status": row["status"] if row else None,
    }))


def log_instruction(args):
    init_db_silent()
    conn = _connect()
    iid = _new_id("INS")
    content_hash = _content_hash_for_text(args.text)
    conn.execute(
        "INSERT INTO instructions (instruction_id, ts, session_id, utm_source, utm_medium, utm_campaign, utm_content, utm_term, raw_text, metadata_json, response_summary, content_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (iid, _now_iso(), args.session_id, args.source, args.medium, args.campaign, args.content, args.term,
         args.text, json.dumps(json.loads(args.metadata) if args.metadata else {}), args.response_summary, content_hash),
    )
    conn.commit()
    conn.close()
    print(json.dumps({"instruction_id": iid}))


def index_transcript(args):
    """Auto-indexes a laptop-chat transcript JSONL (pushed word-for-word by the
    Stop hook) into the existing instructions table + its FTS5 index -- reuses
    the existing schema/search path per STANDING_DIRECTIVE.yaml zero-duplication
    mandate, no parallel table. Idempotent via a per-file .indexed_line state
    file, same pattern the laptop-side push hook already uses."""
    state_path = args.file + ".indexed_line"
    last = 0
    if os.path.isfile(state_path):
        try:
            last = int(open(state_path).read().strip())
        except Exception:
            last = 0

    if not os.path.isfile(args.file):
        print(json.dumps({"indexed": 0, "note": "file not found"}))
        return

    init_db_silent()
    conn = _connect()
    indexed = 0
    line_no = last
    with open(args.file, encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, start=1):
            if line_no <= last:
                continue
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                d = json.loads(raw_line)
            except Exception:
                continue
            t = d.get("type")
            entry_uuid = d.get("uuid", "")
            ts = d.get("timestamp") or _now_iso()

            text = None
            role = None
            if t == "user":
                content = d.get("message", {}).get("content")
                if isinstance(content, str) and content.strip():
                    text = content
                    role = "owner"
            elif t == "assistant":
                content = d.get("message", {}).get("content")
                if isinstance(content, list):
                    parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                    joined = "\n".join(part for part in parts if part.strip())
                    if joined.strip():
                        text = joined
                        role = "ai_agent"

            if text and role:
                iid = _new_id("INS")
                try:
                    conn.execute(
                        "INSERT INTO instructions (instruction_id, ts, session_id, utm_source, utm_medium, utm_campaign, utm_content, utm_term, raw_text, metadata_json, response_summary, content_hash) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (iid, ts, args.session_id, role, "laptop_chat_auto", "", "", "",
                         text, json.dumps({"transcript_uuid": entry_uuid, "transcript_line": line_no}), None,
                         _content_hash_for_text(text)),
                    )
                    indexed += 1
                except Exception:
                    pass

    conn.commit()
    conn.close()

    with open(state_path, "w") as f:
        f.write(str(line_no))

    print(json.dumps({"indexed": indexed, "through_line": line_no}))


def log_work(args):
    init_db_silent()
    conn = _connect()
    wid = _new_id("WRK")
    # --ts override (2026-07-20, historical-import support): a bulk import of
    # past sessions needs to carry each entry's REAL date, not the moment of
    # import -- otherwise the whole point (an accurate timeline) is lost.
    ts = getattr(args, "ts", None) or _now_iso()
    conn.execute(
        "INSERT INTO work_items (work_item_id, ts, instruction_id, software_task_id, ai_task_id, cache_id, ai_cache_id, "
        "utm_source, utm_medium, utm_campaign, utm_content, utm_term, status, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (wid, ts, args.instruction_id, args.software_task_id, args.ai_task_id, args.cache_id, args.ai_cache_id,
         args.source, args.medium, args.campaign, args.content, args.term, args.status,
         json.dumps(json.loads(args.metadata) if args.metadata else {})),
    )
    conn.commit()
    conn.close()
    print(json.dumps({"work_item_id": wid}))


def log_action(args):
    init_db_silent()
    conn = _connect()
    aid = _new_id("ACT")
    conn.execute(
        "INSERT INTO actions (action_id, ts, work_item_id, instruction_id, utm_source, utm_medium, utm_campaign, utm_content, utm_term, result, metadata_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (aid, _now_iso(), args.work_item_id, args.instruction_id, args.source, args.medium, args.campaign,
         args.content, args.term, args.result, json.dumps(json.loads(args.metadata) if args.metadata else {})),
    )
    conn.commit()
    conn.close()
    print(json.dumps({"action_id": aid}))


def log_login(user, source_ip, method):
    """Governance item 11 (veridian_built_login_logging): reuses the existing
    actions table + actions_fts index (zero-duplication) instead of a parallel
    login table -- a login is just another action, source_ip/method carried in
    the same utm_content/utm_term columns log_action already uses for other
    action kinds. Callable directly (security-check.py imports this, since it
    already owns the only real auth.log parser -- see its own module docstring)
    as well as via the log-login CLI subcommand below."""
    init_db_silent()
    conn = _connect()
    aid = _new_id("ACT")
    conn.execute(
        "INSERT INTO actions (action_id, ts, work_item_id, instruction_id, utm_source, utm_medium, utm_campaign, utm_content, utm_term, result, metadata_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (aid, _now_iso(), None, None, "login", "ssh", None, source_ip, method, "success", json.dumps({"user": user})),
    )
    conn.commit()
    conn.close()
    return aid


STOPWORDS = {"the", "a", "an", "of", "to", "for", "and", "or", "in", "on", "vs", "is", "are", "be", "do", "does"}


def _fts_query(raw):
    """2026-07-20 fix: FTS5's default MATCH syntax is implicit AND across
    space-separated bare terms -- a natural query like 'software vs AI
    classification' silently returns ZERO rows if even one word (here:
    'vs') isn't indexed anywhere, which is exactly the false-negative this
    whole tool exists to prevent (a missed duplicate is worse than noise
    from a false positive). Strip stopwords, OR the remaining terms
    together -- forgiving by design for a discovery search.

    2026-07-29 Stage 4 fix: that OR-of-terms design is intentionally kept
    as-is here -- the bug was specifically that a multi-word phrase the
    caller marked as ONE unit (by wrapping it in double quotes, e.g.
    '"vendor invoice reconciliation"', or by hyphenating it into one token,
    e.g. 'vendor-invoice-reconciliation') lost that phrase boundary: the old
    code did raw.split() first -- which breaks on every space/hyphen -- and
    only stripped stray '"' characters off each already-severed word
    afterwards, so each word of the phrase became its own independent OR'd
    term. That let one generic constituent word (e.g. just 'invoice') match
    unrelated text on its own and false-flag as a duplicate. Quoted and
    hyphenated phrases are now pulled out before the split and re-emitted as
    a single FTS5 "exact phrase" clause (quoted, space-joined, adjacency
    required); every other bare word keeps the original behavior of its own
    OR'd term."""
    phrase_terms = []

    def _take_quoted_phrase(m):
        phrase_terms.append(m.group(1).strip())
        return " "

    # Pull out explicit "double quoted phrases" before the whitespace split
    # below ever gets a chance to sever them word-by-word.
    remainder = re.sub(r'"([^"]+)"', _take_quoted_phrase, raw)

    bare_terms = []
    for w in remainder.split():
        w = w.strip('"')
        if not w:
            continue
        if "-" in w:
            # A hyphenated single token is the caller's other way of
            # marking a multi-word phrase as one unit -- keep those words
            # together as one phrase too, instead of OR'ing them apart.
            phrase_terms.append(w.replace("-", " ").strip())
            continue
        if w.lower() in STOPWORDS:
            continue
        bare_terms.append(w)

    if not bare_terms and not phrase_terms:
        # Same "never return an empty/unusable query" fallback as before.
        fallback = raw.split() or [raw]
        bare_terms = [t.strip('"') for t in fallback if t.strip('"')]

    clauses = [f'"{t.replace(chr(34), chr(34) * 2)}"' for t in bare_terms]
    for p in phrase_terms:
        words_in_phrase = [w.replace('"', '""') for w in p.split() if w]
        if words_in_phrase:
            clauses.append('"' + " ".join(words_in_phrase) + '"')

    if not clauses:
        clauses = [f'"{raw.replace(chr(34), chr(34) * 2)}"']

    return " OR ".join(clauses)


def search(args):
    init_db_silent()
    conn = _connect()
    results = {"instructions": [], "work_items": [], "actions": [], "system_index": [], "log_index": []}
    q = _fts_query(args.query)
    for table, fts in [("instructions", "instructions_fts"), ("work_items", "work_items_fts"),
                        ("actions", "actions_fts"), ("system_index", "system_index_fts"),
                        ("log_index", "log_index_fts")]:
        try:
            rows = conn.execute(
                f"SELECT t.* FROM {fts} f JOIN {table} t ON t.rowid = f.rowid WHERE {fts} MATCH ? ORDER BY rank LIMIT ?",
                (q, args.limit),
            ).fetchall()
            results[table] = [dict(r) for r in rows]
        except sqlite3.OperationalError as e:
            results[table] = {"error": str(e)}
    if getattr(args, "tag", None):
        # tags is a JSON-encoded list (see index_add) -- membership check done
        # in Python rather than a JSON1 SQL function, since JSON1 is an
        # optional sqlite3 build-time extension not confirmed present here.
        rows = conn.execute("SELECT * FROM system_index").fetchall()
        results["system_index"] = [
            dict(r) for r in rows if args.tag in json.loads(r["tags"] or "[]")
        ]
    conn.close()
    print(json.dumps(results, indent=2, default=str))


def index_add(args):
    """Add or re-verify one system_index entry. path is UNIQUE -- re-running
    this on an already-indexed path UPDATES it (refreshes verified_ts,
    status, etc.) rather than erroring, since this is meant to be a living
    catalog re-checked over time, not a write-once log."""
    init_db_silent()
    conn = _connect()
    iid = _new_id("IDX")
    now = _now_iso()
    tags_list = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
    conn.execute(
        "INSERT INTO system_index (index_id, ts, path, category, layer, status, purpose, utm_term, calls, called_by, verified_ts, tags, metadata_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(path) DO UPDATE SET category=excluded.category, layer=excluded.layer, status=excluded.status, "
        "purpose=excluded.purpose, utm_term=excluded.utm_term, calls=excluded.calls, called_by=excluded.called_by, "
        "verified_ts=excluded.verified_ts, tags=excluded.tags, metadata_json=excluded.metadata_json",
        (iid, now, args.path, args.category, args.layer, args.status, args.purpose, args.term,
         args.calls, args.called_by, now, json.dumps(tags_list), json.dumps(json.loads(args.metadata) if args.metadata else {})),
    )
    conn.commit()
    row = conn.execute("SELECT index_id FROM system_index WHERE path=?", (args.path,)).fetchone()
    conn.close()
    print(json.dumps({"index_id": row["index_id"], "path": args.path}))


def check_duplicate(args):
    """The concrete fix for 'we keep duplicating': search system_index by
    category and/or keyword BEFORE building something new. Prints every
    existing mechanism that might already do what's being considered.

    Stage 6 (task-20260729, VERIDIAN_CONSOLIDATED_COMPLETION Phase 3) extension:
    system_index (113 rows) was the ONLY source ever consulted here, but three
    other real FTS-backed tables in this same DB carry real inventory that is
    just as relevant to "does this already exist" -- most importantly
    wiring_registry (7,783+ rows, by far the largest real inventory of actual
    code entities: engines/gateways/tables/functions/routes/files), plus
    knowledge_engine (349 rows) and capability_registry (11 rows). Reuses the
    existing _fts_query() (fixed Stage 4, same session) for every table rather
    than reimplementing FTS querying -- one query-building function, four
    tables. category filtering only ever applied to system_index historically
    (the other three tables don't share that column), so it continues to
    apply ONLY to system_index rows, exactly as before; the other three
    sources are keyword-only and unaffected by --category, whether or not one
    is supplied.

    Return shape is unchanged on purpose ({"found", "verdict", "matches"}) so
    every existing caller (task-gateway.py, credit-accountant.py,
    directive_engine.py -- all three invoke this via subprocess + JSON, none
    import the function directly) keeps working with zero changes. Each match
    dict gains one extra "_source_table" key (system_index / wiring_registry /
    knowledge_engine / capability_registry) so a caller CAN tell where a hit
    came from if it wants to, without that key being required by anything
    existing today.

    Merge/dedup: the same real path can legitimately be indexed in more than
    one of these four tables (e.g. system_index and wiring_registry both
    carry a "path" column and do sometimes overlap). Rows are deduped on a
    normalized identity key (path / artifact_path / capability_name,
    lower-cased, with a leading "/opt/veridian/" prefix stripped so an
    absolute and a repo-relative record of the same real file collapse to
    one hit) so "found" reflects distinct real things, not distinct rows.
    system_index is checked first so its exact pre-existing dict shape wins
    the dedup for anything already indexed there -- no behavior change for
    a query that only ever matched system_index before this fix.
    """
    init_db_silent()
    conn = _connect()
    conditions = []
    params = []
    if args.category:
        conditions.append("category = ?")
        params.append(args.category)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = []
    if args.query:
        q = _fts_query(args.query)
        fts_rows = conn.execute(
            "SELECT t.* FROM system_index_fts f JOIN system_index t ON t.rowid = f.rowid WHERE system_index_fts MATCH ?",
            (q,),
        ).fetchall()
        if args.category:
            rows = [r for r in fts_rows if r["category"] == args.category]
        else:
            rows = fts_rows
    elif conditions:
        rows = conn.execute(f"SELECT * FROM system_index {where}", params).fetchall()
    merged = [dict(r, _source_table="system_index") for r in rows]

    # Stage 6: also consult the three other real FTS-backed inventories.
    # These have no "category" column, so args.category never filters them --
    # matches the pre-existing behavior where category filtering was always
    # system_index-specific. Only run when a keyword query was actually given
    # (a bare --category-only call has no keyword to match on here, same as
    # the system_index branch above requiring args.query for its FTS path).
    if args.query:
        q = _fts_query(args.query)
        for table, fts in (
            ("wiring_registry", "wiring_registry_fts"),
            ("knowledge_engine", "knowledge_engine_fts"),
            ("capability_registry", "capability_registry_fts"),
        ):
            try:
                extra_rows = conn.execute(
                    f"SELECT t.* FROM {fts} f JOIN {table} t ON t.rowid = f.rowid WHERE {fts} MATCH ?",
                    (q,),
                ).fetchall()
            except sqlite3.OperationalError:
                # Same fail-open posture as search() above -- a missing/broken
                # FTS table on one source must never block the other three.
                extra_rows = []
            merged.extend(dict(r, _source_table=table) for r in extra_rows)

    def _dedup_key(d):
        ident = d.get("path") or d.get("artifact_path") or d.get("capability_name")
        if not ident:
            # No comparable identity field at all (shouldn't happen for these
            # four schemas, but never silently drop a real hit over it) --
            # fall back to the row's own primary-key-ish id so it still
            # counts as a distinct match rather than being dropped.
            ident = d.get("index_id") or d.get("entity_id") or d.get("artifact_id") or d.get("capability_id") or id(d)
        ident = str(ident).strip().lower()
        if ident.startswith("/opt/veridian/"):
            ident = ident[len("/opt/veridian/"):]
        return ident

    seen = set()
    result = []
    for d in merged:
        key = _dedup_key(d)
        if key in seen:
            continue
        seen.add(key)
        result.append(d)

    conn.close()
    print(json.dumps({
        "found": len(result),
        "verdict": "STOP -- existing mechanism(s) found, review before building" if result else "no existing match found -- safe to proceed, but this is not exhaustive",
        "matches": result,
    }, indent=2, default=str))


VALID_PRE_EXECUTION_FIELDS = {
    "Context Loaded", "Owner Memory Loaded", "Organization Memory Loaded", "End User Memory Loaded",
    "Previous Conversations Loaded", "Previous Commitments Loaded", "Pending Tasks Loaded",
    "Metadata Searched", "YAML Files Searched", "Entity Relationships Searched", "Dependencies Searched",
    "Configuration Searched", "Business Rules Searched", "Documentation Searched", "Existing Software Searched",
    "Existing Scripts Searched", "Existing Automation Searched", "Existing APIs Searched", "Cache Searched",
    "Logs Searched", "Audit Records Searched", "History Searched", "Existing Solution Found",
    "Existing Software Reused", "Task Broken into Steps", "Software Steps Identified", "AI Steps Identified",
    "Human Approval Required", "Execution Plan Created", "Execution Plan Validated", "Dependencies Verified",
    "Permissions Verified", "Configuration Verified", "AI Required", "Software Can Execute Without AI",
    "Previous Commitments Verified", "Duplicate Work Prevented", "Safety Validation Passed",
    "Ready for Execution",
}

VALID_POST_EXECUTION_FIELDS = {
    "Task Completed", "Software Updated", "Script Created/Updated", "Automation Updated", "Workflow Updated",
    "Metadata Updated", "YAML Updated", "Relationships Updated", "Documentation Updated", "Logs Created",
    "Audit Created", "History Updated", "Memory Updated", "Reusable Software Created",
    "AI Work Converted to Software", "Future AI Dependency Reduced", "Testing Completed", "Validation Passed",
    "Owner Approval Required", "Task Successfully Closed",
}


def log_execution(args):
    """Write one Pre-Execution Log or Post-Execution Log row (ai-os/EXECUTION_RULES_AUDIT_2026-07-23.yaml
    Part 40 -- VERIDIAN_EXECUTION_RULES_2026-07-23.md's literal field lists). --fields-file must be a JSON
    object of {field_name: {"status": "YES"|"NO", "evidence": "<real, citable evidence>"}}. Every field name
    is validated against the doc's own literal list for its phase -- typos/invented fields are rejected
    rather than silently accepted, since this table exists specifically to prevent fabricated compliance
    claims. This function does not decide YES/NO itself -- the caller (session_bootstrap.py for PRE,
    postflight_audit_gate.py for POST) must have already run a real check per field."""
    init_db_silent()
    conn = _connect()
    _ensure_execution_log_table(conn)

    with open(args.fields_file, encoding="utf-8") as f:
        fields = json.load(f)

    valid_names = VALID_PRE_EXECUTION_FIELDS if args.phase == "PRE" else VALID_POST_EXECUTION_FIELDS
    unknown = sorted(set(fields) - valid_names)
    if unknown:
        print(json.dumps({"error": f"unknown field name(s) for phase {args.phase} (not in Part 40's literal list)", "unknown": unknown}))
        sys.exit(1)

    bad_status = sorted(k for k, v in fields.items() if v.get("status") not in ("YES", "NO"))
    if bad_status:
        print(json.dumps({"error": "every field's status must be exactly 'YES' or 'NO'", "bad_fields": bad_status}))
        sys.exit(1)

    yes_count = sum(1 for v in fields.values() if v["status"] == "YES")
    no_count = sum(1 for v in fields.values() if v["status"] == "NO")
    elid = _new_id("EXL")
    conn.execute(
        "INSERT INTO execution_log (execution_log_id, ts, phase, work_item_id, software_task_id, source_script, "
        "fields_json, yes_count, no_count, total_fields, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (elid, _now_iso(), args.phase, args.work_item_id, args.software_task_id, args.source_script,
         json.dumps(fields), yes_count, no_count, len(fields),
         json.dumps(json.loads(args.metadata) if args.metadata else {})),
    )
    conn.commit()
    conn.close()
    print(json.dumps({
        "execution_log_id": elid, "phase": args.phase, "software_task_id": args.software_task_id,
        "work_item_id": args.work_item_id, "yes_count": yes_count, "no_count": no_count, "total_fields": len(fields),
    }))


def _ensure_known_fixes_table(conn):
    """Standalone idempotent create, same defensiveness convention as
    _ensure_execution_log_table -- works even if init_db() was never run
    against this DB."""
    conn.execute("""CREATE TABLE IF NOT EXISTS known_fixes (
        signature TEXT PRIMARY KEY,
        fix_action TEXT NOT NULL,
        last_applied TEXT,
        success_count INTEGER NOT NULL DEFAULT 0
    )""")
    conn.commit()


def log_fix(args):
    """INSERT OR REPLACE keyed on signature -- a repeat call for the same
    signature means the watchdog (or an RCA task, per the escalation spec)
    is re-recording the same fix, so success_count increments instead of
    resetting; a brand-new signature starts at success_count=1. fix_action
    is stored as opaque text, looked up by veridian-task-watchdog.py's own
    fixed, whitelisted action registry -- never shell-executed from here or
    there, so this table cannot become an arbitrary-code-execution vector
    even though its contents come from AI-authored RCA output."""
    conn = _connect()
    _ensure_known_fixes_table(conn)
    row = conn.execute("SELECT success_count FROM known_fixes WHERE signature=?", (args.signature,)).fetchone()
    new_count = (row["success_count"] + 1) if row else 1
    conn.execute(
        "INSERT INTO known_fixes (signature, fix_action, last_applied, success_count) VALUES (?,?,?,?) "
        "ON CONFLICT(signature) DO UPDATE SET fix_action=excluded.fix_action, "
        "last_applied=excluded.last_applied, success_count=excluded.success_count",
        (args.signature, args.fix_action, _now_iso(), new_count),
    )
    conn.commit()
    conn.close()
    print(json.dumps({"signature": args.signature, "fix_action": args.fix_action, "success_count": new_count}))


def _ensure_knowledge_engine_table(conn):
    """Standalone idempotent create, same defensiveness convention as
    _ensure_execution_log_table/_ensure_known_fixes_table -- works even if
    init_db() was never run against this DB."""
    conn.execute("""CREATE TABLE IF NOT EXISTS knowledge_engine (
        artifact_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        artifact_path TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        artifact_type TEXT NOT NULL CHECK(artifact_type IN ('canonical','derived')),
        secondary_path TEXT,
        exists_on_disk INTEGER NOT NULL DEFAULT 1,
        purpose TEXT NOT NULL,
        tags TEXT,
        entity_relationships TEXT NOT NULL DEFAULT '[]',
        last_verified_ts TEXT NOT NULL,
        verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED'
            CHECK(verification_status IN ('VERIFIED_MATCH','HASH_DRIFTED','PATH_MISSING','UNVERIFIED')),
        metadata_json TEXT NOT NULL DEFAULT '{}'
    )""")
    conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_engine_fts USING fts5(
        artifact_path, purpose, tags, entity_relationships,
        content='knowledge_engine', content_rowid='rowid'
    )""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS knowledge_engine_ai AFTER INSERT ON knowledge_engine BEGIN
        INSERT INTO knowledge_engine_fts(rowid, artifact_path, purpose, tags, entity_relationships)
        VALUES (new.rowid, new.artifact_path, new.purpose, new.tags, new.entity_relationships);
    END""")
    # 2026-07-24 fix (Phase2 candidate fts5_relevance_tuning): verify-knowledge/
    # annotate-knowledge/add-relationship (below) all UPDATE existing rows in place
    # -- without an AFTER UPDATE sync trigger this reintroduces exactly the same
    # system_index_fts desync bug already root-caused and fixed once (see
    # system_index_au above). AFTER DELETE included too even though nothing here
    # deletes rows yet, so the FTS index cannot silently drift if that changes.
    conn.execute("""CREATE TRIGGER IF NOT EXISTS knowledge_engine_au AFTER UPDATE ON knowledge_engine BEGIN
        INSERT INTO knowledge_engine_fts(knowledge_engine_fts, rowid, artifact_path, purpose, tags, entity_relationships)
        VALUES ('delete', old.rowid, old.artifact_path, old.purpose, old.tags, old.entity_relationships);
        INSERT INTO knowledge_engine_fts(rowid, artifact_path, purpose, tags, entity_relationships)
        VALUES (new.rowid, new.artifact_path, new.purpose, new.tags, new.entity_relationships);
    END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS knowledge_engine_ad AFTER DELETE ON knowledge_engine BEGIN
        INSERT INTO knowledge_engine_fts(knowledge_engine_fts, rowid, artifact_path, purpose, tags, entity_relationships)
        VALUES ('delete', old.rowid, old.artifact_path, old.purpose, old.tags, old.entity_relationships);
    END""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_engine_type ON knowledge_engine(artifact_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_knowledge_engine_path ON knowledge_engine(artifact_path)")
    # Structural duplicate-artifact constraint (task-20260731-074406, real
    # #634-vs-#639 / #641-vs-#629 duplicate-task incidents this session):
    # a bare UNIQUE(content_hash) is wrong -- confirmed against live data
    # that content_hash legitimately repeats (15 rows share the 'n/a'
    # no-hash placeholder; 13 distinct real artifacts share the
    # sha256-of-empty-string hash). (content_hash, artifact_path) is the
    # real key: querying GROUP BY on the live DB found exactly 2 genuine
    # accidental duplicate registrations (same content_hash+artifact_path+
    # artifact_type+secondary_path, minutes apart) and zero false positives
    # against legitimate same-path-different-content re-registrations. Only
    # created here if the 2 known pre-existing duplicates have already been
    # removed by migrate_2026-07-31_dedup_constraints.py -- if a future DB
    # rebuild from an older backup ever reintroduces old duplicate rows,
    # this CREATE UNIQUE INDEX will fail loudly at startup rather than
    # silently skipping; re-run that migration script to fix it (it is
    # idempotent and safe to re-run).
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_knowledge_engine_content_hash_path "
        "ON knowledge_engine(content_hash, artifact_path)"
    )
    conn.commit()
    _migrate_knowledge_engine_fts(conn)
    _migrate_knowledge_engine_utm(conn)


def _migrate_knowledge_engine_utm(conn):
    """UTM metadata consolidation, phase 6 (2026-07-30, campaign
    utm-metadata-consolidation-phase6-2026-07-30): additive ALTER TABLE ADD
    COLUMN for knowledge_engine's five UTM fields, same pattern as
    _migrate_capability_registry_utm (Stage 7 pilot) -- nullable columns
    (SQLite ALTER TABLE ADD COLUMN can't add NOT NULL to a non-empty table
    without a constant DEFAULT), no-op once present (checked via PRAGMA
    table_info), safe to call on every startup.

    Real finding this phase: of 6 bespoke metadata mechanisms evaluated
    (capability_registry's own fields, wiring_registry's fields,
    compliance-tracker Postgres's prompt_versions/orchestraExecutions+
    activityLog/task_capabilities, and this table's own fields),
    knowledge_engine.tags was the ONE genuine UTM-candidate: a free-form JSON
    array of search keywords that duplicates exactly what utm_term already
    exists to hold system-wide (this file's own header docstring literally
    defines utm_term as "comma-separated search keywords") -- a
    format-only difference (JSON array vs comma-joined string), not a real
    second mechanism carrying distinct information. `purpose` (a real one-
    line prose description, not a short categorical label) and
    artifact_type/verification_status/content_hash/entity_relationships (all
    real structural/audit data with CHECK-constrained enums or hash values)
    were evaluated and correctly kept OUT of UTM -- forcing them in would
    lose type safety/queryability for no real simplification gain. Full
    per-mechanism analysis: knowledge_engine artifact_id
    KE-PHASE6-UTM-MERGE-ANALYSIS (register_knowledge'd the same phase this
    migration was written).

    Backfill: utm_term = comma-join of the existing tags JSON array, a
    lossless format conversion (nothing in tags is dropped). `tags` and
    knowledge_engine_fts (which indexes tags, not utm_term) are deliberately
    NOT removed or altered -- still the real FTS5 search surface and the
    --tags convention shared with index-add; nothing that reads tags today
    breaks. utm_source/utm_medium/utm_campaign/utm_content are left
    honestly NULL for historical rows (no reliable per-row "who registered
    this" data exists retroactively, unlike capability_registry's owner-field
    based backfill) -- register_knowledge() populates them for new rows
    going forward when the caller supplies that context, same interface
    convention as log-instruction's --source/--medium/--campaign/--content."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='knowledge_engine'"
    ).fetchone()
    if row is None:
        return  # table doesn't exist yet; the CREATE TABLE IF NOT EXISTS above covers it

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(knowledge_engine)").fetchall()}
    if "utm_source" not in cols:
        for col in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"):
            conn.execute(f"ALTER TABLE knowledge_engine ADD COLUMN {col} TEXT")
        conn.commit()

    backfill_rows = conn.execute(
        "SELECT artifact_id, tags FROM knowledge_engine WHERE utm_term IS NULL"
    ).fetchall()
    for r in backfill_rows:
        tags_raw = r["tags"]
        if not tags_raw:
            continue
        try:
            arr = json.loads(tags_raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(arr, list) or not arr:
            continue
        conn.execute(
            "UPDATE knowledge_engine SET utm_term = ? WHERE artifact_id = ?",
            (",".join(str(t) for t in arr), r["artifact_id"]),
        )
    if backfill_rows:
        conn.commit()


def _migrate_knowledge_engine_fts(conn):
    """Additive migration for a knowledge_engine_fts created before artifact_path
    was indexed (Phase 1 build, 2026-07-23). Detects the old 3-column shape via
    pragma_table_info (fts5 virtual tables support this same as real tables),
    and if found: drops + recreates with the artifact_path column, then rebuilds
    the index from the real knowledge_engine content table using fts5's own
    documented 'rebuild' special command -- never re-derives text by hand."""
    cols = {r["name"] for r in conn.execute("SELECT name FROM pragma_table_info('knowledge_engine_fts')").fetchall()}
    if cols and "artifact_path" not in cols:
        conn.execute("DROP TRIGGER IF EXISTS knowledge_engine_ai")
        conn.execute("DROP TRIGGER IF EXISTS knowledge_engine_au")
        conn.execute("DROP TRIGGER IF EXISTS knowledge_engine_ad")
        conn.execute("DROP TABLE knowledge_engine_fts")
        conn.execute("""CREATE VIRTUAL TABLE knowledge_engine_fts USING fts5(
            artifact_path, purpose, tags, entity_relationships,
            content='knowledge_engine', content_rowid='rowid'
        )""")
        conn.execute("""CREATE TRIGGER knowledge_engine_ai AFTER INSERT ON knowledge_engine BEGIN
            INSERT INTO knowledge_engine_fts(rowid, artifact_path, purpose, tags, entity_relationships)
            VALUES (new.rowid, new.artifact_path, new.purpose, new.tags, new.entity_relationships);
        END""")
        conn.execute("""CREATE TRIGGER knowledge_engine_au AFTER UPDATE ON knowledge_engine BEGIN
            INSERT INTO knowledge_engine_fts(knowledge_engine_fts, rowid, artifact_path, purpose, tags, entity_relationships)
            VALUES ('delete', old.rowid, old.artifact_path, old.purpose, old.tags, old.entity_relationships);
            INSERT INTO knowledge_engine_fts(rowid, artifact_path, purpose, tags, entity_relationships)
            VALUES (new.rowid, new.artifact_path, new.purpose, new.tags, new.entity_relationships);
        END""")
        conn.execute("""CREATE TRIGGER knowledge_engine_ad AFTER DELETE ON knowledge_engine BEGIN
            INSERT INTO knowledge_engine_fts(knowledge_engine_fts, rowid, artifact_path, purpose, tags, entity_relationships)
            VALUES ('delete', old.rowid, old.artifact_path, old.purpose, old.tags, old.entity_relationships);
        END""")
        conn.execute("INSERT INTO knowledge_engine_fts(knowledge_engine_fts) VALUES ('rebuild')")
        conn.commit()


def register_knowledge(args):
    """Add one knowledge_engine pointer row. Real content_hash + exists_on_disk
    are computed from a live read of --path (never guessed) -- a missing file
    is recorded as a real exists_on_disk=0 / verification_status=PATH_MISSING
    row (per KNOWLEDGE_ENGINE_SCHEMA_DESIGN_2026-07-23.yaml's drift-visibility
    requirement), not silently rejected. --relationships is a JSON list of
    {"path": ..., "relationship_type": ..., "evidence": <optional>} objects;
    each is resolved against already-registered rows so related_artifact_id
    is populated whenever the target artifact already has a row (falls back
    to null, never fabricated, if it doesn't yet)."""
    init_db_silent()
    conn = _connect()
    _ensure_knowledge_engine_table(conn)

    aid = _new_id("KE")
    now = _now_iso()
    exists = os.path.isfile(args.path)
    if exists:
        with open(args.path, "rb") as f:
            content_hash = hashlib.sha256(f.read()).hexdigest()
        verification_status = "VERIFIED_MATCH"
    else:
        # No bytes to hash for a referenced-but-missing artifact -- sha256 of
        # the empty string is a documented sentinel, paired with
        # verification_status=PATH_MISSING so this is never confused with a
        # real verified empty file.
        content_hash = hashlib.sha256(b"").hexdigest()
        verification_status = "PATH_MISSING"

    tags_list = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []

    relationships_in = json.loads(args.relationships) if args.relationships else []
    entity_relationships = []
    for rel in relationships_in:
        related_path = rel["path"]
        related_row = conn.execute(
            "SELECT artifact_id FROM knowledge_engine WHERE artifact_path = ? ORDER BY ts DESC LIMIT 1",
            (related_path,),
        ).fetchone()
        entity_relationships.append({
            "related_artifact_id": related_row["artifact_id"] if related_row else None,
            "related_artifact_path": related_path,
            "relationship_type": rel["relationship_type"],
            "evidence": rel.get("evidence"),
        })

    # UTM metadata consolidation, phase 6 (2026-07-30): utm_term is auto-
    # derived from --tags (comma-join of the same list stored in tags) --
    # never asked for separately, since tags already IS the caller's real
    # keyword input and this file's own utm_term convention (comma-separated
    # search keywords) is a lossless format match. The other 4 UTM fields
    # are only ever set from real caller-supplied context (never guessed);
    # left NULL when the caller doesn't know/supply them.
    utm_term = ",".join(tags_list) if tags_list else getattr(args, "utm_term", None)

    try:
        conn.execute(
            "INSERT INTO knowledge_engine (artifact_id, ts, artifact_path, content_hash, artifact_type, "
            "secondary_path, exists_on_disk, purpose, tags, entity_relationships, last_verified_ts, "
            "verification_status, metadata_json, utm_source, utm_medium, utm_campaign, utm_content, utm_term) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (aid, now, args.path, content_hash, args.artifact_type, args.secondary_path,
             1 if exists else 0, args.purpose, json.dumps(tags_list), json.dumps(entity_relationships),
             now, verification_status, json.dumps(json.loads(args.metadata) if args.metadata else {}),
             getattr(args, "utm_source", None), getattr(args, "utm_medium", None),
             getattr(args, "utm_campaign", None), getattr(args, "utm_content", None), utm_term),
        )
    except sqlite3.IntegrityError:
        # Structural duplicate hit (task-20260731-074406): the real fix for
        # #634-vs-#639/#641-vs-#629 -- this used to be an uncaught traceback
        # from the plain INSERT above (nothing enforced content_hash+path
        # uniqueness before idx_knowledge_engine_content_hash_path existed).
        # A clear, structured signal now, not a crash.
        existing = conn.execute(
            "SELECT artifact_id, ts FROM knowledge_engine WHERE content_hash=? AND artifact_path=?",
            (content_hash, args.path),
        ).fetchone()
        conn.close()
        print(json.dumps({
            "error": "duplicate_artifact", "duplicate": True,
            "artifact_path": args.path, "content_hash": content_hash,
            "existing_artifact_id": existing["artifact_id"] if existing else None,
            "existing_ts": existing["ts"] if existing else None,
        }, indent=2, default=str))
        sys.exit(1)
    conn.commit()
    conn.close()
    print(json.dumps({
        "artifact_id": aid, "artifact_path": args.path, "artifact_type": args.artifact_type,
        "exists_on_disk": exists, "verification_status": verification_status,
        "entity_relationships": entity_relationships,
    }, indent=2, default=str))


# Real fix (UMR-20260806-141250-1ceb): shared row-count cap for the two FTS
# queries named in proposal 86 / UMR-20260806-135902-cf13's root-cause
# evidence (this function's own knowledge_engine_fts query below, and
# lookup_entity()'s wiring_registry_fts query further down this file) --
# both previously had no LIMIT and returned every matching row, unbounded,
# straight into a caller (plan_generator.check_reuse_before_dispatch(), via
# resource_governor.submit()) that embeds the full result verbatim into
# umr_tasks.metadata_json on every dispatch. 50 is a deliberate choice, not
# an arbitrary one: FTS5's `ORDER BY rank` already sorts most-relevant-first,
# so the top 50 keeps far more candidates than a human/agent reviewer could
# usefully scan as "possible duplicates to check" (that job realistically
# tops out around 5-10 before it stops being a useful signal) while still
# being generous enough that a real near-duplicate is essentially never
# pushed out of a top-50 rank-ordered slice by noise. No existing internal
# LIMIT precedent to match: lookup_capability()'s own analogous
# capability_registry_fts query was checked and found equally unbounded as
# of this fix (out of this UMR's approved scope to also change).
WIRING_LOOKUP_MATCH_LIMIT = 50
KNOWLEDGE_QUERY_MATCH_LIMIT = 50


def query_knowledge(args):
    """FTS5 search over knowledge_engine (purpose/tags/entity_relationships),
    same MATCH-via-_fts_query + rowid-join pattern as search()'s other trees.
    --tag filters to rows whose tags list contains that exact tag, same
    Python-side membership check search() already uses for system_index.tags
    (JSON1 not confirmed present on this host)."""
    init_db_silent()
    conn = _connect()
    _ensure_knowledge_engine_table(conn)
    q = _fts_query(args.query)
    try:
        # Real fix (UMR-20260806-141250-1ceb, proposal 86): same unbounded-
        # FTS-result issue as lookup_entity()'s wiring_registry_fts query
        # above (root-caused live in UMR-20260806-135902-cf13 -- this
        # query's own result feeds check_reuse_before_dispatch()'s
        # result["knowledge"], embedded into metadata_json.reuse_check_result
        # on every dispatch same as the wiring match list was). Same bound,
        # same reasoning -- see WIRING_LOOKUP_MATCH_LIMIT's own comment.
        rows = conn.execute(
            "SELECT t.* FROM knowledge_engine_fts f JOIN knowledge_engine t ON t.rowid = f.rowid "
            "WHERE knowledge_engine_fts MATCH ? ORDER BY rank LIMIT ?",
            (q, KNOWLEDGE_QUERY_MATCH_LIMIT),
        ).fetchall()
        result = [dict(r) for r in rows]
    except sqlite3.OperationalError as e:
        result = []
    if getattr(args, "tag", None):
        result = [r for r in result if args.tag in json.loads(r["tags"] or "[]")]
    conn.close()
    print(json.dumps({"found": len(result), "matches": result}, indent=2, default=str))


def verify_knowledge(args):
    """Phase2 candidate auto_update_on_task_completion: re-verify the LATEST
    knowledge_engine row for each --path against a live read of the real file
    (never a second guess) -- UPDATE in place (never INSERT), so a re-verify
    can never create a duplicate row for the same artifact_path. Called both
    ad-hoc and from task-gateway.py's close subcommand for every knowledge_engine
    artifact_path touched by the just-closed task's own git diff."""
    init_db_silent()
    conn = _connect()
    _ensure_knowledge_engine_table(conn)
    now = _now_iso()
    results = []
    for path in args.path:
        row = conn.execute(
            "SELECT artifact_id, content_hash FROM knowledge_engine WHERE artifact_path = ? ORDER BY ts DESC LIMIT 1",
            (path,),
        ).fetchone()
        if not row:
            results.append({"path": path, "found": False})
            continue
        exists = os.path.isfile(path)
        if exists:
            with open(path, "rb") as f:
                new_hash = hashlib.sha256(f.read()).hexdigest()
            status = "VERIFIED_MATCH" if new_hash == row["content_hash"] else "HASH_DRIFTED"
        else:
            new_hash = row["content_hash"]
            status = "PATH_MISSING"
        conn.execute(
            "UPDATE knowledge_engine SET verification_status=?, last_verified_ts=?, exists_on_disk=?, content_hash=? WHERE artifact_id=?",
            (status, now, 1 if exists else 0, new_hash, row["artifact_id"]),
        )
        results.append({
            "path": path, "found": True, "artifact_id": row["artifact_id"],
            "previous_hash": row["content_hash"], "new_hash": new_hash,
            "hash_changed": new_hash != row["content_hash"], "verification_status": status,
        })
    conn.commit()
    conn.close()
    print(json.dumps({"verified_count": len(results), "results": results}, indent=2, default=str))


def annotate_knowledge(args):
    """Appends a dated correction note to the LATEST row's metadata_json for
    --path, without touching content_hash/verification_status (those stay
    exactly what a live read of the real file says -- an annotation records a
    judgment call about the row, e.g. 'citing text corrected instead of
    authoring a phantom file', it never fabricates verification evidence).
    Phase2 candidate fill_the_3_real_drift_gaps: the mechanism this uses to
    make a PATH_MISSING row's resolution visible without inventing a file."""
    init_db_silent()
    conn = _connect()
    _ensure_knowledge_engine_table(conn)
    row = conn.execute(
        "SELECT artifact_id, metadata_json FROM knowledge_engine WHERE artifact_path = ? ORDER BY ts DESC LIMIT 1",
        (args.path,),
    ).fetchone()
    if not row:
        print(json.dumps({"error": "no knowledge_engine row found for that path", "path": args.path}))
        sys.exit(1)
    metadata = json.loads(row["metadata_json"] or "{}")
    corrections = metadata.setdefault("corrections", [])
    corrections.append({"ts": _now_iso(), "note": args.note})
    conn.execute(
        "UPDATE knowledge_engine SET metadata_json=? WHERE artifact_id=?",
        (json.dumps(metadata), row["artifact_id"]),
    )
    conn.commit()
    conn.close()
    print(json.dumps({"artifact_id": row["artifact_id"], "path": args.path, "metadata_json": metadata}, indent=2, default=str))


def add_relationship(args):
    """Appends one real edge to the LATEST row's entity_relationships for
    --path, resolved against already-registered rows the same way
    register_knowledge's own --relationships resolution works (never
    fabricates a related_artifact_id). Lets item-2 (entity-relationship layer)
    populate edges for rows that already existed before this ability existed
    -- register_knowledge only accepts relationships at insert time."""
    init_db_silent()
    conn = _connect()
    _ensure_knowledge_engine_table(conn)
    row = conn.execute(
        "SELECT artifact_id, entity_relationships FROM knowledge_engine WHERE artifact_path = ? ORDER BY ts DESC LIMIT 1",
        (args.path,),
    ).fetchone()
    if not row:
        print(json.dumps({"error": "no knowledge_engine row found for that path", "path": args.path}))
        sys.exit(1)
    related_row = conn.execute(
        "SELECT artifact_id FROM knowledge_engine WHERE artifact_path = ? ORDER BY ts DESC LIMIT 1",
        (args.related_path,),
    ).fetchone()
    rels = json.loads(row["entity_relationships"] or "[]")
    rels.append({
        "related_artifact_id": related_row["artifact_id"] if related_row else None,
        "related_artifact_path": args.related_path,
        "relationship_type": args.relationship_type,
        "evidence": args.evidence,
    })
    conn.execute(
        "UPDATE knowledge_engine SET entity_relationships=? WHERE artifact_id=?",
        (json.dumps(rels), row["artifact_id"]),
    )
    conn.commit()
    conn.close()
    print(json.dumps({"artifact_id": row["artifact_id"], "path": args.path, "entity_relationships": rels}, indent=2, default=str))


def add_tag(args):
    """Merges one tag into the LATEST row's tags list for --path (no-op if
    already present). Used by knowledge_registry_multisource.py to retag the
    9 Phase-1 rows with source:SERVER without re-inserting them (which would
    duplicate, since register_knowledge is insert-only) and without hand-SQL."""
    init_db_silent()
    conn = _connect()
    _ensure_knowledge_engine_table(conn)
    row = conn.execute(
        "SELECT artifact_id, tags FROM knowledge_engine WHERE artifact_path = ? ORDER BY ts DESC LIMIT 1",
        (args.path,),
    ).fetchone()
    if not row:
        print(json.dumps({"error": "no knowledge_engine row found for that path", "path": args.path}))
        sys.exit(1)
    tags = json.loads(row["tags"] or "[]")
    if args.tag not in tags:
        tags.append(args.tag)
    conn.execute("UPDATE knowledge_engine SET tags=? WHERE artifact_id=?", (json.dumps(tags), row["artifact_id"]))
    conn.commit()
    conn.close()
    print(json.dumps({"artifact_id": row["artifact_id"], "path": args.path, "tags": tags}, indent=2, default=str))


def upsert_knowledge_fragment(args):
    """Idempotent register-or-update for a knowledge_engine row representing a
    FRAGMENT of a larger file (e.g. one MASTER_INDEX.yaml registries: entry)
    rather than a whole real file on disk -- register_knowledge/verify_knowledge
    both hash bytes read from --path, which is wrong for a sub-document that
    has no file of its own to read. Here --content is the caller-computed
    canonical text for just that fragment (e.g. json.dumps(entry,
    sort_keys=True)); its sha256 IS the content_hash, and --path is a stable
    virtual identifier (e.g. 'ai-os/MASTER_INDEX.yaml#registries.<id>'), not a
    real filesystem path. Same SELECT-latest-then-INSERT-or-UPDATE shape
    verify_knowledge already uses (never a duplicate row for the same
    artifact_path), so a recurring sync (cron or task-gateway.py close) is
    always idempotent: first run INSERTs (VERIFIED_MATCH, freshly captured),
    every later run UPDATEs in place and reports HASH_DRIFTED only for the one
    run where the fragment's real content changed since the previous sync --
    Phase 5 (metadata_knowledge_consolidation)'s enforced sync mechanism
    between MASTER_INDEX.yaml's registries: section and this table."""
    init_db_silent()
    conn = _connect()
    _ensure_knowledge_engine_table(conn)
    now = _now_iso()
    new_hash = hashlib.sha256(args.content.encode("utf-8")).hexdigest()
    tags_list = [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []

    row = conn.execute(
        "SELECT artifact_id, content_hash FROM knowledge_engine WHERE artifact_path = ? ORDER BY ts DESC LIMIT 1",
        (args.path,),
    ).fetchone()

    if row:
        status = "VERIFIED_MATCH" if new_hash == row["content_hash"] else "HASH_DRIFTED"
        conn.execute(
            "UPDATE knowledge_engine SET content_hash=?, secondary_path=?, purpose=?, tags=?, "
            "last_verified_ts=?, verification_status=?, metadata_json=? WHERE artifact_id=?",
            (new_hash, args.secondary_path, args.purpose, json.dumps(tags_list), now, status,
             json.dumps(json.loads(args.metadata) if args.metadata else {}), row["artifact_id"]),
        )
        artifact_id = row["artifact_id"]
        created = False
    else:
        artifact_id = _new_id("KE")
        status = "VERIFIED_MATCH"
        try:
            conn.execute(
                "INSERT INTO knowledge_engine (artifact_id, ts, artifact_path, content_hash, artifact_type, "
                "secondary_path, exists_on_disk, purpose, tags, entity_relationships, last_verified_ts, "
                "verification_status, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (artifact_id, now, args.path, new_hash, args.artifact_type, args.secondary_path, 1,
                 args.purpose, json.dumps(tags_list), "[]", now, status,
                 json.dumps(json.loads(args.metadata) if args.metadata else {})),
            )
        except sqlite3.IntegrityError:
            # The SELECT-then-INSERT above isn't atomic -- two concurrent
            # callers can both see "no row" and both reach this INSERT
            # (task-20260731-074406). idx_knowledge_engine_content_hash_path
            # now catches that race structurally instead of racing to a
            # silent duplicate row.
            existing = conn.execute(
                "SELECT artifact_id, ts FROM knowledge_engine WHERE content_hash=? AND artifact_path=?",
                (new_hash, args.path),
            ).fetchone()
            conn.close()
            print(json.dumps({
                "error": "duplicate_artifact", "duplicate": True,
                "artifact_path": args.path, "content_hash": new_hash,
                "existing_artifact_id": existing["artifact_id"] if existing else None,
                "existing_ts": existing["ts"] if existing else None,
            }, indent=2, default=str))
            sys.exit(1)
        created = True

    conn.commit()
    conn.close()
    print(json.dumps({
        "artifact_id": artifact_id, "path": args.path, "created": created,
        "verification_status": status,
    }, indent=2, default=str))


def list_knowledge(args):
    """Lists knowledge_engine rows (newest first), optionally filtered to one
    --tag (exact membership in the row's tags list, same Python-side check
    query_knowledge's own --tag filter uses). No FTS query required -- used
    for enumeration/orphan-detection (e.g. sync_master_index_registries.py
    diffing 'every row tagged type:master_index_registry_entry' against
    MASTER_INDEX.yaml's current registries: ids), the same role list_capabilities
    plays for capability_registry."""
    init_db_silent()
    conn = _connect()
    _ensure_knowledge_engine_table(conn)
    rows = conn.execute("SELECT * FROM knowledge_engine ORDER BY ts DESC").fetchall()
    conn.close()
    matches = [dict(r) for r in rows]
    if getattr(args, "tag", None):
        matches = [r for r in matches if args.tag in json.loads(r["tags"] or "[]")]
    print(json.dumps({"count": len(matches), "matches": matches}, indent=2, default=str))


def _ensure_task_claims_table(conn):
    """Structural duplicate-TASK lease table (task-20260731-074406), separate
    from knowledge_engine's ARTIFACT constraint above -- work_items has no
    single stable per-task column (software_task_id/ai_task_id each repeat
    multiple times per real task, one row per lifecycle event), so a
    lease/claim concept needed its own table rather than a constraint bolted
    onto work_items. task_key is a stable, title-derived identity (see
    task-gateway.py's _slugify_title -- the SAME algorithm veridian-task.py's
    cmd_create uses for its task_id slug, minus the timestamp prefix), NOT
    task_id itself (task_id can never collide by construction, since it's
    timestamp-prefixed -- that's exactly why two concurrent dispatches of
    "the same" task get two different task_ids and nothing before this
    caught them). UNIQUE(task_key) via a real index (not a Python-side
    SELECT-then-INSERT check) so a concurrent duplicate claim raises
    sqlite3.IntegrityError instead of racing past a check."""
    conn.execute("""CREATE TABLE IF NOT EXISTS task_claims (
        claim_id TEXT PRIMARY KEY,
        task_key TEXT NOT NULL,
        ts TEXT NOT NULL,
        task_id TEXT,
        title TEXT,
        utm_source TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_task_claims_task_key ON task_claims(task_key)")
    conn.commit()


def claim_task_key(args):
    """Atomic lease insert -- the real fix behind task-20260731-074406's real
    #634-vs-#639 / #641-vs-#629 duplicate-dispatch incidents. Called by
    task-gateway.py cmd_start immediately before veridian-task.py create
    actually spends real resources (worktree/branch/systemd unit) on a new
    task. Catches sqlite3.IntegrityError from the UNIQUE(task_key) index
    (see _ensure_task_claims_table) and returns a clear, structured
    duplicate signal instead of an uncaught traceback -- callers treat
    claimed=false as a hard block, not a crash."""
    init_db_silent()
    conn = _connect()
    _ensure_task_claims_table(conn)
    cid = _new_id("CLM")
    now = _now_iso()
    try:
        conn.execute(
            "INSERT INTO task_claims (claim_id, task_key, ts, task_id, title, utm_source, metadata_json) "
            "VALUES (?,?,?,?,?,?,?)",
            (cid, args.task_key, now, getattr(args, "task_id", None), getattr(args, "title", None),
             getattr(args, "source", None), "{}"),
        )
        conn.commit()
        conn.close()
        print(json.dumps({"claimed": True, "claim_id": cid, "task_key": args.task_key}, indent=2, default=str))
    except sqlite3.IntegrityError:
        existing = conn.execute(
            "SELECT claim_id, ts, task_id, title FROM task_claims WHERE task_key=?",
            (args.task_key,),
        ).fetchone()
        conn.close()
        print(json.dumps({
            "claimed": False, "task_key": args.task_key, "error": "duplicate_task_key",
            "existing_claim_id": existing["claim_id"] if existing else None,
            "existing_task_id": existing["task_id"] if existing else None,
            "existing_title": existing["title"] if existing else None,
            "existing_ts": existing["ts"] if existing else None,
        }, indent=2, default=str))


def check_task_key(args):
    """Read-only lookup (no _write_lock needed -- same convention as
    search/query-knowledge/list-knowledge in main()'s dispatch table below).
    Used by task-gateway.py cmd_submit as an early advisory signal before a
    real title/task_id exists to actually claim (submit only has --text, not
    --title, so it cannot itself make the real atomic claim -- that happens
    in cmd_start once a title is known)."""
    init_db_silent()
    conn = _connect()
    _ensure_task_claims_table(conn)
    row = conn.execute(
        "SELECT claim_id, ts, task_id, title FROM task_claims WHERE task_key=?",
        (args.task_key,),
    ).fetchone()
    conn.close()
    print(json.dumps({
        "task_key": args.task_key, "already_claimed": row is not None,
        "existing_task_id": row["task_id"] if row else None,
        "existing_title": row["title"] if row else None,
        "existing_ts": row["ts"] if row else None,
    }, indent=2, default=str))


def cmd_query_ocid_canonical(args):
    """Real, read-only CLI lookup over ocid_canonical_registry
    (UMR-20260805-032326-becc) -- no _write_lock needed, same convention as
    check_task_key/lookup-entity above. --ocid-number for a single real row,
    omitted for the whole real roster."""
    init_db_silent()
    conn = _connect()
    _ensure_ocid_canonical_registry_table(conn)
    rows = query_ocid_canonical_registry(conn, ocid_number=args.ocid_number)
    conn.close()
    print(json.dumps(rows, indent=2, default=str))


def cmd_resolve_ocid_canonical(args):
    """OCID Master Standard v6 Phase 1 (UMR-20260805-042152-e559) CLI entry
    point over resolve_ocid_canonical(). Read-only unless --apply is passed,
    same convention as cmd_reconcile_umr_status below -- resolving/searching
    never mutates the real registry by itself; --apply performs the real
    upsert_ocid_canonical_registry() write under _write_lock().

    UMR-20260805-092408-4f97: --apply now also writes `audit_raw_output`
    (this real run's own `evidence` dict, verbatim -- the exact real
    zero-AI-judgment mechanical search output, not a narrated summary), so
    every real write through this CLI command structurally earns
    not_applicable_confirmed (when honestly not_found) via the
    ocid_canonical_registry_completion_ai/_au triggers, rather than that
    marker ever depending on a caller's separately hand-typed claim."""
    init_db_silent()
    conn = _connect()
    _ensure_ocid_canonical_registry_table(conn)
    result = resolve_ocid_canonical(args.ocid_number, conn)
    if args.apply:
        with _write_lock():
            upsert_ocid_canonical_registry(
                conn, result["ocid_number"],
                canonical_umr_id=result["canonical_umr_id"], status=result["status"],
                all_umr_ids=result["all_umr_ids"], evidence=result["evidence"],
                pr_number=result["pr_number"], pr_repo=result["pr_repo"],
                duplicate_reason=result["duplicate_reason"], not_found=result["not_found"],
                audit_raw_output=result["evidence"],
            )
            conn.commit()
    conn.close()
    print(json.dumps(result, indent=2, default=str))


def cmd_reconcile_umr_status(args):
    """OCID Master Standard v6 Phase 1 (UMR-20260805-042152-e559) CLI entry
    point over reconcile_umr_status_against_pr(). Real, read-only by default
    (reports the proposed correction, never silently applies it) -- pass
    --apply to actually call the existing real update_umr_task() under
    _write_lock(), matching how UMR-20260805-024319-b1e6's earlier real
    correction was done."""
    init_db_silent()
    conn = _connect()
    _ensure_umr_table(conn)
    _ensure_ocid_master_standard_audit_log_table(conn)
    result = reconcile_umr_status_against_pr(conn, args.umr_id)
    if args.apply and result["is_stale"]:
        with _write_lock():
            update_umr_task(
                conn, args.umr_id,
                status=result["proposed_status"],
                ts_completed=result["proposed_ts_completed"],
            )
            conn.commit()
    conn.close()
    print(json.dumps(result, indent=2, default=str))


def cmd_certify_pr_merge(args):
    """OCID Master Standard v6 Phase 1 (UMR-20260805-042152-e559) CLI entry
    point over refuse_certification_if_merged_without_required_checks(), via
    the real caller-side apply_certification_verdict() wrapper (records a
    real 'certification_refused' audit event on refusal). --pr-record-json
    is a path to a real JSON file matching pr_merge_record's own documented
    shape -- this command performs no live GitHub API calls itself."""
    with open(args.pr_record_json) as f:
        pr_merge_record = json.load(f)
    init_db_silent()
    conn = _connect()
    _ensure_ocid_master_standard_audit_log_table(conn)
    verdict, reason = apply_certification_verdict(conn, pr_merge_record)
    conn.close()
    print(json.dumps({"verdict": verdict, "reason": reason}, indent=2, default=str))
    if not verdict:
        sys.exit(1)


def _ensure_capability_registry_table(conn):
    """Standalone idempotent create, same defensiveness convention as
    _ensure_knowledge_engine_table -- works even if init_db() was never run
    against this DB."""
    conn.execute("""CREATE TABLE IF NOT EXISTS capability_registry (
        capability_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        capability_name TEXT NOT NULL,
        inputs TEXT NOT NULL DEFAULT '[]',
        business_rules TEXT NOT NULL DEFAULT '[]',
        workflow TEXT,
        automation TEXT,
        documents TEXT,
        reports TEXT,
        apis TEXT NOT NULL DEFAULT '[]',
        ui_screens TEXT,
        permissions TEXT NOT NULL,
        ai_required INTEGER NOT NULL DEFAULT 0,
        confidence REAL NOT NULL DEFAULT 0.0,
        version TEXT NOT NULL DEFAULT 'unversioned',
        owner TEXT NOT NULL,
        last_verified_ts TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        utm_source TEXT NOT NULL DEFAULT 'superboss-register.py',
        utm_medium TEXT NOT NULL DEFAULT 'register-capability',
        utm_campaign TEXT,
        utm_content TEXT,
        utm_term TEXT,
        -- 2026-08-07 (UMR-20260807-035145-aa45, amendment to UMR-20260806-171945-5767):
        -- same additive vector_json/vector_updated_ts pair as wiring_registry's own
        -- 2026-08-07 addition (see that CREATE TABLE's comment for the full rationale) --
        -- built from capability_name/apis/workflow/owner/business_rules text by
        -- vector_similarity.vector_for_capability_row(), kept current automatically by
        -- register_capability() below on every real insert/update.
        vector_json TEXT,
        vector_updated_ts TEXT
    )""")
    conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS capability_registry_fts USING fts5(
        capability_name, owner, apis, ui_screens, workflow, utm_source, utm_campaign, utm_term,
        content='capability_registry', content_rowid='rowid'
    )""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS capability_registry_ai AFTER INSERT ON capability_registry BEGIN
        INSERT INTO capability_registry_fts(rowid, capability_name, owner, apis, ui_screens, workflow, utm_source, utm_campaign, utm_term)
        VALUES (new.rowid, new.capability_name, new.owner, new.apis, new.ui_screens, new.workflow, new.utm_source, new.utm_campaign, new.utm_term);
    END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS capability_registry_au AFTER UPDATE ON capability_registry BEGIN
        INSERT INTO capability_registry_fts(capability_registry_fts, rowid, capability_name, owner, apis, ui_screens, workflow, utm_source, utm_campaign, utm_term)
        VALUES ('delete', old.rowid, old.capability_name, old.owner, old.apis, old.ui_screens, old.workflow, old.utm_source, old.utm_campaign, old.utm_term);
        INSERT INTO capability_registry_fts(rowid, capability_name, owner, apis, ui_screens, workflow, utm_source, utm_campaign, utm_term)
        VALUES (new.rowid, new.capability_name, new.owner, new.apis, new.ui_screens, new.workflow, new.utm_source, new.utm_campaign, new.utm_term);
    END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS capability_registry_ad AFTER DELETE ON capability_registry BEGIN
        INSERT INTO capability_registry_fts(capability_registry_fts, rowid, capability_name, owner, apis, ui_screens, workflow, utm_source, utm_campaign, utm_term)
        VALUES ('delete', old.rowid, old.capability_name, old.owner, old.apis, old.ui_screens, old.workflow, old.utm_source, old.utm_campaign, old.utm_term);
    END""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_capability_registry_name ON capability_registry(capability_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_capability_registry_ai_required ON capability_registry(ai_required)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_capability_registry_campaign ON capability_registry(utm_campaign)")
    conn.commit()


def _ensure_route_replay_table(conn):
    """Standalone idempotent create, same defensiveness convention as
    _ensure_knowledge_engine_table/_ensure_capability_registry_table -- works
    even if init_db() was never run against this DB."""
    conn.execute("""CREATE TABLE IF NOT EXISTS route_replay (
        replay_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        route_id TEXT NOT NULL,
        capability_name TEXT NOT NULL,
        event_type TEXT NOT NULL CHECK(event_type IN ('capture','replay')),
        request_payload TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        response_payload TEXT NOT NULL,
        response_hash TEXT NOT NULL,
        baseline_replay_id TEXT,
        diff_result TEXT CHECK(diff_result IN ('match','diff')),
        diff_detail TEXT,
        artifact_path TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY (baseline_replay_id) REFERENCES route_replay(replay_id)
    )""")
    conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS route_replay_fts USING fts5(
        route_id, capability_name, diff_detail,
        content='route_replay', content_rowid='rowid'
    )""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS route_replay_ai AFTER INSERT ON route_replay BEGIN
        INSERT INTO route_replay_fts(rowid, route_id, capability_name, diff_detail)
        VALUES (new.rowid, new.route_id, new.capability_name, new.diff_detail);
    END""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_route_replay_route_id ON route_replay(route_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_route_replay_event_type ON route_replay(event_type)")
    conn.commit()


def _latest_capture_for_route(conn, route_id):
    return conn.execute(
        "SELECT * FROM route_replay WHERE route_id = ? AND event_type = 'capture' ORDER BY ts DESC LIMIT 1",
        (route_id,),
    ).fetchone()


def capture_replay(args):
    """Insert one route_replay capture row: the real request payload (call
    args) + real response payload (return value) for one live execution of
    route_id's dispatch-target function, exactly as it was actually called --
    never a synthesized example. request_hash/response_hash are sha256 over
    the payload bytes, same drift-detection convention as knowledge_engine's
    content_hash. Insert-only (mirrors instructions/actions, not
    capability_registry's upsert-in-place) so a route's full capture/replay
    history stays queryable, not just its latest state. Sets this route's
    ai-os/ROUTE_REGISTRY_SCHEMA_2026-07-24.yaml replay_status to 'captured'
    is the caller's job (targeted yaml surgery, same as generate_route_tests.py
    does for test_status) -- this command only owns the sqlite side."""
    init_db_silent()
    conn = _connect()
    _ensure_route_replay_table(conn)

    rid = _new_id("RPL")
    now = _now_iso()
    request_hash = hashlib.sha256(args.request_payload.encode("utf-8")).hexdigest()
    response_hash = hashlib.sha256(args.response_payload.encode("utf-8")).hexdigest()

    conn.execute(
        "INSERT INTO route_replay (replay_id, ts, route_id, capability_name, event_type, request_payload, "
        "request_hash, response_payload, response_hash, baseline_replay_id, diff_result, diff_detail, "
        "artifact_path, metadata_json) VALUES (?,?,?,?,'capture',?,?,?,?,NULL,NULL,NULL,?,?)",
        (rid, now, args.route_id, args.capability_name, args.request_payload, request_hash,
         args.response_payload, response_hash, args.artifact_path,
         json.dumps(json.loads(args.metadata) if args.metadata else {})),
    )
    conn.commit()
    conn.close()
    print(json.dumps({
        "replay_id": rid, "route_id": args.route_id, "event_type": "capture",
        "request_hash": request_hash, "response_hash": response_hash,
    }, indent=2, default=str))


def run_replay(args):
    """Re-executes a route: --response-payload is the REAL freshly re-computed
    response (the caller already re-ran the dispatch-target function against
    current code -- this command does not execute any TypeScript itself, it
    only records + diffs). Diffs it against the latest 'capture' row's
    response_payload for this route_id (byte-for-byte, via response_hash --
    two independently-serialized-but-identical JSON payloads would still hash
    equal since json.dumps(..., sort_keys=True) is used on both ends, see
    generate_route_replays.py), inserts a 'replay' event row recording the
    verdict, and never mutates the original capture row (insert-only,
    auditable history). Errors if no capture row exists yet for this
    route_id -- there is nothing to diff against without one."""
    init_db_silent()
    conn = _connect()
    _ensure_route_replay_table(conn)

    baseline = _latest_capture_for_route(conn, args.route_id)
    if baseline is None:
        print(json.dumps({"error": f"no capture row found for route_id={args.route_id} -- run capture-replay first"}))
        conn.close()
        sys.exit(1)

    rid = _new_id("RPL")
    now = _now_iso()
    request_hash = hashlib.sha256(args.request_payload.encode("utf-8")).hexdigest()
    response_hash = hashlib.sha256(args.response_payload.encode("utf-8")).hexdigest()
    diff_result = "match" if response_hash == baseline["response_hash"] else "diff"
    diff_detail = args.diff_detail if args.diff_detail else (
        "response_hash matches captured baseline exactly" if diff_result == "match"
        else f"response_hash differs from captured baseline (baseline={baseline['response_hash']}, replay={response_hash})"
    )

    conn.execute(
        "INSERT INTO route_replay (replay_id, ts, route_id, capability_name, event_type, request_payload, "
        "request_hash, response_payload, response_hash, baseline_replay_id, diff_result, diff_detail, "
        "artifact_path, metadata_json) VALUES (?,?,?,?,'replay',?,?,?,?,?,?,?,?,?)",
        (rid, now, args.route_id, args.capability_name, args.request_payload, request_hash,
         args.response_payload, response_hash, baseline["replay_id"], diff_result, diff_detail,
         args.artifact_path, json.dumps(json.loads(args.metadata) if args.metadata else {})),
    )
    conn.commit()
    conn.close()
    print(json.dumps({
        "replay_id": rid, "route_id": args.route_id, "event_type": "replay",
        "baseline_replay_id": baseline["replay_id"], "diff_result": diff_result, "diff_detail": diff_detail,
    }, indent=2, default=str))


def _route_replay_row_to_dict(row):
    d = dict(row)
    d["metadata_json"] = json.loads(d["metadata_json"]) if d.get("metadata_json") else {}
    return d


def list_replays(args):
    """Lists route_replay rows, optionally filtered to one --route-id, newest
    first -- used for evidence/row-count verification, same role
    list_capabilities plays for capability_registry."""
    init_db_silent()
    conn = _connect()
    _ensure_route_replay_table(conn)
    if getattr(args, "route_id", None):
        rows = conn.execute(
            "SELECT * FROM route_replay WHERE route_id = ? ORDER BY ts DESC", (args.route_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM route_replay ORDER BY ts DESC").fetchall()
    conn.close()
    matches = [_route_replay_row_to_dict(r) for r in rows]
    print(json.dumps({"count": len(matches), "replays": matches}, indent=2, default=str))


REQUIRED_CAPABILITY_FIELDS = {
    "capability_name", "inputs", "business_rules", "apis", "permissions",
    "ai_required", "confidence", "version", "owner",
}
NULLABLE_JSON_LIST_CAPABILITY_FIELDS = ("documents", "reports", "ui_screens")


def register_capability(args):
    """Insert or re-register one capability_registry row from --record-file (a
    JSON object following ai-os/CAPABILITY_REGISTRY_SCHEMA_2026-07-24.yaml's
    capability_record_schema field-for-field -- not a redesigned shape).
    capability_name is UNIQUE -- re-running this for an already-registered name
    UPDATEs it in place (refreshes ts/last_verified_ts/every field), same
    living-catalog convention as index_add's ON CONFLICT(path) DO UPDATE,
    since a capability's real business_rules/apis/confidence can legitimately
    change between registrations and this must stay current, not write-once.

    Stage 7 pilot (2026-07-29, task-20260729, VERIDIAN_CONSOLIDATED_COMPLETION,
    Option B system-wide UTM adoption): every insert/update also populates the
    five UTM fields via _derive_capability_utm_fields() -- real values derived
    from this same record (utm_campaign from metadata.registered_by_phase or a
    task-id-shaped owner; utm_content from workflow/automation/documents/apis;
    utm_term from capability_name), not placeholders, so a fresh registration
    never needs the one-time backfill migration to catch up."""
    init_db_silent()
    conn = _connect()
    _ensure_capability_registry_table(conn)

    with open(args.record_file, encoding="utf-8") as f:
        record = json.load(f)

    missing = sorted(REQUIRED_CAPABILITY_FIELDS - set(record))
    if missing:
        print(json.dumps({"error": "record-file missing required capability_record_schema field(s)", "missing": missing}))
        sys.exit(1)

    utm = _derive_capability_utm_fields(record)

    cid = _new_id("CAP")
    now = _now_iso()
    conn.execute(
        "INSERT INTO capability_registry (capability_id, ts, capability_name, inputs, business_rules, "
        "workflow, automation, documents, reports, apis, ui_screens, permissions, ai_required, confidence, "
        "version, owner, last_verified_ts, metadata_json, utm_source, utm_medium, utm_campaign, utm_content, utm_term) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(capability_name) DO UPDATE SET ts=excluded.ts, inputs=excluded.inputs, "
        "business_rules=excluded.business_rules, workflow=excluded.workflow, automation=excluded.automation, "
        "documents=excluded.documents, reports=excluded.reports, apis=excluded.apis, "
        "ui_screens=excluded.ui_screens, permissions=excluded.permissions, ai_required=excluded.ai_required, "
        "confidence=excluded.confidence, version=excluded.version, owner=excluded.owner, "
        "last_verified_ts=excluded.last_verified_ts, metadata_json=excluded.metadata_json, "
        "utm_source=excluded.utm_source, utm_medium=excluded.utm_medium, utm_campaign=excluded.utm_campaign, "
        "utm_content=excluded.utm_content, utm_term=excluded.utm_term",
        (
            cid, now, record["capability_name"], json.dumps(record["inputs"]), json.dumps(record["business_rules"]),
            record.get("workflow"), record.get("automation"),
            json.dumps(record["documents"]) if record.get("documents") is not None else None,
            json.dumps(record["reports"]) if record.get("reports") is not None else None,
            json.dumps(record["apis"]),
            json.dumps(record["ui_screens"]) if record.get("ui_screens") is not None else None,
            record["permissions"], 1 if record["ai_required"] else 0, float(record["confidence"]),
            record["version"], record["owner"], now,
            json.dumps(record.get("metadata", {})),
            utm["utm_source"], utm["utm_medium"], utm["utm_campaign"], utm["utm_content"], utm["utm_term"],
        ),
    )
    # 2026-08-07 (UMR-20260807-035145-aa45): same real on-write update mechanism
    # as register_entity_row()'s vector_json population above, for
    # capability_registry -- see that call site's comment for the full
    # rationale.
    if _vector_similarity is not None:
        vec_text = _vector_similarity.text_for_capability_row({
            "capability_name": record["capability_name"],
            "workflow": record.get("workflow"),
            "owner": record["owner"],
            "apis": record["apis"],
            "business_rules": record["business_rules"],
        })
        conn.execute(
            "UPDATE capability_registry SET vector_json = ?, vector_updated_ts = ? WHERE capability_name = ?",
            (json.dumps(_vector_similarity.term_freq_vector(vec_text)), now, record["capability_name"]),
        )
    conn.commit()
    row = conn.execute(
        "SELECT capability_id FROM capability_registry WHERE capability_name = ?",
        (record["capability_name"],),
    ).fetchone()
    conn.close()
    print(json.dumps({"capability_id": row["capability_id"], "capability_name": record["capability_name"]}))


def _capability_row_to_dict(row):
    d = dict(row)
    for field in ("inputs", "business_rules", "apis"):
        d[field] = json.loads(d[field]) if d.get(field) else []
    for field in NULLABLE_JSON_LIST_CAPABILITY_FIELDS:
        d[field] = json.loads(d[field]) if d.get(field) else None
    d["ai_required"] = bool(d["ai_required"])
    d["metadata_json"] = json.loads(d["metadata_json"]) if d.get("metadata_json") else {}
    return d


def lookup_capability(args):
    """Implements ai-os/CAPABILITY_REGISTRY_SCHEMA_2026-07-24.yaml's
    lookup_contract.function_signature: lookupCapability(query) -> {found,
    matches, best_match_confidence}. resolution_order as designed:
      1. exact capability_name match (O(1) lookup)
      2. domain-scoped/keyword FTS match (capability_name/owner/apis/ui_screens/
         workflow), same _fts_query OR-of-terms convention query_knowledge uses
      3. embedding similarity fallback via capability-registry-service.ts's
         findSimilar() -- that function lives in compliance-tracker's own
         TypeScript runtime (a live pgvector cosine-similarity index), not
         reachable from this Python CLI. Honestly reported as
         embedding_fallback_available=False rather than faking a score --
         a caller that needs it calls findSimilar() directly, per the
         lookup_contract's own non_goals_this_phase note that this Python
         wiring composes with, not replaces, that existing mechanism."""
    init_db_silent()
    conn = _connect()
    _ensure_capability_registry_table(conn)

    matches = []
    stage = "none"
    if args.capability_name:
        rows = conn.execute(
            "SELECT * FROM capability_registry WHERE capability_name = ?",
            (args.capability_name,),
        ).fetchall()
        if rows:
            matches = [_capability_row_to_dict(r) for r in rows]
            stage = "exact_capability_name_match"

    if not matches:
        raw_query = " ".join(t for t in (args.intent_text, args.domain) if t)
        if raw_query.strip():
            q = _fts_query(raw_query)
            try:
                rows = conn.execute(
                    "SELECT t.* FROM capability_registry_fts f JOIN capability_registry t ON t.rowid = f.rowid "
                    "WHERE capability_registry_fts MATCH ? ORDER BY rank",
                    (q,),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if rows:
                matches = [_capability_row_to_dict(r) for r in rows]
                stage = "domain_scoped_keyword_match"

    conn.close()
    best_match_confidence = max((m["confidence"] for m in matches), default=0.0)
    print(json.dumps({
        "found": bool(matches),
        "matches": matches,
        "best_match_confidence": best_match_confidence,
        "resolution_stage_used": stage,
        "embedding_fallback_available": False,
        "embedding_fallback_note": "not reachable from this CLI -- call capability-registry-service.ts's "
                                    "findSimilar() directly in compliance-tracker for the embedding-similarity stage.",
    }, indent=2, default=str))


def list_capabilities(args):
    """Lists every capability_registry row -- used for evidence/row-count
    verification (e.g. by populate_capability_registry.py after a batch
    registration), not part of the lookup_contract itself."""
    init_db_silent()
    conn = _connect()
    _ensure_capability_registry_table(conn)
    rows = conn.execute("SELECT * FROM capability_registry ORDER BY capability_name").fetchall()
    conn.close()
    matches = [_capability_row_to_dict(r) for r in rows]
    print(json.dumps({"count": len(matches), "capabilities": matches}, indent=2, default=str))


# ---------------------------------------------------------------------------
# Critical amendment (2026-08-06, UMR-20260806-124654-a8d6, this task's own
# scoped UMR -- amends UMR-20260806-124327-6ffb and stop work order
# UMR-20260806-124055-bc80): the required deterministic-first task sequence.
# Step one/two below (search_task_precedent/cmd_search_task_precedent) are the
# read-only, side-effect-free "before any AI involvement" check: does an exact
# capability_registry script already exist (step one), and if not, has any
# similar kind of task already been done anywhere in past umr_tasks/
# capability_graduation_log history (step two, real search across ALL past
# work, not scoped to one UMR). Step three (AI proceeds under a UMR-scoped
# agent_id) is already specified elsewhere (ai_agent_registry, UMR-20260806-
# 121332-6ba4 and its corrections) -- not re-implemented here, this module
# never touches that table, avoiding a second, competing implementation of
# work already in flight on other branches at the time this was built
# (worker/task-20260806-165903-correction--wire-the-new-ai-agent-id-tab,
# worker/task-20260806-163355-correction--ai-agent-id-scoped-one-per-u, both
# still open PRs #199/#194 -- confirmed by direct inspection, not assumed).
# Step four (record_capability_graduation/cmd_record_capability_graduation) is
# the real critical new requirement this amendment adds: the mandatory,
# never-skippable post-work evaluation, recorded via capability_graduation_log.
# ---------------------------------------------------------------------------

def _ensure_capability_graduation_log_table(conn):
    """Standalone idempotent create, same defensiveness convention as
    _ensure_capability_registry_table/_ensure_route_replay_table -- works even
    if init_db() was never run against this DB."""
    conn.execute("""CREATE TABLE IF NOT EXISTS capability_graduation_log (
        graduation_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        umr_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        task_summary TEXT NOT NULL,
        decision TEXT NOT NULL CHECK(decision IN ('graduated', 'judgment_required')),
        reason TEXT NOT NULL,
        capability_id TEXT,
        script_path TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY (capability_id) REFERENCES capability_registry(capability_id)
    )""")
    conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS capability_graduation_log_fts USING fts5(
        umr_id, agent_id, task_summary, reason,
        content='capability_graduation_log', content_rowid='rowid'
    )""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS capability_graduation_log_ai AFTER INSERT ON capability_graduation_log BEGIN
        INSERT INTO capability_graduation_log_fts(rowid, umr_id, agent_id, task_summary, reason)
        VALUES (new.rowid, new.umr_id, new.agent_id, new.task_summary, new.reason);
    END""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_capability_graduation_log_umr_id ON capability_graduation_log(umr_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_capability_graduation_log_decision ON capability_graduation_log(decision)")
    conn.commit()


def search_task_precedent(conn, task_text, limit=10):
    """Steps one + two of the required sequence, read-only, no side effects.

    Step one: exact-then-FTS match against capability_registry, same two-stage
    resolution_order lookup_capability() already uses (exact capability_name
    equality first, domain-scoped keyword FTS as fallback) -- deliberately
    duplicated rather than refactored into a shared helper, since
    lookup_capability() already has real callers/tests depending on its exact
    current signature and print-and-exit shape; a real behavior change to it
    is out of this amendment's scope. A step-one match means: run that
    existing script, no AI, stop -- this function reports it, the caller is
    the one that actually stops. resolution_stage_used tells a caller which
    of the two it actually was; when it was the FTS fallback, matched >5
    keyword-only rows are reported as broad_keyword_overlap=True (same real
    imprecision agent_work_briefing.py's assemble_briefing() independently
    found and fixed the same way: an OR-of-terms FTS query over a full
    multi-sentence task_text can honestly match on incidental vocabulary, not
    a real fit -- a caller must not treat that as a confident stop).

    Step two (only reached if step one found nothing): a real search across
    ALL past umr_tasks (task_identity/source_trigger/logs_ref, via
    umr_tasks_fts) and ALL past capability_graduation_log rows
    (umr_id/agent_id/task_summary/reason, via capability_graduation_log_fts)
    for this same kind of task already done -- across the whole platform's
    history, not scoped to any one caller's own UMR. Each umr_tasks hit is
    left-joined against capability_graduation_log by umr_id so a caller gets
    the real script_id/capability_id or agent_id that past similar work
    actually used, if any was ever recorded.
    """
    _ensure_capability_registry_table(conn)
    _ensure_capability_graduation_log_table(conn)

    step1_matches = []
    resolution_stage = "none"
    rows = conn.execute(
        "SELECT * FROM capability_registry WHERE capability_name = ?", (task_text,)
    ).fetchall()
    if rows:
        step1_matches = [_capability_row_to_dict(r) for r in rows]
        resolution_stage = "exact_capability_name_match"
    else:
        q = _fts_query(task_text)
        try:
            rows = conn.execute(
                "SELECT t.* FROM capability_registry_fts f JOIN capability_registry t ON t.rowid = f.rowid "
                "WHERE capability_registry_fts MATCH ? ORDER BY rank LIMIT ?",
                (q, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if rows:
            step1_matches = [_capability_row_to_dict(r) for r in rows]
            resolution_stage = "domain_scoped_keyword_match"

    if step1_matches:
        return {
            "resolution_stage_used": resolution_stage,
            "broad_keyword_overlap": resolution_stage == "domain_scoped_keyword_match" and len(step1_matches) > 5,
            "step": 1,
            "action": "exact_script_found_run_it_no_ai_stop",
            "matches": step1_matches,
        }

    precedent = []
    try:
        umr_rows = conn.execute(
            "SELECT u.* FROM umr_tasks_fts f JOIN umr_tasks u ON u.rowid = f.rowid "
            "WHERE umr_tasks_fts MATCH ? ORDER BY u.ts_submitted DESC LIMIT ?",
            (q, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        umr_rows = []
    for row in umr_rows:
        grad = conn.execute(
            "SELECT graduation_id, agent_id, decision, capability_id, script_path, reason, ts "
            "FROM capability_graduation_log WHERE umr_id = ? ORDER BY ts DESC LIMIT 1",
            (row["umr_id"],),
        ).fetchone()
        precedent.append({
            "umr_id": row["umr_id"],
            "task_identity": row["task_identity"],
            "status": row["status"],
            "ts_submitted": row["ts_submitted"],
            "graduation": dict(grad) if grad else None,
        })

    try:
        grad_rows = conn.execute(
            "SELECT g.* FROM capability_graduation_log_fts f JOIN capability_graduation_log g ON g.rowid = f.rowid "
            "WHERE capability_graduation_log_fts MATCH ? ORDER BY g.ts DESC LIMIT ?",
            (q, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        grad_rows = []
    already_covered_umrs = {p["umr_id"] for p in precedent}
    for row in grad_rows:
        if row["umr_id"] in already_covered_umrs:
            continue
        precedent.append({
            "umr_id": row["umr_id"],
            "task_identity": None,
            "status": None,
            "ts_submitted": None,
            "graduation": dict(row),
        })

    if precedent:
        return {
            "step": 2,
            "action": "similar_past_work_found_report_script_or_agent_ids_used",
            "matches": precedent,
        }

    return {
        "step": 3,
        "action": "no_script_and_no_usable_precedent_ai_work_proceeds_under_umr_scoped_agent_id",
        "matches": [],
    }


def cmd_search_task_precedent(args):
    init_db_silent()
    conn = _connect()
    result = search_task_precedent(conn, args.task_text, limit=args.limit)
    conn.close()
    print(json.dumps(result, indent=2, default=str))


def record_capability_graduation(conn, umr_id, agent_id, task_summary, decision, reason,
                                  capability_id=None, script_path=None, metadata=None):
    """Step four, the real critical new requirement: the mandatory,
    never-skippable evaluation that must run the moment real AI work
    completes. Not a code-computed yes/no (whether a task genuinely requires
    ongoing judgment is itself a judgment call, made by the caller) -- what is
    deterministic here is that this record can never be skipped or narrated
    away, and that a 'graduated' decision can never be recorded without a
    real, already-registered capability_id + script_path backing it (raises
    ValueError otherwise -- this is the one guard that IS deterministic: no
    claiming a script was built without proof it was actually registered).

    Both halves of that guarantee are checked for real, not just for
    non-empty strings (2026-08-06 Superboss AUDIT FAIL on PR #205: a
    fabricated, never-registered capability_id was silently accepted
    because only `if not capability_id` was checked -- the schema's FK on
    capability_id is inert since _connect() never sets
    PRAGMA foreign_keys=ON, same documented limitation as
    ocid_artifact_links, which compensates with an explicit Python-side
    existence check; this function now does the same):
      - capability_id must be a real row already present in
        capability_registry (SELECT existence check, right now, never
        assumed from the caller's say-so).
      - script_path must resolve to a real file that actually exists on
        disk, using the same VERIDIAN_ROOT-relative resolution convention
        as verify_registry_file_paths.py's resolve_path()/path_exists()
        (absolute paths used as-is, root-relative paths like
        "scripts/foo.py" resolved under VERIDIAN_ROOT).
    Insert-only, mirrors route_replay's convention -- a UMR re-evaluated later
    gets a second row, so the full history stays queryable."""
    if decision not in ("graduated", "judgment_required"):
        raise ValueError(f"decision must be 'graduated' or 'judgment_required', got {decision!r}")
    if not reason or not reason.strip():
        raise ValueError("reason is required and must be non-empty for every graduation decision")
    if decision == "graduated":
        if not capability_id or not script_path:
            raise ValueError(
                "decision='graduated' requires a real capability_id (from register-capability) "
                "and script_path -- never force a script claim without a registered artifact backing it"
            )
        existing = conn.execute(
            "SELECT 1 FROM capability_registry WHERE capability_id = ?", (capability_id,)
        ).fetchone()
        if not existing:
            raise ValueError(
                f"decision='graduated' requires capability_id {capability_id!r} to already exist in "
                "capability_registry (register it first via register-capability) -- a fabricated or "
                "not-yet-registered capability_id can never back a 'graduated' claim"
            )
        resolved_script_path = (
            script_path if script_path.startswith("/") else os.path.join(VERIDIAN_ROOT, script_path)
        )
        if not os.path.exists(resolved_script_path):
            raise ValueError(
                f"decision='graduated' requires script_path {script_path!r} (resolved to "
                f"{resolved_script_path!r}) to be a real file that exists on disk right now -- "
                "a 'graduated' row can never claim a script that was not actually built"
            )
    else:
        if capability_id or script_path:
            raise ValueError(
                "decision='judgment_required' must not carry a capability_id/script_path -- "
                "no script was built, none should be implied"
            )

    _ensure_capability_graduation_log_table(conn)
    gid = _new_id("GRAD")
    now = _now_iso()
    conn.execute(
        "INSERT INTO capability_graduation_log (graduation_id, ts, umr_id, agent_id, task_summary, "
        "decision, reason, capability_id, script_path, metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (gid, now, umr_id, agent_id, task_summary, decision, reason, capability_id, script_path,
         json.dumps(metadata or {})),
    )
    return gid


def cmd_record_capability_graduation(args):
    init_db_silent()
    conn = _connect()
    try:
        gid = record_capability_graduation(
            conn, args.umr_id, args.agent_id, args.task_summary, args.decision, args.reason,
            capability_id=args.capability_id, script_path=args.script_path,
            metadata=json.loads(args.metadata) if args.metadata else None,
        )
        conn.commit()
    except ValueError as e:
        conn.close()
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
    conn.close()
    print(json.dumps({"graduation_id": gid, "umr_id": args.umr_id, "decision": args.decision}))


def _graduation_row_to_dict(row):
    d = dict(row)
    d["metadata_json"] = json.loads(d["metadata_json"]) if d.get("metadata_json") else {}
    return d


def list_capability_graduations(args):
    """Lists capability_graduation_log rows, optionally filtered to one
    --umr-id, newest first -- same evidence/row-count-verification role
    list_capabilities/list_replays already play for their own tables."""
    init_db_silent()
    conn = _connect()
    _ensure_capability_graduation_log_table(conn)
    if getattr(args, "umr_id", None):
        rows = conn.execute(
            "SELECT * FROM capability_graduation_log WHERE umr_id = ? ORDER BY ts DESC", (args.umr_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM capability_graduation_log ORDER BY ts DESC").fetchall()
    conn.close()
    matches = [_graduation_row_to_dict(r) for r in rows]
    print(json.dumps({"count": len(matches), "graduations": matches}, indent=2, default=str))


WIRING_ENTITY_TYPES = (
    "engine", "gateway", "supabase_table", "function", "route", "file", "script", "cron_job",
    "ai_role", "vercel_project", "github_repo", "browser_component",
    # 2026-07-27, dispatch-script consolidation (scripts/dispatch_core.py): one row per
    # actually-dispatched task, written by dispatch-tick.py/phase-continuation-tick.py --
    # distinct from 'cron_job' (the recurring script entity itself) so the wiring graph can
    # tell "this cron job ran" apart from "this cron job dispatched task X". See
    # _migrate_schema()'s wiring_registry CHECK-widening block for why a live DB created
    # before this addition needs an explicit migration, not just this tuple edit.
    "dispatch_event",
    # 2026-07-27, knowledge-engine/wiring-registry integration (task-20260727-025248):
    # first-class type for governance/constitution docs, built by
    # generate_wiring_registry.py's build_governance_docs() BEFORE the existing
    # knowledge_engine merge (build_from_knowledge_engine) runs, so a governance-tagged
    # knowledge_engine row enriches this entity instead of falling into the generic
    # 'file' type the way it did before -- same "first-class type, not a bucket" pattern
    # 'script' already established. See _migrate_wiring_registry_entity_types() for why a
    # live DB created before this addition needs an explicit migration.
    "governance_doc",
)
WIRING_SOURCE_SYSTEMS = ("server", "vercel", "supabase", "github")
REQUIRED_WIRING_ENTITY_FIELDS = {
    "entity_id", "entity_type", "source_system", "relationships", "last_verified_ts",
    "verification_status", "source_ref",
}


def _ensure_wiring_registry_table(conn):
    """Standalone idempotent create, same defensiveness convention as
    _ensure_capability_registry_table/_ensure_route_replay_table -- works even
    if init_db() was never run against this DB. Kept field-for-field identical
    to the CREATE TABLE in init_db()'s own executescript (single source of
    truth for the DDL; generate_wiring_registry.py's bulk upsert calls this
    same function before writing, never redefines the table itself)."""
    conn.execute(f"""CREATE TABLE IF NOT EXISTS wiring_registry (
        entity_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        entity_type TEXT NOT NULL CHECK(entity_type IN ({",".join("'" + t + "'" for t in WIRING_ENTITY_TYPES)})),
        source_system TEXT NOT NULL CHECK(source_system IN ({",".join("'" + s + "'" for s in WIRING_SOURCE_SYSTEMS)})),
        path TEXT,
        relationships TEXT NOT NULL DEFAULT '[]',
        last_verified_ts TEXT NOT NULL,
        verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED'
            CHECK(verification_status IN ('VERIFIED_MATCH','HASH_DRIFTED','PATH_MISSING','UNVERIFIED')),
        source_ref TEXT NOT NULL DEFAULT '[]',
        metadata_json TEXT NOT NULL DEFAULT '{{}}',
        content_hash TEXT,
        -- 2026-08-06 (task-20260806-035541, Owner directive "real PM cycle
        -- script registry"): additive script-bookkeeping fields, same
        -- nullable-ADD-COLUMN convention as content_hash above. originating_umr
        -- is the real UMR-YYYYMMDD-HHMMSS-hash (or, for pre-UMR-convention
        -- scripts, the real task-YYYYMMDD-HHMMSS id) mechanically recovered
        -- from the entity's own real source file, NEVER invented -- NULL means
        -- a real search found none, not that none was attempted. script_version
        -- is the real version token (e.g. 'v2', 'v3') mechanically parsed from
        -- the script's own filename when present, else NULL -- entity_type
        -- values other than 'script' leave both NULL. See
        -- _migrate_wiring_registry_umr_and_version() for the pre-existing-DB
        -- migration and generate_software_catalog.py/generate_wiring_registry.py
        -- for how these are actually derived, not guessed here.
        originating_umr TEXT,
        script_version TEXT,
        -- 2026-08-07 (UMR-20260807-035145-aa45, amendment to UMR-20260806-171945-5767,
        -- governing chain UMR-20260806-124055-bc80 / UMR-20260807-033123-d5c0): additive
        -- nullable-ADD-COLUMN pair, same convention as content_hash/originating_umr above,
        -- for reuse_verdict_engine.py's deterministic (non-ML) term-frequency vector --
        -- vector_json is a JSON object {{normalized_token: count}} built from the entity's
        -- own real path/entity_id/source_ref/metadata_json text by
        -- vector_similarity.vector_for_wiring_row(), never an embedding-model call (see
        -- vector_similarity.py's own module docstring for why: intent_engine.py's real,
        -- already-adopted constraint against speculative NLU/embedding builds, and
        -- superboss-register.py's own lookup_capability() docstring already documents that
        -- the only real embedding index in this system is compliance-tracker's TS/pgvector
        -- service, not reachable from this Python CLI). vector_updated_ts is the real ISO-8601
        -- timestamp the vector was last (re)computed, NULL until reuse_verdict_engine.py's
        -- backfill or the automatic on-write recompute in register_entity_row() below has run.
        vector_json TEXT,
        vector_updated_ts TEXT
    )""")
    conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS wiring_registry_fts USING fts5(
        path, entity_type, source_ref,
        content='wiring_registry', content_rowid='rowid'
    )""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS wiring_registry_ai AFTER INSERT ON wiring_registry BEGIN
        INSERT INTO wiring_registry_fts(rowid, path, entity_type, source_ref)
        VALUES (new.rowid, new.path, new.entity_type, new.source_ref);
    END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS wiring_registry_au AFTER UPDATE ON wiring_registry BEGIN
        INSERT INTO wiring_registry_fts(wiring_registry_fts, rowid, path, entity_type, source_ref)
        VALUES ('delete', old.rowid, old.path, old.entity_type, old.source_ref);
        INSERT INTO wiring_registry_fts(rowid, path, entity_type, source_ref)
        VALUES (new.rowid, new.path, new.entity_type, new.source_ref);
    END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS wiring_registry_ad AFTER DELETE ON wiring_registry BEGIN
        INSERT INTO wiring_registry_fts(wiring_registry_fts, rowid, path, entity_type, source_ref)
        VALUES ('delete', old.rowid, old.path, old.entity_type, old.source_ref);
    END""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wiring_registry_entity_type ON wiring_registry(entity_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wiring_registry_source_system ON wiring_registry(source_system)")
    conn.commit()


def register_entity_row(conn, entity):
    """Upsert ONE entity dict matching ai-os/WIRING_ENGINE_SCHEMA_2026-07-25.yaml's
    entity_record_schema field-for-field (entity_id/entity_type/source_system/path/
    relationships/last_verified_ts/verification_status/source_ref/metadata), plus the
    optional content_hash field added 2026-07-27 (task-20260727-025248) -- NOT in
    REQUIRED_WIRING_ENTITY_FIELDS since entity types with no single real file
    (ai_role/vercel_project/dispatch_event/...) never have one. Also accepts the
    optional originating_umr/script_version fields added 2026-08-06
    (task-20260806-035541, Owner directive "real PM cycle script registry") -- same
    NOT-required convention, since only entity_type='script' rows carry them (see
    wiring_registry's own CREATE TABLE docstring). Does
    NOT commit or ensure the table -- callers doing a bulk run (generate_wiring_registry.py)
    own one _ensure_wiring_registry_table() + one commit() around many calls to this;
    the register-entity CLI (a single ad hoc row) owns both itself, see register_entity()."""
    missing = sorted(REQUIRED_WIRING_ENTITY_FIELDS - set(entity))
    if missing:
        raise ValueError(f"entity dict missing required entity_record_schema field(s): {missing}")
    now = _now_iso()
    conn.execute(
        "INSERT INTO wiring_registry (entity_id, ts, entity_type, source_system, path, relationships, "
        "last_verified_ts, verification_status, source_ref, metadata_json, content_hash, "
        "originating_umr, script_version) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(entity_id) DO UPDATE SET ts=excluded.ts, entity_type=excluded.entity_type, "
        "source_system=excluded.source_system, path=excluded.path, relationships=excluded.relationships, "
        "last_verified_ts=excluded.last_verified_ts, verification_status=excluded.verification_status, "
        "source_ref=excluded.source_ref, metadata_json=excluded.metadata_json, "
        "content_hash=excluded.content_hash, originating_umr=excluded.originating_umr, "
        "script_version=excluded.script_version",
        (
            entity["entity_id"], now, entity["entity_type"], entity["source_system"], entity.get("path"),
            json.dumps(entity["relationships"]), entity["last_verified_ts"], entity["verification_status"],
            json.dumps(entity["source_ref"]), json.dumps(entity.get("metadata") or {}),
            entity.get("content_hash"), entity.get("originating_umr"), entity.get("script_version"),
        ),
    )
    # 2026-08-07 (UMR-20260807-035145-aa45): the real update mechanism that keeps
    # wiring_registry.vector_json current -- every entity write, from any real
    # caller (this is the one write path every caller already goes through, per
    # this UMR's own SPEC), recomputes its deterministic term-frequency vector
    # here. Never a separate backfill-only step for new/changed rows; the
    # one-time backfill in reuse_verdict_engine.py only exists to cover rows
    # written before this column existed.
    if _vector_similarity is not None:
        vec_text = _vector_similarity.text_for_wiring_row({
            "entity_id": entity["entity_id"],
            "entity_type": entity["entity_type"],
            "path": entity.get("path"),
            "source_ref": entity["source_ref"],
            "metadata_json": entity.get("metadata"),
        })
        conn.execute(
            "UPDATE wiring_registry SET vector_json = ?, vector_updated_ts = ? WHERE entity_id = ?",
            (json.dumps(_vector_similarity.term_freq_vector(vec_text)), now, entity["entity_id"]),
        )


def register_entity(args):
    """CLI wrapper around register_entity_row for a single ad hoc row (--record-file
    a JSON object matching entity_record_schema) -- the generate_wiring_registry.py
    bulk run does NOT go through this CLI, see register_entity_row's own docstring."""
    init_db_silent()
    conn = _connect()
    _ensure_wiring_registry_table(conn)
    with open(args.record_file, encoding="utf-8") as f:
        entity = json.load(f)
    try:
        register_entity_row(conn, entity)
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        conn.close()
        sys.exit(1)
    conn.commit()
    conn.close()
    print(json.dumps({"entity_id": entity["entity_id"], "entity_type": entity["entity_type"]}))


def _wiring_row_to_dict(row):
    d = dict(row)
    d["relationships"] = json.loads(d["relationships"]) if d.get("relationships") else []
    d["source_ref"] = json.loads(d["source_ref"]) if d.get("source_ref") else []
    d["metadata_json"] = json.loads(d["metadata_json"]) if d.get("metadata_json") else {}
    return d


def lookup_entity(args):
    """--entity-id exact match first (O(1)), else an FTS match over
    path/entity_type/source_ref -- same two-stage resolution_order convention
    as lookup_capability, minus the embedding-similarity stage (no equivalent
    exists for wiring entities)."""
    init_db_silent()
    conn = _connect()
    _ensure_wiring_registry_table(conn)

    matches = []
    stage = "none"
    if args.entity_id:
        rows = conn.execute("SELECT * FROM wiring_registry WHERE entity_id = ?", (args.entity_id,)).fetchall()
        if rows:
            matches = [_wiring_row_to_dict(r) for r in rows]
            stage = "exact_entity_id_match"

    if not matches and args.query:
        q = _fts_query(args.query)
        try:
            # Real fix (UMR-20260806-141250-1ceb, proposal 86 / governing
            # UMR-20260806-071025-1d28): this query used to have NO LIMIT --
            # confirmed live root cause of a 2034MB->4067MB (~11 min) DB
            # blowup (UMR-20260806-135902-cf13's own dbstat + row-level
            # evidence): a single sampled row's embedded
            # reuse_check_result.wiring.matches held all 8441 unranked-cutoff
            # matches for one query (~5.97MB just for that one field). Bound
            # chosen as WIRING_LOOKUP_MATCH_LIMIT (see module-level constant
            # below) -- FTS5's own `ORDER BY rank` already puts the most
            # relevant hits first, so this keeps the reuse-check's real
            # signal (top-ranked likely duplicates) while making the
            # per-query result size structurally bounded regardless of how
            # large wiring_registry grows. No internal LIMIT precedent
            # existed to match here: lookup_capability()'s own analogous FTS
            # query (capability_registry_fts, same file) was checked and, as
            # of this fix, was equally unbounded -- out of this UMR's
            # approved scope to change, noted for a future follow-up.
            rows = conn.execute(
                "SELECT t.* FROM wiring_registry_fts f JOIN wiring_registry t ON t.rowid = f.rowid "
                "WHERE wiring_registry_fts MATCH ? ORDER BY rank LIMIT ?",
                (q, WIRING_LOOKUP_MATCH_LIMIT),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if rows:
            matches = [_wiring_row_to_dict(r) for r in rows]
            stage = "keyword_match"

    conn.close()
    print(json.dumps({"found": bool(matches), "matches": matches, "resolution_stage_used": stage}, indent=2, default=str))


def list_entities(args):
    """Lists wiring_registry rows, optionally filtered to one --entity-type,
    used for evidence/row-count verification -- same role list_capabilities
    plays for capability_registry."""
    init_db_silent()
    conn = _connect()
    _ensure_wiring_registry_table(conn)
    if getattr(args, "entity_type", None):
        rows = conn.execute(
            "SELECT * FROM wiring_registry WHERE entity_type = ? ORDER BY entity_id", (args.entity_type,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM wiring_registry ORDER BY entity_id").fetchall()
    conn.close()
    matches = [_wiring_row_to_dict(r) for r in rows]
    print(json.dumps({"count": len(matches), "entities": matches}, indent=2, default=str))


# ---------------------------------------------------------------------------
# 11th tree (2026-07-27, SERVER RESOURCE GOVERNOR, Owner directive same day --
# ai-os/SERVER_RESOURCE_GOVERNOR_2026-07-27.md). Universal Task Metadata
# Record (UMR): one row per task submitted through scripts/resource_governor.py's
# submit() -- the persistent queue table every scheduled trigger (cron, systemd
# timer, systemd worker spawn) now writes to instead of calling `systemctl
# start` directly. Same table/FTS5/upsert-on-conflict convention as every
# other tree above, not a new pattern. status transitions:
#   queued -> dispatched -> running -> completed | failed
#                                    -> sigterm_sent -> killed
#   queued -> rejected_duplicate  (de-dup check in resource_governor.submit()
#                                   found an existing active row for the same
#                                   task_identity -- logged, not silently
#                                   dropped, per this table's own row)
# UMR-20260806-130914-e7f1 (real dispatch, governed by UMR-20260806-071025-1d28):
# 'completed_unmerged' added below -- a real, distinct, honest status for the
# case mark-umr-terminal's own new structured-evidence gate (see
# cmd_mark_umr_terminal's docstring) now separates out: real AI-side work
# genuinely finished, a real commit exists and is a real ancestor check
# CANDIDATE, but that commit is NOT (yet) a real ancestor of origin/main --
# i.e. a real, open, unmerged PR (this repo's own gridlock condition made this
# a routine, not edge-case, outcome the same day this was written: PRs
# #165/#166/#167/#169/#170/#171/#172 all real, tested, reviewed, and stuck
# unmerged behind the same 5/5 concurrency-saturation condition). Naming
# follows this codebase's own existing '<state>_unmerged' precedent
# (backfill_ocid_registry_phase2_columns.py's 'closed_unmerged',
# resource_governor.py's 'CLOSED-unmerged' comment) rather than inventing new
# vocabulary. Deliberately NOT folded into UMR_ACTIVE_STATUSES: the work
# itself is genuinely done (ts_completed is real and set) -- what's pending is
# merge, not further AI work -- so the existing dedup/stale-heartbeat sweeps
# that treat 'dispatched'/'running' rows as still-in-flight must never treat
# a completed_unmerged row the same way (reusing 'dispatched' here, as
# considered and rejected, would have caused exactly that false conflation).
# ---------------------------------------------------------------------------
UMR_STATUSES = (
    "queued", "dispatched", "running", "completed", "completed_unmerged",
    "failed", "rejected_duplicate", "sigterm_sent", "killed",
)
UMR_ACTIVE_STATUSES = ("queued", "dispatched", "running")


def _ensure_umr_table(conn):
    """Standalone idempotent create (CREATE TABLE IF NOT EXISTS), same
    defensiveness convention as _ensure_execution_log_table/
    _ensure_wiring_registry_table -- works even if init_db() was never run
    against this DB. Called both from _migrate_schema() (so `init` and any
    write-path CLI command picks up the table on a pre-existing DB) AND
    directly by resource_governor.py before every umr_tasks read/write (same
    "call the specific ensure function directly, bypassing _migrate_schema()"
    convention dispatch_core._upsert_wiring_row already established for
    wiring_registry). No CHECK-constraint-widening rebuild is needed here the
    way wiring_registry's entity_type migration needed one -- this is a
    brand-new table, so a pre-existing DB simply doesn't have it yet, and
    CREATE TABLE IF NOT EXISTS is genuinely sufficient. Tested against a
    fixture DB seeded with the real pre-existing (non-UMR) schema in
    tests/test_resource_governor.py, not a fresh DB, per PR #101's own
    postmortem on trusting CREATE TABLE IF NOT EXISTS alone.

    2026-08-02 fast path (fixes recurring 'database is locked' crashes,
    see /var/crash/..superboss-register.py.1000.crash 2026-08-01 21:52):
    resource_governor.py calls this before every umr_tasks read/write, and
    touch_umr_heartbeat() (CLI `heartbeat`) is fired every 5 minutes by every
    active worker's checkpoint loop -- so under real concurrency this ran the
    full CREATE TABLE/TRIGGER/INDEX sequence plus 3 migration functions (each
    with its own commit()) on nearly every call, almost always as a pure
    no-op, contending for SQLite's single writer lock. Once the schema is
    fully migrated (checked here via a plain read, no transaction), every
    subsequent call returns immediately with zero writes."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='umr_tasks'"
    ).fetchone()
    if row is not None:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(umr_tasks)").fetchall()}
        # UMR-20260806-095416-b6f0: "external_agent_eligible" added to this
        # fast-path gate set alongside the pre-existing three -- the whole
        # point of this fast path (see docstring above) is to skip re-running
        # every migration function under high-concurrency read/write load
        # once the schema is fully migrated, but that only holds once EVERY
        # real migration's own columns are included here. Forgetting to add
        # a newly migrated column to this set silently strands that
        # migration: _migrate_umr_tasks_external_agent_columns() would never
        # run again on a DB that already satisfied the OLD three-column
        # check (a real bug caught while building this task against the
        # real, live, already-migrated production DB -- confirmed via a
        # direct PRAGMA table_info() readback showing the new columns
        # missing even though _ensure_umr_table() had just run).
        #
        # UMR-20260806-130914-e7f1: the status column's own CHECK constraint
        # is not a column-existence question PRAGMA table_info can answer, so
        # it needs its own real check here -- a plain sqlite_master.sql text
        # read (cheap, no write, safe on every hot-path call, same cost class
        # as the PRAGMA table_info() call already made above) confirming
        # 'completed_unmerged' is really present in the stored CREATE TABLE
        # text. Skipping this would silently strand
        # _migrate_umr_tasks_status_widen() the exact same way forgetting an
        # external_agent_eligible-style column would -- this fast path must
        # never return early on a DB whose real CHECK constraint hasn't
        # actually been widened yet.
        status_migrated = row is not None and "'completed_unmerged'" in (
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='umr_tasks'"
            ).fetchone()["sql"] or ""
        )
        # UMR-20260813-125756-9221: same "must not be skipped by the fast
        # path on an already-migrated DB" hazard the comment above already
        # documents for status_migrated -- the (status, ts_submitted) index
        # _migrate_umr_tasks_status_ts_index() below adds is itself a real
        # migration, so its existence has to be part of this fast-path gate
        # too, or a DB that already satisfies every column/status check
        # (i.e. every real production DB right now) would never pick it up.
        index_migrated = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_umr_tasks_status_ts'"
        ).fetchone() is not None
        if {"last_heartbeat", "tenant_id", "utm_source", "external_agent_eligible",
                "ts_relay_attempted"} <= cols and status_migrated:
            # AUDIT:FAIL 2026-08-13T16:50Z (PR #308, head 34bb70b6) real
            # regression, independently reproduced and fixed here: the naive
            # version of this gate fell all the way through to the "slow
            # path" below whenever index_migrated was False, even though
            # every other real migration already ran. That slow path opens
            # with `CREATE TABLE IF NOT EXISTS umr_tasks (...)` -- a provable
            # silent no-op against a table that already exists under any
            # schema, partial or full -- immediately followed by
            # unconditional `CREATE INDEX ... ON umr_tasks(tier)` /
            # `...(task_identity)` etc., which assume the FULL base schema
            # (tier, ts_submitted, ...) is already present. That assumption
            # only holds for a genuine, from-day-one production umr_tasks
            # table; it does not hold in general for anything that merely
            # satisfies THIS gate (the 5 ALTER-added columns + widened status
            # CHECK say nothing about the original base columns). Reproduced
            # directly against tests/../test_full_server_file_registration.py
            # ::_bootstrap_and_point_env_at_tmp_db's minimal stub table (has
            # the 5 gate columns + widened status, deliberately omits tier
            # and ts_submitted) -- pre-fix: `sqlite3.OperationalError: no
            # such column: tier` inside init_db(); post-fix (below): the stub
            # keeps hitting this fast-path return exactly as it did before
            # this PR, index or no index.
            #
            # Real fix: never fall through to that full slow path just to
            # backfill one additive index. Add ONLY the missing index,
            # directly, the same idempotent/additive shape every other
            # _migrate_umr_* function already uses -- and only attempt it
            # when the column the index is actually built on (ts_submitted)
            # is present, so this can never crash a table (real or test
            # stub) that predates even the base schema. Every real
            # production umr_tasks table has had ts_submitted since its
            # original CREATE TABLE, so this still delivers the PR's real
            # goal (auto-backfilling idx_umr_tasks_status_ts onto every
            # already-migrated live DB on first connect) for every DB that
            # actually matters; it just no longer crashes ones that don't
            # look like a real production table.
            if not index_migrated and "ts_submitted" in cols:
                _migrate_umr_tasks_status_ts_index(conn)
            return
    status_sql = ",".join("'" + s + "'" for s in UMR_STATUSES)
    conn.execute(f"""CREATE TABLE IF NOT EXISTS umr_tasks (
        umr_id TEXT PRIMARY KEY,
        task_identity TEXT NOT NULL,
        ts_submitted TEXT NOT NULL,
        tier INTEGER NOT NULL CHECK(tier BETWEEN 0 AND 4),
        status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ({status_sql})),
        source_trigger TEXT NOT NULL,
        task_kind TEXT NOT NULL DEFAULT 'systemctl_action',
        unit_name TEXT,
        inputs_json TEXT NOT NULL DEFAULT '{{}}',
        outputs_json TEXT NOT NULL DEFAULT '{{}}',
        logs_ref TEXT,
        metric_snapshot_json TEXT,
        ts_dispatched TEXT,
        ts_sigterm TEXT,
        ts_completed TEXT,
        reason TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{{}}'
    )""")
    conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS umr_tasks_fts USING fts5(
        task_identity, source_trigger, logs_ref,
        content='umr_tasks', content_rowid='rowid'
    )""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS umr_tasks_ai AFTER INSERT ON umr_tasks BEGIN
        INSERT INTO umr_tasks_fts(rowid, task_identity, source_trigger, logs_ref)
        VALUES (new.rowid, new.task_identity, new.source_trigger, new.logs_ref);
    END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS umr_tasks_au AFTER UPDATE ON umr_tasks BEGIN
        INSERT INTO umr_tasks_fts(umr_tasks_fts, rowid, task_identity, source_trigger, logs_ref)
        VALUES ('delete', old.rowid, old.task_identity, old.source_trigger, old.logs_ref);
        INSERT INTO umr_tasks_fts(rowid, task_identity, source_trigger, logs_ref)
        VALUES (new.rowid, new.task_identity, new.source_trigger, new.logs_ref);
    END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS umr_tasks_ad AFTER DELETE ON umr_tasks BEGIN
        INSERT INTO umr_tasks_fts(umr_tasks_fts, rowid, task_identity, source_trigger, logs_ref)
        VALUES ('delete', old.rowid, old.task_identity, old.source_trigger, old.logs_ref);
    END""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umr_tasks_identity ON umr_tasks(task_identity)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umr_tasks_status ON umr_tasks(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umr_tasks_tier ON umr_tasks(tier)")
    # UMR-20260813-125756-9221: see _migrate_umr_tasks_status_ts_index()'s own
    # docstring for why the plain idx_umr_tasks_status index above is not
    # enough on its own -- real EXPLAIN QUERY PLAN evidence gathered for this
    # task showed `SELECT * FROM umr_tasks WHERE status=? ORDER BY
    # ts_submitted DESC LIMIT ?` (resource_governor.py --query-umr's real
    # query) using idx_umr_tasks_status for the WHERE but still falling back
    # to `USE TEMP B-TREE FOR ORDER BY`, which materializes every matching
    # row (status, ts_submitted) covers both the WHERE and the ORDER BY, so
    # the same query plans as a pure index walk that stops at LIMIT rows.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umr_tasks_status_ts ON umr_tasks(status, ts_submitted DESC)")
    conn.commit()
    _migrate_umr_last_heartbeat(conn)
    _migrate_umr_tenant_id(conn)
    _migrate_umr_utm(conn)
    _migrate_umr_tasks_external_agent_columns(conn)
    _migrate_umr_relay_courtesy(conn)
    # UMR-20260806-130914-e7f1: must run AFTER every other ALTER-TABLE-ADD-
    # COLUMN migration above (incl. _migrate_umr_relay_courtesy's own
    # ts_relay_attempted/relay_outcome/relay_detail) -- its rebuild copies
    # columns dynamically via a live PRAGMA table_info(umr_tasks) read, so
    # every column added by an earlier migration in this function must
    # already exist on the live table by the time this one runs, or it would
    # be silently dropped on rebuild.
    _migrate_umr_tasks_status_widen(conn)
    _migrate_umr_tasks_status_ts_index(conn)


def _migrate_umr_tasks_status_ts_index(conn):
    """UMR-20260813-125756-9221 (Priority-1 UMR-20260806-171945-5767
    addendum, register-CLI-invocation guard task): idempotent, additive
    CREATE INDEX for a pre-existing umr_tasks table that predates this
    migration -- same "check via sqlite_master, create if missing, no
    rebuild needed (this is a pure additive index, not a column/CHECK
    change)" shape as every other _migrate_umr_* function above, just
    simpler because CREATE INDEX IF NOT EXISTS is itself already idempotent
    and requires no table rebuild.

    Real root cause this fixes (measured against the live, 4GB+
    superboss-register.sqlite for this task, not guessed): --query-umr
    --status X --limit N runs `SELECT * FROM umr_tasks WHERE status=?
    ORDER BY ts_submitted DESC LIMIT ?`. With only the single-column
    idx_umr_tasks_status index available, SQLite's real (measured via
    EXPLAIN QUERY PLAN) plan is `SEARCH ... USING INDEX idx_umr_tasks_status
    (status=?)` followed by `USE TEMP B-TREE FOR ORDER BY` -- it has to
    pull every matching row's FULL columns (including the large
    inputs_json/outputs_json/metadata_json/metric_snapshot_json blobs) into
    a temp b-tree to sort them by ts_submitted BEFORE the LIMIT can be
    applied, so LIMIT bounds the output but not the real work/memory. For
    status='killed' alone that was measured at 826 rows totalling ~717MB of
    JSON blob columns -- a real, measured, direct contributor to the
    incident's ~2GB resident PID 1685324 (51+ minute wedge, D-state,
    wchan=mem_cgroup_handle_over_high, box at zero free memory/swap).

    This composite (status, ts_submitted DESC) index lets SQLite walk the
    index itself in the exact order ORDER BY needs, so it can stop after
    LIMIT matching rows without ever materializing the rest -- confirmed via
    EXPLAIN QUERY PLAN against a real `.backup`-safe copy of the live DB:
    the same query plans as a single `SEARCH ... USING INDEX
    idx_umr_tasks_status_ts (status=?)` with no temp b-tree step at all."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_umr_tasks_status_ts'"
    ).fetchone()
    if row is not None:
        return
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umr_tasks_status_ts ON umr_tasks(status, ts_submitted DESC)")
    conn.commit()


def _migrate_umr_tasks_status_widen(conn):
    """UMR-20260806-130914-e7f1: widens umr_tasks.status's CHECK constraint to
    allow the new 'completed_unmerged' status (see UMR_STATUSES's own comment
    for what this status is and why it exists). SQLite has no ALTER TABLE for
    CHECK constraints, so a pre-existing table (this DB has one, created
    before this addition, with real production rows) needs a real rebuild --
    same proven mechanism _migrate_wiring_registry_entity_types() already
    established for this exact class of problem (CHECK-widening on a live
    table), reused here rather than inventing a second one.

    Unlike that function, this one does the rebuild via a targeted string
    substitution on the table's own real, live, stored CREATE TABLE text
    (sqlite_master.sql) rather than hand-reconstructing the full column list:
    umr_tasks has picked up many ALTER TABLE ADD COLUMN'd columns over time
    (last_heartbeat, tenant_id, the utm_* set, the external_agent_* set --
    see the migration functions above) that are NOT part of this function's
    own base CREATE TABLE text above, so hand-listing columns here would risk
    silently dropping one on rebuild. Substituting only the exact known-old
    status CHECK clause inside the real stored text, and refusing (raising,
    never guessing) if that exact clause isn't found, is the only way to
    guarantee every real column -- base and migrated-in alike -- survives the
    rebuild unchanged.

    No-op (checked via sqlite_master's own stored CREATE TABLE text, same
    convention as the wiring_registry migration) once already migrated, so
    this is safe to call on every _ensure_umr_table() invocation that reaches
    it -- reaches it only because the fast-path gate above already excludes
    the common case via the same sqlite_master.sql check, so in steady state
    this function's own body never runs a second time.

    UMR-20260806-130914-e7f1 real independent-review addendum: unlike
    _migrate_wiring_registry_entity_types() (its real precedent, which does
    its own equivalent rebuild WITHOUT _write_lock() protection -- a real,
    pre-existing gap in that already-shipped code, out of scope to fix
    here), this function's actual rebuild IS wrapped in _write_lock() below,
    for the same real corruption-prevention reason _write_lock()'s own
    docstring documents (2026-07-23 incident): a full DROP+rebuild+RENAME is
    a much larger real write than a single ALTER TABLE ADD COLUMN, so it
    deserves the same real protection reconcile_umr_status_against_pr()'s
    own write already gets, not less. Safe to nest under an outer
    _write_lock() (e.g. the `init` CLI command) because _write_lock() was
    made re-entrant specifically for this real call site -- see its own
    docstring addendum."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='umr_tasks'"
    ).fetchone()
    if row is None:
        return  # table doesn't exist yet -- the CREATE TABLE IF NOT EXISTS above already covers that with the widened CHECK
    old_sql = row["sql"]
    if "'completed_unmerged'" in old_sql:
        return  # already migrated

    old_status_clause = (
        "CHECK(status IN ('queued','dispatched','running','completed','failed',"
        "'rejected_duplicate','sigterm_sent','killed'))"
    )
    if old_status_clause not in old_sql:
        raise RuntimeError(
            "_migrate_umr_tasks_status_widen: expected exact old status CHECK "
            "clause not found in live umr_tasks CREATE TABLE text -- refusing "
            "to guess at a rebuild. Real text was: " + old_sql
        )
    new_status_sql = ",".join("'" + s + "'" for s in UMR_STATUSES)
    new_status_clause = f"CHECK(status IN ({new_status_sql}))"

    migrate_sql = old_sql.replace(old_status_clause, new_status_clause, 1)
    if "CREATE TABLE umr_tasks (" not in migrate_sql:
        raise RuntimeError(
            "_migrate_umr_tasks_status_widen: expected 'CREATE TABLE umr_tasks (' "
            "prefix not found in live umr_tasks CREATE TABLE text -- refusing "
            "to guess at a rebuild. Real text was: " + old_sql
        )
    migrate_sql = migrate_sql.replace(
        "CREATE TABLE umr_tasks (", "CREATE TABLE umr_tasks__migrate (", 1
    )

    with _write_lock():
        # Re-check under the real lock -- a concurrent process may have
        # already completed this exact rebuild while this process was
        # merely constructing migrate_sql above (no write happened yet).
        row2 = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='umr_tasks'"
        ).fetchone()
        if row2 is not None and "'completed_unmerged'" in row2["sql"]:
            return

        conn.execute("DROP TRIGGER IF EXISTS umr_tasks_ai")
        conn.execute("DROP TRIGGER IF EXISTS umr_tasks_au")
        conn.execute("DROP TRIGGER IF EXISTS umr_tasks_ad")
        conn.execute("DROP TABLE IF EXISTS umr_tasks_fts")

        conn.execute(migrate_sql)
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(umr_tasks)").fetchall()]
        cols_sql = ", ".join(cols)
        conn.execute(f"INSERT INTO umr_tasks__migrate ({cols_sql}) SELECT {cols_sql} FROM umr_tasks")
        conn.execute("DROP TABLE umr_tasks")
        conn.execute("ALTER TABLE umr_tasks__migrate RENAME TO umr_tasks")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_umr_tasks_identity ON umr_tasks(task_identity)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_umr_tasks_status ON umr_tasks(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_umr_tasks_tier ON umr_tasks(tier)")

        conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS umr_tasks_fts USING fts5(
            task_identity, source_trigger, logs_ref,
            content='umr_tasks', content_rowid='rowid'
        )""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS umr_tasks_ai AFTER INSERT ON umr_tasks BEGIN
            INSERT INTO umr_tasks_fts(rowid, task_identity, source_trigger, logs_ref)
            VALUES (new.rowid, new.task_identity, new.source_trigger, new.logs_ref);
        END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS umr_tasks_au AFTER UPDATE ON umr_tasks BEGIN
            INSERT INTO umr_tasks_fts(umr_tasks_fts, rowid, task_identity, source_trigger, logs_ref)
            VALUES ('delete', old.rowid, old.task_identity, old.source_trigger, old.logs_ref);
            INSERT INTO umr_tasks_fts(rowid, task_identity, source_trigger, logs_ref)
            VALUES (new.rowid, new.task_identity, new.source_trigger, new.logs_ref);
        END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS umr_tasks_ad AFTER DELETE ON umr_tasks BEGIN
            INSERT INTO umr_tasks_fts(umr_tasks_fts, rowid, task_identity, source_trigger, logs_ref)
            VALUES ('delete', old.rowid, old.task_identity, old.source_trigger, old.logs_ref);
        END""")
        conn.execute("INSERT INTO umr_tasks_fts(umr_tasks_fts) VALUES ('rebuild')")
        conn.commit()


def _ensure_ocid_artifact_links_table(conn):
    """OCID-068 real requirement addendum (UMR-20260804-170055-a069, Owner
    real-time implementation override on the standing hard-rule-7 lock, cited
    UMR-20260804-164106-3fb8/UMR-20260804-170055-a069): structured,
    deterministic linkage between an OCID number, its real UMR, and the real
    PR/commit/file path(s) that closed it -- Option A from the real owner
    review package (ai-os/VERIDIAN_OCID_068_..._OWNER_REVIEW_PACKAGE_2026-08-04.md
    §4e), a new additive table, not a change to umr_tasks itself. Same
    idempotent CREATE TABLE IF NOT EXISTS + standalone-callable convention as
    _ensure_umr_table/_ensure_wiring_registry_table above -- safe to call on
    every write path, works even on a pre-existing DB that predates this
    table. umr_id is a real FOREIGN KEY into umr_tasks (SQLite only enforces
    this when the caller's connection has `PRAGMA foreign_keys = ON`, which
    _connect() does not set today -- documented here rather than silently
    assumed, since foreign_keys is a real, well-known SQLite opt-in). The
    UNIQUE constraint intentionally does NOT enforce true idempotency for
    NULL-valued pr_number/file_path (SQLite treats NULLs as distinct in a
    UNIQUE index) -- insert_ocid_artifact_link() below does an explicit
    pre-insert existence check instead, so idempotency is a real property of
    the Python call, not assumed from the SQL constraint alone."""
    conn.execute("""CREATE TABLE IF NOT EXISTS ocid_artifact_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ocid_number TEXT NOT NULL,
        umr_id TEXT NOT NULL REFERENCES umr_tasks(umr_id),
        repo TEXT NOT NULL,
        pr_number INTEGER,
        commit_sha TEXT,
        file_path TEXT,
        link_kind TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(ocid_number, umr_id, repo, pr_number, file_path)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ocid_links_ocid ON ocid_artifact_links(ocid_number)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ocid_links_umr ON ocid_artifact_links(umr_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ocid_links_pr ON ocid_artifact_links(repo, pr_number)")
    conn.commit()


def _ensure_resume_dead_letter_table(conn):
    """UMR-20260813-235702 fix: resume_interrupted_workers_tick() (dispatch-
    tick.py) used to call resource_governor.submit() unconditionally, every
    tick, for every task_identity still in RESUMABLE_STATUSES with no active
    unit -- even when that identity had already been rejected as a duplicate
    dozens of times in a row by reuse_verdict_engine.assess()'s content-
    similarity check (verdict=duplication_blocked against an unrelated
    already-registered entity, not the find_active_umr_by_identity() check
    _existing_active_umr() already short-circuits in dispatch-tick.py -- a
    completely separate rejection path, so that earlier fix
    (UMR-20260806-103711-bf00) never touched this one). Confirmed live via
    production superboss-register.sqlite: 10 real task identities (dated
    2026-07-18 and 2026-08-07) each carrying 40 consecutive rejected_duplicate
    rows from source_trigger='dispatch-tick:resume_interrupted_workers', still
    growing every ~10-minute tick as of 2026-08-13T23:52Z.

    This table is the durable, small, additive bounded-retry ledger: one row
    per task_identity that has been resubmitted through
    resume_interrupted_workers_tick(), tracking how many CONSECUTIVE times in
    a row the most recent outcome was a rejection, and -- once that streak
    reaches dispatch-tick.py's own MAX_CONSECUTIVE_RESUME_REJECTIONS named
    constant -- a real, permanent marked_dead_ts. A permanently-dead
    task_identity is skipped by resume_interrupted_workers_tick() BEFORE it
    calls resource_governor.submit() at all: no fresh umr_tasks row, no
    reuse_verdict_engine similarity scan. A genuine forward-progress outcome
    (accepted=True, i.e. actually queued) clears the row entirely (see
    record_resume_outcome() below) -- this ledger tracks only CONSECUTIVE
    failure streaks, never a lifetime failure count, so a task_identity that
    starts resuming successfully again is never wrongly kept dead by stale
    history.

    Deliberately a NEW, separate table -- umr_tasks itself is the audit trail
    (never purged/rewritten, see this fix's own governing SPEC step 7); this
    ledger only ever reads outcome signals passed to it explicitly by the
    caller and writes exclusively to its own table. Same idempotent CREATE
    TABLE IF NOT EXISTS + standalone-callable convention as
    _ensure_ocid_artifact_links_table/_ensure_umr_table above."""
    conn.execute("""CREATE TABLE IF NOT EXISTS resume_dead_letter (
        task_identity TEXT PRIMARY KEY,
        consecutive_rejections INTEGER NOT NULL DEFAULT 0,
        last_status TEXT,
        last_ts TEXT NOT NULL,
        marked_dead_ts TEXT,
        reason TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_resume_dead_letter_marked "
                 "ON resume_dead_letter(marked_dead_ts)")
    conn.commit()


def is_resume_dead(conn, task_identity):
    """True if task_identity has already been marked permanently dead /
    non-resumable (see _ensure_resume_dead_letter_table's docstring above).
    Read-only, safe to call every tick before resource_governor.submit()."""
    _ensure_resume_dead_letter_table(conn)
    row = conn.execute(
        "SELECT marked_dead_ts FROM resume_dead_letter WHERE task_identity=?",
        (task_identity,),
    ).fetchone()
    return bool(row and row["marked_dead_ts"])


def record_resume_outcome(conn, task_identity, accepted, max_consecutive, reason_note=None):
    """Records the real outcome of one resume_interrupted_workers_tick()
    resubmission attempt for task_identity, and marks it permanently dead the
    moment its CONSECUTIVE rejection streak reaches max_consecutive (a
    caller-supplied named constant -- see dispatch-tick.py's
    MAX_CONSECUTIVE_RESUME_REJECTIONS). accepted=True (resource_governor.
    submit() actually queued it) clears any existing streak -- real forward
    progress means this identity is not stuck, regardless of past history.
    accepted=False (rejected_duplicate, or a prior resume that ended in
    'failed') increments the streak.

    Returns True the moment this call is what pushed the identity over the
    threshold (so the caller can log a clear one-time "marked permanently
    dead" line), False otherwise (including every call after the identity
    was already dead, and every accepted=True call)."""
    _ensure_resume_dead_letter_table(conn)
    ts = _now_iso()
    if accepted:
        conn.execute("DELETE FROM resume_dead_letter WHERE task_identity=?", (task_identity,))
        conn.commit()
        return False

    row = conn.execute(
        "SELECT consecutive_rejections, marked_dead_ts FROM resume_dead_letter WHERE task_identity=?",
        (task_identity,),
    ).fetchone()
    if row and row["marked_dead_ts"]:
        return False  # already dead; nothing new to record

    new_count = (row["consecutive_rejections"] if row else 0) + 1
    just_died = new_count >= max_consecutive
    marked_dead_ts = ts if just_died else None
    reason = (
        f"{new_count} consecutive rejected resume attempts (cap={max_consecutive}); "
        f"{reason_note or 'source_trigger=dispatch-tick:resume_interrupted_workers'}"
    ) if just_died else None
    conn.execute(
        """INSERT INTO resume_dead_letter
               (task_identity, consecutive_rejections, last_status, last_ts, marked_dead_ts, reason)
           VALUES (?, ?, 'rejected', ?, ?, ?)
           ON CONFLICT(task_identity) DO UPDATE SET
               consecutive_rejections=excluded.consecutive_rejections,
               last_status=excluded.last_status,
               last_ts=excluded.last_ts,
               marked_dead_ts=excluded.marked_dead_ts,
               reason=excluded.reason""",
        (task_identity, new_count, ts, marked_dead_ts, reason),
    )
    conn.commit()
    return just_died


def mark_resume_dead(conn, task_identity, reason):
    """Explicit, direct one-time marker (this fix's own SPEC step 4 cleanup
    pass) -- sets marked_dead_ts immediately regardless of recorded
    consecutive_rejections history, for a task_identity whose real umr_tasks
    history already proves (independently of this ledger, which only started
    counting once this fix shipped) that it is permanently stuck. Idempotent:
    safe to call more than once for the same identity."""
    _ensure_resume_dead_letter_table(conn)
    ts = _now_iso()
    conn.execute(
        """INSERT INTO resume_dead_letter
               (task_identity, consecutive_rejections, last_status, last_ts, marked_dead_ts, reason)
           VALUES (?, 0, 'rejected', ?, ?, ?)
           ON CONFLICT(task_identity) DO UPDATE SET
               marked_dead_ts=excluded.marked_dead_ts,
               reason=excluded.reason""",
        (task_identity, ts, ts, reason),
    )
    conn.commit()


def _ensure_ocid_canonical_registry_table(conn):
    """UMR-20260805-032326-becc (Owner directive): a real, complete, permanent
    OCID-001..068 -> canonical UMR roster, stored durably here (the same
    umr_tasks database, not a separate scratch file) so it stays the single
    real source of truth and does not drift out of date. Distinct in purpose
    from ocid_artifact_links above: that table records many-to-many real
    (OCID, UMR, PR/commit/file) EVIDENCE LINKS as they're discovered during
    normal work; this table is a one-row-per-OCID ROLLUP naming the single
    real CANONICAL UMR for each OCID (when one exists), explicitly carrying
    every other real UMR ever minted for the same OCID (a real duplicate
    dispatch is not silently dropped, just marked non-canonical with a real
    reason), and explicitly recording the honest case where a thorough real
    search found nothing at all -- a case ocid_artifact_links has no way to
    represent (it only stores rows for links that exist).

    One row per real OCID number (PRIMARY KEY, not AUTOINCREMENT -- there is
    exactly one real row per OCID by construction, upserts replace it).
    canonical_umr_id is nullable: NULL means a real, thorough search found no
    real UMR for this OCID (see not_found/search_note), not an unfilled gap.
    all_umr_ids_json is always a real JSON array, even for a single-UMR OCID
    (so callers never need an isinstance check to know how many were found).
    evidence_json records, per real search method used, what was actually
    run and what it found -- the real methodology note UMR-20260805-032326-becc
    asked for, kept alongside the data it justifies rather than in a
    separate doc that could drift out of sync with it."""
    conn.execute("""CREATE TABLE IF NOT EXISTS ocid_canonical_registry (
        ocid_number TEXT PRIMARY KEY,
        canonical_umr_id TEXT REFERENCES umr_tasks(umr_id),
        status TEXT NOT NULL,
        pr_number INTEGER,
        pr_repo TEXT,
        all_umr_ids_json TEXT NOT NULL,
        duplicate_reason TEXT,
        not_found INTEGER NOT NULL DEFAULT 0,
        evidence_json TEXT NOT NULL,
        last_verified_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ocid_canonical_status ON ocid_canonical_registry(status)")
    conn.commit()
    _migrate_ocid_canonical_registry_completion_columns(conn)
    _ensure_ocid_canonical_registry_completion_triggers(conn)


def _migrate_ocid_canonical_registry_completion_columns(conn):
    """OCID-068 Phase 2 real registry-schema extension (Owner directive
    UMR-20260805-090549-9710, extending the now-superseded
    UMR-20260805-085025-c257; citing the canonical OCID-068 UMR
    UMR-20260804-170055-a069 and its permanent closure record
    UMR-20260805-032731-b412). Additive, idempotent ALTER TABLE ADD COLUMN
    migration for DBs created before these columns existed -- same
    PRAGMA-table_info-then-ALTER convention as _migrate_schema's own
    system_index.tags migration above, since SQLite's
    `ALTER TABLE ... ADD COLUMN` errors out if the column already exists.

    Adds real dedicated evidence columns (commit_sha, file_name, file_path,
    merge_status, evidence_summary) so this data no longer lives only inside
    the unstructured evidence_json text blob, plus seven real completion-gate
    boolean columns (has_real_umr, has_real_pr, has_real_commit,
    has_real_merge, has_real_file_path, has_real_evidence_summary,
    is_fully_complete). The seven booleans are NEVER meant to be hand-set by
    this function or by any Python caller -- they default to 0 here purely so
    the column exists with a real, valid NOT NULL value immediately after
    ADD COLUMN; the AFTER INSERT/AFTER UPDATE triggers created by
    _ensure_ocid_canonical_registry_completion_triggers() below immediately
    recompute and overwrite them from the row's own real underlying columns
    on every subsequent write. Existing rows added before this migration ran
    keep their ADD-COLUMN default of 0 until the next write to that row (an
    ALTER TABLE ADD COLUMN backfill does not itself fire an UPDATE trigger)
    -- the backfill step of this same directive re-writes every one of the 69
    existing rows via upsert_ocid_canonical_registry(), which does fire the
    trigger and correctly recomputes every row's real boolean values.

    `not_applicable_confirmed` (Owner reinforcement directive
    UMR-20260805-091934-86a2, extending UMR-20260805-090549-9710): a real,
    trigger-computed (never hand-set, same governance as the 7 has_real_*/
    is_fully_complete columns) explicit marker for the 8 real rows honestly
    confirmed `not_found` (OCID-007..OCID-011, OCID-012, OCID-013,
    OCID-014) -- these are the only real rows where "no file path" is a
    genuine, confirmed non-applicability (the OCID itself was never real /
    never registered), not an unattempted gap.

    `audit_raw_output` (Owner urgent correction UMR-20260805-092408-4f97):
    a real, verbatim, JSON-encoded dump of resolve_ocid_canonical()'s own
    `evidence` dict -- the SAME already-merged (UMR-20260805-042152-e559),
    zero-AI-judgment, fully mechanical 6-method search this file already
    runs (umr_tasks substring match, full-table grep, `gh pr list` x3 repos,
    `git log --grep` x3 repos as cross-check, UMR-ID regex extraction from
    PR bodies, MASTER-TRACKER/ACTIVE-CLAIMS grep as last resort -- see that
    function's own docstring for the exact order). `audit_raw_output` is
    populated exclusively by audit_ocid_canonical_registry.py (a thin,
    deterministic orchestration script that calls resolve_ocid_canonical()
    and upsert_ocid_canonical_registry(), with zero interpretive logic of
    its own beyond what resolve_ocid_canonical() itself already does) --
    never hand-typed prose, never a second parallel search implementation.
    `not_applicable_confirmed` is deliberately gated on BOTH `not_found = 1`
    AND `audit_raw_output` being genuinely present (non-NULL, non-empty) --
    a bare `not_found=True` parameter with no real stored evidence behind it
    is no longer sufficient to earn the confirmed marker (see the trigger
    body below), closing the real fabrication risk UMR-20260805-092408-4f97
    named: a caller writing a plausible-sounding boolean+reason without ever
    genuinely re-running the real search."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(ocid_canonical_registry)").fetchall()}
    new_columns = [
        ("commit_sha", "TEXT"),
        ("file_name", "TEXT"),
        ("file_path", "TEXT"),
        ("merge_status", "TEXT"),
        ("evidence_summary", "TEXT"),
        ("has_real_umr", "INTEGER NOT NULL DEFAULT 0"),
        ("has_real_pr", "INTEGER NOT NULL DEFAULT 0"),
        ("has_real_commit", "INTEGER NOT NULL DEFAULT 0"),
        ("has_real_merge", "INTEGER NOT NULL DEFAULT 0"),
        ("has_real_file_path", "INTEGER NOT NULL DEFAULT 0"),
        ("has_real_evidence_summary", "INTEGER NOT NULL DEFAULT 0"),
        ("is_fully_complete", "INTEGER NOT NULL DEFAULT 0"),
        ("not_applicable_confirmed", "INTEGER NOT NULL DEFAULT 0"),
        ("audit_raw_output", "TEXT"),
    ]
    changed = False
    for name, decl in new_columns:
        if name not in cols:
            conn.execute(f"ALTER TABLE ocid_canonical_registry ADD COLUMN {name} {decl}")
            changed = True
    if changed:
        conn.commit()


def _ensure_ocid_canonical_registry_completion_triggers(conn):
    """OCID-068 Phase 2 (UMR-20260805-090549-9710, reinforced by
    UMR-20260805-091934-86a2's not_applicable_confirmed addition): the real,
    code-enforced completion gate. No Python code path, no caller, and no
    future direct SQL write should be able to set any of the 8 boolean
    columns (the original 7 has_real_*/is_fully_complete, plus
    not_applicable_confirmed) to a value inconsistent with the row's own
    real underlying data -- the only way to truly guarantee that in SQLite
    is a real AFTER INSERT / AFTER UPDATE trigger that recomputes and
    overwrites all 8 booleans from the row's own real columns on every
    single write, whatever the caller passed. Using
    `CREATE TRIGGER IF NOT EXISTS` matches this file's own idempotent
    migration-function convention -- safe to call on every write path/every
    process start, including against a pre-existing DB that predates these
    triggers.

    Each trigger's own UPDATE targets `WHERE rowid = NEW.rowid` (this table's
    PRIMARY KEY is `ocid_number`, not an implicit INTEGER rowid alias, so
    rowid is still the correct, unambiguous single-row target and is not the
    same column as ocid_number). SQLite's default `PRAGMA recursive_triggers`
    is OFF for any connection that never explicitly turns it on (this file's
    _connect() does not), so the AFTER UPDATE trigger's own internal UPDATE
    does not recursively re-fire itself -- real behavior independently
    verified by this task's own test suite
    (tests/test_ocid_registry_completion_gate.py), not merely assumed from
    the SQLite docs."""
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS ocid_canonical_registry_completion_ai
        AFTER INSERT ON ocid_canonical_registry
        BEGIN
            UPDATE ocid_canonical_registry SET
                has_real_umr = CASE WHEN NEW.canonical_umr_id IS NOT NULL AND NEW.not_found = 0 THEN 1 ELSE 0 END,
                has_real_pr = CASE WHEN NEW.pr_number IS NOT NULL THEN 1 ELSE 0 END,
                has_real_commit = CASE WHEN NEW.commit_sha IS NOT NULL THEN 1 ELSE 0 END,
                has_real_merge = CASE WHEN NEW.merge_status = 'merged' THEN 1 ELSE 0 END,
                has_real_file_path = CASE WHEN NEW.file_path IS NOT NULL THEN 1 ELSE 0 END,
                has_real_evidence_summary = CASE WHEN NEW.evidence_summary IS NOT NULL AND length(NEW.evidence_summary) > 0 THEN 1 ELSE 0 END,
                not_applicable_confirmed = CASE WHEN NEW.not_found = 1 AND NEW.audit_raw_output IS NOT NULL AND length(NEW.audit_raw_output) > 0 THEN 1 ELSE 0 END,
                is_fully_complete = CASE WHEN
                    (CASE WHEN NEW.canonical_umr_id IS NOT NULL AND NEW.not_found = 0 THEN 1 ELSE 0 END) = 1
                    AND (CASE WHEN NEW.pr_number IS NOT NULL THEN 1 ELSE 0 END) = 1
                    AND (CASE WHEN NEW.commit_sha IS NOT NULL THEN 1 ELSE 0 END) = 1
                    AND (CASE WHEN NEW.merge_status = 'merged' THEN 1 ELSE 0 END) = 1
                    AND (CASE WHEN NEW.file_path IS NOT NULL THEN 1 ELSE 0 END) = 1
                    AND (CASE WHEN NEW.evidence_summary IS NOT NULL AND length(NEW.evidence_summary) > 0 THEN 1 ELSE 0 END) = 1
                    THEN 1 ELSE 0 END
            WHERE rowid = NEW.rowid;
        END;
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS ocid_canonical_registry_completion_au
        AFTER UPDATE ON ocid_canonical_registry
        BEGIN
            UPDATE ocid_canonical_registry SET
                has_real_umr = CASE WHEN NEW.canonical_umr_id IS NOT NULL AND NEW.not_found = 0 THEN 1 ELSE 0 END,
                has_real_pr = CASE WHEN NEW.pr_number IS NOT NULL THEN 1 ELSE 0 END,
                has_real_commit = CASE WHEN NEW.commit_sha IS NOT NULL THEN 1 ELSE 0 END,
                has_real_merge = CASE WHEN NEW.merge_status = 'merged' THEN 1 ELSE 0 END,
                has_real_file_path = CASE WHEN NEW.file_path IS NOT NULL THEN 1 ELSE 0 END,
                has_real_evidence_summary = CASE WHEN NEW.evidence_summary IS NOT NULL AND length(NEW.evidence_summary) > 0 THEN 1 ELSE 0 END,
                not_applicable_confirmed = CASE WHEN NEW.not_found = 1 AND NEW.audit_raw_output IS NOT NULL AND length(NEW.audit_raw_output) > 0 THEN 1 ELSE 0 END,
                is_fully_complete = CASE WHEN
                    (CASE WHEN NEW.canonical_umr_id IS NOT NULL AND NEW.not_found = 0 THEN 1 ELSE 0 END) = 1
                    AND (CASE WHEN NEW.pr_number IS NOT NULL THEN 1 ELSE 0 END) = 1
                    AND (CASE WHEN NEW.commit_sha IS NOT NULL THEN 1 ELSE 0 END) = 1
                    AND (CASE WHEN NEW.merge_status = 'merged' THEN 1 ELSE 0 END) = 1
                    AND (CASE WHEN NEW.file_path IS NOT NULL THEN 1 ELSE 0 END) = 1
                    AND (CASE WHEN NEW.evidence_summary IS NOT NULL AND length(NEW.evidence_summary) > 0 THEN 1 ELSE 0 END) = 1
                    THEN 1 ELSE 0 END
            WHERE rowid = NEW.rowid;
        END;
    """)
    conn.commit()


def upsert_ocid_canonical_registry(conn, ocid_number, *, canonical_umr_id, status,
                                    all_umr_ids, evidence, pr_number=None, pr_repo=None,
                                    duplicate_reason=None, not_found=False,
                                    commit_sha=None, file_name=None, file_path=None,
                                    merge_status=None, evidence_summary=None,
                                    audit_raw_output=None):
    """Real, idempotent per-OCID upsert -- re-running the real search for the
    same OCID and writing the result again is always safe, matching
    upsert_umr_task()'s own ON CONFLICT DO UPDATE convention. `all_umr_ids`
    and `evidence` are real Python lists/dicts, JSON-encoded here so callers
    never hand-serialize. Caller owns conn/transaction/commit, same
    convention as insert_ocid_artifact_link()/update_umr_task() above.

    OCID-068 Phase 2 (UMR-20260805-090549-9710): `commit_sha`, `file_name`,
    `file_path`, `merge_status`, `evidence_summary` are the real new
    dedicated evidence columns (not a duplicate of evidence_json -- see
    _migrate_ocid_canonical_registry_completion_columns()'s own docstring).

    `audit_raw_output` (UMR-20260805-092408-4f97): a real Python dict/list
    (typically resolve_ocid_canonical()'s own `evidence` dict, verbatim),
    JSON-encoded here same as `evidence`/`all_umr_ids` -- intended to be
    written exclusively by audit_ocid_canonical_registry.py, never hand-
    typed. Nothing in this function enforces that provenance by itself
    (this function stays a plain, generic, reusable upsert); the real
    enforcement is the ocid_canonical_registry_completion_ai/_au triggers'
    own not_applicable_confirmed computation, which requires a genuinely
    non-empty audit_raw_output to ever read true, regardless of what any
    caller passes.

    Deliberately no `has_real_*`/`is_fully_complete`/`not_applicable_confirmed`
    parameters here -- those 8 columns are governed exclusively by the
    ocid_canonical_registry_completion_ai/_au triggers
    (_ensure_ocid_canonical_registry_completion_triggers()), which recompute
    and overwrite them from this row's own real underlying columns on every
    INSERT/UPDATE. No caller-supplied value could ever reach them even if one
    were accepted here, so the parameters are omitted entirely rather than
    accepted-and-silently-dropped, to keep the real call contract honest.

    Real evidence_json schema gate (this cycle's directive, citing
    UMR-20260804-170055-a069/UMR-20260805-032326-becc): before writing
    anything, calls refuse_ocid_registry_completion_if_evidence_incomplete()
    -- when `status` genuinely claims completed/verified and `evidence`
    does not satisfy EVIDENCE_JSON_REQUIRED_KEYS, the write is refused: a
    real, permanent 'evidence_schema_refused' audit event is recorded via
    record_ocid_master_standard_audit_event() (same table/function
    apply_certification_verdict() uses for 'certification_refused'), and
    OcidEvidenceSchemaRefused is raised -- no row is written. Rows whose
    status does not itself claim completion (open/running/not_found/etc.)
    are never subject to this gate and are written exactly as before.

    Deliberately does NOT acquire its own _write_lock() around that
    audit-log insert (unlike reconcile_umr_status_against_pr()/
    apply_certification_verdict() above, which self-lock because THEIR own
    callers do not reliably hold one). This function's own established
    convention is the opposite: "Caller owns conn/transaction/commit" (see
    above) -- every real current call site (audit_ocid_canonical_registry.py,
    backfill_ocid_registry_phase2_columns.py,
    backfill_evidence_json_schema.py, and this file's own CLI command) already
    wraps its call to upsert_ocid_canonical_registry() in an outer
    `with _write_lock():`. flock() is per-open-file-description, not
    per-process/re-entrant, so a second nested `with _write_lock():` call
    from inside one of those outer blocks would block forever waiting on a
    lock the same process already holds -- independently confirmed (real
    `timeout` reproduction, this cycle) to hang indefinitely rather than
    raise. The main INSERT below has the exact same caller-owns-the-lock
    contract already; this refusal-path insert matches it rather than
    introducing a second, incompatible locking convention."""
    verdict, reason = refuse_ocid_registry_completion_if_evidence_incomplete(
        ocid_number, status, evidence
    )
    if not verdict:
        _ensure_ocid_master_standard_audit_log_table(conn)
        record_ocid_master_standard_audit_event(
            conn, "evidence_schema_refused",
            {
                "status": status,
                "reason": reason,
                "evidence_keys_present": (
                    sorted(evidence.keys()) if isinstance(evidence, dict) else None
                ),
            },
            ocid_number=ocid_number, umr_id=canonical_umr_id,
        )
        conn.commit()
        raise OcidEvidenceSchemaRefused(reason)

    conn.execute("""
        INSERT INTO ocid_canonical_registry
            (ocid_number, canonical_umr_id, status, pr_number, pr_repo,
             all_umr_ids_json, duplicate_reason, not_found, evidence_json, last_verified_at,
             commit_sha, file_name, file_path, merge_status, evidence_summary, audit_raw_output)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ocid_number) DO UPDATE SET
            canonical_umr_id=excluded.canonical_umr_id,
            status=excluded.status,
            pr_number=excluded.pr_number,
            pr_repo=excluded.pr_repo,
            all_umr_ids_json=excluded.all_umr_ids_json,
            duplicate_reason=excluded.duplicate_reason,
            not_found=excluded.not_found,
            evidence_json=excluded.evidence_json,
            last_verified_at=excluded.last_verified_at,
            commit_sha=excluded.commit_sha,
            file_name=excluded.file_name,
            file_path=excluded.file_path,
            merge_status=excluded.merge_status,
            evidence_summary=excluded.evidence_summary,
            audit_raw_output=excluded.audit_raw_output
    """, (
        ocid_number, canonical_umr_id, status, pr_number, pr_repo,
        json.dumps(all_umr_ids), duplicate_reason, 1 if not_found else 0,
        json.dumps(evidence), _now_iso(),
        commit_sha, file_name, file_path, merge_status, evidence_summary,
        json.dumps(audit_raw_output, sort_keys=True, default=str) if audit_raw_output is not None else None,
    ))


def query_ocid_canonical_registry(conn, ocid_number=None):
    """Real lookup -- a single OCID's real canonical-UMR row, or the whole
    real roster (ordered by ocid_number) when called with no argument. Uses
    `SELECT *`, so the OCID-068 Phase 2 (UMR-20260805-090549-9710) real
    columns -- commit_sha, file_name, file_path, merge_status,
    evidence_summary, and the 7 trigger-computed has_real_*/is_fully_complete
    gate columns -- are already included in every returned row with no
    change needed here."""
    if ocid_number:
        rows = conn.execute(
            "SELECT * FROM ocid_canonical_registry WHERE ocid_number=?", (ocid_number,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM ocid_canonical_registry ORDER BY ocid_number"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["all_umr_ids"] = json.loads(d.pop("all_umr_ids_json"))
        d["evidence"] = json.loads(d.pop("evidence_json"))
        if d.get("audit_raw_output") is not None:
            d["audit_raw_output"] = json.loads(d["audit_raw_output"])
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# OCID Master Standard v6 -- Phase 1 (UMR-20260805-042152-e559, Owner
# directive; parent references UMR-20260804-170055-a069, canonical OCID-068
# UMR, real status completed, and UMR-20260805-032731-b412, OCID-068
# permanent closure record, real status completed, PR #52 merge commit
# c46da9b777e2a8a60e15230dacd72f2329e885af). This is a deliberately narrow
# first slice of the full "VERIDIAN Deterministic OCID Master Standard
# version six" the Owner directive describes -- three real corrections
# named as real problems hit this session, plus one minimal real append-only
# audit log, NOT the full 11-state lifecycle machine, ownership-chain
# resolution, universal artifact graph, bootstrap/checkpoint-recovery
# sequencing, registry integrity checks, or the strict-JSON-only automated
# output contract (all explicitly deferred -- see
# OCID_MASTER_STANDARD_V6_PHASE1_2026-08-05.md at the repo root for the
# honest scope/phasing writeup).
#
# resolve_ocid_canonical() below reuses and locks down, as one canonical
# implementation, the ad-hoc OCID-verification methodology that already
# informed PR #53's ocid_canonical_registry table (see
# upsert_ocid_canonical_registry/query_ocid_canonical_registry above) --
# this function is the real, callable, testable version of that same
# methodology, not a second competing one.
# ---------------------------------------------------------------------------

_UMR_ID_RE = re.compile(r"UMR-\d{8}-\d{6}-[0-9a-f]{4}")

DEFAULT_OCID_RESOLVER_REPOS = ("compliance-tracker", "veridian-scripts", "projexa")

DEFAULT_OCID_RESOLVER_REPO_LOCAL_PATHS = {
    "compliance-tracker": "/opt/veridian/repos/compliance-tracker",
    # 2026-08-13 (task-20260813-103224, UMR-20260813-101142-5d24): points at
    # the real live checkout, not /opt/veridian/repos/veridian-scripts --
    # that second checkout is orphaned (nothing has pulled it since
    # 2026-08-06, confirmed 200 commits behind origin/main). /opt/veridian/scripts
    # IS a real veridian-scripts checkout, kept current every 2h by
    # sync-repos.sh's direct `git pull --ff-only`.
    "veridian-scripts": "/opt/veridian/scripts",
    "projexa": "/opt/veridian/repos/projexa",
    # UMR-20260813-115911-df5c (real root cause behind this same UMR's own
    # repeated redispatch loop, task-20260813-140326): governance/meta-repo
    # tasks (task.yaml `repo: claude-control`, e.g. every RCA/routing-fix task
    # dispatched against this repo itself) had NO entry here, so
    # mark-umr-terminal's own --repo argparse `choices=list(...)` rejected
    # "claude-control" outright and reconcile_stale_running_workers.py's own
    # parallel REPO_LOCAL_PATHS dict (see that file) could never resolve a
    # local checkout for `git ls-remote`/commit-ancestor verification. Real,
    # live effect confirmed: UMR-20260813-115911-df5c's own worker units kept
    # going inactive at pending_review/blocked with zero real completion
    # candidate ever resolvable, so STEP 3's sweep fell through to "genuinely
    # ambiguous -- real re-queue" every single time, forcing a brand-new
    # duplicate dispatch (task-20260813-132414 -> -135613 -> -140326) of
    # already-completed work instead of ever reaching a terminal status.
    # /opt/veridian/repos/claude-control is the real, already-existing local
    # checkout (origin https://github.com/FChecklist/claude-control.git,
    # confirmed live) -- just never wired into this dict.
    "claude-control": "/opt/veridian/repos/claude-control",
}


def _ensure_ocid_master_standard_audit_log_table(conn):
    """Real, minimal, append-only audit log for OCID Master Standard v6 Phase
    1 -- deliberately NOT the full standard's own generic append-only
    registry-mutation log (explicitly out of scope, see the module comment
    above and OCID_MASTER_STANDARD_V6_PHASE1_2026-08-05.md), just a real,
    working, durable trail for the two real event kinds this PR's own
    functions produce (status_reconciliation, certification_refused), so a
    stale-status finding or a certification refusal is never only a
    transient function return value. Same idempotent CREATE TABLE IF NOT
    EXISTS convention as every other _ensure_*_table function in this file;
    called from _migrate_schema() the same way. Genuinely append-only:
    record_ocid_master_standard_audit_event() below only ever INSERTs, never
    UPDATEs/DELETEs any row here."""
    conn.execute("""CREATE TABLE IF NOT EXISTS ocid_master_standard_audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ocid_number TEXT,
        umr_id TEXT,
        event_type TEXT NOT NULL,
        detail_json TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ocid_master_standard_audit_umr "
                 "ON ocid_master_standard_audit_log(umr_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ocid_master_standard_audit_ocid "
                 "ON ocid_master_standard_audit_log(ocid_number)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ocid_master_standard_audit_event_type "
                 "ON ocid_master_standard_audit_log(event_type)")
    conn.commit()


def record_ocid_master_standard_audit_event(conn, event_type, detail, ocid_number=None, umr_id=None):
    """Genuinely append-only real event record: only ever INSERTs. `detail`
    is a real Python dict, JSON-encoded here so callers never hand-serialize
    (same convention as upsert_ocid_canonical_registry's evidence/all_umr_ids
    handling above). Does NOT commit by default -- callers that already own
    an outer transaction (e.g. a future CLI cmd_* wrapper under
    _write_lock()) should commit themselves; reconcile_umr_status_against_pr()
    below calls conn.commit() itself right after this insert, since that
    call site does not open its own outer transaction."""
    conn.execute(
        "INSERT INTO ocid_master_standard_audit_log "
        "(ocid_number, umr_id, event_type, detail_json, recorded_at) VALUES (?, ?, ?, ?, ?)",
        (ocid_number, umr_id, event_type, json.dumps(detail), _now_iso()),
    )


def _default_ocid_resolver_runner(cmd, cwd=None):
    """Real default subprocess runner -- the only place this module actually
    shells out to gh/git for OCID resolution. Every helper below takes this
    (or a test double) as an explicit `_runner` parameter rather than calling
    subprocess.run directly, so real unit tests never spawn a real
    subprocess or depend on real network/gh-auth state (same testability
    requirement, and same injectable-callable pattern, as this repo's own
    JS precedent scripts/check-sec07-ocid-lock.mjs's evaluate() in the
    sibling compliance-tracker repo, which splits pure decision logic from
    the I/O that feeds it)."""
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)


def _umr_terminal_commit_exists(repo_root, sha, _runner=None):
    """UMR-20260806-130914-e7f1: real existence check for a commit object --
    `git cat-file -e <sha>^{commit}` -- used by cmd_mark_umr_terminal's
    structured-evidence gate to confirm a caller-supplied --commit-sha is a
    real commit object this repo checkout actually has (fetched fresh first),
    not a fabricated/garbage hex string. Fails closed (returns False) on any
    error/timeout -- a real completion write must never proceed on an
    unverifiable claim, same fail-closed convention as
    _is_umr_terminal_commit_ancestor_of_main below."""
    runner = _runner or _default_ocid_resolver_runner
    if not sha or not repo_root or not os.path.isdir(repo_root):
        return False
    try:
        runner(["git", "fetch", "origin", sha], cwd=repo_root)
    except Exception:
        pass
    try:
        result = runner(["git", "cat-file", "-e", sha + "^{commit}"], cwd=repo_root)
        return result.returncode == 0
    except Exception:
        return False


def _is_umr_terminal_commit_ancestor_of_main(repo_root, sha, _runner=None):
    """UMR-20260806-130914-e7f1: real merge-base ancestry check for
    cmd_mark_umr_terminal's completed-status gate -- mirrors
    triage_owner_umr_24h.py's own is_commit_on_main(), the only signal this
    codebase already trusts that a commit is genuinely on main (never a PR's
    mergedAt field alone, and never gh's --json state field alone: a PR can
    be merged into a non-main base, or a base later force-reset). Fetches
    the real default branch and the target sha fresh (best-effort -- a
    fetch failure still lets the subsequent merge-base call fail honestly
    rather than trusting a stale local ref) before the real check. Fails
    closed: any subprocess/timeout error is treated as 'not confirmed an
    ancestor', never as 'assume merged' -- this is the one check
    status=completed's real artifact requirement is not allowed to get
    wrong in the optimistic direction.

    UMR-20260813-141633-f0fc real fix: this used to hardcode
    'origin/main', but not every repo this gate runs against actually uses
    'main' as its default branch -- claude-control's real default branch is
    'master' (confirmed live: `git remote show origin` HEAD branch, and
    independently via `git merge-base --is-ancestor` against
    `origin/master`). That meant every genuinely-merged claude-control
    commit failed this check and got silently downgraded to
    completed_unmerged (or refused outright for --status completed) even
    though it was truly merged -- live-confirmed against
    UMR-20260813-141633-f0fc/commit d9f0c7c (real PR #167, merged, state
    MERGED, and a real ancestor of origin/master) which
    reconcile_stale_running_workers.py had recorded as completed_unmerged
    for exactly this reason. Now resolves the real default branch per repo
    (`git symbolic-ref refs/remotes/origin/HEAD`, same real signal
    _git_default_branch() below already uses for worktree/PR creation),
    falling back to 'main' only if that lookup itself fails."""
    runner = _runner or _default_ocid_resolver_runner
    if not sha or not repo_root or not os.path.isdir(repo_root):
        return False
    default_branch = "main"
    try:
        head_ref = runner(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], cwd=repo_root)
        if head_ref.returncode == 0 and head_ref.stdout.strip():
            default_branch = head_ref.stdout.strip().rsplit("/", 1)[-1]
    except Exception:
        pass
    try:
        runner(["git", "fetch", "origin", default_branch], cwd=repo_root)
    except Exception:
        pass
    try:
        result = runner(["git", "merge-base", "--is-ancestor", sha, f"origin/{default_branch}"], cwd=repo_root)
        return result.returncode == 0
    except Exception:
        return False


def _ocid_casing_variants(ocid_number):
    """Real requirement (methods a/b below): try multiple real casings/
    separators of the OCID number -- e.g. OCID-038 / ocid-038 / ocid_038 /
    OCID038 -- since umr_tasks.task_identity and other free-text columns are
    not written under one single enforced casing convention."""
    m = re.search(r"(\d+)", ocid_number)
    digits = m.group(1) if m else None
    variants = {ocid_number, ocid_number.upper(), ocid_number.lower()}
    if digits:
        base = f"OCID-{digits}"
        variants |= {
            base, base.lower(),
            base.replace("-", "_"), base.replace("-", "_").lower(),
            base.replace("-", ""), base.replace("-", "").lower(),
        }
    return sorted(variants)


def _umr_tasks_substring_query(conn, ocid_number):
    """Method (a): real live umr_tasks.task_identity substring match, tried
    against multiple real casings/separators of the OCID number. Returns a
    dict umr_id -> task_identity for every real row matched (empty dict, not
    None, when nothing matches -- callers record that honestly rather than
    treating an empty result as an error)."""
    matched = {}
    for variant in _ocid_casing_variants(ocid_number):
        rows = conn.execute(
            "SELECT umr_id, task_identity FROM umr_tasks WHERE task_identity LIKE ?",
            (f"%{variant}%",),
        ).fetchall()
        for r in rows:
            matched[r["umr_id"]] = r["task_identity"]
    return matched


_UMR_TASKS_TEXT_COLUMNS = (
    "umr_id", "task_identity", "source_trigger", "unit_name",
    "inputs_json", "outputs_json", "logs_ref", "reason", "metadata_json",
)


def _umr_tasks_full_dump_grep(conn, ocid_number):
    """Method (b): a real full dump + case-insensitive grep of every real
    text column of the ENTIRE umr_tasks table -- never rely on (a)'s
    substring/fuzzy search alone. This exact gap (an OCID string present in
    outputs_json/metadata_json/reason/logs_ref rather than task_identity,
    so (a) alone finds nothing) caused real missed matches this session for
    OCID-022, OCID-023, OCID-058, and OCID-060. Returns a dict
    umr_id -> the real matched row text (for evidence), empty dict when
    nothing matches anywhere."""
    variants = [v.lower() for v in _ocid_casing_variants(ocid_number)]
    cols_sql = ", ".join(_UMR_TASKS_TEXT_COLUMNS)
    rows = conn.execute(f"SELECT {cols_sql} FROM umr_tasks").fetchall()
    matched = {}
    for r in rows:
        row_text = " ".join(str(r[c]) for c in _UMR_TASKS_TEXT_COLUMNS if r[c] is not None)
        row_text_lower = row_text.lower()
        if any(v in row_text_lower for v in variants):
            matched[r["umr_id"]] = row_text
    return matched


def _gh_pr_search(ocid_number, repo, _runner):
    """Method (c): real `gh pr list --repo FChecklist/<repo> --state all
    --search "<OCID> in:title,body"` -- commit-message/git-log search alone
    misses real documentation-only PRs, a real gap found this session.
    Returns {"ok": bool, "prs": [...]} -- "prs" is the real parsed
    --json output on success, [] on any real failure (never raises)."""
    cmd = [
        "gh", "pr", "list", "--repo", f"FChecklist/{repo}", "--state", "all",
        "--search", f"{ocid_number} in:title,body",
        "--json", "number,title,body,url,state,mergedAt",
    ]
    try:
        result = _runner(cmd, None)
    except Exception as exc:  # pragma: no cover -- real subprocess/env failure, never silently swallowed
        return {"ok": False, "error": str(exc), "prs": []}
    if getattr(result, "returncode", 1) != 0:
        return {"ok": False, "error": getattr(result, "stderr", ""), "prs": []}
    try:
        prs = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError, AttributeError):
        prs = []
    return {"ok": True, "prs": prs}


def _git_log_grep(ocid_number, repo, _runner, repo_path=None):
    """Method (d): `git log --all --oneline -i --grep=<OCID>`, used only as
    a real cross-check -- never the sole source, since it misses real
    documentation-only PRs the way (c) does not."""
    path = repo_path or DEFAULT_OCID_RESOLVER_REPO_LOCAL_PATHS.get(repo)
    cmd = ["git", "log", "--all", "--oneline", "-i", f"--grep={ocid_number}"]
    try:
        result = _runner(cmd, path)
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "error": str(exc), "lines": []}
    if getattr(result, "returncode", 1) != 0:
        return {"ok": False, "error": getattr(result, "stderr", ""), "lines": []}
    lines = [line for line in (result.stdout or "").splitlines() if line.strip()]
    return {"ok": True, "lines": lines}


def _extract_umr_ids(text):
    """Method (e): extract real UMR IDs (regex UMR-\\d{8}-\\d{6}-[0-9a-f]{4})
    from real matched PR body/title text. Sorted + de-duplicated; sorted
    order is also chronological order since the UMR ID format is
    timestamp-prefixed."""
    if not text:
        return []
    return sorted(set(_UMR_ID_RE.findall(text)))


def _master_tracker_and_active_claims_grep(ocid_number, _runner, repo_path=None):
    """Method (f): MASTER-TRACKER.yaml/ACTIVE-CLAIMS.yaml (compliance-tracker
    repo) grep -- real last resort, only consulted by resolve_ocid_canonical()
    below when methods (a)-(e) found nothing at all. Returns a dict
    filename -> list of real matched lines (empty list = real, honest zero
    matches for that file; a leading "error: ..." dict value means the file
    itself could not be read, distinct from a real empty match)."""
    path = repo_path or DEFAULT_OCID_RESOLVER_REPO_LOCAL_PATHS["compliance-tracker"]
    found = {}
    for fname in ("ai-os/MASTER-TRACKER.yaml", "ai-os/registry/ACTIVE-CLAIMS.yaml",
                  "MASTER-TRACKER.yaml", "ACTIVE-CLAIMS.yaml"):
        full_path = os.path.join(path, fname)
        # Real fix (independent review, round 1): "--" before the pattern so
        # an ocid_number value starting with "-" is never misparsed as a
        # grep flag instead of a pattern.
        cmd = ["grep", "-i", "--", ocid_number, full_path]
        try:
            result = _runner(cmd, None)
        except Exception as exc:  # pragma: no cover
            found[fname] = f"error: {exc}"
            continue
        returncode = getattr(result, "returncode", 1)
        if returncode == 0:
            found[fname] = [line for line in (result.stdout or "").splitlines() if line.strip()]
        elif returncode == 1:
            found[fname] = []  # real, honest zero matches (grep's own convention)
        else:
            found[fname] = f"error: {getattr(result, 'stderr', '') or ('grep exit ' + str(returncode))}"
    return found


def resolve_ocid_canonical(ocid_number, conn, repos=DEFAULT_OCID_RESOLVER_REPOS,
                            _runner=None, repo_paths=None):
    """The one locked canonical implementation of the real OCID-verification
    methodology (UMR-20260805-042152-e559 Phase 1), run in this exact
    order/precedence:
      (a) real umr_tasks.task_identity substring match, multiple casings
      (b) real full dump + grep of every umr_tasks text column (never (a)
          alone -- see _umr_tasks_full_dump_grep's own docstring for the
          real OCID-022/023/058/060 gap this closes)
      (c) real `gh pr list --search "<OCID> in:title,body"` across all
          `repos`, --state all (catches real documentation-only PRs)
      (d) real `git log --all --oneline -i --grep=<OCID>` across `repos`,
          used only as a cross-check, never the sole source
      (e) real UMR ID extraction from matched PR body/title text
      (f) real MASTER-TRACKER.yaml/ACTIVE-CLAIMS.yaml grep, last resort
          only, consulted only if (a)-(e) found nothing at all

    Returns a dict shaped to be passed straight into
    upsert_ocid_canonical_registry(conn, ocid_number, **result) (minus the
    ocid_number key itself, which the return value also carries for the
    caller's own convenience/logging):
      ocid_number, canonical_umr_id, status, not_found, all_umr_ids,
      evidence, pr_number, pr_repo, duplicate_reason

    If more than one distinct UMR ID is found for this OCID, ALL of them are
    returned in all_umr_ids, plus an explicit canonical_umr_id choice (the
    chronologically-earliest, since the UMR ID format is timestamp-prefixed)
    and a non-None duplicate_reason explaining that choice has NOT been
    human-reviewed -- this function never silently picks one and hides the
    rest. If truly nothing is found after every method, not_found=True is
    returned with per-method evidence of the real empty search recorded in
    `evidence` -- fields are never left blank or guessed.

    Every real subprocess call (gh/git/grep) goes through the injectable
    `_runner` callable (default: real subprocess.run via
    _default_ocid_resolver_runner) so this function -- and every small pure
    helper it calls -- is fully unit-testable without spawning a real
    subprocess, same pattern as this repo's own JS precedent
    scripts/check-sec07-ocid-lock.mjs's evaluate() (sibling compliance-tracker
    repo)."""
    runner = _runner or _default_ocid_resolver_runner
    repo_paths = repo_paths or DEFAULT_OCID_RESOLVER_REPO_LOCAL_PATHS
    evidence = {}
    all_umr_ids = set()

    # (a)
    substring_matches = _umr_tasks_substring_query(conn, ocid_number)
    evidence["umr_tasks_task_identity_substring"] = substring_matches if substring_matches else "zero rows"
    all_umr_ids.update(substring_matches.keys())

    # (b)
    dump_matches = _umr_tasks_full_dump_grep(conn, ocid_number)
    evidence["umr_tasks_full_dump_grep"] = dump_matches if dump_matches else "zero rows"
    all_umr_ids.update(dump_matches.keys())

    # (c)
    pr_hits = []  # list of (repo, pr_dict)
    for repo in repos:
        res = _gh_pr_search(ocid_number, repo, runner)
        evidence[f"gh_pr_search_{repo}"] = res
        if res.get("ok"):
            for pr in res.get("prs", []):
                pr_hits.append((repo, pr))

    # (d) -- cross-check only, never the sole source of a found/not-found decision
    for repo in repos:
        res = _git_log_grep(ocid_number, repo, runner, repo_paths.get(repo))
        evidence[f"git_log_{repo}"] = res.get("lines") if res.get("ok") else res

    # (e)
    pr_umr_ids = set()
    canonical_pr_number = None
    canonical_pr_repo = None
    for repo, pr in pr_hits:
        text = f"{pr.get('title', '')}\n{pr.get('body', '')}"
        found = _extract_umr_ids(text)
        if found:
            pr_umr_ids.update(found)
            if canonical_pr_number is None:
                canonical_pr_number = pr.get("number")
                canonical_pr_repo = repo
    all_umr_ids.update(pr_umr_ids)
    evidence["umr_ids_extracted_from_pr_bodies"] = sorted(pr_umr_ids) if pr_umr_ids else "zero matches"

    # (f) -- real last resort, only if nothing found by (a)-(e)
    if not all_umr_ids:
        mt = _master_tracker_and_active_claims_grep(ocid_number, runner, repo_paths.get("compliance-tracker"))
        evidence["master_tracker_and_active_claims_grep"] = mt
        for lines in mt.values():
            if isinstance(lines, list):
                for line in lines:
                    all_umr_ids.update(_extract_umr_ids(line))
    else:
        evidence["master_tracker_and_active_claims_grep"] = (
            "skipped -- real last resort only, methods (a)-(e) already found a real match"
        )

    all_umr_ids = sorted(all_umr_ids)

    if not all_umr_ids:
        return {
            "ocid_number": ocid_number, "canonical_umr_id": None,
            "status": "not_found", "not_found": True, "all_umr_ids": [],
            "evidence": evidence, "pr_number": None, "pr_repo": None,
            "duplicate_reason": None,
        }

    if len(all_umr_ids) == 1:
        return {
            "ocid_number": ocid_number, "canonical_umr_id": all_umr_ids[0],
            "status": "found", "not_found": False, "all_umr_ids": all_umr_ids,
            "evidence": evidence, "pr_number": canonical_pr_number,
            "pr_repo": canonical_pr_repo, "duplicate_reason": None,
        }

    # multiple distinct real UMR IDs found -- never silently pick one
    canonical_choice = all_umr_ids[0]
    return {
        "ocid_number": ocid_number, "canonical_umr_id": canonical_choice,
        "status": "multiple_umr_ids_found_needs_review", "not_found": False,
        "all_umr_ids": all_umr_ids, "evidence": evidence,
        "pr_number": canonical_pr_number, "pr_repo": canonical_pr_repo,
        "duplicate_reason": (
            f"resolve_ocid_canonical() found {len(all_umr_ids)} distinct real UMR IDs for "
            f"{ocid_number}: {', '.join(all_umr_ids)}. Defaulted canonical_umr_id to the "
            f"chronologically-earliest by UMR-ID timestamp ordering ({canonical_choice}) -- "
            "this default has NOT been human-reviewed and must be confirmed or corrected "
            "before being treated as final, per the real duplicate-UMR gap found this "
            "session for OCID-022, OCID-023, OCID-058, and OCID-060."
        ),
    }


def _find_pr_evidence_for_umr(conn, umr_id, repos, runner):
    """Real helper for reconcile_umr_status_against_pr()'s live-search path:
    finds the real OCID number(s) mentioned in this umr_id's own umr_tasks
    row, then reuses _gh_pr_search (the same method (c) resolve_ocid_canonical
    uses) per repo/OCID, keeping only PRs whose own real title/body actually
    mentions this exact umr_id -- so this never proposes a correction based
    on a PR for someone else's UMR that merely shares the same OCID."""
    row = conn.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
    if row is None:
        return []
    row_text = " ".join(str(row[c]) for c in row.keys() if row[c] is not None)
    ocid_numbers = sorted(set(re.findall(r"OCID-\d+", row_text, re.IGNORECASE)))
    evidence = []
    for repo in repos:
        for ocid in ocid_numbers:
            res = _gh_pr_search(ocid, repo, runner)
            if not res.get("ok"):
                continue
            for pr in res.get("prs", []):
                text = f"{pr.get('title', '')}\n{pr.get('body', '')}"
                if umr_id in text:
                    evidence.append({"repo": repo, **pr})
    return evidence


def reconcile_umr_status_against_pr(conn, umr_id, pr_evidence=None, _runner=None,
                                     repos=DEFAULT_OCID_RESOLVER_REPOS):
    """Cross-checks a given real UMR's real umr_tasks.status/ts_completed
    against real, independently-found PR-merge evidence -- directly targets
    the exact real bug class just found and fixed for
    UMR-20260805-032731-b412 (canonical UMR stuck at 'running'/ts_completed
    null despite the real underlying work being done and the real PR merged),
    and (task-20260814-080739) the identical bug shape for status=
    'completed_unmerged' -- a real commit that was genuinely still unmerged
    at write time but whose PR has since merged, with nothing that ever
    re-checked it (see this function's own stale_statuses comment below for
    a live-confirmed real instance).

    `pr_evidence` may be pre-fetched by the caller (a list of dicts with at
    least "state"/"mergedAt" keys, e.g. gh's own --json output shape) for
    fully deterministic/offline testing; when omitted, this function does a
    real live search via _find_pr_evidence_for_umr() (reusing
    resolve_ocid_canonical's own PR-finding method (c)).

    Never silently auto-applies a correction -- returns
    {is_stale, current_status, proposed_status, proposed_ts_completed,
    evidence} and leaves it to the caller to actually apply the correction
    via the existing real update_umr_task(), consistent with how
    UMR-20260805-024319-b1e6's earlier real correction was done. When a real
    stale status is found, this function DOES record a real, permanent
    'status_reconciliation' audit event via
    record_ocid_master_standard_audit_event() (and commits it) -- the
    finding itself is real and durable even though the correction is not
    auto-applied."""
    row = conn.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
    if row is None:
        return {
            "umr_id": umr_id, "is_stale": False, "current_status": None,
            "proposed_status": None, "proposed_ts_completed": None,
            "evidence": {"error": f"no real umr_tasks row found for umr_id={umr_id}"},
        }

    current_status = row["status"]
    current_ts_completed = row["ts_completed"]

    if pr_evidence is None:
        runner = _runner or _default_ocid_resolver_runner
        pr_evidence = _find_pr_evidence_for_umr(conn, umr_id, repos, runner)

    merged_prs = [pr for pr in pr_evidence if pr.get("state") == "MERGED" or pr.get("mergedAt")]

    if not merged_prs:
        return {
            "umr_id": umr_id, "is_stale": False, "current_status": current_status,
            "proposed_status": None, "proposed_ts_completed": None,
            "evidence": {"pr_evidence": pr_evidence,
                         "note": "no real merged-PR evidence found -- no reconciliation needed"},
        }

    merged_prs_sorted = sorted(merged_prs, key=lambda p: p.get("mergedAt") or "")
    completing_pr = merged_prs_sorted[0]
    merged_at = completing_pr.get("mergedAt")

    # task-20260814-080739 real fix ("close the completed_unmerged dead end"):
    # this used to be {"queued", "dispatched", "running"} only, which meant a
    # row correctly written as completed_unmerged (real commit, genuinely not
    # yet an ancestor of main/master at write time -- see
    # validate_umr_terminal_completion_evidence()'s own docstring) NEVER got
    # re-checked once its PR actually merged: this same function's own
    # real, independently-found merged-PR evidence was sitting right here on
    # every subsequent call and nothing ever consulted it for that one
    # status. Live-confirmed real instance: UMR-20260814-054218-9475, recorded
    # completed_unmerged at 06:02:44Z citing commit 5e9f6dea (PR #209, not yet
    # merged at that moment) -- PR #209 merged for real at 07:16:24Z and nothing
    # ever revisited the row; only this sweep's manual mark-umr-terminal call
    # closed it. completed_unmerged is included here now so
    # --apply performs the exact same real promotion (via the same
    # update_umr_task() write below) that queued/dispatched/running rows
    # already got -- completed_unmerged stops being a silent dead end and
    # becomes self-healing the same way every other stale status already is.
    stale_statuses = {"queued", "dispatched", "running", "completed_unmerged"}
    is_stale = current_status in stale_statuses

    result = {
        "umr_id": umr_id,
        "is_stale": is_stale,
        "current_status": current_status,
        "proposed_status": "completed" if is_stale else current_status,
        "proposed_ts_completed": merged_at if is_stale else current_ts_completed,
        "evidence": {"pr_evidence": pr_evidence, "completing_pr": completing_pr},
    }

    if is_stale:
        # Real fix (independent review, round 1): this INSERT+commit is a
        # real write path -- MUST go through _write_lock() like every other
        # write path in this file (superboss-register.py:176-199), or it
        # risks repeating the exact documented 2026-07-23 SQLite corruption
        # incident (3 distinct signatures): a concurrent writer contending
        # for SQLite's own write lock inside its 30s busy_timeout, SIGKILLed
        # by an outer caller after only 10s, corrupting b-tree pages
        # mid-transaction. This function is called unconditionally (not
        # only from an --apply CLI path), so the lock is acquired here,
        # scoped to just this write, rather than relying on a caller that
        # may not always wrap it.
        with _write_lock():
            record_ocid_master_standard_audit_event(
                conn, "status_reconciliation",
                {
                    "current_status": current_status,
                    "proposed_status": result["proposed_status"],
                    "proposed_ts_completed": result["proposed_ts_completed"],
                    "evidence": result["evidence"],
                },
                umr_id=umr_id,
            )
            conn.commit()

    return result


def refuse_certification_if_merged_without_required_checks(pr_merge_record):
    """Pure function, zero I/O (no live GitHub API calls) -- this standard's
    own independent, redundant certification-refusal logic, operationalizing
    the real branch-protection incident (compliance-tracker PR #932 merged
    at a real failing 'Metadata Index Coverage Check' state with zero real
    reviews; PR #933 the same) that UMR-20260805-034917-33a9 already fixed
    going forward at the GitHub-settings level. This function is a second,
    redundant layer that would have refused to certify either of those two
    real merges even though GitHub itself let them merge.

    pr_merge_record (explicit structured input, no live calls made here):
      {
        "repo": str, "pr_number": int, "merged_at": str,
        "required_status_checks": [{"name": str, "conclusion": str}, ...],
        "approving_reviews_count": int,
        "required_approving_review_count": int,
      }
    "conclusion" values treated as passing: success/neutral/skipped (case-
    insensitive) -- anything else (failure, cancelled, timed_out, action_required,
    stale, or missing) is treated as not passing and refuses certification.

    Returns (verdict: bool, reason: str) -- verdict True means certification
    is allowed; False means refused, with `reason` naming every real cause."""
    repo = pr_merge_record.get("repo")
    pr_number = pr_merge_record.get("pr_number")
    checks = pr_merge_record.get("required_status_checks") or []
    passing_conclusions = {"success", "neutral", "skipped"}
    failing_checks = [
        c for c in checks
        if str(c.get("conclusion", "")).lower() not in passing_conclusions
    ]
    reviews_count = pr_merge_record.get("approving_reviews_count", 0)
    required_reviews = pr_merge_record.get("required_approving_review_count", 1)

    reasons = []
    if failing_checks:
        names = ", ".join(c.get("name", "unknown") for c in failing_checks)
        reasons.append(f"required status check(s) not passing at merge time: {names}")
    if reviews_count < required_reviews:
        reasons.append(
            f"approving review count ({reviews_count}) below required "
            f"({required_reviews}) at merge time"
        )

    if reasons:
        reason = f"REFUSED certification for {repo}#{pr_number}: " + "; ".join(reasons) + "."
        return False, reason

    return True, (
        f"Certification allowed for {repo}#{pr_number}: all required status checks "
        "passing and required approving review count met at merge time."
    )


def apply_certification_verdict(conn, pr_merge_record):
    """Real caller-side usage pattern for
    refuse_certification_if_merged_without_required_checks(): calls the pure
    function, and when it refuses, records a real, permanent
    'certification_refused' audit event via
    record_ocid_master_standard_audit_event() (and commits it) so the
    refusal has a durable trail, not just a transient return value. Returns
    the same (verdict, reason) tuple the pure function returns.

    Real fix (independent review, round 1): this function's own audit-log
    INSERT+commit is a real write path and, like every other write path in
    this file (superboss-register.py:176-199), MUST be acquired via
    _write_lock() -- not doing so risks repeating the exact documented
    2026-07-23 SQLite corruption incident. Callers (e.g. cmd_certify_pr_merge)
    do not need to wrap this call in their own _write_lock() -- the lock is
    acquired and released here, fully, before this function returns."""
    verdict, reason = refuse_certification_if_merged_without_required_checks(pr_merge_record)
    if not verdict:
        with _write_lock():
            record_ocid_master_standard_audit_event(
                conn, "certification_refused",
                {
                    "repo": pr_merge_record.get("repo"),
                    "pr_number": pr_merge_record.get("pr_number"),
                    "merged_at": pr_merge_record.get("merged_at"),
                    "reason": reason,
                },
            )
            conn.commit()
    return verdict, reason


# ---------------------------------------------------------------------------
# PR review/authoring identity independence check (Owner directive, this
# cycle; UMR-20260805-034917-33a9 lineage, Owner Decision OD-20260805-001:
# future violations of the same class must be blocked before merge, not
# only detected afterward).
#
# Real finding this cycle, independently verified against the live GitHub
# API (not assumed from the dispatch text): compliance-tracker's real branch
# protection currently has required_approving_review_count=0 (not 1 as the
# originating directive claimed), FChecklist is the sole real collaborator
# on the repo (admin, the only account with any permission at all), and
# every credential present in this environment (`gh auth status` default,
# $GITHUB_PAT, $GITHUB_PAT_ZAI_KIMI) independently resolves via a live
# `GET /user` call to that exact same account login. There were 100+ real
# open PRs at check time (`gh api` pagination), not "roughly 12". None of
# this contradicts the underlying problem the directive names -- a single
# account is genuinely both the sole author and the only possible approver
# of every PR -- but it does mean the specific numbers in the directive were
# stale/inaccurate, and, more importantly, that no second, genuinely
# independent credential exists anywhere in this environment to provision
# from. See OCID_070_SECOND_REVIEWER_IDENTITY_PROVISIONING_FINDING_2026-08-05.md
# for the full writeup, including why actually provisioning a second GitHub
# identity (a new personal account needing real human/email verification, or
# a GitHub App needing an interactive browser session to create and download
# its private key) is a one-time action only a human with GitHub web-UI
# access can complete -- not something achievable from headless API/CLI
# tools alone, and explicitly NOT something this function or its caller
# attempt to fake by reusing the existing FChecklist credential under a new
# label.
#
# What this section DOES deliver now, ahead of that human step: the actual
# identity-independence check itself, as a real, structural, non-bypassable
# gate ready to wire in the moment a genuinely independent reviewing
# identity exists. Same "second independent layer, explicit structured
# input, no live I/O inside the pure function" design as
# refuse_certification_if_merged_without_required_checks() above, and the
# same audit-log wiring (record_ocid_master_standard_audit_event(),
# event_type="review_identity_independence_refused") as
# apply_certification_verdict() uses for event_type="certification_refused".
#
# Deliberately NOT wired into branch protection yet: flipping
# required_approving_review_count to 1 before a second identity is actually
# installed as a collaborator would immediately block 100% of future PRs
# (GitHub already forbids self-approval), which is a regression against
# OD-20260805-001's stated goal of unblocking queued work, not a fix.
# ---------------------------------------------------------------------------

def refuse_review_if_reviewer_is_author(pr_review_record):
    """Pure function, zero I/O (no live GitHub API calls) -- refuses to
    certify a PR's review as independent if the approving reviewer and the
    PR author are the same real account on any given PR. This is the
    "automated check confirming the real reviewing identity and the real
    authoring identity are never the same account" required by this cycle's
    Owner directive.

    pr_review_record (explicit structured input, no live calls made here):
      {
        "repo": str, "pr_number": int,
        "author_login": str,
        "approving_review_logins": [str, ...],
      }
    Login comparison is case-insensitive (GitHub logins are
    case-insensitive but not guaranteed to be cased consistently across API
    responses) and whitespace-trimmed.

    Returns (verdict: bool, reason: str) -- verdict True means at least one
    approving review came from a genuinely different account than the
    author (independent review exists); False means every approving review
    (if any) was authored by the same account as the PR itself, with
    `reason` naming the real cause."""
    repo = pr_review_record.get("repo")
    pr_number = pr_review_record.get("pr_number")
    author = str(pr_review_record.get("author_login") or "").strip().lower()
    reviewers = [
        str(login or "").strip().lower()
        for login in (pr_review_record.get("approving_review_logins") or [])
    ]

    independent_reviewers = [r for r in reviewers if r and r != author]
    self_reviews = [r for r in reviewers if r and r == author]

    if independent_reviewers:
        return True, (
            f"Independent review confirmed for {repo}#{pr_number}: "
            f"approved by {independent_reviewers[0]} (author: {author})."
        )

    if self_reviews:
        reason = (
            f"REFUSED for {repo}#{pr_number}: approving reviewer "
            f"'{author}' is the same account as the PR author -- "
            "self-approval is not independent review."
        )
    else:
        reason = (
            f"REFUSED for {repo}#{pr_number}: no approving review from any "
            f"account other than the author '{author}' exists yet."
        )
    return False, reason


def apply_review_independence_verdict(conn, pr_review_record):
    """Real caller-side usage pattern for
    refuse_review_if_reviewer_is_author(): calls the pure function, and
    when it refuses, records a real, permanent
    'review_identity_independence_refused' audit event via
    record_ocid_master_standard_audit_event() (and commits it) so the
    refusal has a durable trail, not just a transient return value. Returns
    the same (verdict, reason) tuple the pure function returns.

    Write path uses _write_lock() like every other write path in this file
    (superboss-register.py:176-199) to avoid the documented 2026-07-23
    SQLite corruption pattern; callers do not need to wrap this call in
    their own _write_lock()."""
    verdict, reason = refuse_review_if_reviewer_is_author(pr_review_record)
    if not verdict:
        with _write_lock():
            record_ocid_master_standard_audit_event(
                conn, "review_identity_independence_refused",
                {
                    "repo": pr_review_record.get("repo"),
                    "pr_number": pr_review_record.get("pr_number"),
                    "author_login": pr_review_record.get("author_login"),
                    "approving_review_logins": pr_review_record.get("approving_review_logins"),
                    "reason": reason,
                },
            )
            conn.commit()
    return verdict, reason


# ---------------------------------------------------------------------------
# OCID Master Standard v6 -- evidence_json schema standardization (Owner
# directive, this cycle; citing UMR-20260804-170055-a069 [canonical OCID-068
# UMR] and UMR-20260805-032326-becc [real OCID canonical roster build]).
#
# Real finding, independently confirmed this cycle: ocid_canonical_registry
# has no dedicated real column for umr_id, pr_repo, or a short human evidence
# sentence tied 1:1 to a given row's own real evidence -- the commit_sha/
# file_name/file_path/merge_status/evidence_summary columns OCID-068 Phase 2
# added (_migrate_ocid_canonical_registry_completion_columns() above) are
# real dedicated columns, but evidence_json itself remained a free-form,
# per-search-method text dump with no fixed real shape, and (independently
# confirmed against the live DB) those Phase 2 columns had never actually
# been backfilled -- every one of the 69 real existing rows still had them
# NULL. This section locks down one real required shape for evidence_json
# itself, going forward, for every real row: the same facts as the 5
# dedicated Phase 2 columns, plus umr_id/ocid_number/pr_number/pr_repo/a
# short evidence_summary sentence, all carried INSIDE evidence_json too so a
# caller reading only evidence_json (never joining the dedicated columns)
# still gets the complete real picture. Wired into
# upsert_ocid_canonical_registry() itself as a real, structural,
# non-bypassable gate -- redundant with, and deliberately never a
# replacement for, refuse_certification_if_merged_without_required_checks()
# above: that function gates a PR-merge certification decision; this one
# gates an OCID-registry-row completion claim. Same "second independent
# layer, explicit structured input, no live I/O inside the pure function"
# design as that function, and the same audit-log wiring
# (record_ocid_master_standard_audit_event(), event_type=
# "evidence_schema_refused") as apply_certification_verdict() uses for
# event_type="certification_refused".
# ---------------------------------------------------------------------------

EVIDENCE_JSON_REQUIRED_KEYS = (
    "commit_sha", "file_name", "file_path", "merge_status",
    "umr_id", "ocid_number", "pr_number", "pr_repo", "evidence_summary",
)


class OcidEvidenceSchemaRefused(ValueError):
    """Raised by upsert_ocid_canonical_registry() when a row whose own
    status text genuinely claims completion/verification (see
    _status_claims_verified_or_completed()) is written with an evidence_json
    that does not satisfy EVIDENCE_JSON_REQUIRED_KEYS (see
    validate_evidence_json_schema()). Never raised for a row whose status
    does not itself claim completion (e.g. 'open', 'running, never
    completed', 'not_found') -- those rows are real and legitimately still
    in flight or honestly absent, not yet subject to this gate."""


def _status_claims_verified_or_completed(status):
    """Real, deterministic, zero-AI-judgment detector for whether a row's
    own free-text status genuinely claims 'completed' or 'verified' -- a
    real whole-word, non-negated match only. Excludes negated phrasing
    ('never completed', 'not completed', 'not verified') and excludes
    substring false-positives where the word is fused to a preceding
    identifier rather than standing alone (e.g. 'ts_completed=null',
    'NOT_VERIFIED' -- '_' is a real word character, so there is no real word
    boundary there for \\b to match). Independently verified against all 69
    real existing ocid_canonical_registry rows before this gate shipped:
    matches exactly the 11 rows whose status is a real completion claim
    (OCID-002, 003, 038, 047-052, 068, 069), and correctly excludes
    OCID-004/005 ('running, never completed (historical...)') and OCID-020
    ('...status=running, ts_completed=null; ...NOT_VERIFIED...').

    Known real limitation, honestly documented rather than silently assumed
    away: the negation lookbehinds only match the exact literal "never "/
    "not " (single space, immediately adjacent). A real future status
    string using a hyphen ('not-verified'), no separator ('notcompleted'),
    or a word in between ('not yet completed') would NOT be excluded by
    this check and would incorrectly gate as a completion claim. None of
    the 69 real existing rows use any of those forms; this is a real,
    open gap for future free-text status values, not a closed one -- a
    caller writing a genuinely-negated status in one of those forms should
    expect this gate to (incorrectly) apply and should not silently work
    around it, but should flag it for this detector to be extended."""
    if not status:
        return False
    return bool(re.search(r"(?<!never )(?<!not )\b(completed|verified)\b", status, re.IGNORECASE))


def validate_evidence_json_schema(evidence):
    """Pure, zero-I/O schema check. Returns (ok, missing_or_invalid, reason):
      - ok: bool
      - missing_or_invalid: sorted list of the real problem key names
      - reason: human-readable string naming every real problem, or None

    Every key in EVIDENCE_JSON_REQUIRED_KEYS must be PRESENT. A real, honest
    None/null is a valid value for every key except evidence_summary -- per
    the backfill directive, a genuinely unrecoverable commit_sha/file_path/
    etc. is recorded as null, never guessed. evidence_summary must be
    present AND a non-empty (after stripping whitespace) real string -- a
    short sentence is always required, even for a row where every other
    field is honestly null.

    Extra keys beyond the required 9 (e.g. a nested 'search_evidence' key
    preserving a row's own pre-existing free-text search evidence) are
    always allowed -- this validator only ever checks for a required
    subset, never a closed/exact key set, so no real prior evidence is ever
    forced out just to satisfy it."""
    if not isinstance(evidence, dict):
        return False, ["<evidence is not a dict>"], (
            "evidence_json must be a JSON object, not " + type(evidence).__name__
        )
    problems = [k for k in EVIDENCE_JSON_REQUIRED_KEYS if k not in evidence]
    if "evidence_summary" not in problems:
        summary = evidence.get("evidence_summary")
        if not isinstance(summary, str) or not summary.strip():
            problems.append("evidence_summary")
    problems = sorted(set(problems))
    if problems:
        return False, problems, (
            "evidence_json is missing or has an invalid value for required key(s): "
            + ", ".join(problems)
        )
    return True, [], None


def refuse_ocid_registry_completion_if_evidence_incomplete(ocid_number, status, evidence):
    """Pure function, zero I/O -- the real, second, independent refusal gate
    this directive asks for, deliberately alongside (never replacing)
    refuse_certification_if_merged_without_required_checks() above: that
    function's real incident was a PR merging without required GitHub
    checks/reviews; this one's real gap is an OCID registry row claiming
    'completed'/'verified' status while its own evidence_json carries none
    of the structured facts (commit sha, file path, merge status, PR/UMR
    linkage, a short human evidence sentence) that claim should be backed
    by. Returns (verdict: bool, reason: str) -- verdict True means the write
    is allowed (either the row's status does not itself claim completion, or
    it does and the schema is satisfied); False means refused, with `reason`
    naming every real missing/invalid key."""
    if not _status_claims_verified_or_completed(status):
        return True, (
            f"{ocid_number}: status {status!r} does not itself claim completed/verified -- "
            "evidence_json schema not required for this write."
        )
    ok, _problems, reason = validate_evidence_json_schema(evidence)
    if not ok:
        return False, (
            f"REFUSED write for {ocid_number}: status {status!r} claims completed/verified but "
            + reason + "."
        )
    return True, (
        f"{ocid_number}: status {status!r} claims completed/verified and evidence_json schema "
        "is satisfied."
    )


def _migrate_umr_last_heartbeat(conn):
    """2026-07-29 (Stage 3 reconciliation-sweep fix for 'task exits cleanly but
    umr_tasks status never reconciles', 5 real historical instances): additive
    ALTER TABLE ADD COLUMN for umr_tasks.last_heartbeat (nullable TEXT
    ISO-8601 timestamp), same pattern as _migrate_wiring_registry_content_hash's
    system_index.tags column above -- no CHECK constraint involved, so a
    straight ALTER TABLE ADD COLUMN is sufficient, no full-table rebuild
    needed. Deliberately nullable with no DEFAULT: every umr_tasks row written
    before this migration (all 5 real in-flight tasks at the moment this
    deploys -- PR617-REVIEW, PR618-REVIEW, PR58-CONFLICT, PR610-CONFLICT,
    PHASE-2-CROSSREF) must read back NULL here, never a synthesized 'now' or
    epoch value that a stale-TTL sweep could misread as already-expired.
    resource_governor.py's reconcile_stale_heartbeats() explicitly treats
    NULL as 'unknown, skip', by construction (its own SQL WHERE excludes
    NULL), not as a convention callers must remember.
    Called from INSIDE _ensure_umr_table() itself, not only from
    _migrate_schema(), because resource_governor.py calls _ensure_umr_table()
    directly at several read/write call sites, bypassing _migrate_schema() by
    that function's own documented design (see _ensure_umr_table's docstring)
    -- this column must exist before any of those callers can run a query
    that references it."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(umr_tasks)").fetchall()}
    if "last_heartbeat" not in cols:
        conn.execute("ALTER TABLE umr_tasks ADD COLUMN last_heartbeat TEXT")
        conn.commit()
    conn.execute("CREATE INDEX IF NOT EXISTS idx_umr_tasks_last_heartbeat ON umr_tasks(last_heartbeat)")
    conn.commit()


def _migrate_umr_tenant_id(conn):
    """2026-07-29 (Stage 10 END_USER_ENGINE foundation): additive ALTER TABLE
    ADD COLUMN for umr_tasks.tenant_id (nullable TEXT), same pattern as
    _migrate_umr_last_heartbeat's last_heartbeat column above -- no CHECK
    constraint involved and no full-table rebuild needed. Deliberately
    nullable with no DEFAULT: every real umr_tasks row submitted so far is
    Owner-side work, not real end-user/tenant work, so NULL here correctly
    means "not yet a multi-tenant-scoped task," not a data-quality gap.
    resource_governor.py's submit() passes tenant_id=None for every existing
    real caller (none of them set it), preserving that meaning exactly and
    keeping 100% backward compatibility.

    Checked before this migration: the UTM-shaped instructions/work_items/
    actions tables in this same file have no existing tenant/org_id
    convention (no such column, and their metadata_json has no established
    tenant/org key) -- so tenant_id TEXT is a new convention here, not a
    mismatch with one that already existed.

    Indexed as a PARTIAL index (WHERE tenant_id IS NOT NULL): at the moment
    this migrates, all real umr_tasks rows are NULL (Owner-side), so a full
    index over the column would spend entirely on NULL entries with zero
    query benefit today. A partial index costs nothing while every row is
    NULL, and is ready the moment a future END_USER_ENGINE caller starts
    passing real tenant_id values, at which point "all tasks for tenant X"
    (WHERE tenant_id=?) becomes an index seek instead of a full table scan --
    worth doing now given this table is already growing under active
    dispatch load (a sibling table already has 8,000+ rows per the Stage 7
    investigation).

    Called from INSIDE _ensure_umr_table() itself, not only from
    _migrate_schema(), for the same reason _migrate_umr_last_heartbeat() is:
    resource_governor.py calls _ensure_umr_table() directly at several
    read/write call sites, bypassing _migrate_schema()."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(umr_tasks)").fetchall()}
    if "tenant_id" not in cols:
        conn.execute("ALTER TABLE umr_tasks ADD COLUMN tenant_id TEXT")
        conn.commit()
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_umr_tasks_tenant_id ON umr_tasks(tenant_id) "
        "WHERE tenant_id IS NOT NULL"
    )
    conn.commit()


def _migrate_umr_relay_courtesy(conn):
    """UMR-20260806-115423-500d (real narrowing of the owner_dispatch_gateway
    relay-vs-mechanical-pickup tension): additive ALTER TABLE ADD COLUMN for
    three new nullable umr_tasks columns -- ts_relay_attempted, relay_outcome,
    relay_detail -- same PRAGMA-table_info-then-ALTER pattern as
    _migrate_umr_last_heartbeat/_migrate_umr_tenant_id above.

    Real root cause this closes: dispatch-owner-task.sh's `tmux send-keys`
    call proves only that keystrokes were written into a pane -- never that a
    live process actually read and acted on them. The pre-existing design
    (PR #150 / UMR-20260806-085144-9c63) nonetheless treated a successful
    send-keys as authoritative delivery, writing status='dispatched' (relay
    succeeded) or a real terminal status='failed' (tmux session absent)
    straight onto the umr_tasks row. Both writes independently remove the row
    from `next_queued_task()`'s own real query -- `SELECT * FROM umr_tasks
    WHERE status='queued'` (resource_governor.py) -- which is the ONLY
    function that mechanically dispatches a queued veridian_task_create row
    to a real, independent, non-interactive `veridian-worker@*.service` via
    `_perform_spawn()`. So a row whose tmux keystrokes landed in a dead pane
    (or whose target session was briefly absent) was silently and
    permanently excluded from the one channel that could still have picked
    it up mechanically -- a real dead zone, confirmed by reading
    next_queued_task()/_perform_spawn() directly, not assumed.

    These three columns exist so dispatch-owner-task.sh can keep recording a
    real, honest "we attempted a relay" signal -- genuinely useful for
    diagnosing a stuck row -- WITHOUT that signal ever again being read as
    proof of delivery or used to move the row out of the real 'queued' pool.
    mark_umr_relay_attempted() (below) is the only writer; it never touches
    `status`, `ts_dispatched`, or `ts_completed`.

      ts_relay_attempted  TEXT, nullable, ISO-8601 -- when a relay attempt
        (success OR tmux-session-absent) was recorded. NULL means no relay
        was ever attempted for this row (e.g. --no-relay, or a pure
        systemctl_action row that never goes through dispatch-owner-task.sh
        at all).
      relay_outcome       TEXT, nullable -- 'sent' (send-keys returned 0
        against a session tmux confirmed existed) or 'session_not_found'
        (tmux had no session by that name at relay time). Deliberately NOT
        'delivered'/'failed': neither value is proof of what happened
        downstream of the keystrokes.
      relay_detail        TEXT, nullable -- free-text diagnostic (e.g. which
        tmux session name was targeted), same convention as `reason`.

    Called from INSIDE _ensure_umr_table() itself, not only from
    _migrate_schema(), for the same reason _migrate_umr_last_heartbeat() is:
    resource_governor.py calls _ensure_umr_table() directly at several
    read/write call sites, bypassing _migrate_schema()."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(umr_tasks)").fetchall()}
    for col in ("ts_relay_attempted", "relay_outcome", "relay_detail"):
        if col not in cols:
            conn.execute(f"ALTER TABLE umr_tasks ADD COLUMN {col} TEXT")
    conn.commit()


def _migrate_umr_tasks_external_agent_columns(conn):
    """Real Owner directive UMR-20260806-095416-b6f0: fourth real worker
    channel -- a fully manual human-paste bridge to chat.z.ai, alongside the
    existing systemd `veridian-worker@*` units and the interactive tmux
    session. NEVER any browser automation against chat.z.ai (hard
    Terms-of-Service constraint): the Owner personally copies the rendered
    prompt out and pastes the reply back in, every time, no exception.

    Additive ALTER TABLE ADD COLUMN migration for umr_tasks, same
    PRAGMA-table_info-then-ALTER pattern as _migrate_umr_tenant_id/
    _migrate_pm_decisions_pending_owner_proposal_columns above -- no CHECK
    constraint, no full-table rebuild, safe to run on a pre-existing DB with
    real rows already in it (this migrates the real, live, 1.8GB
    superboss-register.sqlite, not a fresh fixture).

    Columns:
      external_agent_eligible        INTEGER NOT NULL DEFAULT 0 (boolean) --
        never hand-set true by anything except mark_external_agent_eligible()
        below, which runs check_external_agent_eligibility() first and
        refuses to set this to 1 for a row that does not really pass every
        real rule. Existing rows default to 0 (not eligible) -- correct,
        since eligibility is opt-in per-task, never assumed.
      external_agent_task_type       TEXT, restricted at the APPLICATION
        layer (not a DB CHECK constraint -- see
        EXTERNAL_AGENT_ALLOWED_TASK_TYPES below and
        check_external_agent_eligibility()) to exactly: isolated_bugfix,
        doc_update, single_file_refactor, test_addition_only.
      blast_radius                   TEXT -- must equal the literal string
        'isolated' for a row to ever be eligible (see
        check_external_agent_eligibility()); the column itself stays a
        plain nullable TEXT (no CHECK) since a real caller may legitimately
        record a wider blast_radius on an INeligible row for audit purposes.
      requires_multi_file_context     INTEGER NOT NULL DEFAULT 0 (boolean) --
        must be 0/false for a row to ever be eligible.
      files_touched                  TEXT, JSON array of exact repo-relative
        paths (json.dumps'd list of str) -- NOT NULL DEFAULT '[]' so every
        row has a real, parseable value even before this feature ever
        touches it.
      external_agent_status          TEXT, nullable. NULL = never dispatched
        to this channel. Real state machine (see module docstring above
        get_next_external_agent_task()): NULL -> 'dispatched' -> either
        'pr_open' (real success terminal state -- a real PR is open,
        pending real human review, NEVER auto-merged) or 'requeued' (real
        first-strike failure, eligible for exactly one more real dispatch)
        or 'fallen_back_internal' (real second-strike terminal state --
        external_agent_eligible is also forced back to 0 at that point, and
        the row falls back to the normal internal worker pool; a real
        reason is always recorded in umr_tasks.reason).
      external_agent_reject_count    INTEGER NOT NULL DEFAULT 0 -- real
        two-strike counter (expiry counts as a reject); permanently
        excluded from further external-agent dispatch once this reaches 2
        (see get_next_external_agent_task()'s own WHERE clause).
      external_agent_dispatch_count  INTEGER NOT NULL DEFAULT 0 -- real
        count of every real dispatch attempt ever made for this row
        (dispatched/requeued-redispatched both increment it), purely
        informational/audit -- the real gate against a 3rd attempt is
        external_agent_reject_count < 2, not this counter.

    acceptance-criteria / repro-steps are deliberately NOT new dedicated
    columns here: per this file's own STORAGE FORMAT convention (top-of-file
    module docstring -- "typed columns + a JSON metadata blob"), those two
    free-text fields live inside the existing metadata_json column under a
    real "external_agent" sub-object (see mark_external_agent_eligible()),
    exactly like every other free-text elaboration on a umr_tasks row
    already does -- adding two more dedicated top-level TEXT columns for
    what is fundamentally prose would be a second, parallel convention for
    the same kind of data this table already has a real place for."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(umr_tasks)").fetchall()}
    if "external_agent_eligible" not in cols:
        conn.execute("ALTER TABLE umr_tasks ADD COLUMN external_agent_eligible INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    if "external_agent_task_type" not in cols:
        conn.execute("ALTER TABLE umr_tasks ADD COLUMN external_agent_task_type TEXT")
        conn.commit()
    if "blast_radius" not in cols:
        conn.execute("ALTER TABLE umr_tasks ADD COLUMN blast_radius TEXT")
        conn.commit()
    if "requires_multi_file_context" not in cols:
        conn.execute("ALTER TABLE umr_tasks ADD COLUMN requires_multi_file_context INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    if "files_touched" not in cols:
        conn.execute("ALTER TABLE umr_tasks ADD COLUMN files_touched TEXT NOT NULL DEFAULT '[]'")
        conn.commit()
    if "external_agent_status" not in cols:
        conn.execute("ALTER TABLE umr_tasks ADD COLUMN external_agent_status TEXT")
        conn.commit()
    if "external_agent_reject_count" not in cols:
        conn.execute("ALTER TABLE umr_tasks ADD COLUMN external_agent_reject_count INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    if "external_agent_dispatch_count" not in cols:
        conn.execute("ALTER TABLE umr_tasks ADD COLUMN external_agent_dispatch_count INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    # Partial index (same reasoning as idx_umr_tasks_tenant_id above): almost
    # every real row will never be external_agent_eligible, so a partial
    # index over just the eligible rows costs near-nothing today and makes
    # get_next_external_agent_task()'s own SELECT a real index seek once
    # this feature is in real use.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_umr_tasks_external_agent_eligible "
        "ON umr_tasks(external_agent_eligible, external_agent_status) "
        "WHERE external_agent_eligible = 1"
    )
    conn.commit()


def _ensure_external_agent_dispatch_table(conn):
    """Real Owner directive UMR-20260806-095416-b6f0. New, additive,
    append-only child table: one row per real chat.z.ai dispatch ATTEMPT
    (never reused across attempts -- a requeued-after-reject row gets a
    brand-new dispatch_id and a brand-new row on its next real dispatch, so
    the full real history of every real attempt against a given umr_id is
    always fully reconstructable, never overwritten). Same idempotent
    CREATE TABLE IF NOT EXISTS + standalone-callable convention as
    _ensure_ocid_artifact_links_table/_ensure_umr_table above. dispatch_id is
    a real, sortable, timestamp-prefixed id minted via _new_id('EAD') (same
    scheme as every other id in this file -- see the module docstring's ID
    SCHEMES section).

    umr_id is a real FOREIGN KEY into umr_tasks (see
    _ensure_ocid_artifact_links_table's own docstring for why SQLite only
    enforces this with `PRAGMA foreign_keys=ON`, which _connect() does not
    set -- documented, not silently assumed, same as that table).

    provider defaults to 'chat.z.ai' (the one real provider this channel
    exists for today) but is a real column, not a hardcoded constant,
    because a future second manual-paste provider should extend this same
    table rather than force a parallel one.

    status is the real per-attempt lifecycle: dispatched -> submitted (a
    real paste-back was received and parsed) -> accepted (real PR opened) or
    rejected (real validation/gate/apply failure) -- or dispatched -> expired
    (the real 24h window closed with no paste-back at all, set only by
    expire_external_agent_dispatches()). Plain nullable TEXT, restricted at
    the application layer (same convention as external_agent_task_type on
    umr_tasks), not a DB CHECK constraint, since a mid-implementation status
    vocabulary tweak should never require a schema migration.

    prompt_sha256/result_sha256 are real integrity fingerprints of the exact
    prompt text handed to the Owner and the exact reply text pasted back --
    so a later audit can confirm byte-for-byte what was actually sent/
    received without needing to keep re-reading the (potentially large)
    prompt_path/result_raw_path files themselves.

    gate_report_path records the real path to the exact, unmodified
    quality-gate.sh JSON output for this attempt (see
    submit_external_agent_result()) -- the same real quality gate every
    other real worker channel in this codebase runs, zero special-casing."""
    conn.execute("""CREATE TABLE IF NOT EXISTS external_agent_dispatch (
        dispatch_id TEXT PRIMARY KEY,
        umr_id TEXT NOT NULL REFERENCES umr_tasks(umr_id),
        provider TEXT NOT NULL DEFAULT 'chat.z.ai',
        prompt_sha256 TEXT NOT NULL,
        prompt_path TEXT NOT NULL,
        status TEXT NOT NULL,
        dispatched_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        submitted_at TEXT,
        result_raw_path TEXT,
        result_sha256 TEXT,
        branch_name TEXT,
        worktree_path TEXT,
        gate_report_path TEXT,
        pr_number INTEGER,
        pr_url TEXT,
        reject_reason TEXT,
        reviewed_by TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_external_agent_dispatch_umr ON external_agent_dispatch(umr_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_external_agent_dispatch_status ON external_agent_dispatch(status)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_external_agent_dispatch_expiry "
        "ON external_agent_dispatch(status, expires_at) WHERE status = 'dispatched'"
    )
    conn.commit()


def _derive_umr_utm_fields(record):
    """Real, deterministic UTM field derivation for one umr_tasks row (Phase 6,
    task-umr-tasks-utm-correlation-phase6-2026-07-29 -- closes the one real
    gap identified in the UTM tagging standard sweep: instructions/work_items/
    actions/system_index/capability_registry already carry the standard 5
    fields; umr_tasks -- the real task-execution table resource_governor.py's
    submit()/dispatch_one() write to -- did not). Same "pull only from data
    already on the row/record itself, never a placeholder" rule
    _derive_capability_utm_fields established:

    utm_source   -- classified from the row's own real source_trigger (the
                     actual caller-supplied classification of what triggered
                     this task -- see resource_governor.submit()'s docstring):
                     values starting with 'owner' (the real, live
                     'owner_dispatch_gateway' / 'owner_directive_2026-07-28' /
                     'owner_directive_direct_ai_analysis_mid_turn' /
                     'owner_directive_laptop_offline_continuity' rows already
                     in this table) -> 'owner_engine'; values starting with
                     'DIRECTIVE' (the real 'DIRECTIVE' / 'DIRECTIVE-001' /
                     'DIRECTIVE-002' rows) -> 'directive_engine'; anything
                     containing 'test', 'scenario', 'adversarial', or
                     'verif' (the real, live adversarial-test/load-test/
                     scenario-test/verification rows already in this table)
                     -> 'test_harness'; else 'resource_governor' (the real
                     scheduled-trigger default path -- cron/systemd timer/
                     worker spawn per submit()'s own docstring, none of which
                     self-identify any further than source_trigger already
                     does).
    utm_medium   -- constant 'submit': resource_governor.submit() ->
                     upsert_umr_task() is the one real function that has ever
                     written a umr_tasks row (confirmed: upsert_umr_task's
                     only 2 call sites are both inside submit()) -- same
                     single-real-mechanism rule capability_registry's
                     'register-capability' constant medium uses.
    utm_campaign -- source_trigger IS already the real grouping slug for most
                     rows (e.g. 'stage11-loadtest-manual'); several real rows
                     already encode a campaign:variant split with ':'
                     themselves (e.g.
                     'stage10-tenant-column-verification:tenant-test') --
                     split on the first ':' when present and use the left
                     side, else the whole source_trigger, else 'unclassified'
                     for the genuinely-empty case (never fabricated).
    utm_content  -- task_kind: the real, specific mechanism differentiator
                     for this exact row (systemctl_action /
                     veridian_task_create / direct_ai_analysis / ...), same
                     role workflow/automation play in
                     _derive_capability_utm_fields.
    utm_term     -- task_identity, plus unit_name appended when present
                     (comma-separated, per this file's own utm_term
                     convention documented at the top of this module) -- the
                     real identifying/searchable label(s) for this row.

    `record` accepts either a live umr_tasks row (dict/sqlite3.Row) or the
    plain dict resource_governor.submit() builds for upsert_umr_task() --
    same field names either way (task_identity/source_trigger/task_kind/
    unit_name)."""
    source_trigger = record.get("source_trigger") or ""
    lower = source_trigger.lower()
    if lower.startswith("owner"):
        utm_source = "owner_engine"
    elif source_trigger.startswith("DIRECTIVE"):
        utm_source = "directive_engine"
    elif any(k in lower for k in ("test", "scenario", "adversarial", "verif")):
        utm_source = "test_harness"
    else:
        utm_source = "resource_governor"

    utm_campaign = source_trigger.split(":", 1)[0] if ":" in source_trigger else (source_trigger or "unclassified")
    utm_content = record.get("task_kind") or "n/a"
    term_parts = [p for p in (record.get("task_identity"), record.get("unit_name")) if p]
    utm_term = ", ".join(term_parts) if term_parts else None

    return {
        "utm_source": utm_source,
        "utm_medium": "submit",
        "utm_campaign": utm_campaign,
        "utm_content": utm_content,
        "utm_term": utm_term,
    }


def _migrate_umr_utm(conn):
    """Phase 6 (task-umr-tasks-utm-correlation-phase6-2026-07-29): additive
    ALTER TABLE ADD COLUMN for umr_tasks's five UTM fields, same real,
    working pattern _migrate_capability_registry_utm above actually uses
    (not the NOT-NULL-DEFAULT shorthand in that table's base CREATE TABLE
    text) -- all 5 columns added as plain nullable TEXT via ALTER TABLE
    (SQLite's ALTER TABLE ADD COLUMN with NOT NULL only works with a
    constant DEFAULT, which utm_campaign/utm_content/utm_term genuinely
    don't have -- they're derived per-row, not a blanket constant), then
    backfilled via UPDATE using _derive_umr_utm_fields() -- same self-healing
    "re-checked on every subsequent call, no-op once done" convention as
    every other migration in this file. Also matches umr_tasks's own local
    precedent: _migrate_umr_last_heartbeat/_migrate_umr_tenant_id above both
    add their column nullable via ALTER TABLE only, never touching the base
    CREATE TABLE text in _ensure_umr_table().

    utm_source/utm_medium are backfilled per-row too (not a blanket
    constant), unlike capability_registry's utm_source -- umr_tasks has 3
    real, distinct historical origins (owner-driven DIRECTIVE-queue rows,
    owner_dispatch_gateway rows, and adversarial/scenario/load-test harness
    rows), so a single constant would flatten a real, useful distinction
    capability_registry's single-mechanism table never had.

    Index: utm_campaign (not the full row) -- same idx_capability_registry_
    campaign precedent -- this is the field real future queries actually
    group/filter by.

    FTS5 rebuild: umr_tasks_fts must gain utm_source/utm_campaign/utm_term
    as real searchable columns, exact same 3-column subset (not
    utm_medium/utm_content) _migrate_capability_registry_utm added to
    capability_registry_fts. FTS5 has no ALTER-TABLE-ADD-COLUMN path usable
    here without losing the external-content 'rebuild' semantics, so this
    mirrors that migration's mechanism exactly: drop the 3 triggers, drop
    the shadow table, recreate both with the widened column list, then
    re-populate via the fts5 'rebuild' command. Checked for need via the
    shadow table's own stored CREATE TABLE text (same idiom as
    capability_registry's) so this half only runs once, is a no-op
    afterward, and self-heals if a prior run added the base columns but was
    interrupted before the FTS rebuild.

    Called from INSIDE _ensure_umr_table() itself, not only from
    _migrate_schema(), for the same reason _migrate_umr_last_heartbeat/
    _migrate_umr_tenant_id are: resource_governor.py calls _ensure_umr_table()
    directly at several read/write call sites, bypassing _migrate_schema()."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='umr_tasks'"
    ).fetchone()
    if row is None:
        return  # table doesn't exist yet; _ensure_umr_table's CREATE TABLE covers it next call

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(umr_tasks)").fetchall()}
    if "utm_source" not in cols:
        for col in ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"):
            conn.execute(f"ALTER TABLE umr_tasks ADD COLUMN {col} TEXT")
        conn.commit()

    backfill_rows = conn.execute(
        "SELECT umr_id, task_identity, source_trigger, task_kind, unit_name FROM umr_tasks WHERE utm_term IS NULL"
    ).fetchall()
    if backfill_rows:
        for r in backfill_rows:
            fields = _derive_umr_utm_fields(dict(r))
            conn.execute(
                "UPDATE umr_tasks SET utm_source=?, utm_medium=?, utm_campaign=?, utm_content=?, utm_term=? "
                "WHERE umr_id=?",
                (fields["utm_source"], fields["utm_medium"], fields["utm_campaign"],
                 fields["utm_content"], fields["utm_term"], r["umr_id"]),
            )
        conn.commit()

    conn.execute("CREATE INDEX IF NOT EXISTS idx_umr_tasks_utm_campaign ON umr_tasks(utm_campaign)")
    conn.commit()

    fts_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='umr_tasks_fts'"
    ).fetchone()
    if fts_row is not None and "utm_term" not in fts_row["sql"]:
        conn.execute("DROP TRIGGER IF EXISTS umr_tasks_ai")
        conn.execute("DROP TRIGGER IF EXISTS umr_tasks_au")
        conn.execute("DROP TRIGGER IF EXISTS umr_tasks_ad")
        conn.execute("DROP TABLE IF EXISTS umr_tasks_fts")
        conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS umr_tasks_fts USING fts5(
            task_identity, source_trigger, logs_ref, utm_source, utm_campaign, utm_term,
            content='umr_tasks', content_rowid='rowid'
        )""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS umr_tasks_ai AFTER INSERT ON umr_tasks BEGIN
            INSERT INTO umr_tasks_fts(rowid, task_identity, source_trigger, logs_ref, utm_source, utm_campaign, utm_term)
            VALUES (new.rowid, new.task_identity, new.source_trigger, new.logs_ref, new.utm_source, new.utm_campaign, new.utm_term);
        END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS umr_tasks_au AFTER UPDATE ON umr_tasks BEGIN
            INSERT INTO umr_tasks_fts(umr_tasks_fts, rowid, task_identity, source_trigger, logs_ref, utm_source, utm_campaign, utm_term)
            VALUES ('delete', old.rowid, old.task_identity, old.source_trigger, old.logs_ref, old.utm_source, old.utm_campaign, old.utm_term);
            INSERT INTO umr_tasks_fts(rowid, task_identity, source_trigger, logs_ref, utm_source, utm_campaign, utm_term)
            VALUES (new.rowid, new.task_identity, new.source_trigger, new.logs_ref, new.utm_source, new.utm_campaign, new.utm_term);
        END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS umr_tasks_ad AFTER DELETE ON umr_tasks BEGIN
            INSERT INTO umr_tasks_fts(umr_tasks_fts, rowid, task_identity, source_trigger, logs_ref, utm_source, utm_campaign, utm_term)
            VALUES ('delete', old.rowid, old.task_identity, old.source_trigger, old.logs_ref, old.utm_source, old.utm_campaign, old.utm_term);
        END""")
        conn.execute("INSERT INTO umr_tasks_fts(umr_tasks_fts) VALUES ('rebuild')")
        conn.commit()


def find_active_umr_by_identity(conn, task_identity):
    """The real de-duplication check (SCOPE item 4): does task_identity
    already have a row in queued/dispatched/running? Called from inside
    resource_governor.submit()'s superboss-register.py._write_lock(), so two
    racing submissions for the same identity cannot both pass this check --
    same TOCTOU-closing shape as dispatch_core.acquire_dispatch_lock()."""
    placeholders = ",".join("?" * len(UMR_ACTIVE_STATUSES))
    row = conn.execute(
        f"SELECT * FROM umr_tasks WHERE task_identity=? AND status IN ({placeholders}) "
        "ORDER BY ts_submitted DESC LIMIT 1",
        (task_identity, *UMR_ACTIVE_STATUSES),
    ).fetchone()
    return dict(row) if row else None


def find_active_umr_by_ocid(conn, ocid_number):
    """OCID-068 seven-rule guardrails addendum, Rule 6 (UMR-20260804-180711-7f96,
    UMR-20260804-205741-cf3f, citing UMR-20260804-170055-a069): "zero
    duplication, before creating any new UMR verify the OCID... and if a
    match is found return the existing UMR instead of creating a duplicate."

    find_active_umr_by_identity() above already provides real, deterministic
    dedup on task_identity (a caller-provided string) -- this is the OCID-
    dimension complement, using ocid_artifact_links (the real, canonical
    OCID<->UMR registry this same addendum built for exactly this purpose,
    per UMR-20260804-170055-a069) as the join: any UMR already linked to
    ocid_number, currently in an ACTIVE status (queued/dispatched/running).

    Deliberately narrower than "one OCID = one UMR forever" -- this session's
    own real history has many legitimate, sequential, non-overlapping UMRs
    for the same OCID (e.g. OCID-068 itself has ~15 real UMRs across
    distinct PM directives). Blocking that pattern would be a real, wrong
    over-application of this rule. This function only catches a genuinely
    CONCURRENT second UMR for an OCID that already has one actively in
    flight -- the real, narrow case Rule 6 exists to prevent (an OCID being
    worked twice at once, not an OCID being worked more than once, ever).

    Returns the existing active UMR's own real row (dict) if found, else
    None -- never fabricates a match."""
    ocid_rows = conn.execute(
        "SELECT DISTINCT umr_id FROM ocid_artifact_links WHERE ocid_number=?",
        (ocid_number,),
    ).fetchall()
    if not ocid_rows:
        return None
    umr_ids = [r["umr_id"] for r in ocid_rows]
    umr_placeholders = ",".join("?" * len(umr_ids))
    status_placeholders = ",".join("?" * len(UMR_ACTIVE_STATUSES))
    row = conn.execute(
        f"SELECT * FROM umr_tasks WHERE umr_id IN ({umr_placeholders}) "
        f"AND status IN ({status_placeholders}) ORDER BY ts_submitted DESC LIMIT 1",
        (*umr_ids, *UMR_ACTIVE_STATUSES),
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# OCID-068 seven-rule compliance tracking -- full historical roster
# (UMR-20260805-093138-2bd0 + scope clarification UMR-20260805-093254-056e,
# extending UMR-20260805-092408-4f97 / UMR-20260805-091934-86a2 /
# UMR-20260805-090549-9710, citing the canonical OCID-068 UMR
# UMR-20260804-170055-a069). Tables named ocid_compliance_state /
# ocid_compliance_audit_log -- renamed from the originally-requested
# ocid_068_compliance_state/ocid_068_compliance_audit_log per
# UMR-20260805-093254-056e's own explicit authorization to rename for
# clarity once real row coverage became the full OCID-001..069 roster (every
# real OCID x every real UMR in its own all_umr_ids_json), not literally
# scoped to the OCID-068 number alone -- disclosed here and in the PR
# description, not a silent divergence from what was literally asked.
#
# Same anti-fabrication principle as ocid_canonical_registry's has_real_*/
# is_fully_complete columns: none of the boolean columns below are ever
# caller-settable. The ONLY real write path is run_ocid_compliance_audit()
# -> record_ocid_compliance_audit(), which always writes a real
# ocid_compliance_audit_log row (raw, verbatim evidence) for every field in
# the SAME real transaction as the ocid_compliance_state upsert, so current
# state and full history can never drift apart. This is a real, Python-API-
# level guarantee (one function, one transaction, no other function accepts
# these booleans as plain parameters) -- not a SQL-trigger-level one the way
# has_real_*/is_fully_complete are; SQLite triggers cannot verify a value
# was genuinely produced by running real rule-check logic, only that stored
# columns are internally self-consistent. Documented plainly rather than
# overclaiming a guarantee SQLite cannot actually give.
# ---------------------------------------------------------------------------

# Real merge timestamps (UTC) of each rule's own real mechanism PR --
# independently fetched via `gh pr view --repo FChecklist/veridian-scripts
# --json mergedAt` this session (2026-08-05), not guessed. A rule's
# mechanism cannot have been satisfied by any real OCID/UMR pair registered
# (ts_submitted) before its own real merge date -- recorded honestly as
# `false` with a real explanation in that case, never `true`, never
# silently null (UMR-20260805-093254-056e's own explicit instruction).
OCID_068_RULE_MECHANISM_MERGED_AT = {
    "rule_1_umr_reuse_verified": ("2026-08-04T20:18:42Z", "veridian-scripts PR #26"),
    "rule_2_outcome_classification_verified": ("2026-08-04T20:45:42Z", "veridian-scripts PR #29"),
    "rule_3_no_premature_minting_verified": ("2026-08-04T20:52:22Z", "veridian-scripts PR #30"),
    "rule_4_pm_visible_counts_verified": ("2026-08-04T21:09:40Z", "veridian-scripts PR #32"),
    "rule_5_stall_detection_verified": ("2026-08-04T21:20:34Z", "veridian-scripts PR #33"),
    "rule_6_zero_duplication_verified": ("2026-08-04T21:20:24Z", "veridian-scripts PR #34"),
    "rule_7_structured_evidence_verified": (
        "2026-08-04T21:33:12Z",
        "veridian-scripts PR #35 (also depends on ocid_artifact_links, PR #20, merged earlier)",
    ),
}


def _ensure_ocid_compliance_tables(conn):
    """Idempotent CREATE TABLE IF NOT EXISTS for both real tables, same
    convention as every other _ensure_*_table function in this file. One
    real row per real (ocid_number, umr_id) pair in ocid_compliance_state
    (composite PRIMARY KEY -- upserts replace it); ocid_compliance_audit_log
    is genuinely append-only, one real row per real field/rule per real
    audit run, never UPDATEd or DELETEd (same append-only convention as
    ocid_master_standard_audit_log above)."""
    conn.execute("""CREATE TABLE IF NOT EXISTS ocid_compliance_state (
        ocid_number TEXT NOT NULL,
        umr_id TEXT NOT NULL,
        rule_1_umr_reuse_verified INTEGER NOT NULL DEFAULT 0,
        rule_2_outcome_classification_verified INTEGER NOT NULL DEFAULT 0,
        rule_3_no_premature_minting_verified INTEGER NOT NULL DEFAULT 0,
        rule_4_pm_visible_counts_verified INTEGER NOT NULL DEFAULT 0,
        rule_5_stall_detection_verified INTEGER NOT NULL DEFAULT 0,
        rule_6_zero_duplication_verified INTEGER NOT NULL DEFAULT 0,
        rule_7_structured_evidence_verified INTEGER NOT NULL DEFAULT 0,
        file_path TEXT,
        file_path_checked INTEGER NOT NULL DEFAULT 0,
        file_checked INTEGER NOT NULL DEFAULT 0,
        file_path_available INTEGER NOT NULL DEFAULT 0,
        file_path_validated INTEGER NOT NULL DEFAULT 0,
        file_existing INTEGER NOT NULL DEFAULT 0,
        file_work_implemented INTEGER NOT NULL DEFAULT 0,
        file_details TEXT,
        file_created_date TEXT,
        file_last_reviewed_date TEXT,
        version_history TEXT,
        version_date TEXT,
        status_one_word TEXT,
        status_one_sentence TEXT,
        audit_done INTEGER NOT NULL DEFAULT 0,
        audit_passed INTEGER NOT NULL DEFAULT 0,
        last_audit_timestamp TEXT,
        PRIMARY KEY (ocid_number, umr_id)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ocid_compliance_state_ocid ON ocid_compliance_state(ocid_number)")
    conn.execute("""CREATE TABLE IF NOT EXISTS ocid_compliance_audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ocid_number TEXT NOT NULL,
        umr_id TEXT NOT NULL,
        audit_timestamp TEXT NOT NULL,
        rule_or_field_name TEXT NOT NULL,
        result INTEGER,
        raw_output TEXT NOT NULL,
        audited_by TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ocid_compliance_audit_ocid ON ocid_compliance_audit_log(ocid_number)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ocid_compliance_audit_umr ON ocid_compliance_audit_log(umr_id)")
    conn.commit()
    _ensure_ocid_compliance_state_derive_triggers(conn)


# The 13 real boolean fields on ocid_compliance_state that are NEVER
# caller-settable -- every one is trigger-derived from
# ocid_compliance_audit_log's own real, append-only, verbatim-evidence
# rows, not from whatever a caller's INSERT/UPDATE statement supplied.
OCID_COMPLIANCE_STATE_BOOLEAN_FIELDS = (
    "rule_1_umr_reuse_verified", "rule_2_outcome_classification_verified",
    "rule_3_no_premature_minting_verified", "rule_4_pm_visible_counts_verified",
    "rule_5_stall_detection_verified", "rule_6_zero_duplication_verified",
    "rule_7_structured_evidence_verified",
    "file_path_checked", "file_checked", "file_path_available",
    "file_path_validated", "file_existing", "file_work_implemented",
)
OCID_COMPLIANCE_STATE_RULE_FIELDS = tuple(
    f for f in OCID_COMPLIANCE_STATE_BOOLEAN_FIELDS if f.startswith("rule_")
)


def _ensure_ocid_compliance_state_derive_triggers(conn):
    """UMR-20260805-093138-2bd0 / UMR-20260805-092408-4f97's same real
    anti-fabrication principle, applied here the same structural way as
    ocid_canonical_registry's own has_real_*/is_fully_complete triggers:
    a real AFTER INSERT / AFTER UPDATE trigger on ocid_compliance_state
    that OVERWRITES every one of the 13 real boolean columns (7 rules + 6
    file-tracking fields) with a real, correlated-subquery lookup of the
    MOST RECENT matching row in ocid_compliance_audit_log for this exact
    (ocid_number, umr_id, rule_or_field_name) -- never trusting whatever a
    caller's own INSERT/UPDATE statement supplied for those columns. A
    field with NO real audit_log row at all (never audited) derives to a
    real, honest 0/false via COALESCE, never a fabricated pass.
    `audit_done`/`audit_passed` are likewise derived, not caller-settable:
    audit_done = 1 iff at least one real audit_log row exists for this
    pair; audit_passed = 1 iff all 7 real rule_* columns derive to 1.

    This closes the real fabrication gap a purely Python-API-level
    convention (record_ocid_compliance_audit() being "the only real write
    path") cannot fully close by itself: even a bare, hand-typed
    `INSERT INTO ocid_compliance_state (...) VALUES (...)` bypassing that
    function entirely still has every one of its 13 real booleans
    immediately overwritten by this trigger from the real, append-only
    evidence log -- exactly the same structural guarantee already proven
    for ocid_canonical_registry's own completion-gate columns, just
    correlated across two tables via subqueries instead of within one row.
    `file_path`/`file_details`/`status_one_word`/etc. (plain data columns,
    not gate booleans) are deliberately NOT trigger-derived here, same
    precedent as ocid_canonical_registry's own commit_sha/file_path/
    merge_status/evidence_summary columns above."""
    def _latest_result_subquery(field):
        return (
            f"COALESCE((SELECT result FROM ocid_compliance_audit_log "
            f"WHERE ocid_number = NEW.ocid_number AND umr_id = NEW.umr_id "
            f"AND rule_or_field_name = '{field}' "
            f"ORDER BY audit_timestamp DESC, id DESC LIMIT 1), 0)"
        )

    set_clauses = ",\n                ".join(
        f"{field} = {_latest_result_subquery(field)}" for field in OCID_COMPLIANCE_STATE_BOOLEAN_FIELDS
    )
    audit_done_clause = (
        "audit_done = CASE WHEN EXISTS (SELECT 1 FROM ocid_compliance_audit_log "
        "WHERE ocid_number = NEW.ocid_number AND umr_id = NEW.umr_id) THEN 1 ELSE 0 END"
    )
    audit_passed_terms = " AND ".join(
        f"{_latest_result_subquery(field)} = 1" for field in OCID_COMPLIANCE_STATE_RULE_FIELDS
    )
    audit_passed_clause = f"audit_passed = CASE WHEN {audit_passed_terms} THEN 1 ELSE 0 END"

    for trigger_name, event in (
        ("ocid_compliance_state_derive_ai", "AFTER INSERT"),
        ("ocid_compliance_state_derive_au", "AFTER UPDATE"),
    ):
        conn.execute(f"""
            CREATE TRIGGER IF NOT EXISTS {trigger_name}
            {event} ON ocid_compliance_state
            BEGIN
                UPDATE ocid_compliance_state SET
                {set_clauses},
                {audit_done_clause},
                {audit_passed_clause}
                WHERE rowid = NEW.rowid;
            END;
        """)
    conn.commit()


def _rule_mechanism_existed(ts_submitted, merged_at, pr_ref):
    """Real, honest comparison: could this specific real UMR possibly have
    been subject to a rule whose own real mechanism did not exist yet at
    its real mint time? Returns (True/False/None, note) -- False means "the
    mechanism did not exist yet, honestly record false, not skipped"; None
    means "cannot determine (no real ts_submitted on this row)", which the
    caller also honestly records as a real None result, never guessed."""
    if not ts_submitted:
        return None, (f"no real ts_submitted on this umr_tasks row; cannot compare against "
                       f"{pr_ref}'s real merge date {merged_at}")
    try:
        ts_dt = datetime.fromisoformat(ts_submitted.replace("Z", "+00:00"))
        merged_dt = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
    except ValueError as exc:
        return None, f"could not parse a real timestamp for mechanism-existed comparison: {exc}"
    if ts_dt < merged_dt:
        return False, (f"this UMR's real ts_submitted={ts_submitted} is before {pr_ref}'s real merge "
                        f"date {merged_at}; the rule's own mechanism did not exist yet")
    return True, None


def _check_rule_1_umr_reuse(conn, ocid_number, umr_id, umr_row, all_umr_ids):
    """Real check: this umr_id was not minted while another real UMR for
    the same real OCID was already active (per that other UMR's own real
    ts_submitted/ts_completed window) at this umr's own real ts_submitted
    moment -- i.e. a real resume correctly reused the existing active UMR
    instead of minting a real duplicate. umr_tasks.ts_submitted/ts_completed
    are always written via this file's own _now_iso(), so plain string
    comparison of those two columns is real, valid ISO-8601 ordering."""
    ts = umr_row["ts_submitted"] if umr_row else None
    if not ts:
        return None, f"no real umr_tasks row found for {umr_id}; cannot verify reuse-on-resume"
    others = [u for u in all_umr_ids if u != umr_id]
    if not others:
        return True, f"{umr_id} is the only real UMR ever minted for {ocid_number}; no possible reuse violation"
    placeholders = ",".join("?" * len(others))
    rows = conn.execute(
        f"SELECT umr_id, ts_submitted, ts_completed FROM umr_tasks WHERE umr_id IN ({placeholders})",
        others,
    ).fetchall()
    overlapping = []
    for r in rows:
        r_start, r_end = r["ts_submitted"], r["ts_completed"] or "9999-12-31T23:59:59+00:00"
        if r_start and r_start <= ts <= r_end:
            overlapping.append(r["umr_id"])
    if overlapping:
        return False, (f"real umr_tasks row(s) {overlapping} were still real-active (no ts_completed) "
                        f"at/after {ts} when {umr_id} was minted for the same {ocid_number} -- real "
                        f"reuse-on-resume violation")
    return True, (f"no other real UMR among {others} for {ocid_number} was active when {umr_id} "
                  f"(ts_submitted={ts}) was minted")


def _check_rule_2_outcome_classification(umr_row):
    """Real check: this UMR's own real status is a real, recognized
    classified outcome (PR #29's own real 5-canonical-outcomes vocabulary
    plus the 2 real active pre-outcome states), grounded directly in the
    real umr_tasks.status column, never inferred."""
    if not umr_row:
        return None, "no real umr_tasks row found"
    # UMR-20260806-130914-e7f1: 'completed_unmerged' is real, ts_completed-bearing,
    # terminal-for-AI-work-purposes vocabulary too (see UMR_STATUSES's own comment) --
    # excluding it here would misclassify a real, honestly-recorded row as a
    # compliance violation.
    valid_terminal = {"completed", "completed_unmerged", "failed", "killed", "rejected_duplicate"}
    valid_active = {"queued", "dispatched", "running"}
    status = umr_row["status"]
    if status in valid_terminal or status in valid_active:
        return True, f"real status={status!r} is in the known real classified vocabulary {sorted(valid_terminal | valid_active)}"
    return False, f"real status={status!r} is NOT in the known real classified vocabulary {sorted(valid_terminal | valid_active)}"


def _check_rule_3_no_premature_minting(umr_row, ocid_number):
    """Real check (PR #30: 'validate inputs.ocid_number before any UMR
    mint'): this UMR's own real task_identity/inputs_json/metadata_json
    text actually contains a real reference to this ocid_number -- real
    evidence the ocid_number was recorded/validated at mint time, rather
    than a UMR existing with no real, discoverable tie back to the OCID it
    is being audited under."""
    if not umr_row:
        return None, "no real umr_tasks row found"
    text = " ".join(str(umr_row[c]) for c in ("task_identity", "inputs_json", "metadata_json") if umr_row[c] is not None)
    variants = [ocid_number.lower(), ocid_number.upper(), ocid_number.replace("-", "_").lower()]
    text_lower = text.lower()
    if any(v in text_lower for v in variants):
        return True, f"real {ocid_number} reference found in this umr_tasks row's own task_identity/inputs_json/metadata_json"
    return False, f"no real {ocid_number} reference found anywhere in this umr_tasks row's own text fields"


def _check_rule_4_pm_visible_counts(conn, umr_id):
    """Real check: this real umr_id is present and queryable via a real
    direct SELECT against umr_tasks -- the real precondition for it to be
    counted in any real PM-facing rollup."""
    row = conn.execute("SELECT umr_id FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
    if row:
        return True, f"real umr_tasks row for {umr_id} is present and queryable via a real SELECT"
    return False, f"no real umr_tasks row found for {umr_id}"


def _check_rule_5_stall_detection(umr_row):
    """Real check: a real terminal-status UMR must have a real
    ts_completed; a real still-active UMR must have a real last_heartbeat
    -- either missing is the real stall signature Rule 5's own mechanism
    (PR #33) is meant to catch."""
    if not umr_row:
        return None, "no real umr_tasks row found"
    status = umr_row["status"]
    if status in ("completed", "completed_unmerged", "failed", "killed", "rejected_duplicate"):
        ok = umr_row["ts_completed"] is not None
        return ok, f"real terminal status={status!r}; ts_completed={'present' if ok else 'MISSING'}"
    ok = umr_row["last_heartbeat"] is not None
    return ok, f"real active status={status!r}; last_heartbeat={'present' if ok else 'MISSING -- real stall risk'}"


def _check_rule_6_zero_duplication(conn, ocid_number, umr_id):
    """Real check: reuses find_active_umr_by_ocid() -- the exact real,
    already-merged Rule 6 mechanism function above -- rather than
    reimplementing a second, parallel duplication check."""
    active = find_active_umr_by_ocid(conn, ocid_number)
    if active is None:
        return True, f"real find_active_umr_by_ocid({ocid_number!r}) found no currently-active duplicate UMR"
    if active["umr_id"] == umr_id:
        return True, f"real find_active_umr_by_ocid({ocid_number!r}) found only {umr_id} itself active -- no duplicate"
    return False, (f"real find_active_umr_by_ocid({ocid_number!r}) found a DIFFERENT active UMR "
                    f"{active['umr_id']!r} alongside {umr_id} -- real duplication")


def _check_rule_7_structured_evidence(conn, ocid_number, umr_id):
    """Real check: at least one real row exists in ocid_artifact_links for
    this exact (ocid_number, umr_id) pair -- the real, already-merged (PR
    #20) structured-evidence table Rule 7's own mechanism (PR #35) requires."""
    row = conn.execute(
        "SELECT COUNT(*) c FROM ocid_artifact_links WHERE ocid_number=? AND umr_id=?", (ocid_number, umr_id)
    ).fetchone()
    count = row["c"]
    if count > 0:
        return True, f"real ocid_artifact_links has {count} real linkage row(s) for ({ocid_number}, {umr_id})"
    return False, f"real ocid_artifact_links has ZERO linkage rows for ({ocid_number}, {umr_id})"


def _check_file_tracking_fields(conn, canonical_row, runner=None):
    """Real file-tracking checks, grounded in ocid_canonical_registry's own
    real columns (file_path/commit_sha/pr_repo/merge_status -- populated by
    backfill_ocid_registry_phase2_columns.py's own real active gh fetches),
    plus a real `git show` existence check against a real local repo clone.
    `file_path_checked` and `file_checked` are intentionally closely related
    "an attempt was made" markers at two different checkpoints (path
    discovery vs. existence verification) -- documented honestly as
    overlapping by design, not claimed as two independently distinct
    signals they are not."""
    runner = runner or _default_ocid_resolver_runner
    file_path = canonical_row.get("file_path") if canonical_row else None
    file_path_available = bool(file_path)
    file_path_validated = False
    file_existing = False
    validated_detail = "no real file_path on record to validate"
    existing_detail = "no real file_path on record to check current existence"

    if file_path and canonical_row.get("pr_repo"):
        repo_path = DEFAULT_OCID_RESOLVER_REPO_LOCAL_PATHS.get(canonical_row["pr_repo"])
        if repo_path:
            if canonical_row.get("commit_sha"):
                cmd = ["git", "show", f"{canonical_row['commit_sha']}:{file_path}"]
                try:
                    result = runner(cmd, repo_path)
                    file_path_validated = getattr(result, "returncode", 1) == 0
                    validated_detail = f"git show {canonical_row['commit_sha']}:{file_path} (in {repo_path}) -> returncode={getattr(result, 'returncode', None)}"
                except Exception as exc:
                    validated_detail = f"real git show failed: {exc}"
            else:
                validated_detail = "no real commit_sha on record; cannot validate file existed at a specific real commit"
            cmd2 = ["git", "show", f"HEAD:{file_path}"]
            try:
                result2 = runner(cmd2, repo_path)
                file_existing = getattr(result2, "returncode", 1) == 0
                existing_detail = f"git show HEAD:{file_path} (in {repo_path}) -> returncode={getattr(result2, 'returncode', None)}"
            except Exception as exc:
                existing_detail = f"real git show failed: {exc}"

    file_work_implemented = bool(
        canonical_row and canonical_row.get("merge_status") == "merged" and canonical_row.get("commit_sha")
    )
    return {
        "file_path": file_path,
        "file_path_checked": True,
        "file_checked": True,
        "file_path_available": file_path_available,
        "file_path_validated": file_path_validated,
        "file_existing": file_existing,
        "file_work_implemented": file_work_implemented,
        "_raw": {
            "file_path_checked": "this real audit run always attempts real file-path discovery from ocid_canonical_registry",
            "file_checked": "this real audit run always attempts a real existence check when a file_path is available",
            "file_path_available": f"ocid_canonical_registry.file_path = {file_path!r}",
            "file_path_validated": validated_detail,
            "file_existing": existing_detail,
            "file_work_implemented": (
                f"ocid_canonical_registry.merge_status={canonical_row.get('merge_status') if canonical_row else None!r}, "
                f"commit_sha={'present' if (canonical_row and canonical_row.get('commit_sha')) else 'absent'}"
            ),
        },
    }


def record_ocid_compliance_audit(conn, ocid_number, umr_id, results, audited_by, now, file_path, audit_done, audit_passed):
    """The ONLY real write path for ocid_compliance_state. Always writes a
    matching ocid_compliance_audit_log row per field (real, verbatim
    raw_output) in the SAME real transaction as the ocid_compliance_state
    upsert -- caller commits once, after this returns -- so current state
    and full history can never drift apart. `results` is a dict of
    field_name -> (bool_or_None, raw_output_str); a None result (real
    "cannot determine", e.g. no umr_tasks row found at all) is stored as a
    real NULL in the audit log's own `result` column (preserving that exact
    nuance for history) but coerced to 0/false in the summary
    ocid_compliance_state row (a real, honest "not verified", not a
    fabricated pass)."""
    for field, (result, raw_output) in results.items():
        conn.execute(
            "INSERT INTO ocid_compliance_audit_log "
            "(ocid_number, umr_id, audit_timestamp, rule_or_field_name, result, raw_output, audited_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ocid_number, umr_id, now, field, None if result is None else (1 if result else 0), raw_output, audited_by),
        )
    cols = {k: (1 if v[0] is True else 0) for k, v in results.items()}
    conn.execute("""
        INSERT INTO ocid_compliance_state
            (ocid_number, umr_id, rule_1_umr_reuse_verified, rule_2_outcome_classification_verified,
             rule_3_no_premature_minting_verified, rule_4_pm_visible_counts_verified,
             rule_5_stall_detection_verified, rule_6_zero_duplication_verified,
             rule_7_structured_evidence_verified, file_path, file_path_checked, file_checked,
             file_path_available, file_path_validated, file_existing, file_work_implemented,
             audit_done, audit_passed, last_audit_timestamp)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(ocid_number, umr_id) DO UPDATE SET
            rule_1_umr_reuse_verified=excluded.rule_1_umr_reuse_verified,
            rule_2_outcome_classification_verified=excluded.rule_2_outcome_classification_verified,
            rule_3_no_premature_minting_verified=excluded.rule_3_no_premature_minting_verified,
            rule_4_pm_visible_counts_verified=excluded.rule_4_pm_visible_counts_verified,
            rule_5_stall_detection_verified=excluded.rule_5_stall_detection_verified,
            rule_6_zero_duplication_verified=excluded.rule_6_zero_duplication_verified,
            rule_7_structured_evidence_verified=excluded.rule_7_structured_evidence_verified,
            file_path=excluded.file_path,
            file_path_checked=excluded.file_path_checked,
            file_checked=excluded.file_checked,
            file_path_available=excluded.file_path_available,
            file_path_validated=excluded.file_path_validated,
            file_existing=excluded.file_existing,
            file_work_implemented=excluded.file_work_implemented,
            audit_done=excluded.audit_done,
            audit_passed=excluded.audit_passed,
            last_audit_timestamp=excluded.last_audit_timestamp
    """, (
        ocid_number, umr_id,
        cols["rule_1_umr_reuse_verified"], cols["rule_2_outcome_classification_verified"],
        cols["rule_3_no_premature_minting_verified"], cols["rule_4_pm_visible_counts_verified"],
        cols["rule_5_stall_detection_verified"], cols["rule_6_zero_duplication_verified"],
        cols["rule_7_structured_evidence_verified"], file_path,
        cols["file_path_checked"], cols["file_checked"], cols["file_path_available"],
        cols["file_path_validated"], cols["file_existing"], cols["file_work_implemented"],
        1 if audit_done else 0, 1 if audit_passed else 0, now,
    ))


def run_ocid_compliance_audit(conn, ocid_number, umr_id, all_umr_ids, canonical_row, audited_by, runner=None):
    """The one real, mechanical, zero-AI-judgment (beyond what the fixed
    check functions above already encode) entry point that computes every
    real rule/field result for a single (ocid_number, umr_id) pair and
    writes both real tables together via record_ocid_compliance_audit()."""
    runner = runner or _default_ocid_resolver_runner
    umr_row = conn.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
    umr_row = dict(umr_row) if umr_row else None
    ts_submitted = umr_row["ts_submitted"] if umr_row else None

    rule_checkers = {
        "rule_1_umr_reuse_verified": lambda: _check_rule_1_umr_reuse(conn, ocid_number, umr_id, umr_row, all_umr_ids),
        "rule_2_outcome_classification_verified": lambda: _check_rule_2_outcome_classification(umr_row),
        "rule_3_no_premature_minting_verified": lambda: _check_rule_3_no_premature_minting(umr_row, ocid_number),
        "rule_4_pm_visible_counts_verified": lambda: _check_rule_4_pm_visible_counts(conn, umr_id),
        "rule_5_stall_detection_verified": lambda: _check_rule_5_stall_detection(umr_row),
        "rule_6_zero_duplication_verified": lambda: _check_rule_6_zero_duplication(conn, ocid_number, umr_id),
        "rule_7_structured_evidence_verified": lambda: _check_rule_7_structured_evidence(conn, ocid_number, umr_id),
    }

    results = {}
    for field, checker in rule_checkers.items():
        merged_at, pr_ref = OCID_068_RULE_MECHANISM_MERGED_AT[field]
        existed, existed_note = _rule_mechanism_existed(ts_submitted, merged_at, pr_ref)
        if existed is False:
            results[field] = (False, f"mechanism did not exist yet: {existed_note}")
        elif existed is None:
            results[field] = (None, existed_note)
        else:
            results[field] = checker()

    file_fields = _check_file_tracking_fields(conn, canonical_row, runner=runner)
    for k in ("file_path_checked", "file_checked", "file_path_available", "file_path_validated",
              "file_existing", "file_work_implemented"):
        results[k] = (file_fields[k], file_fields["_raw"].get(k, f"{k}={file_fields[k]}"))

    now = _now_iso()
    rule_values = [v[0] for k, v in results.items() if k.startswith("rule_")]
    audit_passed = all(v is True for v in rule_values)

    record_ocid_compliance_audit(
        conn, ocid_number, umr_id, results, audited_by=audited_by, now=now,
        file_path=file_fields["file_path"], audit_done=True, audit_passed=audit_passed,
    )
    return {
        "ocid_number": ocid_number, "umr_id": umr_id,
        "results": {k: v[0] for k, v in results.items()},
        "audit_passed": audit_passed,
    }


def query_ocid_compliance_state(conn, ocid_number=None):
    """Real, read-only lookup -- a single OCID's real compliance-state rows
    (one row per real (ocid_number, umr_id) pair, since ocid_compliance_state's
    real PRIMARY KEY is composite), or the whole real roster (ordered by
    ocid_number, umr_id) when called with no argument. Zero writes, zero
    audit re-run, zero subprocess/network calls -- every column returned here
    (all 13 real rule_*/file_* booleans plus audit_done/audit_passed) is
    already trigger-derived by ocid_compliance_state_derive_ai/_au
    (_ensure_ocid_compliance_state_derive_triggers()) at write time, so this
    function only ever reads back what those triggers already computed from
    ocid_compliance_audit_log's own real, append-only evidence -- it never
    computes anything itself.

    This is the one real read path audit_ocid_compliance.py's `--report` flag
    calls (Owner directive UMR-20260805-093138-2bd0's real report command,
    citing UMR-20260805-092408-4f97): a PM (human or AI) runs
    `audit_ocid_compliance.py --report` and reports this function's own
    output verbatim, performing no analysis or interpretation of its own."""
    if ocid_number:
        rows = conn.execute(
            "SELECT * FROM ocid_compliance_state WHERE ocid_number=? ORDER BY umr_id",
            (ocid_number,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM ocid_compliance_state ORDER BY ocid_number, umr_id"
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Real UTR/UMR/single-source-of-truth taxonomy, recorded at the source
# (UMR-20260805-093630-29d1, citing UMR-20260804-170055-a069, OCID-068, and
# the schema work under UMR-20260805-090549-9710 / UMR-20260805-093138-2bd0).
# No existing table in this file already serves this purpose (checked via
# a direct sqlite_master query for schema/metadata/note/taxonomy-named
# tables before adding this one) -- a real, small, dedicated table so any
# future real query against this database, human or AI, finds this
# taxonomy explained at the source, not needing to infer it from scattered
# reports.
# ---------------------------------------------------------------------------

REGISTRY_TAXONOMY_UTR_UMR_NOTE = (
    "UTR (Universal Task Registry) = the real `umr_tasks` table in this same "
    "database, covering every real dispatched task (one row per real task, "
    "keyed by `umr_id`, tracked from `queued` through a real terminal "
    "status). UMR (Universal Metadata Registry) = the real, broader "
    "knowledge/metadata layer this whole platform uses; the "
    "`UMR-YYYYMMDD-HHMMSS-hash` identifiers already used throughout this "
    "entire system (umr_tasks.umr_id, ocid_canonical_registry."
    "canonical_umr_id/all_umr_ids_json, ocid_artifact_links.umr_id, "
    "ocid_compliance_state/ocid_compliance_audit_log.umr_id, and every "
    "real Owner-directive citation in every real commit/PR this session) "
    "are the real individual entries within that UMR layer, not a separate "
    "identifier scheme. This database file itself "
    "(/opt/veridian/ai-os/memory/superboss-register.sqlite) is the one "
    "real place of truth: it houses the UTR (umr_tasks) and the UMR layer "
    "together with ocid_canonical_registry (per-OCID canonical-UMR/PR/"
    "commit/file rollup + completion gate), ocid_artifact_links (many-to-"
    "many OCID/UMR/PR/commit/file linkage graph), and ocid_compliance_state"
    "/ocid_compliance_audit_log (per-OCID-068-seven-rule compliance state + "
    "append-only history) -- all cross-referencing the same real `umr_id` "
    "values, never a second parallel identifier space. "
    "(UMR-20260805-093630-29d1, citing UMR-20260804-170055-a069, "
    "UMR-20260805-090549-9710, UMR-20260805-093138-2bd0.)"
)


def _ensure_registry_taxonomy_notes_table(conn):
    """Idempotent CREATE TABLE IF NOT EXISTS, same convention as every
    other _ensure_*_table function in this file. One real row per real
    `note_key` (PRIMARY KEY -- an upsert-by-key replaces it, same pattern
    as ocid_canonical_registry's own per-OCID upsert), so a future real
    correction to this taxonomy replaces the existing real note rather than
    accumulating stale duplicates."""
    conn.execute("""CREATE TABLE IF NOT EXISTS registry_taxonomy_notes (
        note_key TEXT PRIMARY KEY,
        note_text TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    )""")
    conn.commit()


def record_registry_taxonomy_note(conn, note_key, note_text):
    """Real, idempotent upsert -- re-running this with the same note_key
    and updated note_text is always safe, same ON CONFLICT DO UPDATE
    convention as upsert_ocid_canonical_registry() above. Caller owns
    conn/transaction/commit."""
    conn.execute("""
        INSERT INTO registry_taxonomy_notes (note_key, note_text, recorded_at)
        VALUES (?, ?, ?)
        ON CONFLICT(note_key) DO UPDATE SET
            note_text=excluded.note_text,
            recorded_at=excluded.recorded_at
    """, (note_key, note_text, _now_iso()))


def _seed_registry_taxonomy_notes(conn):
    """Real, idempotent seed of the one real UTR/UMR taxonomy note --
    called from _migrate_schema() alongside _ensure_registry_taxonomy_notes_table()
    so it is always present on any real DB this module touches, exactly
    like every other real, permanent registry content in this file."""
    record_registry_taxonomy_note(conn, "utr_umr_single_source_of_truth_taxonomy", REGISTRY_TAXONOMY_UTR_UMR_NOTE)
    conn.commit()


def find_most_recent_umr_by_identity(conn, task_identity):
    """OCID-068 seven-rule guardrails addendum, Rule 1 (UMR-20260804-180711-7f96,
    UMR-20260804-194355-be9c): "one logical task shall have exactly one OCID,
    exactly one UMR... and any retry, resume, redispatch, supervisor, worker,
    executor, or restart shall reuse the existing UMR rather than minting a
    new one." find_active_umr_by_identity() above only sees ACTIVE
    (queued/dispatched/running) rows -- by design, since it exists to reject a
    racing SECOND live submission, not to find history. This function is the
    complement: any row at all for task_identity, active or terminal, most
    recent first. Used by submit() to decide whether a resume/retry should
    reuse a prior (now-terminal) UMR id instead of minting a fresh one -- the
    real, previously-documented gap this closes (see this file's own
    resource_governor.py callers and the module comment above
    find_active_umr_by_identity() describing exactly this limitation).

    Real fix (independent review, PR #26 round 1): excludes
    status='rejected_duplicate' rows. The primary real caller,
    resource_governor.py's resume_interrupted_workers_tick() path, calls
    submit() every tick for a task that is still active -- each of those
    calls, while the real row is queued/dispatched/running, inserts a
    rejected_duplicate STUB row (via find_active_umr_by_identity()'s own
    rejection path) with a LATER ts_submitted than the real row it rejected.
    Without this exclusion, a genuine later resume (after the real row
    finally goes terminal) would pick the newest rejected_duplicate stub
    instead of the real historical row -- grafting the resume onto a
    spurious placeholder rather than continuing the real UMR's own history,
    the opposite of this function's purpose."""
    row = conn.execute(
        "SELECT * FROM umr_tasks WHERE task_identity=? AND status != 'rejected_duplicate' "
        "ORDER BY ts_submitted DESC LIMIT 1",
        (task_identity,),
    ).fetchone()
    return dict(row) if row else None


def upsert_umr_task(conn, record):
    """Insert-or-replace ONE umr_tasks row keyed on umr_id (generated here if
    not supplied). Does NOT commit -- caller (resource_governor.py) owns the
    transaction/commit, same convention register_entity_row() documents for
    wiring_registry. Returns the umr_id.

    record["tenant_id"] (Stage 10 END_USER_ENGINE foundation, 2026-07-29):
    optional, defaults to None via record.get() below -- every existing real
    caller omits this key entirely and gets NULL, same as before this field
    existed (100% backward compatible). Treated as an immutable
    submission-time classification field: written once on INSERT and
    deliberately NOT part of the ON CONFLICT DO UPDATE SET list, so a later
    re-upsert of the same umr_id can never silently overwrite/clear the
    tenant a task was originally submitted under.

    Real fix (independent review, PR #26 round 2): tier/source_trigger/
    task_kind/inputs_json ARE now part of the ON CONFLICT DO UPDATE SET list
    (they previously were not, on the same "immutable submission-time field"
    reasoning as tenant_id above -- that reasoning was wrong for these four
    specifically, since before OCID-068 Rule 1's UMR-reuse-on-resume feature
    this function's ON CONFLICT branch was never actually exercised by any
    real caller with a genuinely different new inputs/tier/source_trigger for
    an existing umr_id; the branch was dead code in practice). The real,
    confirmed bug this closes: dispatch-tick.py's
    resume_interrupted_workers_tick() resubmits a stuck task with a corrected
    inputs.action ('reset_failed_and_start' vs 'start', based on the unit's
    live ActiveState) and tier=1 (to intentionally outrank brand-new
    dispatch) on every resume -- resource_governor.py's _perform_spawn()
    reads inputs.action straight off the DB row at dispatch time, not from
    any in-memory task_spec. Before this fix, reusing a terminal row's umr_id
    silently discarded that corrected action/tier and kept the FIRST-EVER
    submission's stale values forever, meaning a resume after a real systemd
    start-limit trip (ActiveState=failed) would keep issuing a plain
    'systemctl start' instead of the required 'reset-failed' first -- making
    stuck-worker auto-resume worse, not better, exactly the scenario Rule 1
    exists to fix. tenant_id and the five UTM fields remain deliberately
    excluded (unaffected by this fix): they are genuinely immutable
    submission-time facts about WHO/WHERE a task originated, not per-attempt
    dispatch parameters like inputs/tier/source_trigger.

    Phase 6 (task-umr-tasks-utm-correlation-phase6-2026-07-29): every INSERT
    also populates the five UTM fields via _derive_umr_utm_fields(record) --
    real values derived from this same record's task_identity/source_trigger/
    task_kind/unit_name, not placeholders -- same "compute at the one real
    call site" pattern register_capability() uses for capability_registry.
    Deliberately NOT part of the ON CONFLICT DO UPDATE SET list, same reason
    tenant_id is excluded above: fixed at INSERT time, never silently
    overwritten by a later re-upsert -- every real status transition goes
    through update_umr_task()'s partial UPDATE instead, which never touches
    these columns either, and (as of Rule 1's UMR-reuse-on-resume feature,
    PR #26) this function's ON CONFLICT branch genuinely does fire in real
    production use, not just in theory."""
    umr_id = record.get("umr_id") or _new_id("UMR")
    now = _now_iso()
    utm = _derive_umr_utm_fields(record)
    conn.execute(
        "INSERT INTO umr_tasks (umr_id, task_identity, ts_submitted, tier, status, source_trigger, "
        "task_kind, unit_name, tenant_id, inputs_json, outputs_json, logs_ref, metric_snapshot_json, "
        "ts_dispatched, ts_sigterm, ts_completed, reason, metadata_json, "
        "utm_source, utm_medium, utm_campaign, utm_content, utm_term) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(umr_id) DO UPDATE SET status=excluded.status, unit_name=excluded.unit_name, "
        "outputs_json=excluded.outputs_json, logs_ref=excluded.logs_ref, "
        "metric_snapshot_json=excluded.metric_snapshot_json, ts_dispatched=excluded.ts_dispatched, "
        "ts_sigterm=excluded.ts_sigterm, ts_completed=excluded.ts_completed, reason=excluded.reason, "
        "metadata_json=excluded.metadata_json, "
        "tier=excluded.tier, source_trigger=excluded.source_trigger, task_kind=excluded.task_kind, "
        "inputs_json=excluded.inputs_json",
        (
            umr_id, record["task_identity"], record.get("ts_submitted") or now,
            record["tier"], record.get("status", "queued"), record["source_trigger"],
            record.get("task_kind", "systemctl_action"), record.get("unit_name"), record.get("tenant_id"),
            json.dumps(record.get("inputs") or {}), json.dumps(record.get("outputs") or {}),
            record.get("logs_ref"), json.dumps(record["metric_snapshot"]) if record.get("metric_snapshot") is not None else None,
            record.get("ts_dispatched"), record.get("ts_sigterm"), record.get("ts_completed"),
            record.get("reason"), json.dumps(record.get("metadata") or {}),
            utm["utm_source"], utm["utm_medium"], utm["utm_campaign"], utm["utm_content"], utm["utm_term"],
        ),
    )
    return umr_id


_GTM_CATEGORY_MUTABLE_COLUMNS = ("child_umr_id", "fix_commit", "fix_file_path", "fix_pr_number")


def update_gtm_certification_category(conn, category_index, **fields):
    """Partial UPDATE of one gtm_certification_categories row, keyed on its
    real category_index. Ported onto current main from PR #165
    (UMR-20260806-114728-d469's own governance-linkage + evidence-
    completeness fix -- all 25 rows were found sharing one shared, itself-
    failed child_umr_id, and only category 3 carried any fix_pr_number).
    PR #165 itself was found stale (branched 25 commits behind current
    main -- merging it as-is would have silently deleted the since-merged
    mark-umr-relay-attempted/requeue-build-lock-contended functionality),
    so this is a fresh, rebased port of just this isolated, additive piece
    rather than a merge of that PR (UMR-20260806-161614-5850).

    Deliberately restricted to _GTM_CATEGORY_MUTABLE_COLUMNS -- child_umr_id
    and the three fix_* columns -- and nothing else. evidence_json,
    evidence_summary, passed, and validated_at are structurally excluded:
    this function raises rather than silently ignoring an attempt to touch
    them, so a real validated result already sitting on a row (most rows
    have one) can never be overwritten by a later governance-linkage pass.
    A verdict change (passed) is explicitly never this function's job.

    Does NOT commit -- caller owns the transaction/commit, same convention
    as update_umr_task()/upsert_umr_task() above. Always stamps
    last_updated_at with a real current timestamp on any real write."""
    unknown = set(fields) - set(_GTM_CATEGORY_MUTABLE_COLUMNS)
    if unknown:
        raise ValueError(
            f"update_gtm_certification_category: refusing to write protected/unknown "
            f"column(s) {sorted(unknown)} -- only {_GTM_CATEGORY_MUTABLE_COLUMNS} are "
            f"mutable through this function (evidence_json/evidence_summary/passed/"
            f"validated_at are never touched here, by design)"
        )
    if not fields:
        return
    set_clauses, values = [], []
    for column, value in fields.items():
        set_clauses.append(f"{column}=?")
        values.append(value)
    set_clauses.append("last_updated_at=?")
    values.append(_now_iso())
    values.append(category_index)
    conn.execute(
        f"UPDATE gtm_certification_categories SET {', '.join(set_clauses)} WHERE category_index=?",
        values,
    )


def cmd_update_gtm_category(args):
    """CLI entry point for update_gtm_certification_category() above, under
    _write_lock() -- same convention as cmd_mark_umr_dispatched/
    cmd_mark_umr_terminal. Refuses (via update_gtm_certification_category's
    own ValueError) to touch anything but child_umr_id/fix_commit/
    fix_file_path/fix_pr_number.

    Usage:
      python3 superboss-register.py update-gtm-category --category-index N \\
          [--child-umr-id UMR-...] [--fix-commit SHA] [--fix-file-path PATH] \\
          [--fix-pr-number N]
    """
    init_db_silent()
    conn = _connect()
    fields = {}
    if args.child_umr_id is not None:
        fields["child_umr_id"] = args.child_umr_id
    if args.fix_commit is not None:
        fields["fix_commit"] = args.fix_commit
    if args.fix_file_path is not None:
        fields["fix_file_path"] = args.fix_file_path
    if args.fix_pr_number is not None:
        fields["fix_pr_number"] = args.fix_pr_number
    existing = conn.execute(
        "SELECT category_index FROM gtm_certification_categories WHERE category_index=?",
        (args.category_index,),
    ).fetchone()
    if not existing:
        conn.close()
        print(json.dumps({"error": f"no gtm_certification_categories row for "
                                    f"category_index={args.category_index}"}))
        sys.exit(1)
    with _write_lock():
        update_gtm_certification_category(conn, args.category_index, **fields)
        conn.commit()
    conn.close()
    print(json.dumps({"category_index": args.category_index, "updated": fields}, indent=2, default=str))


def list_gtm_certification_categories(conn):
    """Real, read-only listing of every real gtm_certification_categories
    row -- the one real reuse point pm-sentinel-tick.sh's Check 4
    (2026-08-15 Owner directive: 'update /pm, PM-in-server, veridian-server-
    sentinel, PM-in-desktop to complete Part3+4 with minimum tokens, real
    work, audit, and completion certificate', governing UMR UMR-20260815-
    044235-a5e1) queries live every tick instead of hardcoding real category
    state -- the real gap count/list changes as real work lands, so it must
    never be cached across ticks or hardcoded into the caller. No CLI
    subcommand previously existed to read this table -- gtm_write_category_
    result.py / update-gtm-category above only ever WRITE one row at a time
    by category_index; nothing before this read every row back."""
    cur = conn.execute(
        "SELECT category_index, category_name, ocid_number, passed, "
        "evidence_summary, validated_at, last_updated_at "
        "FROM gtm_certification_categories ORDER BY category_index"
    )
    return [dict(r) for r in cur]


def cmd_list_gtm_categories(args):
    """CLI entry point for list_gtm_certification_categories() above.

    Usage:
      python3 superboss-register.py list-gtm-categories
    """
    init_db_silent()
    conn = _connect()
    categories = list_gtm_certification_categories(conn)
    conn.close()
    print(json.dumps({"categories": categories}, indent=2, default=str))


# ---------------------------------------------------------------------------
# GTM Part3+4 completion certificate (2026-08-15 Owner directive, governing
# UMR UMR-20260815-044235-a5e1) -- the one real, canonical write path for the
# "completion certificate" the directive requires. Reuses the existing real
# ocid_master_standard_audit_log (record_ocid_master_standard_audit_event()
# above) as its durable store, rather than inventing a second parallel
# append-only-log mechanism -- one new event_type, not a new table. Never
# self-certifies: record_gtm_part3_4_completion_certificate() below
# independently re-verifies its own caller-supplied evidence shape (every
# cited category real passed=1, every cited category real non-empty/non-
# placeholder evidence_summary) and raises rather than silently accepting a
# hollow or partial claim. Idempotent: one real certificate ever, not one
# per tick -- see gtm_part3_4_certificate_status() below.
# ---------------------------------------------------------------------------
GTM_PART3_4_CERTIFICATE_EVENT_TYPE = "gtm_part3_4_completion_certificate"

# Real, deliberately narrow set of placeholder evidence_summary values that
# must never be accepted as real completion evidence -- same "never fabricate
# a pass" discipline gtm_write_category_result.py's own docstring already
# documents for --result blocked. Compared case-insensitively, stripped.
_GTM_PLACEHOLDER_EVIDENCE_VALUES = {
    "", "tbd", "n/a", "na", "todo", "pending", "none", "placeholder",
    "-", "tbd.", "n/a.", "unknown", "coming soon",
}


def gtm_part3_4_certificate_status(conn):
    """Real, read-only, idempotency check: has a genuine Part3+4 GTM-
    certification completion certificate already been recorded? Returns the
    existing real ocid_master_standard_audit_log row (dict) if one exists
    (the earliest one, in the genuinely-unexpected event more than one was
    ever written), else None."""
    _ensure_ocid_master_standard_audit_log_table(conn)
    row = conn.execute(
        "SELECT id, ocid_number, umr_id, detail_json, recorded_at "
        "FROM ocid_master_standard_audit_log WHERE event_type=? "
        "ORDER BY recorded_at ASC LIMIT 1",
        (GTM_PART3_4_CERTIFICATE_EVENT_TYPE,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "ocid_number": row["ocid_number"],
        "umr_id": row["umr_id"],
        "detail": json.loads(row["detail_json"]),
        "recorded_at": row["recorded_at"],
    }


def record_gtm_part3_4_completion_certificate(conn, evidence, umr_id=None):
    """The one real, canonical write path for the Part3+4 GTM-certification
    completion certificate. `evidence` MUST be a real dict with a
    'categories' key holding the real, live gtm_certification_categories
    rows the caller just queried this same tick (list_gtm_certification_
    categories() above) -- never a fabricated or stale list. Re-verifies,
    independently of the caller, that every cited category real passed=1
    and carries a real non-empty, non-placeholder evidence_summary; raises
    ValueError (refuses to write anything) the instant either check fails,
    so a caller bug or a stale read can never produce a false certificate.

    Idempotent: if a real certificate already exists (gtm_part3_4_
    certificate_status()), returns that EXISTING row unchanged (created=
    False) instead of inserting a second one -- one real certificate ever,
    not one per tick.

    Does NOT commit -- caller owns the transaction (_write_lock()), same
    convention as update_gtm_certification_category()/
    record_ocid_master_standard_audit_event() above."""
    existing = gtm_part3_4_certificate_status(conn)
    if existing is not None:
        return existing, False
    categories = evidence.get("categories")
    if not isinstance(categories, list) or len(categories) == 0:
        raise ValueError(
            "record_gtm_part3_4_completion_certificate: evidence['categories'] "
            "must be a real, non-empty list of the actual queried "
            "gtm_certification_categories rows -- refusing to certify without it"
        )
    for c in categories:
        if c.get("passed") != 1:
            raise ValueError(
                f"record_gtm_part3_4_completion_certificate: category_index="
                f"{c.get('category_index')} real passed value is "
                f"{c.get('passed')!r}, not 1 -- refusing to certify while any "
                f"real gap remains (never self-certify on a stale/partial read)"
            )
        summary = (c.get("evidence_summary") or "").strip()
        if summary.lower() in _GTM_PLACEHOLDER_EVIDENCE_VALUES:
            raise ValueError(
                f"record_gtm_part3_4_completion_certificate: category_index="
                f"{c.get('category_index')} has empty/placeholder "
                f"evidence_summary ({summary!r}) -- refusing to certify on "
                f"unevidenced passed=1 (never accept passed=1 with empty "
                f"evidence as real)"
            )
    record_ocid_master_standard_audit_event(
        conn, GTM_PART3_4_CERTIFICATE_EVENT_TYPE, evidence,
        ocid_number="OCID-020", umr_id=umr_id,
    )
    return gtm_part3_4_certificate_status(conn), True


def cmd_record_gtm_part3_4_certificate(args):
    """CLI entry point for record_gtm_part3_4_completion_certificate() above,
    under _write_lock() -- same convention as cmd_update_gtm_category. Reads
    --evidence-json (the real, live gtm_certification_categories rows this
    same tick already queried via list-gtm-categories -- never re-derived or
    fabricated here). Idempotent: prints the existing real certificate
    (newly_created=false) rather than writing a duplicate if one already
    exists.

    Usage:
      python3 superboss-register.py record-gtm-part3-4-certificate \\
          --evidence-json '{"categories": [...]}' [--umr-id UMR-...]
    """
    try:
        evidence = json.loads(args.evidence_json)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"--evidence-json is not valid JSON: {e}"}))
        sys.exit(1)
    init_db_silent()
    conn = _connect()
    try:
        with _write_lock():
            cert, created = record_gtm_part3_4_completion_certificate(
                conn, evidence, umr_id=args.umr_id
            )
            conn.commit()
    except ValueError as e:
        conn.close()
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
    conn.close()
    print(json.dumps({"certificate": cert, "newly_created": created}, indent=2, default=str))


def update_umr_task(conn, umr_id, **fields):
    """Partial UPDATE of an existing umr_tasks row -- only the columns passed
    as keyword args are touched. json_fields are dicts that get json.dumps'd
    automatically before the UPDATE. Does NOT commit, same convention as
    upsert_umr_task()."""
    # files_touched (UMR-20260806-095416-b6f0, external-agent channel): a
    # real Python list in, JSON TEXT column out -- same auto-serialize
    # convention as outputs/metric_snapshot/metadata below, added here so
    # mark_external_agent_eligible() never hand-serializes it either.
    json_fields = {"outputs", "metric_snapshot", "metadata", "files_touched"}
    column_map = {"metric_snapshot": "metric_snapshot_json", "outputs": "outputs_json", "metadata": "metadata_json"}
    if not fields:
        return
    set_clauses, values = [], []
    for key, value in fields.items():
        column = column_map.get(key, key)
        set_clauses.append(f"{column}=?")
        values.append(json.dumps(value) if key in json_fields else value)
    values.append(umr_id)
    conn.execute(f"UPDATE umr_tasks SET {', '.join(set_clauses)} WHERE umr_id=?", values)


def insert_ocid_artifact_link(conn, ocid_number, umr_id, repo, link_kind,
                               pr_number=None, commit_sha=None, file_path=None):
    """OCID-068 real requirement addendum (UMR-20260804-170055-a069). Records
    one real (OCID, UMR, PR/commit/file) linkage row. Caller owns
    conn/transaction/commit, same convention as upsert_umr_task()/
    update_umr_task() above -- this function itself never commits.

    Idempotent by explicit pre-insert check (not by relying on the table's
    UNIQUE constraint alone, since SQLite treats NULL pr_number/file_path as
    always-distinct in a UNIQUE index -- see _ensure_ocid_artifact_links_table's
    own docstring): a second call with identical
    (ocid_number, umr_id, repo, pr_number, file_path) is a real no-op, returns
    the existing row's id rather than inserting a duplicate. This matters
    because both real call sites (resource_governor.py:submit(), a real
    duplicate-rejected resubmission still mints a fresh umr_id so this is
    naturally non-colliding there; supervisor-entrypoint.sh's merge retry
    path, where the SAME PR can be merged-checked more than once) can be
    called more than once for what is conceptually the same real link.

    Deliberately does NOT raise on a schema/constraint error -- callers at
    both real chokepoints (resource_governor.py's submit(), a function every
    real UMR creation on this platform depends on; supervisor-entrypoint.sh's
    PR create/merge steps, which every real autonomous merge depends on) must
    never have their own real, load-bearing operation broken by a failure in
    this purely additive traceability write. Returns None (not the row id) on
    any internal failure, logging nothing itself -- the caller decides
    whether/how to log, per its own existing best-effort-write conventions
    (matches src/instrumentation.ts's onRequestError design in the product
    codebase: "a failure writing the error record must never throw again")."""
    try:
        existing = conn.execute(
            "SELECT id FROM ocid_artifact_links WHERE ocid_number=? AND umr_id=? AND repo=? "
            "AND pr_number IS ? AND file_path IS ?",
            (ocid_number, umr_id, repo, pr_number, file_path),
        ).fetchone()
        if existing:
            return existing["id"]
        cur = conn.execute(
            "INSERT INTO ocid_artifact_links "
            "(ocid_number, umr_id, repo, pr_number, commit_sha, file_path, link_kind, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (ocid_number, umr_id, repo, pr_number, commit_sha, file_path, link_kind, _now_iso()),
        )
        return cur.lastrowid
    except Exception:
        return None


def query_ocid_artifact_links(conn, ocid_number=None, umr_id=None, repo=None, pr_number=None,
                               file_path=None, commit_sha=None, limit=50):
    """Real, read-only lookup -- deterministic linkage query, the whole point
    of this table existing (per the real Owner requirement this addendum
    implements): 'what closed OCID-X' or 'what OCID does PR/commit Y belong
    to', without re-deriving it from governance-doc prose.

    OCID-068 Phase 2 (UMR-20260805-090549-9710): `file_path` and
    `commit_sha` are the real reverse-direction filters this same,
    already-existing (PR #20) linkage graph was missing -- forward lookups
    ('what closed OCID-X') were already covered by ocid_number/umr_id/
    repo/pr_number above; these two answer the reverse direction ('given a
    real file_path or commit_sha, find every real OCID/UMR it belongs to')
    over the exact same table, not a second parallel mechanism."""
    clauses, params = [], []
    if ocid_number:
        clauses.append("ocid_number=?"); params.append(ocid_number)
    if umr_id:
        clauses.append("umr_id=?"); params.append(umr_id)
    if repo:
        clauses.append("repo=?"); params.append(repo)
    if pr_number is not None:
        clauses.append("pr_number=?"); params.append(pr_number)
    if file_path:
        clauses.append("file_path=?"); params.append(file_path)
    if commit_sha:
        clauses.append("commit_sha=?"); params.append(commit_sha)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM ocid_artifact_links {where} ORDER BY created_at DESC LIMIT ?",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


# UMR-20260813-125756-9221: real, measured root cause (see
# _migrate_umr_tasks_status_ts_index()'s docstring for the index half of the
# fix) -- `SELECT *` on umr_tasks pulls inputs_json/outputs_json/
# metadata_json/metric_snapshot_json for every returned row even though
# --query-umr's normal listing use never renders them. Measured against the
# live register for this task: status='killed' alone averaged ~868KB of
# combined JSON-blob-column text per row (826 rows, ~717MB total) against a
# table where every OTHER column combined is small. query_umr_tasks() now
# selects this bounded, blob-free column list by default and only selects
# `*` when a caller explicitly passes full=True (resource_governor.py's
# --query-umr wires this to a new, explicit --full flag -- never the
# default), so a bounded --limit really does bound the resident row data,
# not just the row COUNT.
UMR_TASKS_LIGHT_COLUMNS = (
    "umr_id", "task_identity", "ts_submitted", "tier", "status", "source_trigger",
    "task_kind", "unit_name", "logs_ref", "ts_dispatched", "ts_sigterm", "ts_completed",
    "reason", "last_heartbeat", "tenant_id", "utm_source", "utm_medium", "utm_campaign",
    "utm_content", "utm_term", "external_agent_eligible", "external_agent_task_type",
    "blast_radius", "requires_multi_file_context", "files_touched", "external_agent_status",
    "external_agent_reject_count", "external_agent_dispatch_count", "ts_relay_attempted",
    "relay_outcome", "relay_detail",
)

# UMR-20260813-125756-9221: hard ceiling on --query-umr's own --limit, applied
# regardless of what a caller passes -- defense in depth alongside the real
# SQL-level LIMIT pushdown below (item C's CLI-level wall-clock/RSS guard is
# the other real backstop; this one bounds row COUNT specifically, at the
# one real place umr_tasks rows are ever queried).
MAX_UMR_QUERY_LIMIT = 2000


def _umr_select_columns(full):
    return "*" if full else ", ".join(UMR_TASKS_LIGHT_COLUMNS)


def _umr_row_to_dict(row):
    d = dict(row)
    for key in ("inputs_json", "outputs_json", "metric_snapshot_json", "metadata_json"):
        if d.get(key):
            d[key] = json.loads(d[key])
    return d


def query_umr_tasks(conn, limit=20, status=None, tier=None, task_identity=None, query_text=None,
                     umr_id=None, full=False, exclude_rca_complete=False):
    """Real search over umr_tasks -- exact umr_id match first (umr_id is the
    real PRIMARY KEY, so this can only ever return the one row it names or
    nothing -- real fix, UMR-20260813-042207: --query-umr --umr-id X
    previously fell all the way through to the plain-listing `else` branch
    below because umr_id was parsed by the CLI but never threaded into this
    function's call, silently ignoring X and returning the newest row
    instead, regardless of X), then exact task_identity match, then FTS5
    over task_identity/source_trigger/logs_ref for a free-text --search,
    else a plain filtered listing (newest first). Same two-stage resolution
    shape lookup_entity()/lookup_capability() already use.

    `full` (UMR-20260813-125756-9221): when False (the default), the SELECT
    excludes the large inputs_json/outputs_json/metadata_json/
    metric_snapshot_json blob columns -- see UMR_TASKS_LIGHT_COLUMNS's own
    comment for the real measured sizes this is fixing. Pass full=True to
    get every column, including those blobs, whenever a caller's own logic
    actually reads one of those columns -- e.g. inspecting one exact
    --umr-id row's inputs_json for debugging, or find_target_identifier_
    duplicate()'s real per-row inputs_json parse (real regression once
    fixed here at live-audit time on PR #308: that caller was silently
    defeated by the light-column default until it started passing
    full=True). Getting this wrong is silent, not an error -- the excluded
    columns just come back as missing keys -- so any new caller that reads
    inputs_json/outputs_json/metadata_json/metric_snapshot_json off these
    rows MUST pass full=True.

    `exclude_rca_complete` (real fix, UMR-20260814-013850-fd7f -- RCA of
    UMR-20260813-060311-6eea): pm-sentinel-tick.sh's Check 2a scans
    `--status killed --limit 15` every tick and dispatches a fresh RCA gap
    for EVERY row it gets back, with no check for whether a prior RCA
    already ran and wrote a real, evidenced verdict back into that row's
    own `reason` (the established convention across every RCA task in this
    codebase is a reason string starting literally with "RCA (UMR-...)" --
    see mark-umr-terminal call sites and e.g. this exact row's own reason
    after UMR-20260813-091810-5045 corrected it). Once dispatch-owner-task.sh's
    own 6h content-duplicate window lapses, the identical already-resolved
    row resurfaces and gets re-dispatched again, forever -- the exact
    "Killed-RCA mislabel series" recurring-churn pattern. Only applies to
    the plain-listing (no umr_id/task_identity/query_text) path -- those are
    exact-match/search lookups where a caller asking for one specific row by
    ID has no use for this filter, and filtering post-query there could make
    an intentional exact match vanish. False positives are possible (a
    legitimate non-RCA reason that happens to start with "RCA (") but this
    is a scoped opt-in filter a caller must explicitly request, not a
    default -- direct --umr-id lookups are completely unaffected."""
    limit = min(int(limit), MAX_UMR_QUERY_LIMIT) if limit else limit
    cols = _umr_select_columns(full)
    if umr_id:
        cur = conn.execute(
            f"SELECT {cols} FROM umr_tasks WHERE umr_id=? LIMIT ?",
            (umr_id, limit),
        )
        rows = list(cur)
    elif task_identity:
        cur = conn.execute(
            f"SELECT {cols} FROM umr_tasks WHERE task_identity=? ORDER BY ts_submitted DESC LIMIT ?",
            (task_identity, limit),
        )
        rows = list(cur)
    elif query_text:
        q = _fts_query(query_text)
        fts_cols = "t.*" if full else ", ".join("t." + c for c in UMR_TASKS_LIGHT_COLUMNS)
        try:
            cur = conn.execute(
                f"SELECT {fts_cols} FROM umr_tasks_fts f JOIN umr_tasks t ON t.rowid = f.rowid "
                "WHERE umr_tasks_fts MATCH ? ORDER BY t.ts_submitted DESC LIMIT ?",
                (q, limit),
            )
            rows = list(cur)
        except sqlite3.OperationalError:
            rows = []
    else:
        clauses, params = [], []
        if status:
            clauses.append("status=?")
            params.append(status)
        if tier is not None:
            clauses.append("tier=?")
            params.append(tier)
        if exclude_rca_complete:
            clauses.append("(reason IS NULL OR reason NOT LIKE 'RCA (%')")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        # Real fix (UMR-20260813-125756-9221, see
        # _migrate_umr_tasks_status_ts_index()'s docstring): this ORDER BY
        # ts_submitted DESC LIMIT ? only really bounds the work done -- not
        # just the output -- because idx_umr_tasks_status_ts covers both the
        # WHERE and the ORDER BY, so SQLite can walk it directly instead of
        # falling back to `USE TEMP B-TREE FOR ORDER BY` (which would
        # materialize every matching row, blob columns included, before
        # LIMIT could apply). Confirmed via EXPLAIN QUERY PLAN.
        cur = conn.execute(
            f"SELECT {cols} FROM umr_tasks {where} ORDER BY ts_submitted DESC LIMIT ?", params
        )
        # Real fix (UMR-20260813-125756-9221): stream the cursor instead of
        # a single fetchall() -- the SQL LIMIT above already bounds the
        # result set, but building the Python list row-by-row (rather than
        # asking sqlite3 to buffer the whole result in one C call) keeps
        # this path from ever silently regressing back into "materialize
        # everything, then slice" if a future edit here drops the LIMIT.
        rows = [r for r in cur]

    matches = [_umr_row_to_dict(r) for r in rows]
    if status and (umr_id or task_identity or query_text):
        matches = [m for m in matches if m["status"] == status]
    if tier is not None and (umr_id or task_identity or query_text):
        matches = [m for m in matches if m["tier"] == tier]
    return matches


def query_work_item_token_usage(conn, limit=20):
    """Real query (task-20260814-180958 / UMR-20260814-180929-cbdd): the
    last `limit` real work_items rows carrying a real token_usage block in
    their metadata_json (written by task-gateway.py's cmd_start -- see
    count_tokens_real()/lookup_instruction_raw_text() there), newest first.
    work_items is small (a few thousand rows -- checked live before writing
    this), so a plain metadata_json LIKE scan is fine here; this does not
    need (and does not get) its own index or FTS5 table, unlike umr_tasks'
    much larger real row counts.

    Rows without a complete real token_usage.raw_prompt_tokens/
    final_prompt_tokens pair (every work_items row created before this
    instrumentation existed, or one whose linked instruction_id had no
    raw_text -- see lookup_instruction_raw_text()'s own None case) are
    skipped: this only ever reports on real, complete before/after pairs,
    never a partial or guessed one. Returns a list of dicts, newest first;
    empty list if no real dispatch has this data yet."""
    limit = max(int(limit), 1) if limit else 20
    cur = conn.execute(
        "SELECT work_item_id, ts, metadata_json FROM work_items "
        "WHERE metadata_json LIKE '%token_usage%' ORDER BY ts DESC LIMIT ?",
        (limit,),
    )
    rows = []
    for r in cur:
        try:
            meta = json.loads(r["metadata_json"] or "{}")
        except (TypeError, ValueError):
            continue
        tu = meta.get("token_usage") or {}
        raw_tokens = tu.get("raw_prompt_tokens")
        final_tokens = tu.get("final_prompt_tokens")
        if not isinstance(raw_tokens, int) or not isinstance(final_tokens, int) or raw_tokens <= 0:
            continue
        rows.append({
            "work_item_id": r["work_item_id"],
            "ts": r["ts"],
            "raw_prompt_tokens": raw_tokens,
            "final_prompt_tokens": final_tokens,
            "reduction_pct": tu.get("reduction_pct"),
        })
    return rows


def touch_umr_heartbeat(args):
    """2026-07-29 (Stage 3 reconciliation-sweep prerequisite): CLI entrypoint
    worker-entrypoint.sh/doc-worker-entrypoint.sh call to stamp
    last_heartbeat=now() on the ACTIVE umr_tasks row for --task-identity,
    using the exact same resolution find_active_umr_by_identity() already
    uses for de-duplication (same convention every other lookup path in this
    table follows, not a new one). Best-effort, never an error: a task
    whose dispatch never went through resource_governor.py (e.g. any legacy
    path that still calls systemctl directly) simply has no active umr_tasks
    row yet, and that is a normal, silent no-op here -- callers already wrap
    this in `|| true`, same convention as every other call into this script
    from those two entrypoints, but the function itself must not raise for
    that case either."""
    conn = _connect()
    _ensure_umr_table(conn)
    row = find_active_umr_by_identity(conn, args.task_identity)
    if row is None:
        conn.close()
        print(json.dumps({"ok": True, "updated": False, "reason": "no active umr_tasks row for this task_identity"}))
        return
    update_umr_task(conn, row["umr_id"], last_heartbeat=_now_iso())
    conn.commit()
    conn.close()
    print(json.dumps({"ok": True, "updated": True, "umr_id": row["umr_id"]}))


def _ensure_pm_decisions_pending_table(conn):
    """Standalone idempotent create for pm_decisions_pending (Deterministic
    PM Reporting Contract V3, UMR-20260805-181636-32f2, OCID-020) -- same
    defensiveness convention as _ensure_ocid_artifact_links_table/
    _ensure_umr_table above: works even on a DB that predates this table.

    Note on provenance (found during independent verification for this
    task, not assumed from the task's own SPEC): the standalone
    migrate_2026-08-05_pm_report_tables.py script that first created this
    table (commit 4797b71) only ever landed on an unmerged branch
    (feat/pm-report-v3-schema-umr20260805181636), never on main -- but the
    schema itself was already applied directly to the live
    superboss-register.sqlite, which really does have this table today.
    This CREATE TABLE IF NOT EXISTS is written to match that live schema
    exactly, so it is a true no-op there, and a real bootstrap on any DB
    that lacks the table (fresh init_db(), a restored backup, or a fresh
    clone that never had the standalone migration script run against it).

    generate_pm_report_v3.py (PR #91, already merged) already reads this
    table directly (get_pm_decisions_pending()) for its report's "PM
    decision required" section -- read-only, and out of scope to change
    here. Per the Owner's standing SOP (exactly one canonical read/write
    surface for superboss-register.sqlite), insert_pm_decision_pending()
    and resolve_pm_decision_pending() below are the only real write path
    into this table.

    Owner directive, standing mandate (task-20260806-034817, cites
    UMR-20260805-185000-e94f -- the same parent this table's own
    Deterministic PM Reporting Contract V3 work traces back to): the real
    AI-proposes/PM-decides/AI-completes child-UMR proposal gate for novel
    findings outside already-approved scope extends this same table rather
    than a second parallel one -- see PROGRESS.md for the honest
    extend-vs-new-table reasoning. decision_type distinguishes the two real
    row shapes this table now carries: 'pm_decision' (this function's
    original, unchanged shape -- insert_pm_decision_pending()/
    resolve_pm_decision_pending()) vs 'owner_proposal' (new --
    insert_owner_proposal()/decide_owner_proposal()/
    record_owner_proposal_completion() below). See
    _migrate_pm_decisions_pending_owner_proposal_columns() for the additive
    column migration."""
    conn.execute("""CREATE TABLE IF NOT EXISTS pm_decisions_pending (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        opened_ts TEXT NOT NULL,
        title TEXT NOT NULL,
        detail TEXT NOT NULL,
        options_json TEXT,
        recommended_option TEXT,
        related_umr TEXT,
        status TEXT NOT NULL DEFAULT 'open',
        closed_ts TEXT,
        closed_by TEXT,
        closed_note TEXT
    )""")
    conn.commit()
    _migrate_pm_decisions_pending_owner_proposal_columns(conn)


def _migrate_pm_decisions_pending_owner_proposal_columns(conn):
    """Additive ALTER TABLE ADD COLUMN for pm_decisions_pending's Owner/AI
    child-UMR proposal lifecycle (task-20260806-034817, cites
    UMR-20260805-185000-e94f) -- same "check PRAGMA table_info, ALTER if
    missing, no full-table rebuild" pattern as _migrate_umr_tenant_id/
    _migrate_umr_last_heartbeat above. Reuses the existing table rather than
    a second parallel one; see _ensure_pm_decisions_pending_table's own
    docstring above and PROGRESS.md for the honest extend-vs-new-table
    reasoning.

    decision_type TEXT NOT NULL DEFAULT 'pm_decision': every real row
    written before today is a real PM-decision row (the only real writer
    until this task), so the constant DEFAULT backfills existing rows
    correctly, not a placeholder guess. New 'owner_proposal' rows are only
    ever written by insert_owner_proposal() below.

    completed_ts/artifact_path/commit_sha/evidence: the third real lifecycle
    phase (open -> approved/redirected/held -> completed). Plain nullable
    TEXT, no DEFAULT -- record_owner_proposal_completion() below is the one
    real write path that ever populates them, once AI has actually
    implemented an approved proposal. They stay NULL for every real
    'pm_decision' row and for every 'owner_proposal' row not yet completed,
    by construction, same "NULL means genuinely not-yet, not a data-quality
    gap" convention _migrate_umr_tenant_id documents for tenant_id."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(pm_decisions_pending)").fetchall()}
    if "decision_type" not in cols:
        conn.execute(
            "ALTER TABLE pm_decisions_pending ADD COLUMN decision_type TEXT NOT NULL DEFAULT 'pm_decision'"
        )
        conn.commit()
    for col in ("completed_ts", "artifact_path", "commit_sha", "evidence"):
        if col not in cols:
            conn.execute(f"ALTER TABLE pm_decisions_pending ADD COLUMN {col} TEXT")
    conn.commit()
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pm_decisions_pending_decision_type "
        "ON pm_decisions_pending(decision_type, status)"
    )
    conn.commit()


def insert_pm_decision_pending(conn, title, detail, *, options=None,
                                recommended_option=None, related_umr=None,
                                decision_type="pm_decision"):
    """Opens one real PM decision row -- the one real write path into
    pm_decisions_pending (see _ensure_pm_decisions_pending_table's own
    docstring). `options` is a real Python list, typically of dicts shaped
    like {"option": ..., "detail": ..., "recommended": bool} (matching the
    one real backfilled row from 2026-08-05), JSON-encoded here so callers
    never hand-serialize, same convention as upsert_ocid_canonical_registry's
    evidence/all_umr_ids handling above. `recommended_option` is a short,
    real, plain-text label (e.g. "sqlite3 .recover"), not the JSON blob
    itself, so a reader (or generate_pm_report_v3.py) never has to parse
    options_json just to show which option is recommended. Every new row
    opens as real status 'open' -- only resolve_pm_decision_pending() below
    ever moves it out of that state. Caller owns conn/transaction/commit,
    same convention as insert_ocid_artifact_link()/update_umr_task() above
    -- this function itself never commits. Returns the new row's real
    integer id.

    `decision_type` (UMR-20260806-115605-854d, dead-zone auto-remediation
    audit log): defaults to 'pm_decision' -- byte-identical behavior to
    every real caller before this parameter existed (cmd_insert_pm_decision_pending
    never passes it). A caller may pass a different real, already-established
    decision_type (e.g. 'dead_zone_auto_remediation', reconcile_dispatched_dead_zone.py's
    own real audit-log write) so the row is structurally excluded from
    get_pm_decisions_pending()'s/get_owner_proposals_pending()'s own
    decision_type-scoped WHERE clauses (Section 7/8 of generate_pm_report_v3.py)
    without requiring a second parallel insert function -- 'owner_proposal'
    rows still go through insert_owner_proposal()'s own separate, unchanged
    INSERT, never through here."""
    cur = conn.execute(
        "INSERT INTO pm_decisions_pending "
        "(opened_ts, title, detail, options_json, recommended_option, related_umr, status, decision_type) "
        "VALUES (?, ?, ?, ?, ?, ?, 'open', ?)",
        (_now_iso(), title, detail,
         json.dumps(options) if options is not None else None,
         recommended_option, related_umr, decision_type),
    )
    return cur.lastrowid


def resolve_pm_decision_pending(conn, decision_id, *, closed_by, closed_note=None,
                                 status="resolved", require_decision_type=None):
    """Closes one real, currently-open pm_decisions_pending row -- the real
    counterpart to insert_pm_decision_pending() above, and (per the Owner's
    standing SOP that this script is the one canonical read/write surface
    for superboss-register.sqlite) the only real write path that ever
    moves a row's status away from 'open'. Idempotent by explicit
    pre-update `WHERE status='open'` guard (mirrors
    reconcile_umr_status_against_pr()'s own is_stale gating): resolving an
    already-closed row a second time is a real no-op, never a silent
    overwrite of the first real closed_ts/closed_by/closed_note -- callers
    learn that via this function's own return value (False) rather than
    having to re-query the row themselves. Caller owns conn/transaction/
    commit, same convention as insert_pm_decision_pending() above -- this
    function itself never commits. Returns True if a real open row was
    found and closed, False otherwise (already closed, or no such id).

    `require_decision_type` (added for decide_owner_proposal() below,
    task-20260806-034817): when given, also gates the UPDATE on
    decision_type=<that value>, so a caller for one real decision_type
    (e.g. 'owner_proposal') can never accidentally resolve a row of the
    other real decision_type ('pm_decision') by numeric id collision.
    Default None preserves this function's original, unmodified behavior
    for every existing 'pm_decision' caller -- zero behavior change there."""
    sql = ("UPDATE pm_decisions_pending SET status=?, closed_ts=?, closed_by=?, closed_note=? "
           "WHERE id=? AND status='open'")
    params = [status, _now_iso(), closed_by, closed_note, decision_id]
    if require_decision_type is not None:
        sql += " AND decision_type=?"
        params.append(require_decision_type)
    cur = conn.execute(sql, params)
    return cur.rowcount > 0


def update_pm_decision_pending(conn, decision_id, *, title=None, detail=None,
                                related_umr=None, recommended_option=None):
    """Updates one real, currently-open pm_decisions_pending row IN PLACE --
    added for the STALE-QUEUED aggregation fix (UMR-20260806-163738-4323,
    governing UMR-20260806-071025-1d28). Real incident this closes: prior to
    this, flag_stale_queued_tasks() (resource_governor.py) opened one new
    real row per stale umr_id, so 48 of 118 real open pm_decisions_pending
    rows (~41%) were the identical STALE-QUEUED condition repeated -- Section
    7 of the standing 10-minute PM report (generate_pm_report_v3.py) lists
    every open decision, and at that ratio it stopped supporting a real
    decision at all. The real fix keeps exactly one open aggregate row per
    ongoing condition, and this function is what lets a caller refresh that
    single row's real current count/detail as the real underlying condition
    changes, rather than resolving-and-reinserting (which would needlessly
    burn a fresh id and lose the row's real continuous opened_ts) or
    resorting to a raw UPDATE outside this script (which would violate the
    Owner's standing SOP that this script is the one canonical read/write
    surface for superboss-register.sqlite -- see
    _ensure_pm_decisions_pending_table's own docstring).

    Only touches the four real, editable-in-place fields a caller passes
    (any left None is left completely unchanged, same "only touch what you
    explicitly pass" convention as reconcile_umr_status_against_pr() above);
    options_json/recommended_option's sibling columns are deliberately not
    included here since no real caller has needed them yet -- add them the
    same way if one does. Explicit `WHERE status='open'` guard, same
    idempotent-safety convention as resolve_pm_decision_pending() above: a
    caller can never accidentally revive/edit an already-closed row by id
    collision. Caller owns conn/transaction/commit, same convention as
    insert_pm_decision_pending()/resolve_pm_decision_pending() above -- this
    function itself never commits. Returns True if a real open row was found
    and updated, False otherwise (already closed, or no such id)."""
    sets = []
    params = []
    if title is not None:
        sets.append("title=?")
        params.append(title)
    if detail is not None:
        sets.append("detail=?")
        params.append(detail)
    if related_umr is not None:
        sets.append("related_umr=?")
        params.append(related_umr)
    if recommended_option is not None:
        sets.append("recommended_option=?")
        params.append(recommended_option)
    if not sets:
        return False
    sql = f"UPDATE pm_decisions_pending SET {', '.join(sets)} WHERE id=? AND status='open'"
    params.append(decision_id)
    cur = conn.execute(sql, params)
    return cur.rowcount > 0


_OWNER_PROPOSAL_DECISIONS = ("approved", "redirected", "held")


def insert_owner_proposal(conn, issue, proposal, *, child_umr=None):
    """Opens one real Owner/AI child-UMR proposal row -- deposit half of the
    Owner's standing mandate (task-20260806-034817, cites
    UMR-20260805-185000-e94f): "thinking is by the Project Manager,
    execution is by AI agents, AI agents do not think for themselves" for
    real novel findings outside already-approved scope (not retroactive to
    already-authorized broad-category work in flight, e.g. the GTM script
    build -- that authorization already covers its own scope).

    `issue` is a real, plain-text statement of exactly what the issue is;
    `proposal` is a real, plain-text statement of exactly what AI proposes
    -- nothing implemented yet at this point, by design. Stored in this
    table's existing title/detail columns respectively (see
    _ensure_pm_decisions_pending_table's docstring for why this reuses
    pm_decisions_pending rather than a second parallel table).

    `child_umr` is the real UMR id this proposal is filed under -- minted
    here via _new_id("UMR") (same real ID-minting convention
    upsert_umr_task() already uses) when the caller doesn't already have
    one, so every real proposal always has one, never a placeholder. Stored
    in this table's existing related_umr column. Note this does NOT insert
    a row into umr_tasks itself -- the child UMR here identifies this real
    proposal/decision/completion record, not a dispatched worker task; a
    caller that also needs a real umr_tasks row for this child UMR (e.g. to
    actually dispatch the approved work) creates one separately via
    upsert_umr_task(), passing this same id as umr_id so the two stay
    correlated by construction.

    Row opens as decision_type='owner_proposal', status='open' -- the same
    real "open means awaiting a decision" convention insert_pm_decision_pending()
    already established, so decide_owner_proposal() below can reuse
    resolve_pm_decision_pending()'s existing `WHERE status='open'` guard
    verbatim rather than inventing a second one. Caller owns
    conn/transaction/commit, same convention as insert_pm_decision_pending()
    above -- this function itself never commits. Returns (decision_id,
    child_umr)."""
    child_umr = child_umr or _new_id("UMR")
    cur = conn.execute(
        "INSERT INTO pm_decisions_pending "
        "(opened_ts, title, detail, related_umr, status, decision_type) "
        "VALUES (?, ?, ?, ?, 'open', 'owner_proposal')",
        (_now_iso(), issue, proposal, child_umr),
    )
    return cur.lastrowid, child_umr


def decide_owner_proposal(conn, decision_id, *, decision, closed_by, closed_note=None):
    """Records the PM's real decision on one real, currently-open
    owner_proposal row -- decision half of the Owner's standing mandate
    (task-20260806-034817): approve, redirect, or hold, citing the
    proposal's own child UMR (already on the row via related_umr from
    insert_owner_proposal() above, so callers don't have to pass it again
    here -- `decision_id` alone identifies the one real row).

    Thin, validating wrapper over resolve_pm_decision_pending() above
    (zero duplication of the actual UPDATE) -- `decision` becomes that
    row's terminal `status`, restricted to exactly the 3 real real-world PM
    actions the Owner's directive names (approve/redirect/hold); anything
    else raises ValueError before touching the database, same
    fail-fast-on-bad-input convention run_ocid_compliance_audit() uses
    for its own rule inputs. `require_decision_type='owner_proposal'`
    ensures this can only ever close a real proposal row, never a plain
    pm_decision row that happens to share a numeric id.

    'held' is a real terminal status here (removes the row from
    get_owner_proposals_pending()'s "awaiting PM decision" list), not a
    real pause-and-return-to-open -- a second decide_owner_proposal() call
    on an already-closed row is not possible by design (same idempotency
    guarantee resolve_pm_decision_pending() gives every other real
    decision); re-filing via a new insert_owner_proposal() is the real,
    explicit path if the Owner/PM wants to revive a held proposal later.
    Returns True if a real open proposal was
    found and closed, False otherwise (already decided, unknown id, or not
    actually an owner_proposal row)."""
    if decision not in _OWNER_PROPOSAL_DECISIONS:
        raise ValueError(
            f"decision must be one of {_OWNER_PROPOSAL_DECISIONS!r}, got {decision!r}"
        )
    return resolve_pm_decision_pending(
        conn, decision_id, closed_by=closed_by, closed_note=closed_note,
        status=decision, require_decision_type="owner_proposal",
    )


def record_owner_proposal_completion(conn, decision_id, *, artifact_path, commit_sha, evidence):
    """Records real completion evidence back onto the same real child-UMR
    proposal row -- completion half of the Owner's standing mandate
    (task-20260806-034817): "a real function for AI to record real
    completion once implemented, the real artifact, real file path, real
    commit, real evidence, back onto the same real child UMR row."

    Only ever fires on a real owner_proposal row the PM has already
    real-approved (`WHERE decision_type='owner_proposal' AND
    status='approved'`) -- a redirected or held proposal, or one still
    open awaiting a PM decision, cannot be marked complete, since nothing
    was ever authorized to implement in that state. Idempotent by the same
    explicit pre-update status guard resolve_pm_decision_pending() uses:
    calling this a second time on an already-'completed' row is a real
    no-op, never a silent overwrite of the first real
    artifact_path/commit_sha/evidence -- callers learn that via this
    function's own return value (False). Caller owns
    conn/transaction/commit, same convention as every other write function
    in this file -- this function itself never commits. Returns True if a
    real approved row was found and marked completed, False otherwise."""
    cur = conn.execute(
        "UPDATE pm_decisions_pending SET status='completed', completed_ts=?, "
        "artifact_path=?, commit_sha=?, evidence=? "
        "WHERE id=? AND decision_type='owner_proposal' AND status='approved'",
        (_now_iso(), artifact_path, commit_sha, evidence, decision_id),
    )
    return cur.rowcount > 0


def cmd_insert_pm_decision_pending(args):
    """CLI entry point over insert_pm_decision_pending() -- see that
    function's own docstring. --options-json is a path to a real JSON file
    holding the real options list (same "path to a JSON file" convention as
    cmd_certify_pr_merge's --pr-record-json above), not an inline string,
    since a real options list is typically multi-option prose too long for
    a single shell argument."""
    options = None
    if args.options_json:
        with open(args.options_json) as f:
            options = json.load(f)
    init_db_silent()
    conn = _connect()
    _ensure_pm_decisions_pending_table(conn)
    with _write_lock():
        decision_id = insert_pm_decision_pending(
            conn, args.title, args.detail, options=options,
            recommended_option=args.recommended_option, related_umr=args.related_umr,
        )
        conn.commit()
    conn.close()
    print(json.dumps({"id": decision_id}, indent=2, default=str))


def cmd_resolve_pm_decision_pending(args):
    """CLI entry point over resolve_pm_decision_pending() -- see that
    function's own docstring. Exits non-zero (after still printing the real
    JSON result) when --id did not name a real, currently-open row, same
    "print then sys.exit(1) on a real refusal/no-op" convention as
    cmd_certify_pr_merge above."""
    init_db_silent()
    conn = _connect()
    _ensure_pm_decisions_pending_table(conn)
    with _write_lock():
        resolved = resolve_pm_decision_pending(
            conn, args.decision_id, closed_by=args.closed_by,
            closed_note=args.closed_note, status=args.status,
        )
        conn.commit()
    conn.close()
    print(json.dumps({"id": args.decision_id, "resolved": resolved}, indent=2, default=str))
    if not resolved:
        sys.exit(1)


def cmd_update_pm_decision_pending(args):
    """CLI entry point over update_pm_decision_pending() -- see that
    function's own docstring. Exits non-zero (after still printing the real
    JSON result) when --id did not name a real, currently-open row, same
    "print then sys.exit(1) on a real refusal/no-op" convention as
    cmd_resolve_pm_decision_pending above."""
    init_db_silent()
    conn = _connect()
    _ensure_pm_decisions_pending_table(conn)
    with _write_lock():
        updated = update_pm_decision_pending(
            conn, args.decision_id, title=args.title, detail=args.detail,
            related_umr=args.related_umr, recommended_option=args.recommended_option,
        )
        conn.commit()
    conn.close()
    print(json.dumps({"id": args.decision_id, "updated": updated}, indent=2, default=str))
    if not updated:
        sys.exit(1)


def cmd_insert_owner_proposal(args):
    """CLI entry point over insert_owner_proposal() -- see that function's
    own docstring. --child-umr is optional (a real one is minted here via
    _new_id("UMR") when omitted, same as the function default)."""
    init_db_silent()
    conn = _connect()
    _ensure_pm_decisions_pending_table(conn)
    with _write_lock():
        decision_id, child_umr = insert_owner_proposal(
            conn, args.issue, args.proposal, child_umr=args.child_umr,
        )
        conn.commit()
    conn.close()
    print(json.dumps({"id": decision_id, "child_umr": child_umr}, indent=2, default=str))


def cmd_decide_owner_proposal(args):
    """CLI entry point over decide_owner_proposal() -- see that function's
    own docstring. Exits non-zero (after still printing the real JSON
    result) when --id did not name a real, currently-open owner_proposal
    row, same "print then sys.exit(1) on a real refusal/no-op" convention
    as cmd_resolve_pm_decision_pending above."""
    init_db_silent()
    conn = _connect()
    _ensure_pm_decisions_pending_table(conn)
    with _write_lock():
        decided = decide_owner_proposal(
            conn, args.decision_id, decision=args.decision,
            closed_by=args.closed_by, closed_note=args.closed_note,
        )
        conn.commit()
    conn.close()
    print(json.dumps({"id": args.decision_id, "decision": args.decision, "decided": decided},
                      indent=2, default=str))
    if not decided:
        sys.exit(1)


def cmd_record_owner_proposal_completion(args):
    """CLI entry point over record_owner_proposal_completion() -- see that
    function's own docstring. Exits non-zero (after still printing the real
    JSON result) when --id did not name a real, currently-approved
    owner_proposal row."""
    init_db_silent()
    conn = _connect()
    _ensure_pm_decisions_pending_table(conn)
    with _write_lock():
        recorded = record_owner_proposal_completion(
            conn, args.decision_id, artifact_path=args.artifact_path,
            commit_sha=args.commit_sha, evidence=args.evidence,
        )
        conn.commit()
    conn.close()
    print(json.dumps({"id": args.decision_id, "recorded": recorded}, indent=2, default=str))
    if not recorded:
        sys.exit(1)


def cmd_mark_umr_dispatched(args):
    """UMR-20260806-085144-9c63 (prevention side of the owner_dispatch_gateway
    stuck-at-'queued' finding; reconciliation side is PR #147 /
    UMR-20260806-082646-3aba, out of scope here). CLI entry point that writes
    a real ts_dispatched + status='dispatched' onto an existing, just-minted
    umr_tasks row via the existing real update_umr_task(), under
    _write_lock() -- same convention as cmd_reconcile_umr_status above.

    UMR-20260806-115423-500d (real narrowing, do not re-widen without
    re-reading this): dispatch-owner-task.sh no longer calls this command
    after a "successful" tmux relay -- a successful `tmux send-keys` proves
    only that keystrokes were written into a pane, never that a live process
    read and acted on them, so it is no longer treated as authoritative
    delivery. The relay's own courtesy signal now goes through
    mark-umr-relay-attempted (above), which never touches `status`. This
    command remains available, unchanged, for a genuinely real future
    mechanical-dispatch caller that actually confirms delivery (e.g. a
    non-interactive worker channel that can positively ack receipt) --
    it is not deleted, only no longer wired to the tmux relay's own
    self-reported success.

    Never touches rows this script didn't just mint -- retroactive
    correction of pre-existing rows is PR #147's job, not this one's.

    Usage:
      python3 superboss-register.py mark-umr-dispatched --umr-id UMR-... [--unit-name NAME]
    """
    init_db_silent()
    conn = _connect()
    _ensure_umr_table(conn)
    ts_dispatched = _now_iso()
    fields = {"status": "dispatched", "ts_dispatched": ts_dispatched}
    if args.unit_name:
        fields["unit_name"] = args.unit_name
    with _write_lock():
        update_umr_task(conn, args.umr_id, **fields)
        conn.commit()
    conn.close()
    print(json.dumps({"umr_id": args.umr_id, "status": "dispatched",
                       "ts_dispatched": ts_dispatched}, indent=2, default=str))


def cmd_mark_umr_relay_attempted(args):
    """UMR-20260806-115423-500d. CLI entry point that records a real,
    honest "a tmux relay was attempted" courtesy signal onto an existing
    umr_tasks row -- via the existing real update_umr_task(), under
    _write_lock(), same convention as cmd_mark_umr_dispatched/
    cmd_mark_umr_terminal above.

    Deliberately writes ONLY ts_relay_attempted/relay_outcome/relay_detail
    (see _migrate_umr_relay_courtesy()'s own docstring for why those three
    columns exist and not status/ts_dispatched/ts_completed). This is the
    real, structural fix for the finding that a successful `tmux send-keys`
    is proof only that keystrokes were written into a pane, never that a
    live process read and acted on them: the old mark-umr-dispatched call
    (still available as its own CLI command, for real future mechanical-
    dispatch use -- see its own docstring) used to be called from exactly
    this spot in dispatch-owner-task.sh immediately after every "successful"
    relay, writing status='dispatched' and thereby permanently removing the
    row from next_queued_task()'s `WHERE status='queued'` query -- the ONLY
    real mechanical pickup path independent of the interactive tmux session
    (resource_governor.py's dispatch_one()/_perform_spawn(), confirmed live:
    veridian_task_create rows DO get spawned to a real `veridian-worker@*`
    systemd unit by that path, with zero tmux involvement). A row that
    called this command instead stays exactly status='queued' -- fully
    eligible for that real mechanical pickup on the very next dispatch-tick.py
    tick -- no matter what this command records.

    Called by dispatch-owner-task.sh from BOTH its relay-succeeds branch
    (--outcome sent) and its relay-session-absent branch (--outcome
    session_not_found) -- neither branch is authoritative for status
    anymore; both are equally "we tried the courtesy channel, here is what
    we honestly observed," never "this task's real destiny is decided."

    Usage:
      python3 superboss-register.py mark-umr-relay-attempted --umr-id UMR-... \\
          --outcome {sent,session_not_found} [--detail "..."]
    """
    init_db_silent()
    conn = _connect()
    _ensure_umr_table(conn)
    ts_relay_attempted = _now_iso()
    fields = {"ts_relay_attempted": ts_relay_attempted, "relay_outcome": args.outcome}
    if args.detail:
        fields["relay_detail"] = args.detail
    with _write_lock():
        update_umr_task(conn, args.umr_id, **fields)
        conn.commit()
    conn.close()
    print(json.dumps({"umr_id": args.umr_id, "relay_outcome": args.outcome,
                       "ts_relay_attempted": ts_relay_attempted,
                       "note": "courtesy signal only -- status/queued-pool membership untouched"},
                      indent=2, default=str))


def cmd_requeue_build_lock_contended(args):
    """UMR-20260806-123316-cf9f (proposal 62, child UMR-20260806-121247-a93a).
    quality-gate.sh's own build step calls this -- and ONLY this -- CLI
    command when it fails to acquire the host-wide build lock
    (/tmp/veridian-quality-gate-build.lock) within its short, fixed 20s wait.
    Root incident: all 5 systemd worker slots were serializing on that one
    global lock (live wchan evidence: 4 of 5 genuinely blocked in
    locks_lock_inode_wai for 582-1376s in one sample), so effective
    concurrency was 1, not the configured ceiling of 5 -- this command is
    the "give up the slot and let someone else in" half of the fix (the
    other half is quality-gate.sh itself never internally retry-looping).

    Resets the task's OWN existing umr_tasks row (found via
    find_active_umr_by_identity() -- never a fresh INSERT, this must never
    mint a new row, that is exactly the duplicate-row-explosion failure mode
    proposals 50-53 already found) back to status='queued' so the real
    dispatcher (resource_governor.next_queued_task() / dispatch_one(), the
    same priority-ordered queue every other task goes through) picks it
    back up on its own schedule -- never a direct systemctl call from here.

    task_kind/inputs_json are always forced to 'systemctl_action' /
    {"action": "start"} (mirroring dispatch-tick.py's
    resume_interrupted_workers_tick() convention for exactly this "restart
    the existing worker unit" shape) regardless of this row's ORIGINAL
    task_kind -- leaving a 'veridian_task_create' task_kind in place would
    wrongly mint a brand-new task_id/branch/worker on the next dispatch,
    instead of resuming the real, already-in-progress workspace this row
    already tracks.

    reason='build_lock_contended' is a fixed, hardcoded literal -- never a
    caller-supplied free-text field like mark-umr-terminal's --reason --
    specifically so it stays a stable, greppable, structurally distinct
    marker from every other status='queued' writer on this table. In
    particular this can never collide with dispatch-tick:
    resume_interrupted_workers_tick()'s own crash-recovery path: that
    function's own find_active_umr_by_identity() pre-check already treats
    the status='queued' row this command leaves behind as "already active"
    and skips resubmitting it (see that function's own docstring) -- the two
    mechanisms cannot both act on the same row.

    Refuses (raises SystemExit) if no active (queued/dispatched/running) row
    exists for --task-identity -- there is nothing real to requeue.

    Usage:
      python3 superboss-register.py requeue-build-lock-contended \\
          --task-identity TASK_ID --unit-name veridian-worker@TASK_ID.service
    """
    init_db_silent()
    conn = _connect()
    _ensure_umr_table(conn)
    with _write_lock():
        row = find_active_umr_by_identity(conn, args.task_identity)
        if not row:
            conn.close()
            raise SystemExit(
                f"requeue-build-lock-contended: no active (queued/dispatched/running) "
                f"umr_tasks row found for task_identity={args.task_identity!r} -- refusing "
                f"to requeue a row that does not really exist")
        fields = {
            "status": "queued",
            "ts_dispatched": None,
            "reason": "build_lock_contended",
            "task_kind": "systemctl_action",
            "unit_name": args.unit_name,
            "inputs_json": json.dumps({"action": "start", "requeued_after": "build_lock_contended"}),
        }
        update_umr_task(conn, row["umr_id"], **fields)
        conn.commit()
    conn.close()
    print(json.dumps({"umr_id": row["umr_id"], "task_identity": args.task_identity,
                       "status": "queued", "reason": "build_lock_contended"}, indent=2, default=str))


def validate_umr_terminal_completion_evidence(*, status, file_path, commit_sha, repo_root,
                                               commit_exists_fn=None, is_ancestor_fn=None):
    """UMR-20260806-130914-e7f1 real dispatch (governed by
    UMR-20260806-071025-1d28), correcting the real 'false completion' finding
    against UMR-20260806-122546-78d6: root cause confirmed directly against
    this file's own real pre-fix code (cmd_mark_umr_terminal /
    p_markterm argparse block) was that mark-umr-terminal had NO parameter
    for structured outputs_json/logs_ref evidence at all -- not a
    caller-discipline gap, a real mechanism gap. This function is the real,
    pure(-ish; I/O only via the injected commit_exists_fn/is_ancestor_fn,
    same testability convention as this module's other _runner-injectable
    helpers) decision logic cmd_mark_umr_terminal calls to close it.

    status=completed requires ONE real, independently-verifiable artifact:
      - a real --file-path that genuinely exists on disk (checked here via
        os.path.isfile, resolved against repo_root when not absolute), OR
      - a real --commit-sha that IS a real ancestor of origin/main (checked
        live via _is_umr_terminal_commit_ancestor_of_main -- mirrors
        triage_owner_umr_24h.py's own is_commit_on_main(), never trusting a
        PR's mergedAt field alone).
    A real, existing commit that is NOT yet an ancestor of origin/main (the
    real 'PR open, unmerged' case -- confirmed live for PR #171 / commit
    2290b1b... at the time this was written) is explicitly refused for
    status=completed, with a message pointing the caller at
    status=completed_unmerged instead -- this is the real, honest
    distinction UMR_STATUSES' own comment explains, not a weakened gate.

    status=completed_unmerged requires a real --commit-sha that exists but is
    NOT (yet) an ancestor of origin/main -- if it already is, this refuses
    too (in the other direction: a caller must not under-claim a real merged
    commit as unmerged either).

    Any other status (failed/killed) is always allowed through unchanged --
    this gate only ever concerns the two 'real completed work' statuses.

    Returns (allowed: bool, refusal_reason: str or None)."""
    if status not in ("completed", "completed_unmerged"):
        return True, None

    commit_exists_fn = commit_exists_fn or _umr_terminal_commit_exists
    is_ancestor_fn = is_ancestor_fn or _is_umr_terminal_commit_ancestor_of_main

    file_ok = False
    if file_path:
        candidate = file_path if os.path.isabs(file_path) else os.path.join(repo_root or "", file_path)
        file_ok = os.path.isfile(candidate)

    # Real review finding (PR #256 review.json): this function used to always
    # evaluate the commit_sha branch (2 real 'git fetch' calls plus
    # cat-file/merge-base, 60s timeout each) even when file_ok was already
    # True and status=="completed" -- i.e. even on the cheap, already-decided
    # path, meaning the expensive real subprocess calls below had no
    # short-circuit at all. status=="completed" with file_ok True is
    # returned as allowed unconditionally two lines below regardless of
    # commit_real/is_ancestor, so skip the real git subprocess work entirely
    # in that case. completed_unmerged always needs a real commit_sha
    # evaluation (it has no file-evidence path), so this only ever skips
    # work that could not have changed the outcome.
    commit_real = False
    is_ancestor = False
    if not (status == "completed" and file_ok):
        commit_real = bool(commit_sha) and commit_exists_fn(repo_root, commit_sha)
        is_ancestor = commit_real and is_ancestor_fn(repo_root, commit_sha)

    if status == "completed":
        if file_ok:
            return True, None
        if commit_real and is_ancestor:
            return True, None
        if commit_sha and not commit_real:
            return False, (
                f"--commit-sha {commit_sha!r} is not a real commit object this repo checkout "
                f"({repo_root!r}) could verify (git cat-file -e failed even after a real fetch) -- "
                "refusing to record status=completed"
            )
        if commit_real and not is_ancestor:
            return False, (
                f"--commit-sha {commit_sha!r} is a real commit but is NOT (yet) a real ancestor of "
                "origin/main (real open/unmerged PR) -- refusing to record status=completed; "
                "re-run with --status completed_unmerged instead once you have confirmed the real PR "
                "is genuinely still open, or wait for it to merge and re-verify"
            )
        return False, (
            "status=completed requires a real --file-path that genuinely exists on disk OR a real "
            "--commit-sha that is a real ancestor of origin/main -- neither was supplied or verified"
        )

    # status == "completed_unmerged"
    if not commit_sha:
        return False, (
            "status=completed_unmerged requires a real --commit-sha (the real commit this status "
            "exists to honestly record as done-but-not-yet-merged) -- none was supplied"
        )
    if not commit_real:
        return False, (
            f"--commit-sha {commit_sha!r} is not a real commit object this repo checkout "
            f"({repo_root!r}) could verify (git cat-file -e failed even after a real fetch) -- "
            "refusing to record status=completed_unmerged"
        )
    if is_ancestor:
        return False, (
            f"--commit-sha {commit_sha!r} is ALREADY a real ancestor of origin/main -- use "
            "--status completed instead of completed_unmerged (this status must never be used to "
            "under-claim a real merged commit as still unmerged)"
        )
    return True, None


def derive_umr_output_contract(umr_id, status, reason, outputs):
    """UMR-20260806-171945-5767 ("single deterministic orchestrator: one
    entrance, one exit, boolean output contract for VERIDIAN"), real work
    finally landed by its second amendment
    (task-20260807-053232-second-amendment-to-umr-20260806-171945) after the
    original spec's own dedicated task
    (task-20260806-201941-single-deterministic-orchestrator--one-e, PR #219)
    stayed genuinely blocked on its precondition gate and never wrote any
    application code (confirmed directly: `gh pr diff 219` touches only
    PROGRESS.md), and the SPEC's own presumed "first amendment"
    (UMR-20260807-035145-aa45, a vector-search reuse gate) was independently
    verified to not exist either -- status='running' in umr_tasks but
    last_heartbeat NULL, its systemd unit inactive, no task.yaml under
    TASKS_DIR, and zero wiring_registry/capability_registry rows citing it.
    Building vector-search code on top of that phantom prerequisite would
    itself have been an assumption this SPEC's own "no assumption anywhere"
    bar forbids -- so this function implements only the one piece of the
    governing chain that IS real and well-specified: the original spec's
    standard boolean output-contract shape, adapted from the owner-supplied
    DeepSeek reference JSON (data: string; meta: deterministic/close_ended/
    boolean/work_id).

    Wired into the ONE real chokepoint every terminal umr_tasks write already
    shares -- cmd_mark_umr_terminal() below, called by: (1) this CLI directly,
    (2) agent_work_briefing.py's record_completion() (in-process
    `sbr.cmd_mark_umr_terminal` call), (3) dispatch-owner-task.sh's
    tmux-relay-failure branch (subprocess CLI call) -- three real, already-
    existing scripts, satisfying "produced by at least 3 real scripts" by
    extending the one shared exit point rather than touching each of the 3
    separately (which would itself be the exact duplication this SPEC
    forbids). This is genuinely the platform's one real "single exit point"
    for task-completion output; no second one exists (confirmed: `update_umr_task()`
    is the only writer of the outputs_json column, and this is its only
    terminal-status caller with a real evidence-shaped `outputs` dict; see
    upsert_umr_task() above, which owns *input*-time outputs, not this
    completion-time contract).

    Additive only -- returns a dict meant to be merged in under a new
    'output_contract' key ALONGSIDE the real evidence fields already written
    here (pr_number/commit_sha/file_path/repo), never replacing or renaming
    them: those flat keys are already read directly by
    test_mark_umr_terminal_structured_evidence.py's own
    outputs["file_path"]/outputs["commit_sha"] assertions and by
    umr_completion_percentage.py's rule 1 ("outputs_json parses to a real
    non-empty dict with at least one real value") -- restructuring that shape
    would be exactly the wide-blast-radius regression this file's own past
    UMRs (e.g. the tenant_id/UTM-field ON CONFLICT exclusions above) already
    established the convention of avoiding.

    Every flag is a real boolean computed from this call's own known facts --
    never hardcoded true:
      - deterministic: True iff this write carries at least one real,
        independently-checkable EVIDENCE field -- commit_sha/file_path/
        pr_number (each already gated by
        validate_umr_terminal_completion_evidence() for completed/
        completed_unmerged) -- OR a real non-empty `reason` string.
        Deliberately excludes `repo`: p_markterm's own --repo argparse arg
        carries `default="veridian-scripts"` (confirmed directly against
        this file's own argparse block), so it is ALWAYS present in
        `outputs` regardless of what the caller actually supplied -- treating
        it as evidence would make this flag trivially always-True (the exact
        hardcode-in-disguise this contract exists to avoid), not an honest
        per-run computation. False only for a genuinely bare status flip with
        no real evidence field and no reason -- a real, pre-existing gap this
        honestly surfaces rather than hides: failed/killed are never
        evidence-gated (see validate_umr_terminal_completion_evidence()'s own
        docstring), so a caller CAN mark one with zero evidence and zero
        reason today, and this flag now says so truthfully instead of
        claiming determinism it cannot back up.
      - close_ended: True for every real status this function ever receives
        (completed/failed/killed) EXCEPT completed_unmerged, which is -- by
        its own docstring's real definition -- genuinely NOT yet closed (real
        work done, real commit exists, but still open/unmerged pending real
        merge) -- False there, honestly, not defaulted to True.
      - boolean: True whenever a real write reaches this function at all:
        args.status is drawn from a real, fixed, argparse-enforced 4-value
        enum (completed/completed_unmerged/failed/killed,
        p_markterm.add_argument("--status", choices=[...])), and
        validate_umr_terminal_completion_evidence()'s own gate is itself a
        genuine binary allowed/refused decision that already ran before this
        function is ever called (a refusal exits before reaching here --
        see cmd_mark_umr_terminal). This is an honestly-derived structural
        fact about this one real chokepoint, not a blind hardcode.
      - work_id: the real umr_id already assigned by resource_governor.py's
        real dispatch path (submit()/upsert_umr_task() above) -- never a
        freshly minted uuid, per the owner's explicit instruction.

    `data` is a plain, real, field-interpolated summary string built only
    from this call's own real status/reason/evidence-key values -- never free
    AI narration."""
    real_evidence_keys = {"commit_sha", "file_path", "pr_number"}
    has_evidence = any(k in outputs for k in real_evidence_keys) if outputs else False
    has_reason = bool(reason and reason.strip())
    evidence_keys = sorted(outputs) if outputs else []
    data = f"umr_tasks row {umr_id} marked status={status}"
    if reason:
        data += f" reason={reason!r}"
    data += f" evidence_keys={evidence_keys!r}"
    return {
        "data": data,
        "meta": {
            "deterministic": has_evidence or has_reason,
            "close_ended": status != "completed_unmerged",
            "boolean": True,
            "work_id": umr_id,
        },
    }


def cmd_mark_umr_terminal(args):
    """UMR-20260806-085144-9c63, structurally extended by
    UMR-20260806-130914-e7f1, and by UMR-20260806-171945-5767's second
    amendment (derive_umr_output_contract() above -- see its own docstring
    for the real boolean output-contract shape now attached to every write
    here). CLI entry point that writes a real ts_completed + a real terminal
    status onto an existing umr_tasks row via the existing real
    update_umr_task(), under _write_lock() -- same convention as
    cmd_reconcile_umr_status above.

    Two real callers:
      1. dispatch-owner-task.sh's tmux-relay-failure branch (the real
         'WARNING: tmux session claude not found' case) -- records
         --status failed with a real --reason instead of silently leaving
         the row at status='queued' forever.
      2. Any worker or interactive session recording genuine completion of
         real work done against a UMR, once it actually finishes -- so
         PERCENT_COMPLETE_24H_OWNER_UMR_SET reflects real terminal status
         instead of requiring a later reconciliation-sweep guess.

    --status is restricted to real terminal states this command is meant to
    assert directly (completed/completed_unmerged/failed/killed);
    'rejected_duplicate' and 'sigterm_sent' are written by their own existing
    real code paths (resource_governor.py's duplicate check, SIGTERM
    handling), not this generic CLI.

    UMR171945-0002 (single output gate audit, 2026-08-08): this is the ONE
    real, generic, CLI-facing entry point for an AI/PM caller to ASSERT a
    completion claim, and the ONLY writer that enforces
    validate_umr_terminal_completion_evidence() -- but it is deliberately
    not the only place resource_governor.py itself writes a real terminal
    status. reconcile_stale_heartbeats()/backfill_null_heartbeats() (see
    their own comments at each real write site) write status='completed'
    directly via update_umr_task(), bypassing this function and its
    evidence gate on purpose: their real evidence basis is live,
    directly-observed systemd/session state, not a PR/commit claim the gate
    was built to catch fabrication of. Every real terminal write in this
    codebase still goes through the SAME single underlying update_umr_task()
    function under the same real _write_lock() -- "single output gate"
    holds at the write-function level; the PR/commit evidence gate itself
    is correctly scoped to this CLI's own AI/PM-claimed-completion use
    case, not universal to every real terminal write.

    UMR-20260806-130914-e7f1 (real dispatch UMR-20260806-130914-e7f1,
    governed by UMR-20260806-071025-1d28): status=completed and
    status=completed_unmerged now structurally REQUIRE real, structured,
    independently-verifiable evidence -- see
    validate_umr_terminal_completion_evidence()'s own docstring for the
    exact real rule. A caller that fails this gate gets a real refusal
    (printed JSON with refused=true and a real reason, exit code 1) and the
    row is NOT written at all -- never a silent partial write. Evidence that
    does pass is recorded onto the row's own real outputs_json (never only
    inside the free-text --reason) via update_umr_task()'s existing 'outputs'
    kwarg, so it becomes real, machine-checkable data for the next caller
    (e.g. reconcile_umr_status_against_pr, an audit sweep) instead of prose
    that only a human or an LLM re-reading --reason could parse.

    Usage:
      python3 superboss-register.py mark-umr-terminal --umr-id UMR-... \\
          --status {completed,completed_unmerged,failed,killed} [--reason "why"] \\
          [--commit-sha SHA] [--file-path PATH] [--pr-number N] \\
          [--repo veridian-scripts|compliance-tracker|projexa] [--repo-root PATH]

      --commit-sha / --file-path are REQUIRED (at least one, per the real
      rule above) when --status is completed or completed_unmerged; ignored
      (but still recorded onto outputs_json if supplied) for failed/killed,
      since only a real completed claim needs a real artifact gate.
    """
    init_db_silent()
    conn = _connect()
    _ensure_umr_table(conn)

    repo_root = args.repo_root or DEFAULT_OCID_RESOLVER_REPO_LOCAL_PATHS.get(
        args.repo, DEFAULT_OCID_RESOLVER_REPO_LOCAL_PATHS["veridian-scripts"]
    )
    allowed, refusal_reason = validate_umr_terminal_completion_evidence(
        status=args.status, file_path=args.file_path, commit_sha=args.commit_sha,
        repo_root=repo_root,
    )
    if not allowed:
        conn.close()
        print(json.dumps({
            "umr_id": args.umr_id, "status": args.status, "refused": True,
            "reason": refusal_reason,
        }, indent=2, default=str))
        sys.exit(1)

    ts_completed = _now_iso()
    fields = {"status": args.status, "ts_completed": ts_completed}
    if args.reason:
        fields["reason"] = args.reason
    outputs = {}
    if args.pr_number is not None:
        outputs["pr_number"] = args.pr_number
    if args.commit_sha:
        outputs["commit_sha"] = args.commit_sha
    if args.file_path:
        outputs["file_path"] = args.file_path
    if args.repo:
        outputs["repo"] = args.repo
    # UMR-20260806-171945-5767 second amendment: the real boolean
    # output-contract, additive under its own key -- see
    # derive_umr_output_contract()'s own docstring for why this never
    # replaces/renames pr_number/commit_sha/file_path/repo above. Attached
    # even when `outputs` is otherwise empty (a bare failed/killed flip),
    # since that is exactly the honestly-computed deterministic=False case
    # this contract exists to surface, not one to leave unrecorded.
    outputs_with_contract = dict(outputs)
    outputs_with_contract["output_contract"] = derive_umr_output_contract(
        args.umr_id, args.status, args.reason, outputs)
    fields["outputs"] = outputs_with_contract

    with _write_lock():
        update_umr_task(conn, args.umr_id, **fields)
        conn.commit()
    conn.close()
    print(json.dumps({"umr_id": args.umr_id, "status": args.status,
                       "ts_completed": ts_completed, "outputs": outputs_with_contract},
                      indent=2, default=str))


def reset_umr_task_to_queued(conn, umr_id, *, reason):
    """UMR-20260806-115605-854d (dead-zone auto-remediation, real correction
    to UMR-20260806-115538-1e55's original "just report" framing -- the
    real ask is that mechanical, safe, reversible fixes happen
    automatically, never sit waiting for an AI to read a report). The one
    real, canonical write path that resets a real umr_tasks row from
    status='dispatched' back to 'queued' -- through update_umr_task() only,
    same convention as every other real status-transition wrapper in this
    file (cmd_mark_umr_dispatched/cmd_mark_umr_terminal above,
    reconcile_stale_heartbeats() in resource_governor.py). Never a raw SQL
    UPDATE.

    ts_dispatched is explicitly cleared back to NULL (not left stale) so a
    genuinely fresh dispatch attempt, whenever it next happens, is recorded
    honestly rather than inheriting a timestamp from the abandoned attempt
    this call is correcting -- the same "a queued row's own ts_dispatched is
    NULL until a real dispatch happens" invariant every other real writer of
    this column already relies on (next_queued_task()/dispatch_one() in
    resource_governor.py, cmd_mark_umr_dispatched above).

    Deliberately does not touch unit_name or metadata_json: the one real
    caller (reconcile_dispatched_dead_zone.py) only ever calls this for a
    row it has already confirmed carries unit_name IS NULL (no real systemd
    unit was ever spawned for it -- see that script's own dead-zone
    condition), so there is nothing there to clear, and this is a pure
    status/timestamp transition with no other real field to merge (unlike
    reconcile_owner_dispatch_status.py's apply_correction(), which DOES
    read-merge-write metadata_json because it also records structured
    per-row evidence there -- this function has no such payload).

    Caller owns conn/transaction/commit, same convention as every other
    write function in this file -- this function itself never commits."""
    update_umr_task(conn, umr_id, status="queued", ts_dispatched=None, reason=reason)


def cmd_reset_umr_to_queued(args):
    """CLI entry point over reset_umr_task_to_queued() -- see that
    function's own docstring. The one real, canonical, script-only surface
    for this transition (UMR-20260806-115605-854d): reconcile_dispatched_dead_zone.py
    calls reset_umr_task_to_queued() directly (it already imports this
    module), so this CLI wrapper exists for operator/manual re-runs and
    testability, matching the same "function + thin CLI wrapper" shape as
    mark-umr-dispatched/mark-umr-terminal above.

    Usage:
      python3 superboss-register.py reset-umr-to-queued --umr-id UMR-... --reason "why"
    """
    init_db_silent()
    conn = _connect()
    _ensure_umr_table(conn)
    with _write_lock():
        reset_umr_task_to_queued(conn, args.umr_id, reason=args.reason)
        conn.commit()
    conn.close()
    print(json.dumps({"umr_id": args.umr_id, "status": "queued", "reason": args.reason},
                      indent=2, default=str))


# ---------------------------------------------------------------------------
# master_issue_tracker -- the ONE permanent, callable mechanism for real issue
# tracking (Owner directive, governing chain UMR-20260806-171945-5767 ->
# UMR-20260808-074726-d105). The table itself is real and was already
# populated before this section existed (986 rows: 981 migrated from
# UMR_5767_ISSUE_RESOLUTION_MATRIX.json, plus 5 real OCID-020 GTM-
# certification category failures) -- what was missing was a real,
# permanent, callable WRITE path: the only way to add a row was a one-off
# script run once from /tmp (/tmp/build_master_issue_tracker.py), never
# re-runnable, never callable by a live agent or by this codebase's own
# deterministic gates. This section is that path -- add-issue/close-issue/
# update-issue/list-issues, following the exact same "function does the real
# work and does NOT commit, caller owns the transaction; cmd_* is the thin
# CLI wrapper that opens the connection, calls under _write_lock(), commits"
# convention every other write path in this file already uses (see
# update_gtm_certification_category()/cmd_update_gtm_category() above for
# the closest real precedent). Real schema below is byte-identical to the
# real, live table already on disk -- confirmed via PRAGMA table_info() /
# sqlite_master.sql before writing this, not guessed, and cross-checked
# against /tmp/build_master_issue_tracker.py's own CREATE TABLE statement.
# ---------------------------------------------------------------------------

def _ensure_master_issue_tracker_table(conn):
    """Idempotent CREATE TABLE IF NOT EXISTS -- makes the real, already-live
    master_issue_tracker schema re-creatable on any DB (including a fresh
    test fixture) that doesn't already have it, same convention
    _ensure_umr_table()/_ensure_pm_decisions_pending_table() already
    established. Also registered in _migrate_schema() so `init` and every
    write-path CLI command picks it up on a pre-existing DB, same dual call
    site as _ensure_umr_table()."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS master_issue_tracker (
            tracker_id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id TEXT UNIQUE NOT NULL,
            issue_number INTEGER,
            linked_umr_id TEXT,
            linked_ocid TEXT,
            linked_source TEXT,
            issue_identified TEXT NOT NULL,
            file_name TEXT,
            file_path TEXT,
            existing_solution_in_system TEXT,
            solution_applied TEXT CHECK (solution_applied IN ('YES','NO','PARTIAL','UNKNOWN')),
            issue_resolved_permanently TEXT CHECK (issue_resolved_permanently IN ('YES','NO','PARTIAL','UNKNOWN')),
            new_script_needed TEXT CHECK (new_script_needed IN ('YES','NO','UNKNOWN')),
            new_script_details TEXT,
            apply_fix_notes TEXT,
            audit_notes TEXT,
            check_again_notes TEXT,
            is_closed TEXT NOT NULL DEFAULT 'NO' CHECK (is_closed IN ('YES','NO')),
            is_deterministic TEXT,
            is_ai_free TEXT,
            is_boolean_software TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mit_ocid ON master_issue_tracker(linked_ocid)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mit_closed ON master_issue_tracker(is_closed)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mit_issue_number ON master_issue_tracker(issue_number)")


def _next_master_issue_number(conn):
    row = conn.execute(
        "SELECT COALESCE(MAX(issue_number), 0) + 1 AS n FROM master_issue_tracker"
    ).fetchone()
    return row["n"]


def add_master_issue(conn, issue_id, issue_identified, linked_ocid=None, linked_umr_id=None,
                      linked_source=None, file_name=None, file_path=None, existing_solution=None):
    """Real INSERT -- the one, real, permanent, callable mechanism to add a
    row to master_issue_tracker (Owner directive: any AI agent or
    deterministic script that finds a real issue writes it in here, not into
    a chat message, a one-off file, or nowhere at all -- see this codebase's
    own README-SERVER.md mandatory-recording section). Does NOT commit --
    caller owns the transaction, same convention as every other write
    function in this file.

    Required, real: issue_id (caller-chosen, stable, must not already
    exist -- raises ValueError rather than a silent overwrite; call
    update_master_issue() for an already-existing issue_id),
    issue_identified (a real, non-empty description of the real issue
    found), and at least one of linked_ocid / linked_umr_id -- every real
    row already in this table traces to a real governing OCID or UMR, and an
    issue with neither is not yet traceable enough to record here.
    issue_number is assigned automatically, one past the current real
    MAX(issue_number) -- never caller-supplied, so numbering stays gapless
    and monotonic the same way the 986 pre-existing rows already are."""
    issue_id = (issue_id or "").strip()
    issue_identified = (issue_identified or "").strip()
    if not issue_id:
        raise ValueError("add_master_issue: --issue-id is required and must be non-empty")
    if not issue_identified:
        raise ValueError("add_master_issue: --issue-identified is required and must be non-empty")
    if not linked_ocid and not linked_umr_id:
        raise ValueError("add_master_issue: at least one of --linked-ocid / --linked-umr-id is required")
    existing = conn.execute(
        "SELECT tracker_id FROM master_issue_tracker WHERE issue_id=?", (issue_id,)
    ).fetchone()
    if existing:
        raise ValueError(f"add_master_issue: issue_id {issue_id!r} already exists "
                          f"(tracker_id={existing['tracker_id']}) -- use update_master_issue instead")
    now = _now_iso()
    issue_number = _next_master_issue_number(conn)
    conn.execute(
        "INSERT INTO master_issue_tracker "
        "(issue_id, issue_number, linked_umr_id, linked_ocid, linked_source, issue_identified, "
        "file_name, file_path, existing_solution_in_system, is_closed, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'NO', ?, ?)",
        (issue_id, issue_number, linked_umr_id, linked_ocid, linked_source, issue_identified,
         file_name, file_path, existing_solution, now, now),
    )
    return issue_id, issue_number


def close_master_issue(conn, issue_id, resolution_notes):
    """Real UPDATE -- sets issue_resolved_permanently='YES' and
    is_closed='YES' ONLY if resolution_notes is real and non-empty (Owner/
    task requirement -- an issue is never marked resolved without real
    resolution evidence). Appends resolution_notes onto apply_fix_notes --
    the same column the 673 real already-closed pre-existing rows use for
    exactly this purpose, confirmed by direct SELECT before writing this,
    not guessed. Appends rather than overwrites, so an issue closed a second
    time (re-opened, then re-fixed) never silently loses its own prior real
    resolution history. Does NOT commit -- caller owns the transaction."""
    resolution_notes = (resolution_notes or "").strip()
    if not resolution_notes:
        raise ValueError("close_master_issue: --resolution-notes is required and must be non-empty "
                          "-- an issue is never marked resolved without real resolution evidence")
    row = conn.execute(
        "SELECT apply_fix_notes FROM master_issue_tracker WHERE issue_id=?", (issue_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"close_master_issue: no master_issue_tracker row for issue_id={issue_id!r}")
    prior = (row["apply_fix_notes"] or "").strip()
    combined = f"{prior}\n---\n{resolution_notes}" if prior else resolution_notes
    conn.execute(
        "UPDATE master_issue_tracker SET solution_applied='YES', issue_resolved_permanently='YES', "
        "is_closed='YES', apply_fix_notes=?, updated_at=? WHERE issue_id=?",
        (combined, _now_iso(), issue_id),
    )


_MASTER_ISSUE_MUTABLE_COLUMNS = (
    # Real, confirmed bug fixed 2026-08-08 (independent tier1 review):
    # issue_number was listed here, letting `update-issue --field
    # issue_number=NNN` renumber/collide rows through this path -- directly
    # contradicting add_master_issue()'s own documented invariant that
    # issue_number is "never caller-supplied, so numbering stays gapless
    # and monotonic". Removed; issue_number is immutable through this
    # function, same as tracker_id/issue_id/created_at.
    "linked_umr_id", "linked_ocid", "linked_source", "issue_identified",
    "file_name", "file_path", "existing_solution_in_system", "solution_applied",
    "issue_resolved_permanently", "new_script_needed", "new_script_details", "apply_fix_notes",
    "audit_notes", "check_again_notes", "is_closed", "is_deterministic", "is_ai_free",
    "is_boolean_software",
)


def update_master_issue(conn, issue_id, **fields):
    """Partial UPDATE of any real, mutable master_issue_tracker column
    (everything except tracker_id/issue_id/issue_number/created_at, which
    are immutable by design -- issue_id is this table's own real stable
    key, never reassignable through this path, and issue_number is
    assigned once, automatically, by add_master_issue() and must stay
    gapless/monotonic, never caller-reassignable through this path either).
    Raises on an unknown/protected column,
    same convention as update_gtm_certification_category() above. Enum-
    constrained columns (solution_applied/issue_resolved_permanently/
    new_script_needed/is_closed) are enforced by the table's own real CHECK
    constraints -- an invalid value raises sqlite3.IntegrityError, never
    silently accepted. Does NOT commit -- caller owns the transaction."""
    unknown = set(fields) - set(_MASTER_ISSUE_MUTABLE_COLUMNS)
    if unknown:
        raise ValueError(
            f"update_master_issue: refusing to write protected/unknown column(s) {sorted(unknown)} "
            f"-- only {_MASTER_ISSUE_MUTABLE_COLUMNS} are mutable through this function"
        )
    if not fields:
        return
    row = conn.execute(
        "SELECT tracker_id FROM master_issue_tracker WHERE issue_id=?", (issue_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"update_master_issue: no master_issue_tracker row for issue_id={issue_id!r}")
    set_clauses, values = [], []
    for column, value in fields.items():
        set_clauses.append(f"{column}=?")
        values.append(value)
    set_clauses.append("updated_at=?")
    values.append(_now_iso())
    values.append(issue_id)
    conn.execute(
        f"UPDATE master_issue_tracker SET {', '.join(set_clauses)} WHERE issue_id=?",
        values,
    )


def query_master_issues(conn, linked_ocid=None, linked_umr_id=None, is_closed=None, limit=50):
    """Real SELECT, the same real filter shape this table's own real callers
    need (--linked-ocid / --linked-umr-id / --is-closed), newest-updated-
    first. Any combination of filters may be combined; none is required
    (omit all to list the most recently updated rows overall).

    linked_umr_id added 2026-08-08 (addendum to UMR-20260808-122929-bc77):
    the real, sanctioned way to pull every point of a UMR-scoped point set
    (e.g. the 24 real UMR171945-00NN rows under UMR-20260806-171945-5767) in
    one call, mirroring linked_ocid's existing real filter for OCID-scoped
    rows -- same column, same convention, no new table."""
    clauses, params = [], []
    if linked_ocid:
        clauses.append("linked_ocid=?")
        params.append(linked_ocid)
    if linked_umr_id:
        clauses.append("linked_umr_id=?")
        params.append(linked_umr_id)
    if is_closed:
        clauses.append("is_closed=?")
        params.append(is_closed.strip().upper())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM master_issue_tracker {where} ORDER BY updated_at DESC LIMIT ?", params
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Governance cycle log (task-gateway.py audit-24-points, UMR-20260808-145030-f3d1)
# ---------------------------------------------------------------------------
# Point 2 needs a real query-log table (every real status read through the
# canonical query path -- task-gateway.py status / resource_governor.py
# --query-umr -- gets a logged row); Points 8/9 need a real, timestamped
# memory-check / audit-performed log entry per cycle. Rather than three
# separate one-off tables/files for three closely-related "a real event of
# type X happened at time T" facts, one small, generic, append-only table
# covers all three (event_type discriminates); this is additive, not a
# duplicate of master_issue_tracker (which tracks issue *state*, not *events*)
# or umr_tasks (which tracks task *lifecycle*, not ad-hoc governance events).

def _ensure_governance_cycle_log_table(conn):
    """Idempotent CREATE TABLE IF NOT EXISTS, same convention as
    _ensure_master_issue_tracker_table() above."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS governance_cycle_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            caller TEXT,
            detail TEXT,
            ts TEXT NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_gcl_event_type_ts ON governance_cycle_log(event_type, ts)"
    )


def log_governance_cycle_event(conn, event_type, caller=None, detail=None):
    """Real INSERT -- the one, real, permanent mechanism real callers use to
    record a real governance-cycle event (a canonical-path query, a memory
    check, an audit run). Does NOT commit -- caller owns the transaction,
    same convention as add_master_issue()/update_master_issue() above.
    event_type is caller-chosen but expected to be one of 'query',
    'memory_check', 'audit_performed' by this module's own real callers
    (task-gateway.py's cmd_status/cmd_audit_24_points, resource_governor.py's
    --query-umr branch) -- not enforced by a CHECK constraint since a
    genuinely new event class should never require a schema migration to
    record."""
    event_type = (event_type or "").strip()
    if not event_type:
        raise ValueError("log_governance_cycle_event: event_type is required and must be non-empty")
    conn.execute(
        "INSERT INTO governance_cycle_log (event_type, caller, detail, ts) VALUES (?, ?, ?, ?)",
        (event_type, caller, detail, _now_iso()),
    )


def query_governance_cycle_log(conn, event_type=None, limit=50):
    """Real SELECT, newest-first, optionally filtered by event_type -- same
    shape convention as query_master_issues() above."""
    clauses, params = [], []
    if event_type:
        clauses.append("event_type=?")
        params.append(event_type)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM governance_cycle_log {where} ORDER BY ts DESC LIMIT ?", params
    ).fetchall()
    return [dict(r) for r in rows]


def cmd_log_governance_event(args):
    """CLI entry point for log_governance_cycle_event() above -- the one
    real, non-raw-SQL write path task-gateway.py/resource_governor.py use to
    record a real governance-cycle event."""
    init_db_silent()
    conn = _connect()
    _ensure_governance_cycle_log_table(conn)
    with _write_lock():
        log_governance_cycle_event(conn, args.event_type, caller=args.caller, detail=args.detail)
        conn.commit()
    conn.close()
    print(json.dumps({"ok": True, "event_type": args.event_type}, indent=2, default=str))


def cmd_list_governance_events(args):
    """CLI entry point for query_governance_cycle_log() above. JSON output
    matches this session's own --query-umr/list-issues convention."""
    init_db_silent()
    conn = _connect()
    _ensure_governance_cycle_log_table(conn)
    rows = query_governance_cycle_log(conn, event_type=args.event_type, limit=args.limit)
    conn.close()
    print(json.dumps({"count": len(rows), "matches": rows}, indent=2, default=str))


def cmd_add_issue(args):
    """CLI entry point for add_master_issue() above.

    Usage:
      python3 superboss-register.py add-issue --issue-id ID --issue-identified "..." \\
          (--linked-ocid OCID-NNN | --linked-umr-id UMR-...) \\
          [--file-name NAME] [--file-path PATH] [--existing-solution "..."] \\
          [--linked-source "..."]
    """
    init_db_silent()
    conn = _connect()
    _ensure_master_issue_tracker_table(conn)
    try:
        with _write_lock():
            issue_id, issue_number = add_master_issue(
                conn, args.issue_id, args.issue_identified,
                linked_ocid=args.linked_ocid, linked_umr_id=args.linked_umr_id,
                linked_source=args.linked_source, file_name=args.file_name,
                file_path=args.file_path, existing_solution=args.existing_solution,
            )
            conn.commit()
    except ValueError as e:
        conn.close()
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)
    conn.close()
    print(json.dumps({"ok": True, "issue_id": issue_id, "issue_number": issue_number},
                      indent=2, default=str))


def cmd_close_issue(args):
    """CLI entry point for close_master_issue() above.

    Usage:
      python3 superboss-register.py close-issue --issue-id ID --resolution-notes "..."
    """
    init_db_silent()
    conn = _connect()
    _ensure_master_issue_tracker_table(conn)
    try:
        with _write_lock():
            close_master_issue(conn, args.issue_id, args.resolution_notes)
            conn.commit()
    except ValueError as e:
        conn.close()
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)
    conn.close()
    print(json.dumps({"ok": True, "issue_id": args.issue_id, "is_closed": "YES",
                       "issue_resolved_permanently": "YES"}, indent=2, default=str))


def cmd_update_issue(args):
    """CLI entry point for update_master_issue() above -- repeatable
    --field NAME=VALUE. master_issue_tracker has 19 real mutable columns and
    the task spec explicitly asks for 'any real field to update', so a
    repeatable --field NAME=VALUE is the real minimal surface rather than 19
    new individual flags.

    Usage:
      python3 superboss-register.py update-issue --issue-id ID \\
          --field solution_applied=YES --field audit_notes="..."
    """
    init_db_silent()
    conn = _connect()
    _ensure_master_issue_tracker_table(conn)
    fields = {}
    for item in (args.field or []):
        if "=" not in item:
            conn.close()
            print(json.dumps({"ok": False, "error": f"--field must be NAME=VALUE, got {item!r}"}))
            sys.exit(1)
        name, value = item.split("=", 1)
        fields[name.strip()] = value
    try:
        with _write_lock():
            update_master_issue(conn, args.issue_id, **fields)
            conn.commit()
    except (ValueError, sqlite3.IntegrityError) as e:
        conn.close()
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.exit(1)
    conn.close()
    print(json.dumps({"ok": True, "issue_id": args.issue_id, "updated": fields}, indent=2, default=str))


def cmd_list_issues(args):
    """CLI entry point for query_master_issues() above. JSON output matches
    this session's own --query-umr convention (resource_governor.py
    main()): {"count": N, "matches": [...]}."""
    init_db_silent()
    conn = _connect()
    _ensure_master_issue_tracker_table(conn)
    rows = query_master_issues(conn, linked_ocid=args.linked_ocid, linked_umr_id=args.linked_umr_id,
                                is_closed=args.is_closed, limit=args.limit)
    conn.close()
    print(json.dumps({"count": len(rows), "matches": rows}, indent=2, default=str))


EXTERNAL_AGENT_ALLOWED_TASK_TYPES = (
    "isolated_bugfix", "doc_update", "single_file_refactor", "test_addition_only",
)

# resource_governor.py:66 -- `TIER_MIN, TIER_MAX = 0, 4` -- and
# resource_governor.py:2361 -- `ap.add_argument("--tier", ... help="0
# (highest) .. 4 (lowest)")` -- confirmed directly against that file's own
# real code before hardcoding this (per UMR-20260806-095416-b6f0's own
# explicit instruction not to assume): tier 0 is the highest-severity/most
# critical tier, so the two most critical tiers are 0 and 1. A umr_tasks row
# at either tier can never be external-agent eligible, full stop.
EXTERNAL_AGENT_EXCLUDED_TIERS = (0, 1)

# Real two-strike rule (UMR-20260806-095416-b6f0 §6): at most 2 real
# dispatch attempts total ever happen for one umr_id via this channel --
# the original attempt plus exactly one requeued retry. A 3rd is never
# issued; get_next_external_agent_task()'s own SELECT enforces this via
# `external_agent_reject_count < EXTERNAL_AGENT_MAX_ATTEMPTS`, and
# _external_agent_apply_reject()/expire_external_agent_dispatches() both
# force external_agent_eligible back to 0 the instant reject_count reaches
# this value, permanently falling the row back to the normal internal
# worker pool.
EXTERNAL_AGENT_MAX_ATTEMPTS = 2

EXTERNAL_AGENT_DISPATCH_TTL_HOURS = 24

EXTERNAL_AGENT_PROVIDER = "chat.z.ai"

# Real, deterministic secret/credential/sensitive-path exclusion
# (UMR-20260806-095416-b6f0 §3): zero overlap allowed between
# files_touched and any path matching these patterns, checked against the
# full real repo-relative path, case-insensitive. `migrations?` covers both
# `migration`/`migrations` as either a path segment or filename fragment;
# same for the rest -- deliberately broad substring matches (not anchored
# to exact path segments) since a real secret/credential/auth-adjacent file
# is exactly the class of thing that must never be handed to an external,
# unauthenticated-to-this-repo human-mediated channel, and a false-positive
# exclusion here (an eligible task wrongly rejected) is a real, acceptable,
# far cheaper cost than a false negative (a real secret ever leaving this
# repo through this channel).
_EXTERNAL_AGENT_FORBIDDEN_PATH_RE = re.compile(
    r"(secret|credential|password|token|\.env\b|env\.|migrations?[/\\]|"
    r"(^|[/\\])auth([/\\._-]|$)|(^|[/\\])rbac([/\\._-]|$)|\.github[/\\]workflows)",
    re.IGNORECASE,
)


def _is_unsafe_external_agent_path(path):
    """Real, defense-in-depth guard (found and fixed during real independent
    review of UMR-20260806-095416-b6f0's implementation PR) against a
    files_touched/diff path that could escape the real repo root: an
    absolute path -- `os.path.join(repo_root, rel_path)` in
    get_next_external_agent_task() silently DISCARDS repo_root when the
    second argument is absolute, so an unchecked absolute files_touched
    entry would read+embed the content of ANY file on disk (a secret, an
    SSH key, the live production DB itself) into the real prompt handed to
    chat.z.ai -- a real external, untrusted third party -- or a real `..`
    path-traversal segment (same real risk, reached a different way). Used
    on BOTH real sides of this channel: the outbound files_touched allow-
    list (check_external_agent_eligibility(), gating BEFORE any file is
    ever read) and the inbound diff paths chat.z.ai's reply names
    (submit_external_agent_result()) -- the inbound side already had this
    exact check inline; this function is the single shared real
    implementation both now call, so the two can never drift apart again."""
    if not path:
        return True
    normalized = path.replace("\\", "/")
    if normalized.startswith("/"):
        return True
    if ".." in normalized.split("/"):
        return True
    return False


def _is_external_agent_doc_path(path):
    """Real markdown/docs-path check for the doc_update task type's real
    up-to-3-files allowance (UMR-20260806-095416-b6f0 §3): a `.md`/`.rst`
    file anywhere, OR any file (any extension) that actually lives under a
    real `docs/` path -- matches this repo's own real convention of mixing
    `.md` docs at the repo root (README-dispatch-consolidation.md,
    PROGRESS.md, the OCID_*.md files) with occasional non-.md docs content
    under a docs/ directory elsewhere in the platform."""
    p = path.lower().replace("\\", "/")
    return p.endswith((".md", ".rst")) or p.startswith("docs/") or "/docs/" in p


def check_external_agent_eligibility(*, task_type, files_touched, blast_radius,
                                      requires_multi_file_context, acceptance_criteria,
                                      repro_steps, tier):
    """Real, pure, deterministic eligibility check (UMR-20260806-095416-b6f0
    §3) -- takes plain values, touches no DB, so it is fully unit-testable
    (see tests/test_external_agent_dispatch.py) independent of
    mark_external_agent_eligible()'s DB-row plumbing below. A task is
    eligible only if EVERY one of these real rules holds; returns
    (eligible: bool, reasons: list[str]) -- reasons is always the real,
    complete list of every rule that failed (never short-circuits on the
    first failure), so a caller/human always sees the whole real picture in
    one pass rather than fixing one issue at a time by trial and error.

      1. task_type is one of EXTERNAL_AGENT_ALLOWED_TASK_TYPES.
      2. files_touched has at least 1 real entry, and at most 1 -- EXCEPT
         doc_update, which allows up to 3, and only if every one of those
         (up to 3) is a real markdown/docs-path file (_is_external_agent_doc_path).
      3. Zero overlap between files_touched and
         _EXTERNAL_AGENT_FORBIDDEN_PATH_RE, AND no entry is an absolute path
         or contains a '..' traversal segment (_is_unsafe_external_agent_path)
         -- otherwise get_next_external_agent_task() could read and embed
         real file content from OUTSIDE the repo root into the real prompt
         handed to chat.z.ai, a real external, untrusted third party.
      4. acceptance_criteria is a real, non-empty (post-strip) string.
      5. For isolated_bugfix specifically: repro_steps is also a real,
         non-empty (post-strip) string.
      6. requires_multi_file_context is falsy.
      7. blast_radius is exactly the literal string 'isolated'.
      8. tier is not in EXTERNAL_AGENT_EXCLUDED_TIERS (the two most critical
         tiers, 0 and 1 -- confirmed against resource_governor.py's own real
         tier scale, see that constant's own comment above)."""
    reasons = []
    if task_type not in EXTERNAL_AGENT_ALLOWED_TASK_TYPES:
        reasons.append(
            f"external_agent_task_type {task_type!r} is not one of the 4 real allowed values "
            f"{EXTERNAL_AGENT_ALLOWED_TASK_TYPES}"
        )
    files = list(files_touched or [])
    if len(files) < 1:
        reasons.append("files_touched is empty -- a real eligible task must name at least one real file")
    max_files = 3 if task_type == "doc_update" else 1
    if len(files) > max_files:
        reasons.append(
            f"files_touched has {len(files)} real entries, more than the max of {max_files} "
            f"allowed for task_type {task_type!r}"
        )
    if task_type == "doc_update":
        non_doc = [f for f in files if not _is_external_agent_doc_path(f)]
        if non_doc:
            reasons.append(
                f"doc_update allows only markdown/docs-path files -- found non-doc entries: {non_doc}"
            )
    unsafe_paths = [f for f in files if _is_unsafe_external_agent_path(f)]
    if unsafe_paths:
        reasons.append(
            f"files_touched contains unsafe absolute/path-traversal entries -- would let this "
            f"channel read and leak real file content from outside the real repo root: {unsafe_paths}"
        )
    forbidden_hits = [f for f in files if _EXTERNAL_AGENT_FORBIDDEN_PATH_RE.search(f)]
    if forbidden_hits:
        reasons.append(
            f"files_touched contains path(s) matching the real secret/credential/auth/rbac/"
            f"migrations/.github-workflows exclusion pattern: {forbidden_hits}"
        )
    if not (acceptance_criteria and str(acceptance_criteria).strip()):
        reasons.append("acceptance_criteria is empty -- a real, non-empty acceptance-criteria field is required")
    if task_type == "isolated_bugfix" and not (repro_steps and str(repro_steps).strip()):
        reasons.append("repro_steps is empty -- required specifically for task_type='isolated_bugfix'")
    if requires_multi_file_context:
        reasons.append("requires_multi_file_context is true -- must be 0/false to be eligible")
    if blast_radius != "isolated":
        reasons.append(f"blast_radius {blast_radius!r} != 'isolated'")
    if tier in EXTERNAL_AGENT_EXCLUDED_TIERS:
        reasons.append(
            f"tier {tier} is one of the two most critical tiers {EXTERNAL_AGENT_EXCLUDED_TIERS} "
            f"(resource_governor.py: 0=highest)"
        )
    return (len(reasons) == 0, reasons)


def mark_external_agent_eligible(conn, umr_id, *, task_type, blast_radius, requires_multi_file_context,
                                  files_touched, acceptance_criteria, repro_steps=None):
    """Real write path (and the ONLY real write path) that ever sets
    umr_tasks.external_agent_eligible=1 (UMR-20260806-095416-b6f0 §9) --
    always runs check_external_agent_eligibility() first and raises
    ValueError (never silently marks a non-eligible row) if any real rule
    fails. Caller owns conn/transaction/commit, same convention as
    update_umr_task()/upsert_umr_task() above -- this function itself never
    commits.

    acceptance_criteria/repro_steps are stored inside the row's existing
    metadata_json under a real "external_agent" sub-object (see
    _migrate_umr_tasks_external_agent_columns()'s own docstring for why
    these two are deliberately not separate columns) -- this REPLACES any
    prior "external_agent" sub-object on the row (not a merge), since a
    real re-mark is always a real, complete redeclaration of eligibility,
    never a partial patch."""
    row = conn.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
    if row is None:
        raise ValueError(f"no real umr_tasks row for umr_id={umr_id!r}")
    eligible, reasons = check_external_agent_eligibility(
        task_type=task_type, files_touched=files_touched, blast_radius=blast_radius,
        requires_multi_file_context=requires_multi_file_context, acceptance_criteria=acceptance_criteria,
        repro_steps=repro_steps, tier=row["tier"],
    )
    if not eligible:
        raise ValueError(f"umr_id={umr_id!r} is NOT really eligible for the external-agent channel: {reasons}")
    metadata = json.loads(row["metadata_json"] or "{}")
    metadata["external_agent"] = {"acceptance_criteria": acceptance_criteria, "repro_steps": repro_steps}
    update_umr_task(
        conn, umr_id,
        external_agent_eligible=1, external_agent_task_type=task_type, blast_radius=blast_radius,
        requires_multi_file_context=1 if requires_multi_file_context else 0,
        files_touched=list(files_touched), metadata=metadata,
    )
    return {"umr_id": umr_id, "eligible": True, "reasons": []}


# Real, fixed, documented prompt template (UMR-20260806-095416-b6f0 §4). The
# Owner copies everything render_external_agent_prompt() returns into
# chat.z.ai by hand and pastes chat.z.ai's reply back by hand -- NEVER any
# browser automation against chat.z.ai, a hard Terms-of-Service constraint,
# not a style preference. The two identifying lines
# ("DISPATCH_ID: <id>" / "UMR_ID: <id>") are deliberately literal, exact,
# line-anchored, and grep-able -- parse_external_agent_reply() below parses
# them with an anchored regex, not a fuzzy search, so a reply that doesn't
# open with them exactly is rejected rather than misparsed.
_EXTERNAL_AGENT_PROMPT_TEMPLATE = """\
=== EXTERNAL AGENT DISPATCH (chat.z.ai manual-paste bridge) ===
DISPATCH_ID: {dispatch_id}
UMR_ID: {umr_id}
=== END IDENTIFYING LINES -- copy everything below this line into chat.z.ai, then paste chat.z.ai's reply back verbatim (nothing added, nothing removed) ===

You are acting as an external contract engineer with NO direct access to
this private repository. Below is the exact, complete, real, current
content of every file you are allowed to touch. Do not assume any other
file's content. Do not ask for repository access. Do not browse the web for
this repository.

TASK TYPE: {task_type}
FILES YOU MAY TOUCH (exactly these paths, no others): {files_list}
FILES_TOUCHED_JSON: {files_json}

ACCEPTANCE CRITERIA:
{acceptance_criteria}

REPRO STEPS:
{repro_steps}

CURRENT FILE CONTENTS:
{file_content_blocks}
REQUIRED REPLY FORMAT -- your reply is parsed by a program, not read by a
human first, so follow this EXACTLY:
  Line 1 (exact, verbatim): DISPATCH_ID: {dispatch_id}
  Line 2 (exact, verbatim): UMR_ID: {umr_id}
  Then exactly ONE fenced diff block, opened with ```diff and closed with
  ```, containing a real, valid unified diff (git-apply-compatible) that
  touches ONLY the file(s) listed above.
  Nothing else of substance: no other prose, no other code block, no
  commentary before or after the diff fence.

Do not touch any file not listed above. Do not add, move, delete, or rename
any file not listed above. Do not include secrets, credentials, or any
change unrelated to the acceptance criteria above.
=== END OF TASK ===
"""


def render_external_agent_prompt(*, dispatch_id, umr_id, task_type, files_touched,
                                  acceptance_criteria, repro_steps, file_contents):
    """Renders the real, fixed prompt template above. `file_contents` is a
    dict of {real repo-relative path: real file text or None (file does not
    yet exist -- a real, honest signal for a task that creates a new file,
    never silently blanked)}."""
    blocks = []
    for path in files_touched:
        content = file_contents.get(path)
        if content is None:
            body = "(this file does not exist yet in the repository -- this is a real new-file task)"
        else:
            body = content
        blocks.append(f"--- BEGIN FILE: {path} ---\n{body}\n--- END FILE: {path} ---")
    return _EXTERNAL_AGENT_PROMPT_TEMPLATE.format(
        dispatch_id=dispatch_id, umr_id=umr_id, task_type=task_type,
        files_list=", ".join(files_touched), files_json=json.dumps(list(files_touched)),
        acceptance_criteria=acceptance_criteria or "(none recorded)",
        repro_steps=repro_steps or "(not applicable to this task_type)",
        file_content_blocks="\n\n".join(blocks),
    )


def parse_external_agent_reply(text):
    """Real, strict parser for the Owner's pasted-back chat.z.ai reply
    (UMR-20260806-095416-b6f0 §4/§6). Returns (parsed: dict|None,
    error: str|None) -- exactly one of the two is real/non-None. `parsed`
    is {"dispatch_id": str, "umr_id": str, "diff_text": str} on success.

    Real, strict requirements (any violation is a real reject, never a
    best-effort recovery):
      - the first two real non-blank lines must be exactly
        'DISPATCH_ID: <id>' then 'UMR_ID: <id>' (line-anchored, no leading
        text tolerated on either line);
      - everything after those two lines must contain EXACTLY one fenced
        code block, opened with the literal ```diff and closed with ```;
      - nothing else of substance (only whitespace) may appear before or
        after that single fenced block."""
    lines = text.splitlines()
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx + 1 >= len(lines):
        return None, "reply is too short -- expected a DISPATCH_ID line and a UMR_ID line, followed by a fenced diff block"
    m1 = re.match(r"^DISPATCH_ID:\s*(\S+)\s*$", lines[idx].strip())
    if not m1:
        return None, f"line {idx + 1} must be exactly 'DISPATCH_ID: <id>', got {lines[idx]!r}"
    m2 = re.match(r"^UMR_ID:\s*(\S+)\s*$", lines[idx + 1].strip())
    if not m2:
        return None, f"line {idx + 2} must be exactly 'UMR_ID: <id>', got {lines[idx + 1]!r}"
    dispatch_id = m1.group(1)
    umr_id = m2.group(1)
    rest = "\n".join(lines[idx + 2:])
    fences = list(re.finditer(r"^```(\S*)\s*$", rest, re.MULTILINE))
    if len(fences) != 2:
        return None, f"expected exactly one fenced diff block (2 ``` fence lines), found {len(fences)} fence line(s)"
    open_fence, close_fence = fences
    open_tag = open_fence.group(1).strip()
    if open_tag != "diff":
        return None, f"the fenced block must be opened with ```diff exactly, got ```{open_tag!r}"
    before = rest[:open_fence.start()].strip()
    after = rest[close_fence.end():].strip()
    if before or after:
        return None, "reply must contain nothing else of substance outside the single fenced ```diff block"
    diff_text = rest[open_fence.end():close_fence.start()].strip("\n")
    if not diff_text.strip():
        return None, "the fenced ```diff block is empty"
    return {"dispatch_id": dispatch_id, "umr_id": umr_id, "diff_text": diff_text}, None


def _extract_diff_paths(diff_text):
    """Real, best-effort-but-conservative extraction of every real file path
    a unified diff touches, used both to validate the real allow-list
    before ever applying anything AND (via `git status --porcelain` after a
    real apply) as defense-in-depth against a diff that lies about its own
    header paths. Prefers real `diff --git a/X b/Y` headers (git's own real
    format); falls back to `+++`/`---` headers only when no `diff --git`
    header exists at all (a minimal single-file unified diff)."""
    paths = set()
    for m in re.finditer(r"^diff --git a/(.+?) b/(.+?)\s*$", diff_text, re.MULTILINE):
        paths.add(m.group(1))
        paths.add(m.group(2))
    if not paths:
        for m in re.finditer(r"^\+\+\+ (?:b/)?(.+?)\s*$", diff_text, re.MULTILINE):
            if m.group(1) != "/dev/null":
                paths.add(m.group(1))
        for m in re.finditer(r"^--- (?:a/)?(.+?)\s*$", diff_text, re.MULTILINE):
            if m.group(1) != "/dev/null":
                paths.add(m.group(1))
    return paths


def _extract_files_allowlist_from_prompt(prompt_path):
    """Real cross-check read: recovers the real files-touched allow-list
    exactly as it was embedded (FILES_TOUCHED_JSON: [...]) in the real
    prompt file at real dispatch time, independent of umr_tasks.files_touched's
    CURRENT value -- so submit_external_agent_result() validates against the
    real allow-list 'computed at dispatch time' per UMR-20260806-095416-b6f0
    §6, not merely today's value (nothing in this codebase mutates
    files_touched between dispatch and submit today, but this makes that a
    checked property, not an assumed one). Returns None (skip the
    cross-check, real umr_tasks.files_touched alone remains authoritative)
    if the prompt file is missing/unreadable or the marker line can't be
    found/parsed -- never raises."""
    try:
        with open(prompt_path, "r") as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r"^FILES_TOUCHED_JSON:\s*(\[.*\])\s*$", text, re.MULTILINE)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except (ValueError, TypeError):
        return None


def _default_external_agent_runner(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def _git_default_branch(repo_root, git_run):
    r = git_run(["git", "symbolic-ref", "refs/remotes/origin/HEAD"], repo_root)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip().rsplit("/", 1)[-1]
    return "main"


def _git_worktree_cleanup(repo_root, worktree_path, git_run):
    """Safe to call unconditionally once a real commit exists in the
    worktree's branch: `git worktree remove` only deletes the working-tree
    checkout directory, never the branch ref or its commits -- those live in
    the shared repo's own refs (same repo the worktree was added from), so
    they stay real and inspectable (`git show <branch>`) even after this
    runs. Keeps ai-os/external_agent/worktrees/ from accumulating one stale
    directory per real dispatch attempt forever."""
    git_run(["git", "worktree", "remove", "--force", worktree_path], repo_root)


def _gh_repo_slug(repo_root, git_run):
    r = git_run(["git", "remote", "get-url", "origin"], repo_root)
    url = (r.stdout or "").strip()
    m = re.search(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?$", url)
    return m.group(1) if m else None


def _extract_pr_number(pr_url):
    if not pr_url:
        return None
    m = re.search(r"/pull/(\d+)", pr_url)
    return int(m.group(1)) if m else None


def _external_agent_apply_reject(conn, disp, task, now, reason):
    """Real two-strike bookkeeping shared by submit_external_agent_result()
    (a real validation/apply/gate/PR failure) and
    expire_external_agent_dispatches() (a real 24h timeout with no
    paste-back at all) -- both are real 'this attempt failed' events for the
    exact same real state machine. Does NOT commit -- caller owns the
    transaction, same convention as update_umr_task() etc."""
    conn.execute(
        "UPDATE external_agent_dispatch SET status='rejected', reject_reason=?, submitted_at=? "
        "WHERE dispatch_id=?",
        (reason, now, disp["dispatch_id"]),
    )
    new_count = task["external_agent_reject_count"] + 1
    if new_count < EXTERNAL_AGENT_MAX_ATTEMPTS:
        update_umr_task(conn, task["umr_id"], external_agent_reject_count=new_count,
                         external_agent_status="requeued")
        outcome = "rejected_requeued"
    else:
        fallback_reason = (
            f"external-agent two-strike limit reached ({new_count} rejects, real dispatch "
            f"{disp['dispatch_id']}) -- permanent fallback to the normal internal worker pool. "
            f"Last reject reason: {reason}"
        )
        update_umr_task(conn, task["umr_id"], external_agent_reject_count=new_count,
                         external_agent_status="fallen_back_internal", external_agent_eligible=0,
                         reason=fallback_reason)
        outcome = "rejected_fallback_to_internal"
    return {"outcome": outcome, "dispatch_id": disp["dispatch_id"], "umr_id": task["umr_id"],
            "reason": reason, "reject_count": new_count}


def get_next_external_agent_task(conn, *, artifacts_root, repo_root, now=None):
    """Real CLI command `get-next-external-agent-task`'s core logic
    (UMR-20260806-095416-b6f0 §5). Selects ONE real eligible umr_tasks row.
    Caller (cmd_get_next_external_agent_task) wraps this whole call in
    `with _write_lock():` -- the SAME cross-process serialization
    mechanism every other real write path in this file uses (see
    _write_lock()'s own docstring) -- so two concurrent real callers can
    never both select and dispatch the same real row: the second caller
    always blocks until the first's SELECT+INSERT+UPDATE+commit has fully
    landed, then re-runs its own SELECT against the now-updated real state.

    Deliberately does NOT read or write umr_tasks.status (the general
    systemctl-worker/dispatch-tick lifecycle column) -- external_agent_status
    is a real, fully separate state machine for this real, fully separate
    channel; touching `status` here would falsely signal to
    resource_governor.py/health-check-15min.py/dispatch-tick.py that a real
    systemd worker unit is running, when none is.

    Returns None if no real row is currently eligible+available. Otherwise
    returns a dict with the real dispatch_id/prompt_text/prompt_path/
    expires_at/umr_id. Does NOT commit -- caller owns the transaction."""
    now = now or _now_iso()
    row = conn.execute(
        "SELECT * FROM umr_tasks WHERE external_agent_eligible=1 "
        "AND (external_agent_status IS NULL OR external_agent_status='requeued') "
        "AND external_agent_reject_count < ? "
        "ORDER BY tier ASC, ts_submitted ASC LIMIT 1",
        (EXTERNAL_AGENT_MAX_ATTEMPTS,),
    ).fetchone()
    if row is None:
        return None

    dispatch_id = _new_id("EAD")
    metadata = json.loads(row["metadata_json"] or "{}")
    ext_meta = metadata.get("external_agent") or {}
    files_touched = json.loads(row["files_touched"] or "[]")
    # Real defense-in-depth (found during real independent review): re-check
    # every path here too, immediately before it's ever read, even though
    # mark_external_agent_eligible() (the only real path that ever sets
    # files_touched) already refuses an unsafe path via
    # check_external_agent_eligibility(). os.path.join(repo_root, rel_path)
    # below silently DISCARDS repo_root for an absolute rel_path -- this is
    # the exact real line that would leak arbitrary local file content into
    # the prompt handed to chat.z.ai if that first gate were ever bypassed
    # or this function were ever called with a hand-built row.
    unsafe = [p for p in files_touched if _is_unsafe_external_agent_path(p)]
    if unsafe:
        raise ValueError(
            f"real invariant violation: umr_tasks {row['umr_id']!r} files_touched contains unsafe "
            f"absolute/path-traversal entries {unsafe} -- refusing to read or embed them into a "
            f"prompt for chat.z.ai"
        )
    file_contents = {}
    for rel_path in files_touched:
        abs_path = os.path.join(repo_root, rel_path)
        if os.path.isfile(abs_path):
            with open(abs_path, "r", errors="replace") as f:
                file_contents[rel_path] = f.read()
        else:
            file_contents[rel_path] = None

    prompt_text = render_external_agent_prompt(
        dispatch_id=dispatch_id, umr_id=row["umr_id"], task_type=row["external_agent_task_type"],
        files_touched=files_touched, acceptance_criteria=ext_meta.get("acceptance_criteria") or "",
        repro_steps=ext_meta.get("repro_steps") or "", file_contents=file_contents,
    )
    prompt_dir = os.path.join(artifacts_root, "prompts")
    os.makedirs(prompt_dir, exist_ok=True)
    prompt_path = os.path.join(prompt_dir, f"{dispatch_id}.txt")
    with open(prompt_path, "w") as f:
        f.write(prompt_text)
    prompt_sha256 = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()

    dispatched_at = now
    expires_at = (datetime.fromisoformat(now) + timedelta(hours=EXTERNAL_AGENT_DISPATCH_TTL_HOURS)).isoformat()

    conn.execute(
        "INSERT INTO external_agent_dispatch (dispatch_id, umr_id, provider, prompt_sha256, prompt_path, "
        "status, dispatched_at, expires_at) VALUES (?,?,?,?,?,?,?,?)",
        (dispatch_id, row["umr_id"], EXTERNAL_AGENT_PROVIDER, prompt_sha256, prompt_path,
         "dispatched", dispatched_at, expires_at),
    )
    update_umr_task(
        conn, row["umr_id"],
        external_agent_status="dispatched",
        external_agent_dispatch_count=row["external_agent_dispatch_count"] + 1,
    )
    return {
        "dispatch_id": dispatch_id, "umr_id": row["umr_id"], "prompt_text": prompt_text,
        "prompt_path": prompt_path, "expires_at": expires_at, "dispatched_at": dispatched_at,
    }


def submit_external_agent_result(conn, *, reply_text, artifacts_root, repo_root, now=None,
                                  reviewed_by=None, push=True, git_run=None, gh_run=None):
    """Real CLI command `submit-external-agent-result`'s core logic
    (UMR-20260806-095416-b6f0 §6). `git_run`/`gh_run` default to real
    subprocess calls (_default_external_agent_runner); tests inject fakes so
    they never depend on real network/gh-auth while still exercising real
    `git` against a real scratch repo for the worktree-never-main safety
    property. Does NOT commit -- caller owns the transaction, same
    convention as every other real write function in this file."""
    now = now or _now_iso()
    git_run = git_run or _default_external_agent_runner
    gh_run = gh_run or _default_external_agent_runner

    parsed, err = parse_external_agent_reply(reply_text)
    if err:
        return {"outcome": "error", "reason": f"reply parse failed: {err}"}

    dispatch_id = parsed["dispatch_id"]
    disp = conn.execute("SELECT * FROM external_agent_dispatch WHERE dispatch_id=?", (dispatch_id,)).fetchone()
    if disp is None:
        return {"outcome": "error", "reason": f"no real external_agent_dispatch row for dispatch_id={dispatch_id!r}"}
    if disp["status"] != "dispatched":
        return {"outcome": "error",
                 "reason": f"dispatch {dispatch_id} is not in status='dispatched' (found {disp['status']!r}) "
                            f"-- refusing to double-process the same real attempt"}

    task = conn.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (disp["umr_id"],)).fetchone()
    if task is None:
        return {"outcome": "error", "reason": f"umr_tasks row {disp['umr_id']!r} not found"}

    if parsed["umr_id"] != disp["umr_id"]:
        return _external_agent_apply_reject(
            conn, disp, task, now,
            reason=f"UMR_ID mismatch: dispatch {dispatch_id} really belongs to {disp['umr_id']!r}, "
                   f"reply named {parsed['umr_id']!r}",
        )

    if disp["expires_at"] and now > disp["expires_at"]:
        return _external_agent_apply_reject(
            conn, disp, task, now,
            reason=f"dispatch {dispatch_id} expired at {disp['expires_at']} (submitted at {now})",
        )

    allowlist = set(json.loads(task["files_touched"] or "[]"))
    prompt_allowlist = _extract_files_allowlist_from_prompt(disp["prompt_path"])
    if prompt_allowlist is not None and set(prompt_allowlist) != allowlist:
        return _external_agent_apply_reject(
            conn, disp, task, now,
            reason=f"files_touched allow-list drifted between real dispatch and real submit for "
                   f"{dispatch_id}: prompt-time={sorted(prompt_allowlist)} current={sorted(allowlist)}",
        )

    diff_text = parsed["diff_text"]
    diff_paths = _extract_diff_paths(diff_text)
    if not diff_paths:
        return _external_agent_apply_reject(
            conn, disp, task, now,
            reason="diff contains no recognizable file path headers ('diff --git'/'+++'/'---')",
        )
    unsafe = sorted(p for p in diff_paths if _is_unsafe_external_agent_path(p))
    if unsafe:
        return _external_agent_apply_reject(
            conn, disp, task, now, reason=f"diff touches unsafe/absolute/traversal path(s): {unsafe}",
        )
    out_of_scope = sorted(p for p in diff_paths if p not in allowlist)
    if out_of_scope:
        return _external_agent_apply_reject(
            conn, disp, task, now,
            reason=f"diff touches path(s) outside the real allow-list computed at dispatch time: "
                   f"{out_of_scope} (allowed: {sorted(allowlist)})",
        )

    # --- apply ONLY to a fresh real git worktree, never main directly ---
    branch_name = f"external-agent/{dispatch_id}"
    worktree_path = os.path.join(artifacts_root, "worktrees", dispatch_id)
    os.makedirs(os.path.dirname(worktree_path), exist_ok=True)
    default_branch = _git_default_branch(repo_root, git_run)

    r = git_run(["git", "worktree", "add", "-b", branch_name, worktree_path, f"origin/{default_branch}"], repo_root)
    if r.returncode != 0:
        return _external_agent_apply_reject(
            conn, disp, task, now, reason=f"git worktree add failed: {(r.stderr or '').strip()[:2000]}",
        )

    patch_path = os.path.join(artifacts_root, "patches", f"{dispatch_id}.patch")
    os.makedirs(os.path.dirname(patch_path), exist_ok=True)
    with open(patch_path, "w") as f:
        f.write(diff_text + "\n")

    check_r = git_run(["git", "apply", "--check", patch_path], worktree_path)
    if check_r.returncode != 0:
        _git_worktree_cleanup(repo_root, worktree_path, git_run)
        return _external_agent_apply_reject(
            conn, disp, task, now, reason=f"diff failed 'git apply --check': {(check_r.stderr or '').strip()[:2000]}",
        )
    apply_r = git_run(["git", "apply", patch_path], worktree_path)
    if apply_r.returncode != 0:
        _git_worktree_cleanup(repo_root, worktree_path, git_run)
        return _external_agent_apply_reject(
            conn, disp, task, now, reason=f"diff failed to apply: {(apply_r.stderr or '').strip()[:2000]}",
        )

    status_r = git_run(["git", "status", "--porcelain"], worktree_path)
    changed_paths = {line[3:].strip() for line in (status_r.stdout or "").splitlines() if line.strip()}
    sneaky = sorted(p for p in changed_paths if p not in allowlist)
    if sneaky:
        _git_worktree_cleanup(repo_root, worktree_path, git_run)
        return _external_agent_apply_reject(
            conn, disp, task, now, reason=f"applied diff changed real path(s) outside the allow-list: {sneaky}",
        )

    git_run(["git", "add", "-A"], worktree_path)
    commit_msg = (
        f"external-agent(chat.z.ai): {task['task_identity']}\n\n"
        f"DISPATCH_ID: {dispatch_id}\nUMR_ID: {task['umr_id']}\n"
        f"Real Owner directive: UMR-20260806-095416-b6f0"
    )
    commit_r = git_run(["git", "commit", "-m", commit_msg], worktree_path)
    if commit_r.returncode != 0:
        _git_worktree_cleanup(repo_root, worktree_path, git_run)
        return _external_agent_apply_reject(
            conn, disp, task, now,
            reason=f"git commit failed (empty diff / no real change?): {(commit_r.stderr or '').strip()[:2000]}",
        )

    # --- real, existing, unmodified quality-gate pipeline -- zero special-casing ---
    gate_report_path = os.path.join(artifacts_root, "gate-reports", f"{dispatch_id}.json")
    os.makedirs(os.path.dirname(gate_report_path), exist_ok=True)
    quality_gate_sh = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quality-gate.sh")
    gate_r = git_run(["bash", quality_gate_sh, worktree_path, gate_report_path], repo_root)
    conn.execute(
        "UPDATE external_agent_dispatch SET branch_name=?, worktree_path=?, gate_report_path=?, submitted_at=? "
        "WHERE dispatch_id=?",
        (branch_name, worktree_path, gate_report_path, now, dispatch_id),
    )
    if gate_r.returncode != 0:
        _git_worktree_cleanup(repo_root, worktree_path, git_run)
        return _external_agent_apply_reject(
            conn, disp, task, now, reason=f"real quality-gate.sh FAILED (see {gate_report_path})",
        )

    # --- push + open a real PR, tagged with external-agent provenance -- NEVER auto-merged ---
    if push:
        push_r = git_run(["git", "push", "origin", branch_name], repo_root)
        if push_r.returncode != 0:
            _git_worktree_cleanup(repo_root, worktree_path, git_run)
            return _external_agent_apply_reject(
                conn, disp, task, now, reason=f"git push failed: {(push_r.stderr or '').strip()[:2000]}",
            )

    pr_title = f"external-agent(chat.z.ai): {task['task_identity']}"
    pr_body = (
        "**EXTERNAL AGENT PR -- chat.z.ai manual-paste bridge**\n\n"
        "Provenance: this real change was produced by a human copy/paste round trip to "
        "chat.z.ai (never browser automation -- hard Terms-of-Service constraint), applied "
        "to a fresh real git worktree (never main directly), and passed the real, "
        "unmodified quality-gate.sh pipeline.\n\n"
        f"DISPATCH_ID: {dispatch_id}\nUMR_ID: {task['umr_id']}\n"
        "Real Owner directive: UMR-20260806-095416-b6f0\n\n"
        "**NEVER AUTO-MERGE THIS PR.** It requires real human review before merge, "
        "regardless of what the normal internal-worker auto-merge pipeline does for other PRs.\n"
    )
    repo_slug = _gh_repo_slug(repo_root, git_run)
    pr_cmd = ["gh", "pr", "create", "--base", default_branch, "--head", branch_name,
              "--title", pr_title, "--body", pr_body, "--label", "external-agent-review-required"]
    if repo_slug:
        pr_cmd[2:2] = ["--repo", repo_slug]
    pr_r = gh_run(pr_cmd, repo_root)
    if pr_r.returncode != 0:
        # Real, best-effort retry without --label (a missing/unrecognized label must
        # never block a real, otherwise-successful PR from opening for real human review).
        pr_cmd_no_label = [c for c in pr_cmd if c not in ("--label", "external-agent-review-required")]
        pr_r = gh_run(pr_cmd_no_label, repo_root)
    if pr_r.returncode != 0:
        return _external_agent_apply_reject(
            conn, disp, task, now, reason=f"gh pr create failed: {(pr_r.stderr or '').strip()[:2000]}",
        )

    pr_url = (pr_r.stdout or "").strip().splitlines()[-1] if (pr_r.stdout or "").strip() else None
    pr_number = _extract_pr_number(pr_url)
    conn.execute(
        "UPDATE external_agent_dispatch SET status='accepted', pr_number=?, pr_url=?, reviewed_by=? "
        "WHERE dispatch_id=?",
        (pr_number, pr_url, reviewed_by, dispatch_id),
    )
    update_umr_task(conn, task["umr_id"], external_agent_status="pr_open")
    return {
        "outcome": "accepted", "dispatch_id": dispatch_id, "umr_id": task["umr_id"],
        "pr_url": pr_url, "pr_number": pr_number, "branch_name": branch_name,
        "gate_report_path": gate_report_path,
    }


def expire_external_agent_dispatches(conn, now=None):
    """Real CLI command `expire-external-agent-dispatches`'s core logic
    (UMR-20260806-095416-b6f0 §7). Pure bookkeeping, zero reasoning/
    judgment: every real `external_agent_dispatch` row still in
    status='dispatched' whose expires_at has passed is mechanically marked
    'expired', and its umr_tasks row gets exactly the same real two-strike
    treatment as any other real failure (_external_agent_apply_reject) --
    never a 3rd real dispatch. Idempotent (the `WHERE status='dispatched'`
    guard means an already-processed row is never touched twice) and
    side-effect-free beyond marking rows (no git/gh calls at all) -- real,
    safe to run on a real schedule (cron/systemd timer). Does NOT commit --
    caller owns the transaction."""
    now = now or _now_iso()
    rows = conn.execute(
        "SELECT * FROM external_agent_dispatch WHERE status='dispatched' AND expires_at < ?",
        (now,),
    ).fetchall()
    results = []
    for disp in rows:
        task = conn.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (disp["umr_id"],)).fetchone()
        if task is None:
            conn.execute(
                "UPDATE external_agent_dispatch SET status='expired', submitted_at=? "
                "WHERE dispatch_id=? AND status='dispatched'",
                (now, disp["dispatch_id"]),
            )
            results.append({"dispatch_id": disp["dispatch_id"], "umr_id": disp["umr_id"],
                             "outcome": "expired_orphaned_no_umr_row"})
            continue
        conn.execute(
            "UPDATE external_agent_dispatch SET status='expired', submitted_at=? "
            "WHERE dispatch_id=? AND status='dispatched'",
            (now, disp["dispatch_id"]),
        )
        result = _external_agent_apply_reject(
            conn, disp, task, now,
            reason=f"dispatch {disp['dispatch_id']} expired (real {EXTERNAL_AGENT_DISPATCH_TTL_HOURS}h "
                   f"window, expires_at={disp['expires_at']}) with no real paste-back received",
        )
        # _external_agent_apply_reject() already set external_agent_dispatch.status='rejected' --
        # this real expiry sweep's own outcome is 'expired' regardless, so overwrite that one
        # field back for an accurate real audit trail (the row's terminal status here really is
        # 'expired', a distinct real reason from a rejected-on-submit row).
        conn.execute(
            "UPDATE external_agent_dispatch SET status='expired' WHERE dispatch_id=?",
            (disp["dispatch_id"],),
        )
        result["dispatch_status"] = "expired"
        results.append(result)
    return results


def cmd_get_next_external_agent_task(args):
    """Usage:
      python3 superboss-register.py get-next-external-agent-task \\
          [--artifacts-root PATH] [--repo-root PATH]
    Prints a JSON header, then the real rendered prompt text -- the Owner
    copies everything from the "COPY EVERYTHING BELOW" marker onward into
    chat.z.ai by hand. NEVER any browser automation against chat.z.ai."""
    init_db_silent()
    conn = _connect()
    _ensure_umr_table(conn)
    _ensure_external_agent_dispatch_table(conn)
    artifacts_root = args.artifacts_root or EXTERNAL_AGENT_ARTIFACTS_ROOT
    repo_root = args.repo_root or EXTERNAL_AGENT_REPO_ROOT
    with _write_lock():
        result = get_next_external_agent_task(conn, artifacts_root=artifacts_root, repo_root=repo_root)
        if result is not None:
            conn.commit()
    conn.close()
    if result is None:
        print(json.dumps({"ok": True, "dispatched": False,
                           "message": "no real eligible external-agent task available right now"}, indent=2))
        return
    print(json.dumps({"ok": True, "dispatched": True, "dispatch_id": result["dispatch_id"],
                       "umr_id": result["umr_id"], "prompt_path": result["prompt_path"],
                       "expires_at": result["expires_at"]}, indent=2))
    print("\n" + "=" * 78)
    print("COPY EVERYTHING BELOW THIS LINE INTO chat.z.ai BY HAND "
          "(never browser-automate this -- hard ToS constraint):")
    print("=" * 78 + "\n")
    print(result["prompt_text"])


def cmd_submit_external_agent_result(args):
    """Usage:
      python3 superboss-register.py submit-external-agent-result \\
          --reply-file PATH [--reviewed-by NAME] [--no-push] \\
          [--artifacts-root PATH] [--repo-root PATH]
    --reply-file is a real file holding exactly what the Owner pasted back
    from chat.z.ai, byte for byte."""
    init_db_silent()
    conn = _connect()
    _ensure_umr_table(conn)
    _ensure_external_agent_dispatch_table(conn)
    with open(args.reply_file, "r") as f:
        reply_text = f.read()
    artifacts_root = args.artifacts_root or EXTERNAL_AGENT_ARTIFACTS_ROOT
    repo_root = args.repo_root or EXTERNAL_AGENT_REPO_ROOT
    with _write_lock():
        result = submit_external_agent_result(
            conn, reply_text=reply_text, artifacts_root=artifacts_root, repo_root=repo_root,
            reviewed_by=args.reviewed_by, push=not args.no_push,
        )
        conn.commit()
    conn.close()
    print(json.dumps(result, indent=2, default=str))


def cmd_expire_external_agent_dispatches(args):
    """Usage: python3 superboss-register.py expire-external-agent-dispatches
    Pure bookkeeping, zero reasoning/judgment -- safe on a real cron/systemd
    timer schedule."""
    init_db_silent()
    conn = _connect()
    _ensure_umr_table(conn)
    _ensure_external_agent_dispatch_table(conn)
    with _write_lock():
        results = expire_external_agent_dispatches(conn)
        conn.commit()
    conn.close()
    print(json.dumps({"ok": True, "expired_count": len(results), "results": results}, indent=2, default=str))


def cmd_mark_external_agent_eligible(args):
    """Usage:
      python3 superboss-register.py mark-external-agent-eligible \\
          --umr-id UMR-... --task-type {isolated_bugfix,doc_update,single_file_refactor,test_addition_only} \\
          --blast-radius isolated --files-touched-json '["path/one.md"]' \\
          --acceptance-criteria "..." [--repro-steps "..."] [--requires-multi-file-context]"""
    init_db_silent()
    conn = _connect()
    _ensure_umr_table(conn)
    files_touched = json.loads(args.files_touched_json)
    with _write_lock():
        result = mark_external_agent_eligible(
            conn, args.umr_id, task_type=args.task_type, blast_radius=args.blast_radius,
            requires_multi_file_context=args.requires_multi_file_context,
            files_touched=files_touched, acceptance_criteria=args.acceptance_criteria,
            repro_steps=args.repro_steps,
        )
        conn.commit()
    conn.close()
    print(json.dumps(result, indent=2, default=str))


# ---------------------------------------------------------------------------
# Owner priority sequence (real amendment to UMR-20260807-070110-5ea7, itself
# governed by UMR-20260806-124055-bc80; this section built under
# task-20260807-081913-amendment-to-umr-20260807-070110-5ea7): 5ea7 built the
# narrow single-table owner_priority_override fix (umr_id, reason, set_by,
# ts -- created idempotently again below in case that UMR's own worker has
# not created it yet at the moment this one runs). This section extends it
# into a real, self-advancing 4-phase sequence so the Owner never again has
# to hand-edit the override table mid-starvation-fix.
#
# PHASE 1/2 membership is the exact, explicit, bounded UMR id set this
# task's own SPEC named ("plus its blocker chain" / "plus") -- frozen as
# literal constants below, never a search.
#
# PHASE 3/4 membership is real OCID-020/OCID-021 governing UMRs, looked up
# live from ocid_canonical_registry (never hand-typed), plus every real UMR
# discovered by discover_prompt_citing_umrs() below. That search is
# deliberately scoped to each candidate row's own parsed
# inputs_json.prompt/.title fields, NOT a raw substring scan of the whole
# row (inputs_json column or metadata_json column) -- live-verified before
# writing this: several real umr_tasks rows carry multi-MEGABYTE
# metadata_json blobs (e.g. UMR-20260806-130110-c620 at 7.1MB; dozens of
# others in the 6-7MB range) that are historical audit-report dumps
# embedded for storage convenience, not real linkage. A raw LIKE scan
# across metadata_json for OCID-020's governing UMR
# (UMR-20260802-165606-4413) matched 567 of 8022 rows; restricting to
# genuinely parsed prompt/title text narrows that to the real citation set
# (179 rows) -- e.g. UMR-20260729-112414-3269, dated BEFORE
# UMR-20260802-165606-4413 even existed, "matched" only because its
# 1.19MB metadata_json embeds an unrelated program-registration report
# that happens to name it in a "purpose" string deep inside. prompt/title
# are the real, comparatively small, human/agent-authored narrative fields
# every genuine citation ("citing UMR-X for OCID-020",
# "GOVERNING CHAIN: UMR-X, UMR-Y") actually lives in.
# ---------------------------------------------------------------------------

OWNER_PRIORITY_PHASE1_MEMBERS = [
    "UMR-20260806-141055-1fec", "UMR-20260807-024922-f432", "UMR-20260807-061238-ae93",
]
OWNER_PRIORITY_PHASE2_MEMBERS = [
    "UMR-20260806-171945-5767", "UMR-20260807-035145-aa45", "UMR-20260807-040704-992a",
]


def _ensure_owner_priority_tables(conn):
    """Idempotent CREATE TABLE IF NOT EXISTS, same convention as every other
    _ensure_<table>_table() in this file. Two real, additive tables:

    owner_priority_sequence -- one row per real phase (phase_order,
    phase_name, governing_umr, real_member_umrs as a real JSON array,
    status in {'pending','active','complete'}). The durable record of what
    the Owner's real 4-phase order actually names -- written once by
    seed_owner_priority_sequence() below, never hand-edited afterward.
    Also carries confirmed_complete_members (JSON array, additive column,
    default '[]') -- real review finding (PR #256 review.json) confirmed
    advance_owner_priority_phases() re-ran _umr_genuinely_completed() for
    EVERY member on EVERY tick, including members already confirmed
    complete on a prior tick; this column is the persisted memo of which
    member UMRs have already been independently verified complete, so a
    later tick only re-checks the real remainder. ALTER TABLE ADD COLUMN
    (not CREATE TABLE IF NOT EXISTS, which only covers brand-new DBs) --
    same additive-migration convention as _migrate_schema()'s own
    system_index.tags / wiring_registry.content_hash columns above.

    owner_priority_override -- UMR-20260807-070110-5ea7's own real table
    (umr_id, reason, set_by, ts), created here too (idempotently) since
    that UMR's own worker may not have created it yet at the time this one
    runs concurrently -- both workers race to CREATE TABLE IF NOT EXISTS
    the identical schema, which is safe by construction (SQLite serializes
    DDL under this module's own _write_lock() anyway). Real review finding
    (PR #256 review.json): a CREATE TABLE IF NOT EXISTS race is only safe
    if the two real schemas genuinely match -- if 5ea7's own worker created
    a table with a different real column set first, this would otherwise
    silently coexist and misbehave rather than fail loudly. So after the
    idempotent create, this function independently re-verifies via
    PRAGMA table_info that the table actually on disk -- whoever's worker
    created it -- has exactly the expected (umr_id, reason, set_by, ts)
    columns, and raises loudly (never silently proceeds) if it does not."""
    conn.execute("""CREATE TABLE IF NOT EXISTS owner_priority_sequence (
        phase_order INTEGER PRIMARY KEY,
        phase_name TEXT NOT NULL,
        governing_umr TEXT NOT NULL,
        real_member_umrs TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT,
        updated_at TEXT
    )""")
    seq_cols = {row["name"] for row in conn.execute("PRAGMA table_info(owner_priority_sequence)").fetchall()}
    if "confirmed_complete_members" not in seq_cols:
        conn.execute(
            "ALTER TABLE owner_priority_sequence ADD COLUMN confirmed_complete_members TEXT NOT NULL DEFAULT '[]'"
        )
    conn.execute("""CREATE TABLE IF NOT EXISTS owner_priority_override (
        umr_id TEXT PRIMARY KEY,
        reason TEXT,
        set_by TEXT,
        ts TEXT
    )""")
    conn.commit()
    override_cols = {row["name"] for row in conn.execute("PRAGMA table_info(owner_priority_override)").fetchall()}
    expected_override_cols = {"umr_id", "reason", "set_by", "ts"}
    if override_cols != expected_override_cols:
        raise RuntimeError(
            "owner_priority_override real schema mismatch -- expected columns "
            f"{sorted(expected_override_cols)!r}, found {sorted(override_cols)!r} on disk. "
            "This means a concurrently-dispatched worker (e.g. UMR-20260807-070110-5ea7's own "
            "work) created this table with a real, different schema first -- refusing to proceed "
            "rather than silently writing against a mismatched table (PR #256 review.json finding)."
        )


def discover_prompt_citing_umrs(conn, governing_umr):
    """Real, deterministic, reproducible search for every umr_tasks row
    whose OWN inputs_json.prompt or inputs_json.title genuinely names
    `governing_umr` -- deliberately narrower than a raw substring scan of
    the whole row (see the module comment above for the live
    false-positive evidence that ruled that out). Two-step: a cheap SQL
    LIKE over the raw inputs_json column narrows the candidate set (SQLite
    cannot index into embedded JSON text), then each candidate's
    inputs_json is actually parsed and only its 'prompt'/'title' string
    values are checked. Returns a sorted list of umr_id strings, excluding
    governing_umr itself. Never raises on a malformed inputs_json row --
    skips it (fails closed: an unparseable row is never silently counted
    as a real citation)."""
    rows = conn.execute(
        "SELECT umr_id, inputs_json FROM umr_tasks WHERE inputs_json LIKE ? AND umr_id != ?",
        (f"%{governing_umr}%", governing_umr),
    ).fetchall()
    hits = []
    for row in rows:
        row = dict(row)
        try:
            inputs = json.loads(row["inputs_json"]) if row["inputs_json"] else {}
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(inputs, dict):
            continue
        prompt = inputs.get("prompt") or ""
        title = inputs.get("title") or ""
        if governing_umr in prompt or governing_umr in title:
            hits.append(row["umr_id"])
    return sorted(hits)


def _lookup_ocid_governing_umr(conn, ocid_number):
    """Real lookup against ocid_canonical_registry -- the single real
    OCID->canonical-UMR rollup this codebase already maintains (see
    _ensure_ocid_canonical_registry_table's own docstring). Returns the
    real canonical_umr_id, or None if the OCID has no row / no canonical
    UMR on file (never guessed/hand-typed)."""
    row = conn.execute(
        "SELECT canonical_umr_id FROM ocid_canonical_registry WHERE ocid_number = ?",
        (ocid_number,),
    ).fetchone()
    if not row:
        return None
    return dict(row)["canonical_umr_id"]


def build_owner_priority_sequence_phases(conn):
    """Real, deterministic construction of the 4 real phases this task's
    SPEC named, in strict order. Phases 1-2 are the literal explicit UMR
    ids the SPEC gave (OWNER_PRIORITY_PHASE1/2_MEMBERS above -- SPEC's own
    words, "plus its blocker chain"/"plus", name an exact, bounded,
    explicit set, not a search). Phases 3-4 look up OCID-020/OCID-021's
    real governing UMR live from ocid_canonical_registry, then run
    discover_prompt_citing_umrs() against it -- real member set = the
    governing UMR itself plus every real UMR discovered citing it. Raises
    ValueError if either OCID has no real canonical_umr_id on file (never
    silently seeds a phase with a hand-typed guess)."""
    ocid_020_umr = _lookup_ocid_governing_umr(conn, "OCID-020")
    if not ocid_020_umr:
        raise ValueError("OCID-020 has no real canonical_umr_id in ocid_canonical_registry -- refusing to guess")
    ocid_021_umr = _lookup_ocid_governing_umr(conn, "OCID-021")
    if not ocid_021_umr:
        raise ValueError("OCID-021 has no real canonical_umr_id in ocid_canonical_registry -- refusing to guess")

    phase3_children = discover_prompt_citing_umrs(conn, ocid_020_umr)
    phase4_children = discover_prompt_citing_umrs(conn, ocid_021_umr)

    return [
        {
            "phase_order": 1, "phase_name": "UMR-20260806-141055-1fec blocker chain",
            "governing_umr": "UMR-20260806-141055-1fec",
            "real_member_umrs": list(OWNER_PRIORITY_PHASE1_MEMBERS),
        },
        {
            "phase_order": 2, "phase_name": "UMR-20260806-171945-5767 amendment chain",
            "governing_umr": "UMR-20260806-171945-5767",
            "real_member_umrs": list(OWNER_PRIORITY_PHASE2_MEMBERS),
        },
        {
            "phase_order": 3, "phase_name": "OCID-020 governing UMR + discovered children",
            "governing_umr": ocid_020_umr,
            "real_member_umrs": sorted(set([ocid_020_umr] + phase3_children)),
        },
        {
            "phase_order": 4, "phase_name": "OCID-021 governing UMR + discovered children",
            "governing_umr": ocid_021_umr,
            "real_member_umrs": sorted(set([ocid_021_umr] + phase4_children)),
        },
    ]


def seed_owner_priority_sequence(conn, force=False):
    """Idempotent: does nothing (returns {'seeded': False, ...}) if
    owner_priority_sequence already has rows, unless force=True (test-only
    escape hatch for re-seeding a scratch copy). Seeds all 4 real phases in
    one transaction, phase 1 'active', phases 2-4 'pending' -- "Never
    activate more than one phase at once" from the SPEC is true from the
    very first write. Also performs the very first real
    owner_priority_override sync (phase 1's real members only)."""
    _ensure_owner_priority_tables(conn)
    existing = dict(conn.execute("SELECT COUNT(*) AS n FROM owner_priority_sequence").fetchone())
    if existing["n"] > 0 and not force:
        return {"seeded": False, "reason": "owner_priority_sequence already has rows"}
    if force:
        conn.execute("DELETE FROM owner_priority_sequence")
        conn.execute("DELETE FROM owner_priority_override")

    phases = build_owner_priority_sequence_phases(conn)
    now = _now_iso()
    for phase in phases:
        status = "active" if phase["phase_order"] == 1 else "pending"
        conn.execute(
            "INSERT INTO owner_priority_sequence "
            "(phase_order, phase_name, governing_umr, real_member_umrs, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (phase["phase_order"], phase["phase_name"], phase["governing_umr"],
             json.dumps(phase["real_member_umrs"]), status, now, now),
        )
    _sync_owner_priority_override(conn, now=now)
    conn.commit()
    return {"seeded": True, "phases": phases}


def _umr_genuinely_completed(conn, umr_id, repos_root="/opt/veridian/repos"):
    """Real evidence check, reusing validate_umr_terminal_completion_evidence
    (the same real gate cmd_mark_umr_terminal already enforces at WRITE
    time for new completions) as a READ-time re-verification -- deliberately
    does NOT trust umr_tasks.status='completed' by itself (the SPEC's own
    words: "not a status label alone"), since some real rows reach
    status='completed' through a different code path (a
    'veridian_task_create' dispatch row whose own outputs_json only ever
    recorded the spawned task's id, never a commit/file -- confirmed live
    for UMR-20260806-141055-1fec, one of this sequence's own Phase 1
    members) that the write-time gate does not cover.

    Returns (bool, reason_str). Fails closed: any ambiguity resolves to
    'not genuinely complete', never assumed complete."""
    row = conn.execute(
        "SELECT status, outputs_json FROM umr_tasks WHERE umr_id = ?", (umr_id,)
    ).fetchone()
    if not row:
        return False, f"{umr_id}: no such row in umr_tasks"
    row = dict(row)
    if row["status"] != "completed":
        return False, f"{umr_id}: status={row['status']!r}, not 'completed'"
    try:
        outputs = json.loads(row["outputs_json"]) if row["outputs_json"] else {}
    except (json.JSONDecodeError, TypeError):
        outputs = {}
    if not isinstance(outputs, dict):
        outputs = {}
    file_path = outputs.get("file_path")
    commit_sha = outputs.get("commit_sha")
    repo = outputs.get("repo")
    if repo == "veridian-scripts":
        # 2026-08-13 (task-20260813-103224, UMR-20260813-101142-5d24):
        # veridian-scripts is special-cased to the real live checkout
        # (/opt/veridian/scripts, kept current every 2h by sync-repos.sh's
        # direct `git pull --ff-only`) rather than the generic
        # repos_root-join, which would resolve to
        # /opt/veridian/repos/veridian-scripts -- an orphaned second
        # checkout, 200 commits behind as of this fix, nothing has pulled it
        # since 2026-08-06. Not a prior correctness bug (both
        # _umr_terminal_commit_exists / _is_umr_terminal_commit_ancestor_of_main
        # do a real `git fetch origin` before checking, in either checkout),
        # but pointing this at the real live tree is the honest, current
        # answer to "what does this repo mean on this box" going forward.
        repo_root = "/opt/veridian/scripts"
    else:
        repo_root = os.path.join(repos_root, repo) if repo else None
    allowed, refusal_reason = validate_umr_terminal_completion_evidence(
        status="completed", file_path=file_path, commit_sha=commit_sha, repo_root=repo_root,
    )
    if allowed:
        return True, (f"{umr_id}: real evidence verified "
                       f"(commit_sha={commit_sha!r} ancestor-of-main / file_path={file_path!r})")
    return False, f"{umr_id}: status='completed' but no independently-verifiable real evidence -- {refusal_reason}"


def _sync_owner_priority_override(conn, now=None):
    """Populates owner_priority_override with ONLY the currently-active
    phase's real members, always removing every prior entry first -- exact
    SPEC wording ("Populate owner_priority_override with only the active
    phase real members, always, removing prior phase entries"). If no
    phase is currently active (e.g. all 4 phases already complete), the
    table is left empty -- a real, reversible, honest "no override in
    effect" state, never a stale leftover."""
    now = now or _now_iso()
    active = conn.execute(
        "SELECT phase_order, phase_name, real_member_umrs FROM owner_priority_sequence WHERE status = 'active'"
    ).fetchall()
    conn.execute("DELETE FROM owner_priority_override")
    if not active:
        return {"active_phase": None, "override_members": []}
    if len(active) > 1:
        # Real, row-independent safety invariant -- must never happen by
        # construction (advance_owner_priority_phases only ever activates
        # the single next phase after completing the current one), but
        # fails loudly rather than silently picking one if it somehow did.
        raise RuntimeError(
            f"owner_priority_sequence has {len(active)} 'active' phases at once -- "
            "invariant violation, refusing to populate owner_priority_override"
        )
    phase = dict(active[0])
    members = json.loads(phase["real_member_umrs"])
    for umr_id in members:
        conn.execute(
            "INSERT INTO owner_priority_override (umr_id, reason, set_by, ts) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(umr_id) DO UPDATE SET reason=excluded.reason, set_by=excluded.set_by, ts=excluded.ts",
            (umr_id, f"owner_priority_sequence phase {phase['phase_order']} ({phase['phase_name']})",
             "owner_priority_sequence:advance_owner_priority_phases", now),
        )
    return {"active_phase": phase["phase_order"], "override_members": members}


# Real review finding (PR #256 review.json): phase 3/4 membership is a live
# discovery query, not the small bounded explicit list phases 1/2 use (the
# SPEC's own evidence: 179 hits for OCID-020, 70 for OCID-021) -- once one of
# those phases is active, evaluating every not-yet-confirmed member in one
# tick could require hundreds of synchronous real 'git fetch'/cat-file/
# merge-base subprocess calls (each up to 60s) before run_tick() ever reaches
# next_queued_task()/dispatch_one(), stalling dispatch for the entire system
# during a degraded network -- the exact starvation failure mode this feature
# exists to fix, reintroduced at a larger blast radius. Bounding how many
# NOT-YET-CONFIRMED members get a real evidence check in a single tick caps
# that worst case regardless of phase size; combined with the persisted
# confirmed_complete_members memo below, a large phase converges over
# several ticks instead of stalling one.
OWNER_PRIORITY_PHASE_MAX_EVALUATIONS_PER_TICK = 25


def advance_owner_priority_phases(conn, now=None, repos_root="/opt/veridian/repos"):
    """The real, deterministic advance function the SPEC requires to run
    every tick before next_queued_task (see resource_governor.py's
    run_tick(), which calls this first). Idempotent and safe to call every
    tick even when nothing changes: checks the currently-active phase's
    real members for genuine completion (via _umr_genuinely_completed
    above), and if -- and only if -- every single one is genuinely
    complete, marks that phase 'complete' and activates the next
    phase_order (if any pending phase remains). Always re-syncs
    owner_priority_override to the (possibly just-changed) active phase
    afterward, so the override table is never stale even on a tick that
    made no phase transition. Never activates more than one phase
    (owner_priority_sequence.status='active' is a real invariant enforced
    loudly by _sync_owner_priority_override above). Fully reversible: every
    write here is a plain UPDATE/DELETE/INSERT against these two tables,
    trivially undone (e.g. re-run seed_owner_priority_sequence(force=True)).

    Real review finding (PR #256 review.json, round 1), fixed here: this
    used to re-run _umr_genuinely_completed() for every member of the
    active phase on EVERY tick, including members already confirmed
    complete on a prior tick, with no bound on how many real evidence
    checks happen in one tick. Now: members already recorded in the row's
    own confirmed_complete_members (persisted JSON list) are never
    re-checked, and at most OWNER_PRIORITY_PHASE_MAX_EVALUATIONS_PER_TICK
    not-yet-confirmed members get a real _umr_genuinely_completed()
    evidence check per call -- the remainder are reported as 'not yet
    evaluated this tick' and picked up on a later call. The phase only
    transitions to 'complete' once every real member is present in
    confirmed_complete_members (i.e. every member has, across however many
    ticks it took, been independently verified) -- this bound changes WHEN
    a large phase's completion is detected, never WHETHER a member's
    completion is genuinely verified before the phase transitions.

    Real review finding (PR #256 review.json, round 2), fixed here too:
    round 1's fix still ran the entire function -- including the real
    evidence-check loop below, which for commit_sha-backed members shells
    out to real 60s-timeout git fetch/cat-file/merge-base subprocess calls
    -- while the caller held this file's own cross-process _write_lock(),
    the same OS-level flock every other write-path invocation of this
    script (dispatch, submit, mark-terminal, ...) across the whole system
    must also acquire. That serialized every writer in the system behind
    this one tick's git subprocess calls -- worse than round 1's bug, not
    better. Same convention cmd_mark_umr_terminal already uses (calls
    validate_umr_terminal_completion_evidence() BEFORE acquiring
    _write_lock(), wrapping only the resulting write): this function now
    acquires _write_lock() itself, in two short, separate critical
    sections around the real reads/writes only -- the real evidence-check
    loop in between runs with NO lock held at all. Callers (resource_governor.py's
    _advance_owner_priority_phases_safe, cmd_advance_owner_priority_phases
    below) must NOT wrap this call in their own _write_lock() -- doing so
    would (via _write_lock()'s own real reentrancy) collapse the two short
    sections back into one long one spanning the unlocked evidence loop,
    silently reintroducing this exact bug.

    Because the lock is released between the first read and the final
    write, a real concurrent writer (another process, or another call to
    this same function) could in principle commit a confirmed_complete_members
    update in between -- the final write section re-reads that column fresh
    from disk immediately before writing and unions it with this call's own
    newly-confirmed members, so such a write is merged, never clobbered."""
    now = now or _now_iso()

    with _write_lock():
        _ensure_owner_priority_tables(conn)
        seeded = seed_owner_priority_sequence(conn)  # no-op if already seeded
        active_row = conn.execute(
            "SELECT phase_order, phase_name, real_member_umrs, confirmed_complete_members "
            "FROM owner_priority_sequence WHERE status = 'active'"
        ).fetchone()
        result = {"seeded_this_call": seeded.get("seeded", False), "transitioned": False,
                  "evaluated_phase": None, "member_evidence": []}
        if not active_row:
            result["sync"] = _sync_owner_priority_override(conn, now=now)
            conn.commit()
            return result
        active = dict(active_row)

    members = json.loads(active["real_member_umrs"])
    try:
        confirmed = json.loads(active["confirmed_complete_members"] or "[]")
    except (json.JSONDecodeError, TypeError):
        confirmed = []
    confirmed_set = set(confirmed)
    result["evaluated_phase"] = active["phase_order"]

    to_check = [m for m in members if m not in confirmed_set]
    this_tick_batch = to_check[:OWNER_PRIORITY_PHASE_MAX_EVALUATIONS_PER_TICK]
    still_pending = set(to_check[OWNER_PRIORITY_PHASE_MAX_EVALUATIONS_PER_TICK:])

    # ---- real evidence-check loop: no _write_lock() held across this ----
    # This is the one part of the function that can shell out to real,
    # 60s-timeout git subprocess calls (via _umr_genuinely_completed ->
    # validate_umr_terminal_completion_evidence, for commit_sha-backed
    # members). Deliberately runs against `conn` with no write lock held
    # (only real, read-only SELECTs happen here against umr_tasks) so a
    # slow/degraded network during this loop never blocks any other
    # process's own write-path invocation of this script.
    member_evidence = []
    newly_confirmed = []
    for m in members:
        if m in confirmed_set:
            member_evidence.append({"umr_id": m, "genuinely_completed": True,
                                     "reason": "previously confirmed (memoized, not re-checked this tick)"})
        elif m in still_pending:
            member_evidence.append({"umr_id": m, "genuinely_completed": False,
                                     "reason": "not yet evaluated this tick (per-tick evaluation cap reached)"})
        else:
            ok, reason = _umr_genuinely_completed(conn, m, repos_root=repos_root)
            member_evidence.append({"umr_id": m, "genuinely_completed": ok, "reason": reason})
            if ok:
                newly_confirmed.append(m)
    result["member_evidence"] = member_evidence
    result["members_evaluated_this_tick"] = len(this_tick_batch)
    result["members_still_pending"] = sorted(still_pending)

    # ---- real writes only from here on: lock re-acquired, no further ----
    # subprocess calls happen inside this section.
    with _write_lock():
        if newly_confirmed:
            # Re-read the row's own confirmed_complete_members fresh (not
            # the stale copy read before the unlocked loop above)
            # immediately before writing, so a real concurrent writer that
            # committed while this call was shelling out to git is unioned
            # in, never clobbered.
            fresh_row = conn.execute(
                "SELECT confirmed_complete_members FROM owner_priority_sequence WHERE phase_order = ?",
                (active["phase_order"],),
            ).fetchone()
            try:
                fresh_confirmed = (json.loads(dict(fresh_row)["confirmed_complete_members"] or "[]")
                                    if fresh_row else [])
            except (json.JSONDecodeError, TypeError):
                fresh_confirmed = []
            confirmed_set = set(fresh_confirmed) | confirmed_set | set(newly_confirmed)
            conn.execute(
                "UPDATE owner_priority_sequence SET confirmed_complete_members = ?, updated_at = ? "
                "WHERE phase_order = ?",
                (json.dumps(sorted(confirmed_set)), now, active["phase_order"]),
            )

        if confirmed_set >= set(members):
            # Real invariant: only transition phase_order -> 'complete' if
            # it is still genuinely 'active' at write time -- a concurrent
            # writer could in principle have already advanced it (or this
            # phase could have been re-seeded away) between the unlocked
            # read above and this lock re-acquisition.
            still_active = conn.execute(
                "SELECT 1 FROM owner_priority_sequence WHERE phase_order = ? AND status = 'active'",
                (active["phase_order"],),
            ).fetchone()
            if still_active:
                conn.execute(
                    "UPDATE owner_priority_sequence SET status = 'complete', updated_at = ? "
                    "WHERE phase_order = ?",
                    (now, active["phase_order"]),
                )
                next_row = conn.execute(
                    "SELECT phase_order FROM owner_priority_sequence WHERE phase_order > ? AND status = 'pending' "
                    "ORDER BY phase_order LIMIT 1",
                    (active["phase_order"],),
                ).fetchone()
                if next_row:
                    next_order = dict(next_row)["phase_order"]
                    conn.execute(
                        "UPDATE owner_priority_sequence SET status = 'active', updated_at = ? WHERE phase_order = ?",
                        (now, next_order),
                    )
                    result["transitioned"] = True
                    result["new_active_phase"] = next_order

        result["sync"] = _sync_owner_priority_override(conn, now=now)
        conn.commit()
    return result


def cmd_seed_owner_priority_sequence(args):
    """Usage: python3 superboss-register.py seed-owner-priority-sequence [--force]"""
    init_db_silent()
    conn = _connect()
    _ensure_umr_table(conn)
    _ensure_ocid_canonical_registry_table(conn)
    with _write_lock():
        result = seed_owner_priority_sequence(conn, force=args.force)
    conn.close()
    print(json.dumps(result, indent=2, default=str))


def cmd_advance_owner_priority_phases(args):
    """Usage: python3 superboss-register.py advance-owner-priority-phases

    Deliberately does NOT wrap advance_owner_priority_phases() in its own
    _write_lock() (round 2 of the PR #256 review.json finding): that
    function now acquires the lock itself, only around its real reads/
    writes, and deliberately releases it across its own real
    evidence-check loop (which can shell out to real, 60s-timeout git
    subprocess calls for commit_sha-backed members). Wrapping the whole
    call in an outer _write_lock() here would, via _write_lock()'s own
    real reentrancy, collapse those two short critical sections back into
    one long one spanning the unlocked loop -- silently reintroducing the
    exact bug that fix exists to prevent."""
    init_db_silent()
    conn = _connect()
    _ensure_umr_table(conn)
    _ensure_ocid_canonical_registry_table(conn)
    result = advance_owner_priority_phases(conn)
    conn.close()
    print(json.dumps(result, indent=2, default=str))


def cmd_show_owner_priority_state(args):
    """Usage: python3 superboss-register.py show-owner-priority-state"""
    init_db_silent()
    conn = _connect()
    _ensure_owner_priority_tables(conn)
    phases = [dict(r) for r in conn.execute(
        "SELECT * FROM owner_priority_sequence ORDER BY phase_order").fetchall()]
    for p in phases:
        p["real_member_umrs"] = json.loads(p["real_member_umrs"])
    override = [dict(r) for r in conn.execute(
        "SELECT * FROM owner_priority_override ORDER BY umr_id").fetchall()]
    conn.close()
    print(json.dumps({"owner_priority_sequence": phases, "owner_priority_override": override},
                      indent=2, default=str))


# Real, env-overridable defaults (same convention as DB_PATH's own
# SUPERBOSS_REGISTER_DB env override above) -- tests point these at real
# scratch directories/repos instead of the real, live
# /opt/veridian/ai-os/external_agent and this real repo checkout.
EXTERNAL_AGENT_ARTIFACTS_ROOT = os.environ.get("EXTERNAL_AGENT_ARTIFACTS_ROOT", "/opt/veridian/ai-os/external_agent")
EXTERNAL_AGENT_REPO_ROOT = os.environ.get("EXTERNAL_AGENT_REPO_ROOT", os.path.dirname(os.path.abspath(__file__)))


def init_db_silent():
    if not os.path.exists(DB_PATH):
        conn = _connect()
        conn.close()
    conn = _connect()
    conn.execute("SELECT 1 FROM instructions LIMIT 1")
    _migrate_schema(conn)
    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init")

    p_vacuum = sub.add_parser("vacuum-compact",
                               help="Part A of task-20260815-051128-prevent-register-corruption-"
                                    "recurrence: compact the live DB via VACUUM INTO a validated "
                                    "temp file, then atomically swap it in (see "
                                    "atomic_replace_live_db()'s docstring) -- never writes into "
                                    "the live file path directly")

    p_hb = sub.add_parser("heartbeat", help="Stage 3 reconciliation-sweep prerequisite: stamp "
                           "last_heartbeat=now() on the active umr_tasks row for --task-identity")
    p_hb.add_argument("--task-identity", dest="task_identity", required=True)

    p_ins = sub.add_parser("log-instruction")
    p_ins.add_argument("--text", required=True)
    p_ins.add_argument("--source", default="owner")
    p_ins.add_argument("--medium", default="ssh_session")
    p_ins.add_argument("--campaign", default="")
    p_ins.add_argument("--content", default="")
    p_ins.add_argument("--term", default="")
    p_ins.add_argument("--session-id", dest="session_id", default="")
    p_ins.add_argument("--metadata", default="")
    p_ins.add_argument("--response-summary", dest="response_summary", default="")

    p_work = sub.add_parser("log-work")
    p_work.add_argument("--instruction-id", dest="instruction_id", default=None)
    p_work.add_argument("--software-task-id", dest="software_task_id", default=None)
    p_work.add_argument("--ai-task-id", dest="ai_task_id", default=None)
    p_work.add_argument("--cache-id", dest="cache_id", default=None)
    p_work.add_argument("--ai-cache-id", dest="ai_cache_id", default=None)
    p_work.add_argument("--source", default="ai_agent")
    p_work.add_argument("--medium", default="claude_code_cli")
    p_work.add_argument("--campaign", default="")
    p_work.add_argument("--content", default="")
    p_work.add_argument("--term", default="")
    p_work.add_argument("--status", default="open")
    p_work.add_argument("--metadata", default="")
    p_work.add_argument("--ts", default=None, help="ISO8601 override for historical imports; defaults to now")

    p_act = sub.add_parser("log-action")
    p_act.add_argument("--work-item-id", dest="work_item_id", default=None)
    p_act.add_argument("--instruction-id", dest="instruction_id", default=None)
    p_act.add_argument("--source", default="ai_agent")
    p_act.add_argument("--medium", default="claude_code_cli")
    p_act.add_argument("--campaign", default="")
    p_act.add_argument("--content", required=True)
    p_act.add_argument("--term", default="")
    p_act.add_argument("--result", default="")
    p_act.add_argument("--metadata", default="")

    p_search = sub.add_parser("search")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument("--tag", default=None, help="filter system_index results to rows whose tags list contains this exact tag")

    p_idx = sub.add_parser("index-add")
    p_idx.add_argument("--path", required=True, help="file/table/mechanism location, e.g. src/lib/task-tightening.ts")
    p_idx.add_argument("--category", required=True, help="cache|validation|guardrail|router|monitor|task_register|hallucination_detection|confidence_scoring|dispatch_entrypoint|classification|other")
    p_idx.add_argument("--layer", required=True, help="shell|typescript|database|documentation")
    p_idx.add_argument("--status", required=True, help="live|partial|dead|deprecated|designed_not_built")
    p_idx.add_argument("--purpose", required=True)
    p_idx.add_argument("--term", default="")
    p_idx.add_argument("--calls", default="")
    p_idx.add_argument("--called-by", dest="called_by", default="")
    p_idx.add_argument("--tags", default="", help="comma-separated list, e.g. module:dispatch,priority:high -- stored as a JSON-encoded list")
    p_idx.add_argument("--metadata", default="")

    p_dup = sub.add_parser("check-duplicate")
    p_dup.add_argument("query", nargs="?", default="")
    p_dup.add_argument("--category", default="")

    p_getsc = sub.add_parser("get-search-cache",
                              help="task-20260814-181008: real short-TTL cache lookup for "
                                   "task-gateway.py cmd_submit's check-duplicate/search/"
                                   "query-knowledge/zoekt search step -- returns "
                                   "{hit, result, age_seconds, cache_key}; hit=False on a "
                                   "miss or an expired (> --ttl-seconds, default "
                                   "SEARCH_CACHE_TTL_SECONDS) row.")
    p_getsc.add_argument("--query-text", dest="query_text", required=True)
    p_getsc.add_argument("--ttl-seconds", dest="ttl_seconds", type=float, default=None)

    p_putsc = sub.add_parser("put-search-cache",
                              help="task-20260814-181008: real upsert of a search_cache row "
                                   "-- --result-json is the combined check-duplicate/search/"
                                   "query-knowledge/zoekt result dict cmd_submit just "
                                   "computed live, stored verbatim for the next matching "
                                   "get-search-cache lookup within TTL.")
    p_putsc.add_argument("--query-text", dest="query_text", required=True)
    p_putsc.add_argument("--result-json", dest="result_json", required=True)

    p_cdup = sub.add_parser("check-content-duplicate", help="Stage 2 (task-20260729): "
                             "content-hash dedup for same-text chat resubmission -- has "
                             "this exact instruction text already been submitted recently.")
    p_cdup.add_argument("--text", required=True)
    p_cdup.add_argument("--window-hours", dest="window_hours", type=float, default=24)

    p_tidup = sub.add_parser(
        "check-target-identifier-duplicate",
        help="Addendum to UMR-20260813-102459-10c3: deterministic (not fuzzy, not "
             "hash-exact) dedup -- does a queued/running umr_tasks row from the last "
             "--window-hours already target the exact same PR number+repo, file path, "
             "or script name as (--title, --prompt)? Real fix for the incident where "
             "--search (FTS5, fuzzy) missed an exact recent duplicate whose wording "
             "differed from the first dispatch.")
    p_tidup.add_argument("--title", required=True)
    p_tidup.add_argument("--prompt", required=True)
    p_tidup.add_argument("--repo", default=None,
                          help="target repo, used both to scope a bare 'PR #N' mention "
                               "in --title/--prompt and as the fallback repo for rows "
                               "whose own inputs_json has none")
    p_tidup.add_argument("--window-hours", dest="window_hours", type=float, default=4)
    p_tidup.add_argument("--limit", type=int, default=30)

    p_exec = sub.add_parser("log-execution")
    p_exec.add_argument("--phase", required=True, choices=["PRE", "POST"])
    p_exec.add_argument("--work-item-id", dest="work_item_id", default=None)
    p_exec.add_argument("--software-task-id", dest="software_task_id", default=None)
    p_exec.add_argument("--source-script", dest="source_script", required=True,
                         help="e.g. ai-os/scripts/session_bootstrap.py or ai-os/scripts/postflight_audit_gate.py")
    p_exec.add_argument("--fields-file", dest="fields_file", required=True,
                         help="path to a JSON file: {field_name: {\"status\": \"YES\"|\"NO\", \"evidence\": \"...\"}}")
    p_exec.add_argument("--metadata", default="")

    p_idxt = sub.add_parser("index-transcript")
    p_idxt.add_argument("--file", required=True, help="path to the raw laptop-chat transcript JSONL")
    p_idxt.add_argument("--session-id", dest="session_id", required=True)

    p_login = sub.add_parser("log-login")
    p_login.add_argument("--user", required=True, help="linux username")
    p_login.add_argument("--source-ip", dest="source_ip", required=True)
    p_login.add_argument("--method", required=True, help="publickey|password")

    p_fix = sub.add_parser("log-fix")
    p_fix.add_argument("--signature", required=True, help="first 60 chars of the stalled/looping checkpoint note")
    p_fix.add_argument("--fix-action", dest="fix_action", required=True,
                        help="name of a known, whitelisted recovery action (see veridian-task-watchdog.py's FIX_ACTIONS registry)")

    p_regk = sub.add_parser("register-knowledge")
    p_regk.add_argument("--path", required=True, help="real, absolute artifact_path -- exists_on_disk is detected live, not asserted")
    p_regk.add_argument("--artifact-type", dest="artifact_type", required=True, choices=["canonical", "derived"])
    p_regk.add_argument("--purpose", required=True, help="one line, sourced from the artifact's own self-declared header/meta, never guessed from filename")
    p_regk.add_argument("--tags", default="", help="comma-separated list, stored as a JSON-encoded list (same convention as index-add)")
    p_regk.add_argument("--relationships", default="[]",
                        help='JSON list of {"path": "<real path>", "relationship_type": "<free text>", "evidence": "<optional file:line/quote>"}')
    p_regk.add_argument("--secondary-path", dest="secondary_path", default=None,
                         help="nullable -- only for artifacts with a real dual-location precedent (e.g. MASTER_INDEX.yaml)")
    p_regk.add_argument("--metadata", default="")
    # UTM metadata consolidation, phase 6 (2026-07-30): optional, same
    # interface convention as log-instruction's --source/--medium/--campaign/
    # --content/--term. utm_term is NOT listed here -- it's auto-derived
    # from --tags (comma-join), never asked for separately.
    p_regk.add_argument("--utm-source", dest="utm_source", default=None,
                         help="who: owner|end_user|org|ai_agent|software (optional, left NULL if unknown)")
    p_regk.add_argument("--utm-medium", dest="utm_medium", default=None,
                         help="channel: ssh_session|claude_code_cli|chat_ui|api|cron (optional)")
    p_regk.add_argument("--utm-campaign", dest="utm_campaign", default=None,
                         help="initiative/project grouping, freeform slug (optional)")
    p_regk.add_argument("--utm-content", dest="utm_content", default=None,
                         help="short structured label of what, not a sentence (optional)")

    p_queryk = sub.add_parser("query-knowledge")
    p_queryk.add_argument("query")
    p_queryk.add_argument("--tag", default=None, help="filter results to rows whose tags list contains this exact tag")

    p_verifyk = sub.add_parser("verify-knowledge")
    p_verifyk.add_argument("--path", required=True, action="append",
                            help="artifact_path to re-verify against a live file read; repeatable")

    p_annotk = sub.add_parser("annotate-knowledge")
    p_annotk.add_argument("--path", required=True)
    p_annotk.add_argument("--note", required=True, help="dated correction/decision note appended to metadata_json.corrections")

    p_relk = sub.add_parser("add-relationship")
    p_relk.add_argument("--path", required=True)
    p_relk.add_argument("--related-path", dest="related_path", required=True)
    p_relk.add_argument("--relationship-type", dest="relationship_type", required=True)
    p_relk.add_argument("--evidence", default=None)

    p_tagk = sub.add_parser("add-tag")
    p_tagk.add_argument("--path", required=True)
    p_tagk.add_argument("--tag", required=True)

    p_upsertf = sub.add_parser("upsert-knowledge-fragment")
    p_upsertf.add_argument("--path", required=True, help="stable virtual identifier, e.g. "
                            "'ai-os/MASTER_INDEX.yaml#registries.<id>'")
    p_upsertf.add_argument("--content", required=True, help="canonical text for just this fragment; its "
                            "sha256 becomes content_hash")
    p_upsertf.add_argument("--artifact-type", dest="artifact_type", default="derived", choices=["canonical", "derived"])
    p_upsertf.add_argument("--secondary-path", dest="secondary_path", default=None,
                            help="the real file this fragment lives inside")
    p_upsertf.add_argument("--purpose", required=True)
    p_upsertf.add_argument("--tags", default=None)
    p_upsertf.add_argument("--metadata", default=None)

    p_listk = sub.add_parser("list-knowledge")
    p_listk.add_argument("--tag", default=None)

    p_regc = sub.add_parser("register-capability")
    p_regc.add_argument("--record-file", dest="record_file", required=True,
                         help="path to a JSON file matching CAPABILITY_REGISTRY_SCHEMA_2026-07-24.yaml's capability_record_schema")

    p_lookc = sub.add_parser("lookup-capability")
    p_lookc.add_argument("--capability-name", dest="capability_name", default=None)
    p_lookc.add_argument("--intent-text", dest="intent_text", default=None)
    p_lookc.add_argument("--domain", default=None)

    p_listc = sub.add_parser("list-capabilities")

    p_precedent = sub.add_parser("search-task-precedent",
                                  help="steps one+two of the required deterministic-first sequence: exact "
                                       "capability_registry script match, then real cross-history precedent "
                                       "search over umr_tasks/capability_graduation_log")
    p_precedent.add_argument("--task-text", dest="task_text", required=True,
                              help="task title/description to search for a matching script or past precedent")
    p_precedent.add_argument("--limit", type=int, default=10)

    p_regg = sub.add_parser("record-graduation",
                             help="step four: mandatory post-AI-work evaluation, can this become a permanent "
                                  "deterministic script -- recorded either way, never skipped")
    p_regg.add_argument("--umr-id", dest="umr_id", required=True)
    p_regg.add_argument("--agent-id", dest="agent_id", required=True)
    p_regg.add_argument("--task-summary", dest="task_summary", required=True)
    p_regg.add_argument("--decision", required=True, choices=["graduated", "judgment_required"])
    p_regg.add_argument("--reason", required=True)
    p_regg.add_argument("--capability-id", dest="capability_id", default=None,
                         help="required when --decision=graduated; the capability_id register-capability just returned")
    p_regg.add_argument("--script-path", dest="script_path", default=None,
                         help="required when --decision=graduated; repo-relative path of the new deterministic script")
    p_regg.add_argument("--metadata", default=None)

    p_listg = sub.add_parser("list-graduations")
    p_listg.add_argument("--umr-id", dest="umr_id", default=None)

    p_capr = sub.add_parser("capture-replay")
    p_capr.add_argument("--route-id", dest="route_id", required=True)
    p_capr.add_argument("--capability-name", dest="capability_name", required=True)
    p_capr.add_argument("--request-payload", dest="request_payload", required=True,
                         help="real JSON call args for this route's dispatch-target function")
    p_capr.add_argument("--response-payload", dest="response_payload", required=True,
                         help="real JSON return value from actually executing that function")
    p_capr.add_argument("--artifact-path", dest="artifact_path", required=True,
                         help="evidence directory this capture's raw payloads/output were written to")
    p_capr.add_argument("--metadata", default=None)

    p_runr = sub.add_parser("run-replay")
    p_runr.add_argument("--route-id", dest="route_id", required=True)
    p_runr.add_argument("--capability-name", dest="capability_name", required=True)
    p_runr.add_argument("--request-payload", dest="request_payload", required=True)
    p_runr.add_argument("--response-payload", dest="response_payload", required=True,
                         help="real JSON return value from re-executing the function against current code")
    p_runr.add_argument("--artifact-path", dest="artifact_path", required=True)
    p_runr.add_argument("--diff-detail", dest="diff_detail", default=None,
                         help="override the auto-generated diff_detail message")
    p_runr.add_argument("--metadata", default=None)

    p_listr = sub.add_parser("list-replays")
    p_listr.add_argument("--route-id", dest="route_id", default=None)

    p_rege = sub.add_parser("register-entity")
    p_rege.add_argument("--record-file", dest="record_file", required=True,
                         help="path to a JSON file matching WIRING_ENGINE_SCHEMA_2026-07-25.yaml's entity_record_schema")

    p_looke = sub.add_parser("lookup-entity")
    p_looke.add_argument("--entity-id", dest="entity_id", default=None)
    p_looke.add_argument("--query", default=None, help="keyword query over path/entity_type/source_ref")

    p_liste = sub.add_parser("list-entities")
    p_liste.add_argument("--entity-type", dest="entity_type", default=None,
                          help="engine|gateway|supabase_table|function|route|file|script|cron_job|"
                               "ai_role|vercel_project|github_repo|browser_component")

    # Structural duplicate-task lease (task-20260731-074406) -- see
    # _ensure_task_claims_table's docstring for task_key vs task_id.
    p_claimtk = sub.add_parser("claim-task-key")
    p_claimtk.add_argument("--task-key", dest="task_key", required=True)
    p_claimtk.add_argument("--task-id", dest="task_id", default=None)
    p_claimtk.add_argument("--title", default=None)
    p_claimtk.add_argument("--source", default=None, help="who's claiming: owner|ai_agent|software (optional)")

    p_checktk = sub.add_parser("check-task-key")
    p_checktk.add_argument("--task-key", dest="task_key", required=True)

    p_qocidc = sub.add_parser("query-ocid-canonical")
    p_qocidc.add_argument("--ocid-number", dest="ocid_number", default=None,
                           help="e.g. OCID-038; omit for the whole real roster")

    p_resolvec = sub.add_parser("resolve-ocid-canonical",
                                 help="OCID Master Standard v6 Phase 1 (UMR-20260805-042152-e559): "
                                      "run the real, canonical methods a-f OCID->UMR resolution")
    p_resolvec.add_argument("--ocid-number", dest="ocid_number", required=True)
    p_resolvec.add_argument("--apply", action="store_true",
                             help="also write the real result into ocid_canonical_registry "
                                  "(default: read-only report)")

    p_reconc = sub.add_parser("reconcile-umr-status",
                               help="OCID Master Standard v6 Phase 1: cross-check a real UMR's "
                                    "status/ts_completed against real PR-merge evidence")
    p_reconc.add_argument("--umr-id", dest="umr_id", required=True)
    p_reconc.add_argument("--apply", action="store_true",
                           help="also apply the proposed correction via update_umr_task() "
                                "(default: read-only report, never silently applied)")

    p_certify = sub.add_parser("certify-pr-merge",
                                help="OCID Master Standard v6 Phase 1: real, offline certification "
                                     "verdict against an explicit pr_merge_record JSON file")
    p_certify.add_argument("--pr-record-json", dest="pr_record_json", required=True)

    p_inspm = sub.add_parser("insert-pm-decision-pending",
                              help="Deterministic PM Reporting Contract V3 (UMR-20260805-181636-32f2): "
                                   "open one real PM decision row for generate_pm_report_v3.py to surface")
    p_inspm.add_argument("--title", required=True)
    p_inspm.add_argument("--detail", required=True)
    p_inspm.add_argument("--options-json", dest="options_json", default=None,
                          help="path to a real JSON file holding the options list "
                               "(same shape as pm_decisions_pending.options_json)")
    p_inspm.add_argument("--recommended-option", dest="recommended_option", default=None)
    p_inspm.add_argument("--related-umr", dest="related_umr", default=None)

    p_respm = sub.add_parser("resolve-pm-decision-pending",
                              help="Deterministic PM Reporting Contract V3: close one real, "
                                   "currently-open pm_decisions_pending row")
    p_respm.add_argument("--id", dest="decision_id", type=int, required=True)
    p_respm.add_argument("--closed-by", dest="closed_by", required=True)
    p_respm.add_argument("--closed-note", dest="closed_note", default=None)
    p_respm.add_argument("--status", default="resolved",
                          help="terminal status to record (default: resolved)")

    p_updpm = sub.add_parser("update-pm-decision-pending",
                              help="UMR-20260806-163738-4323: update one real, currently-open "
                                   "pm_decisions_pending row IN PLACE (e.g. refresh an aggregate "
                                   "condition row's count/detail as the real condition changes) "
                                   "rather than opening a new row per occurrence")
    p_updpm.add_argument("--id", dest="decision_id", type=int, required=True)
    p_updpm.add_argument("--title", default=None)
    p_updpm.add_argument("--detail", default=None)
    p_updpm.add_argument("--related-umr", dest="related_umr", default=None)
    p_updpm.add_argument("--recommended-option", dest="recommended_option", default=None)

    p_insprop = sub.add_parser("insert-owner-proposal",
                                help="Owner standing mandate (task-20260806-034817, cites "
                                     "UMR-20260805-185000-e94f): deposit one real child-UMR "
                                     "proposal for real novel findings outside already-approved "
                                     "scope -- AI states the issue and what it proposes, nothing "
                                     "implemented yet")
    p_insprop.add_argument("--issue", required=True, help="exactly what the real issue is")
    p_insprop.add_argument("--proposal", required=True, help="exactly what AI proposes")
    p_insprop.add_argument("--child-umr", dest="child_umr", default=None,
                            help="real child UMR id (minted via _new_id('UMR') if omitted)")

    p_decprop = sub.add_parser("decide-owner-proposal",
                                help="Owner standing mandate: PM approves, redirects, or holds "
                                     "one real, currently-open owner-proposal row, citing its "
                                     "child UMR")
    p_decprop.add_argument("--id", dest="decision_id", type=int, required=True)
    p_decprop.add_argument("--decision", required=True, choices=list(_OWNER_PROPOSAL_DECISIONS))
    p_decprop.add_argument("--closed-by", dest="closed_by", required=True)
    p_decprop.add_argument("--closed-note", dest="closed_note", default=None)

    p_compprop = sub.add_parser("record-owner-proposal-completion",
                                 help="Owner standing mandate: AI records real completion "
                                      "(artifact, file path, commit, evidence) back onto the "
                                      "same real, PM-approved child-UMR proposal row")
    p_compprop.add_argument("--id", dest="decision_id", type=int, required=True)
    p_compprop.add_argument("--artifact-path", dest="artifact_path", required=True)
    p_compprop.add_argument("--commit-sha", dest="commit_sha", required=True)
    p_compprop.add_argument("--evidence", required=True)

    p_markdisp = sub.add_parser("mark-umr-dispatched",
                                 help="UMR-20260806-085144-9c63: write a real ts_dispatched + "
                                      "status='dispatched' onto a just-minted umr_tasks row, "
                                      "called by dispatch-owner-task.sh right after its real "
                                      "tmux relay succeeds")
    p_markdisp.add_argument("--umr-id", dest="umr_id", required=True)
    p_markdisp.add_argument("--unit-name", dest="unit_name", default=None)

    p_markrelay = sub.add_parser("mark-umr-relay-attempted",
                                  help="UMR-20260806-115423-500d: record a real, honest "
                                       "'a tmux relay was attempted' courtesy signal onto a "
                                       "umr_tasks row -- never touches status/ts_dispatched/"
                                       "ts_completed, so the row stays fully eligible for "
                                       "dispatch-tick.py's own real mechanical pickup regardless "
                                       "of what this records")
    p_markrelay.add_argument("--umr-id", dest="umr_id", required=True)
    p_markrelay.add_argument("--outcome", required=True, choices=["sent", "session_not_found"])
    p_markrelay.add_argument("--detail", default=None)

    p_reqlock = sub.add_parser("requeue-build-lock-contended",
                                help="UMR-20260806-123316-cf9f: reset a task's OWN existing "
                                     "umr_tasks row back to status='queued' (reason="
                                     "'build_lock_contended') after quality-gate.sh's build "
                                     "step failed to acquire the host-wide build lock within "
                                     "its short wait -- called only by quality-gate.sh itself, "
                                     "never mints a new row")
    p_reqlock.add_argument("--task-identity", dest="task_identity", required=True)
    p_reqlock.add_argument("--unit-name", dest="unit_name", required=True)

    p_markterm = sub.add_parser("mark-umr-terminal",
                                 help="UMR-20260806-085144-9c63, structurally extended by "
                                      "UMR-20260806-130914-e7f1: write a real ts_completed + "
                                      "terminal status onto a umr_tasks row -- used both by "
                                      "dispatch-owner-task.sh's tmux-relay-failure branch "
                                      "(--status failed) and by a worker/interactive session "
                                      "recording genuine completion. --status completed/"
                                      "completed_unmerged now REQUIRE real structured evidence "
                                      "(--commit-sha real-and-an-ancestor-of-origin/main, or a "
                                      "real --file-path) -- see cmd_mark_umr_terminal's own "
                                      "docstring")
    p_markterm.add_argument("--umr-id", dest="umr_id", required=True)
    p_markterm.add_argument("--status", required=True,
                             choices=["completed", "completed_unmerged", "failed", "killed"])
    p_markterm.add_argument("--reason", default=None)
    p_markterm.add_argument("--commit-sha", dest="commit_sha", default=None,
                             help="real commit SHA -- required (with/without --file-path) for "
                                  "--status completed (must be a real ancestor of origin/main) "
                                  "or completed_unmerged (must be real but NOT yet an ancestor)")
    p_markterm.add_argument("--file-path", dest="file_path", default=None,
                             help="real file path that must genuinely exist on disk (absolute, "
                                  "or resolved against --repo-root/--repo) -- an alternative real "
                                  "artifact to --commit-sha for --status completed")
    p_markterm.add_argument("--pr-number", dest="pr_number", type=int, default=None,
                             help="real PR number, recorded onto outputs_json for traceability "
                                  "(not itself verified live -- --commit-sha is the real gate)")
    p_markterm.add_argument("--repo", default="veridian-scripts",
                             choices=list(DEFAULT_OCID_RESOLVER_REPO_LOCAL_PATHS),
                             help="which real local repo checkout to verify --commit-sha/--file-path "
                                  "against (default: veridian-scripts)")
    p_markterm.add_argument("--repo-root", dest="repo_root", default=None,
                             help="override the local repo checkout path used for the real "
                                  "commit-ancestor/file-exists check (default: derived from --repo)")

    p_gtmupd = sub.add_parser("update-gtm-category",
                               help="UMR-20260806-114728-d469 (ported to current main under "
                                    "UMR-20260806-161614-5850): partial UPDATE of one "
                                    "gtm_certification_categories row's child_umr_id/"
                                    "fix_commit/fix_file_path/fix_pr_number ONLY -- never "
                                    "evidence_json/evidence_summary/passed/validated_at")
    p_gtmupd.add_argument("--category-index", dest="category_index", type=int, required=True)
    p_gtmupd.add_argument("--child-umr-id", dest="child_umr_id", default=None)
    p_gtmupd.add_argument("--fix-commit", dest="fix_commit", default=None)
    p_gtmupd.add_argument("--fix-file-path", dest="fix_file_path", default=None)
    p_gtmupd.add_argument("--fix-pr-number", dest="fix_pr_number", type=int, default=None)

    sub.add_parser("list-gtm-categories",
                    help="2026-08-15 Owner directive (Part3+4 GTM-certification "
                         "completion tracking, governing UMR UMR-20260815-044235-a5e1): "
                         "real, read-only listing of every gtm_certification_categories "
                         "row -- the one real query pm-sentinel-tick.sh's Check 4 makes "
                         "live every tick")

    p_gtmcert = sub.add_parser("record-gtm-part3-4-certificate",
                                help="2026-08-15 Owner directive (governing UMR "
                                     "UMR-20260815-044235-a5e1): the one real, canonical, "
                                     "idempotent write path for the Part3+4 GTM-"
                                     "certification completion certificate -- refuses "
                                     "(raises) unless --evidence-json proves every cited "
                                     "category real passed=1 with real non-empty, "
                                     "non-placeholder evidence_summary")
    p_gtmcert.add_argument("--evidence-json", dest="evidence_json", required=True,
                            help="JSON object with a 'categories' key holding the real, "
                                 "live gtm_certification_categories rows this tick already "
                                 "queried via list-gtm-categories -- never fabricated")
    p_gtmcert.add_argument("--umr-id", dest="umr_id", default=None,
                            help="real governing UMR id to cite on the certificate record "
                                 "(optional -- e.g. this tick's own dispatching UMR)")

    p_resetq = sub.add_parser("reset-umr-to-queued",
                               help="UMR-20260806-115605-854d: reset a real umr_tasks row from "
                                    "status='dispatched' back to 'queued' (clears ts_dispatched) -- "
                                    "the one real, canonical write path reconcile_dispatched_dead_zone.py's "
                                    "own auto-remediation uses, never a raw SQL UPDATE")
    p_resetq.add_argument("--umr-id", dest="umr_id", required=True)
    p_resetq.add_argument("--reason", required=True)

    # UMR-20260808-074726-d105 (governing chain UMR-20260806-171945-5767):
    # the one real, permanent, callable write path into master_issue_tracker
    # -- see the section comment above add_master_issue() for full context.
    p_addissue = sub.add_parser("add-issue",
                                 help="add a real row to master_issue_tracker -- the mandatory real "
                                      "mechanism any AI agent or deterministic script must use to "
                                      "record a real issue found (never a chat message, a one-off "
                                      "file, or nowhere at all)")
    p_addissue.add_argument("--issue-id", dest="issue_id", required=True)
    p_addissue.add_argument("--issue-identified", dest="issue_identified", required=True)
    p_addissue.add_argument("--linked-ocid", dest="linked_ocid", default=None)
    p_addissue.add_argument("--linked-umr-id", dest="linked_umr_id", default=None)
    p_addissue.add_argument("--linked-source", dest="linked_source", default=None)
    p_addissue.add_argument("--file-name", dest="file_name", default=None)
    p_addissue.add_argument("--file-path", dest="file_path", default=None)
    p_addissue.add_argument("--existing-solution", dest="existing_solution", default=None)

    p_closeissue = sub.add_parser("close-issue",
                                   help="mark a master_issue_tracker row issue_resolved_permanently="
                                        "YES and is_closed=YES -- only if --resolution-notes is real "
                                        "and non-empty")
    p_closeissue.add_argument("--issue-id", dest="issue_id", required=True)
    p_closeissue.add_argument("--resolution-notes", dest="resolution_notes", required=True)

    p_updissue = sub.add_parser("update-issue",
                                 help="partial UPDATE of any real, mutable master_issue_tracker "
                                      "column, repeatable --field NAME=VALUE")
    p_updissue.add_argument("--issue-id", dest="issue_id", required=True)
    p_updissue.add_argument("--field", action="append", default=[],
                             help="NAME=VALUE, repeatable, e.g. --field audit_notes=\"...\"")

    p_listissue = sub.add_parser("list-issues",
                                  help="list/filter master_issue_tracker rows, JSON output matching "
                                       "--query-umr's own {count, matches} convention")
    p_listissue.add_argument("--linked-ocid", dest="linked_ocid", default=None)
    # Added 2026-08-08 (addendum to UMR-20260808-122929-bc77, governing chain
    # UMR-20260806-171945-5767): the linked_umr_id column has been real and
    # queryable via raw SQL since this table's own creation, but this CLI's
    # own list-issues subcommand -- the ONE sanctioned, non-raw-SQL read path
    # every other real caller of this table already uses -- had no way to
    # filter by it, only --linked-ocid. That blocked the addendum's own real
    # boolean test ("list-issues --linked-umr-id UMR-20260806-171945-5767
    # shows the real, current boolean result for each of the 12 points"),
    # which needs exactly this filter to make master_issue_tracker itself
    # the live, queryable record of a UMR-scoped point set, the same real
    # pattern --linked-ocid already established for OCID-scoped ones.
    p_listissue.add_argument("--linked-umr-id", dest="linked_umr_id", default=None)
    p_listissue.add_argument("--is-closed", dest="is_closed", default=None, choices=["YES", "NO"])
    p_listissue.add_argument("--limit", type=int, default=50)

    p_logevent = sub.add_parser("log-governance-event",
                                 help="record a real governance-cycle event (query/memory_check/"
                                      "audit_performed) into governance_cycle_log -- task-gateway.py "
                                      "audit-24-points Points 2/8/9")
    p_logevent.add_argument("--event-type", dest="event_type", required=True)
    p_logevent.add_argument("--caller", default=None)
    p_logevent.add_argument("--detail", default=None)

    p_listevents = sub.add_parser("list-governance-events",
                                   help="list/filter governance_cycle_log rows, JSON output matching "
                                        "list-issues' own {count, matches} convention")
    p_listevents.add_argument("--event-type", dest="event_type", default=None)
    p_listevents.add_argument("--limit", type=int, default=50)

    # Real Owner directive UMR-20260806-095416-b6f0: fourth real worker
    # channel, a fully manual human-paste bridge to chat.z.ai. NEVER any
    # browser automation against chat.z.ai (hard ToS constraint).
    p_eaelig = sub.add_parser("mark-external-agent-eligible",
                               help="UMR-20260806-095416-b6f0: mark one real umr_tasks row "
                                    "external_agent_eligible=1 via the real eligibility function "
                                    "-- refuses (raises) if the row does not really pass every rule")
    p_eaelig.add_argument("--umr-id", dest="umr_id", required=True)
    p_eaelig.add_argument("--task-type", dest="task_type", required=True,
                           choices=list(EXTERNAL_AGENT_ALLOWED_TASK_TYPES))
    p_eaelig.add_argument("--blast-radius", dest="blast_radius", required=True)
    p_eaelig.add_argument("--files-touched-json", dest="files_touched_json", required=True,
                           help="JSON array of exact repo-relative paths, e.g. '[\"README.md\"]'")
    p_eaelig.add_argument("--acceptance-criteria", dest="acceptance_criteria", required=True)
    p_eaelig.add_argument("--repro-steps", dest="repro_steps", default=None,
                           help="required (by the real eligibility function) for task-type isolated_bugfix")
    p_eaelig.add_argument("--requires-multi-file-context", dest="requires_multi_file_context",
                           action="store_true", default=False)

    p_eanext = sub.add_parser("get-next-external-agent-task",
                               help="UMR-20260806-095416-b6f0: select+dispatch one real eligible "
                                    "task to the chat.z.ai manual-paste bridge, print the real "
                                    "prompt for the Owner to copy out by hand")
    p_eanext.add_argument("--artifacts-root", dest="artifacts_root", default=None)
    p_eanext.add_argument("--repo-root", dest="repo_root", default=None)

    p_easubmit = sub.add_parser("submit-external-agent-result",
                                 help="UMR-20260806-095416-b6f0: parse the Owner's real pasted-back "
                                      "chat.z.ai reply, apply to a fresh worktree (never main), run "
                                      "the real unmodified quality-gate.sh, open a real PR (never "
                                      "auto-merged) on success, real two-strike reject/requeue/"
                                      "fallback otherwise")
    p_easubmit.add_argument("--reply-file", dest="reply_file", required=True,
                             help="path to a real file holding exactly what the Owner pasted back, verbatim")
    p_easubmit.add_argument("--reviewed-by", dest="reviewed_by", default=None)
    p_easubmit.add_argument("--no-push", dest="no_push", action="store_true", default=False,
                             help="skip the real 'git push' step (real tests only)")
    p_easubmit.add_argument("--artifacts-root", dest="artifacts_root", default=None)
    p_easubmit.add_argument("--repo-root", dest="repo_root", default=None)

    p_eaexpire = sub.add_parser("expire-external-agent-dispatches",
                                 help="UMR-20260806-095416-b6f0: pure bookkeeping -- mark every real "
                                      "external_agent_dispatch row past its real 24h expires_at as "
                                      "expired and apply the real two-strike rule; idempotent, "
                                      "safe on a real cron/systemd-timer schedule")

    p_opseed = sub.add_parser("seed-owner-priority-sequence",
                               help="Amendment to UMR-20260807-070110-5ea7 (governed by "
                                    "UMR-20260806-124055-bc80): seed the real 4-phase "
                                    "owner_priority_sequence table (no-op if already seeded)")
    p_opseed.add_argument("--force", action="store_true", default=False,
                           help="test-only: delete+re-seed even if rows already exist")

    p_opadvance = sub.add_parser("advance-owner-priority-phases",
                                  help="Real deterministic phase-advance check + "
                                       "owner_priority_override resync -- run every tick before "
                                       "next_queued_task (resource_governor.py's run_tick() calls "
                                       "this automatically; this CLI entry is for manual/test use)")

    p_opshow = sub.add_parser("show-owner-priority-state",
                               help="Print the real current owner_priority_sequence + "
                                    "owner_priority_override table contents")

    args = p.parse_args()
    if args.cmd == "init":
        with _write_lock():
            init_db()
    elif args.cmd == "vacuum-compact":
        result_path = vacuum_compact_db()
        print(json.dumps({"ok": True, "db": result_path}))
    elif args.cmd == "heartbeat":
        with _write_lock():
            touch_umr_heartbeat(args)
    elif args.cmd == "log-instruction":
        with _write_lock():
            log_instruction(args)
    elif args.cmd == "log-work":
        with _write_lock():
            log_work(args)
    elif args.cmd == "log-action":
        with _write_lock():
            log_action(args)
    elif args.cmd == "search":
        search(args)
    elif args.cmd == "index-add":
        with _write_lock():
            index_add(args)
    elif args.cmd == "check-duplicate":
        check_duplicate(args)
    elif args.cmd == "get-search-cache":
        cmd_get_search_cache(args)
    elif args.cmd == "put-search-cache":
        cmd_put_search_cache(args)
    elif args.cmd == "check-content-duplicate":
        cmd_check_content_duplicate(args)
    elif args.cmd == "check-target-identifier-duplicate":
        cmd_check_target_identifier_duplicate(args)
    elif args.cmd == "log-execution":
        with _write_lock():
            log_execution(args)
    elif args.cmd == "index-transcript":
        with _write_lock():
            index_transcript(args)
    elif args.cmd == "log-login":
        with _write_lock():
            aid = log_login(args.user, args.source_ip, args.method)
            print(json.dumps({"action_id": aid}))
    elif args.cmd == "log-fix":
        with _write_lock():
            log_fix(args)
    elif args.cmd == "register-knowledge":
        with _write_lock():
            register_knowledge(args)
    elif args.cmd == "query-knowledge":
        query_knowledge(args)
    elif args.cmd == "verify-knowledge":
        with _write_lock():
            verify_knowledge(args)
    elif args.cmd == "annotate-knowledge":
        with _write_lock():
            annotate_knowledge(args)
    elif args.cmd == "add-relationship":
        with _write_lock():
            add_relationship(args)
    elif args.cmd == "add-tag":
        with _write_lock():
            add_tag(args)
    elif args.cmd == "upsert-knowledge-fragment":
        with _write_lock():
            upsert_knowledge_fragment(args)
    elif args.cmd == "list-knowledge":
        list_knowledge(args)
    elif args.cmd == "register-capability":
        with _write_lock():
            register_capability(args)
    elif args.cmd == "lookup-capability":
        lookup_capability(args)
    elif args.cmd == "list-capabilities":
        list_capabilities(args)
    elif args.cmd == "search-task-precedent":
        cmd_search_task_precedent(args)
    elif args.cmd == "record-graduation":
        with _write_lock():
            cmd_record_capability_graduation(args)
    elif args.cmd == "list-graduations":
        list_capability_graduations(args)
    elif args.cmd == "capture-replay":
        with _write_lock():
            capture_replay(args)
    elif args.cmd == "run-replay":
        with _write_lock():
            run_replay(args)
    elif args.cmd == "list-replays":
        list_replays(args)
    elif args.cmd == "register-entity":
        with _write_lock():
            register_entity(args)
    elif args.cmd == "lookup-entity":
        lookup_entity(args)
    elif args.cmd == "list-entities":
        list_entities(args)
    elif args.cmd == "claim-task-key":
        with _write_lock():
            claim_task_key(args)
    elif args.cmd == "check-task-key":
        check_task_key(args)
    elif args.cmd == "query-ocid-canonical":
        cmd_query_ocid_canonical(args)
    elif args.cmd == "resolve-ocid-canonical":
        cmd_resolve_ocid_canonical(args)
    elif args.cmd == "reconcile-umr-status":
        cmd_reconcile_umr_status(args)
    elif args.cmd == "certify-pr-merge":
        cmd_certify_pr_merge(args)
    elif args.cmd == "insert-pm-decision-pending":
        cmd_insert_pm_decision_pending(args)
    elif args.cmd == "resolve-pm-decision-pending":
        cmd_resolve_pm_decision_pending(args)
    elif args.cmd == "update-pm-decision-pending":
        cmd_update_pm_decision_pending(args)
    elif args.cmd == "insert-owner-proposal":
        cmd_insert_owner_proposal(args)
    elif args.cmd == "decide-owner-proposal":
        cmd_decide_owner_proposal(args)
    elif args.cmd == "record-owner-proposal-completion":
        cmd_record_owner_proposal_completion(args)
    elif args.cmd == "mark-umr-dispatched":
        cmd_mark_umr_dispatched(args)
    elif args.cmd == "mark-umr-relay-attempted":
        cmd_mark_umr_relay_attempted(args)
    elif args.cmd == "requeue-build-lock-contended":
        cmd_requeue_build_lock_contended(args)
    elif args.cmd == "mark-umr-terminal":
        cmd_mark_umr_terminal(args)
    elif args.cmd == "update-gtm-category":
        cmd_update_gtm_category(args)
    elif args.cmd == "list-gtm-categories":
        cmd_list_gtm_categories(args)
    elif args.cmd == "record-gtm-part3-4-certificate":
        cmd_record_gtm_part3_4_certificate(args)
    elif args.cmd == "reset-umr-to-queued":
        cmd_reset_umr_to_queued(args)
    elif args.cmd == "add-issue":
        cmd_add_issue(args)
    elif args.cmd == "close-issue":
        cmd_close_issue(args)
    elif args.cmd == "update-issue":
        cmd_update_issue(args)
    elif args.cmd == "list-issues":
        cmd_list_issues(args)
    elif args.cmd == "log-governance-event":
        cmd_log_governance_event(args)
    elif args.cmd == "list-governance-events":
        cmd_list_governance_events(args)
    elif args.cmd == "mark-external-agent-eligible":
        cmd_mark_external_agent_eligible(args)
    elif args.cmd == "get-next-external-agent-task":
        cmd_get_next_external_agent_task(args)
    elif args.cmd == "submit-external-agent-result":
        cmd_submit_external_agent_result(args)
    elif args.cmd == "expire-external-agent-dispatches":
        cmd_expire_external_agent_dispatches(args)
    elif args.cmd == "seed-owner-priority-sequence":
        cmd_seed_owner_priority_sequence(args)
    elif args.cmd == "advance-owner-priority-phases":
        cmd_advance_owner_priority_phases(args)
    elif args.cmd == "show-owner-priority-state":
        cmd_show_owner_priority_state(args)
