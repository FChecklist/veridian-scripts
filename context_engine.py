#!/usr/bin/env python3
"""
context_engine.py -- the real extension of the Context Engine toward
cross-turn conversational memory (closes engine 2,
ai-os/20_ENGINES_10_GATEWAYS_PHASE_PLAN_2026-07-24.yaml's
phase_6_intent_context_planning_learning).

Grounded in a real read of repos/compliance-tracker/src/lib/services/context.ts:
ServiceContext is `{ orgId, actor, request? }` -- real, enforced,
request-scoped tenant/actor context threaded through every service call, but
it carries nothing across turns of a conversation (no session concept at
all). Phase 6's own objective text says to extend it toward real cross-turn
memory "reusing Knowledge Engine's entity_relationships, not a new store" --
Knowledge Engine (engine 15, full coverage) is
ai-os/memory/superboss-register.sqlite + scripts/superboss-register.py, and
its entity_relationships column is a JSON list of
{related_artifact_id, related_artifact_path, relationship_type, evidence}
edges (see register_knowledge()/add_relationship() in that file).

knowledge_engine's own rows are keyed by a real on-disk artifact_path
(register-knowledge's --path is hashed via sha256 of an actual file) -- a
conversation session is not a file, so shoehorning it into that table would
misuse exists_on_disk/verification_status (permanently PATH_MISSING for
something that was never supposed to be a file). Per this whole plan's own
established precedent (Phase 1 added capability_registry, Phase 3 added
automation_rules/automation_rule_runs) -- a real Nth table in the SAME db
file, same write-lock/idempotent-create convention, is not "a new store" in
the sense this phase's objective forbids (a competing Redis/vector-DB/
session-store elsewhere); it is the same reuse-not-duplicate pattern this
whole file already establishes. What IS reused, verbatim, is the
entity_relationships edge SHAPE/mechanism itself: this module's
conversation_memory.entity_relationships column stores the exact same
{related_entity_type, related_entity_id, relationship_type, evidence}
edge shape Knowledge Engine's own column uses, not a redesigned one.

Repo boundary (same posture every phase 1-5 status_detail already states):
context.ts itself lives in compliance-tracker, a separate repo -- this
module does not edit it. See
ai-os/PHASE6_INTENT_CONTEXT_PLANNING_LEARNING_2026-07-24.yaml's own
context_engine section for the documented, not-yet-built TS-side hook
(an optional `conversationSessionId` on ServiceContext that would call
recall-context/remember-turn below).

Subcommands: remember-turn, recall-context, list-sessions
"""
import argparse
import json
import sqlite3
import sys
import uuid

VERIDIAN_ROOT = "/opt/veridian"
AI_OS = f"{VERIDIAN_ROOT}/ai-os"
DB_PATH = f"{AI_OS}/memory/superboss-register.sqlite"


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS conversation_memory (
        session_id TEXT PRIMARY KEY,
        org_id TEXT NOT NULL,
        actor_ref TEXT,
        created_ts TEXT NOT NULL,
        last_active_ts TEXT NOT NULL,
        turn_count INTEGER NOT NULL DEFAULT 0,
        entity_relationships TEXT NOT NULL DEFAULT '[]',
        summary TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_memory_org ON conversation_memory(org_id)")
    conn.commit()


def _now_iso():
    conn = sqlite3.connect(DB_PATH)
    val = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0]
    conn.close()
    return val


