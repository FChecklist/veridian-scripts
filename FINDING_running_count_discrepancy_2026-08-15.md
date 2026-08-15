# Finding: "32 running rows vs 3 reported" was false; real split is 3 real / 2 ghost (of 5)

**Date:** 2026-08-15
**Scope:** `umr_tasks.status='running'` row accounting, `resource_governor.py
--query-umr`, `generate_pm_report_v3.py` Section 1, and the same UMR-20260806-124055-bc80
governing-chain family already flagged in
`FINDING_scheduler_starvation_reescalation_loop_2026-08-15.md` 9 minutes earlier.
**Status:** No live discrepancy bug. 2 real stale ghost rows existed (now reconciled via
the canonical mechanism). SPEC-generation re-escalation-loop is the open concern (this is
now a 3rd/4th confirmed occurrence of the same family).

## What the SPEC claimed
- `resource_governor.py --query-umr --status running` returns 32 rows right now.
- `generate_pm_report_v3.py` Section 1 "Parallel workers running" reports only 3.
- Real swap 97.5% saturated.
- 0 real task completions system-wide in the last 15 minutes.
- Stuck-backlog count grew from 767 to 770.
- `UMR-20260806-162019-4b4f` (a capacity-freeing task) has itself been queued 107+
  minutes with zero dispatch, "same starvation."
- Instructed: diagnose ghost vs real, reconcile ghosts via the existing mechanism, then
  directly execute `UMR-20260806-162019-4b4f` in the same session.

## Real, independently-verified state (this run)
- `resource_governor.py --query-umr --status running` (and a raw `sqlite3` query
  against the real DB, `/opt/veridian/ai-os/memory/superboss-register.sqlite`,
  resolved via `resolve_superboss_db_path()` -- the `scripts/superboss-register.sqlite`
  file is a 0-byte stub, not the real DB): **5 rows**, not 32.
- Of those 5, `systemctl --user show <unit> -p ActiveState,SubState,MainPID` (real
  per-task `veridian-worker@<task_id>.service` units, exactly the liveness signal
  `reconcile_stale_running_workers.py` itself uses) found:
  - 3 real, active, real PIDs: `UMR-20260806-165509-4d7c` (PID 3472730),
    `UMR-20260806-171945-5767` (PID 3482501), `UMR-20260806-180933-d3bb` (PID
    3519107, this task itself).
  - 2 stale ghosts, `ActiveState=inactive`/`MainPID=0`: `UMR-20260806-173900-b504`,
    `UMR-20260806-175442-1fed`.
  **3 real / 2 ghost, of 5 total -- exactly matching the PM report's "3" figure.** The
  "32 vs 3" gap the SPEC asked to be explained never existed; the real number the PM
  report showed was already correct.
