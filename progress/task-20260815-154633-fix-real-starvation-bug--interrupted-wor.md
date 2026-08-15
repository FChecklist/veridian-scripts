# PROGRESS -- task-20260815-154633-fix-real-starvation-bug--interrupted-wor

## Completed
- [x] Located the real target script: `dispatch-tick.py` (found via
      `grep -rn "resume_interrupted_workers_tick"` -- main() calls
      `supervisor_sweep_tick()` -> `resume_interrupted_workers_tick()` ->
      `gap_queue_tick()` -> `module_queue_tick()`, in that real order).
- [x] Independently confirmed the live incident against the real production
      `superboss-register.sqlite` (not trusting the SPEC's numbers): 17
      active `dispatch-tick:resume_interrupted_workers` umr_tasks rows (14
      queued + 3 running) against `dispatch_core.CONCURRENCY_CAP=5`, real
      `running_worker_count()==5` -- fully saturated.
- [x] Traced the real starvation mechanism: `resume_interrupted_workers_tick()`
      queues accepted candidates via `resource_governor.submit()` at
      `tier=1` (highest real priority); `resource_governor.dispatch_one()`'s
      own tick (separate real process) drains that queue into real running
      workers against the same `dispatch_core.CONCURRENCY_CAP`.
      `module_queue_tick()` checks the same real
      `has_free_slot_with_stale_swap_override()` gate directly. A resume
      backlog >= cap deterministically starves every other real consumer of
      that shared cap for as long as the backlog persists.
- [x] Implemented the fix in `dispatch-tick.py` (real file, not
      `dispatch_core.py` -- which stays frozen under the 2026-08-08
      stop-work order, per this file's own precedent
      (`has_free_slot_with_stale_swap_override()`) of wrapping
      `dispatch_core.py`'s real output rather than editing it):
      `resume_interrupted_workers_tick()` now caps its own real concurrent
      consumption (queued+dispatched+running umr_tasks rows under the new
      `RESUME_SOURCE_TRIGGER` constant) at `CONCURRENCY_CAP - 1` via the new
      `_count_active_resume_umr()` helper + a `reserved_max_active` check
      inside the loop. A real candidate beyond that limit is skipped this
      tick (reported in a new `skipped_reserved_capacity` list, retried next
      tick) rather than submitted -- resume's own real recovery guarantee is
      unchanged (every candidate is still found and still eligible every
      tick); only how many may be simultaneously ACTIVE at once is bounded
      below the real fixed cap.
- [x] Wrote real tests (`tests/test_resume_reserved_capacity_no_starvation.py`):
  - `test_large_resume_backlog_leaves_a_real_slot_for_module_queue_dispatch`:
    a real 15-task backlog (3x cap) against a real scratch
    superboss-register.sqlite, run across 3 real ticks, proves resume never
    exceeds `CAP-1=4` real active rows -- then, with real
    `running_worker_count()` mocked to that same number (simulating the
    worst real case), proves a real fresh module-queue item still gets
    dispatched via the real, unmocked `module_queue_tick()` code path (real
    `has_free_slot_detail()` cap gate, real `dispatch_module_item()`, real
    queue YAML read/write).
  - `test_small_resume_backlog_behavior_is_completely_unchanged`: a real
    2-task backlog (well under `CAP-1`) resumes both tasks exactly as
    before this fix, zero `skipped_reserved_capacity` entries -- the
    regression test proving no behavior change below the reservation limit.
  - Both pass under `python3 -m pytest` and under standalone
    `python3 tests/test_resume_reserved_capacity_no_starvation.py`.
- [x] Ran the full pre-existing dispatch-tick/resume regression suite (105
      tests across 16 files, including
      `test_resume_interrupted_workers_bounded_retry.py`,
      `test_resume_interrupted_workers_no_duplicate_row.py`,
      `test_umr_reuse_on_resume.py`,
      `test_dispatch_tick_stale_swap_override.py`,
      `test_dispatch_tick_owner_dispatch_reconciliation.py`,
      `test_dupguard_overbroad_scope_fix.py`,
      `test_reconcile_stale_running_workers.py`,
      `test_dispatch_tick_stale_running_reconciliation.py`,
      `test_build_lock_contended_requeue.py`,
      `test_directive_engine_fail_closed_duplicate_check.py`,
      `test_dispatch_decision_journal_logging.py`,
      `test_dispatch_owner_task_status_write.py`,
      `test_rule4_pm_visible_real_counts.py`,
      `test_rule5_real_stall_detection.py`,
      `test_target_identifier_dedup.py`): all 105 pass, zero regressions.

## Remaining
- [ ] Commit + push this change.
- [ ] Independent AUDIT:PASS, specifically confirming real interrupted-worker
      recovery is not weakened -- only no longer able to starve fresh
      dispatches indefinitely.
- [ ] `record-completion` call to `agent_work_briefing.py` for
      `UMR-20260815-070818-d173` once real work is confirmed complete.
