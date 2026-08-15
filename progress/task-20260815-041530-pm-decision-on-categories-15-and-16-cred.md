# PROGRESS -- task-20260815-041530-pm-decision-on-categories-15-and-16-cred

Governing UMR: UMR-20260806-071025-1d28. Two-part SPEC.

## Completed

### Part 1 -- PM decision on categories 15/16 credential creation

- [x] Independently verified (before acting) whether the PM ruling in the SPEC
      still applied. It did not: `pm_decisions_pending` rows id=69 (category 15)
      and id=70 (category 16), queried directly from the live
      `/opt/veridian/ai-os/memory/superboss-register.sqlite` (never raw SQL for
      writes; this was a read-only verification query), are both
      `status=resolved`, `closed_ts=2026-08-15T03:48:07Z` / `03:48:17Z` --
      **~27-37 minutes before this task's own SPEC was dispatched
      (04:15:30Z)**.
  - Resolved by `task-20260815-033541-owner-delegated-decision--provision-a-re`,
    a genuine **Owner-delegated** decision (not AI self-answered -- id=69/70's
    own `detail` text records the original 2026-08-06 blocked-check evidence:
    zero `GTM_TEST_*`/`TENANT_TEST_*`/etc. env vars, zero Owner go-ahead docs
    found, script correctly refused to guess/create a credential itself).
  - Real action taken by that decision: a dedicated **non-production** dummy
    tenant "Meridian Test Industries" (org_id=dstmb99kn1hc4toxb6iqs1td, slug
    `meridian-test-industries-gtm-fixture-nonprod`, `internal_use_exempt=true`)
    with 4 real per-role accounts (owner/admin, manager, member, viewer) was
    provisioned via `compliance-tracker/scripts/gtm-provision-cat15-16-test-tenant.ts`
    -- **not** the live production tenant, and not a self-serve credential the
    AI invented.
  - Independently re-verified directly against `gtm_certification_categories`
    (live DB, not just trusted from the closed_note prose):
    `category_index=15`: `passed=1`, `validated_at=2026-08-15T03:46:31Z`,
    full per-persona evidence (4/4 logins, 0 cross-tenant leaks, foreign
    resource fetch denied 404 for every role).
    `category_index=16`: `passed=1`, `validated_at=2026-08-15T03:47:43Z`,
    17/17 role x endpoint checks matched the documented ROLE_RANK boundary,
    0 mismatches.
  - Noted honestly: the provisioning script's own landing PR,
    `FChecklist/compliance-tracker#1199`, was still **OPEN** (state=OPEN,
    mergeable=MERGEABLE) at verification time -- the `gtm_certification_categories`
    rows are the independently-authoritative real test-run record either way
    (they reflect a real run against live `https://projexa-ai.com`, not a
    claim resting on that PR having merged); noted for completeness, out of
    this task's own scope (compliance-tracker repo).
- [x] **Conclusion: this SPEC's Part 1 premise is stale** (the same
      "re-dispatched governing UMR with a claim that doesn't match live
      state" pattern this session has hit before on this same governing UMR
      chain -- see PR #402/#401 on this same UMR-20260806-071025-1d28). The
      executor is not actually still holding on an unanswered categories
      15/16 question; that question was already answered by legitimate PM/
      Owner-delegated authority and fully executed, with both categories now
      passing. No new credential was created by this task (per the ruling);
      no new tenant was provisioned by this task (one already exists, real,
      non-production, Owner-delegated); no new Owner escalation was opened
      (the one the SPEC anticipated needing was already resolved).
- [x] Recorded via `superboss-register.py log-work` (see command below,
      real invocation, output captured) -- zero raw SQL.

### Part 2 -- fail-open duplicate-check + killed-row-resurrection fix (UMR-20260806-093654-7566)

- [x] Confirmed governing evidence independently before writing any code:
      `resource_governor.py --query-umr --umr-id UMR-20260730-041943-093a`
      shows `status=killed`, `reason="resubmitted (reused umr_id, prior status
      was 'killed')"` -- the live DB itself corroborates the SPEC's journal-log
      claim (this workspace has no access to the live host's actual
      `journalctl -u veridian-directive-engine.service` output to
      independently re-read the two cited log lines byte-for-byte; the DB
      row's own `reason` column is the independent corroboration actually
      available here, and it matches).
      `UMR-20260729-112414-3269` (`PHASE-4-BUILD-WORKFLOW`) is `status=completed`
      (already terminal, not stuck).
