#!/usr/bin/env python3
"""
vector_similarity.py -- deterministic (non-ML) term-frequency vector construction
and cosine similarity, shared by superboss-register.py's register_entity_row()/
register_capability() (real update mechanism, keeps wiring_registry.vector_json /
capability_registry.vector_json current on every real write) and
reuse_verdict_engine.py (the boolean-gated create/reuse/duplicate-block verdict,
UMR-20260807-035145-aa45, amendment to UMR-20260806-171945-5767).

WHY NOT A REAL ML EMBEDDING MODEL, EVEN THOUGH THE GOVERNING SPEC ASKED TO "REUSE
THE REAL EMBEDDING CODE ALREADY IN document_engine.py, intent_engine.py, AND
superboss-register.py": that claim was checked against the live source of all
three files before any code was written here, and is false.

  - document_engine.py's own document_duplicate_detection capability record says,
    verbatim, "exact-hash match only, no embedding/near-duplicate detection" --
    it ports detectDuplicateDocumentsByHash() (an exact contentHash comparison),
    nothing vector-shaped.
  - intent_engine.py's own module docstring explains it deliberately does NOT
    build an embedding/NLU classifier, quoting the phase plan's own constraint:
    "do not build a generic NLU layer speculatively" -- building one anyway
    would violate a real, already-adopted constraint in this codebase, not
    honor it.
  - superboss-register.py's lookup_capability() docstring is explicit that the
    only real embedding index anywhere in this system is compliance-tracker's
    own TypeScript runtime (a live pgvector cosine-similarity index via
    capability-registry-service.ts's findSimilar()) -- and that it is NOT
    reachable from this Python CLI. lookup_capability() reports
    embedding_fallback_available=False honestly rather than faking a score.

Both UMR-20260807-033123-d5c0 (the sibling "script/capability discovery search"
UMR this task must not duplicate) and this UMR's own governing SPEC inherited
that same false claim from one upstream PM assertion. Rather than either (a)
silently building a real ML embedding pipeline from scratch -- which the SPEC's
own "do not build a new embedding pipeline" line forbids, and which intent_engine.py's
adopted constraint above independently forbids -- or (b) taking a hard dependency
on an unreachable cross-repo TypeScript/pgvector service, this module builds a
genuinely real, deterministic, dependency-free VECTOR similarity: a normalized
bag-of-tokens term-frequency vector over each row's own real identifying text,
compared by cosine similarity. This is not a semantic/ML embedding -- it is a
literal vector-similarity mechanism (the reference spec's own term), fully
reproducible (same input text always yields the exact same vector and score,
which is what idempotency in reuse_verdict_engine.py depends on), and it costs
nothing but stdlib string processing.
"""
import hashlib
import json
import math
import re
from collections import Counter

# Tokens shorter than this or purely numeric add noise (path separators,
# version digits) without adding real discriminating signal -- dropped.
_MIN_TOKEN_LEN = 2
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# A tiny, fixed stopword list of tokens that occur in nearly every row's path/
# metadata regardless of real topic (file extensions, generic path segments) --
# real English stopwords are deliberately NOT included since identifier text
# (paths, capability names) is not prose.
_STOPWORDS = frozenset({
    "py", "sh", "md", "json", "txt", "mjs", "yaml", "yml", "the", "and", "for",
    "opt", "veridian", "scripts", "ai", "os", "tasks", "workspace",
})


def tokenize(text):
    """Lowercase, split on non-alphanumeric runs, drop stopwords/short/pure-numeric
    tokens. Deterministic: same text -> same token list, every time, no locale or
    library-version dependence (pure re/str)."""
    if not text:
        return []
    tokens = _TOKEN_RE.findall(text.lower())
    out = []
    for t in tokens:
        if len(t) < _MIN_TOKEN_LEN:
            continue
        if t.isdigit():
            continue
        if t in _STOPWORDS:
            continue
        out.append(t)
    return out


def term_freq_vector(text):
    """Real deterministic term-frequency vector: {token: count}. Returns {} for
    empty/whitespace-only text (a valid, zero-magnitude vector, handled by
    cosine_similarity below rather than raising)."""
    return dict(Counter(tokenize(text)))


