# PROGRESS -- task-20260815-220852-document-the-real-dispatch-tick-architec

## Completed
- [x] Confirmed real systemd state: `veridian-governor-tick.service` is loaded+enabled+active (running `resource_governor_tick_loop.sh`, PID live since 2026-08-13), `veridian-cron-dispatch-tick.timer` is currently disabled (real unit file renamed to `.timer.disabled`; `timers.target.wants/` symlink is dangling; `systemctl --user cat/is-enabled/is-active` all confirm not-found/inactive)
- [x] Read `dispatch-owner-task.sh` in full around the submission flow (line 23 comment vs. real line 389 `--submit` call) -- confirmed it does NOT call `resource_governor.py --tick` itself
- [x] Traced `resource_governor.py`: `--tick` -> `run_tick()` -> `dispatch_one()` -> `next_queued_task()` (source_trigger-agnostic `SELECT ... WHERE status='queued'`)
- [x] Traced `dispatch-tick.py`'s `resume_interrupted_workers_tick`/`module_queue_tick`/`gap_queue_tick`/`supervisor_sweep_tick` -- confirmed none of them operate on `owner_dispatch_gateway`-sourced `umr_tasks` queued rows; `resume_interrupted_workers_tick` only re-feeds `resource_governor.submit()`, which still requires the governor tick to dispatch
- [x] Live journal evidence (`systemctl --user status veridian-governor-tick.service`) showing real `veridian-dispatch-decision` log lines for this task's own UMR (UMR-20260815-111843-28fc) being deferred (cap_exhausted, load1_backoff) then dispatched, all on the 30s loop cadence
- [x] Wrote `docs/dispatch-architecture-real-tick-mechanism.md` with file:line citations for every claim
- [x] Committed and pushed

## Remaining
- [x] None -- doc delivered, no code changes required per SPEC
