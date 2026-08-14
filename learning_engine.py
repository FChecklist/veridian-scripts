#!/usr/bin/env python3
"""
learning_engine.py -- the real feedback-consumption extension of the
Learning Engine (closes engine 16,
ai-os/20_ENGINES_10_GATEWAYS_PHASE_PLAN_2026-07-24.yaml's
phase_6_intent_context_planning_learning).

Grounded in a real read of
repos/compliance-tracker/src/lib/loops/task-reflection.ts: runTaskReflection()
computes real speedVerdict/costVerdict (its own verdictFor(), honest
"insufficient_data" below MIN_COMPARABLE_SAMPLE=3 comparable prior rows, a
+/-15% VERDICT_BAND) plus two flags (differentAiTierFlag/reusablePatternFlag)
that are ALWAYS written with verdict: null, needsJudgment: true -- by that
file's own comment, nothing in this codebase ever sets them to a real
value. Confirmed by reading the whole file: it is write-only. That is
literally this phase's own objective text: "today they are recorded, not
yet consumed."

task-reflection.ts and its taskReflections table live in compliance-tracker
(separate repo/Postgres DB) -- out of this repo's boundary to edit, same
posture as phases 1-5's own TS-side gaps. This module builds the AI-OS
operational-layer sibling: it ports verdictFor()'s exact algorithm
(MIN_COMPARABLE_SAMPLE=3, VERDICT_BAND=0.15, honest insufficient_data) onto
this repo's own real outcome history --
scripts/automation_rule_engine.py's automation_rule_runs.status
(success/failed) -- and, unlike task-reflection.ts's flags, this module's
verdict is REAL CONSUMED FEEDBACK, not a recorded-only judgment: a rule
whose verdict crosses "degraded" flips automation_rules.is_active to 0.
evaluate-rules (automation_rule_engine.py's cmd_evaluate_rules) already
filters `WHERE trigger_type = ? AND is_active = 1` -- so a degraded rule
genuinely stops firing on the very next evaluate-rules call, with zero
change needed to that file. This is the literal mechanism the objective
asks for: a verdict that feeds back into engine behavior, not one that
sits recorded and unread.

Subcommands: reflect, reflect-all, list-reflections
"""
import argparse
import json
import sqlite3
import sys
import uuid

VERIDIAN_ROOT = "/opt/veridian"
AI_OS = f"{VERIDIAN_ROOT}/ai-os"
SCRIPTS = f"{VERIDIAN_ROOT}/scripts"
SUPERBOSS_REGISTER = f"{SCRIPTS}/superboss-register.py"

# Real, canonical DB-path resolution -- same lazy-import-cached convention
# reconcile_stale_running_workers.py / worker-exit-status-bridge.py already use.
_sbr = None


def _superboss_register():
    global _sbr
    if _sbr is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "superboss_register_learning_engine", SUPERBOSS_REGISTER)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sbr = _mod
    return _sbr


def _resolve_db_path():
    return _superboss_register().resolve_superboss_db_path()


MIN_COMPARABLE_SAMPLE = 3
DEGRADED_FAILURE_RATE = 0.5


