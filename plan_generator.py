#!/usr/bin/env python3
"""
plan_generator.py -- the real multi-step plan/DAG generation extension of
the Planning Engine (closes engine 4,
ai-os/20_ENGINES_10_GATEWAYS_PHASE_PLAN_2026-07-24.yaml's
phase_6_intent_context_planning_learning).

Grounded in real reads of both named files:
  - task-tightening.ts's TightTask envelope (objective/scope/successCriteria/
    complexityTier/expectedOutput/constraints/knownContext) validates that a
    SINGLE task is specified completely enough to dispatch -- it has no
    concept of multiple steps or ordering at all.
  - dynamic-chain-directory-service.ts's searchChains()/detectMissingChain()
    do keyword-ranked recommendation/lookup over an already-existing, finite
    set of dynamic_chains rows -- real, but it recommends from known paths,
    it does not generate a new sequence of steps for a goal that needs more
    than one.

Neither generates a novel multi-step DAG from a goal, which is this whole
phase's own named gap for engine 4. This module builds that: given an
objective description, it splits it into ordered steps (deterministic
splitting only -- same "no LLM call" discipline every gate in this
codebase already follows, including task-tightening.ts's own header),
resolves EACH step against Phase 1's live capability_registry via
lookup-capability (so a step that already has a deterministic capability
path is marked resolved, not a hypothetical), and writes the DAG's
sequencing as real entity_relationships-shaped edges (plan_step
'depends_on' the previous plan_step) into a new plans/plan_steps sibling
table pair -- same reuse-not-duplicate posture as context_engine.py's
conversation_memory table.

Each step reuses TightTask's own field SHAPE (objective/scope/
successCriteria/complexityTier) as its own per-step envelope -- not a
redesign, and not a re-implementation of validateTightTask()'s actual
validation logic (that stays TS-side, out of this repo's boundary); this
module only requires the same fields be present per step so a generated
plan step is immediately TightTask-shaped if a caller later dispatches it
through that real gate.

Subcommands: generate-plan, get-plan, list-plans
"""
import argparse
import json
import re
import sqlite3
import subprocess
import sys
import uuid

VERIDIAN_ROOT = "/opt/veridian"
SCRIPTS = f"{VERIDIAN_ROOT}/scripts"
AI_OS = f"{VERIDIAN_ROOT}/ai-os"
DB_PATH = f"{AI_OS}/memory/superboss-register.sqlite"
SUPERBOSS = f"{SCRIPTS}/superboss-register.py"

VALID_TIERS = ["mechanical", "integrative", "judgment"]

