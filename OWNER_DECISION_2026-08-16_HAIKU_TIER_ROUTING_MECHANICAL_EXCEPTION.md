# Owner decision: veridian-scripts-scoped exception permitting Haiku 4.5 for genuinely mechanical worker dispatches

**Date:** 2026-08-16
**Scope:** `veridian-scripts` only. Does NOT touch `compliance-tracker`'s Rule 8
(90-day quality mandate, active through ~2026-10-08), which correctly does
not govern `veridian-scripts` and is intentionally left unedited by this
task.
**Governing UMR (this task):** UMR-20260816-041030-cdc4 (task_identity
`owner-task-20260816-041025-1567455`, unit
`veridian-worker@task-20260816-041054-real--corrected-tier-aware-haiku-4-5-rou.service`)
**Supersedes the false-premise finding in:** UMR-20260815-135358-cbb7 (see
`FINDING_haiku_tier_routing_premise_false_2026-08-15.md` in this repo's
history / PR #426) -- that dispatch correctly declined to implement a
`--model haiku` branch keyed on `umr_tasks.tier` (a dispatch-PRIORITY field,
0=highest..4=lowest, not a complexity signal -- confirmed there via
`resource_governor.py`'s own `DEFAULT_TIER`/`CHECK` constraint/
`next_queued_task()` sort usage, and via direct counter-evidence that this
same objective's own governing dispatches were themselves tier 0, i.e.
judgment-heavy work). That finding also named the two real prerequisites
that would need to be true before this could be safely implemented:

1. a real, low-latency, pre-invocation `complexity_tier` signal threaded
   from `plan_generator.py`/`pm_lifecycle.py`'s existing 3-value
   (`mechanical` / `integrative` / `judgment`) enum into `task.yaml` at
   task-creation time, which did not exist before this task, and
2. a verifiable Owner decision scoped to `veridian-scripts` (not just
   compliance-tracker's roster) authorizing an exception to Rule 8.

This document records prerequisite 2. Prerequisite 1 is implemented in the
same commit as this file -- see `pm_lifecycle.py` (`dispatch_task()`),
`dispatch-owner-task.sh` (`--complexity-tier` passthrough),
`resource_governor.py` (`_perform_spawn()`'s `veridian_task_create` branch),
`veridian-task.py` (`cmd_create`'s `--complexity-tier`), and
`worker-entrypoint.sh` (both `claude -p` call sites).

## The real, verified Owner authorization

The Owner was asked, in the interactive PM session, two explicit questions
on 2026-08-16 and answered both "Yes":

1. authorized dispatching the real, correctly-scoped fix described below
   (threading an actual complexity signal into `task.yaml` rather than
   misusing dispatch-priority tier), and
2. explicitly authorized, in these exact terms presented to them and
   confirmed:

> veridian-scripts worker dispatches may use Haiku 4.5 for genuinely
> mechanical (not judgment-tier) work, once a real complexity signal exists
> to gate it.

This is a real, verifiable Owner decision scoped ONLY to `veridian-scripts`
and ONLY to genuinely-mechanical-classified work -- it is not a blanket
exception and does not touch compliance-tracker's Rule 8 quality mandate.

## What this decision does NOT authorize

- No change to `compliance-tracker/AGENTS.md` Rule 8 itself (that file
  belongs to a different repo's governance and was not edited by this
  task).
- No inference of complexity from `umr_tasks.tier` (dispatch priority),
  task title text, or any other proxy -- only the real, explicitly-set
  `task.yaml` `complexity_tier` field counts.
- No unsafe default: `complexity_tier` being absent or unset (the case for
  every dispatch path that doesn't go through `pm_lifecycle.py`'s `run`
  command with a real `--complexity-tier` value, e.g. raw
  `owner_dispatch_gateway` submissions relayed straight into the
  interactive session) MUST route to `--model sonnet`, unchanged. Only
  `complexity_tier == 'mechanical'` routes to `--model haiku`.

## Real implementation this decision gates

See `worker-entrypoint.sh`'s "Tier-aware Haiku routing" block (reads
`task.yaml`'s `complexity_tier` via the file's own established
`yaml.safe_load` pattern, immediately after `WORKSPACE`/`BRANCH`/
`CHECKPOINT_COUNT`) and both `claude -p` call sites (main invocation and the
`--continue` auto-fix retry), which now pass `--model "$CLAUDE_MODEL"`
instead of a hardcoded `--model sonnet`.
