# PROGRESS -- task-20260813-145820-guard-register-cli-invocations--one-quer

## SPEC
Addendum to Priority-1 UMR-20260806-171945-5767 (UMR-20260813-125756-9221).
Real, measured incident: a single `resource_governor.py --query-umr --status
killed --limit 200` invocation (PID 1685324) sat in state D
(wchan=mem_cgroup_handle_over_high) for 51-55+ minutes at ~2.04-2.09GB RSS
while the box's swap was fully exhausted and /proc/pressure/memory sat at a
steady ~30-39% full-stall. Scope: the register CLI invocation layer only
(resource_governor.py's --query-umr and superboss-register.py's
query_umr_tasks()) -- distinct from queued UMR-20260813-120054-4e66
(phantom-row reconciliation) and UMR-20260813-115911-df5c (RCA routing).

## Completed
- [x] **A. Real root cause, measured (not guessed).** Cloned
      `FChecklist/veridian-scripts` fresh to `/tmp/veridian-scripts-work/repo`
      (the live `/opt/veridian/scripts` checkout had unrelated uncommitted
      work from a different in-progress task on a different branch -- never
      touched it). Took a safe `sqlite3 .backup` copy of the real, live
      4GB+ register (`/opt/veridian/ai-os/memory/superboss-register.sqlite`)
      to `/tmp/register_test_copy.sqlite` and ran real `EXPLAIN QUERY PLAN`:
      `SELECT * FROM umr_tasks WHERE status='killed' ORDER BY ts_submitted
      DESC LIMIT 200` plans as `SEARCH ... USING INDEX idx_umr_tasks_status
      (status=?)` + `USE TEMP B-TREE FOR ORDER BY` -- the single-column
      status index cannot satisfy the ORDER BY, so SQLite materializes
      EVERY matching row (status='killed': 826 real rows) with every
      column, including the large inputs_json/outputs_json/metadata_json/
      metric_snapshot_json blobs (measured: ~717MB combined across those
      826 rows, ~868KB/row average), into a temp b-tree BEFORE the LIMIT
      can apply. LIMIT bounded the *output*, never the real work/memory.
      Real, measured confirmation: `SELECT status, COUNT(*) ... GROUP BY
      status` and `SELECT SUM(LENGTH(...)) ... WHERE status='killed'`
      against the real register.
