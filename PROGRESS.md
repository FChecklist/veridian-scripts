# PROGRESS -- task-20260806-192038-mandatory-real-study--all-twenty-engines

## Completed
- [x] Located real data sources: wiring_registry/capability_registry live in
      /opt/veridian/ai-os/memory/superboss-register.sqlite (not the repo-root
      working-copy.sqlite). Confirmed via `resolve_superboss_db_path()`.
- [x] Pulled all 20 `entity_type='engine'` rows from wiring_registry (real
      paths + stored metadata_json).
- [x] Pulled all 16 capability_registry rows (real apis/workflow columns).
- [x] Extracted full real function/class inventory of superboss-register.py
      via `ast` (174 defs/classes with line numbers) -- grep truncates at 50
      matches in this sandbox, ast parse does not.
- [x] Traced "the real orchestrator" to the held `orchestrator_router.py`
      proposal (child UMR-20260806-065104-c69a, PM decision task
      task-20260806-142201-...): identified 3 real gaps -- (1) no cross-task
      agent identity memory, (2) no deterministic pre-dispatch reuse gate,
      (3) no single standing instruction routing point.
- [x] Traced "the real agent id registry" to ai_agent_registry.py +
      agent_work_briefing.py (merged PR #199/#206, capability_registry rows
      `ai_agent_registry`/`agent_work_briefing`).
- [x] Traced "the real wiring health check" to verify_registry_file_paths.py
      (merged, this branch's own recent commit) -- distinct in scope from
      health-check-15min.py (systemd/server health, not registry-path health).
- [x] Dispatched 5 parallel real-file-reading study agents (engines 1-5,
      6-10, 11-15, 16-20, and all 16 capability_registry rows) -- in flight.

- [x] Collected all 5 agent findings (engines 1-5, 6-10, 11-15, 16-20, all
      16 capability_registry rows) -- all real, file-verified.
- [x] Cross-checked the 3 orchestrator_router.py gaps (from the held PM
      decision, task-20260806-142201): gap 1 (agent identity memory) and
      gap 2 (pre-dispatch reuse gate) are now fully closed by
      ai_agent_registry.py + agent_work_briefing.py; gap 3 (single routing
      point) has substantial real coverage in resource_governor.py.
- [x] Confirmed independently (own grep, corroborating the sub-agent and
      REGISTRY_FILE_PATH_VERIFICATION_2026-08-06.md) that
      `search-task-precedent`/`record-graduation`/`list-graduations` do
      NOT exist yet in superboss-register.py on disk (PR #205 unmerged).
- [x] Wrote final study doc:
      /opt/veridian/ai-os/memory/MANDATORY_REAL_STUDY_ALL_TWENTY_ENGINES_2026-08-06.md
- [x] Registered doc in knowledge_engine: KE-20260806-193557-12cd
      (VERIFIED_MATCH)

- [x] record-completion via agent_work_briefing.py
      (UMR-20260806-125647-990f -> completed, AGENT-20260806-125647-990f)

## Remaining
- [ ] commit + push
