# PROGRESS -- task-20260807-052031-rca-confirmed---interference-removed--no

Governing chain (as claimed by SPEC): UMR-20260806-124055-bc80, UMR-20260807-010907-6984,
UMR-20260807-020846-772f.

## Completed
- [x] Independently verified every load-bearing claim in the SPEC before taking any
      write/DROP/rebuild action (per known recurring false-premise dispatch pattern in this
      repo -- memory `veridian-task-prompt-false-premise-pattern`, now 27+ prior cases).
      This is the **5th task in the exact same escalation chain** to independently
      investigate this: `task-20260807-003146` (did the real fix) -> `task-20260807-044711`
      -> `task-20260807-051409` -> `task-20260807-051416` -> this task, all within the same
      ~5-hour window, each re-confirming no live corruption.
- [x] SPEC step 1 (safety re-check): confirmed via `lsof`/`fuser` no write handle open on
      `superboss-register.sqlite` right now; confirmed
      `systemctl --user is-active veridian-cron-generate-wiring-registry.timer` returns
      `inactive` immediately before starting.
- [x] SPEC step 2 (permanently disable timer): **already done, before this task started.**
      `systemctl --user is-enabled` already returns `disabled` (`UnitFileState=disabled`),
      no symlink in `timers.target.wants/`, `ActiveState=inactive`/`SubState=dead`. Nothing
      further to do here.
- [x] **Central SPEC claim independently disproven:** SPEC's premise is that
      `veridian-cron-generate-wiring-registry.timer` was firing "every few minutes" and
      that this concurrent-writer interference explains 10+ unchanged corruption results.
      The unit's own `OnCalendar=*-*-* 0/6:40:00` (every 6 hours) and its full
      `journalctl --user -u veridian-cron-generate-wiring-registry.service` history confirm
      real runs only at ~00:4x/06:4x/12:4x/18:4x UTC -- never "every few minutes." This
      claim does not match live/historical evidence.
- [x] **Central task premise (that wiring_registry is still corrupt and needs a
      drop-and-rebuild) independently disproven, live, right now:**
  - `PRAGMA integrity_check` via a fresh read-only (`immutable=1`) connection on the live
    `/opt/veridian/ai-os/memory/superboss-register.sqlite`: returns exactly **`ok`**.
  - Live `wiring_registry` row count: **24,299**, newest row `ts` =
    `2026-08-07T05:23:07Z` (minutes before this check) -- i.e. actively, healthily growing
    via normal operation, not static/corrupt.
  - FTS5 shadow index present and consistent (`wiring_registry_fts` + `_data`/`_idx`/
    `_docsize`/`_config`), no vtable errors.
  - This deterministic-briefing's own cited entity
    (`dispatch_event-owner-task-20260807-024920-3015279`) resolves via `wiring_query.py`
    with `verification_status: VERIFIED_MATCH` -- it is simply this task's own auto-recorded
    dispatch event, not corruption evidence.
- [x] Traced the real provenance of the underlying incident: `task-20260807-003146`
      genuinely found real corruption (`database disk image is malformed`), did a real
      `.recover`-validated rebuild live (24,281 rows), and **deliberately renamed the old
      corrupt table aside** (`wiring_registry_corrupted_orig_20260807T004638Z`) rather than
      dropping it, for forensics -- explicitly predicting in writing that a whole-DB
      `PRAGMA integrity_check` would keep flagging that old remnant's pre-existing
      corruption. `task-20260807-051409` later dropped that now-redundant remnant table
      (already covered by an external file-level backup) once confirmed safe, making the
      whole-DB integrity check clean too. Every re-escalation since has been re-alarming on
      that same predicted, already-explained symptom, or on stale/misread UMR state.
  - `UMR-20260806-124055-bc80`: real, `status=completed`, tied to an unrelated already-
    merged task (`task-20260806-192052-...`, PR #212/#237) -- not evidence of ongoing
    corruption.
  - `UMR-20260807-010907-6984`: real, `status=running` -- this is the same UMR that
    `task-20260807-051409` and `task-20260807-051416` already worked under and closed out
    as false-premise/resolved.
  - `UMR-20260807-020846-772f`: real, `status=running` -- appears to be the dispatch that
    spawned this task.
- [x] Did **not** perform SPEC steps 3 (fresh forensic backup ahead of a rebuild), 4 (DROP
      `wiring_registry` + FTS5 shadow tables and rebuild from scratch), since the live table
      is healthy, current, and growing normally -- a destructive drop/rebuild of a healthy
      24k-row table would itself be the harmful action here, discarding real live data
      (including rows written since the 00:53Z rebuild) for no corrective benefit.
- [x] Recorded findings via `agent_work_briefing.py record-completion` (UMR-20260807-024922-f432).

## Remaining
- [ ] None on the database itself -- `wiring_registry` is healthy, `integrity_check` is
      `ok`, and the timer is already disabled/inactive as SPEC step 2 wanted.
- [ ] **Human follow-up strongly recommended, escalating what the two immediately prior
      sibling tasks already flagged and which this task now directly reconfirms:** whatever
      is generating these urgent re-escalation SPECs (5 dispatches in ~5 hours, all citing
      the same already-resolved incident, with claims -- "fires every few minutes,"
      "10+ unchanged checks" -- that don't match live system/journal state) is itself
      misbehaving and should be investigated/paused at its source, not answered by a 6th
      identical investigation.

## SPEC step 6 -- timer disable/re-enable recommendation
**Recommendation: leave `veridian-cron-generate-wiring-registry.timer` disabled
permanently**, with `wiring_registry` updates going through the deterministic dispatched
task path (as this SPEC's step 6 framed as the alternative) -- not because concurrent
writes were ever actually shown to cause the original corruption (the "fires every few
minutes" claim used to justify that theory is false; the timer's own history shows a well-
behaved 6-hour cadence with no overlapping runs), but because:
1. The original corruption's best-supported cause (from `task-20260807-003146`'s own
   root-cause note) is this box's recurring swap exhaustion under load, not a lock
   collision between two writers -- disabling one particular periodic writer doesn't fix
   that, but reducing the number of independent scheduled processes that can land a write
   during a resource-pressure spike is a reasonable, low-cost mitigation while swap
   pressure remains unaddressed.
2. `wiring_registry` is demonstrably still being kept fresh without the timer (newest row
   `ts` is minutes old, from dispatch-path writes), so there is no freshness cost to keeping
   it off.
3. If it is ever re-enabled, it should not be re-enabled as-is: it should first gain a real
   cooperative-lock check (e.g. respecting the same `superboss-register.sqlite.writelock`
   convention `task-20260807-051409` used) before writing, so it cannot silently interleave
   with a dispatched task's own write, even though that wasn't the actual cause of this
   specific incident.

## SPEC step 7 -- graduated finding
Written up at
`/opt/veridian/ai-os/tasks/task-20260807-052031-rca-confirmed---interference-removed--no/workspace/FINDING_wiring_registry_reescalation_loop_2026-08-07.md`
(this task) -- documents the real original corruption/repair (003146), the real
already-completed remnant cleanup (051409), and this recurring false-premise re-escalation
pattern specifically for `wiring_registry`, so it isn't re-investigated a 6th time.
