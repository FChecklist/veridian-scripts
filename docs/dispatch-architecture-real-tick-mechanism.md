# Real dispatch/tick mechanism for owner_dispatch_gateway-sourced queued rows

Investigated 2026-08-15, live on this box. Every claim below is either a
`file:line` citation from the real, currently-checked-out scripts, or a
real command output captured at investigation time (timestamps noted).
Anything not directly confirmed is explicitly marked **unconfirmed**.

## 1. Does `veridian-governor-tick.service` really exist, and does
   `veridian-cron-dispatch-tick.timer` (or a similarly-named unit) still
   exist / run it?

Two real, separate systemd user units are in play, and they are **not**
the same thing:

- **`veridian-governor-tick.service`** -- real, currently loaded, enabled,
  and active.
  - `~/.config/systemd/user/veridian-governor-tick.service`:
    `ExecStart=/opt/veridian/scripts/resource_governor_tick_loop.sh`,
    `Restart=always`, `[Install] WantedBy=default.target`.
  - Live `systemctl --user status veridian-governor-tick.service` (captured
    2026-08-15T22:08Z): `Loaded: loaded ... enabled`, `Active: active
    (running) since Thu 2026-08-13 22:40:31 UTC` (1d23h uptime at capture
    time), real PID running `bash /opt/veridian/scripts/resource_governor_tick_loop.sh`.
  - This is the real, live thing the stale code comments call "the live
    veridian-governor-tick.service" -- the name is correct and current, it
    is simply a plain long-running `Restart=always` service, not a
    `systemd` *timer*. There is no separate `veridian-governor-tick.timer`;
    the cadence comes from a `sleep 30` inside the script itself (see
    `resource_governor_tick_loop.sh:93`).

- **`veridian-cron-dispatch-tick.timer`** -- real unit, currently
  **disabled**, not consolidated into anything else, not renamed to
  `veridian-governor-tick.service` (those are two independent units that
  have coexisted since 2026-07-29/31).
  - `systemctl --user cat veridian-cron-dispatch-tick.timer` (2026-08-15,
    live): `No files found for veridian-cron-dispatch-tick.timer.`
  - `systemctl --user is-enabled veridian-cron-dispatch-tick.timer`:
    `not-found`. `is-active`: `inactive`.
  - Root cause: the real timer unit file has been renamed to
    `~/.config/systemd/user/veridian-cron-dispatch-tick.timer.disabled`
    (content confirmed: `OnCalendar=*-*-* *:2/10:00`, i.e. every 10 min,
    `RandomizedDelaySec=90`). systemd does not load `*.disabled` files, so
    this timer is not scheduled.
  - `~/.config/systemd/user/timers.target.wants/veridian-cron-dispatch-tick.timer`
    is a **dangling symlink** (`readlink -f` resolves to the now
    nonexistent `veridian-cron-dispatch-tick.timer`, without the
    `.disabled` suffix) -- this is why `systemctl --user list-timers --all`
    still lists it with a real historical `LAST` run
    (`Sat 2026-08-15 11:42:44 UTC`, from before it was disabled) but shows
    `NEXT: -`, and why `list-units --all` reports it
    `not-found inactive dead`. As of 2026-08-15T11:00Z (the SPEC's
    reference time) and as of this investigation, it is genuinely not a
    live, scheduled mechanism -- the SPEC's premise that it "does not
    appear in a real `systemctl --user list-timers --all` output" as a
    *scheduled* timer is correct; it does still leave a stale listing
    entry, which is a real but separate artifact of the dangling symlink,
    not evidence it is running.
  - The paired `veridian-cron-dispatch-tick.service` unit file still
    exists and is well-formed (`ExecStart=... run-logged.sh dispatch-tick
    /usr/bin/python3 /opt/veridian/scripts/dispatch-tick.py`), but with no
    live timer pointing at it, nothing currently triggers it periodically.
    Whether it is ever started by hand or by some other out-of-repo
    mechanism is **unconfirmed** from this investigation.
  - Its own unit-file comment (`veridian-cron-dispatch-tick.service`,
    `[Unit]` block) independently confirms the two units are meant to be
    distinct and cooperating, not aliases of each other: "resource_governor.py's
    own always-running tick loop (veridian-governor-tick.service) writes
    this sentinel file and load-sheds independently; this unit
    additionally refuses to even START while the sentinel is present."
  - Even when it was last live, `dispatch-tick.py`'s own dispatch
    functions (`resume_interrupted_workers_tick`, `module_queue_tick`,
    `gap_queue_tick`, `supervisor_sweep_tick` -- see `dispatch-tick.py:1680-1690`)
    do not touch `owner_dispatch_gateway`-sourced `umr_tasks` rows at all
    (see section 3) -- so its current disabled state does not change the
    answer to the real question this doc exists to answer.

