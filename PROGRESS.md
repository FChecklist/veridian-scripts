# PROGRESS -- task-20260813-183133-third-attempt--pm-sentinel-tick-sh-posit

## SPEC
Third redispatch of UMR-20260813-145511-5aca / UMR-20260813-170956-5385
(governing chain UMR-20260806-171945-5767): fix pm-sentinel-tick.sh's
positional `systemctl show` parse + add a guard against an impossible
ActiveState value, with real tests and a real PR.

## Completed
- [x] Re-verified the SPEC's premise independently before acting (known
      task-dispatch false-premise pattern -- see memory
      `veridian-task-prompt-false-premise-pattern`):
  - The SPEC's claim "both prior attempts died WITHOUT opening a PR" is
    **false**. Prior task `task-20260813-171208-fix-pm-sentinel-tick-sh-
    positional-activ` (governed by the same two UMRs) actually completed
    the real fix and pushed it as commit `32b4276` onto the existing open
    PR #299 (FChecklist/veridian-scripts) rather than opening a competing
    PR (its own SPEC's coordination point 4) -- confirmed via
    `git log --all`, `gh pr view 299`, and reading that task's own
    PROGRESS.md (now on `main` via PR #313, commit `025a3f8`).
  - PR #313 (worker/task-20260813-171208-...) *was* opened and *was*
    merged (`8db4abe`, 2026-08-13T17:37:01Z) -- but its diff is
    PROGRESS.md-only; it never carried the actual code fix. The real code
    fix (order-independent parse + duplicate-content-refusal exit-code fix
    + 2 regression tests, `pm-sentinel-tick.sh` + `test_pm_sentinel_tick.py`)
    is sitting complete and passing (8/8) on **PR #299**
    (`worker/task-20260813-123933-add-query-once-decide-and-fix`), which
    was OPEN/MERGEABLE/CLEAN and had simply never been merged.
  - UMR-20260813-170956-5385's DB row had been mislabeled `killed` by
    `reconcile_owner_dispatch_status.py` (a real separate race-condition
    bug, independently RCA'd and fixed by a concurrent task,
    `task-20260813-183210-rca--umr-20260813-170956-5385-killed`, PR #319)
    -- re-confirmed via `resource_governor.py --query-umr` that this row is
    now corrected to `status=completed`. No action needed from this task on
    that row.
  - The live production bug is real and was still active minutes before
    this task started: the real cron log
    (`/opt/veridian/ai-os/logs/pm-sentinel-tick-cron.log`, 18:18 run) shows
    `MISMATCH: UMR-20260808-151244-134c status=running but unit
    veridian-governor-tick.service ActiveState=success Result=active` --
    the exact impossible fingerprint from the SPEC, live, today, still
    unfixed on `/opt/veridian/scripts/pm-sentinel-tick.sh` (the live
    deploy checkout, on an old branch, still has the `--value`/`sed -n
    1p`/`2p` positional read). `veridian-pm-sentinel-tick.service` itself
    is `Active: failed (Result: exit-code)` right now.
  - ACTION 1 (name-keyed parse) was already fully done on PR #299/commit
    `32b4276`. ACTION 2 (a guard that rejects an impossible ActiveState
    value and fails loudly) was **not** present -- a real remaining gap.
- [x] Added ACTION 2 on top of PR #299's existing fix, stacked as a new
      commit on the same branch (continuing the established
      don't-open-a-competing-PR coordination, since PR #299 already *is*
      the real, tested, mergeable vehicle for this fix): a `case`-based
      guard in Check 2b that rejects any `ACTIVE_STATE` value outside
      systemd's real ActiveState enum (active/reloading/inactive/failed/
      activating/deactivating/maintenance/empty), logs a loud
      `IMPOSSIBLE VALUE` line, increments `TICK_FAILURES` (real non-zero
      tick exit), and `continue`s past the MISMATCH/RCA-dispatch check for
      that row entirely -- defense-in-depth against a *future* silent
      re-transposition, not just today's known cause.
- [x] Added `PmSentinelTickImpossibleActiveStateGuardTest` to
      `test_pm_sentinel_tick.py`: feeds a real fake systemctl returning the
      live-reproduced impossible fingerprint `ActiveState=success
      Result=active`, asserts no MISMATCH/no RCA dispatch, a loud logged
      rejection, zero new dispatched rows, and a real non-zero tick exit.
- [x] Targeted test run (order-independent-parse + new impossible-value
      guard tests) passing against the updated script -- see commit for
      full evidence; full 9-test suite run before push.

## Remaining
- [ ] Push the stacked commit to PR #299's branch
      (`worker/task-20260813-123933-add-query-once-decide-and-fix`).
- [ ] Merge PR #299 to `main` (it is purely additive -- 1806/0 net lines,
      new files only, no existing file touched -- clean/mergeable, and is
      the actual fix this SPEC and its two predecessors were chasing).
- [ ] Post evidence (PR comment / commit message) citing the live
      reproduction and the concurrent-task dedup finding.
- [ ] Call `agent_work_briefing.py record-completion` for
      UMR-20260813-175244-0c40.
