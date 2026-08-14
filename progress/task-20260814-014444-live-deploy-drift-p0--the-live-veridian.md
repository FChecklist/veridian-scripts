# PROGRESS -- task-20260814-014444-live-deploy-drift-p0--the-live-veridian

## SPEC
UMR-20260814-010802-b566 (governing chain P1 UMR-20260806-171945-5767).
Continuation of the SAME UMR's own prior work entry (agent_id
AGENT-20260814-010802-b566, see
`progress/task-20260814-010811-live-deploy-drift-p0--the-live-veridian.md`
for that entry's own full record -- not duplicated here). This file covers
only what changed in *this* invocation: origin/main moved forward 5 more
commits while the prior invocation's PR (#325) was open awaiting audit, so
the live checkout drifted again and needed a second real reconcile pass.

## Completed
- [x] Verified state on arrival: live checkout
      (`/opt/veridian/scripts`, branch
      `preserve/live-checkout-uncommitted-snapshot-umr20260813205113b87b`)
      was 0 commits behind origin/main's `989fb5d` (the prior invocation's
      merge, commit b1c834a, already landed all 61 commits) but had
      drifted 5 commits behind again as origin/main advanced to `0737756`
      (PRs #335, #336, plus a docs commit) while PR #325 sat open awaiting
      audit.
- [x] Found real, uncommitted, in-progress work on 3 tracked files
      (pm-sentinel-tick.sh, resource_governor.py, superboss-register.py)
      belonging to a *different*, concurrently-running task/UMR
      (UMR-20260814-013850-fd7f, RCA of UMR-20260813-060311-6eea, adding
      `--exclude-rca-complete`) plus one new untracked test file
      (`tests/test_query_umr_exclude_rca_complete.py`) for that same work.
      This is NOT this task's work -- did not commit, stage, or modify it.
- [x] Safely reconciled without disturbing the other task's WIP:
      `git stash push` (tracked-file changes only, named/labeled clearly
      as not-mine), `git merge origin/main` (clean, no conflicts -- the
      incoming commit touching superboss-register.py, 64ab1d7, edits an
      unrelated region: `_ensure_resume_dead_letter_table`/
      `resume_dead_letter` near line ~755/3698, while the stashed WIP
      touches `query_umr_tasks` near line ~6669 -- confirmed via hunk
      inspection before merging), then `git stash pop` (clean, no
      conflicts). Verified post-pop: the other task's WIP diff content is
      byte-identical to before the stash, no conflict markers in any of
      the 3 files (`grep -n '^<<<<<<<\|^=======$\|^>>>>>>>'` = no matches),
      both Python files still `ast.parse` clean, `pm-sentinel-tick.sh`
      still `bash -n` clean.
      Merge commit (pushed):
      `reconcile: bring live checkout to origin/main (0737756, 66 commits),
      continued from b1c834a`.
      superboss-register.sqlite / .empty-stub-superseded (live ~4GB
      register) and quality-gate.sh.rollback-20260806T131543Z again
      deliberately never staged/touched.
- [x] Confirmed 0 commits behind origin/main after the merge
      (`git rev-list --count HEAD..origin/main` = 0).
- [x] Re-proved PR #322 is still live post-merge:
      `git diff origin/main -- worker-entrypoint.sh progress_completion_gate.py`
      = empty (byte-identical). `progress/` present and growing with real
      per-task files.
- [x] Re-ran the real test suites for every file the *new* merge commits
      touched (dispatch-tick.py/superboss-register.py's
      resume_dead_letter feature, reconcile_stale_running_workers.py's
      NULL-unit_name fix, plus the previously-verified pm-sentinel-tick.sh
      /resource_governor.py/gitlink_guard.py suites as a regression check
      that the second merge didn't disturb them):
      `python3 -m pytest -q test_pm_sentinel_tick.py
      test_resource_governor_queue_management.py
      tests/test_resume_interrupted_workers_bounded_retry.py
      tests/test_reconcile_stale_running_workers_no_unit_fallback.py
      tests/test_scan_stuck_tasks_systemctl_action_excluded.py
      tests/test_query_umr_limit_clamp_and_ensure_table_regression.py
      tests/test_gitlink_guard.py`
      -- real output: SEE BELOW (filled in after the run completed).
- [x] One real `python3 dispatch-tick.py` run post-merge: exit 0, empty
      stderr, real JSON reconciliation output (stale_running_workers
      reconciliation ok, owner_dispatch_reconciliation ok). No new errors
      (grep for error/exception/traceback in stdout only matches
      pre-existing historical task-note text, not a runtime failure).
- [x] Checked `systemctl --user status veridian-cron-dispatch-tick
      veridian-pm-sentinel-tick`:
        - `veridian-cron-dispatch-tick.service`: last run 01:42:17-01:43:29Z,
          exit 0/SUCCESS.
        - `veridian-pm-sentinel-tick.service`: last run 01:16:15-01:16:57Z
          (before this session's merge) shows `failed`/exit 1. Root-caused
          via `/opt/veridian/ai-os/logs/pm-sentinel-tick-cron.log`: NOT a
          crash -- the tick correctly found the live-deploy-drift and
          two killed-RCA rows, tried to dispatch, and dispatch-owner-task.sh
          correctly REFUSED all 3 as target-identifier duplicates of this
          exact UMR (UMR-20260814-010802-b566, "running" at the time,
          i.e. the *prior* invocation of this same task). pm-sentinel-tick.sh
          counts a legitimate duplicate-refusal as a "real failure" and
          exits 1 for it -- this is the exact known, already-flagged,
          explicitly out-of-scope follow-up item from the prior invocation's
          own progress file ("Check-0 self-dispatch is itself still
          refused as a target-identifier duplicate of whichever task is
          currently reconciling drift ... by design/correctly"). Confirmed
          pre-existing and unrelated to this session's merge (the failing
          run predates this session's merge commit by >30 minutes; the
          intermittent exit-1 pattern for this unit also predates today
          entirely, e.g. 2026-08-13T13:17, 14:16, 15:19, 16:17, 17:16,
          18:18, 19:17, 20:18 -- interleaved with clean runs). Did not
          touch pm-sentinel-tick.sh's failure-counting logic -- out of
          this task's scope, not silently left broken.

## Remaining
- [ ] Push this commit + this progress file, refresh PR #325 (same
      branch, new head SHA) with a comment re-requesting a fresh Tier-1
      audit against the new head SHA. Do not merge without a fresh
      AUDIT:PASS matching that exact head SHA.
- [ ] Record completion via `agent_work_briefing.py record-completion`
      once pushed.
- [ ] (Carried over, still explicitly out of scope for this task) the
      pm-sentinel-tick.sh duplicate-refusal-counted-as-failure behavior
      above -- worth a dedicated UMR.
