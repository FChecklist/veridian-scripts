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
"""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

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
    """
    os.makedirs(os.path.dirname(_WRITE_LOCK_PATH), exist_ok=True)
    with open(_WRITE_LOCK_PATH, "w") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    rand = secrets.token_hex(2)
    return f"{prefix}-{ts}-{rand}"


def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


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
    _migrate_instructions_content_hash(conn)
    _migrate_capability_registry_utm(conn)


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
    already has 'dispatch_event' from the prior migration."""
    _migrate_wiring_registry_content_hash(conn)
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
        content_hash TEXT
    )""")
    conn.execute(
        "INSERT INTO wiring_registry__migrate (entity_id, ts, entity_type, source_system, path, "
        "relationships, last_verified_ts, verification_status, source_ref, metadata_json, content_hash) "
        "SELECT entity_id, ts, entity_type, source_system, path, relationships, last_verified_ts, "
        "verification_status, source_ref, metadata_json, content_hash FROM wiring_registry"
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
        rows = conn.execute(
            "SELECT t.* FROM knowledge_engine_fts f JOIN knowledge_engine t ON t.rowid = f.rowid "
            "WHERE knowledge_engine_fts MATCH ? ORDER BY rank",
            (q,),
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
        utm_term TEXT
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
        content_hash TEXT
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
    (ai_role/vercel_project/dispatch_event/...) never have one. Does
    NOT commit or ensure the table -- callers doing a bulk run (generate_wiring_registry.py)
    own one _ensure_wiring_registry_table() + one commit() around many calls to this;
    the register-entity CLI (a single ad hoc row) owns both itself, see register_entity()."""
    missing = sorted(REQUIRED_WIRING_ENTITY_FIELDS - set(entity))
    if missing:
        raise ValueError(f"entity dict missing required entity_record_schema field(s): {missing}")
    now = _now_iso()
    conn.execute(
        "INSERT INTO wiring_registry (entity_id, ts, entity_type, source_system, path, relationships, "
        "last_verified_ts, verification_status, source_ref, metadata_json, content_hash) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(entity_id) DO UPDATE SET ts=excluded.ts, entity_type=excluded.entity_type, "
        "source_system=excluded.source_system, path=excluded.path, relationships=excluded.relationships, "
        "last_verified_ts=excluded.last_verified_ts, verification_status=excluded.verification_status, "
        "source_ref=excluded.source_ref, metadata_json=excluded.metadata_json, "
        "content_hash=excluded.content_hash",
        (
            entity["entity_id"], now, entity["entity_type"], entity["source_system"], entity.get("path"),
            json.dumps(entity["relationships"]), entity["last_verified_ts"], entity["verification_status"],
            json.dumps(entity["source_ref"]), json.dumps(entity.get("metadata") or {}),
            entity.get("content_hash"),
        ),
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
            rows = conn.execute(
                "SELECT t.* FROM wiring_registry_fts f JOIN wiring_registry t ON t.rowid = f.rowid "
                "WHERE wiring_registry_fts MATCH ? ORDER BY rank",
                (q,),
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
# ---------------------------------------------------------------------------
UMR_STATUSES = (
    "queued", "dispatched", "running", "completed", "failed",
    "rejected_duplicate", "sigterm_sent", "killed",
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
        if {"last_heartbeat", "tenant_id", "utm_source"} <= cols:
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
    conn.commit()
    _migrate_umr_last_heartbeat(conn)
    _migrate_umr_tenant_id(conn)
    _migrate_umr_utm(conn)


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


def update_umr_task(conn, umr_id, **fields):
    """Partial UPDATE of an existing umr_tasks row -- only the columns passed
    as keyword args are touched. json_fields are dicts that get json.dumps'd
    automatically before the UPDATE. Does NOT commit, same convention as
    upsert_umr_task()."""
    json_fields = {"outputs", "metric_snapshot", "metadata"}
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


def query_ocid_artifact_links(conn, ocid_number=None, umr_id=None, repo=None, pr_number=None, limit=50):
    """Real, read-only lookup -- deterministic linkage query, the whole point
    of this table existing (per the real Owner requirement this addendum
    implements): 'what closed OCID-X' or 'what OCID does PR/commit Y belong
    to', without re-deriving it from governance-doc prose."""
    clauses, params = [], []
    if ocid_number:
        clauses.append("ocid_number=?"); params.append(ocid_number)
    if umr_id:
        clauses.append("umr_id=?"); params.append(umr_id)
    if repo:
        clauses.append("repo=?"); params.append(repo)
    if pr_number is not None:
        clauses.append("pr_number=?"); params.append(pr_number)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM ocid_artifact_links {where} ORDER BY created_at DESC LIMIT ?",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def _umr_row_to_dict(row):
    d = dict(row)
    for key in ("inputs_json", "outputs_json", "metric_snapshot_json", "metadata_json"):
        if d.get(key):
            d[key] = json.loads(d[key])
    return d


def query_umr_tasks(conn, limit=20, status=None, tier=None, task_identity=None, query_text=None):
    """Real search over umr_tasks -- exact task_identity match first, then
    FTS5 over task_identity/source_trigger/logs_ref for a free-text
    --search, else a plain filtered listing (newest first). Same two-stage
    resolution shape lookup_entity()/lookup_capability() already use."""
    if task_identity:
        rows = conn.execute(
            "SELECT * FROM umr_tasks WHERE task_identity=? ORDER BY ts_submitted DESC LIMIT ?",
            (task_identity, limit),
        ).fetchall()
    elif query_text:
        q = _fts_query(query_text)
        try:
            rows = conn.execute(
                "SELECT t.* FROM umr_tasks_fts f JOIN umr_tasks t ON t.rowid = f.rowid "
                "WHERE umr_tasks_fts MATCH ? ORDER BY t.ts_submitted DESC LIMIT ?",
                (q, limit),
            ).fetchall()
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
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        rows = conn.execute(
            f"SELECT * FROM umr_tasks {where} ORDER BY ts_submitted DESC LIMIT ?", params
        ).fetchall()

    matches = [_umr_row_to_dict(r) for r in rows]
    if status and (task_identity or query_text):
        matches = [m for m in matches if m["status"] == status]
    if tier is not None and (task_identity or query_text):
        matches = [m for m in matches if m["tier"] == tier]
    return matches


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

    p_cdup = sub.add_parser("check-content-duplicate", help="Stage 2 (task-20260729): "
                             "content-hash dedup for same-text chat resubmission -- has "
                             "this exact instruction text already been submitted recently.")
    p_cdup.add_argument("--text", required=True)
    p_cdup.add_argument("--window-hours", dest="window_hours", type=float, default=24)

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

    args = p.parse_args()
    if args.cmd == "init":
        with _write_lock():
            init_db()
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
    elif args.cmd == "check-content-duplicate":
        cmd_check_content_duplicate(args)
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
