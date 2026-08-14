# PROGRESS -- task-20260814-183604-sweep-veridian-scripts-for-real-audited

Governing chain: UMR-20260806-124055-bc80

## False-premise finding (verified independently before writing any code)

The dispatch's claimed precedent -- "document_engine.py, intent_engine.py,
and superboss-register.py already contain real embedding or vector-similarity
code" -- is **false**, confirmed by reading each file directly:

- `document_engine.py`'s own registered capability row says outright:
  `"permissions": "exact-hash match only, no embedding/near-duplicate detection"`.
- `intent_engine.py`'s own module docstring says it deliberately does **not**
  build an embedding/NLU layer (Phase 6 constraint: "do not build a generic
  NLU layer speculatively").
- `superboss-register.py`'s `lookup_capability()` explicitly reports
  `embedding_fallback_available: false` and documents why: the real
  embedding-similarity mechanism (`capability-registry-service.ts`'s
  `findSimilar()`, a live pgvector cosine-similarity index) lives in
  **compliance-tracker**'s own Postgres/TypeScript runtime, tenant/org-scoped
  to that SaaS product's data model, and its own docstring says it is
  "not reachable from this Python CLI".

Also found in the course of verifying the deterministic briefing's "reuse
directly, do not rebuild" claim for `task_precedent_search`: capability_registry
row `CAP-20260806-182313-9028` claims a `search-task-precedent` CLI /
`search_task_precedent()` function exists in `superboss-register.py` -- but
the live, deployed `/opt/veridian/scripts/superboss-register.py` has no such
subcommand (confirmed by running it -- argparse's real subcommand list does
not include it). The real code exists (`repos/veridian-scripts` commit
`db2f2d635a`, blob `a37a4a16`) but only on **PR #205**
(`worker/task-20260806-181146-critical-amendment--every-task-must-sear`),
which is **open, not merged**. The capability row was registered against a
branch, not against what any other live script can actually call today.

What IS real and reachable: `repos/compliance-tracker/src/lib/embeddings.ts`
really calls OpenRouter's live `/api/v1/embeddings` endpoint
(`openai/text-embedding-3-small`, 1536-dim), and `OPENROUTER_API_KEY` is
genuinely present in this environment too (already used for real by
`anthropic_openrouter_proxy.py`). So a genuine, non-fabricated embedding
mechanism was buildable -- just not by extending the three named files, which
don't have one.

## Completed
- [x] Verified the false-premise claims above independently (files read directly, live CLI subcommands actually run, PR #205 state checked via `gh pr view`)
- [x] Checked `pruned_code_search` (real: `find_code.sh`, grep-based, no ranking) and `task_precedent_search` (phantom in the live script -- see above) before building anything new
- [x] Built `capability_semantic_search.py`, a thin wrapper (same pattern as the existing `wiring_query.py`) that composes with, never duplicates, the two existing deterministic tools:
  - stage 0 (unmodified, reused as-is): `superboss-register.py lookup-capability` (capability_registry exact/FTS) + `wiring_query.py`'s `query()` (wiring_registry exact/FTS)
  - new: real ranked cosine-similarity search over cached OpenRouter `text-embedding-3-small` embeddings, own local cache DB (`capability_embedding_cache.sqlite`) so it is never a second writer against `superboss-register.sqlite`
- [x] `reindex` subcommand: real embedding calls, 169/169 rows indexed (capability_registry 18/18 + wiring_registry `entity_type='script'` 151/151). `entity_type` in `{file, function, dispatch_event, supabase_table, ai_role, cron_job, engine, ...}` (24,153 rows) explicitly logged as out-of-scope this pass, not silently dropped.
- [x] Ran 3 real test queries against the live OpenRouter endpoint (real evidence, see below)
- [x] Registered `capability_semantic_search` in capability_registry as `CAP-20260807-054020-6688`, citing UMR-20260806-124055-bc80 and the real script path
- [x] 8 real unit tests (`test_capability_semantic_search.py`), all passing -- cosine math, content-hash determinism, cache hit/miss routing, honest-error-without-fabrication path
- [x] Committed + pushed

## Real test-query evidence (2026-08-07, live `openai/text-embedding-3-small` via OpenRouter)

**Query 1 -- this UMR's own task description:**
```
"extend the existing pruned_code_search and task_precedent_search capabilities,
or add one new thin wrapper if genuinely needed, so that searching for whether
a script or capability already exists for a given task description uses real
embedding or vector-similarity mechanism, returning ranked real matches by
real similarity score"
```
Top ranked_semantic_matches:
1. `task_precedent_search` (capability_registry) -- **0.6139**
2. `pruned_code_search` (capability_registry) -- **0.5138**
3. `find_code.sh` (wiring_registry script) -- **0.4932**
4. `wiring_query.py` (wiring_registry script) -- **0.4899**
5. `test_dedup_constraints_2026-07-31.py` (wiring_registry script) -- **0.4253**

The deterministic FTS stage on wiring_registry returned 10,937 raw keyword
hits for the same query -- the exact "plain grep, not real semantic search"
problem this UMR describes. The semantic stage returns 5 genuinely relevant,
distinctly-scored results.

**Query 2 -- unrelated domain (sales commission):**
```
"calculate sales commission payouts for an agent based on closed deals this month"
```
Top ranked_semantic_matches:
1. `commission_calculator` (capability_registry) -- **0.4051**
2. `credit-accountant.py` (wiring_registry script) -- **0.2844**
3. `cost-usage-60min.py` (wiring_registry script) -- **0.2782**
4. `cost-reconciliation.py` (wiring_registry script) -- **0.2732**
5. `gratuity_calculator` (capability_registry) -- **0.2677**

Correct top match, real score separation, no fabrication.

**Query 3 -- paraphrased so keyword/FTS matching alone does poorly:**
```
"I need a way to grep the codebase for a pattern without walking node_modules
or .git and blowing up disk I/O"
```
Deterministic capability_registry FTS stage found only 1 (spurious) match
(`document_pdf_generation`). Semantic stage:
1. `find_code.sh` (wiring_registry script) -- **0.5270**
2. `pruned_code_search` (capability_registry) -- **0.5209**
3. `file_inventory.py` (wiring_registry script) -- **0.3746**
4. `deploy-live-scripts.sh` (wiring_registry script) -- **0.3735**
5. `gtm_check_static_code_analysis.py` (wiring_registry script) -- **0.3605**

This is the concrete case where FTS/grep-only search misses the real answer
and real embedding similarity finds it -- the improvement the UMR asked for.

## Remaining
- [ ] Optional future widening: embed `wiring_registry` `entity_type='function'` (5,028 rows) and/or `entity_type='file'` (17,662 rows) if a real need for that granularity shows up (not built speculatively, same discipline `intent_engine.py` already established)
- [ ] Once PR #205 (`task_precedent_search`'s real `search_task_precedent()`) merges to main, consider wiring its FTS cross-history stage into `capability_semantic_search.py search`'s deterministic stage too (not required for this UMR -- current stage-0 reuse of `lookup-capability` + `wiring_query.py` is already real and unmodified)
