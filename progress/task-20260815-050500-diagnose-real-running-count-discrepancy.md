# PROGRESS -- task-20260815-050500-diagnose-real-running-count-discrepancy

UMR: UMR-20260806-180933-d3bb. Governing chain (per SPEC): UMR-20260806-124055-bc80,
UMR-20260806-162019-4b4f.

## Completed
- [x] Independently queried `umr_tasks` directly (raw sqlite3 against the real DB at
      `/opt/veridian/ai-os/memory/superboss-register.sqlite`, resolved via
      `resolve_superboss_db_path()`, NOT the 0-byte stub at
      `scripts/superboss-register.sqlite`) and via `resource_governor.py --query-umr
      --status running`. **Real result: 5 rows at status='running', not the 32 the SPEC
      claimed.** `count=5, matches=5` (below the default `--limit 20`, so nothing was
      truncated).
- [x] For each of the 5 `unit_name IS NOT NULL` rows, ran `systemctl --user show
      <unit> -p ActiveState,SubState,MainPID` (the `--user` scope matters -- a plain
      `systemctl show` against the *system* manager returns false
      `inactive`/`MainPID=0` for every one of these units, including this task's own
      currently-executing unit; that would have been a self-inflicted false-ghost
      misread). Real split:
      - `UMR-20260806-165509-4d7c` -- ActiveState=active, real PID 3472730 -- REAL.
      - `UMR-20260806-171945-5767` -- ActiveState=active, real PID 3482501 -- REAL.
      - `UMR-20260806-180933-d3bb` -- ActiveState=active, real PID 3519107 -- REAL
        (this task itself).
      - `UMR-20260806-173900-b504` -- ActiveState=inactive, MainPID=0 -- GHOST.
      - `UMR-20260806-175442-1fed` -- ActiveState=inactive, MainPID=0 -- GHOST.
      **Real split: 3 real / 2 ghost (of 5 total), matching
      `generate_pm_report_v3.py` Section 1's "3 parallel workers running" figure
      exactly.** The SPEC's framing of "32 vs 3" as a discrepancy needing
      investigation was itself the false premise -- there was never a 32.
- [x] Cross-checked the canonical `reconcile_stale_running_workers.py` (dry run first,
      no `--execute`) -- it independently reached the identical 3 real / 2 ghost split
      via its own `systemctl --user` liveness check, confirming the manual check above
      was not a fluke.
- [x] Fixed the 2 confirmed ghost rows via the **existing, canonical, reversible**
      mechanism only -- `reconcile_stale_running_workers.py --execute` (never a raw SQL
      UPDATE, never a delete):
      - `UMR-20260806-173900-b504` -> `completed_unmerged` (real branch/commit
        `8c215529ed4cc76d200994ccc3e54aad9b854f2e` still exists, not yet merged;
        `mark-umr-terminal`'s own structured-evidence gate made this call, not this
        script).
      - `UMR-20260806-175442-1fed` -> `completed` (real commit
        `a7f5be5b9b266ff20b346f40a7d4ab8984a9ebb8`, already merged as PR #409 --
        confirmed independently in `git log`).
      Both writes went through `superboss-register.py mark-umr-terminal`, the same
      canonical CLI path every other real terminal-status write in this codebase uses
      -- fully reversible/auditable via that same path, no data deleted.
- [x] Confirmed real freed capacity: `status='running'` count is now 4 (was 5), and all
      4 remaining rows are independently confirmed real/active (verified each via
      `systemctl --user show`, including a 5th row -- `UMR-20260806-182453-702a`,
      `task-20260815-050710-pm-correction-the-checklist-metric-oscil` -- that was
      dispatched by the live system mid-investigation; also genuinely active,
      PID 3532150). Zero ghost rows remain at `status='running'`.
- [x] Verified every other specific claim in the SPEC against live state before acting
      on any of them (per `veridian-task-prompt-false-premise-pattern` memory --
      verify independently before any write/restore/kill):
      - Swap: `free -m` shows **5053MB/12287MB used = ~41.1%**, not 97.5%.
      - "0 real task completions in the last 15 minutes system-wide": **false** -- 61
        rows have a real `ts_completed` timestamp in the last 15 minutes (mix of
        `completed`/`failed`/`completed_unmerged`), several within the last 5 minutes
        of this task starting.
      - "stuck-backlog count grew from 767 to 770": no `stuck` status exists in this
        schema at all; the immediately-prior task's own finding doc
        (`FINDING_scheduler_starvation_reescalation_loop_2026-08-15.md`, 9 minutes
        before this task) already confirmed no count anywhere near 767 exists.
      - "UMR-20260806-162019-4b4f queued 107+ minutes with zero dispatch": **false**.
        Live row: `status='failed'`, `ts_submitted=2026-08-06T16:20:19Z`,
        `ts_dispatched=2026-08-15T04:56:26Z`, `ts_completed=2026-08-15T04:58:35Z`
        (this task itself dispatched at 05:05:04Z, ~6.5 minutes later). `reason`
        field: `worker-exit-status-bridge` bridged it to `failed`
        because its own worker's `task.yaml` last checkpoint self-reported
        `status='blocked'` -- i.e. it was dispatched, ran, and reached a genuine
        self-reported terminal state entirely on its own, minutes before this task
        began. It was never "queued 107+ minutes."
- [x] Per the above, did **not** blindly "directly execute UMR-20260806-162019-4b4f"
      as the SPEC instructed -- that row is not queued/starved, it is `failed` with a
      real, already-bridged, self-reported `blocked` outcome. Blindly re-dispatching a
      row that already reached a genuine terminal state, on the strength of a
      demonstrably false "still queued" claim, would violate this codebase's own
      standing rule (`reconcile_stale_running_workers.py`'s own docstring: "NEVER
      trusts a worker process's exit code as evidence of substantive completion") in
      the opposite direction -- assuming un-checked staleness instead of un-checked
      completion. If the underlying capacity-freeing work this UMR represents is still
      genuinely needed, it needs real triage through the existing queue/dedup path
      (a fresh, independently-justified dispatch), not a mechanical "run it again"
      off stale SPEC text.
- [x] Wrote `FINDING_running_count_discrepancy_2026-08-15.md` documenting the real
      3-real/2-ghost split and every other false claim, for the same
      re-escalation-loop reasons already flagged in
      `FINDING_scheduler_starvation_reescalation_loop_2026-08-15.md` (9 minutes prior,
      same UMR governing-chain family).

## Remaining
- [ ] None -- diagnosis complete, confirmed ghost rows reconciled via canonical path,
      freed capacity confirmed, false "execute UMR-20260806-162019-4b4f" instruction
      correctly not acted on (see reasoning above). Recommend investigating the
      SPEC-generation source itself (same recommendation as the prior finding doc),
      since this is now a 3rd/4th instance of the identical re-escalation pattern in
      this UMR family within roughly an hour.
