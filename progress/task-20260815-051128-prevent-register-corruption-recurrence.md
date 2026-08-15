# PROGRESS -- task-20260815-051128-prevent-register-corruption-recurrence

SPEC: prevent the 2026-08-15 register-corruption incident from recurring
(Part A atomic writes, Part B stuck-writelock detection, Part C fresher
backups, plus landing PR#349 if still open/clean).

## Completed

- [x] Investigated real incident evidence + verified PR#349's real live
      state (was OPEN/MERGEABLE/CLEAN, not yet merged, not duplicated by
      anything else) before touching anything.
- [x] **PR#349 landed for real**: merged 2026-08-15T05:29:35Z
      (https://github.com/FChecklist/veridian-scripts/pull/349,
      `gh pr merge 349 --merge`). Fixes the test-harness ~4GB register-copy
      leak (schema-only copies instead of full-file `.backup()`), adds
      `reap_stale_test_scratch.py` + its own systemd cron unit. Merged
      `origin/main` back into this branch afterward (fast-forward, no
      conflicts -- confirmed by re-running the affected test files).
- [x] **Part A -- atomic writes** (`superboss-register.py`):
      - Repo-wide grep for direct writes to `DB_PATH` outside a normal
        transactional sqlite3 connection found zero existing violators (the
        in-place `ALTER TABLE`/rebuild migrations already in this file go
        through the ordinary `_connect()`/`_write_lock()` path, out of
        scope by the SPEC's own carve-out).
      - Added `atomic_replace_live_db(build_temp_db, db_path=None)`: builds
        a new sqlite file at a temp path on the same filesystem, validates
        its real SQLite header + a real `PRAGMA integrity_check`, then
        does exactly one atomic `os.replace()` onto the live path. Any
        failure/exception/kill before that point leaves the live file
        completely untouched.
      - Added `vacuum_compact_db()` (first real use of the pattern --
        compaction/VACUUM was explicitly named in the incident and did not
        exist anywhere in this codebase before this fix) and a new
        `vacuum-compact` CLI subcommand.
      - Real test (`tests/test_atomic_db_rewrite.py`, 5 tests): includes
        the done-criteria test -- spawns a real subprocess mid-write,
        SIGKILLs it, and confirms the live file is byte-identical to
        before and still passes a real integrity_check. All 5 pass.
- [x] **Part B -- stuck-writelock detection** (`resource_governor.py` +
      `resource_governor_tick_loop.sh`):
      - Added `detect_stuck_writelock()`: flags `superboss-
        register.sqlite.writelock` as stuck iff it is older than
        `WRITELOCK_STALE_SECONDS` (default 300s = 5min) **and**
        `build_lock_liveness_guard.py`'s real `find_lock_holder_pid()`
        (reused, not reimplemented) confirms no live process holds it --
        matching the real incident's own signature (old + unheld).
      - New `--writelock-staleness-scan` CLI subcommand, logs a real
        ATTENTION.md alert when it fires.
      - Wired into the ALREADY-running 30s `resource_governor_tick_loop.sh`
        (no new standing daemon).
      - Real tests (`tests/test_resource_governor_writelock_staleness.py`,
        7 tests): function-level (no file / fresh / old+unheld /
        old+genuinely-held-by-a-real-flock-subprocess) plus a real CLI
        subprocess test firing on a synthetic old writelock and confirming
        the real ATTENTION.md write. All 7 pass.
- [x] **Part C -- fresher backups** (`resource_governor.py` +
      `resource_governor_tick_loop.sh`):
      - Added `run_daily_backup_check()`: self-throttled (cheap, stat-only
        `newest_backup_mtime()` check every 30s tick; only actually takes a
        backup if the newest existing one is missing or >24h old). Reuses
        `full_server_file_registration.py`'s own `take_backup()` verbatim
        (same real online `Connection.backup()` + integrity-verify-or-
        delete mechanism, same `superboss-register.sqlite.pre-fullfile-
        backup-<ts>` naming convention) -- no new backup mechanism or
        naming convention invented. Pruning needs zero new code:
        `prune_memory_backups.py`'s existing discovery already recognizes
        any `superboss-register.sqlite.*` file generically.
      - New `--daily-backup-check` CLI subcommand, wired into the same
        already-running 30s tick loop (not a new systemd unit -- avoids the
        `systemd/README.md` closed-set STANDING RULE on new periodic
        units).
      - Daily cadence justified: sub-daily would make this job's own
        backups evict each other under the existing shared keep-3-verified
        policy before a slow incident is ever caught by one.
      - Added `VERIDIAN_FFR_BACKUPS_DIR` / `VERIDIAN_PMB_MEMORY_DIR` /
        `VERIDIAN_PMB_BACKUPS_DIR` / `VERIDIAN_PMB_LIVE_DB` env-override
        seams to `full_server_file_registration.py` /
        `prune_memory_backups.py` (same convention as `SUPERBOSS_
        REGISTER_DB`) so this could be tested end-to-end without ever
        touching production paths.
      - Real tests (`tests/test_resource_governor_daily_backup.py`, 4
        tests): function-level (no backup -> takes one / fresh backup ->
        skips / stale backup -> takes a new one) plus a real CLI subprocess
        test confirming a real fresh, integrity-checked backup file is
        actually produced on disk. All 4 pass.
- [x] Full new-test sweep: 16/16 pass
      (`tests/test_atomic_db_rewrite.py`,
      `tests/test_resource_governor_writelock_staleness.py`,
      `tests/test_resource_governor_daily_backup.py`).
- [x] Regression check: `test_prune_memory_backups.py` (36),
      `test_full_server_file_registration.py`, and (post PR#349 merge)
      `test_resource_governor_queue_management.py` (13) all still pass.
      One pre-existing failure (`test_move_down_never_crosses_a_tier_
      boundary`, before PR#349's schema-only-copy fix landed) was confirmed
      to reproduce identically against unmodified HEAD via `git stash` --
      not caused by this work, and is now gone anyway after PR#349 merged
      (it was itself a real-production-data-dependent flake in the old
      full-copy test fixture).

## Remaining

- [ ] None -- all 3 parts + PR#349 landing are real, tested, and committed.
      Deploy note: `resource_governor_tick_loop.sh`'s live copy under
      `~/.config/...`/wherever it's actually invoked from must pick up
      this change on next deploy for Parts B/C to actually run in
      production (same "no automated deploy step, live checkout IS what
      runs" convention as every other `.py`/`.sh` change in this repo --
      no different handling needed here).