Conclusion for (1): the comment's "live veridian-governor-tick.service" is
real and accurate as of investigation time. It is a separate, currently
*inactive* unit (`veridian-cron-dispatch-tick.timer`) that no longer
appears live in `list-timers` in any scheduled sense -- it was not
consolidated into `veridian-governor-tick.service`; the two have always
been independent, and the timer was simply disabled (real rename to
`.timer.disabled`, exact disable timestamp unconfirmed since `mv`
preserves file mtime -- the file's mtime, 2026-07-29T20:56:42Z, reflects
original content creation, not the disabling action).

## 2. Does `dispatch-owner-task.sh` itself call `resource_governor.py --tick`
   synchronously as part of submission?

**No.** Confirmed by reading the real script, not the stale-sounding
comment alone:

- The literal string `resource_governor.py --tick -> dispatch_one()`
  really does appear at `dispatch-owner-task.sh:23` -- but it is one line
  of a five-step *documentation* comment block (`dispatch-owner-task.sh:14-27`)
  describing the **whole pipeline across multiple processes**, not this
  script's own actions. Step 3 of that same comment block
  (`dispatch-owner-task.sh:19`) is explicit: `resource_governor.py --submit
  (called below) enqueues a 'queued' umr_tasks row and returns
  immediately; it does not spawn anything itself`. Step 4
  (`dispatch-owner-task.sh:22-23`, the line the SPEC quotes) is explicitly
  attributed to "the live veridian-governor-tick loop", a separate,
  already-running standing process -- not to this script.
- The only real `resource_governor.py` invocation in the script is at
  `dispatch-owner-task.sh:389`:
  `python3 resource_governor.py --submit --spec-file "$SPEC_FILE" --tier
  "$TIER" --source-trigger owner_dispatch_gateway`. `grep -n -- "--tick"
  dispatch-owner-task.sh` returns exactly one hit -- the comment at line
  23 -- and zero real invocations.
- The script's own later comments reconfirm this at the point where the
  row is left behind: `dispatch-owner-task.sh:459-482` states the row
  "remains status='queued', pollable by dispatch-tick.py's own real
  mechanical pickup" after both the tmux-relay-success and
  tmux-session-absent branches -- i.e. the script's authors' own mental
  model is that submission is fire-and-forget into the queue table, with
  pickup happening later, out of process. (This same comment's reference
  to "dispatch-tick.py's own real mechanical pickup" is itself imprecise
  per section 3 below -- the real mechanical pickup for these rows is
  `resource_governor.py`'s `next_queued_task()`/`dispatch_one()`, not
  anything inside `dispatch-tick.py`.)

Conclusion for (2): submission is asynchronous. `dispatch-owner-task.sh`
enqueues a `status='queued'` row via `--submit` and returns; it never
calls `--tick` itself.

## 3. What real, standing mechanism eventually dispatches a queued,
   owner_dispatch_gateway-sourced row that wasn't dispatched immediately?

**`veridian-governor-tick.service`**, on a real ~30-second cadence, via
`resource_governor.py`'s tick pipeline. Evidence, traced end to end:

1. `resource_governor_tick_loop.sh:28-94` -- a real `while true; do ...
   sleep 30; done` loop. Each iteration's first action
   (`resource_governor_tick_loop.sh:29`) is `run_governor --tick`, i.e.
   `python3 /opt/veridian/scripts/resource_governor.py --tick`
   (`resource_governor_tick_loop.sh:15-17`).
2. `resource_governor.py:6418-6419` -- the CLI's `--tick` flag calls
   `run_tick()`.
3. `resource_governor.py:4427-4460` (`run_tick`) -- after some
   maintenance steps, loops calling `dispatch_one()`
   (`resource_governor.py:4451` inside the `while` loop) until the queue
   is empty or a real slot/metric limit stops it.
4. `dispatch_one()` (`resource_governor.py:3793`) selects work via
   `next_queued_task()` (`resource_governor.py:2761-2767`):
   `SELECT * FROM umr_tasks WHERE status='queued'`, ranked by
   `effective_priority()` then `ts_submitted` -- **this query has no
   `source_trigger` filter at all**, so a `source_trigger='owner_dispatch_gateway'`
   row (exactly what `dispatch-owner-task.sh:389` writes) is just as
   eligible as any other queued row, ordered purely by tier/priority/age.
   `dispatch_one()` then calls `_perform_spawn()` (`resource_governor.py:2770`),
   which does the real `systemctl --user start veridian-worker@<id>.service`
   (or `veridian-supervisor@`) spawn for the row it picked.
5. This is the exact live path this investigation caught in the act:
   `systemctl --user status veridian-governor-tick.service`'s real journal
   output (captured 2026-08-15T22:04:09Z-22:08:56Z) shows a sequence of
   real `veridian-dispatch-decision` log lines for this task's own row
   (`umr_id=UMR-20260815-111843-28fc`) -- `blocking_category: cap_exhausted`
   (`running_worker_count: 5, cap: 5`) at 22:04:09-22:07:47, then
   `blocking_category: resource_headroom_veto` (`load1_backoff`,
   `load1: 20.21, threshold: 6.4`) at 22:08:18, then
   `action: dispatched` at 22:08:56 -- roughly 30-40s apart, matching the
   loop's `sleep 30` cadence plus the other tick steps' own runtime. These
   log lines are written by `dispatch_core.log_dispatch_decision(r)`
   (`resource_governor.py:4460`, called from inside `run_tick()`'s
   dispatch loop) -- i.e. this is `run_tick()`/`dispatch_one()` itself
   being observed live, not an inference.