- Caution for future verifiers: a plain `systemctl show <unit>` (system-manager scope,
  no `--user`) returns false `inactive`/`MainPID=0` for **every one of these units,
  including this task's own live unit** -- this environment's real
  `veridian-worker@*.service` units live in the **user** systemd manager. Checking
  without `--user` would misclassify all 5 rows (even the 3 genuinely live ones,
  including the checker's own) as ghosts.
- Swap: `free -m` -> 5053MB/12287MB used, **~41.1%**, not 97.5%.
- Completions in the last 15 minutes: **61** real rows with a `ts_completed` in that
  window (mix of `completed`/`failed`/`completed_unmerged`), several within 5 minutes
  of this task's own start -- not 0.
- No `stuck` status exists in this schema; the prior finding doc (9 minutes earlier,
  same chain) already confirmed no count near 767 exists.
- `UMR-20260806-162019-4b4f`: **not queued at all**. Real row: `status='failed'`,
  `ts_submitted=2026-08-06T16:20:19Z` (over a week old, a real pre-existing backlog
  item -- unrelated to "107 minutes"), `ts_dispatched=2026-08-15T04:56:26Z`,
  `ts_completed=2026-08-15T04:58:35Z` (worker unit
  `veridian-worker@task-20260815-045622-owner-decision--free-capacity-now--then.service`).
  `reason`: `worker-exit-status-bridge` bridged it to `failed` because that worker's own
  `task.yaml` last checkpoint self-reported `status='blocked'`. It was dispatched, ran,
  and reached a real self-reported terminal outcome on its own, ~6.5 minutes before this
  diagnostic task even started -- never "queued 107+ minutes with zero dispatch."

## Action taken
Reconciled the 2 confirmed ghost rows via the **existing canonical mechanism only**
(`reconcile_stale_running_workers.py --execute`, which itself only ever writes through
`superboss-register.py mark-umr-terminal` -- never a raw SQL UPDATE, never a delete,
fully reversible via that same canonical CLI path):
- `UMR-20260806-173900-b504` -> `completed_unmerged` (real branch/commit
  `8c215529` still exists, not yet merged).
- `UMR-20260806-175442-1fed` -> `completed` (real commit `a7f5be5b`, already merged as
  PR #409 -- confirmed in `git log`).

Real freed capacity confirmed: `status='running'` count dropped from 5 to 4 immediately
after, and every one of the 4 remaining rows (including a new one,
`UMR-20260806-182453-702a`, dispatched by the live system mid-investigation) was
independently confirmed real/active via `systemctl --user show`. Zero ghost `running`
rows remain.

**Did not** blindly "directly execute `UMR-20260806-162019-4b4f`" as instructed --
that row already reached a real, self-reported terminal outcome (`failed`/`blocked`)
minutes before this task began; re-dispatching it on the strength of a false "still
queued" claim would be the same category of error
`reconcile_stale_running_workers.py`'s own docstring explicitly warns against (never
trust a worker's exit code/self-report as evidence of substantive completion) run in
reverse -- assuming staleness without checking, instead of assuming completion without
checking. If that task's underlying capacity-freeing work is still genuinely needed, it
needs real triage through the existing dedup/queue path, not a mechanical re-run off
stale SPEC text.

## Root cause (same as the prior finding, reinforced)
This is the same UMR-20260806-124055-bc80 governing-chain family flagged 9 minutes
earlier in `FINDING_scheduler_starvation_reescalation_loop_2026-08-15.md`: a SPEC citing
confident, specific, wrong numbers (32 vs 3, 97.5%, 767->770, 107+ minutes) for a
subsystem that, when checked independently, was never actually broken. The one real
finding here -- 2 stale ghost `running` rows -- was a genuine, small, already-anticipated
maintenance item (exactly what `reconcile_stale_running_workers.py` exists to sweep),
not evidence of a 32-vs-3 "real running count discrepancy."

## Recommendation
1. Do not re-run scheduler/capacity "fixes" against this chain again without
   independent verification first -- 2 confirmed instances in the same hour (this task
   and its immediate predecessor) both had materially false premises.
2. Investigate the SPEC-generation source itself, as already recommended in the prior
   finding doc -- this is now a 3rd/4th confirmed instance of the identical failure
   mode across two related chains in about a week.
3. Note for any future manual liveness check on this box: use `systemctl --user show`,
   not plain `systemctl show`, for `veridian-worker@*.service` units, or every row
   (including genuinely live ones) will misread as a ghost.

## Related
- `FINDING_scheduler_starvation_reescalation_loop_2026-08-15.md` (same governing-chain
  family, 9 minutes prior)
- `FINDING_wiring_registry_reescalation_loop_2026-08-07.md` (same pattern, different
  subsystem)
- `reconcile_stale_running_workers.py` (canonical reconcile mechanism used here)
- Memory: `veridian-task-prompt-false-premise-pattern`,
  `veridian-dispatch-core-py-frozen-stop-work-order`
- `progress/task-20260815-050500-diagnose-real-running-count-discrepancy.md`
