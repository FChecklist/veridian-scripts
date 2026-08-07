# SPEC verification -- task-20260806-222550-resolve-the-two-stale-queued-rows-blocki

Per this repo's documented false-premise-pattern history (23+ prior cases; see e.g.
`ab23324`, `1a0d138`), verified the SPEC's claims against the real, canonical DB
(`/opt/veridian/ai-os/memory/superboss-register.sqlite` -- **not**
`scripts/superboss-register.sqlite`, which is a stale decoy per that file's own
`resolve_superboss_db_path()` docstring) before taking any action.

## Claim vs. real current state (checked 2026-08-06T22:2x UTC)

| SPEC claim | Real state, verified directly |
|---|---|
| `UMR-20260729-112414-3269` queued 189.8h, blocking dispatch | `status='completed'`. Dispatched 2026-08-06T10:42:18Z, completed 2026-08-06T11:17:18Z via the heartbeat-sweep reconciliation path (`reason`: "reconciled by heartbeat sweep: unit ...inactive, last_heartbeat stale, real exit status=completed"). |
| `UMR-20260804-064310-f247` queued 50.5h, blocking dispatch | `status='killed'`. Dispatched 2026-08-06T10:42:22Z, reconciled 2026-08-06T14:30:55Z (`reason`: "Reconciled from phantom running state...no live process found via `ps`. Real GitHub evidence: PR #977 (compliance-tracker) still OPEN/unmerged...Reconciled to killed"). |
| "already surfaced independently as pm_decisions_pending rows 22 and 23" | True as of 09:13:44 UTC this cycle -- but both rows were already `status='superseded'` by 16:51:21 UTC, folded into aggregate row id=185 ("STALE-QUEUED-AGGREGATE") by a one-time migration off the one-row-per-umr_id emission shape, landed in **PR #196 (commit e7fea42)**, already merged. |
| "zero veridian-worker units running... against a real concurrency cap of 5" | **5 active/running units right now**, incl. this task's own worker unit. `systemctl --user list-units 'veridian-worker@*' --state=active,running` returns exactly 5. |
| load 11.21 | Real current load average: 3.78, 3.54, 5.47 (`uptime`). |
| "the existing reconciliation logic does not treat these rows as actionable" | It did: both rows were reconciled hours before this task was dispatched, via the canonical heartbeat-sweep path (not raw SQL), each carrying a specific evidenced reason. |
| Step 4: "extend reconciliation so a queued row older than a defensible threshold is detected and surfaced automatically" | **Already built and running**: `flag_stale_queued_tasks()` in `resource_governor.py` (line 1410), wired into every `run_tick()` call (line 1515), threshold `MAX_QUEUED_AGE_SECONDS` = 4.0h (`VERIDIAN_GOVERNOR_MAX_QUEUED_AGE_S`, default `4*60*60`), inserts one idempotent `STALE-QUEUED:` row per stale `umr_id` via the canonical `insert_pm_decision_pending()` (never raw SQL), skips rows that already have an open flag. Confirmed live: it opened rows 279-288 at 21:16:32 UTC today for the 8 *currently* stale (>4h) queued rows -- ~66 minutes before this task started. |

## Cross-reference

`UMR-20260729-112414-3269`'s "completed" status was *already independently found and
documented* by an earlier task on this same branch history, `task-20260806-212450`
(its own UMR: `UMR-20260806-092722-e526`, merged via PR #227, commit `bf5f973`) --
reaching an identical conclusion from an identical direct DB query. This is now the
second consecutive task cycle in which this exact already-terminal row has been
re-presented as a current urgent blocker; the "queued 189.8h" figure appears to be a
stale, cached measurement being recirculated rather than a fresh read.

## Conclusion

Both cited rows genuinely were stuck exactly as described, **as of ~09:13-10:20 UTC this
cycle** -- the SPEC's root-cause analysis of `next_queued_task`'s aging-priority tiebreak is
accurate and was a real, evidenced incident. But by 14:30:55 UTC (nearly 8 hours before this
task was dispatched at 22:25 UTC), both rows had already reached the correct real terminal
status through the canonical reconciliation path, with reason and evidence recorded on each
row, and the requested Step 4 auto-detection extension already exists, is deployed, and is
actively running every tick.

Taking the SPEC's requested actions now would be redundant at best (re-closing
already-terminal rows) and duplicative at worst (a parallel `flag_stale_queued_tasks`
reimplementation racing the one already in production). Per repo convention, no `umr_tasks`
or `pm_decisions_pending` write was made by this task.

**No PR opened** -- there is no real code change or row correction to make; both requested
outcomes were already true. Reporting this verification in `PROGRESS.md` instead, matching
prior no-op-verified cases in this repo's history.
