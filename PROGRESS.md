- [ ] Flag for the next real dispatch: run `deploy-live-scripts.sh` (or
      wait for its normal cron cadence) so `/opt/veridian/scripts/` picks
      up PR #250/#251/this task's `resource_governor.py` changes and steps
      2/9 stop fail-open no-op-ing. (PR #251's own merge, separately flagged
      as remaining by a concurrent task's PROGRESS.md, is now resolved --
      merged via this task's STEP 1 above.)
- [ ] Graduate into `capability_registry` citing this UMR
      (UMR-20260807-110133-205d).
- [ ] `agent_work_briefing.py record-completion` for UMR-20260807-110133-205d.

---

- [ ] None. Declined the redundant re-close as a correct non-failure outcome;
      flagging again (8th time in this chain) that the SPEC-generation source
      needs a live-state check before dispatch, not another worker cycle.

---

# PROGRESS -- task-20260807-081913-amendment-to-umr-20260807-070110-5ea7--s

Real amendment to UMR-20260807-070110-5ea7 (governed by UMR-20260806-124055-bc80):
extends 5ea7's narrow single-UMR owner_priority_override fix into a real,
self-advancing 4-phase owner_priority_sequence.

## Pre-work verification (before any write)

- [x] Verified every cited UMR id in the SPEC is a real row in the live
      `umr_tasks` table (not fabricated) -- see check below.
- [x] Confirmed no `owner_priority_override`/`owner_priority_sequence`
      table or code exists yet anywhere in the repo (grep across scripts +
      ai-os came back empty) -- this is genuinely new work, not a
      duplicate of prior work.
- [x] Confirmed OCID-020 -> `UMR-20260802-165606-4413` and OCID-021 ->
      `UMR-20260802-173631-ca85` via a real `ocid_canonical_registry`
      lookup (never hand-typed).
- [x] Caught and avoided a real false-positive trap in Phase 3/4 discovery:
      a raw substring LIKE scan of `metadata_json` matched 567/8022 rows
      for OCID-020's governing UMR, but several real rows carry
      multi-megabyte `metadata_json` blobs (confirmed:
      UMR-20260806-130110-c620 at 7.1MB) that are historical audit-report
      dumps, not real linkage -- e.g. `UMR-20260729-112414-3269`, dated
      *before* OCID-020's own governing UMR even existed, only "matched"
      because its 1.19MB metadata_json embeds an unrelated report
      mentioning it. Fixed by scoping the real deterministic search to each
      row's parsed `inputs_json.prompt`/`.title` fields only (179 real
      hits for OCID-020, 70 for OCID-021).
- [x] Caught a second false-premise risk: `umr_tasks.status='completed'`
      is **not** always backed by real, independently-verifiable evidence
      -- e.g. Phase 1 member `UMR-20260806-141055-1fec` is `status=completed`
      but its own `outputs_json` only ever recorded a spawned child
      task id, never a commit/file. Built `_umr_genuinely_completed()` to
      re-verify real evidence (file exists on disk / commit is a real
      ancestor of origin/main) rather than trusting the status label alone.

## Completed

- [x] `superboss-register.py`: added `owner_priority_sequence` +
      `owner_priority_override` tables (`_ensure_owner_priority_tables`),
      real deterministic Phase 3/4 discovery (`discover_prompt_citing_umrs`,
      `_lookup_ocid_governing_umr`, `build_owner_priority_sequence_phases`),
      idempotent seeding (`seed_owner_priority_sequence`), real
      evidence-based completion check (`_umr_genuinely_completed`, reusing
      the existing `validate_umr_terminal_completion_evidence` gate), the
      real advance function (`advance_owner_priority_phases`) and
      override-resync (`_sync_owner_priority_override`), plus 3 CLI
      subcommands (`seed-owner-priority-sequence`,
      `advance-owner-priority-phases`, `show-owner-priority-state`).
- [x] `resource_governor.py`: `run_tick()` now calls
      `_advance_owner_priority_phases_safe()` first, before
      `next_queued_task()`/`dispatch_one()` -- fail-open (never raises),
      same convention as `scan_stuck_tasks`/`dispatch_one`'s own
      `_safe_superboss_register` wrapper. Deliberately does **not** touch
      `next_queued_task()`'s own row-selection logic -- that consumption
      side of `owner_priority_override` is UMR-20260807-070110-5ea7's own
      separately-dispatched real work; this only keeps the table populated.
