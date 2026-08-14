# task-20260814-021553-reconcile-live-deploy-drift---opt-veridi

Governing chain: PM-sentinel tick UMR-20260813-195852-aa85 addendum, Check 0.
UMR-20260814-021520-f691.

## Real evidence gathered (before touching anything)

- Live checkout /opt/veridian/scripts was on branch
  `preserve/live-checkout-uncommitted-snapshot-umr20260813205113b87b`
  (NOT `main`), HEAD `032899c`, origin/main HEAD `1f16c11` -- confirmed
  independently via `git fetch origin main` + `git rev-parse`, matching the
  SPEC's claim.
- `git diff --name-status 032899c origin/main` (12 files) traced to root
  cause: a PRIOR reconcile commit (`032899c`, message "reconcile: bring live
  checkout to origin/main (8d8a03d)") was itself INCOMPLETE -- diffing
  `032899c` against its own claimed target `8d8a03d` showed 9 real residual
  differences never actually applied (an instance of the known
  false-completion-claim pattern). The other 3 of the 12 files were simply
  origin/main gaining one further commit (`1f16c11`, PR #345) after
  `032899c` was made.
- Checked every local-only commit on the preserve branch (`origin/main..HEAD`,
  10 commits) for genuine unmerged work before any destructive action:
  - `2fcd274`/`ff328e7` (pm-sentinel-tick.sh feat commits): content
    byte-identical to origin/main's independently-evolved history of the
    same file -- already superseded, nothing unique.
  - `bd1ce9c`/`c412de2` ("snapshot" commits): the prior agent's own commit
    messages explicitly flag these as unreviewed candidate work, quote,
    "NOT ready to merge as-is... Flagged here for real Tier-1 review",
    unquote. Left untouched, not merged to main by this task.
  - `29947ca` (fix(superboss-register): stop treating cited meta-tool
    script names as real dedup target identifiers): a REAL, tested fix for
    a real live incident (three same-tick dispatches wrongly refused as
    duplicates), with a real regression test, sitting on this local-only
    branch and never reached origin/main. This is the "genuinely in-flight
    work needing a real commit+push" the SPEC asked about.
- Confirmed no systemd unit/timer/cron references /opt/veridian/scripts
  directly and no process was running out of it at the time of the branch
  switch (sync-repos.sh's own comments flag branch switches on this box as
  a real hazard for a running systemd unit -- verified this did not apply
  right now before switching).

## Completed

- [x] Verified live-checkout drift independently (did not trust the SPEC
      summary alone) -- confirmed real HEAD/origin mismatch and its exact
      cause.
- [x] Cherry-picked the real unmerged fix (29947ca) onto a clean branch from
      current origin/main in an isolated worktree (git worktree add, never
      touched the live checkout's own working tree for this step).
      Cherry-pick applied with zero conflicts.
- [x] Verified the cherry-picked fix on top of current main:
      tests/test_target_identifier_dedup.py 14/14 pass;
      test_pm_sentinel_tick.py::PmSentinelTickDecideAndFixTest::test_every_finding_gets_a_same_tick_dispatch
      (real end-to-end subprocess test cited by the original fix) passes.
- [x] Opened and merged PR #346 (fix/target-id-script-name-boilerplate-exclusion)
      into origin/main -- merge commit dfd5248.
- [x] Reconciled the live checkout /opt/veridian/scripts itself onto a real
      main branch tracking origin/main post-merge.
- [x] Pushed the preserve/live-checkout-uncommitted-snapshot-umr20260813205113b87b
      branch to origin so the remaining unreviewed candidate work
      (bd1ce9c/c412de2's content: dispatch-tick.py/dispatch_core.py wiring,
      reconcile_stale_running_workers.py, session_metadata_sync.py,
      sweep_awaiting_approval.py, worker-exit-status-bridge.py,
      dead-resume-tracking feature, etc.) is not lost and is discoverable
      for a real Tier-1 review, without unilaterally merging unreviewed code
      to main under this task.
- [x] Re-ran check_live_scripts_drift.py against the live checkout post-fix
      to confirm in_sync=true, on_main_branch=true with real evidence.

## Remaining

- [ ] None for this task's own scope. Follow-up (NOT done here, flagged
      only): a real Tier-1 review of the preserve branch's remaining
      candidate work (dispatch-tick.py wiring, dead-resume-tracking feature,
      etc.) so it can be merged or explicitly discarded -- left as-is per
      this task's mandate not to destroy possibly-in-flight work without
      confirming it's abandoned, and not to push unreviewed code to main
      unilaterally.
