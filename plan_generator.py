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
import os
import re
import sqlite3
import subprocess
import sys
import uuid

VERIDIAN_ROOT = "/opt/veridian"
SCRIPTS = f"{VERIDIAN_ROOT}/scripts"
AI_OS = f"{VERIDIAN_ROOT}/ai-os"
SUPERBOSS = f"{SCRIPTS}/superboss-register.py"

_sbr = None


def _superboss_register():
    global _sbr
    if _sbr is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "superboss_register_plan_generator", SUPERBOSS)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sbr = _mod
    return _sbr


def _resolve_db_path():
    return _superboss_register().resolve_superboss_db_path()

VALID_TIERS = ["mechanical", "integrative", "judgment"]

# Deterministic-only splitting, same discipline task-tightening.ts's own
# header documents for every gate in this codebase: split on explicit
# sequencing punctuation/connectors a caller actually wrote, never infer
# steps that weren't stated. A single-sentence objective with none of these
# is one step, not artificially subdivided.
STEP_SPLIT_RE = re.compile(r"\s*(?:;|\bthen\b|\bafter that\b|,\s*then\b)\s*", re.IGNORECASE)


def _connect():
    conn = sqlite3.connect(_resolve_db_path())
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
    conn = sqlite3.connect(_resolve_db_path())
    val = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0]
    conn.close()
    return val


def split_objective_into_steps(objective):
    """Pure, unit-testable, no DB/LLM dependency -- same 'extracted so it's
    directly testable' discipline as task-tightening.ts's own
    extractDeclaredScopeFiles()/checkFilesWithinDeclaredScope()."""
    parts = [p.strip() for p in STEP_SPLIT_RE.split(objective) if p.strip()]
    return parts if parts else [objective.strip()]


def _lookup_capability(intent_text, domain=None):
    cmd = ["python3", SUPERBOSS, "lookup-capability", "--intent-text", intent_text]
    if domain:
        cmd += ["--domain", domain]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"found": False, "matches": []}


def _lookup_entity(query_text):
    proc = subprocess.run(
        ["python3", SUPERBOSS, "lookup-entity", "--query", query_text],
        capture_output=True, text=True,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"found": False, "matches": []}


def _query_knowledge(query_text):
    proc = subprocess.run(
        ["python3", SUPERBOSS, "query-knowledge", query_text],
        capture_output=True, text=True,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"found": 0, "matches": []}


def _search_system_index(query_text):
    proc = subprocess.run(
        ["python3", SUPERBOSS, "search", query_text, "--limit", "5"],
        capture_output=True, text=True,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"system_index": []}


# Phase 7 (reuse-check-enforcement-gate-phase7-2026-07-30): a capability
# match at or above this confidence is treated as "this already exists,
# reuse it" rather than merely "worth a human glance" -- same threshold
# convention lookup_capability()'s own resolution_order comment (in
# superboss-register.py) treats exact-name/domain-scoped matches as
# high-confidence signal for. Advisory only; see
# check_reuse_before_dispatch()'s own docstring.
REUSE_CONFIDENCE_THRESHOLD = 0.7

# Real fix (UMR-20260806-141250-1ceb, proposal 86 / governing
# UMR-20260806-071025-1d28): defense-in-depth cap on what
# check_reuse_before_dispatch() embeds into a caller's metadata_json (real
# caller today: resource_governor.py's submit(),
# metadata_json.reuse_check_result -- see that function's own docstring).
# Independent from, and in ADDITION TO, the query-side LIMIT the same UMR
# added to superboss-register.py's lookup_entity()/query_knowledge(): this
# bound holds even if a future caller reaches those registries some other
# way, or a regression later drops the query-side LIMIT -- per
# UMR-20260806-135902-cf13's own root-cause finding that exactly this
# (a full, unbounded match list embedded verbatim into a stored row) is what
# grew umr_tasks to 1855.7MB in ~11 minutes (one sampled row's embedded
# reuse_check_result.wiring.matches alone held all 8441 unranked-cutoff
# matches for a single query). Every match LIST embedded in the returned
# result is capped to this many items; a `total_matches` field is kept
# alongside so "how many exist" is never silently lost -- only the full
# verbatim dump is -- matching proposal 86's own approved `detail` field
# ("top-N matches + a real total_matches count"). Deliberately smaller than
# WIRING_LOOKUP_MATCH_LIMIT/KNOWLEDGE_QUERY_MATCH_LIMIT (superboss-register.py,
# 50 each): those bound what one query fetches for the reuse-relevance logic
# below to reason over; this bounds only what ends up permanently stored on
# a umr_tasks row for accountability -- a human/PM skimming metadata_json
# needs a few concrete examples, not fifty.
EMBEDDED_MATCH_SUMMARY_LIMIT = 10


