# Dispatch/status script consolidation (2026-07-27)

task-20260726-210339-consolidate-6-dispatch-status-scripts-in. Closes the real
root cause of the 2026-07-26 OOM-kill incident: 3 independent worker-spawn code
paths (`systemctl start veridian-supervisor@`, `veridian-task.py create`,
`task-gateway.py submit/start`) each ran with no shared concurrency cap between
them. See `dispatch_core.py`'s own module docstring for the mechanism.

## Old -> new mapping

| Retired (renamed `.superseded-by-consolidation-2026-07-27`) | Replaced by |
| --- | --- |
| `supervisor-sweep.sh` | `dispatch-tick.py` |
| `queue-dispatcher.py` | `dispatch-tick.py` |
| `module-queue-dispatcher.py` | `dispatch-tick.py` |
| `auto_phase_continuation.py` | `phase-continuation-tick.py` |
| `veridian_status_monitor.py` | `status-remediation-tick.py` |
| `veridian_remediation_dispatcher.py` | `status-remediation-tick.py` |

New shared library: `dispatch_core.py` (one flock-backed concurrency lock, one
shared cap, one `ai-os/tasks/*/task.yaml` walk, wiring_registry helpers) --
imported by all 3 new scripts.

The 6 originals are renamed, not deleted, so their real history (and the
option to diff against them) stays available. They are not wired into
anything -- nothing in this repo imports or subprocess-calls them by their old
names after this change.

## Why renamed instead of deleted

Per this task's own SCOPE: "rename them with a
`.superseded-by-consolidation-2026-07-27` suffix so they're clearly retired
but recoverable."

## Deployment status

Not deployed, not added to the live crontab. The crontab has had every
dispatch-heavy job paused (`#PAUSED-OOM-INCIDENT-2026-07-26`) since the
incident this consolidation fixes; re-enabling scheduled dispatch (for the new
scripts, or anything else) is a separate, explicit Owner decision after this
PR is reviewed and merged. See the PR body for the proposed cron schedule.

## Config/state files these scripts read (production-only, not tracked in
this repo -- same as `ai-os/tasks/*`)

- `ai-os/gap_queue.yaml` (`dispatch_paused`/`held_task_ids` -- Owner-set,
  unchanged by this consolidation)
- `ai-os/queues/*.yaml`
- `ai-os/locks/worker-spawn.lock` (new, created on first use by
  `dispatch_core.acquire_dispatch_lock()`)
- `ai-os/PHASE_READY_CACHE.json` (new -- written every tick by
  `phase-continuation-tick.py`, read by `status-remediation-tick.py`)
- `ai-os/LIVE_STATUS_2026-07-26.yaml` (unchanged filename/shape)
