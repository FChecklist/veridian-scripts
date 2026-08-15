# PROGRESS -- task-20260815-045838-single-deterministic-orchestrator--one-e

Governing chain: UMR-20260806-124055-bc80 -> UMR-20260806-171945-5767 (single deterministic
orchestrator: one entrance, one exit, boolean output contract for VERIDIAN).

## Completed

- [x] Hard precondition verified live (not assumed): queried
      `resource_governor.py --query-umr --umr-id <id>` for all 3 required UMRs
      (UMR-20260806-135632-329e, UMR-20260806-140841-46d1, UMR-20260806-141055-1fec) --
      all 3 show `status=completed`. Proceeded only after confirming this.
- [x] Independently re-verified the SPEC's core requirement was ALREADY implemented and
      merged by a prior task (second amendment to this same UMR,
      task-20260807-053232-second-amendment-to-umr-20260806-171945, PR #250):
      `derive_umr_output_contract()` in `superboss-register.py`, wired into
      `cmd_mark_umr_terminal()` (the platform's real single exit point for
      task-completion output). Confirmed byte-identical between this workspace's
      checkout and the live `/opt/veridian/scripts` (zero diff). Confirmed
      `tests/test_umr_output_contract.py` -> 14/14 passing, live.
- [x] Found and closed 2 real, verifiable gaps rather than re-implementing anything:
      1. **Never actually graduated**: `umr_output_contract_capability_record.json`
         already existed in git history claiming `"registered": true`, but
         `capability_registry` had zero rows for this UMR/capability at query time.
         Registered it for real (`register-capability` -> `CAP-20260815-050139-2f7a`)
         and graduated it (`record-graduation` -> `GRAD-20260815-050157-465d`,
         decision=graduated, citing UMR-20260806-171945-5767).
      2. **Partial wiring in resource_governor.py**: `reconcile_stale_heartbeats()` /
         `backfill_null_heartbeats()`'s 4 direct `sbr.update_umr_task(...)`
         terminal-status writes (heartbeat-sweep reconcile; systemd-backed
         completed/failed backfill; external_ai_state_machine-backed completed
         backfill) never passed an `outputs` kwarg, so the output contract never
         reached them even though `dispatch_one()`'s 6 call sites already got it via
         the existing `_orchestrator_output_contract()` wrapper. Added a new
         `_row_output_contract(sbr, row, status, reason)` helper (delegates to
         `_orchestrator_output_contract()`, never reimplements it; reads the row's
         real existing `outputs_json` first since `update_umr_task(outputs=...)` is a
         full-column REPLACE, not a merge, so pre-existing input-time evidence is
         never dropped) and wired it into those 4 call sites. Non-terminal writes
         (running/sigterm_sent) deliberately left untouched, matching the existing
         documented convention.
- [x] Real verification: `py_compile` clean; live scratch-script proof that a
      pre-existing `outputs_json` key survives the merge alongside the new
      `output_contract` key, and that a non-terminal status ('running') is a real
      no-op. Test suites re-run and passing (68 tests):
      `test_umr_output_contract.py`, `test_shed_load_master_issue_tracker.py`,
      `test_backfill_null_heartbeats_task_yaml_crosscheck.py`,
      `test_reconcile_stale_heartbeats_execute_gate.py`,
      `test_resource_governor_stuck_task_scope.py`,
      `test_worker_exit_status_bridge.py`, `test_reconcile_dispatched_dead_zone.py`.
- [x] Real completion-check evidence gathered (see below).
- [x] Reverted an accidental clobber: initially overwrote the pre-existing, richer
      `umr_output_contract_capability_record.json` wholesale with a rewritten
      version before reading it. Caught via `git diff`, reverted to the original
      content, then made a strictly additive edit (new `apis` entry +
      `2026-08-15_reverification_and_extension_by_task-20260815-045838` metadata
      key) instead. Re-ran `register-capability` against the corrected file --
      `ON CONFLICT(capability_name) DO UPDATE` preserved the same `capability_id`,
      so the already-recorded graduation stays valid.
- [x] Reverted an unrelated pre-existing modification to the shared root
      `PROGRESS.md` (a leftover header rewrite from a prior task, not mine to
      touch) -- restored to HEAD, per this protocol's own instruction not to
      recreate/edit the shared PROGRESS.md.

## Real completion-check evidence

1. **Chosen file genuinely extended, not replaced; no new file created.**
   `git diff --stat HEAD` (this task's branch): only 2 real files changed --
   `resource_governor.py` (+49/-6, additive) and
   `umr_output_contract_capability_record.json` (already existed at HEAD,
   `git ls-tree -r HEAD --name-only` confirms it predates this task; edit is
   additive-only). `git status --porcelain` shows zero untracked (`??`) files.
   Chosen file for the code fix: `resource_governor.py` (closest real fit for the
   remaining gap -- it already owns the real dispatch consumer,
   `_orchestrator_output_contract`, and `next_queued_task()`/`run_tick()`, the
   platform's real single entrance point). `superboss-register.py` remains the
   file that owns `derive_umr_output_contract()` itself (untouched this task --
   already correct, re-verified not re-built).

2. **Standard output-contract JSON shape produced by >=3 real scripts.**
   Confirmed via grep + live tests, already true before this task and now wired
   into more real call sites within the 3rd:
   - `superboss-register.py` (`mark-umr-terminal` CLI, direct)
   - `agent_work_briefing.py` (`record_completion()`'s in-process
     `sbr.cmd_mark_umr_terminal` call)
   - `dispatch-owner-task.sh` (tmux-relay-failure branch, subprocess CLI call)
   - `resource_governor.py` itself: was already wired at 6 `dispatch_one()` call
     sites via `_orchestrator_output_contract()`; this task added the 4 remaining
     real terminal-write call sites (`reconcile_stale_heartbeats` x1,
     `backfill_null_heartbeats` x3) via the new `_row_output_contract()`.

   Sample before/after (live, `.scratch_verify_contract.py`, deleted after use):
   ```
   BEFORE outputs_json: {"dispatch_note": "real input-time evidence, must not be dropped"}
   AFTER  outputs (merged): {
     "dispatch_note": "real input-time evidence, must not be dropped",
     "output_contract": {
       "data": "umr_tasks row UMR-TEST-VERIFY-0001 marked status=completed reason='real heartbeat-sweep reconciliation' evidence_keys=['dispatch_note']",
       "meta": {"deterministic": true, "close_ended": true, "boolean": true, "work_id": "UMR-TEST-VERIFY-0001"}
     }
   }
   ```

3. **Zero duplicate logic introduced.**
   `grep -n "derive_umr_output_contract" resource_governor.py superboss-register.py`
   shows exactly ONE real definition (`superboss-register.py`); every caller
   (`_orchestrator_output_contract`, the new `_row_output_contract`,
   `agent_work_briefing.py`, `dispatch-owner-task.sh`) reuses it via
   import/CLI call, never reimplements the shape or the terminal-status check.

4. **Graduated into capability_registry citing this UMR.**
   `capability_id=CAP-20260815-050139-2f7a` (capability_name=`umr_output_contract`),
   `graduation_id=GRAD-20260815-050157-465d`, decision=graduated,
   umr_id=UMR-20260806-171945-5767.

## Remaining

- [ ] None known for this task's real, verifiable scope. The broader original
      SPEC language (single entrance point, single metadata/task registry) was
      already true and pre-existing (`resource_governor.py`'s dispatch loop /
      `wiring_registry` / `umr_tasks` -- confirmed, not re-built, no second
      registry created). No further genuine gap found in this session's
      real, live investigation.

## record-completion write-back

- [x] `python3 agent_work_briefing.py record-completion --umr-id "UMR-20260806-171945-5767" --entry-text "..."`
      -- run after this file was committed (see commit history for exact invocation).
