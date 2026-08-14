# Tracked systemd --user unit templates

These are version-controlled reference copies of live unit files that
otherwise only exist, un-versioned, under
`~/.config/systemd/user/` on VERIDIAN-DEV. There is no automated deploy
step for this directory (unlike the .py scripts in the parent dir, which
ARE the live files -- this repo's working tree at /opt/veridian/scripts IS
what runs) -- a change here must still be copied to
`~/.config/systemd/user/<name>` by hand, followed by
`systemctl --user daemon-reload`, same as before this directory existed.
Added 2026-08-01 alongside the 24-unit OOM-kill RCA fix (see veridian-task.py
cmd_create and dispatch-tick.py's resume_interrupted_workers_tick) so the
worker unit template's own root-cause fix (removing [Install]/WantedBy=
default.target) is reviewable in the same PR as the code that depends on it,
instead of being an invisible, SSH-only edit no PR ever showed.

## veridian-supervisor@.service

Tracked here for the first time 2026-08-13 (UMR-20260813-090037-9a34, addendum to
UMR-20260806-171945-5767, supervisor-side half of PR #249's own AUDIT:FAIL finding "(b)
NO SUPERVISOR-SIDE BRIDGE"). The live file has been running un-versioned under
`~/.config/systemd/user/veridian-supervisor@.service` (plus a separately-managed
`~/.config/systemd/user/veridian-supervisor@.service.d/override.conf` MemoryHigh/MemoryMax
drop-in, intentionally NOT tracked here, matching every other unit in this directory --
none of them track their own drop-in overrides either) since before this directory
existed. This PR adds the missing `ExecStopPost=` line (see
worker-exit-status-bridge.py's own "Supervisor-side reuse" docstring section for the
real root-cause writeup) -- everything else in this tracked copy matches the live file's
real, pre-existing content, confirmed via `systemctl --user cat`. Same manual-copy +
`systemctl --user daemon-reload` deploy step as every other file in this directory (see
STANDING RULE above) -- not yet deployed live as of this PR (deploy happens after merge,
same convention `test_build_lock_liveness_guard_deployment.py` documents for unit #20).

## veridian-cron-prune-memory-backups.{service,timer,path}

Tracked here for the first time 2026-08-06 (UMR-20260806-134738-eec3,
governing UMR-20260806-071025-1d28). The .service and (pre-fix) .timer were
already live/installed since UMR-20260806-084306-f599 Step 6 (PR #151,
same day) but had never been copied into this version-controlled directory
until now. This change also fixes the .timer's cadence (once/day was being
outrun by the real observed same-day backup creation rate) and adds a new
.path unit as the primary, event-based trigger -- see the comment header in
each of those two files for the full real-evidence reasoning. This is a
trigger-mechanism change to the already-authorized unit #19, not a new
unit-#20 under the ~/.config/systemd/user/README.md closed-set STANDING
RULE.