def cmd_remember_turn(args):
    """Upserts the session row and appends one real
    entity_relationships-shaped edge -- 'entity mentioned/acted-on in this
    turn'. Called once per turn by whatever call site already has orgId +
    actor (ServiceContext's own real fields) plus a sessionId; this module
    does not invent a sessionId source, matching every other best-effort
    edge writer in this whole plan (createChainVersion()'s own
    entity_relationships write, read verbatim from
    dynamic-chain-directory-service.ts, follows the identical
    try/insert/log-on-failure shape this mirrors)."""
    conn = _connect()
    _ensure_tables(conn)
    now = _now_iso()
    row = conn.execute("SELECT * FROM conversation_memory WHERE session_id = ?", (args.session_id,)).fetchone()

    edge = {
        "related_entity_type": args.entity_type,
        "related_entity_id": args.entity_id,
        "relationship_type": args.relationship_type,
        "evidence": args.evidence,
        "turn_ts": now,
    }

    if row is None:
        rels = [edge]
        conn.execute(
            "INSERT INTO conversation_memory (session_id, org_id, actor_ref, created_ts, last_active_ts, "
            "turn_count, entity_relationships, summary) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (args.session_id, args.org_id, args.actor_ref, now, now, json.dumps(rels), args.summary),
        )
    else:
        rels = json.loads(row["entity_relationships"] or "[]")
        rels.append(edge)
        new_summary = args.summary if args.summary else row["summary"]
        conn.execute(
            "UPDATE conversation_memory SET last_active_ts=?, turn_count=turn_count+1, "
            "entity_relationships=?, summary=? WHERE session_id=?",
            (now, json.dumps(rels), new_summary, args.session_id),
        )

    conn.commit()
    conn.close()
    print(json.dumps({
        "session_id": args.session_id, "remembered": True,
        "turn_count": (row["turn_count"] + 1) if row else 1,
        "entity_relationships": rels,
    }, indent=2))


def cmd_recall_context(args):
    """The real cross-turn recall -- given only a session_id (the one thing
    a new turn has that a fresh ServiceContext otherwise wouldn't), returns
    every entity_relationships edge accumulated so far plus the running
    summary. This is the literal answer to 'what does this conversation
    already know', servable to a caller building an extended ServiceContext
    the same way context.ts's own ReadContext is a narrower sibling of its
    ServiceContext."""
    conn = _connect()
    _ensure_tables(conn)
    row = conn.execute("SELECT * FROM conversation_memory WHERE session_id = ?", (args.session_id,)).fetchone()
    conn.close()
    if row is None:
        print(json.dumps({"session_id": args.session_id, "found": False, "entity_relationships": [], "summary": None}, indent=2))
        return
    print(json.dumps({
        "session_id": args.session_id, "found": True, "org_id": row["org_id"],
        "actor_ref": row["actor_ref"], "created_ts": row["created_ts"], "last_active_ts": row["last_active_ts"],
        "turn_count": row["turn_count"], "entity_relationships": json.loads(row["entity_relationships"] or "[]"),
        "summary": row["summary"],
    }, indent=2))


def cmd_list_sessions(args):
    conn = _connect()
    _ensure_tables(conn)
    query = "SELECT session_id, org_id, actor_ref, created_ts, last_active_ts, turn_count FROM conversation_memory"
    params = []
    if args.org_id:
        query += " WHERE org_id = ?"
        params.append(args.org_id)
    query += " ORDER BY last_active_ts DESC LIMIT ?"
    params.append(args.limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    print(json.dumps({"count": len(rows), "sessions": [dict(r) for r in rows]}, indent=2))


def build_parser():
    p = argparse.ArgumentParser(prog="context_engine.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("remember-turn")
    r.add_argument("--session-id", dest="session_id", required=True)
    r.add_argument("--org-id", dest="org_id", required=True)
    r.add_argument("--actor-ref", dest="actor_ref", default=None)
    r.add_argument("--entity-type", dest="entity_type", required=True, help="e.g. capability, dynamic_chain, document")
    r.add_argument("--entity-id", dest="entity_id", required=True)
    r.add_argument("--relationship-type", dest="relationship_type", required=True, help="e.g. mentioned, resolved_to, acted_on")
    r.add_argument("--evidence", default=None)
    r.add_argument("--summary", default=None, help="optional running summary update for this session")
    r.set_defaults(func=cmd_remember_turn)

    c = sub.add_parser("recall-context")
    c.add_argument("--session-id", dest="session_id", required=True)
    c.set_defaults(func=cmd_recall_context)

    l = sub.add_parser("list-sessions")
    l.add_argument("--org-id", dest="org_id", default=None)
    l.add_argument("--limit", type=int, default=50)
    l.set_defaults(func=cmd_list_sessions)

    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