# Deterministic-only splitting, same discipline task-tightening.ts's own
# header documents for every gate in this codebase: split on explicit
# sequencing punctuation/connectors a caller actually wrote, never infer
# steps that weren't stated. A single-sentence objective with none of these
# is one step, not artificially subdivided.
STEP_SPLIT_RE = re.compile(r"\s*(?:;|\bthen\b|\bafter that\b|,\s*then\b)\s*", re.IGNORECASE)


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS plans (
        plan_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        plan_name TEXT NOT NULL,
        objective TEXT NOT NULL,
        complexity_tier TEXT NOT NULL,
        step_count INTEGER NOT NULL
    )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_plans_name ON plans(plan_name)")
    conn.execute("""CREATE TABLE IF NOT EXISTS plan_steps (
        step_id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL,
        step_index INTEGER NOT NULL,
        objective TEXT NOT NULL,
        scope TEXT NOT NULL,
        success_criteria TEXT NOT NULL,
        complexity_tier TEXT NOT NULL,
        resolved_capability_name TEXT,
        ai_required INTEGER,
        depends_on_step_id TEXT,
        entity_relationships TEXT NOT NULL DEFAULT '[]'
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_plan_steps_plan_id ON plan_steps(plan_id)")
    conn.commit()


def _now_iso():
    conn = sqlite3.connect(DB_PATH)
    val = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0]
    conn.close()
    return val


def split_objective_into_steps(objective):
    """Pure, unit-testable, no DB/LLM dependency -- same 'extracted so it's
    directly testable' discipline as task-tightening.ts's own
    extractDeclaredScopeFiles()/checkFilesWithinDeclaredScope()."""
    parts = [p.strip() for p in STEP_SPLIT_RE.split(objective) if p.strip()]
    return parts if parts else [objective.strip()]


def _lookup_capability(intent_text):
    proc = subprocess.run(
        ["python3", SUPERBOSS, "lookup-capability", "--intent-text", intent_text],
        capture_output=True, text=True,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"found": False, "matches": []}


def cmd_generate_plan(args):
    if args.complexity_tier not in VALID_TIERS:
        print(json.dumps({"error": f"--complexity-tier must be one of {VALID_TIERS}"}, indent=2))
        sys.exit(1)

    steps_text = split_objective_into_steps(args.objective)
    conn = _connect()
    _ensure_tables(conn)

    existing = conn.execute("SELECT plan_id FROM plans WHERE plan_name = ?", (args.plan_name,)).fetchone()
    if existing:
        conn.close()
        print(json.dumps({"error": f"plan_name '{args.plan_name}' already exists (plan_id={existing['plan_id']}) -- generate-plan does not overwrite, use a new --plan-name"}, indent=2))
        sys.exit(1)

    plan_id = f"PLAN-{uuid.uuid4().hex[:12]}"
    now = _now_iso()
    conn.execute(
        "INSERT INTO plans (plan_id, ts, plan_name, objective, complexity_tier, step_count) VALUES (?, ?, ?, ?, ?, ?)",
        (plan_id, now, args.plan_name, args.objective, args.complexity_tier, len(steps_text)),
    )

    previous_step_id = None
    step_rows = []
    for idx, step_text in enumerate(steps_text):
        lookup = _lookup_capability(step_text)
        found = bool(lookup.get("found"))
        resolved_capability_name = lookup["matches"][0]["capability_name"] if found and lookup.get("matches") else None
        ai_required = lookup["matches"][0]["ai_required"] if found and lookup.get("matches") else None

        step_id = f"STEP-{uuid.uuid4().hex[:12]}"
        edges = []
        if previous_step_id:
            edges.append({
                "related_entity_type": "plan_step",
                "related_entity_id": previous_step_id,
                "relationship_type": "depends_on",
                "evidence": f"sequenced after step {idx} in generate-plan's deterministic split",
            })

        is_final_step = idx + 1 == len(steps_text)
        default_success_criteria = (
            f"Step {idx + 1} (final step) completes and satisfies the plan's overall objective"
            if is_final_step
            else f"Step {idx + 1} completes and its output is available to step {idx + 2}"
        )
        conn.execute(
            "INSERT INTO plan_steps (step_id, plan_id, step_index, objective, scope, success_criteria, "
            "complexity_tier, resolved_capability_name, ai_required, depends_on_step_id, entity_relationships) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (step_id, plan_id, idx, step_text,
             args.scope or f"Step {idx + 1} of plan '{args.plan_name}' -- see resolved_capability_name for the deterministic path, if any",
             args.success_criteria or default_success_criteria,
             args.complexity_tier, resolved_capability_name,
             None if ai_required is None else (1 if ai_required else 0),
             previous_step_id, json.dumps(edges)),
        )
        step_rows.append({
            "step_id": step_id, "step_index": idx, "objective": step_text,
            "resolved_capability_name": resolved_capability_name, "ai_required": ai_required,
            "depends_on_step_id": previous_step_id,
        })
        previous_step_id = step_id

    conn.commit()
    conn.close()
    print(json.dumps({
        "plan_id": plan_id, "plan_name": args.plan_name, "step_count": len(steps_text),
        "deterministic_steps": sum(1 for s in step_rows if s["resolved_capability_name"] and not s["ai_required"]),
        "steps": step_rows,
    }, indent=2))


def cmd_get_plan(args):
    conn = _connect()
    _ensure_tables(conn)
    plan = conn.execute("SELECT * FROM plans WHERE plan_id = ? OR plan_name = ?", (args.plan, args.plan)).fetchone()
    if not plan:
        conn.close()
        print(json.dumps({"error": "no such plan", "plan": args.plan}, indent=2))
        sys.exit(1)
    steps = conn.execute("SELECT * FROM plan_steps WHERE plan_id = ? ORDER BY step_index", (plan["plan_id"],)).fetchall()
    conn.close()
    print(json.dumps({
        "plan": dict(plan),
        "steps": [dict(s) | {"entity_relationships": json.loads(s["entity_relationships"] or "[]")} for s in steps],
    }, indent=2))


def cmd_list_plans(args):
    conn = _connect()
    _ensure_tables(conn)
    rows = conn.execute("SELECT * FROM plans ORDER BY ts DESC LIMIT ?", (args.limit,)).fetchall()
    conn.close()
    print(json.dumps({"count": len(rows), "plans": [dict(r) for r in rows]}, indent=2))


def build_parser():
    p = argparse.ArgumentParser(prog="plan_generator.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate-plan")
    g.add_argument("--plan-name", dest="plan_name", required=True)
    g.add_argument("--objective", required=True, help='free text; split into steps on ";" / "then" / "after that"')
    g.add_argument("--complexity-tier", dest="complexity_tier", required=True, choices=VALID_TIERS)
    g.add_argument("--scope", default=None, help="applied to every generated step if given; otherwise an auto-generated per-step placeholder")
    g.add_argument("--success-criteria", dest="success_criteria", default=None)
    g.set_defaults(func=cmd_generate_plan)

    gp = sub.add_parser("get-plan")
    gp.add_argument("plan", help="plan_id or plan_name")
    gp.set_defaults(func=cmd_get_plan)

    l = sub.add_parser("list-plans")
    l.add_argument("--limit", type=int, default=50)
    l.set_defaults(func=cmd_list_plans)

    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
