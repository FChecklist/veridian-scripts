#!/usr/bin/env python3
"""
intent_engine.py -- the real, non-speculative extension of the Intent Engine
(closes engine 1, ai-os/20_ENGINES_10_GATEWAYS_PHASE_PLAN_2026-07-24.yaml's
phase_6_intent_context_planning_learning).

Phase 6's own objective text is explicit: extend Intent Engine "beyond
mode-pill recall toward general free-text intent classification for
never-seen requests ONLY ONCE A REAL PRODUCT NEED FOR THIS IS IDENTIFIED --
today's recall-based coverage may already be sufficient; do not build a
generic NLU layer speculatively." Building a generic NLU/embedding
classifier with no evidence of real demand would be exactly the
speculative build this task's own CONSTRAINTS forbid.

So this module does not build NLU. It builds the honest DETECTOR that
would surface a real product need if one exists: every free-text
--intent-text lookup already flows through Phase 1's
`lookup-capability` CLI (exact-name -> domain-keyword-FTS resolution,
see CAPABILITY_REGISTRY_SCHEMA_2026-07-24.yaml's lookup_contract). A
genuine "never-seen request, no deterministic path" gap shows up as a
lookup-capability miss (found=false). This module wraps that call,
records every miss to a new intent_unmatched_log table (same DB, same
write-lock/idempotent-create convention as automation_rule_engine.py's
own sibling tables -- not a new store), and reports on it -- honestly,
per this whole session's own "insufficient_data, never fabricated"
discipline (task-reflection.ts's verdictFor(), decision-service.py's
risk-tier classification) -- rather than declaring a pattern before one
is real.

Subcommands: check-intent, gap-report, list-unmatched
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from collections import Counter

VERIDIAN_ROOT = "/opt/veridian"
SCRIPTS = f"{VERIDIAN_ROOT}/scripts"
AI_OS = f"{VERIDIAN_ROOT}/ai-os"
SUPERBOSS = f"{SCRIPTS}/superboss-register.py"

# Real, canonical DB-path resolution -- same lazy-import-cached convention
# reconcile_stale_running_workers.py / worker-exit-status-bridge.py already use.
_sbr = None


def _superboss_register():
    global _sbr
    if _sbr is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "superboss_register_intent_engine", SUPERBOSS)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sbr = _mod
    return _sbr


def _resolve_db_path():
    return _superboss_register().resolve_superboss_db_path()

# Below this many repeats of the SAME normalized query, a miss is one-off
# curiosity, not a pattern -- mirrors task-reflection.ts's own
# MIN_COMPARABLE_SAMPLE=3 "insufficient_data below this bar" discipline,
# reused rather than inventing a second threshold philosophy.
MIN_REPEAT_FOR_PATTERN = 3


def _connect():
    conn = sqlite3.connect(_resolve_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS intent_unmatched_log (
        log_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        query_text TEXT NOT NULL,
        normalized_query TEXT NOT NULL,
        domain TEXT,
        session_id TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_intent_unmatched_normalized ON intent_unmatched_log(normalized_query)")
    conn.commit()


def _now_iso():
    conn = sqlite3.connect(_resolve_db_path())
    val = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0]
    conn.close()
    return val


def _normalize(text):
    return " ".join(text.strip().lower().split())


def cmd_check_intent(args):
    """Real call, not a re-implementation -- delegates the actual resolution
    to Phase 1's own lookup-capability CLI (subprocess, same _run_json
    convention as automation_rule_engine.py), then logs a miss. A hit is not
    logged -- there is no gap to detect when recall-based coverage already
    resolved the request, matching this module's own stated purpose."""
    proc = subprocess.run(
        ["python3", SUPERBOSS, "lookup-capability", "--intent-text", args.intent_text]
        + (["--domain", args.domain] if args.domain else []),
        capture_output=True, text=True,
    )
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(json.dumps({"error": "lookup-capability did not return JSON", "stdout": proc.stdout, "stderr": proc.stderr}))
        sys.exit(1)

    found = bool(result.get("found"))
    if not found:
        conn = _connect()
        _ensure_tables(conn)
        conn.execute(
            "INSERT INTO intent_unmatched_log (log_id, ts, query_text, normalized_query, domain, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"IUL-{uuid.uuid4().hex[:12]}", _now_iso(), args.intent_text, _normalize(args.intent_text),
             args.domain, args.session_id),
        )
        conn.commit()
        conn.close()

    print(json.dumps({"intent_text": args.intent_text, "found": found, "lookup_result": result}, indent=2))


def cmd_gap_report(args):
    """The honest 'has a real product need for general NLU emerged yet'
    check. A single miss proves nothing (could be a typo, an out-of-domain
    request, a one-off). Only a normalized query repeated
    MIN_REPEAT_FOR_PATTERN+ times across DISTINCT sessions is reported as a
    real candidate pattern -- same repeats-across-independent-samples bar
    task-reflection.ts's own MIN_COMPARABLE_SAMPLE uses, not a lower one
    invented to manufacture a finding."""
    conn = _connect()
    _ensure_tables(conn)
    rows = conn.execute("SELECT * FROM intent_unmatched_log").fetchall()
    conn.close()

    total = len(rows)
    by_query = Counter(r["normalized_query"] for r in rows)
    sessions_by_query = {}
    for r in rows:
        sessions_by_query.setdefault(r["normalized_query"], set()).add(r["session_id"])

    candidates = [
        {"normalized_query": q, "repeat_count": c, "distinct_sessions": len(sessions_by_query[q])}
        for q, c in by_query.items()
        if c >= MIN_REPEAT_FOR_PATTERN and len(sessions_by_query[q]) >= 2
    ]
    candidates.sort(key=lambda c: c["repeat_count"], reverse=True)

    verdict = "real_pattern_detected" if candidates else "insufficient_data"
    print(json.dumps({
        "verdict": verdict,
        "total_unmatched_logged": total,
        "distinct_unmatched_queries": len(by_query),
        "candidate_patterns": candidates,
        "note": (
            "insufficient_data is the honest, expected result today -- this report only recommends "
            "building general free-text NLU classification once repeated, cross-session misses are "
            "real. It does not build NLU itself, per this phase's own CONSTRAINTS."
            if verdict == "insufficient_data" else
            "Repeated, cross-session misses found -- this is the real evidence this phase's own "
            "objective required before any NLU build is justified. Still not built here; see "
            "PHASE6_INTENT_CONTEXT_PLANNING_LEARNING_2026-07-24.yaml for the recommended next step."
        ),
    }, indent=2))


def cmd_list_unmatched(args):
    conn = _connect()
    _ensure_tables(conn)
    rows = conn.execute("SELECT * FROM intent_unmatched_log ORDER BY ts DESC LIMIT ?", (args.limit,)).fetchall()
    conn.close()
    print(json.dumps({"count": len(rows), "rows": [dict(r) for r in rows]}, indent=2))


def build_parser():
    p = argparse.ArgumentParser(prog="intent_engine.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check-intent")
    c.add_argument("--intent-text", dest="intent_text", required=True)
    c.add_argument("--domain", default=None)
    c.add_argument("--session-id", dest="session_id", default=None)
    c.set_defaults(func=cmd_check_intent)

    g = sub.add_parser("gap-report")
    g.set_defaults(func=cmd_gap_report)

    l = sub.add_parser("list-unmatched")
    l.add_argument("--limit", type=int, default=50)
    l.set_defaults(func=cmd_list_unmatched)

    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
