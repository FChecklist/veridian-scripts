# Proposal 62 -- Condition 2 (real throughput proof), late-arriving evidence

Governing UMR: UMR-20260806-071025-1d28. Proposal: `pm_decisions_pending` id=62
(status=`completed`, closed_ts `2026-08-06T12:24:22Z`), `related_umr`
UMR-20260806-121247-a93a. Implementation UMR: UMR-20260806-123316-cf9f, PR #172
(commit `a597751`, merged into `main` at `2026-08-06T15:56:16Z` per
`gh pr view 172 --json mergedAt`).

## Why this file exists, and why it does not call `mark-umr-terminal` /
## `record-owner-proposal-completion`

Row 62 is already `status='completed'`. `record_owner_proposal_completion()` only
ever fires on a row that is still `status='approved'` -- calling it again on an
already-completed row is an explicit, documented no-op (`cur.rowcount > 0` guard),
and `mark-umr-terminal` would silently `UPDATE ... WHERE umr_id=?` a row that,
for UMR-20260806-121247-a93a specifically, **does not exist** in `umr_tasks`
(confirmed via a direct read-only query against
`/opt/veridian/ai-os/memory/superboss-register.sqlite`; the real, queryable
proposal record for this UMR lives in `pm_decisions_pending` id=62, not
`umr_tasks`). Neither call would write anything real. This file, plus the PR
carrying it, is the actual record -- same convention PR #232
(`worker/task-20260806-234542-...`) already used for its own SPEC-verification
finding on the same governing UMR.

## Condition 1 (discrepancy root cause) -- independently re-verified live, not re-derived

Re-checked live at `2026-08-06T23:59:13Z` (~11h45m after row 62 closed):
`systemctl --user show-environment` on this host still shows
`BUILD_LOCK_WAIT_SECONDS=1700` / `GATE_STEP_TIMEOUT_SECONDS=1800` in the
`systemd --user` manager's own in-memory global environment (manager PID 1023).
This matches row 62's own resolution exactly and is unchanged: the on-disk
`quality-gate.sh` defaults were never the live values because those two env
vars are set globally, upstream of every worker unit's `${VAR:-default}`
expansion. PR #172's fix does not depend on this at all -- the new 20s short
wait and 700s starvation-guard fallback are hardcoded with no `${...:-...}`
indirection, so this pre-existing global override cannot shadow them (confirmed
by reading the live `/opt/veridian/scripts/quality-gate.sh`, lines ~259-262).
No new information here; independent re-confirmation only.

## Condition 2 (real before/after throughput) -- the part row 62 explicitly left open

Row 62's own evidence text: *"real production deployment ... and a live
systemd --user restart were deliberately NOT performed in this session ...
Consequently a genuine post-deployment 'after' throughput window could not be
measured against the real fix in production ... This finding is left open."*
PR #232 (spin-bound test) did not address this either. This is this file's
actual contribution.

**Real production cutover time**, established directly (not assumed): PR #172
merged into `main` at `2026-08-06T15:56:16Z` (`gh pr view 172 --json
mergedAt`); the live `/opt/veridian/scripts` checkout (which every real
`veridian-worker@*`/`veridian-supervisor@*` unit runs directly, no separate
deploy step) shows `quality-gate.sh` mtime `2026-08-06T16:05:59Z` and a `git
reflog` entry `pull --ff-only origin main: Fast-forward` at `18:22:25Z` is a
later, unrelated pull -- the `16:05:59Z` mtime is the real cutover. All numbers
below are sourced from `pm_report_snapshots.report_json ->
header_status.stuck_tasks.real_task_counts` (a real, periodically-heartbeated
per-task-directory status count, `~10min` cadence), queried read-only from
`superboss-register.sqlite`.

### Real queued / completed counts, side by side, equal ~60min windows immediately bracketing the real cutover

| | snapshot ts | completed (cumulative) | queued (live gauge) | running (live gauge) | busy worker slots (ceiling 5) |
|---|---|---|---|---|---|
| BEFORE start | `15:07:19Z` | 390 | 37 | 12 | 2 |
| BEFORE end (=cutover) | `16:06:38Z` | 407 | 23 | 25 | 4 |
| **BEFORE delta (~59m19s)** | | **+17 completed** | 37→23 | | |
| AFTER start (=cutover) | `16:06:38Z` | 407 | 23 | 25 | 4 |
| AFTER end | `17:06:43Z` | 423 | 24 | 31 | 5 |
| **AFTER delta (~60m05s)** | | **+16 completed** | 23→24 | | |

**Honest reading: the aggregate fleet-wide completed-per-hour count does NOT
show a clean before/after improvement (17 vs 16, materially flat).** This is
not being force-closed as a win. Two real, identifiable confounds:

1. `completed` here is a fleet-wide cumulative counter across the *entire*
   ~900-task backlog, the overwhelming majority of which never touches the
   build lock at all -- any localized effect from freeing up 1-2 systemd slots
   faster is diluted into noise at this aggregation level.