def _bounded_list_summary(items, limit=EMBEDDED_MATCH_SUMMARY_LIMIT):
    """items -> (truncated_list, total_count, was_truncated). Pure, no I/O
    -- same 'extracted so it's directly testable' discipline as
    split_objective_into_steps()/_name_overlaps_intent() above."""
    items = items or []
    total = len(items)
    return items[:limit], total, total > limit

# Small, local stopword set for _name_overlaps_intent() below -- deliberately
# NOT importing superboss-register.py's own STOPWORDS in-process (this
# module only ever talks to that script via subprocess, same convention as
# _lookup_capability() etc.); this list only needs to filter common English
# function words out of a registry NAME (e.g. "of"/"and"/"the" inside a
# capability_name), a much narrower job than _fts_query()'s own stopword use.
STOPWORDS = {"the", "and", "for", "of", "with", "from", "into", "onto", "this", "that"}

_NAME_WORD_RE = re.compile(r"[a-z0-9]+")


def _name_overlaps_intent(name, intent_text):
    """Mechanical (no LLM call) relevance check: does a registry name (e.g.
    capability_name 'commission_calculator', or a wiring entity_id) actually
    describe what intent_text is about, rather than merely sharing an
    incidental FTS term with it? True if every significant (len > 2,
    de-stopworded) word of `name` -- split on '_'/'-'/camelCase boundaries --
    appears as a whole word somewhere in intent_text. Used by
    check_reuse_before_dispatch() instead of capability_registry's own
    per-row 'confidence' field (a static data-quality rating, not a
    query-relevance score -- see that function's own comment for the real
    false-positive this caught live in Phase 7 testing)."""
    if not name or not intent_text:
        return False
    words = [w for w in _NAME_WORD_RE.findall(name.lower()) if len(w) > 2 and w not in STOPWORDS]
    if not words:
        return False
    intent_words = set(_NAME_WORD_RE.findall(intent_text.lower()))
    return all(w in intent_words for w in words)


