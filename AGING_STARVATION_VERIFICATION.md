# Verification: UMR-20260807-061238-ae93 "aging starvation" claim (task-20260807-081909)

## SPEC claim
Governing chain UMR-20260806-124055-bc80 / UMR-20260807-061238-ae93. Claimed: real tier-0
row `UMR-20260807-061238-ae93` remained queued while 5 real dispatches went to real tier-1/
tier-2 rows sharing the same `source_trigger=owner_dispatch_gateway` /
`task_kind=veridian_task_create`, because aging-driven `effective_priority` decay pushed those
older lower-tier rows down to tier 0 and the `ts_submitted` tiebreak favored them for being
older, overriding the Owner's explicit tier-0 priority. Directed building a new
`owner_priority_override` table (`umr_id, reason, set_by, ts`) consulted before the normal sort
in `next_queued_task`/`run_tick`, seeded with `UMR-20260807-061238-ae93` and
`UMR-20260806-141055-1fec`, with real-tick evidence that `ae93` gets `ts_dispatched` set.

This is a **near-verbatim repeat of `task-20260806-201936-urgent-structural-fix--next-queued-task`**
(same mechanism, same `owner_priority_override` schema, and the *same second seed UMR*
`UMR-20260806-141055-1fec`), which was independently verified false and declined — see
`veridian-task-prompt-false-premise-pattern` case #23 in the durable false-premise record for
this dispatch pipeline.

## Live verification (2026-08-07 ~08:20-08:22 UTC), against the real DB
`/opt/veridian/ai-os/memory/superboss-register.sqlite` (confirmed live: 4.07 GB, mtime today;
the copies under `scripts/`, `repos/veridian-scripts/`, `ai-os/scripts-backup-*/` and
`ai-os/` root are all 0-byte stubs):

| Row | Claimed state | Real state |
|---|---|---|
| `UMR-20260807-061238-ae93` | queued, starved, tier 0 | **`status=running`, `ts_dispatched='2026-08-07T08:19:07'`** (non-NULL). Real systemd unit `veridian-worker@task-20260807-081903-mandatory-execute-the-rebuild--do-not-in.service` confirmed `active`/`running`. Dispatched ~2h07m after `ts_submitted` (06:12:38) — normal tick cadence, not indefinite starvation. |
| `UMR-20260806-141055-1fec` (2nd seed) | implied still relevant/blocked | **`status=completed`**, `ts_dispatched='2026-08-06T19:40:12'` — completed ~13 hours before this task was even dispatched. |
| Governing `UMR-20260806-124055-bc80` | live stop-work/priority order | **`status=completed`**, dispatched 2026-08-06T16:59:24 — terminal, not a currently-active order. |
| `owner_priority_override` table | implied not yet built (asked to seed it) | Confirmed **does not exist** (`sqlite_master` query, 0 rows) — consistent with case #23 never being implemented. |

Cross-check of the actual dispatch burst around `ae93` (`source_trigger='owner_dispatch_gateway'
AND task_kind='veridian_task_create'`, ordered by `ts_submitted`): the rows the SPEC says
"went ahead of ae93" (`UMR-20260807-074739-dde3`, `-070904-736a`, `-070110-5ea7`) are all
**submitted later** than `ae93` (06:12:38) and dispatched in the *same* ~08:19 burst as `ae93`
itself — `ae93` dispatched at 08:19:07, actually *first* among that cluster (dde3 @08:19:21,
736a @08:19:17, 5ea7 @08:19:12). No inversion is visible in the real data for this row.

Contrary evidence against the claimed aging-inversion direction: the currently-queued tier-1
rows (`UMR-20260806-223456-4d42` et al.) have been queued **9.8 hours** — far longer than any
tier-0 row's turnaround — and have *not* been dispatched ahead of anything. If aging were
actually winning ties in favor of older lower-tier rows the way the SPEC describes, these
9.8h-old tier-1 rows should have been promoted/dispatched long before now. There is a
genuinely-still-queued tier-0 row right now (`UMR-20260807-060727-c3ae`, queued 2.26h) — but
that is not the row the SPEC named, and 2.26h is in the same range as `ae93`'s own normal
2.1h turnaround, not evidence of starvation either.

## Conclusion
The concrete, falsifiable claim ("ae93 real tier 0, remained queued while ...") is **false as
of live verification**: `ae93` already has a non-NULL `ts_dispatched`, a real running systemd
unit, and was in fact one of the earliest-dispatched rows in its own burst. The requested
"real boolean evidence" (confirm `ae93` transitions to `ts_dispatched` not null within one
tick) is already true without any code change. The second seed row (`1fec`) is a stale
citation, `completed` since the prior evening.

The underlying `effective_priority`/tiebreak mechanism in `resource_governor.py` (`tier -
age//AGING_PROMOTION_INTERVAL_SECONDS`, ties broken by older `ts_submitted`) is real and
*could* in theory produce the described inversion for some future row — that structural
possibility was already correctly identified and explicitly declined once (case #23) as too
risky/hardcoded to build on a fabricated instance. Nothing in this cycle's live data shows it
actually happening. Per the established false-premise-verification protocol for this pipeline,
building the requested hardcoded `owner_priority_override` bypass — seeded with a row that's
already running and a row that's already completed — would be exactly the kind of unnecessary,
hard-to-reverse scheduler change the protocol warns against.

**No DB write, no scheduler code change, no `capability_registry` graduation performed.**

## Sibling dispatch burst noted
Four sibling tasks were dispatched within ~15 seconds of each other in this same batch:
`task-20260807-081903-mandatory-execute-the-rebuild--do-not-in`,
`task-20260807-081909-confirmed-with-fresh-evidence--aging-bas` (this task),
`task-20260807-081913-amendment-to-umr-20260807-070110-5ea7-...`,
`task-20260807-081918-resume-real-audit-for-umr-20260806-14105...`. Not investigated further
here (out of this task's scope) but flagged in case a reviewer wants to check for the same
cascade/self-amendment pattern documented in cases #21/#22.
