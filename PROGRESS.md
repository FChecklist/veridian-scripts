# PROGRESS -- task-20260806-192043-precise-correction-based-on-real-direct

SPEC: apply 3 real precise corrections to the internal dev-ops orchestrator
chain (UMR-20260806-124327-6ffb / UMR-20260806-125524-720c), mirroring 3
proven compliance-tracker patterns: (1) prompt-os-resolver.ts's versioned
`prompt_templates` table instead of free-composed prose dispatch text, (2)
orchestra-execution-logger.ts's structured execution log (org/task/layer/
event_type/input/output/model/cost/denied/gated), (3) capability_registry's
`pruned_code_search` (scripts/find_code.sh) called directly by step 1
("does a script already exist"), never reimplemented.

## Completed

- [x] Verified independently (per [[veridian-task-prompt-false-premise-pattern]]
      memory note) before writing anything:
      - `repos/compliance-tracker/src/lib/prompt-os-resolver.ts` and
        `src/lib/orchestra-execution-logger.ts` are real, both wired into
        `src/app/api/ai/orchestrate/route.ts` (a genuinely different,
        customer-facing, Postgres-backed system -- confirmed not the same
        domain as this sqlite-backed internal orchestrator). SPEC's premise
        here is TRUE.
      - `scripts/find_code.sh` + its `capability_registry` row
        (`pruned_code_search`) are real and already registered.
      - UMR-20260806-124327-6ffb: real row, `status=completed`.
        UMR-20260806-125524-720c: real row, `status=running` -- this is the
        UMR behind task-20260806-181155, whose deliverable is
        `unified_orchestrator.py`, shipped in **PR #207** (still open,
        unmerged to `origin/main` as of this task).
      - PR #207 got a real Superboss `AUDIT FAIL` (2026-08-06T18:40:18Z,
        AGENTS.md Operating Rule 7c: PROGRESS.md claimed end-to-end test
        evidence that didn't exist in the diff). A **different, automated**
        actor (`VERIDIAN-DEV Ops`, not this task, not the original worker
        session) pushed real corrective commits addressing that finding
        (`1c512b0` rebase, `f43af5a` adding `tests/test_unified_orchestrator.py`)
        minutes before this task started -- confirmed genuinely real (not a
        truncated/corrupted file as first appeared under a shell pager
        artifact; re-verified with `git cat-file -p` directly: 616 lines,
        valid Python, `ast.parse` clean).
      - This task's SPEC never mentions the AUDIT FAIL / fix cycle, but it
        doesn't contradict the SPEC's 3 corrections either -- they're a
        different, additive concern (design-pattern parity, not the missing-
        test finding). Proceeding, based on this branch's current real tip.
      - `plan_generator.check_reuse_before_dispatch()` (composed by
        `unified_orchestrator.step_reuse_check`) genuinely does NOT call
        `find_code.sh` anywhere -- confirmed by reading its full body. It
        only queries capability_registry/wiring_registry/knowledge_engine/
        system_index metadata, never a live source-tree grep. SPEC's
        "step one never actually calls find_code.sh" premise is TRUE, a
        real gap, not a false claim.
      - `step_submission_contract` genuinely builds `audit_method` and
        `submit_command` as inline f-string prose, no template system.
        SPEC's "free composed prose dispatch text" premise is TRUE for
        those 2 real strings.
      - No structured execution-log table/writer exists anywhere in
        `unified_orchestrator.py` or `superboss-register.py` today (only
        `task_audits`, which is exit-code/stdout/stderr shaped, not the
        org/task/layer/event_type/model/cost/denied/gated shape the SPEC
        asks to mirror). SPEC's premise here is TRUE.
- [x] Merged PR #207's real branch tip (`f43af5a`) into this task's branch
      so the 3 corrections land on the real, current orchestrator code
      rather than fiction -- same "amendment builds on real prior UMR work"
      convention already used repeatedly in this exact chain (e.g.
      `f4d6af3`'s own PR #199 merge).

## Remaining

- [ ] Correction 1: real `prompt_templates`/`prompt_versions` tables in
      superboss-register.sqlite + `resolve_prompt_template()` (fail-loud,
      mirrors `resolvePromptTemplate`), replacing `step_submission_contract`'s
      2 inline prose strings with resolved, versioned, labeled rows.
- [ ] Correction 2: real `orchestrator_executions` structured log table
      (org/task/layer/event_type/input/output/model/cost/status incl.
      denied/gated) + `record_orchestrator_execution()` (never throws,
      mirrors `recordOrchestraExecution`'s fire-and-forget posture), wired
      into `unified_orchestrator.run()`'s real step transitions.
- [ ] Correction 3: `step_reuse_check` calls `scripts/find_code.sh` directly
      (the real, already-registered `pruned_code_search` capability) in
      addition to the existing registry-metadata lookups, never
      reimplementing search logic.
- [ ] Real tests for all 3 corrections, run green locally before push.
- [ ] Commit + push; record completion via `agent_work_briefing.py`
      against `UMR-20260806-130110-c620`.
