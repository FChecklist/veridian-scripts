# PROGRESS -- task-20260806-212450-stop-the-phase-3-and-phase-4-duplicate-s

Governing UMR: UMR-20260806-071025-1d28. This task's own UMR: UMR-20260806-092722-e526.

## Finding: SPEC premise is stale -- both loops it describes were already fixed
## ~11 hours before this task started. No zombie rows to close, no new code fix
## needed. Verified independently per Step 1 before touching anything.

## Completed

- [x] Step 1 -- independently re-verified the two "zombie" rows. **Both claims
      are false as of now.** Real DB query (`/opt/veridian/ai-os/memory/superboss-register.sqlite`,
      the canonical DB resolved by `superboss-register.py`'s `resolve_superboss_db_path()`
      -- not the 0-byte decoy `umr_tasks.db` files):
      - `UMR-20260730-041943-093a` (PHASE-3-BUILD-CALC): status = **killed** (not
        "running"). `ts_dispatched = 2026-08-06T10:42:22Z`. `reason`: "resubmitted
        (reused umr_id, prior status was 'killed')".
      - `UMR-20260729-112414-3269` (PHASE-4-BUILD-WORKFLOW): status = **completed**
        (not "queued"). `ts_dispatched = 2026-08-06T10:42:18Z`. `reason`: "reconciled
        by heartbeat sweep: unit veridian-worker@task-20260806-104213-...
        inactive, last_heartbeat stale (>900s), real exit status=completed".
      - `ps aux` scan for either identity: no live process (confirms SPEC's own claim).
      - `systemctl --user list-units --all` scan for either identity: no matching
        unit (confirms SPEC's own claim).
      - Both rows are already terminal -- **there is nothing to close.** They were
        genuinely dead at the moment the SPEC's evidence was gathered (~09:26 UTC
        today), exactly as claimed, but got dispatched and resolved by the
        governor at 10:42:18-22 UTC today (11+ hours before this task started at
        21:24:50 UTC), as a direct side effect of the root-cause fix below.

- [x] Step 2 -- N/A. Nothing genuinely dead in running/queued state remains; no
      `mark-umr-terminal` call was made (would be a false/duplicate write against
      an already-terminal row).

- [x] Step 3 -- root cause was **already found and fixed**, under this same
      governing UMR chain, before this task cycle:
      - `/opt/veridian/scripts/directive_engine.py` lines 274-297
        (`_already_flagged_for_review`/`note_needs_review`) and lines 319-375
        (`process_one`'s terminal-state branch) plus lines 47-48
        (`DIRECTIVE_RETRY_STATE_FILE`) -- fixed under UMR-20260806-090229-f2a7
        (child of the same UMR-20260806-071025-1d28 cited as this SPEC's
        governing UMR). Replaces the old "retry every tick forever" behavior
        with: retry exactly once, durably record that in
        `/opt/veridian/ai-os/tasks/DIRECTIVE_RETRY_STATE.json` (owned exclusively
        by this module, immune to other modules' row mutations), then surface a
        real blocker via `note_needs_review()` -> `/opt/veridian/ai-os/PENDING_OWNER_REVIEW.md`
        instead of resubmitting again. Verified live: the state file already
        contains `{"PHASE-3-BUILD-CALC": {"umr_id": "UMR-20260806-095410-713b",
        "ts": "2026-08-06T10:17:50Z"}, "PHASE-4-BUILD-WORKFLOW": {"umr_id":
        "UMR-20260806-095411-dab2", "ts": "2026-08-06T10:17:51Z"}}` -- the exact
        two identities named in this SPEC, marked exactly when the flood for
        them stopped.
      - `/opt/veridian/scripts/dispatch-tick.py` lines 196-296
        (`_existing_active_umr()` read-only pre-check added to
        `resume_interrupted_workers_tick()`) -- fixed under UMR-20260806-103711-bf00
        (same governing chain). This is a **second, separate** resubmission
        source (`source_trigger='dispatch-tick:resume_interrupted_workers'`)
        that was blind-retrying ~22 different `task-2026080[2-6]-*` identities
        since as far back as 2026-08-02, now gated the same way (read-only
        liveness check before ever calling `submit()`, so a still-legitimately-
        queued/capped task no longer writes a fresh `rejected_duplicate` row
        every tick).
      - I made **no code changes** -- both fixes are already deployed and live.
        Confirmed no new rejected_duplicate rows from either source since
        2026-08-06T10:42:33Z (11+ hours as of this writing).

- [x] Step 4 -- checked for other identities stuck in the same pattern: **yes,
      many, and they were already covered by the dispatch-tick.py fix above** (not
      by directive_engine.py, which only manages DIRECTIVE.yaml's own queue).
      Real counts, `source_trigger='dispatch-tick:resume_interrupted_workers'`,
      all-time as of this query:
      | task_identity | rejected_duplicate rows | last one |
      |---|---|---|
      | task-20260803-214944-pm-final-decision--ocid-020-independentl | 313 | 2026-08-06T09:02:40Z |
      | task-20260804-063409-pm-decision--get-a-genuinely-independent | 301 | 2026-08-06T10:42:28Z |
      | task-20260804-063253-pm-decision--authorize-the-small-ocid-06 | 301 | 2026-08-06T10:42:28Z |
      | task-20260804-063103-register-ocid-063--mechanical-handoff-pr | 301 | 2026-08-06T10:42:28Z |
      | task-20260804-063059-pm-decision--continue-monitoring--start | 301 | 2026-08-06T10:42:27Z |
      | task-20260804-062840-pm-decision--confirm-the-corrected-ocid | 301 | 2026-08-06T10:42:27Z |
      | ...16 more identities, 7-300 rows each | | |
      | **total: 30 distinct identities, 5,855 rows** | | all stopped by 2026-08-06T10:42:33Z |
      Query: `SELECT task_identity, count(*) FROM umr_tasks WHERE source_trigger='dispatch-tick:resume_interrupted_workers' AND status='rejected_duplicate' GROUP BY task_identity ORDER BY count(*) DESC;`
      Every one of these last-fired at 2026-08-06T10:42:2x-33Z -- the same moment
      as the directive_engine.py fix and the same moment the two SPEC-named rows
      got dispatched -- confirming a single real remediation event already closed
      all of these, not just the two named in this SPEC.

- [x] Step 5 -- proved the fix is holding. Before (peak, today, hour 09 UTC):
      195 rejected_duplicate rows/hour combined across both loops (matches
      SPEC's claimed ~126-195/hr order of magnitude). After 2026-08-06T10:42:33Z:
      **0** rejected_duplicate rows from either source, sustained for 11+ hours
      up to task start, **plus** a real live 10-minute window checked directly by
      this task: 0 rejected_duplicate rows in [2026-08-06T21:25:03Z,
      2026-08-06T21:35:03Z) (checked with correct ISO-8601 string comparison;
      note an initial query attempt using SQLite `datetime()` produced a false
      1196 due to a 'T' vs space separator lexical-compare bug in that throwaway
      query -- caught and discarded, not used as evidence).

- [x] Step 6 -- PR opened and merged:
      https://github.com/FChecklist/veridian-scripts/pull/227
      (merge commit `bf5f97309e80be094923424ad3471bd401952a6e`, docs-only:
      PROGRESS.md). Completion evidence recorded via the canonical
      `agent_work_briefing.py record-completion` (which itself writes through
      to `umr_tasks`/`ai_agent_registry` -- never raw SQL), citing:
      - Files independently re-verified as already carrying the real fix
        (this task made no code changes): `/opt/veridian/scripts/directive_engine.py`
        (lines 274-297, 319-375, 47-48) and `/opt/veridian/scripts/dispatch-tick.py`
        (lines 196-296).
      - Before: ~195 rejected_duplicate rows/hr peak (hour 09 UTC, 2026-08-06).
      - After: 0 rejected_duplicate rows sustained 2026-08-06T10:42:33Z ->
        2026-08-07T00:30:20Z (14h+), including the live 10-minute window
        (21:25:03-21:35:03Z) and a fresh point-in-time recheck at task-close.
      - `UMR-20260806-092722-e526` (this task's own UMR) marked
        `status=completed` at 2026-08-07T00:30:35Z.

## Remaining
(none -- all 6 steps complete)
