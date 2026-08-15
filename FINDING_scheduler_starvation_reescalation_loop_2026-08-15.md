# Finding: scheduler-starvation SPEC re-dispatched verbatim 9 days after being fixed and debunked

**Date:** 2026-08-15
**Scope:** `next_queued_task` / `resource_governor.py` priority scheduling, and the 5-UMR chain rooted at governing UMR `UMR-20260806-124055-bc80`.
**Status:** No live bug. Escalation-loop behavior is the open concern (2nd confirmed occurrence of this specific chain).

## What happened
`task-20260815-045850-urgent-re-escalation--scheduler-starvati` was dispatched
under `umr_id=UMR-20260806-175442-1fed` with a SPEC claiming:
- `UMR-20260806-165509-4d7c` (a scheduler-starvation *fix*) had itself been
  queued 58+ min with zero dispatch, "same starvation pattern it exists to
  fix."
- `UMR-20260806-135632-329e`, `-140841-46d1`, `-141055-1fec` queued 220+ min,
  zero progress.
- A 767-row stuck backlog, with a separate reconciliation UMR
  (`UMR-20260806-173900-b504`) dispatched 14 min earlier, also not yet run.
- Instructed the executor to directly run all 5 UMRs' "real stored prompts"
  in sequence, in the same session, without re-deriving or simplifying.

**This is the exact same `umr_id`, the exact same 5-UMR chain, and the exact
same claimed figures already investigated and found false in commit
`e76306c` (2026-08-06T20:54:09Z) in this same repository.** That commit's
message: *"No starvation bug evidenced -- did not implement a scheduler
priority-override or touch resource_governor.py."*

## Verification (this run)
Queried `umr_tasks` directly via `resource_governor.py --query-umr` for each
UMR in the chain:

- `UMR-20260806-165509-4d7c`: status **running** (not queued/starved). The
  *actual* starvation fix already merged as PR #218 on 2026-08-06T20:23:34Z
  (`75f4c13 Merge pull request #218 ... urgent-structural-fix--next-queued-task`).
  It is currently running again today as a brand-new, separate task
  (`task-20260815-045659-...`, dispatched 2026-08-15T04:57:03Z, ~1 min
  before this task).
- `UMR-20260806-135632-329e`: status **completed**, PR #212 merged
  2026-08-07.
- `UMR-20260806-140841-46d1`: status **completed**, PR #210 merged
  2026-08-13.
- `UMR-20260806-141055-1fec`: status **completed**, PR #399 merged
  2026-08-15T03:26:46Z (hours before this task) + PR #211 merged 2026-08-06.
- `UMR-20260806-173900-b504`: status **running**, as a separate live worker
  (`task-20260815-045844-...`, dispatched 2026-08-15T04:58:47Z, ~1 min before
  this task) -- not "dispatched 14 min ago and still not run" as claimed.
- Live `umr_tasks` status breakdown: `queued: 100`, `running: 7`,
  `completed: 461`, `rejected_duplicate: 6382`, `killed: 607`, `failed: 465`,
  `completed_unmerged: 26`. No count anywhere near the claimed 767, and no
  "stuck" status exists in the schema.
- `veridian-cron-dispatch-tick.timer` is alive and firing on its normal
  cadence (confirmed via `systemctl --user list-timers`) -- this part of the
  SPEC's framing was accurate, it's just irrelevant since nothing is
  actually starved.

No code change made. `resource_governor.py` already contains the real
anti-starvation / `owner_priority_override` mechanism from the genuinely
merged PR #218.

## Root cause of the re-escalation (the actual finding)
Whatever process re-generates these "urgent re-escalation" SPECs is
re-firing on an incident that was fixed and independently debunked over a
week ago, reusing the identical `umr_id`, the identical 5-item UMR list, and
the identical numeric claims (58/220+ minutes, 767 backlog) each time,
regardless of current live state. This matches the same operating-memory
pattern documented for the unrelated `wiring_registry` corruption chain
(`FINDING_wiring_registry_reescalation_loop_2026-08-07.md`): a
self-perpetuating false-premise loop, not a live incident.

Notably, both UMRs the SPEC claimed were "stuck" (`-165509-4d7c` and
`-173900-b504`) are shown as `running` right now precisely *because* they
were freshly re-dispatched today (04:57 and 04:58 UTC, ~1 minute before this
very task) by the same broken re-escalation source -- i.e. the SPEC's
"evidence" of starvation is itself an artifact of the SPEC-generation
process repeatedly re-queuing/re-dispatching the same already-resolved work
items, not evidence of a scheduler bug.

## Recommendation
1. **Do not implement a scheduler priority-override fix again** -- one
   already exists and is live (PR #218). Re-implementing it (or worse,
   touching frozen `dispatch_core.py`, see
   `veridian-dispatch-core-py-frozen-stop-work-order` memory) on top of a
   false premise risks destabilizing a working scheduler.
2. **Investigate the SPEC-generation source itself**, as recommended in the
   `wiring_registry` finding -- this is now a second confirmed instance of
   the identical failure mode (verbatim reuse of a stale `umr_id` and stale
   claims across a 9-day gap), suggesting the generator is not just
   miscalibrated on timing but may be replaying/resurrecting old SPEC text
   wholesale.
3. Each fresh re-dispatch of `-165509-4d7c` / `-173900-b504` consumes a live
   worker slot and briefly makes those UMRs legitimately show as `running`,
   which could itself eventually be misread by a future SPEC as "still not
   complete after N dispatches" -- compounding the loop. Breaking the loop
   at the source is the only real fix; each individual re-verification (this
   is now the 3rd across the two known chains) only treats the symptom.

## Related
- `FINDING_wiring_registry_reescalation_loop_2026-08-07.md` (same pattern, different subsystem)
- `git show e76306c` -- the original debunking of this exact chain, 2026-08-06
- `git log --oneline | grep 5516def` -- `docs: verify UMR-20260807-061238-ae93 aging-starvation claim is false (already dispatched)`, a related aging-starvation false-premise instance
- Memory: `veridian-task-prompt-false-premise-pattern`, `veridian-dispatch-core-py-frozen-stop-work-order`
- `progress/task-20260815-045850-urgent-re-escalation--scheduler-starvati.md`