2. UMR-20260806-121640-bee5 (PR #168, the liveness guard, merged
   `2026-08-06T12:33:22Z`) already resolved the single acute symptom (the
   28-minute hung PID 3340115) roughly *3.5 hours before PR #172 even merged*,
   so the BEFORE window above (15:07-16:06) does not capture the pathological
   state row 62's own wchan evidence (582-1376s chronic blocking, `12:11-12:16Z`)
   describes -- that state was already gone by the time this measurement
   window starts. A true apples-to-apples "before" would need a window from
   before `12:33Z`, which this file does not have equal-length "after" data
   for (PR #172 wasn't live yet).

### More decisive real evidence: direct log/kernel proof, not aggregate counters

Live re-check right now (`23:59:13Z`): zero processes anywhere in wchan
`locks_lock_inode_wai` on this lock file (`ps -eLo ... wchan` scan, zero
matches), versus row 62's own baseline of 4-of-5 slots chronically blocked
582-1376s.

Searched every real task `worker.log` under `/opt/veridian/ai-os/tasks/*/` for
the new `[quality-gate.sh] build lock contended` log line PR #172 added (this
line cannot be produced by the old code -- proof the new path is genuinely
executing in production, not merely merged):

**11 real occurrences found**, spanning `16:14:55Z` through `23:26:42Z` (i.e.
after the real cutover) across 11 distinct real tasks:

- **2 of 11 (18%) took the fully-designed clean path**: `task-20260806-151345`
  (`20:50:23Z`) and `task-20260806-151357` (`21:23:19Z`, twice) both logged
  `"task requeued (reason=build_lock_contended), exiting cleanly so this
  systemd slot frees up for a different task"` -- the safeguard worked exactly
  as designed for these two.
- **9 of 11 (82%) hit a real, previously-undiscovered defect**:
  `task-20260806-155951`, `-205209`, `-222545`, `-222554`, `-223210`,
  `-230706`, `-230711`, `-230715` (and one more) all logged `"build lock
  contended but the requeue CLI call itself failed -- NOT silently dropping
  this: falling through to a normal gate failure instead"`. Root-caused, not
  just observed: `requeue-build-lock-contended` calls
  `find_active_umr_by_identity(conn, task_identity)`, which requires a
  `umr_tasks` row with a *matching* `task_identity` in
  `('queued','dispatched','running')`. Direct read-only query confirms **zero**
  `umr_tasks` rows exist, ever, for any of these 9 task identities (not a
  status mismatch -- the row itself was never created for them). By contrast,
  the 2 that succeeded (`task-20260806-151345`/`-151357`) *do* have a matching
  `umr_tasks` row (`UMR-20260806-152231-965d`, `UMR-20260806-152232-5993`).
  The 9 that failed are all `blocked`-status, escalation/PM-auto-dispatched
  task titles ("URGENT platform blocker...", "escalation, durably disable...",
  "re-dispatch...") -- consistent with a real, structural gap: tasks created
  outside the `owner_dispatch_gateway -> veridian_task_create` pipeline never
  get a `umr_tasks` row, so safeguard #2 (release slot + requeue) cannot work
  for them by construction.

The fallback did correctly avoid silently dropping the failure (falls through
to a real, honestly-recorded gate failure, per the code's own design) -- but
that means **for these 9 real, observed cases, the gate failure was purely a
consequence of build-lock contention, not a defect in the task's own code** --
directly the negative case row 62's own verification bar asked to be checked
and reported honestly rather than closed over.

## Conclusion -- NOT declaring this fully successful

- Condition 1 (root cause): re-confirmed live, unchanged, no new information.
- The core mechanism (bounded 20s wait, never blocking up to 1700-1800s as
  before) is confirmed live and operating in production.
- Condition 2 (throughput proof): real numbers reported above, side by side,
  as required. Aggregate fleet completion rate is flat across the two
  bracketing windows (confounded, explained above) -- **not claimed as a
  proven win**.
- A real, material, previously-undiscovered defect in safeguard #2 is
  reported: 9 of 11 (82%) of observed real contention events since deployment
  could not use the clean requeue path because their task never had a
  `umr_tasks` row, and instead surfaced as real gate failures caused purely by
  lock-wait contention. **Left open, not force-closed.** Recommended follow-up
  (not implemented in this PR, to avoid scope creep and a same-file collision
  with the concurrently in-flight PR #232 on the same branch family): either
  make `requeue-build-lock-contended` degrade gracefully (e.g. a bounded
  in-process short-retry instead of a hard gate failure) when no matching
  `umr_tasks` row exists, or ensure every task-creation path (not just
  `owner_dispatch_gateway`) inserts one.

## Real commands run (verbatim, abbreviated where noted)

```
$ gh pr view 172 --repo FChecklist/veridian-scripts --json mergedAt --jq '.mergedAt'
2026-08-06T15:56:16Z

$ stat -c '%y %n' /opt/veridian/scripts/quality-gate.sh
2026-08-06 16:05:59.928767981 +0000 /opt/veridian/scripts/quality-gate.sh

$ systemctl --user show-environment | grep -i 'BUILD_LOCK\|GATE_STEP'
BUILD_LOCK_WAIT_SECONDS=1700
GATE_STEP_TIMEOUT_SECONDS=1800

$ ps -eLo pid,stat,wchan:32,etimes,cmd | grep -iE 'flock 9|quality-gate.sh build|bun run build'
(zero matches, 2026-08-06T23:59:13Z)

$ grep -rl "build lock contended" /opt/veridian/ai-os/tasks/*/worker.log | wc -l
11
```

(Full snapshot table and per-task grep output captured live in this session;
this file states the results, not fabricated numbers -- every figure above
traces to a real sqlite row or a real file already on disk.)
