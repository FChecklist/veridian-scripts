# Finding: SPEC premise false -- PRs 205, 207, 203 are already closed and their work already landed or is correctly obsolete

**UMR:** UMR-20260806-185845-a298 (this task's own dispatch row; unit `veridian-worker@task-20260815-050950-unblock-three-stalled-owner-order-tasks.service`)
**Governing authority cited by SPEC:** UMR-20260806-124055-bc80 -- verified **status=`completed`** in the live `umr_tasks` table, not an active order requiring urgent unblocking.
**Date:** 2026-08-15

## What the SPEC claimed

Three Owner order amendment tasks (181146 / 181155 / 181159, PRs 205/207/203) created "at 18:11" have sat at status `blocked` for ~45 minutes this cycle, stalling the Owner completion bar at "47 of 152" for three sentinel cycles, with 18 rows queued and one worker running against a ceiling of five. PR 205 and PR 207 were "genuinely REJECTED... AUDIT FAIL", both reading `mergeable CONFLICTING` / gate `DIRTY`. PR 203 was "Superboss APPROVED at tier2" with only the merge itself failing, reading `mergeable UNKNOWN`, "the same state PR 201 showed before GitHub recomputed it as cleanly mergeable."

## What independent verification actually found

1. **`blocked` is not a real status in this system.** `umr_tasks.status` has a hard `CHECK` constraint: `queued, dispatched, running, completed, completed_unmerged, failed, rejected_duplicate, sigterm_sent, killed`. No row, and no allowed value, is ever `blocked`.
2. **Tasks 181146/181155/181159 have no `umr_tasks` row at all** (`resource_governor.py --query-umr --search` returns 0 matches for all three). They exist only as `work_items` rows (`WRK-20260806-181148-d316`, `WRK-20260806-181157-1784`, `WRK-20260806-181201-62a8`), all `status='pending'`, **created 2026-08-06T18:11**, i.e. **9 days before this task ran**, not 45 minutes.
3. **All three PRs are already `CLOSED`** (`gh pr view --json state`), not open/blocked:
   - **PR 205** (task 181146): real history is FAIL audit (2026-08-06) -> genuine PASS audit (2026-08-07) -> closed 2026-08-14 as *"Superseded and landed"* -- its already-PASS'd commits were rebased onto `main` (one real additive conflict resolved, re-audited PASS independently) and merged via **PR #392**, merge commit `4bb4955fbc5ea51716000b999c82ca2988e93d23`. Verified independently: that commit **is a real ancestor of `origin/main`**.
   - **PR 207** (task 181155): FAIL -> genuine PASS -> closed 2026-08-14 as a **strict subset of open PR #213**. Verified independently: `git merge-base --is-ancestor refs/prs/207 refs/prs/213` = true, and PR #213's own body independently states *"Builds on top of #207's real branch tip"*. PR #213 is confirmed genuinely `OPEN`.
   - **PR 203** (task 181159): PASS -> a later genuine FAIL (diff is PROGRESS.md-only, delivers none of the PR's stated objective) -> closed as **obsolete**: its entire diff rewrites the shared root `PROGRESS.md`, but commit `1c363b6` on `origin/main` ("fix(worker-entrypoint): per-task progress files + real completion gate...") structurally moved every worker off that shared file onto `progress/${TASK_ID}.md` and added `progress_completion_gate.py`, which explicitly refuses PROGRESS.md-only diffs as completion evidence. Verified independently: `1c363b6` is a real ancestor of `origin/main`, `progress_completion_gate.py` exists on disk, and the current root `PROGRESS.md` today holds an unrelated, much later task's placeholder (`task-20260815-045850-...`) -- confirming the PR's premise no longer holds and merging it now would be a **regression**, not a fix.
   - All three closures cite **`task-20260814-060159`** (a real prior task, one day before this dispatch) as the triage that resolved them.
4. **Registry snapshot at time of this investigation does not match the SPEC's numbers either**: `queued=94` (not 18), `running=4` (not "one... against a ceiling of five" -- and one of those 4 running rows *is this very task*). This SPEC is another instance of the documented false-premise re-dispatch pattern (see `[[veridian-task-prompt-false-premise-pattern]]` in agent memory, and the same-day precedent at commit `2487414`).

## Why no code fix was made

The absolute rule for this task says a Superboss AUDIT FAIL is a real finding and "the only acceptable response is to fix what it identified" -- or, if genuinely mistaken, to say so and leave it rejected. Neither branch applies here: the FAIL audits on 205 and 207 were **already followed by genuine PASS audits** days ago, and the PRs were then **correctly closed for structural reasons** (superseded-and-merged, strict-subset-of-an-open-PR, and obsolete-by-later-refactor respectively) by real prior work, independently re-verified above via git ancestry, `merge-base`, and on-disk file checks -- not by trusting the closing comments at face value.

Re-opening 205/207 and "fixing" them to pass rebase would **recreate content that is either already merged (205, via #392) or already a strict subset of an open PR (207, #213)** -- i.e. it would manufacture the exact kind of duplication the task's own hard rule 2 exists to prevent. Merging 203 would **regress** the per-task-progress-file structural fix that landed after it was opened. So: **no code change was made.** This matches the accepted same-day precedent at commit `2487414` (verified-false-premise, no scheduler-starvation fix built) for exactly this scenario shape.

## Step 5 -- real Owner completion bar (load: 1m/5m/15m = 3.27/4.36/5.62 on 8 cores, moderate)

`generate_platform_completion_checklist.py` (git-archive snapshot pinned to HEAD `872d28d`) reports:
- `scripts: 0/176` (SPEC claimed 47/152 -- both the numerator *and* the 152 denominator are wrong; real total is 176)
- `tables: 48/49`
- `search: 11/11`

Spot-checked the `scripts: 0/176` number: every single script shows pytest `error` (not `fail`) in the checklist's evidence column. Running the same test file directly against the live checkout (`pytest test_gap_status.py`) passes cleanly (7 passed). This means the checklist tool's `git archive` snapshot mechanism is itself producing systemic collection errors independent of any real code defect -- a separate, real anomaly worth a follow-up task, but out of scope for this one (unrelated to the three PRs this task was dispatched to unblock).

## Real evidence recorded

Per-PR outcome recorded via `superboss-register.py log-action --work-item-id <WRK id>` (canonical tool, no raw SQL) for each of the three work items, plus this file and the per-task progress log.

## Outcome

- PR 205, 207, 203: left closed, as-is. No merge, no reopen, no re-audit override.
- No code file changed (none was warranted -- see above).
- Owner order UMR-20260806-124055-bc80: already `completed`, left as-is.
- This task's own UMR (UMR-20260806-185845-a298) marked terminal `completed_unmerged` (docs-only artifact, no mergeable code commit) with this file as evidence.