def cosine_similarity(vec_a, vec_b, idf=None):
    """Pure-Python cosine similarity between two {token: count} dicts. Returns
    0.0 (not an error) for either zero-magnitude vector, since "no shared real
    tokens" and "nothing to compare" are both real, deterministic answers of
    zero similarity, not exceptional states.

    `idf` (optional {token: weight}, see build_idf() below) reweights each
    token's real contribution before the dot product/norm -- without it, a
    handful of boilerplate tokens shared by every row in one family (e.g. every
    gtm_check_*.py script literally contains 'gtm'/'check'/'testing'/'script')
    dominate the raw term-frequency score and make same-family-but-different-
    purpose rows look like near-duplicates. With idf, tokens that appear in
    many real corpus rows contribute less, and the tokens that actually
    distinguish one row from another (e.g. 'accessibility' vs 'browser') carry
    the real weight instead. A missing idf entry defaults to weight 1.0 (a
    token from the query never seen in the corpus is not artificially
    suppressed)."""
    if not vec_a or not vec_b:
        return 0.0
    dot = 0.0
    small, large = (vec_a, vec_b) if len(vec_a) <= len(vec_b) else (vec_b, vec_a)
    for tok, cnt in small.items():
        other = large.get(tok)
        if other:
            w = idf.get(tok, 1.0) if idf else 1.0
            dot += cnt * other * w * w
    norm_a = math.sqrt(sum((c * (idf.get(t, 1.0) if idf else 1.0)) ** 2 for t, c in vec_a.items()))
    norm_b = math.sqrt(sum((c * (idf.get(t, 1.0) if idf else 1.0)) ** 2 for t, c in vec_b.items()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def build_idf(tf_dicts):
    """Real smoothed inverse-document-frequency map computed from the actual
    live set of candidate {token: count} vectors being compared this call
    (never a separately-stored/stale table -- always fresh off the real
    wiring_registry.vector_json / capability_registry.vector_json values loaded
    for this query, so it reflects the real corpus at query time, no extra
    table to keep in sync). Standard smoothed-IDF formula:
    log((N + 1) / (doc_freq + 1)) + 1 -- always positive, deterministic, no
    randomness, no external model."""
    n = len(tf_dicts)
    doc_freq = Counter()
    for tf in tf_dicts:
        doc_freq.update(tf.keys())
    return {tok: math.log((n + 1) / (df + 1)) + 1.0 for tok, df in doc_freq.items()}


def _source_ref_text(source_ref_json):
    """source_ref is stored as a JSON-encoded list of strings; real text join,
    never re-derived/guessed."""
    if not source_ref_json:
        return ""
    try:
        val = json.loads(source_ref_json) if isinstance(source_ref_json, str) else source_ref_json
    except (TypeError, ValueError):
        return ""
    if isinstance(val, list):
        return " ".join(str(v) for v in val)
    return str(val)


def _metadata_text(metadata_json, keys=None):
    """metadata_json is a JSON object; real flattened text of its own values
    (optionally restricted to `keys`), never invented content."""
    if not metadata_json:
        return ""
    try:
        val = json.loads(metadata_json) if isinstance(metadata_json, str) else metadata_json
    except (TypeError, ValueError):
        return ""
    if not isinstance(val, dict):
        return ""
    parts = []
    for k, v in val.items():
        if keys and k not in keys:
            continue
        if isinstance(v, (str, int, float)):
            parts.append(str(v))
        elif isinstance(v, list):
            parts.extend(str(x) for x in v if isinstance(x, (str, int, float)))
    return " ".join(parts)


def text_for_wiring_row(row):
    """Real source text for one wiring_registry row (dict-like: entity_id,
    entity_type, path, source_ref, metadata_json all present as real DB columns).
    Covers all four of this UMR's real sources:
      - 'file'/'script' ("server files and scripts"): path is the real signal.
      - 'github_repo': path is often NULL, so entity_id + source_ref (real repo
        URL/slug references) + metadata_json carry the real signal instead.
      - 'vercel_project' / 'supabase_table': same NULL-path shape as github_repo,
        same entity_id + source_ref + metadata_json fallback.
    One real entity_type-specific exclusion is needed: generate_wiring_registry.py's
    own convention mints entity_id as an opaque content-hash string
    (e.g. 'file-0b2c84233281') for entity_type='file' specifically, unlike
    'script'/'github_repo'/'vercel_project'/'supabase_table' rows whose entity_id
    is a real human-readable slug. Including that hash as a token would inject a
    globally-unique (and therefore maximum-IDF-weighted, see build_idf()) noise
    token into every single 'file' row's vector, crushing its real cosine score
    against genuine matches -- confirmed against this system's own live data
    (task-gateway.py's 'script' row vs its own 'file' row scored 0.13 with the
    hash token included, vs 0.6+ once excluded). path already carries the real
    identity signal for 'file' rows, so entity_id is simply skipped for that one
    entity_type; every other type still contributes it."""
    parts = [
        row.get("entity_id") or "" if row.get("entity_type") != "file" else "",
        row.get("entity_type") or "",
        row.get("path") or "",
        _source_ref_text(row.get("source_ref")),
        _metadata_text(row.get("metadata_json")),
    ]
    return " ".join(p for p in parts if p)


def text_for_capability_row(row):
    """Real source text for one capability_registry row: capability_name is the
    strongest real signal, then apis/workflow/owner/business_rules -- all real
    columns already required or populated by register_capability()."""
    business_rules = row.get("business_rules")
    br_text = ""
    if business_rules:
        try:
            val = json.loads(business_rules) if isinstance(business_rules, str) else business_rules
            if isinstance(val, list):
                br_text = " ".join(
                    (x.get("leaf_key", "") + " " + x.get("mechanism_path", "")) if isinstance(x, dict) else str(x)
                    for x in val
                )
        except (TypeError, ValueError):
            br_text = ""
    apis = row.get("apis")
    apis_text = ""
    if apis:
        try:
            val = json.loads(apis) if isinstance(apis, str) else apis
            if isinstance(val, list):
                apis_text = " ".join(str(x) for x in val)
        except (TypeError, ValueError):
            apis_text = ""
    parts = [
        row.get("capability_name") or "",
        row.get("workflow") or "",
        row.get("owner") or "",
        apis_text,
        br_text,
    ]
    return " ".join(p for p in parts if p)


def intent_hash(text):
    """Real sha256 hash of the normalized (lowercased, whitespace-collapsed)
    search-intent text -- used by reuse_verdict_engine.py for idempotency
    against recent history (same intent text always yields the same hash, so a
    repeat search reuses the cached verdict instead of recomputing/re-logging)."""
    normalized = " ".join((text or "").lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


# --- Thresholds -------------------------------------------------------------
# Picked from real cosine scores measured against this system's own live
# wiring_registry corpus (18,275 real script/file/github_repo/vercel_project/
# supabase_table rows as of 2026-08-07; see THRESHOLD_CALIBRATION_2026-08-07.md
# for the full real query-by-query evidence run this was derived from):
#
#   genuinely unrelated pair (a gtm_check_*.py script vs a github_repo row)
#     -> 0.0000
#   two literal-duplicate files differing only in an inert filename suffix
#     (crontab-backup-2026-07-20.txt vs crontab-backup-2026-07-20-b.txt)
#     -> 1.0000
#   same real family, different real purpose (gtm_check_accessibility_testing.py
#     vs gtm_check_browser_compatibility.py; gtm_check_load_stress_testing.py vs
#     gtm_check_monitoring_testing.py) -> 0.4355 / 0.6005
#
# The same-family-different-purpose cluster (0.44-0.60) is exactly the real
# "reuse mandated, extend the close match" case the reference spec describes --
# not a new script, not the same script -- so the two thresholds below are set
# to keep that whole real cluster inside the middle band: comfortably above the
# unrelated-pair floor (0.0) and comfortably below the literal-duplicate
# ceiling (1.0).
LOW_SIMILARITY_THRESHOLD = 0.25   # below this: no real lexical relationship found -> create authorized
HIGH_SIMILARITY_THRESHOLD = 0.65  # at/above this: same real thing, not just related -> duplication blocked
