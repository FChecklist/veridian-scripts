#!/usr/bin/env python3
"""capability_semantic_search.py -- UMR-20260806-124055-bc80 (owner standing
order 2026-08-07: "the deterministic search-for-existing-scripts mechanism
must work like a real semantic search, giving what is needed directly, not
requiring AI to manually grep or manually decide if a script exists").

WHY A NEW THIN WRAPPER (checked first, per this UMR's own instruction not to
build new embedding infrastructure from scratch if it already exists):

  - document_engine.py's own capability_registry row says outright:
    "exact-hash match only, no embedding/near-duplicate detection".
  - intent_engine.py's own module docstring says outright that it
    deliberately does NOT build an embedding/NLU layer (Phase 6 constraint:
    "do not build a generic NLU layer speculatively").
  - superboss-register.py's lookup_capability() docstring documents a real
    embedding-similarity mechanism -- but it is capability-registry-service.ts's
    findSimilar(), a pgvector cosine-similarity index living in
    compliance-tracker's own Postgres/TypeScript runtime, tenant/org-scoped to
    that SaaS product's own data model. Its own code says so explicitly:
    "not reachable from this Python CLI" / embedding_fallback_available=False.
    That service is real (verified: repos/compliance-tracker/src/lib/
    embeddings.ts really calls OpenRouter's /api/v1/embeddings with
    openai/text-embedding-3-small, cached by content-hash in Postgres) but it
    cannot be imported into this Python/SQLite ops registry -- different
    runtime, different database, different (tenant-scoped) data model.
  - capability_registry's own task_precedent_search row (CAP-20260806-182313-9028)
    claims a `search-task-precedent` CLI / search_task_precedent() function in
    superboss-register.py exists and says "reuse directly, do not rebuild" --
    but the LIVE, deployed /opt/veridian/scripts/superboss-register.py has no
    such subcommand (confirmed by running it: argparse lists the real
    subcommand set and search-task-precedent is not among them). The real code
    exists (repos/veridian-scripts db2f2d635a, blob a37a4a16) but only on the
    OPEN, UNMERGED PR #205 (worker/task-20260806-181146-...); the capability
    row was registered against a branch, not against what any other live
    script can actually call today. This module degrades gracefully either
    way: if search_task_precedent lands in the live script later, callers
    still get its FTS-based cross-history stage for free via lookup_capability's
    own resolution; this module never depends on it existing.

So: no local embedding mechanism was reachable to extend. What IS reachable
and real, from this exact environment, is the SAME underlying provider
compliance-tracker's embeddings.ts already validated in production --
OpenRouter's live /api/v1/embeddings endpoint (openai/text-embedding-3-small,
1536-dim), using the same OPENROUTER_API_KEY this box's own
anthropic_openrouter_proxy.py already calls for real, non-speculatively.
This module reuses that real, already-proven provider/model choice rather
than inventing a new one, and composes with (never re-implements) the two
existing deterministic tools:

  - superboss-register.py lookup-capability (exact-then-FTS over
    capability_registry) -- called via subprocess, unmodified.
  - wiring_query.py's query() (exact-then-FTS over wiring_registry +
    knowledge_engine) -- imported and called directly, unmodified.

On top of those two, this module adds the one piece that genuinely did not
exist anywhere reachable: real ranked-by-cosine-similarity semantic search
over every capability_registry row and every wiring_registry row whose
entity_type='script' (151 rows at reindex time; entity_type in
{file, function, dispatch_event, ...} -- 24k+ rows -- is out of scope for
this pass, logged explicitly below rather than silently dropped, since those
are not "does a script exist" candidates).

Embeddings are cached locally (sha256(text) keyed, same content-hash-cache
shape embeddings.ts already uses, new SQLite file so this never becomes a
second writer against superboss-register.sqlite) so a `search` after the one
`reindex` costs exactly one real embedding call (the query text) plus zero
network calls for every already-indexed row.

Honest-degradation convention (matches lookup_capability's own
embedding_fallback_available=False pattern): if OPENROUTER_API_KEY is unset
or the API call fails, `embedding_available` is reported False and only the
real deterministic (exact/FTS) matches are returned -- never a fabricated
score.

Subcommands:
    reindex                          -- (re)embed every capability_registry
                                         row + wiring_registry entity_type='script'
                                         row, real OpenRouter calls, cached.
    search --task-text "..." [--limit N]
                                      -- real ranked semantic search: exact/FTS
                                         stage-0 short-circuit, else cosine
                                         similarity over the cached vectors.
"""
import argparse
import hashlib
import importlib.util as _ilu
import json
import datetime
import math
import os
import sqlite3
import sys
import urllib.error
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EMBED_MODEL = "openai/text-embedding-3-small"
OPENROUTER_URL = "https://openrouter.ai/api/v1/embeddings"
CACHE_DB_PATH = os.environ.get(
    "CAPABILITY_SEMANTIC_SEARCH_CACHE_DB",
    "/opt/veridian/ai-os/memory/capability_embedding_cache.sqlite",
)
# Scope for this pass -- see module docstring. Not a silent cap: reindex
# prints exactly how many wiring_registry rows were skipped and why.
WIRING_ENTITY_TYPES_IN_SCOPE = ("script",)

