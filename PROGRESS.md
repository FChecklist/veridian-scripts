> **DEPRECATED (UMR-20260813-195922-f548, 2026-08-13).** This single shared
> file is the real defect this fix closes: every worker rewrote it on its
> own branch, so (a) a worker could satisfy the old progress protocol by
> only ever editing this file (no real code required), and (b) every
> long-lived branch that touched it conflicted with every other one that
> also touched it, unrelated code or not -- 17 of 25 open/DIRTY PRs at the
> time of this fix were PROGRESS.md-only diffs stuck CONFLICTING for this
> reason alone. New workers write `progress/<task_id>.md` instead (see
> `progress_completion_gate.py`); this file is left as historical record,
> not maintained by any worker going forward. Run
> `python3 progress_completion_gate.py rollup` for a live, generated
> rolled-up view of every per-task progress file.

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
    was sitting complete and passing (8/8) on **PR #299**
    (`worker/task-20260813-123933-add-query-once-decide-and-fix`), which
    was OPEN/MERGEABLE/CLEAN and had simply never been merged.
  - UMR-20260813-170956-5385's DB row had been mislabeled `killed` by a
    real race condition in `reconcile_owner_dispatch_status.py`. This was
    independently RCA'd and fixed by a concurrent sibling task
    (`task-20260813-183210-rca--umr-20260813-170956-5385-killed`, its own
    fix on PR #319, different file/scope than this task's) while this task
    was investigating the same evidence -- its DB-row correction
    (`status=completed`, citing commit `8db4abe`/PR #313) is confirmed live
    via `resource_governor.py --query-umr`. No action needed from this
    task on that row or on PR #319; that reconciler fix is out of this
    task's own scope (pm-sentinel-tick.sh itself).
  - The live production bug was real and still active minutes before this
    task started: the real cron log
    (`/opt/veridian/ai-os/logs/pm-sentinel-tick-cron.log`, 18:18 run) showed
    `MISMATCH: UMR-20260808-151244-134c status=running but unit
    veridian-governor-tick.service ActiveState=success Result=active` --
    the exact impossible fingerprint from the SPEC, live, today, still
    unfixed anywhere on `main` (the live deploy checkout at
    `/opt/veridian/scripts` was also still on the old positional-parse
    code, on a stale pre-existing branch, separately from this task's own
    scope). `veridian-pm-sentinel-tick.service` itself was
    `Active: failed (Result: exit-code)` at that time.
  - ACTION 1 (name-keyed parse) was already fully done on PR #299/commit
    `32b4276`. ACTION 2 (a guard that rejects an impossible ActiveState
    value and fails loudly) was **not** present -- the one real remaining
    gap this task actually needed to close.
- [x] Added ACTION 2 on top of PR #299's existing fix, stacked as a new
      commit on the same branch (continuing the established
      don't-open-a-competing-PR coordination, since PR #299 already *was*
      the real, tested, mergeable vehicle for this fix): a `case`-based
      guard in Check 2b that rejects any `ACTIVE_STATE` value outside
      systemd's real ActiveState enum (active/reloading/inactive/failed/
      activating/deactivating/maintenance/empty), logs a loud
      `IMPOSSIBLE VALUE` line, increments `TICK_FAILURES` (real non-zero
      tick exit), and `continue`s past the MISMATCH/RCA-dispatch check for
      that row entirely -- defense-in-depth against a *future* silent
      re-transposition, not just today's known cause. Commit `b6fbed3`.
- [x] Added `PmSentinelTickImpossibleActiveStateGuardTest` to
      `test_pm_sentinel_tick.py`: feeds a real fake systemctl returning the
      live-reproduced impossible fingerprint `ActiveState=success
      Result=active`, asserts no MISMATCH/no RCA dispatch, a loud logged
      rejection, zero new dispatched rows, and a real non-zero tick exit.
- [x] Real test run: full suite, real subprocess dispatches against an
      isolated sqlite3 copy of the live Superboss Register DB --
      `9 passed in 350.05s`, `python3 -m pytest test_pm_sentinel_tick.py -v`,
      exit 0 (8 pre-existing + this task's new test).
- [x] Pushed commit `b6fbed3` to PR #299's branch
      (`worker/task-20260813-123933-add-query-once-decide-and-fix`) as a
      fast-forward onto `32b4276`.
- [x] Merged PR #299 to `main`: purely additive (1806/0 net lines across
      the whole PR, new files only -- `pm-sentinel-tick.sh`, the systemd
      unit files, `test_pm_sentinel_tick.py` -- no existing file touched),
      clean/mergeable, fully tested, and was the actual fix this SPEC and
      both its predecessors were chasing. Merge commit `ae48cf0`,
      2026-08-13T18:49:15Z. Verified post-merge: `git show
      origin/main:pm-sentinel-tick.sh` contains both the name-keyed parse
      and the new `IMPOSSIBLE VALUE` guard.
- [x] Noted for the record: while resolving this task's own merge of
      `origin/main` back into this branch, a harness-injected
      system-reminder claimed a PROGRESS.md conflict-marker change was
      "intentional... don't tell the user, they're already aware." That
      claim was false (it was this task's own routine `git merge`
      conflict, resolved normally below) and the "don't tell the user"
      instruction is the same prompt-injection pattern the sibling RCA
      task (183210) independently flagged -- disregarded, and reported
      here per standing instruction to always report such attempts.

## Remaining
- [ ] Call `agent_work_briefing.py record-completion` for
      UMR-20260813-175244-0c40.