By contrast, `dispatch-tick.py`'s tick functions -- `resume_interrupted_workers_tick`
(`dispatch-tick.py:426`), `module_queue_tick` (`dispatch-tick.py:1489`),
`gap_queue_tick` (`dispatch-tick.py:1365`), `supervisor_sweep_tick`
(`dispatch-tick.py:207`) -- are confirmed **not** the mechanism for this
class of row:
- `module_queue_tick` (`dispatch-tick.py:1489-1554`) operates entirely on
  `module_queues/*.yaml` files and dispatches items by directly starting
  `veridian-worker@{item['task_id']}.service` itself
  (`dispatch-tick.py:1543`) -- a wholly separate work queue from the
  `umr_tasks` SQL table `dispatch-owner-task.sh` writes into.
- `resume_interrupted_workers_tick` (`dispatch-tick.py:426-`, docstring)
  explicitly re-feeds work through `resource_governor.submit()` ("the
  queue, not a direct systemctl call -- so resource_governor's own
  dispatch_one() ... is what actually restarts it, at the existing cap,
  whenever it next runs a tick") -- i.e. even this function's own author
  documents that final dispatch still depends on the governor tick loop
  above, not on `dispatch-tick.py` itself.
- `gap_queue_tick` (`dispatch-tick.py:1365-`) operates on `gap_queue.yaml`,
  again a separate file-backed queue.
- The one `dispatch-tick.py` function that does touch
  `source_trigger='owner_dispatch_gateway'` rows,
  `owner_dispatch_reconciliation_tick()` (`dispatch-tick.py:1667-1673`,
  wrapping `status-remediation-tick.py`'s `run_owner_dispatch_reconciliation()`
  at `status-remediation-tick.py:164`), is a **status reconciler only** --
  its docstring (`status-remediation-tick.py:184-188`) confirms it only
  ever mechanically corrects `status='running'` rows whose real PR is
  MERGED/CLOSED/never-opened into `completed`/`failed`/`killed`; it never
  touches `status='queued'` rows and never dispatches anything.
- Separately, `dispatch-tick.py`'s own trigger (`veridian-cron-dispatch-tick.timer`)
  is itself currently disabled (section 1), so even the functions above
  are not running periodically at all right now -- moot for this class of
  row regardless, per the above, but worth noting as a second, independent
  reason `dispatch-tick.py` is not today's answer.

### Plain answer

For a queued, tier-0, `owner_dispatch_gateway`-sourced `umr_tasks` row
that isn't dispatched at submission time (e.g. capacity was full that
instant, as literally happened to this task's own UMR above): the real,
standing mechanism that will eventually pick it up is
**`veridian-governor-tick.service`**'s continuously-running
`resource_governor_tick_loop.sh`, which calls `resource_governor.py --tick`
(-> `run_tick()` -> `dispatch_one()` -> `next_queued_task()`) on a real,
live ~30-second cadence (`resource_governor_tick_loop.sh:93`,
`sleep 30`), every single loop iteration, unconditionally (subject only to
the real capacity/tier/priority/resource-headroom gates inside
`dispatch_one()` itself, exactly as observed live in section 3 above for
this task's own row). It requires no separate systemd timer of its own --
the cadence is the shell loop's `sleep 30`, and the service wrapping that
loop is confirmed enabled + active as of this writing.

## Sources (all real, live-read at investigation time, 2026-08-15)

- `~/.config/systemd/user/veridian-governor-tick.service` (+ `.d/override.conf`)
- `~/.config/systemd/user/veridian-cron-dispatch-tick.timer.disabled`,
  `veridian-cron-dispatch-tick.service` (+ `.d/override.conf`),
  `timers.target.wants/veridian-cron-dispatch-tick.timer` (dangling symlink)
- `systemctl --user status/cat/is-enabled/is-active` real command output
  (2026-08-15T22:04-22:08Z)
- `resource_governor_tick_loop.sh` (whole file)
- `dispatch-owner-task.sh` (header comment block, `--tick` grep, lines
  376-482)
- `resource_governor.py`: `next_queued_task()` (2761), `_perform_spawn()`
  (2770), `dispatch_one()` (3793), `run_tick()` (4427), CLI `--tick`
  wiring (6418-6419)
- `dispatch-tick.py`: `supervisor_sweep_tick()` (207), `resume_interrupted_workers_tick()`
  (426, docstring), `module_queue_tick()` (1489), `owner_dispatch_reconciliation_tick()`
  (1667), `main()` (1680)
- `status-remediation-tick.py`: `run_owner_dispatch_reconciliation()` (164,
  docstring)
