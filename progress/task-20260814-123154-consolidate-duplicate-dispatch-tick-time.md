# PROGRESS -- task-20260814-123154-consolidate-duplicate-dispatch-tick-time

## Completed

- [x] Compared both unit pairs via `systemctl --user cat`:
  - `veridian-cron-dispatch-tick.timer`/`.service` (real, active): fires
    `*-*-* *:2/10:00` with 90s jitter, `ConditionPathExists=!EMERGENCY_STOP`
    safety gate, `MemoryHigh=256M`/`MemoryMax=384M` override, one of the
    documented "closed set of 18" periodic jobs
    (task-20260729-cron-consolidation-phase6). ExecStart:
    `run-logged.sh "dispatch-tick" /usr/bin/python3
    /opt/veridian/scripts/dispatch-tick.py`.
  - `veridian-dispatch-tick.timer`/`.service` (old, dormant): fires every
    10min via `OnUnitActiveSec`, no jitter, no EMERGENCY_STOP gate, no
    memory cap. ExecStart: `/usr/bin/python3
    /opt/veridian/scripts/dispatch-tick.py` (same script, `WorkingDirectory=
    /opt/veridian/repos/claude-control`, different log path).
- [x] Confirmed same underlying script by md5sum
  (`/opt/veridian/scripts/dispatch-tick.py` == same file both units invoke,
  no distinct args/flags between the two ExecStart lines).
- [x] Confirmed `dispatch-tick.py` has no `os.getcwd()`/relative-path/`cwd=`
  dependency (grep, no hits) -- the old unit's `WorkingDirectory=` setting
  makes no behavioral difference, so it is not "unique logic" worth
  preserving.
- [x] Confirmed via `systemctl --user status`/`list-timers --all` that the
  old timer's last real log write is 2026-07-31 18:45 UTC (matches SPEC),
  and it is otherwise strictly a subset of the new unit's behavior (no
  EMERGENCY_STOP gate, no jitter, no memory cap) -- **verdict: fully
  redundant, nothing to merge forward into
  `veridian-cron-dispatch-tick.service`.**
- [x] Retired the old pair, did not touch the cron pair:
  - `systemctl --user disable --now veridian-dispatch-tick.timer` (removed
    from `timers.target.wants`, stopped).
  - `systemctl --user stop veridian-dispatch-tick.service` (was already
    inactive/dead; stopped for certainty).
  - `systemctl --user disable veridian-dispatch-tick.service` -- no-op per
    systemd (unit has no `[Install]` section, only ever timer-triggered);
    expected, not an error.
  - `systemctl --user daemon-reload`.
  - Renamed the unit files (not deleted, so history/recovery stays
    possible, matching this repo's own precedent in
    `README-dispatch-consolidation.md`) off the `.timer`/`.service`
    extension so systemd stops indexing them and they no longer appear in
    `systemctl --user list-unit-files`:
    - `~/.config/systemd/user/veridian-dispatch-tick.timer` ->
      `veridian-dispatch-tick.timer.superseded-2026-08-14`
    - `~/.config/systemd/user/veridian-dispatch-tick.service` ->
      `veridian-dispatch-tick.service.superseded-2026-08-14`
- [x] Verified post-change: `systemctl --user list-unit-files | grep
  dispatch-tick` now shows exactly one pair
  (`veridian-cron-dispatch-tick.timer`/`.service`, both still
  enabled/static as before -- untouched). `list-timers --all` likewise
  shows only the cron timer now. `veridian-cron-dispatch-tick.timer` still
  fired successfully during this session (last run 12:22 UTC, next run
  ~12:33 UTC) -- confirmed unaffected by this change.

## Remaining

- [ ] None. Task complete: ambiguity eliminated, no code merge was needed
  (old unit was fully redundant), real unit was not modified.

## Note on scope vs. this git repo

This task's real change is entirely in live systemd --user config
(`~/.config/systemd/user/`), not in this repository. Neither
`veridian-dispatch-tick.*` nor `veridian-cron-dispatch-tick.*` unit files
are tracked in this repo's `systemd/` directory (checked -- only 9 `.service`
+ 5 `.timer` files are tracked there, and dispatch-tick is not among them),
so there is no in-repo code diff for this objective; the task's own
`prompt.txt` does not name a `.py`/`.sh`/etc. file as its objective either,
so `progress_completion_gate.py check-completion`'s code-diff requirement
does not apply here.
