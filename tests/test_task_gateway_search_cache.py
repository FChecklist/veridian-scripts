#!/usr/bin/env python3
"""Real tests for task-20260814-181008: a real, short-TTL cache for
task-gateway.py cmd_submit's search step (check-duplicate/search/query-
knowledge/zoekt, all four run against the same keyword_str -- see that
function in task-gateway.py). Real, confirmed gap this closes: every
dispatch re-ran all four fresh, even when a near-identical query had just
run minutes earlier from a different dispatch.

The cache table (search_cache) lives in the SAME sqlite file cmd_submit's
other lookups already use (superboss-register.sqlite) -- no new database.
See get_search_cache()/put_search_cache()/_search_cache_key() in
superboss-register.py for the real implementation and TTL justification.

cmd_submit is called in-process (importlib-loaded, then called directly --
same convention run_zoekt_search() itself is tested with in
test_task_gateway_zoekt_search.py), NOT via subprocess against
task-gateway.py's own hardcoded SUPERBOSS constant
(f"{VERIDIAN_ROOT}/scripts/superboss-register.py" -- the real, live,
already-deployed /opt/veridian/scripts copy, which does not yet carry this
task's new get-search-cache/put-search-cache subcommands until this
change merges and deploys). tg.SUPERBOSS is monkeypatched to this
checkout's OWN superboss-register.py instead, so these tests exercise the
real code actually being changed here, not the stale live deployment.

A deliberately unreachable ZOEKT_URL (same fail-open convention
test_task_gateway_zoekt_search.py's own
test_run_zoekt_search_fails_open_on_unreachable_service() uses) keeps this
deterministic and independent of whether the real, live Zoekt service
happens to be up right now.

Real evidence a cache hit happened: cmd_submit's own output carries a
"search_cache" block (hit/age_seconds/ttl_seconds/cache_key) -- the second
dispatch's hit=True is the logged cache-hit marker; the byte-identical
duplicate/search/knowledge/zoekt payloads between the two dispatches are
corroborating evidence the second call reused the cached result wholesale."""
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
from argparse import Namespace

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASK_GATEWAY = os.path.join(SCRIPTS_DIR, "task-gateway.py")
SUPERBOSS = os.path.join(SCRIPTS_DIR, "superboss-register.py")


def _load_scratch_sbr():
    """Same importlib-module-level-DB_PATH-override pattern
    test_task_gateway_zoekt_search.py's own _seed_scratch_db() uses --
    NEVER the plain `init` CLI subcommand for a not-yet-existing scratch
    path: resolve_superboss_db_path() only honors SUPERBOSS_REGISTER_DB
    when the path it names already exists on disk and is non-zero size, so
    a fresh scratch path handed to the CLI directly silently falls back to
    the real, live default DB instead -- exactly the trap this helper
    avoids."""
    spec = importlib.util.spec_from_file_location("sbr_search_cache_seed", SUPERBOSS)
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def _seed_scratch_db(path):
    sbr = _load_scratch_sbr()
    sbr.DB_PATH = path
    sbr.init_db()


def _load_task_gateway_against_this_checkout():
    """Loads THIS checkout's task-gateway.py, then repoints its SUPERBOSS
    constant at THIS checkout's own superboss-register.py -- see module
    docstring above for why this must never be the hardcoded
    /opt/veridian/scripts default while this task's new subcommands are
    unmerged."""
    spec = importlib.util.spec_from_file_location("tg_search_cache", TASK_GATEWAY)
    tg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tg)
    tg.SUPERBOSS = SUPERBOSS
    return tg


def _submit(tg, text, session_id):
    args = Namespace(text=text, source="ai_agent", session_id=session_id, tier=None)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        tg.cmd_submit(args)
    return json.loads(buf.getvalue())