- [x] Checked all 28 open PRs against `FChecklist/veridian-scripts` for a
      collision on `directive_engine.py`/`resource_governor.py` before writing
      (per SPEC instruction: rebase, don't race). Only PR #357 touches
      `directive_engine.py` (env-path hazard rewiring, `SUPERBOSS_DB` /
      `load_directive()` / `find_in_flight_duplicate()`'s DB-connect line) --
      does not overlap `run_check_duplicate_battery()` or `process_one()`'s
      battery-handling/retry branches. PRs #276/#273/#196/#184 touch
      `resource_governor.py` in unrelated regions (stop-work-order gate,
      issue tracker, stale-queued aggregation, dispatch resume gating); none
      touch the Rule-1 reuse-on-resubmit block or `submit()`'s validation
      preamble. No collision.
- [x] Root cause confirmed and fixed in **the real file that emits the cited
      log lines**, `directive_engine.py`:
  - `run_check_duplicate_battery()`: failure path (subprocess/timeout/
    unparseable-JSON) still returns `None`, but the log message and
    docstring now correctly describe it as a failure signal the caller must
    treat as fail-closed, not "proceeding".
  - `process_one()`: `battery is None` (the check genuinely could not run)
    now skips the submission, calls `note_needs_review()` with a real
    blocker, logs it, and returns `"duplicate_check_failed_fail_closed"` --
    symmetric with the existing `duplicate_found=true` branch. Previously
    fell straight through to `submit_task()` (fail open).
  - `process_one()` now captures `is_terminal_resubmission` (status was
    failed/rejected_duplicate/killed at entry) and passes
    `force_new_umr_id=is_terminal_resubmission` into `submit_task()`.
  - `submit_task()`: new optional `force_new_umr_id` kwarg (default False,
    every existing caller unaffected), forwarded onto the spec dict as
    `"force_new_umr_id": True` only when set.
- [x] `resource_governor.py`'s `submit()` (the actual umr_id-reuse mechanism,
      OCID-068 Rule 1): new opt-in `task_spec["force_new_umr_id"]` handling.
      When set and a prior row exists, `reused_umr_id` is forced back to
      `None` regardless of the prior row's status -- `upsert_umr_task()`'s
      existing "no umr_id supplied" branch then mints a genuinely fresh
      `umr_id`, leaving the prior (possibly terminal) row completely
      untouched. Default (flag omitted/False) is **100% unchanged** --
      confirmed by `test_resubmission_without_force_flag_still_reuses_umr_id_for_other_callers`,
      which reproduces `dispatch-tick.py`'s real resume-an-interrupted-worker
      shape (Rule-1's own docstring: "the real caller this fixes is
      dispatch-tick.py's resume_interrupted_workers_tick()", reusing a
      terminal row's umr_id **is** that caller's intended behavior --
      deliberately NOT changed here, only opted out of for
      directive_engine.py's one real terminal-retry path).
- [x] Two new real regression tests added
      (`tests/test_directive_engine_fail_closed_duplicate_check.py`):
  - `test_failed_duplicate_check_skips_submission_fail_closed` -- proves a
    failed duplicate check results in a skip, never a submission.
  - `test_terminal_row_cannot_be_revived_under_its_own_umr_id` -- proves a
    terminal row cannot be revived under its own umr_id (exercises the real
    `resource_governor.py submit()` entrypoint directly, not a mock).
  - Plus two supporting tests (`test_successful_duplicate_check_with_no_duplicate_still_submits`,
    `test_resubmission_without_force_flag_still_reuses_umr_id_for_other_callers`)
    guarding against over-broad fixes.
- [x] Fixed two pre-existing tests in `tests/test_directive_engine_retry_gate.py`
      that were incidentally relying on the OLD fail-open bug (unmocked
      `run_check_duplicate_battery()` really fails in this sandboxed
      environment, and used to silently fall through to `submit_task()`) --
      now explicitly stub `run_check_duplicate_battery()` to a clean
      no-duplicate result, since those two tests exist to test the
      retry-once gate, not the duplicate battery.
- [x] Full test run: `tests/test_directive_engine_retry_gate.py` (7),
      `tests/test_directive_engine_fail_closed_duplicate_check.py` (4),
      `test_directive_engine_stop_audit_monitor.py` (16) all pass.
      `test_resource_governor_queue_management.py`/`_owner_priority_advance.py`/
      `_telemetry_retention.py` + `tests/test_resource_governor_stuck_task_scope.py`:
      21/23 pass; the 2 failures (`test_list_queue_real_dispatch_order`,
      `test_move_down_never_crosses_a_tier_boundary`) independently confirmed
      **pre-existing on main** (`git stash` + re-run reproduces the identical
      2 failures with zero code changes) -- not a regression from this work.
- [x] `UMR-20260730-041943-093a` (PHASE-3-BUILD-CALC) and
      `UMR-20260729-112414-3269` (PHASE-4-BUILD-WORKFLOW): both **already
      terminal** (killed / completed respectively) at verification time --
      neither was actually stuck. Deliberately did NOT call
      `mark-umr-terminal` on either: both rows already carry real, accurate,
      historically-correct terminal evidence, and re-invoking that command
      would overwrite their real `ts_completed` with "now", destroying real
      history for no benefit (they don't need a NEW terminal status, they
      need to *stay* closed, which is exactly what this fix now guarantees:
      a future retry of either `task_identity` can no longer reuse their
      `umr_id` and flip them back to `queued`/`running`).

## Remaining

- [ ] Commit + push, open PR against `FChecklist/veridian-scripts` main.
- [ ] Record completion evidence via `superboss-register.py log-work`
      (Part 2, with real commit sha + PR number once available).
- [ ] Call `agent_work_briefing.py record-completion --umr-id
      UMR-20260806-102610-1930` per the deterministic briefing.
