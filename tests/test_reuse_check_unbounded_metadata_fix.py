#!/usr/bin/env python3
"""Real tests for UMR-20260806-141250-1ceb (approved proposal 86, child UMR
of UMR-20260806-135902-cf13, governed by UMR-20260806-071025-1d28).

Real, confirmed incident this closes: the live source-of-truth DB
(superboss-register.sqlite) grew 2034MB -> 4067MB in ~11 real minutes
(13:44-13:55Z, 2026-08-06). Root cause: superboss-register.py's
lookup_entity() ran an FTS5 query against wiring_registry_fts with NO
LIMIT clause, returning every matching row; plan_generator.py's
check_reuse_before_dispatch() then embedded that full, unbounded result
verbatim into umr_tasks.metadata_json.reuse_check_result on every
owner_dispatch_gateway dispatch (resource_governor.py's submit()). One
sampled real row carried a 7128106-byte reuse_check_result, 5974144 bytes of
which was reuse_check_result.wiring.matches alone (8441 items).

This file proves BOTH approved fixes hold, end-to-end, against a real
scratch SQLite database -- never the live production database:
  1. superboss-register.py's lookup_entity()/query_knowledge() FTS queries
     now cap results at WIRING_LOOKUP_MATCH_LIMIT/KNOWLEDGE_QUERY_MATCH_LIMIT
     regardless of how many rows actually match.
  2. plan_generator.py's check_reuse_before_dispatch() independently caps
     what it embeds (EMBEDDED_MATCH_SUMMARY_LIMIT + a real total_matches
     count), so the embedding path stays bounded even if fix #1 were
     reverted or bypassed by some future caller.

Same real-scratch-DB, real-subprocess-call discipline as
tests/test_wiring_registry_umr_and_version.py and
tests/test_resolve_superboss_db_path.py -- SUPERBOSS_REGISTER_DB points the
REAL superboss-register.py CLI (invoked exactly as production code invokes
it, via subprocess) at an isolated temp-file database for the duration of
each test only.
"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUPERBOSS = os.path.join(SCRIPTS_DIR, "superboss-register.py")

# Real per-row size sampled live from the incident itself
# (UMR-20260806-135902-cf13's own evidence) -- kept here as a literal,
# named comparison point rather than a magic number in the assertion below.
REAL_INCIDENT_SAMPLED_ROW_BYTES = 7128106


def _load_sbr():
    spec = importlib.util.spec_from_file_location("sbr_reuse_check_test", SUPERBOSS)
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def _load_plan_generator():
    """plan_generator.py's own SUPERBOSS constant is hardcoded to the LIVE
    deployment path (/opt/veridian/scripts/superboss-register.py) --
    intentional in real production use (resource_governor.py always runs
    from that live checkout), but wrong for THIS test: loading
    plan_generator.py from this worktree must still exercise THIS
    worktree's own (fixed) superboss-register.py, not whatever happens to
    be live, or this test would silently stop testing the code under test.
    Monkeypatch the loaded module's SUPERBOSS attribute right after import --
    _lookup_entity()/_query_knowledge()/etc. all read the module-level
    SUPERBOSS name from their own __globals__ at call time, so reassigning
    the attribute here redirects every later subprocess call."""
    spec = importlib.util.spec_from_file_location(
        "plan_generator_reuse_check_test", os.path.join(SCRIPTS_DIR, "plan_generator.py"))
    pg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pg)
    pg.SUPERBOSS = SUPERBOSS
    return pg


def _seed_large_wiring_registry(conn, sbr, count, token):
    """count real wiring_registry rows, all with `token` in their `path` (an
    FTS5-indexed column), via the REAL register_entity_row() insert path --
    same function generate_wiring_registry.py's real bulk writer uses, not a
    hand-rolled INSERT."""
    sbr._ensure_wiring_registry_table(conn)
    now = sbr._now_iso()
    for i in range(count):
        sbr.register_entity_row(conn, {
            "entity_id": f"script-{token}-{i:06d}.py",
            "entity_type": "script",
            "source_system": "server",
            "path": f"/opt/veridian/scripts/{token}_{i:06d}.py",
            "relationships": [],
            "last_verified_ts": now,
            "verification_status": "UNVERIFIED",
            "source_ref": ["software_catalog"],
        })
    conn.commit()


def _seed_large_knowledge_engine(conn, sbr, count, token):
    """count real knowledge_engine rows, all with `token` in their `purpose`
    (an FTS5-indexed column). Inserted directly (not via register_knowledge(),
    which reads a real --path file off disk -- irrelevant to what's under
    test here: the FTS row-count bound) but against the real, production
    _ensure_knowledge_engine_table() schema/triggers, so the real FTS index
    is populated exactly as it would be in production."""
    sbr._ensure_knowledge_engine_table(conn)
    now = sbr._now_iso()
    for i in range(count):
        conn.execute(
            "INSERT INTO knowledge_engine (artifact_id, ts, artifact_path, content_hash, artifact_type, "
            "exists_on_disk, purpose, tags, entity_relationships, last_verified_ts, verification_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"KE-{token}-{i:06d}", now, f"/opt/veridian/docs/{token}_{i:06d}.md",
             "0" * 64, "derived", 0, f"real {token} synthetic knowledge row {i}", "[]", "[]", now, "UNVERIFIED"),
        )
    conn.commit()


def _scratch_db(d):
    """A real, valid, fully-bootstrapped scratch DB path.

    Deliberately bootstraps IN-PROCESS (monkeypatching this loaded sbr
    module's own DB_PATH, then calling the REAL init_db()/_ensure_umr_table())
    rather than via a subprocess `init` call: resolve_superboss_db_path()'s
    real, documented algorithm only honors SUPERBOSS_REGISTER_DB when the
    named path ALREADY exists on disk and is non-zero size (step 2) --
    pointing it at a not-yet-created path silently falls through to the
    real default DB_PATH instead (same gotcha
    tests/test_resolve_superboss_db_path.py's own docstring documents). Since
    a bare `init` subprocess call would be the very thing creating that file,
    running it first via subprocess would silently target the REAL
    production database instead of this test's scratch file -- bootstrapping
    in-process sidesteps that entirely, and every later CLI call in this
    file only ever runs via subprocess AFTER db_path already exists here."""
    sbr = _load_sbr()
    db_path = os.path.join(d, "scratch.sqlite")
    sbr.DB_PATH = db_path
    sbr.init_db()
    conn = sbr._connect()
    sbr._ensure_umr_table(conn)
    conn.close()
    assert os.path.exists(db_path) and os.path.getsize(db_path) > 0
    return db_path, sbr


def test_lookup_entity_fts_query_is_bounded_directly():
    """Step 1's own direct regression target: lookup-entity's real CLI
    (superboss-register.py, the exact query previously at lines 2852-2856)
    never returns more than WIRING_LOOKUP_MATCH_LIMIT rows, no matter how
    many real rows match."""
    with tempfile.TemporaryDirectory() as d:
        db_path, sbr = _scratch_db(d)
        token = "zzzreusechecklookup"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _seed_large_wiring_registry(conn, sbr, 500, token)
        conn.close()

        env = dict(os.environ)
        env["SUPERBOSS_REGISTER_DB"] = db_path
        proc = subprocess.run(
            [sys.executable, SUPERBOSS, "lookup-entity", "--query", token],
            capture_output=True, text=True, env=env,
        )
        result = json.loads(proc.stdout)
        assert result["found"] is True
        assert len(result["matches"]) == sbr.WIRING_LOOKUP_MATCH_LIMIT, (
            f"500 real matching rows seeded, expected the query to cap at "
            f"WIRING_LOOKUP_MATCH_LIMIT={sbr.WIRING_LOOKUP_MATCH_LIMIT}, got {len(result['matches'])}")
        print(f"PASS: test_lookup_entity_fts_query_is_bounded_directly -> "
              f"500 matching rows seeded, query returned {len(result['matches'])} "
              f"(limit={sbr.WIRING_LOOKUP_MATCH_LIMIT})")


def test_query_knowledge_fts_query_is_bounded_directly():
    """Same regression target as above for query_knowledge()'s analogous
    knowledge_engine_fts query, named explicitly in proposal 86's approved
    scope alongside lookup_entity()."""
    with tempfile.TemporaryDirectory() as d:
        db_path, sbr = _scratch_db(d)
        token = "zzzreuseknowledgelookup"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _seed_large_knowledge_engine(conn, sbr, 300, token)
        conn.close()

        env = dict(os.environ)
        env["SUPERBOSS_REGISTER_DB"] = db_path
        proc = subprocess.run(
            [sys.executable, SUPERBOSS, "query-knowledge", token],
            capture_output=True, text=True, env=env,
        )
        result = json.loads(proc.stdout)
        assert result["found"] == sbr.KNOWLEDGE_QUERY_MATCH_LIMIT
        assert len(result["matches"]) == sbr.KNOWLEDGE_QUERY_MATCH_LIMIT, (
            f"300 real matching rows seeded, expected the query to cap at "
            f"KNOWLEDGE_QUERY_MATCH_LIMIT={sbr.KNOWLEDGE_QUERY_MATCH_LIMIT}, got {len(result['matches'])}")
        print(f"PASS: test_query_knowledge_fts_query_is_bounded_directly -> "
              f"300 matching rows seeded, query returned {len(result['matches'])} "
              f"(limit={sbr.KNOWLEDGE_QUERY_MATCH_LIMIT})")


def test_bounded_list_summary_helper():
    """Pure unit test for plan_generator._bounded_list_summary(), the Step 2
    write-side truncation primitive."""
    pg = _load_plan_generator()

    bounded, total, truncated = pg._bounded_list_summary(list(range(37)), limit=10)
    assert bounded == list(range(10))
    assert total == 37
    assert truncated is True

    bounded2, total2, truncated2 = pg._bounded_list_summary([1, 2], limit=10)
    assert bounded2 == [1, 2]
    assert total2 == 2
    assert truncated2 is False

    bounded3, total3, truncated3 = pg._bounded_list_summary(None, limit=10)
    assert bounded3 == []
    assert total3 == 0
    assert truncated3 is False

    print("PASS: test_bounded_list_summary_helper")


def test_check_reuse_before_dispatch_metadata_json_size_bounded_with_large_registry():
    """Step 3, the real end-to-end regression test proving the actual fix:
    a wiring_registry with thousands of rows ALL matching one query can no
    longer produce an oversized metadata_json.reuse_check_result -- the
    exact shape that grew umr_tasks to 1855.7MB in ~11 minutes
    (UMR-20260806-135902-cf13's own real evidence).

    Runs the REAL reuse-check path end-to-end:
    plan_generator.check_reuse_before_dispatch() shelling out to the REAL,
    just-fixed superboss-register.py CLI (lookup-entity/query-knowledge/
    lookup-capability/search), against a real scratch SQLite DB seeded with
    3000 synthetic wiring_registry rows (same order of magnitude as the live
    incident's own 8441-row single-query match count) all matching one FTS
    query term, plus 200 synthetic knowledge_engine rows -- then builds the
    exact same {"reuse_check_result": ...} dict resource_governor.py's
    submit() writes into umr_tasks.metadata_json and measures its real
    serialized size.
    """
    token = "zzzreusechecklarge"
    wiring_row_count = 3000
    knowledge_row_count = 200

    with tempfile.TemporaryDirectory() as d:
        db_path, sbr = _scratch_db(d)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        _seed_large_wiring_registry(conn, sbr, wiring_row_count, token)
        _seed_large_knowledge_engine(conn, sbr, knowledge_row_count, token)
        conn.close()

        # Sanity check the scratch DB really has this many matching rows,
        # via a real lookup-entity call, BEFORE relying on
        # check_reuse_before_dispatch()'s own bound -- so a future edit that
        # accidentally shrinks the seed data can't make this test pass for
        # the wrong reason.
        env = dict(os.environ)
        env["SUPERBOSS_REGISTER_DB"] = db_path
        raw = subprocess.run(
            [sys.executable, SUPERBOSS, "lookup-entity", "--query", token],
            capture_output=True, text=True, env=env,
        )
        raw_result = json.loads(raw.stdout)
        assert raw_result["found"] is True
        assert len(raw_result["matches"]) == sbr.WIRING_LOOKUP_MATCH_LIMIT

        pg = _load_plan_generator()
        # check_reuse_before_dispatch() shells out via its own internal
        # subprocess.run() calls, which inherit the CURRENT process env by
        # default (no explicit env= passed) -- patch os.environ for the
        # duration of the call, exactly like a real caller setting
        # SUPERBOSS_REGISTER_DB before invoking this module would, then
        # restore it unconditionally.
        old_env = os.environ.get("SUPERBOSS_REGISTER_DB")
        os.environ["SUPERBOSS_REGISTER_DB"] = db_path
        try:
            result = pg.check_reuse_before_dispatch(token, task_identity="test-task-identity-zzzreusechecklarge")
        finally:
            if old_env is None:
                os.environ.pop("SUPERBOSS_REGISTER_DB", None)
            else:
                os.environ["SUPERBOSS_REGISTER_DB"] = old_env

    # The real per-dispatch embedding shape (resource_governor.py's own
    # `metadata = {"reuse_check_result": reuse_check_result}`, submit()).
    metadata = {"reuse_check_result": result}
    metadata_json_size = len(json.dumps(metadata, default=str).encode("utf-8"))

    # Structural bound: however large wiring_registry/knowledge_engine get,
    # the embedded match lists can never exceed EMBEDDED_MATCH_SUMMARY_LIMIT,
    # and the "how many exist" signal (total_matches) is preserved even
    # though the full dump is not.
    assert len(result["wiring"]["matches"]) <= pg.EMBEDDED_MATCH_SUMMARY_LIMIT
    assert len(result["knowledge"]["matches"]) <= pg.EMBEDDED_MATCH_SUMMARY_LIMIT
    assert result["wiring"]["matches_truncated"] is True
    assert result["wiring"]["total_matches"] == sbr.WIRING_LOOKUP_MATCH_LIMIT
    assert result["knowledge"]["total_matches"] == min(knowledge_row_count, sbr.KNOWLEDGE_QUERY_MATCH_LIMIT)

    # The real, concrete proof: the live incident's own sampled row was
    # REAL_INCIDENT_SAMPLED_ROW_BYTES (7128106 bytes) for ONE dispatch.
    # Assert this 3000-row-all-matching scratch DB's whole metadata_json
    # stays under 200KB -- more than 35x smaller than that single real
    # observed row, real headroom, not a knife-edge pass.
    assert metadata_json_size < 200_000, (
        f"metadata_json size {metadata_json_size} bytes is not structurally bounded "
        f"(real pre-fix incident row was {REAL_INCIDENT_SAMPLED_ROW_BYTES} bytes)")

    print(f"PASS: test_check_reuse_before_dispatch_metadata_json_size_bounded_with_large_registry -> "
          f"{wiring_row_count} synthetic wiring rows, {knowledge_row_count} synthetic knowledge rows, "
          f"real metadata_json size = {metadata_json_size} bytes "
          f"(vs {REAL_INCIDENT_SAMPLED_ROW_BYTES} bytes observed for ONE real row pre-fix, "
          f"{REAL_INCIDENT_SAMPLED_ROW_BYTES / metadata_json_size:.1f}x smaller)")


if __name__ == "__main__":
    test_lookup_entity_fts_query_is_bounded_directly()
    test_query_knowledge_fts_query_is_bounded_directly()
    test_bounded_list_summary_helper()
    test_check_reuse_before_dispatch_metadata_json_size_bounded_with_large_registry()