_sbr_spec = _ilu.spec_from_file_location("superboss_register", os.path.join(SCRIPT_DIR, "superboss-register.py"))
_sbr = _ilu.module_from_spec(_sbr_spec)
_sbr_spec.loader.exec_module(_sbr)

_wq_spec = _ilu.spec_from_file_location("wiring_query", os.path.join(SCRIPT_DIR, "wiring_query.py"))
_wq = _ilu.module_from_spec(_wq_spec)
_wq_spec.loader.exec_module(_wq)


# ---------------------------------------------------------------------------
# Local embedding cache (own SQLite file -- never a second writer against
# superboss-register.sqlite, matching this codebase's one-script-one-writer
# discipline for that DB).
# ---------------------------------------------------------------------------

def _cache_connect():
    os.makedirs(os.path.dirname(CACHE_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(CACHE_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS embedding_cache ("
        " content_hash TEXT PRIMARY KEY,"
        " text TEXT NOT NULL,"
        " model TEXT NOT NULL,"
        " embedding TEXT NOT NULL,"
        " created_ts TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS embedding_index ("
        " source_table TEXT NOT NULL,"
        " source_id TEXT NOT NULL,"
        " content_hash TEXT NOT NULL,"
        " path TEXT,"
        " label TEXT,"
        " indexed_ts TEXT NOT NULL,"
        " PRIMARY KEY (source_table, source_id))"
    )
    conn.commit()
    return conn


def _content_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fetch_embeddings_real(texts):
    """Real OpenRouter /api/v1/embeddings call (batched, up to len(texts) in
    one request -- same endpoint/model repos/compliance-tracker/src/lib/
    embeddings.ts already validated live). Returns (vectors, error) -- vectors
    is None on any failure, error is a short human-readable string; never
    raises, matches this codebase's honest-degradation convention."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None, "OPENROUTER_API_KEY not set in environment"
    req = urllib.request.Request(
        OPENROUTER_URL,
        method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        data=json.dumps({"model": EMBED_MODEL, "input": texts}).encode("utf-8"),
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        vectors = [row["embedding"] for row in sorted(data["data"], key=lambda r: r["index"])]
        return vectors, None
    except urllib.error.HTTPError as e:
        return None, f"HTTPError {e.code}: {e.read()[:300]!r}"
    except Exception as e:  # noqa: BLE001 -- honest degrade, never fabricate
        return None, f"{type(e).__name__}: {e}"


def embed_texts(texts, cache_conn, batch_size=100):
    """Returns {text: vector} for every text, using the cache first and only
    calling the real API for cache misses, batched. Returns (vectors_by_text,
    error) -- error is set (and vectors_by_text may be partial) if any real
    call failed; callers must check it rather than assume every text got a
    vector."""
    result = {}
    misses = []
    for t in texts:
        h = _content_hash(t)
        row = cache_conn.execute("SELECT embedding FROM embedding_cache WHERE content_hash = ?", (h,)).fetchone()
        if row:
            result[t] = json.loads(row["embedding"])
        else:
            misses.append(t)

    error = None
    for i in range(0, len(misses), batch_size):
        batch = misses[i:i + batch_size]
        vectors, err = _fetch_embeddings_real(batch)
        if vectors is None:
            error = err
            break
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        for t, v in zip(batch, vectors):
            cache_conn.execute(
                "INSERT OR REPLACE INTO embedding_cache (content_hash, text, model, embedding, created_ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (_content_hash(t), t, EMBED_MODEL, json.dumps(v), now),
            )
            result[t] = v
        cache_conn.commit()
    return result, error


def cosine_similarity(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Row -> embeddable text, per source table.
# ---------------------------------------------------------------------------

def _capability_row_text(row):
    parts = [
        row.get("capability_name") or "",
        row.get("owner") or "",
        " ".join(row.get("apis") or []) if isinstance(row.get("apis"), list) else str(row.get("apis") or ""),
        row.get("workflow") or "",
        row.get("permissions") or "",
    ]
    br = row.get("business_rules")
    if isinstance(br, list):
        parts.append(" ".join(json.dumps(r) if not isinstance(r, str) else r for r in br))
    return " -- ".join(p for p in parts if p)


def _wiring_script_row_text(row):
    meta = {}
    try:
        meta = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
    except (TypeError, json.JSONDecodeError):
        pass
    return " -- ".join(p for p in [row["entity_id"], row["path"], meta.get("purpose", "")] if p)


def _load_capability_rows():
    conn = sqlite3.connect(f"file:{_sbr.DB_PATH}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    rows = [_sbr._capability_row_to_dict(r) for r in conn.execute("SELECT * FROM capability_registry").fetchall()]
    conn.close()
    return rows


def _load_wiring_script_rows():
    conn = sqlite3.connect(f"file:{_sbr.DB_PATH}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    placeholders = ",".join("?" for _ in WIRING_ENTITY_TYPES_IN_SCOPE)
    in_scope = conn.execute(
        f"SELECT * FROM wiring_registry WHERE entity_type IN ({placeholders})",
        WIRING_ENTITY_TYPES_IN_SCOPE,
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) n FROM wiring_registry").fetchone()["n"]
    conn.close()
    return [dict(r) for r in in_scope], total


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_reindex(_args):
    cache_conn = _cache_connect()
    cap_rows = _load_capability_rows()
    wiring_rows, wiring_total = _load_wiring_script_rows()

    texts_and_meta = []
    for r in cap_rows:
        texts_and_meta.append(("capability_registry", r["capability_id"], _capability_row_text(r), None, r["capability_name"]))
    for r in wiring_rows:
        texts_and_meta.append(("wiring_registry", r["entity_id"], _wiring_script_row_text(r), r["path"], r["entity_id"]))

    texts = [t[2] for t in texts_and_meta]
    vectors_by_text, error = embed_texts(texts, cache_conn)

    indexed = 0
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for source_table, source_id, text, path, label in texts_and_meta:
        if text not in vectors_by_text:
            continue
        cache_conn.execute(
            "INSERT OR REPLACE INTO embedding_index (source_table, source_id, content_hash, path, label, indexed_ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (source_table, source_id, _content_hash(text), path, label, now),
        )
        indexed += 1
    cache_conn.commit()
    cache_conn.close()

    skipped_wiring = wiring_total - len(wiring_rows)
    print(json.dumps({
        "indexed": indexed,
        "capability_registry_rows": len(cap_rows),
        "wiring_registry_rows_in_scope": len(wiring_rows),
        "wiring_registry_entity_types_in_scope": list(WIRING_ENTITY_TYPES_IN_SCOPE),
        "wiring_registry_rows_out_of_scope": skipped_wiring,
        "out_of_scope_note": (
            f"{skipped_wiring} wiring_registry rows with entity_type not in "
            f"{WIRING_ENTITY_TYPES_IN_SCOPE} (file/function/dispatch_event/etc.) "
            "were not embedded this pass -- logged, not silently dropped; "
            "extend WIRING_ENTITY_TYPES_IN_SCOPE to widen coverage."
        ),
        "embedding_error": error,
        "cache_db": CACHE_DB_PATH,
    }, indent=2))
    return 0 if error is None else 1


def cmd_search(args):
    limit = args.limit
    query_text = args.task_text

    # Stage 0: real deterministic exact/FTS matches from the two existing,
    # unmodified tools -- always run first and always reported, so an exact
    # hit is never hidden behind a similarity score.
    cap_lookup = _run_lookup_capability(query_text)
    wiring_lookup = _wq.query(query_text)

    cache_conn = _cache_connect()
    vectors_by_text, error = embed_texts([query_text], cache_conn)
    query_vector = vectors_by_text.get(query_text)

    ranked = []
    if query_vector is not None:
        idx_rows = cache_conn.execute(
            "SELECT i.source_table, i.source_id, i.path, i.label, c.embedding, c.text "
            "FROM embedding_index i JOIN embedding_cache c ON c.content_hash = i.content_hash"
        ).fetchall()
        for r in idx_rows:
            vec = json.loads(r["embedding"])
            score = cosine_similarity(query_vector, vec)
            ranked.append({
                "source_table": r["source_table"],
                "source_id": r["source_id"],
                "path": r["path"],
                "label": r["label"],
                "similarity_score": round(score, 6),
            })
        ranked.sort(key=lambda m: m["similarity_score"], reverse=True)
        ranked = ranked[:limit]
    cache_conn.close()

    print(json.dumps({
        "task_text": query_text,
        "deterministic_stage": {
            "capability_registry": {
                "found": cap_lookup.get("found", False),
                "resolution_stage_used": cap_lookup.get("resolution_stage_used"),
                "matches": [m.get("capability_name") for m in cap_lookup.get("matches", [])],
            },
            "wiring_registry": {
                "stage": wiring_lookup["wiring_registry"]["stage"],
                "count": wiring_lookup["wiring_registry"]["count"],
            },
        },
        "embedding_available": query_vector is not None,
        "embedding_error": error,
        "embedding_model": EMBED_MODEL,
        "ranked_semantic_matches": ranked,
    }, indent=2))
    return 0 if query_vector is not None else 1


def _run_lookup_capability(intent_text):
    import subprocess
    out = subprocess.run(
        [sys.executable, os.path.join(SCRIPT_DIR, "superboss-register.py"), "lookup-capability", "--intent-text", intent_text],
        capture_output=True, text=True,
    )
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return {"found": False, "matches": [], "resolution_stage_used": "error", "stderr": out.stderr[:500]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("reindex", help="(re)embed every capability_registry row + in-scope wiring_registry rows")

    p_search = sub.add_parser("search", help="real ranked semantic search over both registries")
    p_search.add_argument("--task-text", required=True, help="free-text task/script description to search for")
    p_search.add_argument("--limit", type=int, default=10, help="max ranked semantic matches to return (default 10)")

    args = parser.parse_args()
    if args.cmd == "reindex":
        return cmd_reindex(args)
    if args.cmd == "search":
        return cmd_search(args)
    parser.error("unknown command")


if __name__ == "__main__":
    sys.exit(main())
