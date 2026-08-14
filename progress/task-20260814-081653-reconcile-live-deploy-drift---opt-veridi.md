# task-20260814-081653-reconcile-live-deploy-drift---opt-veridi

UMR: UMR-20260814-081536-c8b1 (governing chain: UMR-20260813-195852-aa85 addendum)

## Completed

- [x] Independently re-verified the drift claim live (memory: prior SPECs on this
      chain have had false premises -- verified rather than trusted the summary).
      Confirmed via `git -C /opt/veridian/scripts`: branch=main, HEAD=293f97f
      (real ancestor of origin/main via `git merge-base --is-ancestor`), origin/main
      after fetch=363702c, 4 tracked files differ (resource_governor.py,
      tests/preflight_guard_hardstop_test.sh, tests/test_dupguard_overbroad_scope_fix.py,
      worker-entrypoint.sh) -- matches check_live_scripts_drift.py's own output.
- [x] Found the SPEC's own stated root-cause theory ("sync-repos.sh refuses a
      dirty/non-main checkout") did NOT hold at the moment I first checked: the
      checkout was on `main` and `git diff --quiet` / `git diff --cached --quiet`
      both reported clean (only 3 untracked stray files present, no modified
      tracked files). This is a second confirmed instance of the false-premise
      pattern this chain keeps producing (see my own recorded memory
      `veridian-task-prompt-false-premise-pattern`) -- the real root cause was
      elsewhere.
- [x] Root-caused via `systemctl --user`: `veridian-cron-sync-repos.timer` was
      **disabled** (absent from `timers.target.wants/`, `systemctl --user
      list-unit-files` showed `disabled` against a `preset: enabled`), so nothing
      had force-pulled `/opt/veridian/scripts` since the last real log at
      2026-08-13 10:47 UTC (`/opt/veridian/logs/sync-repos-20260813-104653.log`,
      which shows a real successful `OK: ff328e7` pull that landed fine that day).
      Cross-checked against `~/.config/systemd/user/README.md`'s own 2026-07-30
      re-audit, which explicitly confirmed this exact timer "present/enabled/active"
      as part of the intended closed set -- current `disabled` state is a real
      regression, not documented/intentional policy. No `resource-governor-
      EMERGENCY_STOP` sentinel was present, and no ATTENTION.md/journal entry
      recorded an intentional disable, so I re-enabled it (safe: re-enabling a
      unit whose own script is fetch+ff-only and already skips a dirty/non-main
      checkout cannot destroy anything by construction) -- `systemctl --user
      enable --now veridian-cron-sync-repos.timer`, confirmed `enabled`/`active`,
      next fire 2026-08-14 10:08:51 UTC.
- [x] Verified the fix live end-to-end: manually fired
      `systemctl --user start veridian-cron-sync-repos.service` once (real run,
      same ExecStart as the timer). Real log
      `/opt/veridian/logs/sync-repos-20260814-082635.log` shows it correctly
      pulled the other clean mirrored repos (claude-control, veda-advisors,
      veridian-brain, sumeet-spec all `OK: <sha>`) and correctly
      `SKIPPED: uncommitted local changes present` for `/opt/veridian/scripts` --
      see next item for why that skip is the right call right now, not a bug.
- [x] **Real, live, in-flight work found in the checkout** -- between my first
      check (clean) and my second check minutes later, `/opt/veridian/scripts`
      became genuinely dirty: `progress_completion_gate.py`,
      `tests/test_progress_completion_gate.py`,
      `tests/test_worker_exit_status_bridge.py` all show real unstaged diffs
      (262 lines) with mtimes 2026-08-14 08:20:44-08:22:24 UTC -- 4 minutes old
      at last check (current time 08:26 UTC). This is unmistakably a concurrent
      agent actively editing directly in this live checkout right now (matches
      the reflog pattern of many prior tasks checking out branches and
      committing directly here), not stale/abandoned work. Per the task's own
      explicit instruction, did **not** touch, stash, commit, checkout, or
      fast-forward this working tree -- forcing a pull/merge here right now
      would risk destroying that real in-flight work.
- [x] Did **not** unilaterally re-enable the ~14 other `veridian-cron-*` timers
      also found disabled during this investigation (only `dispatch-tick`,
      `zoekt-reindex`, `pm-report-tick`, `pm-sentinel-tick`, and the
      `prune-memory-backups.path` trigger are currently enabled, out of the
      documented ~20-unit closed set) -- flagging as a separate, real,
      out-of-scope finding below rather than expanding this task's blast
      radius without a dedicated investigation into each one.
- [x] Recorded a real, honest terminal outcome (see Remaining) rather than
      claiming full drift resolution: the live checkout is still 4 commits
      behind origin/main as of this writing, genuinely blocked by real
      in-flight work, not by anything this task could safely act on further.

## Remaining / real open findings for follow-up (not actioned by this task)

- [ ] `/opt/veridian/scripts` is still 4 commits behind `origin/main`
      (293f97f vs 363702c) as of 2026-08-14 08:26 UTC. This will now
      auto-resolve via the re-enabled timer's own fetch+ff-only+dirty-skip
      logic the next time the checkout is genuinely clean on `main` -- no
      further manual action needed unless the in-flight edits above are
      abandoned rather than committed.
- [ ] **Bigger, separate finding for Owner/PM attention**: fleet-wide,
      ~14 of the ~20-unit documented closed set of `veridian-cron-*` systemd
      --user timers are currently `disabled` (only dispatch-tick,
      zoekt-reindex, pm-report-tick, pm-sentinel-tick, and
      prune-memory-backups.path remain enabled), each against a `preset:
      enabled`, with no recorded rationale in README.md/ATTENTION.md for the
      disablement. This affects health-check-15min, security-check,
      cost-usage-60min, credit-ledger-prune, file-inventory,
      knowledge-registry-multisource, software-catalog-gen,
      audit-pipeline-security, phase-continuation-tick,
      generate-wiring-registry, status-remediation-tick,
      veridian-self-check, sync-vercel-env, sync-verdian-ai-data,
      sync-controller-back, system-sync, session-metadata-60min. Deliberately
      left un-touched by this task -- out of its own scope (this task's
      mandate was the `/opt/veridian/scripts` drift specifically) and each
      one deserves its own real evidence check before re-enabling, same as
      was done here for sync-repos.

## Real evidence trail

- `check_live_scripts_drift.py --live-dir /opt/veridian/scripts` output
  (re-run live, matches SPEC's citation).
- `systemctl --user list-unit-files 'veridian-cron-*'` (disabled/enabled
  state of every unit).
- `/opt/veridian/logs/sync-repos-20260813-104653.log` (last real pull before
  the gap) and `/opt/veridian/logs/sync-repos-20260814-082635.log` (first
  real pull after the fix, confirms fetch+skip-dirty behavior intact).
- `~/.config/systemd/user/README.md` (documents the intended closed set /
  2026-07-30 re-audit confirming this timer should be enabled).
- `git diff --stat` / `stat` on the 3 dirty files in `/opt/veridian/scripts`
  (real, fresh in-flight-work evidence).
