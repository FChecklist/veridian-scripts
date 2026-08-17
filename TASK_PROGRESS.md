# Task: Establish Ground Truth for Every Non-Terminal Row

**Objective**: Fix the managed work register which has rows claiming states that don't match reality.

**SPEC** defines:
- TRULY_ACTIVE: live worker, progressing (leave alone)
- PHANTOM: claims running/dispatched but no worker alive (reconcile)
- DONE_MISLABELLED: says failed/blocked but work landed (correct with SHA)
- FAKE_COMPLETE: says complete but diff touched no shippable code (reopen)
- GENUINELY_PENDING: real work, not started/finished, still wanted (leave open)
- OBSOLETE: superseded/duplicated (recommend retiring, don't delete)

## Completed

- [x] Step 1: Enumerate every non-terminal row (before-count: 73 rows)
  - 53 rows in `completed_unmerged` status
  - 20 rows in `running` status
  - 0 rows in `queued`, `dispatched`, or `sigterm_sent`
- [ ] Step 2: Gather ground truth evidence for each row
- [ ] Step 3: Classify each row into the closed set
- [ ] Step 4: Perform unambiguous actions
- [ ] Step 5: Report GENUINELY_PENDING and OBSOLETE lists

## Current Findings

**Running rows (20 total)**:
- ALL are `owner-task-*` type identities
- ALL have last_heartbeat = NULL (never sent heartbeat)
- 14 from 2026-08-16 (yesterday, many hours old)
- 5 from 2026-08-17 (today)
- 1 from 2026-08-15 (two days old)
- Most have reason="queued" but status="running" (inconsistency)
- None have corresponding git branches

**completed_unmerged rows (53 total)**:
- Marked as complete (ts_completed is set) but PR not merged
- Include various task types: RCA tasks, GTM tasks, retry tasks
- Span from 2026-08-05 to 2026-08-16

## Remaining

- [ ] Resolve ambiguity: what should owner-task "running" status mean?
- [ ] Check if completed_unmerged rows' PRs were eventually merged
- [ ] Check live deployed checkout for completed_unmerged work
- [ ] Classify and reconcile each row
- [ ] Record final verdict with head SHA

## Notes

- Register: `/opt/veridian/scripts/superboss-register.sqlite`
- UMR_ACTIVE_STATUSES: queued, dispatched, running
- Also check: completed_unmerged, sigterm_sent (intermediate states)
