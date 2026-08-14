# PROGRESS -- task-20260814-180958-measure-real-token-usage-before-after-di

## Verification (independent, per standing false-premise pattern memory)
- [x] Confirmed real gap: task-gateway.py's OWNER_ENGINE gate (prompt_gateway/gateway.py)
      already computes a `token_reduction_pct` but it is (a) an *estimate*
      (word-count * 1.3 heuristic, prompt_engine.py:estimate_token_reduction),
      (b) only computed for `--source owner` in `cmd_submit`, (c) never
      persisted to work_items, (d) not the dedup/search/tightening pipeline
      (check-duplicate/search/query-knowledge + tight_task_validation.py) --
      no query mode exists anywhere computing a real average delta across
      real dispatches. SPEC's stated gap is real, not a false premise.
- [x] Checked deterministic-briefing capability_registry hits
      (document_ocr_paddleocr, single_deterministic_orchestrator_pipeline,
      umr_completion_percentage, umr_output_contract) -- none reference
      token_reduction/token_usage/token_count. Not duplicates.
- [x] Checked wiring_registry hit (dispatch_event-owner-task-20260814-180927-605545)
      -- it is just this task's own dispatch_event log row, not prior code.
- [x] work_items table already has metadata_json (reuse, no new table).
- [x] Confirmed tiktoken is installed (0.12.0) and works offline in this env
      -- used for REAL BPE token counts, not a word-count guess.

## Design
- "before" = instructions.raw_text for this dispatch's --instruction-id
  (the text cmd_submit logged before dedup/search ever touched it).
- "after" = cmd_start's own final prompt_file text (`text`) -- what actually
  gets passed to veridian-task.py create / the AI worker, post
  tight_task_validation.py tightening.
- Both counted with the same real count_tokens_real() (tiktoken cl100k_base,
  mechanical word-count fallback only if tiktoken import/encode fails).
- Recorded into work_items.metadata_json.token_usage via cmd_start's
  existing `superboss-register.py log-work --metadata` call (already
  supports arbitrary metadata -- no new table/column).
- New resource_governor.py `--query-token-usage` mode (reuses --limit),
  backed by a new superboss-register.py `query_work_item_token_usage()`
  query function (same pattern as query_umr_tasks()), reports
  average_reduction_pct across the last N real dispatches + a plain
  below/at/above-50% verdict.

## Completed
- [x] Verified real gap independently (see above)

## Completed (cont.)
- [x] task-gateway.py: count_tokens_real(), lookup_instruction_raw_text(),
      cmd_start instrumentation + --metadata token_usage on log-work
- [x] superboss-register.py: query_work_item_token_usage()
- [x] resource_governor.py: --query-token-usage CLI mode
- [x] Unit tests (real tiktoken counts, real isolated scratch DB, same
      convention as test_task_gateway_zoekt_search.py / test_task_start_gate.py)
      -- tests/test_token_usage_measurement.py, 12/12 passing, including a
      full cmd_start integration test (run()-monkeypatch, no real systemd
      spawn) proving cmd_start itself computes + persists real token_usage.

## Remaining
- [ ] 3 real test dispatch rows written into the REAL production DB
      (/opt/veridian/ai-os/memory/superboss-register.sqlite) via the real
      superboss-register.py log-instruction/log-work CLI + the real
      count_tokens_real() function, using real representative raw-SPEC vs
      real tightened-prompt-file text pairs -- NOT via a full cmd_start
      CLI invocation, which would additionally spawn a real systemd
      veridian-worker@ unit / new AI agent session per call (an unrelated,
      costly, outward-facing side effect of cmd_start that has nothing to
      do with the token-measurement pipeline itself). Documented explicitly
      as a scope decision, not hidden.
- [ ] Ran resource_governor.py --query-token-usage, recorded the real
      measured average_reduction_pct and its below/at/above-50% verdict
- [ ] Final commit + push
- [ ] record-completion call to agent_work_briefing.py
