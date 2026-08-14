# PROGRESS -- task-20260814-181008-cache-recent-search-results-with-a-real

## Completed
- [x] Investigated real gap: `task-gateway.py` `cmd_submit()` re-runs `check-duplicate`,
      `search`, `query-knowledge` (superboss-register.sqlite FTS5) and `run_zoekt_search`
      (live Zoekt HTTP call) fresh on every dispatch, keyed on the same `keyword_str`.
- [x] Added a real `search_cache` table to `superboss-register.py`, reusing the existing
      `superboss-register.sqlite` file (no new database):
      `_ensure_search_cache_table()`, wired into `_migrate_schema()` (existing-DB migration
      convention, same as `_ensure_governance_cycle_log_table` etc).
- [x] Added `_search_cache_key()` (order-insensitive, normalized sha256 -- reordered
      keyword extraction from two different dispatches of the same real text still hits
      the same entry), `get_search_cache()`, `put_search_cache()`, plus CLI subcommands
      `get-search-cache` / `put-search-cache` (argparse + dispatch wiring).
- [x] TTL = 5 minutes, `SEARCH_CACHE_TTL_SECONDS` (env override
      `VERIDIAN_SEARCH_CACHE_TTL_S`, same convention as `EXTERNAL_AGENT_DISPATCH_TTL_HOURS`
      / resource_governor.py's `HEARTBEAT_STALE_TTL_SECONDS`). Justification (in code
      comment): covers the real "different dispatch minutes later" duplicate-burst window
      this feature targets, while staying well under `check_content_duplicate`'s 24h and
      `check_target_identifier_duplicate`'s 4h windows so newly registered
      instructions/knowledge/capabilities are never masked for long.
- [x] Wired the cache into `task-gateway.py` `cmd_submit()`: on a hit, reuses the cached
      combined result (`dup_result`/`search_result`/`knowledge_result`/`zoekt_result`) and
      skips all 4 live calls; on a miss/expiry, runs live as before then populates the
      cache. Added `get_search_cache_result()` / `put_search_cache_result()` fail-open
      wrappers (same convention as `run_zoekt_search`'s fail-open design) so a cache
      hiccup (or deploy-ordering skew) never blocks or crashes `cmd_submit`.
- [x] Added a `"search_cache"` block to `cmd_submit`'s JSON output
      (`hit`/`age_seconds`/`ttl_seconds`/`cache_key`) as the real, logged evidence marker.
- [x] Real tests: `tests/test_task_gateway_search_cache.py` -- direct round-trip of
      `get_search_cache`/`put_search_cache` (incl. order-insensitive key, TTL-expiry via
      `ttl_seconds=0`), plus two real `cmd_submit()` dispatches with near-identical text:
      first is an honest miss, second is a real, logged `hit=True` with byte-identical
      reused duplicate/search/knowledge/zoekt payloads (the second never re-ran the live
      calls). A third test confirms a genuinely different query is a real miss (no
      spurious cross-hit).
- [x] Fixed a self-caused regression: the new `get-search-cache`/`put-search-cache`
      subcommands don't exist yet on the live, already-deployed `/opt/veridian/scripts`
      copy `task-gateway.py` hardcodes its `SUPERBOSS` path to -- made the cache calls
      fail-open so `cmd_submit` still works (falls through to a live miss) against an
      un-upgraded `superboss-register.py`. Re-verified `tests/test_task_gateway_zoekt_search.py`
      (pre-existing, unrelated test) still passes.
- [x] Verified: `python3 -m ast` syntax check on both files; manual CLI round-trip against
      a scratch DB; `tests/test_task_gateway_search_cache.py` and
      `tests/test_task_gateway_zoekt_search.py` both pass; a 62-test regression sample
      (`test_resolve_superboss_db_path.py`, `test_query_umr_by_id.py`,
      `test_external_agent_dispatch.py`, `test_ocid_canonical_registry.py`,
      `test_target_identifier_dedup.py`) passes.
- [x] Caught and cleaned up a real slip during manual testing: the CLI `init` subcommand
      against a not-yet-existing `SUPERBOSS_REGISTER_DB` path silently falls back to the
      real, live default DB (`resolve_superboss_db_path()` only honors the env override
      once the path already exists and is non-zero size) -- one test row was briefly
      written to and immediately deleted from the real, live
      `/opt/veridian/ai-os/memory/superboss-register.sqlite` (`search_cache` table itself
      -- an empty, additive schema table -- was left in place, matching how every other
      migration in this file behaves against the live DB). All real test code afterward
      uses the `_seed_scratch_db()`-style importlib module-level `DB_PATH` override
      instead, same convention `tests/test_task_gateway_zoekt_search.py` already used.

## Remaining
- [ ] Full `tests/` directory regression run in progress (background, `pytest tests/ -q`)
      -- confirm no unrelated collateral failures once it completes.
- [ ] Commit + push.
- [ ] `agent_work_briefing.py record-completion` for UMR-20260814-180949-806f.
