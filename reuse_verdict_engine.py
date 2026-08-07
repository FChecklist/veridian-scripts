#!/usr/bin/env python3
"""
reuse_verdict_engine.py -- the real, deterministic create/reuse/duplicate-block
orchestrator (UMR-20260807-035145-aa45, amendment to UMR-20260806-171945-5767,
governing chain UMR-20260806-124055-bc80 -> UMR-20260806-171945-5767 ->
UMR-20260807-033123-d5c0).

WHAT THIS IS: a boolean-gated, three-tier verdict for "does something like this
already exist, for ANY of this system's four real wiring sources" --

    similarity < LOW_SIMILARITY_THRESHOLD   -> create_authorized
    LOW <= similarity < HIGH                -> reuse_mandated_extend (the
                                                closest real match must be
                                                extended, not duplicated)
    similarity >= HIGH_SIMILARITY_THRESHOLD -> duplication_blocked

using the deterministic (non-ML) cosine-similarity vectors built by
vector_similarity.py over wiring_registry (entity_type in 'file'/'script'
["server files and scripts"], 'github_repo', 'vercel_project', 'supabase_table'
-- this UMR's four real sources) and capability_registry, kept current by the
real on-write update hooks added to superboss-register.py's register_entity_row()
and register_capability() -- never a separate/parallel vector table.

REAL DIVISION OF RESPONSIBILITY WITH UMR-20260807-033123-d5c0 (checked live,
not assumed -- that sibling UMR finished mid-way through this one's own build,
so this reflects its real landed capability, not a guess at its scope):

  - UMR-20260807-033123-d5c0 landed capability_registry row
    CAP-20260807-054020-6688 (capability_semantic_search), owning REAL semantic
    SEARCH via a genuine ML embedding call (OpenRouter openai/text-embedding-3-small,
    the same provider repos/compliance-tracker/src/lib/embeddings.ts already
    uses live) -- scoped ONLY to capability_registry + wiring_registry
    entity_type='script' (169 rows; its own metadata logs the rest of
    wiring_registry as explicitly out of scope this pass, not silently
    dropped), storing its embeddings in a SEPARATE capability_embedding_cache.sqlite
    ("own separate writer... never a second writer against the canonical DB").
    Its own metadata_json.false_premise_found independently reached the exact
    same conclusion this module's vector_similarity.py docstring did: the
    governing SPEC's "embedding code already exists" claim is false.
  - This UMR owns the POLICY layer: given ANY of the four real wiring sources
    named in ITS OWN SPEC (not scripts/capabilities alone -- also file/github_repo/
    vercel_project/supabase_table) and a real similarity score, decide
    create vs reuse-and-extend vs block, with real idempotency, AND it owns
    the one storage shape its own SPEC explicitly requires that the sibling's
    approach does not attempt: real vector_json columns ON the canonical
    wiring_registry/capability_registry tables themselves, not a parallel
    cache DB. The two are complementary, not duplicative: different technique
    (deterministic lexical term-vector here vs a real ML embedding call
    there), different storage (canonical-table columns here vs a separate
    cache DB there), different scope (all four wiring sources + capabilities
    here vs capabilities + scripts only there). This module deliberately does
    NOT call capability_semantic_search.py's live OpenRouter embedding
    pipeline from inside a "deterministic" verdict engine -- that would make
    every verdict depend on network reachability/cost for a real external API
    call, and would mix two different score distributions (semantic-embedding
    cosine vs lexical-token cosine) under one threshold pair calibrated only
    against the latter. A future pass MAY have this module prefer
    capability_semantic_search's ranked result for the capability_registry/
    script subset it already covers; documented here as real future scope,
    not built speculatively now.

WHY A DETERMINISTIC LEXICAL VECTOR, NOT AN ML EMBEDDING: see
vector_similarity.py's own module docstring -- the governing SPEC's claim that
document_engine.py/intent_engine.py/superboss-register.py already contain real
embedding code was checked against live source and is false; this substitutes
a genuinely real, dependency-free vector-similarity mechanism instead of either
building new ML infrastructure (forbidden by the SPEC itself) or depending on
an unreachable cross-repo TypeScript/pgvector service.

Subcommands: assess, backfill-vectors
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import vector_similarity as vs

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SUPERBOSS_PATH = os.path.join(SCRIPT_DIR, "superboss-register.py")

# The four real wiring_registry sources this UMR's SPEC names explicitly
# ("server files and scripts, github_repo, vercel_project, supabase_table").
# 'file' and 'script' are both "server files and scripts" -- wiring_registry
# already keeps them as two distinct entity_type values (script = a real
# scripts/*.py|*.sh entry point; file = every other tracked server file), both
# real members of this UMR's first source.
WIRING_SOURCE_ENTITY_TYPES = ("file", "script", "github_repo", "vercel_project", "supabase_table")

# Idempotency window for "recent history": 24 hours, matching this system's
# own daily PM-cycle cadence (pm_cycle_precheck.py, daily crontab-backup-*
# convention) -- a repeat of the exact same search intent inside one real
# operating day reuses the cached verdict; outside that window the underlying
# registries may have genuinely changed, so it is real-recomputed instead of
# trusted stale.
RECENT_HISTORY_WINDOW = timedelta(hours=24)


def _load_superboss():
    """In-process importlib load of superboss-register.py -- the exact same
    convention agent_work_briefing.py/resource_governor.py already use for this
    hyphenated-filename sibling module (a plain `import` cannot name it)."""
    spec = importlib.util.spec_from_file_location("superboss_register", SUPERBOSS_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _connect(sb):
    sb.init_db_silent()
    conn = sb._connect()
    sb._ensure_wiring_registry_table(conn)
    sb._ensure_capability_registry_table(conn)
    return conn


def load_candidates(conn):
    """Real candidate set: every current row across this UMR's four real
    wiring_registry sources plus every capability_registry row. Uses each
    row's own stored vector_json when present (the real, kept-current column);
    falls back to computing it inline only for rows a backfill has not reached
    yet, so `assess` never returns an artificially incomplete result while a
    backfill is pending."""
    placeholders = ",".join("?" * len(WIRING_SOURCE_ENTITY_TYPES))
    wiring_rows = conn.execute(
        f"SELECT entity_id, entity_type, path, source_ref, metadata_json, vector_json "
        f"FROM wiring_registry WHERE entity_type IN ({placeholders})",
        WIRING_SOURCE_ENTITY_TYPES,
    ).fetchall()
    cap_rows = conn.execute(
        "SELECT capability_id, capability_name, apis, workflow, owner, business_rules, vector_json "
        "FROM capability_registry"
    ).fetchall()

    candidates = []
    for r in wiring_rows:
        d = dict(r)
        tf = json.loads(d["vector_json"]) if d.get("vector_json") else vs.term_freq_vector(vs.text_for_wiring_row(d))
        candidates.append({"source": "wiring_registry", "id": d["entity_id"], "kind": d["entity_type"], "tf": tf})
    for r in cap_rows:
        d = dict(r)
        tf = json.loads(d["vector_json"]) if d.get("vector_json") else vs.term_freq_vector(vs.text_for_capability_row(d))
        candidates.append({
            "source": "capability_registry", "id": d["capability_id"],
            "kind": "capability:" + d["capability_name"], "tf": tf,
        })
    return candidates


def _lookup_cached_verdict(conn, intent_hash_val, now):
    row = conn.execute(
        "SELECT ts, metadata_json FROM wiring_registry WHERE entity_id = ?",
        (f"search_intent-{intent_hash_val}",),
    ).fetchone()
    if row is None:
        return None
    ts = datetime.fromisoformat(row["ts"])
    if now - ts > RECENT_HISTORY_WINDOW:
        return None  # real, not stale-trusted: outside the recent-history window, recompute
    meta = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
    cached = meta.get("verdict_result")
    if not cached:
        return None
    return {**cached, "cache_hit": True, "cached_ts": row["ts"]}


def _record_verdict(conn, sb, intent_hash_val, intent_text, result, now_iso):
    entity = {
        "entity_id": f"search_intent-{intent_hash_val}",
        "entity_type": "dispatch_event",
        "source_system": "server",
        "path": None,
        "relationships": [{
            "relationship_type": "search_intent_verdict",
            "verdict": result["verdict"],
            "best_match_id": (result["best_match"] or {}).get("id"),
            "score": result["score"],
        }],
        "last_verified_ts": now_iso,
        "verification_status": "VERIFIED_MATCH",
        "source_ref": [intent_hash_val],
        "metadata": {
            "intent_text": intent_text[:1000],
            "verdict_result": result,
        },
    }
    sb.register_entity_row(conn, entity)
    conn.commit()


def assess(conn, sb, intent_text, use_cache=True):
    """The real three-tier verdict for one search-intent text. Deterministic:
    the same intent_text against an unchanged registry state always returns
    the same verdict/score, by construction (sha256 intent hash, stable token
    vectors, stable IDF over the same candidate set)."""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    intent_hash_val = vs.intent_hash(intent_text)

    if use_cache:
        cached = _lookup_cached_verdict(conn, intent_hash_val, now)
        if cached is not None:
            return cached

    candidates = load_candidates(conn)
    idf = vs.build_idf([c["tf"] for c in candidates])
    query_tf = vs.term_freq_vector(intent_text)

    best = None
    best_score = 0.0
    for c in candidates:
        score = vs.cosine_similarity(query_tf, c["tf"], idf)
        if best is None or score > best_score:
            best = {"id": c["id"], "source": c["source"], "kind": c["kind"]}
            best_score = score

    if best_score >= vs.HIGH_SIMILARITY_THRESHOLD:
        verdict = "duplication_blocked"
    elif best_score >= vs.LOW_SIMILARITY_THRESHOLD:
        verdict = "reuse_mandated_extend"
    else:
        verdict = "create_authorized"

    result = {
        "intent_hash": intent_hash_val,
        "verdict": verdict,
        "score": round(best_score, 4),
        "best_match": best,
        "low_threshold": vs.LOW_SIMILARITY_THRESHOLD,
        "high_threshold": vs.HIGH_SIMILARITY_THRESHOLD,
        "candidates_considered": len(candidates),
        "cache_hit": False,
    }
    _record_verdict(conn, sb, intent_hash_val, intent_text, result, now_iso)
    return result


def backfill_vectors(conn, sb, execute=False):
    """One-time (safe to re-run; only touches rows still missing vector_json)
    catch-up for rows registered before the vector columns existed. Reuses the
    exact same write paths a real caller would (register_entity_row() directly
    for wiring rows; the real register-capability CLI, via a real record-file,
    for capability rows -- capability_registry has no equivalent bare row-level
    helper to call in-process without re-deriving UTM fields by hand, so this
    goes through the one real public entrypoint instead of reimplementing
    that derivation here). Read-only dry run by default, same convention as
    resource_governor.py's --reconcile-stale/--backfill-null-heartbeats."""
    placeholders = ",".join("?" * len(WIRING_SOURCE_ENTITY_TYPES))
    wiring_rows = conn.execute(
        f"SELECT * FROM wiring_registry WHERE entity_type IN ({placeholders}) "
        f"AND (vector_json IS NULL OR vector_json = '')",
        WIRING_SOURCE_ENTITY_TYPES,
    ).fetchall()
    for r in wiring_rows:
        d = dict(r)
        if execute:
            entity = {
                "entity_id": d["entity_id"], "entity_type": d["entity_type"],
                "source_system": d["source_system"], "path": d.get("path"),
                "relationships": json.loads(d["relationships"]) if d.get("relationships") else [],
                "last_verified_ts": d["last_verified_ts"], "verification_status": d["verification_status"],
                "source_ref": json.loads(d["source_ref"]) if d.get("source_ref") else [],
                "metadata": json.loads(d["metadata_json"]) if d.get("metadata_json") else {},
                "content_hash": d.get("content_hash"), "originating_umr": d.get("originating_umr"),
                "script_version": d.get("script_version"),
            }
            sb.register_entity_row(conn, entity)
    if execute:
        conn.commit()

    cap_rows = conn.execute(
        "SELECT * FROM capability_registry WHERE vector_json IS NULL OR vector_json = ''"
    ).fetchall()
    for r in cap_rows:
        d = dict(r)
        if execute:
            record = {
                "capability_name": d["capability_name"],
                "inputs": json.loads(d["inputs"]) if d.get("inputs") else [],
                "business_rules": json.loads(d["business_rules"]) if d.get("business_rules") else [],
                "workflow": d.get("workflow"), "automation": d.get("automation"),
                "documents": json.loads(d["documents"]) if d.get("documents") else None,
                "reports": json.loads(d["reports"]) if d.get("reports") else None,
                "apis": json.loads(d["apis"]) if d.get("apis") else [],
                "ui_screens": json.loads(d["ui_screens"]) if d.get("ui_screens") else None,
                "permissions": d["permissions"], "ai_required": bool(d["ai_required"]),
                "confidence": d["confidence"], "version": d["version"], "owner": d["owner"],
                "metadata": json.loads(d["metadata_json"]) if d.get("metadata_json") else {},
            }
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
                json.dump(record, f)
                tmp_path = f.name
            try:
                subprocess.run(
                    ["python3", SUPERBOSS_PATH, "register-capability", "--record-file", tmp_path],
                    capture_output=True, text=True, check=True,
                )
            finally:
                os.unlink(tmp_path)

    return {
        "wiring_rows_needing_backfill": len(wiring_rows),
        "capability_rows_needing_backfill": len(cap_rows),
        "executed": execute,
    }


def cmd_assess(args):
    sb = _load_superboss()
    conn = _connect(sb)
    result = assess(conn, sb, args.intent_text, use_cache=not args.no_cache)
    conn.close()
    print(json.dumps(result, indent=2, default=str))


def cmd_backfill_vectors(args):
    sb = _load_superboss()
    conn = _connect(sb)
    result = backfill_vectors(conn, sb, execute=args.execute)
    conn.close()
    print(json.dumps(result, indent=2, default=str))


def build_parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    p_assess = sub.add_parser("assess", help="real three-tier create/reuse/block verdict for one search-intent text")
    p_assess.add_argument("--intent-text", required=True)
    p_assess.add_argument("--no-cache", action="store_true", help="skip the recent-history idempotency cache")
    p_assess.set_defaults(func=cmd_assess)

    p_backfill = sub.add_parser("backfill-vectors", help="one-time catch-up for rows missing vector_json")
    p_backfill.add_argument("--execute", action="store_true", help="apply real writes (default: read-only dry run)")
    p_backfill.set_defaults(func=cmd_backfill_vectors)

    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