- [x] **B. Fixed: SQL-level LIMIT pushdown + no default blob columns +
      streaming.**
  - `superboss-register.py`: added composite index
      `idx_umr_tasks_status_ts ON umr_tasks(status, ts_submitted DESC)`,
      created for fresh DBs in `_ensure_umr_table()` and idempotently
      backfilled onto pre-existing DBs (incl. the real live one) via new
      `_migrate_umr_tasks_status_ts_index()`, wired into both the
      always-run migration chain AND the fast-path gate (so it isn't
      silently stranded on an already-migrated DB -- same class of bug a
      prior migration's own comment already flagged). Re-ran EXPLAIN QUERY
      PLAN against a copy with the index: plans as a single `SEARCH ...
      USING INDEX idx_umr_tasks_status_ts (status=?)`, no temp b-tree.
  - `query_umr_tasks()`: new `UMR_TASKS_LIGHT_COLUMNS` (every column except
      the 4 large JSON blobs) is the real default SELECT column list for
      every branch (umr_id/task_identity/search/plain-listing); new
      `full=False` kwarg (default) opt-in for full-blob rows, wired to a
      new `resource_governor.py --full` CLI flag. Hard `MAX_UMR_QUERY_LIMIT
      = 2000` clamp regardless of caller-supplied `--limit`. Cursor results
      are now streamed into the result list (`[r for r in cur]`) rather
      than `.fetchall()`, so a future edit that drops the SQL LIMIT
      degrades gracefully instead of silently regressing to
      materialize-then-slice.
- [x] **C. Real hard guard at the CLI entry point.** New
      `install_cli_resource_guard()` wraps every `resource_governor.py`
      invocation in `__main__` (not just --query-umr -- the incident class
      is generic to any CLI call): `signal.alarm()` wall-clock ceiling
      (`VERIDIAN_GOVERNOR_CLI_WALL_CLOCK_S`, default 180s) raising a real
      `CliGuardTimeout` caught at the top level (exit 124, matches
      `timeout(1)`'s own convention); a background daemon thread polling
      real `/proc/self/status` VmRSS (`VERIDIAN_GOVERNOR_CLI_RSS_CEILING_MB`,
      default 1024MB) that hard-`os._exit(137)`s on breach (a background
      thread cannot safely raise into a main thread stuck deep in a C
      call -- see the function's own docstring for why `sqlite3_step()`
      releases the GIL and lets this watchdog run at all). Found and fixed
      a real race while building this: `while not stop_event.wait(iv)`
      waits out the full interval before its first check, so a healthy,
      fast invocation could finish before ever being sampled -- fixed to
      check once immediately, then enter the wait loop. Verified all three
      real behaviors against the patched CLI: artificially low wall-clock
      -> exit 124 with a clear message; artificially low RSS ceiling ->
      exit 137 with `measured_rss_mb`/`ceiling_mb` in the message; normal
      thresholds -> exit 0, correct output, unaffected.
- [x] **D. earlyoom real config, verified against this failure mode.**
      `journalctl -u earlyoom.service` for the incident window: zero
      entries (not a permissions artifact -- the unit was genuinely
      silent). `/etc/default/earlyoom`: `EARLYOOM_ARGS="-r 3600"` only --
      real defaults apply: `-m 10 -s 10`, AND-gated ("both memory and swap
      must be below minimum"). Real evidence this AND-gate is the actual
      gap: the incident's swap was fully exhausted (well under any real
      threshold) but `buff/cache` was ~8GB of the box's 15GB, so
      MemAvailable (which counts reclaimable cache) almost certainly never
      dropped under earlyoom's 10% floor -- the swap-side condition was
      true, the memory-side condential never was, so the AND never fired.
      Separately, even a firing earlyoom's SIGKILL cannot preempt a process
      already in D-state/TASK_UNINTERRUPTIBLE (well-documented Linux
      kernel behavior) -- the real compensating control for THIS specific
      failure shape is not a different earlyoom threshold (no percentage
      tuning fixes the fundamental AND-gate-vs-cache-masks-exhaustion gap),
      it's preventing the ballooning process from ever reaching that state,
      which is exactly what B/C above do. Documented plainly rather than
      making a cosmetic earlyoom config change that would not have changed
      the real outcome.
- [x] **E. Real before/after measurement**, exact failing command
      (`--query-umr --status killed --limit 200`), run as a real subprocess
      against isolated `.backup`-safe copies of the live register (never
      the production file itself), peak RSS via `/proc/<pid>/status
      VmHWM` polling:
  - BEFORE (original, unpatched code): killed by the benchmark's own 25s
      safety window, still running, **peak RSS 1953.3MB and climbing**
      (real reproduction of the incident's ~2GB signature on a smaller,
      private copy -- did not need the full 51 minutes to prove the same
      pathology).
  - AFTER (patched code, same data, including the automatic
      `idx_umr_tasks_status_ts` migration running on first connect):
      **0.25s wall-clock, 28MB peak RSS**, correct 200-row result.
  - Real, measured >390x wall-clock and >69x memory improvement (lower
      bound on wall-clock since BEFORE never actually finished within the
      safety window).
- [x] **F. PID 1685324**: confirmed already gone (`ps -p 1685324` exit 1,
      no matching process) before this task started any remediation -- no
      kill action needed or taken.
- [x] Ran the full existing test suite (576 tests) against the patched
      code with `VERIDIAN_SCRIPTS_DIR` pointed at the patched clone (never
      the live `/opt/veridian/scripts`): 574 passed. The 2 failures are
      real, pre-existing, and independent of this change --
      `test_timer_is_really_enabled_and_active` (a systemd-user-timer
      environment check that fails identically outside a real systemd
      login session) and
      `test_dispatch_one_defense_in_depth_blocks_preexisting_queued_row`
      (reads REAL system `swap_used_pct`, currently ~95% -- itself
      corroborating evidence, see note below); reproduced the identical
      failure against the unpatched original code with the same real swap
      state, proving it is not caused by this change.
- [x] `tests/test_query_umr_by_id.py` (the one existing test that directly
      exercises `query_umr_tasks()`/the CLI's --query-umr path) still
      passes unchanged against the patched code.

## Notable real observation (not this task's scope to act on)
While benchmarking, a **live, contemporaneous, different recurrence** of
this same failure class was observed: PID 2407746 (owned by a different,
concurrently-running task, `task-20260813-150119-remove-0-byte-decoy-
register-files-that`), state D, wchan=mem_cgroup_handle_over_high, ~2.0GB
RSS, holding an open fd directly on the real live
`superboss-register.sqlite` -- a raw script bypassing both
resource_governor.py's CLI and the superboss_gateway.py single-gateway
mandate. Not touched (different task's live process, out of this task's
scope) but documented here and in the PR as further, real, un-fabricated
evidence of how live/systemic this class of bug is, independent of the
fix in this PR.

## Remaining
- [ ] None for this task's scope. Open items for OTHER, already-tracked
      UMRs (explicitly out of scope here, not duplicated): phantom-row
      reconciliation (UMR-20260813-120054-4e66), RCA routing
      (UMR-20260813-115911-df5c), and migrating the ~46 other scripts that
      still `sqlite3.connect()` the live register directly (tracked
      separately per superboss_gateway's own capability record scope_note)
      -- the live recurrence noted above is a real, current instance of
      that exact backlog item.
