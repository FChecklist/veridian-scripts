# PROGRESS -- task-20260815-045659-urgent-structural-fix--next-queued-task

UMR: UMR-20260806-165509-4d7c. Governing chain: UMR-20260806-124055-bc80.

## False-premise finding (verified before any write)

The SPEC's specific claims about the 4 named UMRs and about "no existing
override mechanism" do not match live state:

- Line numbers are wrong: `next_queued_task` is at resource_governor.py:2727
  (not 822); `run_tick` is defined at line 4393 (not called near 1263 --
  that region is unrelated stop-work-order-exemption code).
- **An `owner_priority_override` table already exists**, built by
  UMR-20260807-070110-5ea7 and extended by task-20260807-081913's
  `owner_priority_sequence` 4-phase auto-advance system, in
  superboss-register.py (`_ensure_owner_priority_tables`,
  `_sync_owner_priority_override`, `advance_owner_priority_phases`), wired
  into every real tick via `resource_governor.py`'s
  `_advance_owner_priority_phases_safe()`. Exact schema match to what the
  SPEC asked for (umr_id, reason, set_by, ts). The SPEC's claim "no
  existing override flag exists" is false.
- Queried the real live DB (resolved via `resolve_superboss_db_path()` ->
  `/opt/veridian/ai-os/memory/superboss-register.sqlite` -- **not** the
  0-byte stub files at `/opt/veridian/scripts/superboss-register.sqlite` or
  `/opt/veridian/superboss-register.sqlite`, both empty). Real state of the
  4 named UMRs, queried 2026-08-15 ~05:05 UTC:

  | umr_id | status | tier | ts_submitted | ts_dispatched |
  |---|---|---|---|---|
  | UMR-20260806-135632-329e | completed | 0 | 2026-08-06T13:56 (~8.6 days old) | 2026-08-06T19:20 (set) |
  | UMR-20260806-140841-46d1 | completed | 0 | 2026-08-06T14:08 (~8.6 days old) | 2026-08-06T19:20 (set) |
  | UMR-20260806-141055-1fec | completed | 1 | 2026-08-06T14:10 (~8.6 days old) | 2026-08-15T03:18 (set) |
  | UMR-20260806-162019-4b4f | failed | 0 | 2026-08-06T16:20 (~8.5 days old) | 2026-08-15T04:56 (set) |

  All 4 already have `ts_dispatched IS NOT NULL`; 3 of 4 are terminal
  (`completed`/`failed`, never re-enters `status='queued'`). None are
  ages "160-175+ minutes" -- they are ~8.5 days old. None are currently
  starved/queued. Real total queued tier-0 count right now: 3 (none of
  which are among the 4 named UMRs).
  UMR-20260806-141055-1fec is also already a real member of the currently
  *active* `owner_priority_sequence` phase 1 -- the auto-sync mechanism
  already treats it as priority, and it dispatched hours before this task
  started.

  Consequence: seeding these 4 specific ids into any override
  table/allowlist would be a provable no-op (none are dispatchable), and
  step 4's demand for "real boolean proof [one of these 4] transitions to
  ts_dispatched IS NOT NULL within that tick" cannot be honestly satisfied
  -- 3/4 already have it set from days/hours before this task even began,
  and none re-enter the queue.