def _connect():
    conn = sqlite3.connect(_resolve_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS learning_reflections (
        reflection_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        rule_id TEXT NOT NULL,
        sample_size INTEGER NOT NULL,
        success_count INTEGER NOT NULL,
        failure_count INTEGER NOT NULL,
        success_rate REAL,
        verdict TEXT NOT NULL,
        action_taken TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_learning_reflections_rule_id ON learning_reflections(rule_id)")
    conn.commit()


def _now_iso():
    conn = sqlite3.connect(_resolve_db_path())
    val = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0]
    conn.close()
    return val


def verdict_for(success_count, failure_count):
    """Pure, unit-testable -- same 'exported for direct unit testing' bar
    task-reflection.ts's own verdictFor() sets, same insufficient_data-below-
    threshold honesty. Deliberately narrower question than that file's
    speed/cost band comparison (this asks reliable vs degraded, not
    faster/slower), because the real behavior this module consumes a
    verdict into (disable a chronically-failing rule) only needs a binary
    answer, not a 3-way band."""
    total = success_count + failure_count
    if total < MIN_COMPARABLE_SAMPLE:
        return "insufficient_data", None
    rate = success_count / total
    return ("degraded" if rate < DEGRADED_FAILURE_RATE else "reliable"), rate


def _reflect_one(rule_id):
    conn = _connect()
    _ensure_tables(conn)
    rule = conn.execute("SELECT * FROM automation_rules WHERE rule_id = ?", (rule_id,)).fetchone()
    if not rule:
        conn.close()
        return {"error": f"no automation_rules row for rule_id {rule_id}"}

    runs = conn.execute(
        "SELECT status FROM automation_rule_runs WHERE rule_id = ? ORDER BY ts DESC LIMIT 20",
        (rule_id,),
    ).fetchall()
    success_count = sum(1 for r in runs if r["status"] == "success")
    failure_count = sum(1 for r in runs if r["status"] == "failed")
    verdict, rate = verdict_for(success_count, failure_count)

    action_taken = "none"
    if verdict == "degraded" and rule["is_active"]:
        conn.execute("UPDATE automation_rules SET is_active = 0 WHERE rule_id = ?", (rule_id,))
        action_taken = "disabled_by_learning_verdict"
    elif verdict == "reliable" and not rule["is_active"]:
        # Symmetric real feedback: a rule previously disabled by this same
        # mechanism that has since recovered (its 20-run window no longer
        # shows a degraded rate, e.g. after an upstream fix) is
        # automatically re-enabled -- consumed behavior in both directions,
        # not a one-way kill switch with no path back.
        conn.execute("UPDATE automation_rules SET is_active = 1 WHERE rule_id = ?", (rule_id,))
        action_taken = "reenabled_by_learning_verdict"

    reflection_id = f"LR-{uuid.uuid4().hex[:12]}"
    conn.execute(
        "INSERT INTO learning_reflections (reflection_id, ts, rule_id, sample_size, success_count, "
        "failure_count, success_rate, verdict, action_taken) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (reflection_id, _now_iso(), rule_id, len(runs), success_count, failure_count, rate, verdict, action_taken),
    )
    conn.commit()
    conn.close()
    return {
        "reflection_id": reflection_id, "rule_id": rule_id, "rule_name": rule["rule_name"],
        "sample_size": len(runs), "success_count": success_count, "failure_count": failure_count,
        "success_rate": rate, "verdict": verdict, "action_taken": action_taken,
    }


def cmd_reflect(args):
    result = _reflect_one(args.rule_id)
    if "error" in result:
        print(json.dumps(result, indent=2))
        sys.exit(1)
    print(json.dumps(result, indent=2))


def cmd_reflect_all(args):
    conn = _connect()
    _ensure_tables(conn)
    rule_ids = [r["rule_id"] for r in conn.execute("SELECT rule_id FROM automation_rules").fetchall()]
    conn.close()
    results = [_reflect_one(rule_id) for rule_id in rule_ids]
    print(json.dumps({"count": len(results), "reflections": results}, indent=2))


def cmd_list_reflections(args):
    conn = _connect()
    _ensure_tables(conn)
    query = "SELECT * FROM learning_reflections"
    params = []
    if args.rule_id:
        query += " WHERE rule_id = ?"
        params.append(args.rule_id)
    query += " ORDER BY ts DESC LIMIT ?"
    params.append(args.limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    print(json.dumps({"count": len(rows), "reflections": [dict(r) for r in rows]}, indent=2))


def build_parser():
    p = argparse.ArgumentParser(prog="learning_engine.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("reflect")
    r.add_argument("--rule-id", dest="rule_id", required=True)
    r.set_defaults(func=cmd_reflect)

    ra = sub.add_parser("reflect-all")
    ra.set_defaults(func=cmd_reflect_all)

    l = sub.add_parser("list-reflections")
    l.add_argument("--rule-id", dest="rule_id", default=None)
    l.add_argument("--limit", type=int, default=50)
    l.set_defaults(func=cmd_list_reflections)

    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
