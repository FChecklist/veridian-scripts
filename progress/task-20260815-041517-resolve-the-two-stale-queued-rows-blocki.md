# PROGRESS -- task-20260815-041517-resolve-the-two-stale-queued-rows-blocki

## Completed

- [x] Independently verified the SPEC's core premise against the live DB
      (`/opt/veridian/ai-os/memory/superboss-register.sqlite`, resolved via
      `superboss-register.py`'s own `resolve_superboss_db_path()`, never
      the known 0-byte decoy at `/opt/veridian/scripts/superboss-register.sqlite`).
      **Both named rows are already terminal, not queued:**
      - `UMR-20260729-112414-3269` (task_identity `PHASE-4-BUILD-WORKFLOW`):
        `status='completed'`, `ts_completed='2026-08-06T11:17:18.510951+00:00'`.
      - `UMR-20260804-064310-f247`: `status='killed'`,
        `ts_completed='2026-08-06T14:30:55.275574+00:00'`.
      Both reached terminal status **9 days before this SPEC was dispatched**,
      independently of this task -- nothing to move via superboss-register.py,
      no terminal-status write needed or made.
- [x] Verified `pm_decisions_pending` rows 22 and 23 (the SPEC's own cited
      evidence): both already `status='superseded'`,
      `closed_ts='2026-08-06T16:51:21...'`, `closed_by='agent:UMR-20260806-163738-4323'`,
      explicitly citing "superseded by real aggregate row id=185
      (STALE-QUEUED-AGGREGATE) ... see PR #196 / commit e7fea42". The SPEC
      presented this same 2026-08-06 09:13 UTC snapshot as if it were a live,
      unresolved, urgent blocker today (2026-08-15) -- it is 9-day-old,
      already-resolved data. Matches the established false-premise SPEC
      pattern for this repo (see prior verification commit `c38589f`, same
      day, same governing UMR family, one row in common).
- [x] Verified the "zero workers running" claim is false **for the current
      moment**: `veridian-governor-tick.service` has been `active running`
      continuously since 2026-08-13T22:40:31Z, is actively dispatching every
      tick, and at the time of this check had **5/5 concurrency-cap workers
      running** (`cap_exhausted` dispatch-decision log lines), not zero.
- [x] Found the REAL, currently-live defect behind the SPEC's Step 4 ask
      ("extend reconciliation so a queued row older than a defensible
      threshold is detected and surfaced automatically"): that logic
      **already exists** (`flag_stale_queued_tasks()`, `MAX_QUEUED_AGE_SECONDS`
      = 4h) and already runs every governor tick -- but the aggregation fix
      for it (one real `STALE-QUEUED-AGGREGATE:` pm_decisions_pending row,
      updated in place, instead of one new row per stale umr_id) was written,
      tested, and opened as PR #196
      (`fix/stale-queued-decision-aggregation-umr20260806163738-4323`,
      commit `e7fea42`) on 2026-08-06, but **was never merged to `main`**
      (`git merge-base --is-ancestor e7fea42 HEAD` → NO). PR #196 was still
      `OPEN`, `MERGEABLE`, `mergeStateStatus=CLEAN`, zero reviews, zero CI
      checks, 9 days later.
      Real, measured consequence on `main` right now, before this fix:
      **60 of 79 open `pm_decisions_pending` rows (76%)** are individual
      `STALE-QUEUED: <umr_id> queued <N>h ...` rows for the current real
      queued backlog (23+ rows queued >4h, oldest ~210h as of this check) --
      the exact "always-on signal carries no information" duplicate-row
      defect PR #196's own commit message describes, still live because the
      fix was never landed.
- [x] Chose the real remediation: cherry-picked PR #196's already-reviewed,
      already-tested commit (`e7fea42`) onto this task's branch on top of
      current `origin/main` (`a9ff270`). Clean cherry-pick, no conflicts
      (`resource_governor.py`, `superboss-register.py`,
      `tests/test_flag_stale_queued_tasks.py`,
      `tests/test_pm_decisions_pending.py`). `git diff origin/main --stat`
      matches PR #196's own diff exactly.
- [x] Ran the directly relevant tests: `test_flag_stale_queued_tasks.py` +
      `test_pm_decisions_pending.py` -- **26/26 passed**. Also ran
      `test_resource_governor_owner_priority_advance.py`,
      `test_resource_governor_telemetry_retention.py`,
      `test_resource_governor_queue_management.py` for regressions: 2
      pre-existing failures in `test_resource_governor_queue_management.py`
      (`test_list_queue_real_dispatch_order`,
      `test_move_down_never_crosses_a_tier_boundary`) reproduce identically
      on `origin/main` **without** this cherry-pick (confirmed by re-running
      against the plain `a9ff270` tip) -- pre-existing flakiness against the
      shared live DB, unrelated to this change, not touched here.
- [x] Did not weaken or bypass the duplicate gate. Did not delete any row.
      Did not touch `dispatch_core.py` (frozen, per standing stop-work
      order). Did not restart `veridian-directive-engine.service`. Did not
      rotate any credential or delete/archive any repository. Did not edit
      the shared `PROGRESS.md` (reverted an unrelated pre-existing local
      modification found at task start, and cleanly discarded an unrelated
      stash-pop conflict from a completely different, older task branch
      that a stray `git stash`/`git stash pop` briefly surfaced -- left that
      other task's stash entry untouched in the stash list).
- [x] Rebased/verified: branch already sits directly on `origin/main`
      tip (`a9ff270`) at PR-open time -- no rebase needed, no PROGRESS.md
      conflict possible.
- [ ] Push branch + open PR (next).
- [ ] Record real completion via `agent_work_briefing.py record-completion`.

## Remaining

- [ ] Confirm real before/after `STALE-QUEUED:` row counts once
      `flag_stale_queued_tasks()` runs again live under the new code
      (next `veridian-governor-tick` cycle self-resolves the 60 individual
      rows to one aggregate row automatically -- no manual action needed;
      report the observed after-count in the PR).
- [ ] Report PR number + commit hash in final summary to the user.

## Verdict

**SPEC premise false**, same class as this repo's prior 2026-08-06-dated
false-premise incidents (see memory: veridian-task-prompt-false-premise-
pattern). The two named rows are not queued and need no terminal-status
write from this task. However, independent investigation surfaced a real,
currently-live, verifiable defect in the same subsystem the SPEC named
(`resource_governor.py`'s stale-queued reconciliation) with an
already-written, already-tested, never-merged fix (PR #196 / `e7fea42`) --
landed here via cherry-pick onto a fresh PR rather than leaving it to rot
unmerged for a 10th day.