- [x] `test_owner_priority_sequence.py` (5 tests, all real, run against a
      real **copy** of the live DB via SQLite's own `backup()` API, never
      the live table -- a raw `shutil.copy2` of this WAL-mode live DB was
      tried first and produced a real "database disk image is malformed"
      error mid-run, confirming the copy must use the backup API, not a
      byte copy):
      - all 4 phases seed with real discovered UMR ids, every id verified
        to be a real `umr_tasks` row
      - only Phase 1 is active immediately after seeding
      - a tick before Phase 1 members are genuinely complete makes no
        transition
      - once Phase 1 members are given real evidence (a real existing
        file path), the phase correctly advances: Phase 1 -> complete,
        Phase 2 -> active, `owner_priority_override` resynced to exactly
        Phase 2's members
      - never more than one phase is active at once
      - the live DB is provably untouched by any of the above
- [x] `test_resource_governor_owner_priority_advance.py` (2 tests): confirms
      `run_tick()` calls the advance function before the dispatch loop and
      seeds a real copy DB correctly; confirms fail-open behavior (never
      raises) when Superboss Register is unavailable. `max_dispatches=0`
      guarantees no real systemctl/dispatch call is ever made.
- [x] All 7 new tests pass (`python3 -m pytest test_owner_priority_sequence.py
      test_resource_governor_owner_priority_advance.py -q` -> `7 passed`).
- [x] Verified I had accidentally made this exact edit against the **live**
      shared checkout at `/opt/veridian/scripts/superboss-register.py`
      (wrong repo -- not this task's own git workspace) before catching it;
      reverted that file back to its pre-edit state (confirmed via
      `git diff` that only the other concurrently-running worker's
      legitimate in-progress changes remained) and redid the real work in
      this task's own workspace/branch instead.
- [x] PR #256 review.json (reject, tier1) flagged this claim as "a claim
      about a live production file outside this diff's own scope and was
      not independently re-verified as part of this review" -- independently
      re-verified during the real fix-up pass for that review:
      `git diff -- superboss-register.py` in `/opt/veridian/scripts` right
      now contains 122 insertions/5 deletions, all scoped to
      UMR-20260807-035145-aa45's vector-similarity work (`_vector_similarity`
      import, `_migrate_wiring_registry_vector`,
      `_migrate_capability_registry_vector`) -- grepping that live diff for
      `owner_priority_sequence`, `owner_priority_override`,
      `advance_owner_priority_phases`, `_umr_genuinely_completed` returns zero
      matches. The accidental edit is genuinely not present in the live
      checkout; only the other, unrelated worker's legitimate in-progress
      change remains, confirming the original claim above.

- [x] Opened PR #256: https://github.com/FChecklist/veridian-scripts/pull/256

## Remaining

- [ ] PR #256 review/merge (out of this task's control once opened).
- [ ] Once merged and deployed via the existing live-deploy pipeline, run
      `python3 superboss-register.py seed-owner-priority-sequence` for real
      against the live DB (not done by this task -- deploy pipeline's job,
      per this repo's own convention).

---
# PROGRESS -- task-20260807-142156-fix-pr-256-real-audit-fail--memoize-owne

Real fix-up task (governed by UMR-20260807-070904-736a, UMR-20260807-070110-5ea7),
same PR #256, not a new one.

## Pre-work verification (before any write -- standing false-premise-check policy)

- [x] Fetched PR #256's real head (`d890bae`, commit ts 2026-08-07T11:58:27Z)
      and read `advance_owner_priority_phases()` as it actually exists there.
      **The exact finding quoted in this task's own SPEC (no memoization, no
      per-tick bound, no short-circuit for the cheap file_path path) was
      already fixed on this head** -- `confirmed_complete_members`
      memoization, `OWNER_PRIORITY_PHASE_MAX_EVALUATIONS_PER_TICK=25`, and
      `validate_umr_terminal_completion_evidence()`'s file_ok short-circuit
      all already exist in the code, with an existing passing regression
      test (`test_advance_memoizes_confirmed_members_and_bounds_per_tick_evaluations`).
      This SPEC's quoted "exact real finding" text is verbatim the **first**
      of PR #256's two real audit-fail review comments (posted
      2026-08-07T08:48:49Z, i.e. *before* `d890bae` fixed it) -- confirmed via
      `gh api repos/.../issues/256/comments`, not the current/second one
      (posted 2026-08-07T12:06:52Z, *after* `d890bae`). Matches this
      environment's own recurring false-premise pattern (stale/superseded
      finding text handed down as if current).