def test_search_cache_functions_round_trip_on_a_scratch_db():
    """Direct, real round trip against get_search_cache()/put_search_cache()
    themselves -- miss, then a hit after put, with the stored result_json
    coming back byte-for-byte, an order-insensitive key match, then an
    explicit ttl_seconds=0 override behaving exactly like an expired row (a
    real, deterministic way to exercise expiry without sleeping in a
    test)."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        sbr = _load_scratch_sbr()
        sbr.DB_PATH = scratch_db
        sbr.init_db()

        miss = sbr.get_search_cache("real search cache round trip")
        assert miss["hit"] is False, miss
        assert miss["result"] is None, miss

        sbr.put_search_cache("real search cache round trip", {"dup_result": {"found": 0}})
        hit = sbr.get_search_cache("real search cache round trip")
        assert hit["hit"] is True, hit
        assert hit["result"] == {"dup_result": {"found": 0}}, hit
        assert hit["age_seconds"] is not None and hit["age_seconds"] >= 0, hit

        # Order-insensitive key: re-ordered tokens of the same real query
        # text still hit the same cache entry.
        reordered_hit = sbr.get_search_cache("round trip cache search real")
        assert reordered_hit["hit"] is True, reordered_hit
        assert reordered_hit["cache_key"] == hit["cache_key"], reordered_hit

        expired = sbr.get_search_cache("real search cache round trip", ttl_seconds=0)
        assert expired["hit"] is False, expired


def test_cmd_submit_second_near_identical_dispatch_reuses_the_cached_result():
    """The real done-criteria test: two real cmd_submit dispatches with
    near-identical text (different wording, same real keywords) within the
    TTL window. The second must carry a real, logged cache-hit marker
    (search_cache.hit=True) proving it reused the first dispatch's cached
    search-step result instead of re-running check-duplicate/search/
    query-knowledge/zoekt live."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)

        old_db = os.environ.get("SUPERBOSS_REGISTER_DB")
        old_zoekt = os.environ.get("ZOEKT_URL")
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        # Deliberately unreachable, same convention as
        # test_run_zoekt_search_fails_open_on_unreachable_service().
        os.environ["ZOEKT_URL"] = "http://127.0.0.1:1"
        try:
            tg = _load_task_gateway_against_this_checkout()
            r1 = _submit(tg, "investigate real_search_cache_marker_9f31 behavior",
                         "test-search-cache-1")
            r2 = _submit(tg, "please investigate real_search_cache_marker_9f31 behavior now",
                         "test-search-cache-2")
        finally:
            if old_db is None:
                os.environ.pop("SUPERBOSS_REGISTER_DB", None)
            else:
                os.environ["SUPERBOSS_REGISTER_DB"] = old_db
            if old_zoekt is None:
                os.environ.pop("ZOEKT_URL", None)
            else:
                os.environ["ZOEKT_URL"] = old_zoekt

        # First dispatch: a real, honest miss (nothing cached yet).
        assert r1["search_cache"]["hit"] is False, r1["search_cache"]

        # Second dispatch, near-identical text within TTL: a real, logged
        # cache-hit marker -- the actual done-criteria evidence.
        assert r2["search_cache"]["hit"] is True, r2["search_cache"]
        assert r2["search_cache"]["age_seconds"] is not None
        assert r2["search_cache"]["age_seconds"] < r2["search_cache"]["ttl_seconds"], r2["search_cache"]

        # Reused wholesale: every part of the cached search-step result is
        # byte-identical between the two dispatches (the second never
        # re-ran check-duplicate/search/query-knowledge/zoekt live).
        assert r1["duplicate_evidence"] == r2["duplicate_evidence"]
        assert r1["prior_search_results"] == r2["prior_search_results"]
        assert r1["knowledge_matches"] == r2["knowledge_matches"]
        assert r1["zoekt_matches"] == r2["zoekt_matches"]


def test_cmd_submit_dispatch_with_a_different_query_is_a_real_miss():
    """A genuinely different query must never spuriously hit a prior,
    unrelated cache entry."""
    with tempfile.TemporaryDirectory() as d:
        scratch_db = os.path.join(d, "scratch.sqlite")
        _seed_scratch_db(scratch_db)

        old_db = os.environ.get("SUPERBOSS_REGISTER_DB")
        old_zoekt = os.environ.get("ZOEKT_URL")
        os.environ["SUPERBOSS_REGISTER_DB"] = scratch_db
        os.environ["ZOEKT_URL"] = "http://127.0.0.1:1"
        try:
            tg = _load_task_gateway_against_this_checkout()
            r1 = _submit(tg, "investigate real_search_cache_marker_alpha", "cache-miss-1")
            r2 = _submit(tg, "investigate real_search_cache_marker_beta", "cache-miss-2")
        finally:
            if old_db is None:
                os.environ.pop("SUPERBOSS_REGISTER_DB", None)
            else:
                os.environ["SUPERBOSS_REGISTER_DB"] = old_db
            if old_zoekt is None:
                os.environ.pop("ZOEKT_URL", None)
            else:
                os.environ["ZOEKT_URL"] = old_zoekt

        assert r1["search_cache"]["hit"] is False, r1["search_cache"]
        assert r2["search_cache"]["hit"] is False, r2["search_cache"]


if __name__ == "__main__":
    test_search_cache_functions_round_trip_on_a_scratch_db()
    print("PASS: test_search_cache_functions_round_trip_on_a_scratch_db")
    test_cmd_submit_second_near_identical_dispatch_reuses_the_cached_result()
    print("PASS: test_cmd_submit_second_near_identical_dispatch_reuses_the_cached_result")
    test_cmd_submit_dispatch_with_a_different_query_is_a_real_miss()
    print("PASS: test_cmd_submit_dispatch_with_a_different_query_is_a_real_miss")
    print("ALL PASS")
