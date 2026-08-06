# Merged-in reference: PR #207 / task-20260806-181155's own PROGRESS.md

Preserved verbatim (not squashed) from the merged branch
`worker/task-20260806-181155-amendment--close-three-real-gaps--submis` for
history/audit purposes -- this task's own root `PROGRESS.md` is the
authoritative progress doc for THIS task (task-20260806-192043).

# PROGRESS -- task-20260806-181155-amendment--close-three-real-gaps--submis

SPEC: UMR-20260806-125524-720c, amendment to UMR-20260806-124055-bc80 /
-124327-6ffb / -124654-a8d6 / -124936-13b1. Close 3 real gaps found on
self-audit: (1) deterministic submission contract stated at AI hand-off,
(2) independent re-verification before status=completed, (3) general
input/output validation inside the orchestrator itself.

## Completed

- [x] Verified independently (not assumed) that no prior UMR in this chain
      had actually built "the one unified orchestrator" script yet -- when
      this task began, UMR-20260806-124327-6ffb was still status='running',
      its own dispatched worker task (task-20260806-181141) had not
      started. Amending a nonexistent script would be fiction, so this task
      built the base steps UMR-124327-6ffb/-124654-a8d6/-124936-13b1
      specified, plus this task's own 3 gap-closing steps, in one script:
      `unified_orchestrator.py`.
- [x] Mid-build, found a real, live collision: PR #199
      (worker/task-20260806-165903-correction--wire-the-new-ai-agent-id-tab)
      merged into origin/main *while this task was in progress*, already
      building real, tested `ai_agent_registry.py` (agent_id reuse/mint) and
      `agent_work_briefing.py` (briefing assembly + write-back), live-wired
      into worker-entrypoint.sh. Merged origin/main in, removed the
      duplicate agent-registry code this task had briefly added to
      `superboss-register.py`, and rewrote `unified_orchestrator.py` to
      compose those two real modules directly instead of a second,
      competing implementation (kept in git history, not silently
      squashed -- see commit log).
- [x] `superboss-register.py`: added `task_audits` canonical write path
      (`_ensure_task_audits_table`, `record_task_audit`, CLI
      `record-task-audit`) -- the table already existed live (0 rows, no
      real writer anywhere in this repo's current code), now the real
      backing store for Gap 2's independent re-verification trail.
- [x] `unified_orchestrator.py` built with 10 real deterministic steps,
      each returning a real boolean:
      1. `step_reuse_check` -- composes plan_generator.py's
         `check_reuse_before_dispatch()` (capability/wiring/knowledge/
         system_index, already merged/wired)
      2. `step_prior_agent_precedent` -- cross-history search over past
         completed umr_tasks + their agent_ids
      3. `step_resolve_agent_id` -- composes `ai_agent_registry.py`'s
         `ensure_agent()` (PR #199)
      4. `step_assemble_briefing` -- composes `agent_work_briefing.py`'s
         `assemble_briefing()` (PR #199)
      5. **GAP 1** `step_submission_contract` -- deterministically extracts
         the real runnable audit command from the task's own
         successCriteria (reuses `tight_task_validation.py`'s command-
         detection regexes) and states output_file_path / submit_command /
         audit_method plainly, never implicit
      6. **GAP 3a** `step_validate_input` -- composes
         `tight_task_validation.py`'s `validate_tight_task()`, generalized
         to run inside the orchestrator itself (not only the external-agent
         bridge's own separate `check_external_agent_eligibility()`)
      7. **GAP 2** `step_reverify` -- independently RE-RUNS the real audit
         command (never trusts a self-reported "done"), records verdict via
         `record_task_audit()`
      8. **GAP 3b** `step_validate_output` -- real boolean pass/fail: step 7
         passed AND every expected output file really exists on disk
      9. `step_writeback` -- composes `agent_work_briefing.py`'s
         `record_completion()`; refuses to write status=completed unless
         steps 7 AND 8 both really passed
      10. `step_graduate_evaluation` -- deterministic graduate-to-script
          decision (UMR-124654-a8d6 step 4), decision-only, writes nothing
          (a sibling in-progress task is separately building the persistent
          `capability_graduation_log` recorder -- not duplicated here)
      Plus standalone `step_verify_all_registry_paths()` (UMR-124936-13b1).
- [x] End-to-end test against one real task (verify `gap-status.py` runs
      clean), against a scratch copy of the live DB
      (`SUPERBOSS_REGISTER_DB` override, zero production writes):
      - Positive path: all 10 steps returned `ok: true`; step 7 really ran
        `python3 gap-status.py` (real exit 0); step 9 really flipped the
        seeded `umr_tasks.status` to `completed`; agent_id correctly reused
        (not re-minted) on a second run.
      - **Negative path** (proves the gate is load-bearing, not decorative):
        a task whose successCriteria's real command deliberately exits 1 --
        step 7 recorded `passed: false` (real exit_code=1), step 8/9/10 all
        correctly refused, and `umr_tasks.status` genuinely stayed `queued`,
        never falsely marked completed.
- [x] `verify-registry-paths` run for real against the live production DB
      (read-only): found and fixed 2 real bugs in the check itself while
      building it (multi-path `;`-separated values resolved against the
      wrong root; `#fragment` knowledge_engine identifiers checked as
      literal paths) before trusting the result. Final real numbers:
      wiring_registry 540/603 disk-checkable rows pass (63 genuine
      failures -- see below), 7325 rows honestly excluded as not real disk
      paths by design (supabase/vercel/github identifiers, cron crontab-line
      rows); ai_agent_registry 1/1 pass; capability_registry has no
      dedicated path column in its live schema (0 checked, flagged not
      silently skipped).

## Remaining / honestly still open

- [ ] 63 genuine wiring_registry path failures found (not fixed by this
      task -- out of this amendment's 3-gap scope, flagged per
      UMR-124936-13b1's own "record real defects, never leave a broken
      path silently in the metadata" rule): includes 2 confirmed-stale
      script rows (`module-queue-dispatcher.py` / `queue-dispatcher.py`,
      genuinely absent from `/opt/veridian/scripts/`, referenced by both
      wiring_registry and live crontab-backup rows -- likely superseded by
      dispatch_core.py's own consolidated dispatch-tick.py per that
      module's own docstring) and ~60 `ai-os-scripts/*.py` /
      `ai-os/*.yaml` knowledge_engine rows pointing at files that do not
      exist on this disk. A follow-up UMR should triage: fix path, or
      remove row.
- [ ] cron_job entity_type has two incompatible real `path` conventions
      live today (a bare script path vs. a full crontab command line) --
      found while building the path check, not corrected here (schema
      decision, out of this task's scope).
- [ ] UMR-20260806-124654-a8d6's own sibling task
      (task-20260806-181146-critical-amendment) is independently building
      `search-task-precedent` / `record-graduation` capability_registry
      entries at the same time as this task -- not consumed here (their
      code was not yet on origin/main at build time); a future pass should
      reconcile `unified_orchestrator.py` steps 1/2/10 against whatever
      that task lands, once merged, per the same reuse-not-duplicate rule.