def check_reuse_before_dispatch(intent_text, task_identity=None, domain=None):
    """Phase 7 (reuse-check-enforcement-gate-phase7-2026-07-30): the single,
    shared, real "check existing before building new" enforcement function.
    Until this, that check existed only as prompt instructions given by hand
    to each dispatched agent (worked today for the SAP-equivalent-reports
    work only because the prompts happened to say so every time) -- nothing
    in software stopped a future dispatch from skipping it. Separately,
    task-gateway.py's cmd_submit and directive_engine.py's process_one
    (via run_check_duplicate_battery) already run their own real
    check-duplicate/search/query-knowledge/lookup-capability battery before
    ever constructing a task_spec -- but resource_governor.py's submit(),
    the real low-level task-creation entrypoint everything else funnels
    into, had no equivalent of its own and could be reached directly,
    bypassing both.

    This function is that missing, shared piece: it does not reimplement
    any registry lookup -- it composes the same already-built
    superboss-register.py subcommands this module's own _lookup_capability()
    already used (lookup-capability), plus lookup-entity (wiring_registry),
    query-knowledge (knowledge_engine), and search (system_index, among
    other FTS trees), all via subprocess exactly as this module and
    task-gateway.py's cmd_submit already call them.

    Returns a real, structured result:
      {
        "checked_at": iso8601 str or None,
        "intent_text": str, "task_identity": str or None,
        "capability": <lookup-capability's own full JSON response>,
        "wiring": <lookup-entity's own full JSON response>,
        "knowledge": <query-knowledge's own full JSON response>,
        "system_index_search": <search's own full JSON response>,
        "reuse_candidates": [str, ...],   # names/ids pulled from whichever
                                          # of the above found something
        "confidence": float,             # capability's best_match_confidence
        "recommendation": "proceed" | "needs_review" | "reuse_instead",
        "error": str or None,            # set (non-fatal) if a lookup failed
      }

    Never raises and never blocks -- a broken or inconclusive check always
    still returns recommendation="proceed" (or "needs_review"), the same
    fail-open philosophy as directive_engine.py's
    find_in_flight_duplicate()/run_check_duplicate_battery(): this is
    advisory, recorded for accountability (e.g. callers writing it to
    umr_tasks.metadata_json.reuse_check_result), never a hard gate that
    could block genuinely new, legitimate work.
    """
    intent_text = (intent_text or "").strip()
    result = {
        "checked_at": None,
        "intent_text": intent_text,
        "task_identity": task_identity,
        "capability": {"found": False, "matches": [], "best_match_confidence": 0.0},
        "wiring": {"found": False, "matches": []},
        "knowledge": {"found": 0, "matches": []},
        "system_index_search": {"system_index": []},
        "reuse_candidates": [],
        "confidence": 0.0,
        "recommendation": "proceed",
        "error": None,
    }
    try:
        result["checked_at"] = _now_iso()
    except Exception:
        pass

    if not intent_text:
        return result

    errors = []
    try:
        result["capability"] = _lookup_capability(intent_text, domain)
    except Exception as e:
        errors.append(f"capability lookup failed: {e}")
    try:
        result["wiring"] = _lookup_entity(intent_text)
    except Exception as e:
        errors.append(f"wiring lookup failed: {e}")
    try:
        result["knowledge"] = _query_knowledge(intent_text)
    except Exception as e:
        errors.append(f"knowledge lookup failed: {e}")
    try:
        result["system_index_search"] = _search_system_index(intent_text)
    except Exception as e:
        errors.append(f"search failed: {e}")
    if errors:
        result["error"] = "; ".join(errors)

    cap = result["capability"] or {}
    cap_found = bool(cap.get("found"))
    # capability_registry's own "confidence" field (surfaced here as
    # best_match_confidence) is a static per-ROW data-quality rating set at
    # register-capability time (e.g. "how sure are we this record is
    # accurate") -- NOT a query-relevance score. Any FTS hit at all, however
    # generic the shared term, carries that same ~1.0 row confidence, so
    # thresholding on it directly (an earlier version of this function did)
    # falsely recommended reuse_instead for text that only coincidentally
    # shared a common word with an unrelated row (caught live during Phase 7
    # end-to-end testing: a "quasar-flux telemetry ingestion" test task
    # matched on generic terms and got reuse_instead). Real relevance is
    # instead computed mechanically here -- same "no LLM call, deterministic
    # only" discipline this module's own split_objective_into_steps() already
    # follows -- by checking whether the matched capability_name's own
    # significant words actually appear in the submitted intent_text.
    cap_matches = cap.get("matches", []) or []
    strong_cap_matches = [m for m in cap_matches
                           if m.get("capability_name") and _name_overlaps_intent(m["capability_name"], intent_text)]
    cap_confidence = max((float(m.get("confidence") or 0.0) for m in strong_cap_matches), default=0.0)

    wiring_matches = (result["wiring"] or {}).get("matches") or []
    wiring_found = bool((result["wiring"] or {}).get("found")) and bool(wiring_matches)
    strong_wiring_matches = [m for m in wiring_matches
                              if m.get("entity_id") and _name_overlaps_intent(m["entity_id"], intent_text)]

    knowledge_matches = (result["knowledge"] or {}).get("matches") or []
    knowledge_found = len(knowledge_matches) > 0
    sys_idx_matches = ((result["system_index_search"] or {}).get("system_index")) or []
    search_found = isinstance(sys_idx_matches, list) and len(sys_idx_matches) > 0

    candidates = []
    if cap_found:
        candidates += [m.get("capability_name") for m in cap_matches if m.get("capability_name")]
    if wiring_found:
        candidates += [m.get("entity_id") for m in wiring_matches if m.get("entity_id")]
    if knowledge_found:
        candidates += [m.get("purpose") or m.get("id") for m in knowledge_matches[:5]]
    result["reuse_candidates"] = [c for c in candidates if c]

    strong_match = bool(strong_cap_matches) or bool(strong_wiring_matches)
    result["confidence"] = cap_confidence if strong_match else 0.0

    if strong_match and cap_confidence >= REUSE_CONFIDENCE_THRESHOLD:
        result["recommendation"] = "reuse_instead"
    elif strong_match or cap_found or wiring_found or knowledge_found or search_found:
        result["recommendation"] = "needs_review"
    else:
        result["recommendation"] = "proceed"

    # Real fix (UMR-20260806-141250-1ceb): bound what actually gets embedded
    # (see EMBEDDED_MATCH_SUMMARY_LIMIT's own comment above) -- computed from
    # the SAME already-fetched match lists the relevance logic above already
    # used, so recommendation/confidence/reuse_candidates are unaffected by
    # this; only the stored representation shrinks, here, right before
    # return, so every real caller (today: resource_governor.py's submit())
    # gets an already-bounded result with no extra step required on its end.
    cap_bounded, cap_total, cap_truncated = _bounded_list_summary(cap_matches)
    result["capability"] = {**cap, "matches": cap_bounded,
                             "total_matches": cap_total, "matches_truncated": cap_truncated}

    wiring_bounded, wiring_total, wiring_truncated = _bounded_list_summary(wiring_matches)
    result["wiring"] = {**(result["wiring"] or {}), "matches": wiring_bounded,
                         "total_matches": wiring_total, "matches_truncated": wiring_truncated}

    knowledge_bounded, knowledge_total, knowledge_truncated = _bounded_list_summary(knowledge_matches)
    result["knowledge"] = {**(result["knowledge"] or {}), "matches": knowledge_bounded,
                            "total_matches": knowledge_total, "matches_truncated": knowledge_truncated}

    sys_bounded, sys_total, sys_truncated = _bounded_list_summary(sys_idx_matches)
    result["system_index_search"] = {**(result["system_index_search"] or {}), "system_index": sys_bounded,
                                      "total_matches": sys_total, "matches_truncated": sys_truncated}

    return result


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
