# PROGRESS -- task-20260806-205156-phase-0--real-stale-backlog-reconciliati

## Completed
- [x] Ran `resource_governor.py --reconcile-stale` (real dry run) -> `{"actions": []}` (0 actions)
- [x] Ran `resource_governor.py --reconcile-stale --execute` (real writes) -> `{"actions": []}` (0 actions, 0 writes)
- [x] Regenerated STUCK_TASKS_HEARTBEAT.json for real via the actual production path
      (`systemctl --user start veridian-cron-dispatch-tick.service`, exit 0/SUCCESS,
      not a raw script invocation of the mutating full tick) -> new `generated_at`
      2026-08-06T20:56:35Z, stuck count 775 (blocked_task_count 777)
- [x] Investigated why `--reconcile-stale` does not reach these rows and confirmed the
      real, specific, structural reason (see below) -- not a null result

## Root-cause finding (real, code- and evidence-verified)

SPEC's premise does not hold: `resource_governor.py --reconcile-stale --execute` cannot
reduce STUCK_TASKS_HEARTBEAT.json's stuck-task count, by construction, and the real
evidence confirms it made zero difference.

1. **STUCK_TASKS_HEARTBEAT.json's `stuck_tasks` list is task.yaml-level, not
   umr_tasks-level.** It's built by `dispatch-tick.py:find_stuck_tasks()`, which only
   looks at task.yaml docs with `status == "blocked"` whose `last_checkpoint_at` is
   stale (>30min). Per that function's own docstring: "Blocked is a
   terminal-for-automation status ... nothing else on the box will touch it again
   without a real PM decision."
2. **`resource_governor.py:reconcile_stale_heartbeats()` (the `--reconcile-stale`
   function) never reads task.yaml files at all.** Its entire scope is one SQL query:
   `SELECT * FROM umr_tasks WHERE status IN ('running','dispatched') AND
   last_heartbeat IS NOT NULL AND last_heartbeat < cutoff` (superboss-register.sqlite,
   a completely different table/status vocabulary from task.yaml's `blocked`). There is
   no code path connecting the two. This is confirmed live: the dry run and the execute
   run both returned 0 rows examined -- not because writes were skipped, but because
   zero rows in `umr_tasks` currently match that WHERE clause at all.
3. **Live counts (2026-08-06T20:56:35Z umr_tasks GROUP BY status):** running=30,
   queued=1, failed=452, rejected=6376, retrying=2, completed=453, killed=607,
   total=7949. There is no `blocked` or `dispatched`-with-stale-heartbeat bucket
   feeding the stuck count -- the 775 stuck rows are exclusively task.yaml
   `status=='blocked'`, a status `--reconcile-stale` was never designed to touch.
4. **Independently corroborated by a prior real attempt**, UMR-20260806-112310-7655
   (11:23 UTC today, `run-reconcile-stale-sweep-20260806-1123`, `outputs_json={}`,
   `ts_completed=NULL` as the SPEC notes -- but its `metadata_json` **does** contain a
   real, substantive finding, it just wasn't written back as a completion output):
   `reconcile_stale_heartbeats()`'s WHERE clause also structurally excludes an entire
   dispatch class (tmux-relay dispatches via dispatch-owner-task.sh, which have no
   `unit_name` and thus never get a `last_heartbeat` write at all -- that field is only
   populated by the systemd worker checkpoint loop). Same root defect class as finding
   #2 above (the sweep's WHERE clause is structurally blind to whole categories of real
   rows), reached via a different angle (NULL heartbeat vs. task.yaml-level status).

**Net: before=767 (SPEC, 17:32:57Z) / observed-before=774 (20:52:24Z, already
regenerating live every ~7min via `veridian-cron-dispatch-tick.timer`) / dry-run
actions=0 / execute actions=0 (0 real writes) / after=775 (20:56:35Z, real fresh
regeneration).** The count did not drop -- it grew slightly, consistent with zero
effect from `--reconcile-stale`, because these are `blocked` task.yaml rows requiring
a real PM decision per the code's own design, not stale-heartbeat `umr_tasks` rows.
`--reconcile-stale --execute` is not the right tool for this backlog; no code change
was made to force it to be, per the verify-before-write policy -- don't force a write
path that the evidence shows is structurally a no-op.

## Remaining
- [ ] None for this task's ask (investigate + report). Actually clearing the 775
      blocked task.yaml backlog is a separate, real PM-decision-gated remediation (per
      `find_stuck_tasks()`'s own docstring) outside `--reconcile-stale`'s scope --
      out of scope for this SPEC, flagged here for a follow-up UMR if desired.