- What *is* real and independently confirmed: `next_queued_task`
  (resource_governor.py:2727, real code read) does genuinely sort only by
  `(effective_priority, ts_submitted)` with **no** override/preemption
  check of any kind -- and the existing `_advance_owner_priority_phases_safe()`
  docstring in the same file explicitly says this consumption side is
  real, separately-dispatched, not-yet-landed work ("this mechanism
  (whenever it lands)"). That gap is real and is what this task actually
  fixes -- using the existing table/convention, not a new one, and not the
  4 stale ids.

## Completed

- [x] Verified DB path convention (`resolve_superboss_db_path()`) and real
      live state of the 4 named UMRs and of the existing
      `owner_priority_override`/`owner_priority_sequence` mechanism before
      any write.
- [x] Added `_owner_priority_override_ids(conn)` + wired a narrow,
      bounded preemption check into `next_queued_task()`
      (resource_governor.py): if any currently-`queued` row's `umr_id` is
      an exact member of the existing `owner_priority_override` table, the
      oldest such row wins outright, before and regardless of the normal
      `(effective_priority, ts_submitted)` sort; falls back unchanged when
      no queued row is overridden. Reuses the existing table/schema (no
      new table, no JSON file, per SPEC's own "check existing convention
      first" instruction). Matches on the real `umr_id` column, not a
      prompt/metadata_json substring scan (the substring approach the
      SPEC proposed is the same approach `discover_prompt_citing_umrs()`'s
      own docstring in superboss-register.py documents as producing real
      false positives on this database -- 567/8022 rows on a raw LIKE scan
      vs. 179 genuine citations).
- [x] Added `sbr._ensure_owner_priority_tables(conn)` call in
      `dispatch_one()` before `next_queued_task()`, matching the existing
      `_ensure_umr_table`/`_ensure_ocid_artifact_links_table` convention.
- [x] Added `import sqlite3` (previously unused/unimported in this file;
      needed for the fail-open `except sqlite3.OperationalError`).
- [x] Updated `_advance_owner_priority_phases_safe()`'s docstring to note
      the consumption side has now landed (was previously explicit that it
      had not).
- [x] New test file `test_resource_governor_next_queued_task_owner_priority.py`
      (6 tests, real scratch-DB, no live DB touched): override row beats a
      tier-0/3h-old competitor; falls back to normal sort with no
      override; oldest-of-multiple-overrides wins; **override entry for a
      non-`queued` (e.g. `completed`) row is correctly never selected**
      (direct regression coverage of the false-premise finding above);
      fails open (no crash, normal fallback) when the override table does
      not exist yet; `dispatch_one()` really creates the table before
      selection on a first-ever tick. All 6 pass.
- [x] Ran pre-existing `test_resource_governor_owner_priority_advance.py`
      and `test_resource_governor_queue_management.py` against this change
      (real live-DB-copy based suites): 19 passed, 2 pre-existing failures
      (`test_list_queue_real_dispatch_order`,
      `test_move_down_never_crosses_a_tier_boundary`) -- confirmed via
      `git stash` that both fail identically on the unmodified baseline
      (pre-existing, unrelated to this change; not caused by it).
- [x] Step 4 ("real boolean proof") -- resolved honestly rather than
      literally, see below.

## Step 4 -- real boolean proof, and why it's not a live production tick

Queried the real live DB again right before finalizing (2026-08-15
~05:06 UTC): `owner_priority_override` currently holds
`UMR-20260806-141055-1fec` (already `status='completed'`,
`ts_dispatched` already set hours earlier), plus two ids
(`UMR-20260807-024922-f432`, `UMR-20260807-061238-ae93`) that don't even
exist in `umr_tasks`. **No currently-queued row is an override match
today.** A live production tick, before/after, would therefore show zero
visible change for any override id -- not because the fix doesn't work,
but because nothing eligible exists right now (consistent with the
false-premise finding above).

Also confirmed `veridian-governor-tick.service` (systemd --user) is
`active running` right now -- the real live tick loop -- executing the
currently-*deployed* `/opt/veridian/scripts/resource_governor.py`, not
this branch's modified copy. Manually invoking `run_tick()`/
`dispatch_one()` against the live DB from this workspace would (a) run
the OLD deployed code, proving nothing about this fix, since it isn't
merged/deployed yet, and (b) cause a real, hard-to-reverse production
dispatch/spawn side effect with no clear authorization for that
out-of-band action -- so this was deliberately NOT done.

Real, honest proof instead: the deterministic test suite
(`test_resource_governor_next_queued_task_owner_priority.py`, 6/6
passing) exercises the exact real code path
(`next_queued_task`/`_owner_priority_override_ids`) against real sqlite
tables built with the real `sbr._ensure_umr_table`/
`sbr._ensure_owner_priority_tables`/`sbr.upsert_umr_task` calls, with a
real before/after: seed a tier-0/3h-old real competitor plus a
tier-2/fresh real overridden row -> `next_queued_task()` returns the
overridden row, not the competitor (`test_override_row_wins_regardless_of_tier_and_age`).
Once this PR merges and reaches the live deploy, the next real tick will
apply this logic to whatever the live active phase's real members are at
that time.

## Remaining

- [x] `record-completion` write-back to `agent_work_briefing.py` citing
      UMR-20260806-165509-4d7c, including the false-premise finding.
      Independently re-verified via GitHub: PR #412, 2 non-docs-only real
      files in diff, state=OPEN. `umr_tasks` row marked
      `status=completed` with real evidence
      (commit_sha=7819974f0a869193b58c2b5939ee74e0136b1edf,
      file_path=resource_governor.py, pr_number=412,
      repo=veridian-scripts).
- [x] Evaluated real graduation into `capability_registry` per the
      standing 4-step spec -- this landed as a fix inside an
      already-registered capability's own source file
      (resource_governor.py), not a new standalone script; repo
      convention (`*_capability_record.json` files map one row per
      registered *script* via `mechanism_path`, not per function) means
      no new registry row is warranted here. Not forcing a mismatched
      entry.
- [x] Commit + push. PR opened:
      https://github.com/FChecklist/veridian-scripts/pull/412
      (worker/task-20260815-045659-urgent-structural-fix--next-queued-task
      -> main, commit 7819974).
