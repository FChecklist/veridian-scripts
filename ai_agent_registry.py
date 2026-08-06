#!/usr/bin/env python3
"""
ai_agent_registry.py -- real, deterministic AI agent identity + persistent
memory registry for the AI-OS dispatch layer (veridian-scripts repo).

Direct correction (UMR-20260806-121332-6ba4) to UMR-20260806-121252-3207's
own original build spec, received as a real Owner clarification after that
UMR was dispatched. UMR-20260806-121252-3207 specified the scoping unit for
an agent_id as "this real class of work" (a fuzzy similar-task-class match).
The Owner clarification replaces that: the real scoping unit is one real UMR
equals one real agent_id, never shared across UMRs and never matched by
fuzzy task-class similarity, since a UMR id is already a real permanent
unique key and requires zero judgment to match. This file implements ONLY
the corrected scoping (this module, this table); it does not implement
UMR-20260806-121252-3207's separate first item (the UMR-20260806-065104-c69a
status-correction), which is out of this correction's scope and remains
whatever task ends up covering that UMR's own full original spec.

Real schema (new table inside the ONE existing live
/opt/veridian/ai-os/memory/superboss-register.sqlite -- zero new files,
zero new database, per both UMRs' explicit instruction):
  ai_agent_registry(agent_id PK, umr_id UNIQUE NOT NULL, role_label,
    memory_file_path, created_at, last_used_at, total_tasks_handled,
    metadata_json)
umr_id is UNIQUE -- this is the actual mechanism enforcing "one real UMR
equals one real agent_id, never shared across UMRs" at the database level,
not just in this module's own logic.

Deterministic id format matching the existing UMR id convention (per
UMR-20260806-121252-3207's own still-valid requirement, now made concrete
by the correction's "zero judgment" standard): agent_id is a pure string
transform of umr_id -- "UMR-" is replaced with "AGENT-", nothing else
changes. Given umr_id is already a real permanent unique key, this
transform is itself collision-free and requires no lookup, no similarity
scoring, no judgment call to compute or to verify.

Real behavior:
  - ensure-agent: first real dispatch under a real UMR mints exactly one
    agent_id tied 1:1 to that umr_id (enforced by the UNIQUE constraint,
    not just checked); a second call for the same umr_id is idempotent and
    returns the existing row (already_existed=true), never a second agent.
  - record-work: every real step of work done under that same real UMR --
    every cycle, every retry, every follow-up dispatch that cites the same
    UMR -- appends one real timestamped entry to that exact same agent's
    own memory file (never a shared file, never another agent's file), and
    bumps last_used_at/total_tasks_handled on that same row. Auto-mints via
    ensure-agent if this is the first call for the UMR.
  - lookup-agent / list-agents: real read-only lookups, used by
    check-before-dispatch and by any future caller that just needs the row.
  - check-before-dispatch: implements the real two-step deterministic check
    this correction's own closing sentence specifies -- (1) does an
    existing capability_registry row already handle this (delegates to
    superboss-register.py's own lookup-capability, never a reimplementation
    of that lookup), (2) does an agent_id already exist for this exact UMR
    with real history. new_work_justified is true only if both answer no.

This module is itself the "real script" the correction's second half
requires: it is registered in capability_registry (see
ai_agent_registry_capability_record.json, registered via
`superboss-register.py register-capability`) exactly like every other real
script, so a future zero-duplication check finds it there too, and never
needs to re-derive or re-guess this file's own existence.
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
AI_OS = "/opt/veridian/ai-os"
AGENT_MEMORY_DIR = f"{AI_OS}/memory/agents"

UMR_ID_RE = re.compile(r"^UMR-(\d{8}-\d{6}-[0-9a-f]{4})$")

_sbr = None


def _superboss_register():
    """In-process importlib load of superboss-register.py -- same pattern
    dispatch_core.py._superboss_register()/resource_governor.py's own
    established in this codebase (the filename has a hyphen, so it cannot
    be a plain `import`). Reuses that module's own canonical
    resolve_superboss_db_path()-derived DB_PATH rather than recomputing a
    second, parallel path anywhere in this file."""
    global _sbr
    if _sbr is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "superboss_register_agent_registry", os.path.join(SCRIPTS, "superboss-register.py"))
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sbr = _mod
    return _sbr


def _connect():
    sbr = _superboss_register()
    import sqlite3
    conn = sqlite3.connect(sbr.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _now_iso():
    # Reuses sqlite's own clock, same ordering-consistency convention as
    # automation_rule_engine.py's own _now_iso() -- every table's `ts`-shaped
    # column across this codebase is derived this way, not via Python's
    # datetime.now(), so cross-table ordering never drifts against sqlite
    # itself.
    conn = _connect()
    val = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0]
    conn.close()
    return val


def _ensure_tables(conn):
    """Standalone idempotent create, same defensiveness convention as
    superboss-register.py's own _ensure_capability_registry_table -- works
    even if init_db() was never run against this DB."""
    conn.execute("""CREATE TABLE IF NOT EXISTS ai_agent_registry (
        agent_id TEXT PRIMARY KEY,
        umr_id TEXT NOT NULL UNIQUE,
        role_label TEXT NOT NULL,
        memory_file_path TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_used_at TEXT,
        total_tasks_handled INTEGER NOT NULL DEFAULT 0,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ai_agent_registry_umr_id ON ai_agent_registry(umr_id)")
    conn.commit()


def _agent_id_for_umr(umr_id):
    """Pure, deterministic, zero-judgment transform -- the entire real
    mechanism the correction requires. Raises ValueError on any umr_id not
    shaped like the real existing UMR id convention (UMR-YYYYMMDD-HHMMSS-
    xxxx) rather than minting an agent_id for something that is not
    actually a real UMR id."""
    m = UMR_ID_RE.match(umr_id)
    if not m:
        raise ValueError(
            f"umr_id {umr_id!r} does not match the real UMR id convention "
            "'UMR-YYYYMMDD-HHMMSS-xxxx' -- refusing to mint an agent_id for it"
        )
    return f"AGENT-{m.group(1)}"


def _agent_row_to_dict(row):
    d = dict(row)
    d["metadata_json"] = json.loads(d["metadata_json"]) if d.get("metadata_json") else {}
    return d


def _memory_file_path(agent_id):
    return os.path.join(AGENT_MEMORY_DIR, f"{agent_id}.md")


def ensure_agent(umr_id, role_label):
    """Mints (first call for this umr_id) or reuses (every later call --
    idempotent) exactly one agent_id for this exact umr_id. Returns
    (row_dict, already_existed)."""
    agent_id = _agent_id_for_umr(umr_id)

    conn = _connect()
    _ensure_tables(conn)
    existing = conn.execute(
        "SELECT * FROM ai_agent_registry WHERE umr_id = ?", (umr_id,)
    ).fetchone()
    if existing:
        conn.close()
        return _agent_row_to_dict(existing), True

    os.makedirs(AGENT_MEMORY_DIR, exist_ok=True)
    memory_file_path = _memory_file_path(agent_id)
    now = _now_iso()
    if not os.path.exists(memory_file_path):
        with open(memory_file_path, "w", encoding="utf-8") as f:
            f.write(
                f"# {agent_id} -- persistent memory\n\n"
                f"Scoped 1:1 to {umr_id} (UMR-20260806-121332-6ba4 correction: one real UMR "
                "equals one real agent_id, never a fuzzy task-class match). Every real step "
                "of work done under this UMR, across every cycle/retry/follow-up dispatch "
                f"that cites {umr_id}, is appended below.\n\n"
                f"- role_label: {role_label}\n"
                f"- created_at: {now}\n"
            )

    try:
        conn.execute(
            "INSERT INTO ai_agent_registry (agent_id, umr_id, role_label, memory_file_path, "
            "created_at, last_used_at, total_tasks_handled, metadata_json) "
            "VALUES (?, ?, ?, ?, ?, NULL, 0, '{}')",
            (agent_id, umr_id, role_label, memory_file_path, now),
        )
        conn.commit()
    except Exception:
        # Real race: another process minted this exact umr_id's row between
        # our SELECT and this INSERT. The UNIQUE(umr_id) constraint is the
        # real enforcement point (never a caller-side assumption) -- lose
        # the race honestly and return the row that won it, rather than
        # raising or silently creating a second agent for the same UMR.
        conn.rollback()
        row = conn.execute(
            "SELECT * FROM ai_agent_registry WHERE umr_id = ?", (umr_id,)
        ).fetchone()
        conn.close()
        if row:
            return _agent_row_to_dict(row), True
        raise

    row = conn.execute(
        "SELECT * FROM ai_agent_registry WHERE umr_id = ?", (umr_id,)
    ).fetchone()
    conn.close()
    return _agent_row_to_dict(row), False


def cmd_ensure_agent(args):
    try:
        row, already_existed = ensure_agent(args.umr_id, args.role_label)
    except ValueError as e:
        print(json.dumps({"error": str(e)}, indent=2))
        sys.exit(1)
    print(json.dumps({**row, "already_existed": already_existed}, indent=2))


def cmd_record_work(args):
    try:
        row, _already_existed = ensure_agent(args.umr_id, args.role_label or "unlabeled AI-judgment task")
    except ValueError as e:
        print(json.dumps({"error": str(e)}, indent=2))
        sys.exit(1)

    now = _now_iso()
    with open(row["memory_file_path"], "a", encoding="utf-8") as f:
        f.write(f"\n## {now}\n{args.entry_text}\n")

    conn = _connect()
    _ensure_tables(conn)
    conn.execute(
        "UPDATE ai_agent_registry SET last_used_at = ?, total_tasks_handled = total_tasks_handled + 1 "
        "WHERE agent_id = ?",
        (now, row["agent_id"]),
    )
    conn.commit()
    updated = conn.execute(
        "SELECT * FROM ai_agent_registry WHERE agent_id = ?", (row["agent_id"],)
    ).fetchone()
    conn.close()
    print(json.dumps(_agent_row_to_dict(updated), indent=2))


def cmd_lookup_agent(args):
    conn = _connect()
    _ensure_tables(conn)
    row = conn.execute(
        "SELECT * FROM ai_agent_registry WHERE umr_id = ?", (args.umr_id,)
    ).fetchone()
    conn.close()
    if not row:
        print(json.dumps({"found": False, "umr_id": args.umr_id}, indent=2))
        return
    d = _agent_row_to_dict(row)
    memory_content = None
    if args.include_memory and os.path.isfile(d["memory_file_path"]):
        with open(d["memory_file_path"], encoding="utf-8") as f:
            memory_content = f.read()
    print(json.dumps({"found": True, **d, "memory_content": memory_content}, indent=2))


def cmd_list_agents(args):
    conn = _connect()
    _ensure_tables(conn)
    rows = conn.execute("SELECT * FROM ai_agent_registry ORDER BY created_at").fetchall()
    conn.close()
    agents = [_agent_row_to_dict(r) for r in rows]
    print(json.dumps({"count": len(agents), "agents": agents}, indent=2))


def cmd_check_before_dispatch(args):
    """The real deterministic two-step check this correction's own closing
    sentence specifies. Step 1 delegates to superboss-register.py's own
    lookup-capability (never a second, competing capability lookup). Step 2
    is this module's own exact-umr_id lookup. new_work_justified is true
    only if both real answers are no."""
    sbr = _superboss_register()
    import io
    import contextlib

    buf = io.StringIO()
    capability_match = None
    with contextlib.redirect_stdout(buf):
        try:
            lookup_args = argparse.Namespace(
                capability_name=None, intent_text=args.intent_text, domain=None,
            )
            sbr.lookup_capability(lookup_args)
        except SystemExit:
            pass
    try:
        capability_match = json.loads(buf.getvalue())
    except json.JSONDecodeError:
        capability_match = {"found": False, "matches": [], "error": "lookup_capability output unparseable"}

    conn = _connect()
    _ensure_tables(conn)
    agent_row = conn.execute(
        "SELECT * FROM ai_agent_registry WHERE umr_id = ?", (args.umr_id,)
    ).fetchone()
    conn.close()
    agent_match = _agent_row_to_dict(agent_row) if agent_row else None

    capability_found = bool(capability_match and capability_match.get("found"))
    agent_found = agent_match is not None
    print(json.dumps({
        "umr_id": args.umr_id,
        "capability_registry_match": capability_match,
        "existing_agent_for_this_umr": agent_match,
        "new_work_justified": not (capability_found or agent_found),
    }, indent=2))


def build_parser():
    p = argparse.ArgumentParser(prog="ai_agent_registry.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("ensure-agent", help="mint (first call) or reuse (idempotent) the one agent_id for this exact umr_id")
    e.add_argument("--umr-id", dest="umr_id", required=True)
    e.add_argument("--role-label", dest="role_label", required=True)
    e.set_defaults(func=cmd_ensure_agent)

    r = sub.add_parser("record-work", help="append one real work entry to this UMR's own agent memory file")
    r.add_argument("--umr-id", dest="umr_id", required=True)
    r.add_argument("--entry-text", dest="entry_text", required=True)
    r.add_argument("--role-label", dest="role_label", default=None,
                    help="only used if this is the first call for this umr_id (mint-time label)")
    r.set_defaults(func=cmd_record_work)

    la = sub.add_parser("lookup-agent")
    la.add_argument("--umr-id", dest="umr_id", required=True)
    la.add_argument("--include-memory", dest="include_memory", action="store_true")
    la.set_defaults(func=cmd_lookup_agent)

    ls = sub.add_parser("list-agents")
    ls.set_defaults(func=cmd_list_agents)

    c = sub.add_parser("check-before-dispatch",
                        help="the real deterministic check: capability_registry first, then this exact UMR's own agent_id, only then new work")
    c.add_argument("--umr-id", dest="umr_id", required=True)
    c.add_argument("--intent-text", dest="intent_text", required=True)
    c.set_defaults(func=cmd_check_before_dispatch)

    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
