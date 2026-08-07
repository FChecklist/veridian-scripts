# PROGRESS -- task-20260806-212444-stop-the-directive-resubmission-flood-po

UMR: UMR-20260806-092209-7a2e

## Verdict: SPEC premise stale/false -- the described defect was real but was
already found and fixed by prior work (UMR-20260806-090229-f2a7) hours before
this SPEC was issued. No new code change made. Documenting the independent
verification per the repo's standing false-premise protocol
([[veridian-task-prompt-false-premise-pattern]]).

## Completed

- [x] Step 1 -- found the real emitter, with log/code evidence, not inference.
  - `directive_engine.py`, run in a loop by `directive_engine.sh` under a
    `screen` session (`directive_execution`), **not** any systemd unit. The
    SPEC's suggested check target `veridian-directive-engine.service` does
    not exist -- confirmed via `systemctl list-unit-files 'veridian*directive*'`
    (0 results) and `find /opt/veridian/scripts/systemd` (only
    build-lock-liveness-guard, pm-report-tick, and the worker@ template are
    defined there). `veridian-governor-tick.service` is real and active but
    is the *dispatcher* (`resource_governor_tick_loop.sh`), not the emitter.
  - Direct evidence: `/opt/veridian/ai-os/tasks/directive_status.log` shows
    the exact resubmit-then-reject cycle for both task identities
    (`check-duplicate battery call failed, fail-open, proceeding` ->
    `submitted`/`queued for Owner review: submit rejected: duplicate
    submission rejected...`), and `umr_tasks` rows for both identities carry
    `source_trigger='DIRECTIVE'`.

- [x] Step 2 -- real root cause, cited not guessed.
  - This was a real blind retry loop, already root-caused and documented in
    `directive_engine.py` itself (see `git log -- directive_engine.py`,
    commit `b0a2516`): `process_one()`'s "retry exactly once, then hold for
    Owner review" policy was gated on an in-memory `entry.get("_retried")`
    flag on a dict that `main()` recreates fresh from `DIRECTIVE.yaml` every
    outer-loop tick (60s). The flag never survived to the next tick, so every
    tick resubmitted the same `task_identity` via `submit_task()`, forever,
    instead of the one retry the code intended.

- [x] Step 3 -- fix the real root cause (already done, verified in place, not
  re-implemented).
  - Two commits landed **the same day**, both before/around this SPEC's own
    stated discovery time of 09:20 UTC:
    - `b0a2516` (2026-08-06 09:24:54 UTC) -- round 1: durable retry-once
      state.
    - `68e0b94` (2026-08-06 09:41:11 UTC) -- round 2, after a Superboss
      review found round 1's signal (`umr_tasks.reason`) could be silently
      clobbered by `resource_governor.py`'s own legitimate `reason` rewrites
      on its `rejected_duplicate` paths. Round 2 moved the retry-once state
      to a small file exclusively owned by `directive_engine.py`:
      `DIRECTIVE_RETRY_STATE_FILE` = `/opt/veridian/ai-os/tasks/DIRECTIVE_RETRY_STATE.json`.
    - Current behavior (confirmed by reading the live code): on a terminal
      `failed`/`rejected_duplicate`/`killed` outcome, `process_one()` checks
      `_has_already_retried(task_identity)` against that file. First terminal
      outcome -> exactly one resubmission is allowed, marked durably via
      `_mark_retried()` *before* the resubmit is even attempted. Any further
      terminal outcome for the same identity -> `note_needs_review()` writes
      a one-line entry to a pending-review file and returns, with no further
      resubmission -- i.e. exactly the "wait or escalate exactly once"
      behavior this SPEC's step 3 asks for.
  - Confirmed the deployed file is not stale: `diff <(git show HEAD:directive_engine.py) directive_engine.py`
    -> empty, live file matches repo HEAD exactly (includes both fix commits).
  - Did not touch the duplicate gate (`find_in_flight_duplicate` /
    `resource_governor.py` dup check) -- confirmed correct and untouched, per
    SPEC instruction.

