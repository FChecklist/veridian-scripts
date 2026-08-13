# PROGRESS -- task-20260813-183210-rca--umr-20260813-170956-5385-killed

## SPEC
Real RCA of UMR-20260813-170956-5385 (status=killed, unit_name
veridian-worker@task-20260813-171208-fix-pm-sentinel-tick-sh-positional-activ.service).
Determine the real root cause and either fix + redispatch the real
remaining scope, or record a real, honest terminal outcome via
superboss-register.py mark-umr-terminal citing real evidence. Do not
fabricate completion.

## Completed
- [x] Queried `resource_governor.py --query-umr --umr-id UMR-20260813-170956-5385`
      myself (never trusted the SPEC's summary alone) and read the row's
      full `outputs_json`/`reason`/`metadata_json`.
- [x] Real RCA, independently verified against live state (not narration):
  - The dispatched task (`task-20260813-171208-fix-pm-sentinel-tick-sh-positional-activ`)
    **genuinely completed successfully**: `task.yaml` status=`completed`;
    it fixed pm-sentinel-tick.sh's Check 2b positional ActiveState/Result
    parse and the non-zero-exit-on-cap-reached defect, landed that fix as
    commit `32b4276` on the pre-existing open PR #299 (per SPEC's
    no-competing-PR instruction), ran the real regression suite (8 passed),
    and its own PROGRESS.md-only PR #313 was supervisor-reviewed
    (`review.json` verdict=approve, independently re-ran the tests and
    verified PR #299's diff itself) and merged autonomously to main at
    commit `8db4abe` (confirmed: `8db4abe` is a real ancestor of
    `origin/main`, and is the exact "Merge pull request #313" commit
    visible in this repo's own `git log`).
  - The `status='killed'` label on UMR-20260813-170956-5385 was **false** --
    written by `reconcile_owner_dispatch_status.py` (per the row's own
    `metadata_json.reconcile_owner_dispatch_status`) at
    `2026-08-13T17:33:39Z`, reasoning "no PR was ever opened ... orphaned
    dispatch, never produced a real artifact."
  - **Real root cause**: a race in `reconcile_owner_dispatch_status.py`'s
    classification logic. The task's worker unit legitimately went
    `inactive` at `17:27:03Z` when it reached `task.yaml` status
    `pending_review` (the expected, correct worker->supervisor handoff --
    `veridian-task.py` stops the worker unit and starts
    `veridian-supervisor@<task_id>.service` at that exact moment). The
    reconciler ran its snapshot check at `17:33:39Z`, while the supervisor
    review was still genuinely in flight -- PR #313 (the only PR whose
    branch matches this task) was not created until `17:36:57Z`, **3m18s
    after** the reconciler's check (confirmed via `gh pr view 313
    --json createdAt`). `reconcile_owner_dispatch_status.py` never checked
    the `veridian-supervisor@...` unit's own state before concluding
    "no live process, no real deliverable" -- it saw worker-inactive +
    no-PR-yet + `task.yaml` pending_review and fell straight into the
    `killed` bucket.
  - This exact race was **already found and fixed once** in this same
    codebase, in `reconcile_stale_running_workers.py`'s STEP 3 (its own
    pending_review + supervisor-unit-ActiveState guard, live-confirmed
    incident on `task-20260813-135613`) -- `reconcile_owner_dispatch_status.py`
    duplicates the same reconciliation problem space (any
    `status='running'` `umr_tasks` row backed by a `veridian-worker@...`
    unit) but never reused that safeguard, so it reintroduced the identical
    false-terminal bug for `source_trigger='owner_dispatch_gateway'` rows.
- [x] **Fixed the real root cause** in `reconcile_owner_dispatch_status.py`:
      added the same pending_review + supervisor-unit-ActiveState guard
      `reconcile_stale_running_workers.py` already carries, reused rather
      than re-solved -- if `task.yaml` status is `pending_review` and the
      row's `veridian-supervisor@<task_id>.service` unit is still
      active/transitional, the row now routes to `NEEDS_AI_JUDGMENT`
      ("real review still in flight") instead of being mechanically
      relabeled `killed`. Rows whose supervisor has genuinely also settled
      still fall through to the normal mechanical rules unchanged.
- [x] Added 2 real regression tests to `tests/test_reconcile_owner_dispatch_status.py`:
      `test_pending_review_with_active_supervisor_needs_judgment` (the real
      UMR-20260813-170956-5385 shape -- must NOT auto-terminalize) and
      `test_pending_review_with_settled_supervisor_still_falls_through`
      (a genuinely-finished pending_review row with no PR must still
      mechanically resolve to `killed`, no infinite hold).
- [x] Ran the real test suite: `python3 -m pytest
      tests/test_reconcile_owner_dispatch_status.py
      test_apply_owner_dispatch_status_corrections.py -v` -- **20/20
      passed** (14 pre-existing + 2 new in the reconcile suite, unchanged
      in the sibling apply-corrections suite).
- [x] Corrected the real database row: `superboss-register.py
      mark-umr-terminal --umr-id UMR-20260813-170956-5385 --status
      completed --commit-sha 8db4abeb54a2384cf62edb3be9eee4cef6c00d03
      --pr-number 313 --repo veridian-scripts`, citing the real evidence
      chain above in `--reason`. Verified: row now reads
      `status=completed`, `ts_completed` set, real commit-sha ancestor
      check passed.
- [x] Caught and corrected my own process error: I first made this edit
      (and a matching test edit) against `/opt/veridian/scripts` -- a
      separate, already-drifted local git checkout (off-main, pre-existing
      unrelated local modifications) used as a live deploy target, NOT
      this task's own repo/branch. Reverted both accidental edits there
      (`git checkout --`, confirmed clean) and re-applied the real fix
      + tests in this task's own workspace/branch instead, which is what
      this PROGRESS.md and the eventual PR actually carry.

## Remaining
- [ ] None outstanding on this task's own scope. Merging this branch is
      the only remaining step (standard supervisor review process, outside
      this task's own authority to self-merge without that gate).
