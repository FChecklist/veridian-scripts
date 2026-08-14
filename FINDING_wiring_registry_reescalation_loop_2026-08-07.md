# Finding: `wiring_registry` corruption -- real incident, real fix, then a self-perpetuating false-premise re-escalation loop

**Date:** 2026-08-07
**Scope:** `wiring_registry` table in `/opt/veridian/ai-os/memory/superboss-register.sqlite`
**Status:** DB healthy as of this writing. Escalation-loop behavior is the open concern.

## Timeline (all times UTC, 2026-08-06/07)

1. **~19:40-19:53** -- `wiring_registry` genuinely corrupts (`database disk image is
   malformed`). Best-supported (not certain) cause: this box's recurring swap exhaustion
   under load, not a concurrent-writer lock collision -- see `task-20260807-003146`'s
   root-cause note (no sudo/kernel-log access to confirm an OOM-kill directly; internal
   `health-15min.log` RAM samples for the window were unremarkable, but swap itself wasn't
   directly observable, and this same box was independently observed swap-exhausted again
   ~5.5h later during the repair itself).
2. **00:40:29 - 00:53** (`task-20260807-003146`) -- Real repair, real evidence:
   - Forensic file-level backup of all 3 live WAL-mode files taken first
     (`superboss-register.sqlite.corrupt-wiring-registry-real-20260807T004029Z` + `-wal`/
     `-shm`, md5 recorded).
   - `sqlite3 .recover` run against the **backup copy** (not live, to avoid extra load):
     clean recovery, 24,281 rows.
   - Old corrupt `wiring_registry` **renamed aside** (not dropped) to
     `wiring_registry_corrupted_orig_20260807T004638Z`, deliberately, for forensics on top
     of the file-level backup.
   - Fresh `wiring_registry` + FTS5 index recreated; the 24,281 recovered rows restored
     into it; `PRAGMA integrity_check` clean against the live table (2 remaining findings,
     both explicitly scoped to the intentionally-preserved old remnant table, not the live
     one).
   - Attempt to pause `veridian-cron-generate-wiring-registry.timer` first (SPEC's step 1)
     was blocked in that task by lack of interactive polkit/sudo access in that session --
     documented as an accepted residual risk, not silently skipped.
3. **051409 task (~05:14-05:15)** -- Confirms the 003146 rebuild is real and holding; drops
   the now-redundant renamed-aside remnant table (`wiring_registry_corrupted_orig_...`,
   already covered by the untouched file-level backup) under the repo's cooperative
   `.writelock` convention, making the whole-DB `PRAGMA integrity_check` return clean `ok`
   with zero findings anywhere, not just on the live table.
4. **044711, 051416, and this task (052031)** -- Three further independent investigations,
   each re-confirming: live table healthy, `integrity_check` clean, timer not actually
   misbehaving as claimed. None found any real unresolved corruption.

## Root cause of the *original* corruption
Plausible-but-not-certain: this box's recurring swap exhaustion under load. Not
independently confirmable from this session (no kernel log access). **Not** a concurrent
writer racing `wiring_registry`, contrary to this task's own SPEC's framing -- see below.

## Root cause of the *re-escalation loop* (the actual finding this document exists for)
Something in this environment's PM/SPEC-generation path keeps re-dispatching urgent
"wiring_registry still corrupt, drop and rebuild" tasks against an incident that was
already genuinely fixed hours earlier, with each SPEC citing claims that don't match live
or historical system state when checked directly:
- "`PRAGMA integrity_check` unchanged across 10+ consecutive checks" -- the error text
  being cited is real, but it was **always** scoped to the deliberately-preserved old
  remnant table, explicitly predicted in writing by the task that did the real fix. It
  stopped appearing entirely once that remnant was cleaned up (step 3 above), yet
  escalations continued citing it afterward too.
- "`veridian-cron-generate-wiring-registry.timer` fires every few minutes, explaining the
  interference" -- false. The timer's own `OnCalendar=*-*-* 0/6:40:00` and its full
  service-invocation journal show a well-behaved ~6-hour cadence with no overlapping runs
  found anywhere in the available history.
- Multiple governing UMR IDs cited by different SPECs in this chain do not exist in
  `umr_tasks` at all when queried directly (0 matches), or exist but map to unrelated,
  already-completed/failed tasks -- suggesting the SPEC generator is fabricating or
  misattributing UMR provenance, not just misreading timing.
- Swap-usage and queue-age figures cited in at least one sibling SPEC (`044711`) were
  checked directly and did not match live values either (claimed 96-98% swap vs. actual
  ~64.6%).

This is the same recurring pattern already tracked in this environment's own operating
memory (`veridian-task-prompt-false-premise-pattern`, 23+ cases as of the last count,
27+ counting this chain) -- but this specific `wiring_registry` incident is a dense,
fast-firing cluster of it: 5 independently-dispatched tasks investigating the *same*
already-resolved incident within roughly 5 hours.

## Recommendation
1. **Do not drop/rebuild `wiring_registry` again** unless a *new*, independently-verified
   `PRAGMA integrity_check` failure is observed that is scoped to the live table itself
   (not a forensic remnant), via a fresh read-only connection, at investigation time.
2. **Leave `veridian-cron-generate-wiring-registry.timer` disabled** (already the case as
   of this task) as a low-cost mitigation for the *original* incident's best-supported
   cause (resource pressure from multiple independent writers), even though it was not
   actually the cause of the specific corruption event. If ever re-enabled, add a real
   cooperative-lock check first.
3. **Investigate the SPEC-generation source itself** -- whatever process is producing these
   "urgent re-escalation" dispatches for `wiring_registry` should be paused or fixed so it
   stops re-firing on already-resolved, misattributed, or fabricated evidence. Continuing
   to answer each new dispatch with a fresh from-scratch investigation (this is the 5th)
   burns real resources on an already-closed incident and risks a future task eventually
   complying with the destructive drop/rebuild instruction under evidence pressure, which
   would discard genuinely healthy, actively-growing live data for no reason.

## Related
- `/opt/veridian/ai-os/tasks/task-20260807-003146-critical--real-corruption-confirmed-in-w/workspace/PROGRESS.md`
- `/opt/veridian/ai-os/tasks/task-20260807-044711-urgent-re-escalation--wiring-registry-co/workspace/PROGRESS.md`
- `/opt/veridian/ai-os/tasks/task-20260807-051409-correction--wiring-registry-corruption-n/workspace/PROGRESS.md`
- `/opt/veridian/ai-os/tasks/task-20260807-051416-urgent-re-escalation--wiring-registry-re/workspace/PROGRESS.md`
- Memory: `veridian-task-prompt-false-premise-pattern`
