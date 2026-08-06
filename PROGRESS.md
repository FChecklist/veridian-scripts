# PROGRESS -- task-20260806-073846-owner-directive--build-a-real-pm-cycle-s

SPEC: real Owner directive, citing `UMR-20260805-181636-32f2` (report-generator chain) and
`UMR-20260805-185000-e94f` (deterministic-script-consolidation chain) -- extend the zero-manual-search
principle from the 10-minute PM report to the rest of the real PM cycle: (1) script registry
sufficiency check, (2) backfill every existing script with its originating UMR, (3) one new read-only
PM-cycle data-gathering script, (4) self-registration of every touched/new script.

## Completed

- [x] **Verified independently, per this session's own standing memory note (SPECs have repeatedly not
      matched live state -- verify before any write) -- this exact SPEC, word-for-word including the
      same two UMR citations, was already fully executed by a prior task
      (`task-20260806-035541`) and merged to `main` as **PR #114**, `mergedAt=2026-08-06T07:36:48Z` --
      **~2 minutes before this task (`task-20260806-073846`) was even dispatched.** This task is a
      duplicate re-dispatch of an already-closed directive, not new work. Per the SPEC's own explicit
      "zero duplication applies to this request too" principle, did not rebuild or parallel-implement
      anything. Confirmed every deliverable is genuinely live rather than trusting the merge alone:
  - **Item 1 (registry sufficiency check):** confirmed live in `superboss-register.py` --
    `capability_registry` already has `version TEXT NOT NULL DEFAULT 'unversioned'` (line 512, no
    version field was missing). It's schema-shaped for business capabilities, not generic script
    bookkeeping (no `path` column) -- PR #114 correctly used `wiring_registry`'s `entity_type='script'`
    rows instead (already zero-dup-safe by `ON CONFLICT` upsert on `entity_id`), extended with two new
    nullable columns `originating_umr` / `script_version` via an additive idempotent migration
    (confirmed present at `superboss-register.py:737-741` and in the entity-type-rebuild path at
    `:2739-2740`). No new parallel table was built.
  - **Item 2 (backfill):** `generate_software_catalog.py` + `generate_wiring_registry.py` backfill path
    confirmed present and already run for real against the live production DB (see PR #114's own
    PROGRESS.md / `PM_CYCLE_PRECHECK_VERIFICATION_2026-08-06.md` for the full live evidence: 101 -> 122
    scripts cataloged, 124 `entity_type='script'` rows tagged with real, mechanically-recovered
    `originating_umr` where honestly recoverable, `NULL` where not, none invented). Independently
    reconfirmed the `gtm_check_*.py` premise in the SPEC is still false -- zero such files exist on
    this live server (`ls /opt/veridian/scripts/gtm_check_*.py` -> no matches); they exist only on
    unmerged feature branches, as already documented.
  - **Item 3 (new script):** `pm_cycle_precheck.py` confirmed live in this workspace **and** already
    deployed to the real `/opt/veridian/scripts/pm_cycle_precheck.py` (verified as a genuinely separate
    path from this task's own workspace, not a symlink/alias). Ran it for real against the live
    production DB as this task's own independent re-verification -- see sample invocation output below.
    All 5 sections (server health, dispatch-tick deltas since last cycle, zero-dup precheck, tracked PR
    state via `gh pr view`, OCID-068 regression checks) executed correctly, read-only, in one SSH round
    trip.
  - **Item 4 (self-registration):** confirmed all 4 touched/new scripts from PR #114
    (`superboss-register.py`, `generate_software_catalog.py`, `generate_wiring_registry.py`,
    `pm_cycle_precheck.py`) are self-registered in `wiring_registry` per that PR's own logged
    `register-entity` invocations.
  - Test suite: `python3 -m pytest test_pm_cycle_precheck.py test_generate_software_catalog.py -q` --
    **16 passed**, run fresh in this task, not assumed from PR #114's own record.

## Real live evidence (this task's own independent re-verification, 2026-08-06T07:40:27Z)

Sample invocation, run for real against the live production DB, read-only
(`--no-bookkeeping-write`):

```
$ python3 pm_cycle_precheck.py --search-term "pm cycle precheck duplicate" --pr-numbers "133,114" --no-bookkeeping-write

==============================================================================
PM CYCLE PRECHECK -- 2026-08-06T07:40:27.782495+00:00
==============================================================================

-- Server health --
  RAM/swap: {'mem_total_mb': 15608, 'mem_available_mb': 12549, 'swap_total_mb': 4095, 'swap_free_mb': 1041, 'swap_free_pct': 25.42}
  Load average: {'load_1min': 7.4, 'load_5min': 6.85, 'load_15min': 7.52}
  Dispatch tick active: True
  Parallel workers: 4
  Stuck tasks: 660
  tmux session alive: True
  Emergency stop present: False
  DB integrity ok: True

-- Dispatch tick results since last PM cycle (2026-08-06T07:37:44.080540+00:00) --
  total tasks since last cycle: 0

-- Zero-duplication precheck for 'pm cycle precheck duplicate' --
  active (queued/dispatched/running) umr_tasks matches: 30
    UMR-20260806-042540-e272 [running] corruption-recovery-file_inventory-resume-20260806-0424
    UMR-20260806-031558-4dbd [running] child-umr-pm-decisions-pending-writer-redispatch-v2
    ... (30 total, truncated for this report)
  existing capability/script/knowledge check found=35 verdict=STOP -- existing mechanism(s) found, review before building

-- Tracked PR state checks --
  PR #133 [OPEN]: fix(pm-report-v3): Section 12 real perf fix ... merged_at=None
  PR #114 [MERGED]: feat: real PM-cycle script registry extension + zero-manual pm_cycle_precheck.py merged_at=2026-08-06T07:36:48Z

-- OCID-068 regression checks --
  resolver present (resolve_ocid_canonical): True
  ocid_canonical_registry row count: 69 (expected 69) -> OK
  seven guardrail PRs still ancestors of origin/main:
    PR #26 (Rule 1) OK / PR #29 (Rule 2) OK / PR #30 (Rule 3) OK / PR #32 (Rule 4) OK /
    PR #33 (Rule 5) OK / PR #34 (Rule 6) OK / PR #35 (Rule 7) OK
  REGRESSION DETECTED: False
==============================================================================
```

Notably, the script's own zero-duplication precheck, run against the search term describing this very
task, correctly flags `verdict=STOP -- existing mechanism(s) found` -- a live, mechanical confirmation
(not asserted by me) that this exact capability already exists, corroborating the duplicate-dispatch
finding above.

## Remaining

- [ ] Nothing further for this worker to do. All four SPEC items are independently confirmed live on
      `main` via PR #114 (already merged, `mergedAt=2026-08-06T07:36:48Z`). No new PR opened by this
      task -- there is no real diff to ship without duplicating already-merged work, and the SPEC's own
      "zero duplication applies to this request too" clause forecloses building a parallel
      implementation just to have something to submit.
- [ ] Recommend the dispatch layer treat this as closed against PR #114 rather than continuing to
      re-issue this directive -- this is the second time in this repo's recent history a nearly
      simultaneous re-dispatch of an in-flight/just-completed directive has occurred (see this
      session's memory note on the veridian-scripts task-dispatch false-premise pattern for the prior
      instance).