- [x] Read the real, current (second, most recent) audit-fail comment in
      full instead of trusting the SPEC's quote. Its real finding: round 1's
      own fix still ran the entire real evidence-check loop (including real
      60s-timeout `git fetch`/`cat-file`/`merge-base` subprocess calls for
      commit_sha-backed members) while `resource_governor.py`'s
      `_advance_owner_priority_phases_safe()` held superboss-register.py's
      cross-process `_write_lock()` -- the same OS-level flock every other
      write-path invocation of the script (dispatch, submit, mark-terminal,
      ...) system-wide must also acquire. A degraded network during an
      active large phase could hold that lock for tens of minutes, blocking
      every other worker's write across the whole Superboss Register --
      worse than the bug round 1 fixed, not better. Also flagged (minor): no
      `finally` around `conn.close()` in `_advance_owner_priority_phases_safe`,
      leaking the connection on an exception path.
- [x] Independently re-read the current code myself and confirmed both
      findings are real: `resource_governor.py`'s
      `_advance_owner_priority_phases_safe()` wrapped the *entire*
      `sbr.advance_owner_priority_phases(conn, now=now)` call (including its
      internal evidence-check loop) in `with sbr._write_lock():`; and
      `conn.close()` sat after that `with` block with no `try`/`finally`.
      Decided to fix the real, current (second) finding rather than
      re-applying the already-fixed first one, per this task's own SPEC
      intent ("real fix required" / "fresh audit against the new head").

## Completed

- [x] `superboss-register.py`: restructured `advance_owner_priority_phases()`
      to acquire `_write_lock()` itself, in two short separate critical
      sections around the real reads/writes only (seed + read active phase;
      later, write confirmed_complete_members / phase transition / override
      resync) -- the real evidence-check loop in between (the one place that
      can shell out to real git subprocess calls) now runs with **no lock
      held at all**. The final write section re-reads
      `confirmed_complete_members` fresh immediately before writing and
      unions it with this call's own newly-confirmed members (never
      clobbers a real concurrent writer that committed during the unlocked
      loop), and re-verifies the phase is still genuinely `'active'` before
      transitioning it to `'complete'`.
- [x] Removed the now-redundant (and, if left, actively-harmful via
      `_write_lock()`'s own real reentrancy) outer `_write_lock()` wrapping
      at both real call sites: `resource_governor.py`'s
      `_advance_owner_priority_phases_safe()` and `superboss-register.py`'s
      `cmd_advance_owner_priority_phases()`.
- [x] `resource_governor.py`: fixed the real connection-leak-on-exception
      finding too -- `conn` is now opened before, and closed in a real
      `finally` after, the call.
- [x] `test_owner_priority_sequence.py`: added
      `test_phase3_4_scale_git_subprocess_calls_bounded_and_lock_not_held` --
      builds a synthetic 150-member phase (real umr_tasks rows, real
      commit_sha-only evidence, comparable order of magnitude to the SPEC's
      own 179/70 phase 3/4 evidence), monkeypatches the one real subprocess
      entry point (`_default_ocid_resolver_runner`) with a counting fake
      that also records `sbr._write_lock_depth[0]` at call time, and
      asserts (a) no single tick ever issues more than `cap * 4` real git
      subprocess calls regardless of total member count (stays flat across
      repeated ticks, never grows unbounded) and (b) not one of those calls
      happened while `_write_lock()` was held. Verified this test actually
      catches the real round-2 regression: temporarily re-wrapped
      `advance_owner_priority_phases()` in an outer `_write_lock()` (the old
      bug) and confirmed the test fails with 600 real subprocess calls
      recorded at `lock_depth=1`; reverted, test passes clean.
- [x] Full real suite: `pytest test_owner_priority_sequence.py
      test_resource_governor_owner_priority_advance.py -v` -> **10 passed**,
      including `test_live_db_untouched`.

## Remaining

- [ ] Push to PR #256's existing branch (same PR, not a new one, per SPEC).
- [ ] Fresh audit against the new head commit is required before merge --
      this task does not merge it itself, per SPEC.