- [x] Step 4 -- verify observed row-creation rate, before and after.
  - **Before** (the real incident, both identities, `source_trigger=DIRECTIVE`,
    `status=rejected_duplicate`): 2026-08-06 09:11:51Z -> 09:54:11Z, one row
    roughly every 61s for PHASE-3-BUILD-CALC (43 rows in ~42.5 min) and every
    ~61s for PHASE-4-BUILD-WORKFLOW starting 09:13:54Z (37 rows through
    09:54:11Z, plus one straggler at 10:17:52Z after the fix had landed but
    before that tick's `DIRECTIVE.yaml` reload picked it up -- consistent
    with commit `68e0b94`'s 09:41:11Z landing time and the engine's 60s poll
    cadence plus deploy propagation). Measured rate: **~2 junk rows/min**,
    matching the SPEC's own figure.
  - **After**: queried `umr_tasks` directly (`resource_governor.py
    --query-umr --task-identity <id> --limit 100`) for both identities --
    **zero** rows with `status=rejected_duplicate` after 2026-08-06
    10:17:52Z. Current time at verification: 2026-08-06T21:2x UTC, i.e.
    **~11 hours clean**, far exceeding the 5-minute bar this step asks for.
    `DIRECTIVE_RETRY_STATE.json` (mtime 10:17:51Z) shows both identities
    already marked retried-once, consistent with the fixed code.
  - Also directly re-ran the SPEC's own literal repro command,
    `python3 resource_governor.py --query-umr --limit 14` (most-recent-14,
    unfiltered): **0 of 14** rows are DIRECTIVE/rejected_duplicate for either
    identity -- the SPEC's claim that this command "currently returns twelve
    of fourteen rows" as such is **not reproducible now**; the 14 most recent
    rows are ordinary `owner_dispatch_gateway` activity from the last ~2
    hours. The flood the SPEC describes was real but had already fully
    stopped, on its own, via the pre-existing fix, well before this task
    started.
  - Net: **no code change made in this task** -- the fix this SPEC step 3
    asks for was already implemented, deployed, and independently verified
    working. Re-implementing it would duplicate `b0a2516`/`68e0b94` and risk
    reopening the exact cross-module fragility round 2 already closed.

- [x] Step 5 -- honest report on whether the underlying queued rows are
  genuinely stuck.
  - **PHASE-4-BUILD-WORKFLOW**'s underlying row (`UMR-20260729-112414-3269`):
    not stuck. Dispatched 2026-08-06 10:42:18Z, ran to real completion,
    reconciled `completed` at 11:17:18Z via the heartbeat sweep
    (`veridian-worker@task-20260806-104213-...`, real `returncode=0`).
  - **PHASE-3-BUILD-CALC**'s underlying row (`UMR-20260730-041943-093a`):
    dispatched 10:42:22Z, ran ~8.5 hours, then its worker unit
    (`veridian-worker@task-20260806-104218-...`) received an explicit
    `SIGTERM ... on client request` at 19:17:33Z (journalctl, verbatim) and
    the row reconciled to `killed`. "On client request" is a deliberate
    external stop, not a timeout/hang -- consistent with the standing Owner
    absolute stop-work order (`task-20260806-165921-owner-absolute-stop-work-order`,
    issued 16:59:21Z, which explicitly pauses PR/push work) rather than a
    queue bug.
  - Conclusion: **neither underlying row is genuinely stuck** by real
    evidence. No child UMR proposal was filed for step 5, because filing one
    against a row that completed normally, or one that was intentionally
    stopped by a standing Owner order, would itself be a false-premise
    escalation of exactly the kind [[veridian-task-prompt-false-premise-pattern]]
    warns about. If PHASE-3-BUILD-CALC's work still needs to finish, that is
    a resume decision gated on the Owner stop-work order lifting, not a bug
    fix.

- [x] Rebased onto latest `origin/main` before opening the PR (already
  up to date, `1110091`, no PROGRESS.md conflict).
- [x] Opened PR, documentation-only (no functional code touched -- the fix
  already exists on `main`).

## Remaining
- [ ] None. Task complete pending PR merge.

## Real measured row-creation rate (as required by SPEC)
- Before fix: ~2 junk `rejected_duplicate`/`DIRECTIVE` rows/min for
  PHASE-3-BUILD-CALC + PHASE-4-BUILD-WORKFLOW combined, 09:11:51Z-09:54:11Z.
- After fix (already live before this task started): 0 rows/min, confirmed
  clean for ~11 hours as of this verification (09:54:11Z/10:17:52Z last junk
  row -> 21:2xZ now).
- Fix commits: `b0a2516098b24a4a8881474e5215530fe9fdf76e`,
  `68e0b9471006af9f88bf918081d179e9114a72b7` (both pre-existing on `main`,
  not authored by this task).

## Update (invocation 2/20)
- Re-verified state on resume: task's own conclusion and fix status unchanged
  (nothing new landed on `directive_engine.py` since invocation 1).
- PR #225 had drifted to `CONFLICTING`/`DIRTY` against `origin/main` purely
  because many unrelated PRs merged (each replacing the shared root
  `PROGRESS.md`) after this branch was opened -- not a content problem with
  this task's own change.
- Rebased onto latest `origin/main` (`5ebc095`), resolved the `PROGRESS.md`
  conflict by taking this task's own full section (that file is
  fully-replaced-per-task, not cumulative, per the repo's established
  pattern), force-pushed with `--force-with-lease`.
- Confirmed via `gh pr view 225`: now `mergeStateStatus=CLEAN`,
  `mergeable=MERGEABLE`. No functional code touched in this update either --
  still documentation-only.
