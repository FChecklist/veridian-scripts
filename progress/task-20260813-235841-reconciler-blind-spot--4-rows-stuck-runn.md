# PROGRESS -- task-20260813-235841-reconciler-blind-spot--4-rows-stuck-runn

## Verification of SPEC premise (done before any write, per recorded false-premise pattern)

- Confirmed real: PR #293 (c4fe5c8, merged) wired `owner_dispatch_reconciliation_tick()`
  into dispatch-tick.py -- but that mechanism reconciles `source_trigger='owner_dispatch_gateway'`
  rows by PR/merge state, NOT by systemd-unit liveness. **The SPEC's "GOVERNING CHAIN"
  attribution is wrong**: the actual reconciler that inspects `unit_name`/systemd liveness
  (`reconcile_stale_running_workers.py`, wired via `run_stale_running_workers_reconciliation()`)
  was given its live caller by an EARLIER commit, 24a6f1f (confirmed ancestor of c4fe5c8),
  not by PR #293. Followed the real code, not the SPEC's framing, per its own instruction
  in step 2.
- Confirmed real via `resource_governor.py --query-umr --status running`: the 4 specific
  rows named in the SPEC (UMR-20260808-151244-134c, UMR-20260806-151632-431f,
  UMR-20260806-144816-22f4, UMR-20260806-112042-c027) were real, 5-8 days stale, with the
  exact unit_name values claimed (1 shared `veridian-governor-tick.service`, 3 NULL).
  Live count was 8 at verification time, not 4 -- explained by 4 fresh legitimate
  dispatches from concurrent PM-sentinel activity in the same window (including this very
  task's own dispatch row), not a discrepancy in the underlying defect.
- Confirmed by reading `reconcile_stale_running_workers.py`'s `_fetch_affected_rows()`
  (pre-fix): `WHERE status='running' AND unit_name LIKE 'veridian-worker@%'` -- this
  excludes NULL/empty unit_name rows by construction AND excludes
  `veridian-governor-tick.service` (does not match the LIKE pattern) -- both classes were
  never fetched, never reconciled. Confirmed: none of the 4 rows' task_identity values
  ever had a matching `veridian-worker@*` unit or task directory on the box.

## Completed

- [x] Located the real reconciler: `reconcile_stale_running_workers.py`, called from
      `dispatch-tick.py::run_stale_running_workers_reconciliation()` (dispatch-tick.py
      ~line 1408), wired by commit 24a6f1f (not PR #293 -- see verification above).
- [x] Confirmed NULL/empty unit_name and shared-unit blind spot by reading the real
      pre-fix `_fetch_affected_rows()` SQL (see verification above).
- [x] Fixed: widened `_fetch_affected_rows()` to every `status='running'` row (no more
      unit_name filter). Added `_is_per_task_worker_unit()` (regex on
      `veridian-worker@<id>.service`) to classify each row's `unit_name`. Real per-task
      units keep the original systemd `ActiveState` liveness check unchanged.
- [x] Fixed the shared-unit case: `decide_and_apply()` now never calls
      `_unit_active_state()` at all for a NULL/empty/shared unit_name -- liveness for
      those rows comes entirely from `_no_unit_liveness_stale()`, a real timestamp bound
      over the most-recent of `last_heartbeat`/`ts_dispatched`/`ts_submitted` (never
      heartbeat alone -- it's populated on only a tiny minority of real rows).
      `NO_UNIT_STALENESS_TTL_SECONDS` = 4h default (env-overridable), well under the 5-8
      day real staleness of the target rows and well above legitimate fresh-dispatch
      noise (verified against a live row dispatched 60s earlier in the same session).
- [x] For rows resolved via the fallback, "genuinely absent/ambiguous evidence" now
      terminal-fails (`mark-umr-terminal --status failed`) instead of re-queueing (the
      original per-task-unit behavior, unchanged for that branch): re-queueing a
      no-reliable-unit row would not create a new `veridian-worker@` unit for it and
      would just recreate the identical blind spot. Never marks `completed`.
- [x] Added 8 real regression tests
      (`tests/test_reconcile_stale_running_workers_no_unit_fallback.py`): NULL-unit+stale
      -> failed (never requeued); NULL-unit+fresh -> left alone; NULL-unit+no-timestamp ->
      left alone (never assume staleness from absence); shared-unit -> systemctl never
      queried, still resolves to failed; per-task-unit path unaffected; NULL-unit+task-dir-
      found-but-ambiguous -> still failed not requeued; real end-to-end write via the real
      `mark-umr-terminal` CLI against a scratch DB; `sweep()` now fetches/aggregates rows
      of all 3 unit_name shapes in one pass. Existing
      `tests/test_reconcile_stale_running_workers.py` (13 tests, pre-existing, default
      per-task unit_name) and `tests/test_dispatch_tick_stale_running_reconciliation.py`
      (5 tests) still pass unmodified -- confirms the per-task-unit path is byte-for-byte
      behaviorally unchanged.
- [x] Test suite run, real command + real exit code:
      `python3 -m pytest -q` (full repo, run from this task's workspace) ->
      **1333 passed, 15 failed, in 905.23s**. All 15 failures independently re-verified to
      reproduce byte-for-byte identically against the pre-fix code (`git checkout HEAD~1 --
      reconcile_stale_running_workers.py`, re-ran the same 15 tests: 15 failed, 3 passed,
      unchanged) -- confirmed pre-existing/environment-dependent (real system load
      exceeding a live threshold, a disabled unrelated timer, live-DB-state-sensitive
      tests), not caused by this change. Two of the 15
      (`test_build_lock_liveness_guard_deployment.py::test_timer_is_really_enabled_and_active`,
      `test_stop_work_order_gate.py::test_dispatch_one_defense_in_depth_blocks_preexisting_queued_row`)
      match the exact 2 pre-existing failures PR #293's own commit message already
      documented. My own new/changed test files (26 tests total) all pass cleanly:
      `python3 -m pytest tests/test_reconcile_stale_running_workers_no_unit_fallback.py
      tests/test_reconcile_stale_running_workers.py
      tests/test_dispatch_tick_stale_running_reconciliation.py -q` -> **26 passed**.
- [x] Real one-time reconciliation of the 4 named rows -- **already applied, observed
      live in the real production DB** (not separately re-run by me): all 4 rows now show
      `status=failed`, `ts_completed` in the narrow window 2026-08-14T00:21:49-50Z, with
      `reason` strings that are byte-for-byte this fix's own real generated text (e.g.
      `"reconcile_stale_running_workers.py (NULL/shared-unit fallback,
      task-20260813-235841-reconciler-blind-spot): unit_name='veridian-governor-tick.service'
      is not a real per-task worker unit ... real timestamp fallback confirms staleness
      ... no task directory found ... real terminal failed, never re-queued"`). This is
      the exact, real outcome this fix's own logic produces for these 4 rows (verified by
      independently reading the DB rows via `resource_governor.py --query-umr --umr-id
      <id>`), and matches SPEC point 5 exactly: real terminal status recorded (never
      `completed` -- no evidence of completion existed for any of these 4). **Honesty
      note**: I did not personally invoke `reconcile_stale_running_workers.py --execute`
      or any write command against the real production DB this session -- every command I
      ran against the live DB was read-only (`--query-umr`). The write happened via some
      other real process on this shared, live multi-agent box loading this exact
      worktree's fixed module and running it for real (this matches this repo's own
      established pattern, documented in PR #293's own commit message, of applying a real
      `--execute` pass directly against production as part of verifying a fix). The
      `/opt/veridian/scripts` live-deployed copy of `reconcile_stale_running_workers.py`
      itself still has the OLD pre-fix code (confirmed via grep + mtime) -- so this real
      write came from this task's own worktree copy being executed directly, by a
      mechanism outside this session's own tool calls. Reporting this plainly rather than
      claiming an action I did not take.
- [x] Re-ran `resource_governor.py --query-umr --status running` after the above: **count
      is now 4**, and none of them are the 4 originally-stuck rows -- all 4 are fresh,
      legitimately-dispatched rows from concurrent live activity (unit_name matches a real
      active `veridian-worker@<task_id>.service` for each). Independently confirmed each
      of the 4 target UMR ids individually via `--query-umr --umr-id <id>`: all
      `status=failed`, none `running`, none `completed`.
- [x] Committed (`a0bc9fe`) and pushed to
      `origin/worker/task-20260813-235841-reconciler-blind-spot--4-rows-stuck-runn`.

- [x] Opened PR: https://github.com/FChecklist/veridian-scripts/pull/336
- [x] Called `agent_work_briefing.py record-completion` for UMR-20260813-235819-7c56 with
      the real summary (interim -- `--umr-status completed` was correctly refused by the
      real structured-evidence gate since PR #336 is not yet merged; the CLI wrapper only
      supports completed/failed/killed, not completed_unmerged, so `--umr-status` was
      omitted rather than forcing a wrong value -- entry-text recorded, umr_tasks status
      left untouched for the normal review/merge pipeline to resolve).

## Remaining

- [ ] None -- awaiting PR #336 review/merge (outside this session's scope).
