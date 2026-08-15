# task-20260815-045844-phase-0--real-stale-backlog-reconciliati

## Completed

- [x] Verified live `/opt/veridian/ai-os/STUCK_TASKS_HEARTBEAT.json` before touching anything.
      Real state at start: `generated_at=2026-08-15T04:53:19.267338+00:00`,
      `blocked_task_count=1114`, `stuck_tasks` len=1108, of which only 522/1108
      `last_note`s mention "credit"/2026-08-05 — **not** all of them, and the
      file is nowhere near the SPEC's cited `generated_at=2026-08-06T17:32:57Z`/
      767 count. That snapshot is 9 days stale: `veridian-cron-dispatch-tick.timer`
      runs `dispatch-tick.py` every ~10 min and it rewrites this file every tick
      (confirmed via `systemctl --user list-timers`, last-fire 04:52:52 UTC,
      9 min before this task started). The backlog has grown since 8/6, not
      shrunk, and most current entries cite real, unrelated causes (PR
      rejections, quality-gate failures, failed merges — see sampled
      `last_note`s), not the resolved 2026-08-05 credit-exhaustion incident.
- [x] Checked the SPEC's "GOVERNING CHAIN" and prior-attempt UMR ids against
      the real DB (`/opt/veridian/ai-os/memory/superboss-register.sqlite` —
      the real `DB_PATH` per `superboss-register.py`'s
      `resolve_superboss_db_path()`; the `/opt/veridian/*.sqlite` files at
      the top level are the documented stale decoys, all 0 bytes):
      - `UMR-20260806-124055-bc80` (cited as "GOVERNING CHAIN"): real row,
        status=`completed`. Its real `inputs_json` is a broad, unrelated
        "Owner absolute stop work order" about finishing every deterministic
        script/wiring/metadata linkage platform-wide — **not** a directive
        about this heartbeat backlog specifically. Citing it as this task's
        governing chain is a mismatch, not a real authorization trail for
        this specific action.
      - `UMR-20260806-112310-7655`: real row, status=`completed`,
        `ts_completed` empty, `outputs_json={}` — SPEC's claim about this one
        checks out.
      - `UMR-20260802-084634-a89f` (`rejected_duplicate`) and
        `UMR-20260802-084556-8a54` (`failed`) — both confirmed accurate.
- [x] Ran the real dry run: `python3 resource_governor.py --reconcile-stale`
      → `{"actions": []}` (0 actions).
- [x] Ran the real execute: `python3 resource_governor.py --reconcile-stale --execute`
      → `{"actions": []}` (0 actions, same as dry run — no writes applied
      because none were eligible).
- [x] Root-caused the 0-actions result with live evidence, not assumption:
      `reconcile_stale_heartbeats()` (resource_governor.py:4677) only queries
      `umr_tasks WHERE status IN ('running','dispatched') AND last_heartbeat
      IS NOT NULL AND last_heartbeat < cutoff`. Live query against the real
      DB at the time of the dry run: only 4 rows are `status='running'` (0
      `'dispatched'`), and **all 4 have `last_heartbeat IS NULL`** — so the
      WHERE clause matches zero rows by construction (the function's own
      docstring flags this NULL-exclusion behavior explicitly).
      Separately, and this is the real reason the count never moves: the
      1108–1110 entries in `STUCK_TASKS_HEARTBEAT.json`'s `stuck_tasks` array
      are **not** umr_tasks rows at all. They come from `dispatch-tick.py`'s
      `find_stuck_tasks()` (line ~695), which scans `task.yaml` files for
      `status=='blocked'` with a stale `last_checkpoint_at` — a completely
      separate table/status vocabulary
      (`task.yaml` per-worker lifecycle vs. `umr_tasks` per-dispatch
      governance state, per that file's own Rule-4 comment at line ~740).
      `reconcile_stale_heartbeats()` never reads task.yaml and structurally
      cannot affect this count. `--reconcile-stale` is the wrong tool for
      this specific backlog, not a broken one.
- [x] Regenerated `STUCK_TASKS_HEARTBEAT.json` for real via its real,
      confirmed generator: `systemctl --user start
      veridian-cron-dispatch-tick.service` (the same unit
      `veridian-cron-dispatch-tick.timer` fires every ~10 min; forcing it
      early is a real, safe, in-band regeneration, not a workaround).
      Service exited 0 (`ExecMainStatus=0`).
      Real after state: `generated_at=2026-08-15T05:03:46.627354+00:00`,
      `blocked_task_count=1119`, `stuck_tasks` len=**1110**,
      `real_task_counts.running=4` (matches the live DB query above).

## Real evidence summary (required by SPEC)

| Metric | Value |
| --- | --- |
| SPEC-claimed baseline | 767 stuck tasks, generated_at 2026-08-06T17:32:57Z (9 days stale at task start — not the live count) |
| Real before count (live, at task start) | 1108 stuck_tasks / blocked_task_count 1114, generated_at 2026-08-15T04:53:19Z |
| Real dry-run actions count | 0 |
| Real execute actions count | 0 |
| Real after count | 1110 stuck_tasks / blocked_task_count 1119, generated_at 2026-08-15T05:03:46Z |
| Count dropped? | No — it grew by 2 (1108→1110) over the ~10 min window; backlog is actively accumulating, not shrinking |
| Real reason count didn't drop | `--reconcile-stale` targets `umr_tasks` rows with status `running`/`dispatched` + stale non-NULL `last_heartbeat` (0 such rows exist right now). The 1110 `STUCK_TASKS_HEARTBEAT.json` entries are `task.yaml` `status=='blocked'` rows from a disjoint code path (`dispatch-tick.py:find_stuck_tasks()`) this function never queries. No code defect — this is a scope mismatch between the SPEC's chosen tool and the actual backlog. |

## Remaining

- [ ] None for this investigation — the real, specific reason `--reconcile-stale`
      cannot clear this backlog is documented above with live evidence. If
      the 1110 blocked `task.yaml` rows genuinely need mechanical
      reconciliation, that requires a different, purpose-built sweep over
      `task.yaml`/`blocked` state (not `reconcile_stale_heartbeats()`), which
      is out of this task's real scope — flagging as a finding for PM
      review rather than building unrequested new code against a
      Owner-authorization chain (`UMR-20260806-124055-bc80`) that, per above,
      doesn't actually cover this specific action.
