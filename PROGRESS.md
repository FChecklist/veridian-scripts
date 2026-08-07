# PROGRESS -- task-20260807-051409-correction--wiring-registry-corruption-n

Governing chain (as claimed by SPEC): UMR-20260806-124055-bc80, UMR-20260806-222708-1d3b,
task-20260807-003146-critical--real-corruption-confirmed-in-w.

## Completed
- [x] Independently verified every load-bearing claim in the SPEC before taking any
      destructive DDL action (per known recurring false-premise pattern in this repo --
      memory `veridian-task-prompt-false-premise-pattern`).
  - Both governing UMR IDs queried directly against the live `resource_governor.py
      --query-umr --task-identity`: **UMR-20260806-124055-bc80 -> 0 matches**,
      **UMR-20260806-222708-1d3b -> 0 matches**. Neither exists. (The third reference,
      `task-20260807-003146-critical--real-corruption-confirmed-in-w`, does exist and its
      own PROGRESS.md is the actual source of ground truth used below.)
  - Read that prior task's PROGRESS.md in full. It shows Step 4 (drop + rebuild
      `wiring_registry`) **was fully completed live**, restoring 24,281
      `.recover`-validated rows and rebuilding the FTS5 index, with real verified evidence
      at the time. Step 5 (re-running the heavier scan scripts for incremental freshness)
      was explicitly and transparently **deferred as a documented nice-to-have**, not
      silently skipped -- the restored data was already an exact validated snapshot of the
      pre-corruption state, not a guess or an empty table.
  - That same document *predicted*, in writing, exactly the symptom this SPEC cites as new
      evidence: a whole-DB `PRAGMA integrity_check` would keep surfacing
      `Tree 89 page 512918 cell 448: Rowid 24281 out of order` because the *old* corrupted
      table was deliberately **renamed aside** (not dropped) to
      `wiring_registry_corrupted_orig_20260807T004638Z` as a forensic remnant, on top of an
      already-taken full-file backup (`superboss-register.sqlite.corrupt-wiring-registry-
      real-20260807T004029Z`, md5 recorded in that same doc). SPEC presents this predicted,
      already-documented symptom as fresh proof nothing was ever fixed.
  - Independently re-ran `PRAGMA integrity_check` myself against the live DB just now
      (read-only, `immutable=1`, fresh connection): reproduced the identical error text.
      Then used `dbstat` to determine **which table page 512918 actually belongs to**:
      exclusively `wiring_registry_corrupted_orig_20260807T004638Z` -- zero overlap with
      the live `wiring_registry` table's own pages.
  - Independently full-table-scanned the live `wiring_registry` table (`COUNT(*)`,
      `COUNT(DISTINCT entity_id)`, `MIN/MAX(rowid)`): 24,296 rows, all distinct, zero
      errors reading every page. FTS5 shadow table row count matches exactly (24,296 =
      24,296) -- the live table and its search index are structurally healthy.
  - Checked the `ts` field distribution on the live table: rows carry fresh, real
      timestamps through **2026-08-07T04:xx** (7,846 rows) and **2026-08-06T19:xx**
      (15,690 rows) -- both after the corruption window and consistent with the prior
      task's restore + the `veridian-cron-generate-wiring-registry.timer` (confirmed
      active/waiting by that same prior task) picking up its normal scheduled cadence
      exactly as that task predicted it would. This is direct, positive evidence of an
      actual completed rebuild plus ongoing organic growth -- the opposite of "unchanged
      since before any recovery attempt."
  - Re-examined the SPEC's specific "file entity_type count == 17662 = proof of no
      rebuild" claim: 17,662 is the *exact* file count that the prior task's
      `.recover`-validated restore produced (documented in its own PROGRESS.md) -- an exact
      match to a faithful, validated rebuild is expected, correct behavior, not evidence of
      inaction. The table's *total* row count (24,296, up from the restore's 24,281) and
      per-type deltas (e.g. `dispatch_event` 661 -> 676) show real incremental growth on
      top of that validated base.

## Remaining
- [ ] None outstanding on the live `wiring_registry` table itself -- it was never actually
      broken in this cycle; the prior task's rebuild was real and already verified.

## Real corrective action actually taken this cycle
(Narrower than the SPEC demanded, and for good reason -- the SPEC's "drop the corrupted
table and rebuild from scratch" target no longer existed; what remained was purely a
leftover-cleanup opportunity the prior task explicitly flagged as optional.)
- [x] Confirmed the renamed-aside `wiring_registry_corrupted_orig_20260807T004638Z` table
      is fully redundant with the pre-existing external file-level backup taken 6 minutes
      earlier (`superboss-register.sqlite.corrupt-wiring-registry-real-20260807T004029Z`,
      confirmed still present, still contains the original pre-rename `wiring_registry`
      table) -- safe to drop from the live DB without losing forensic history.
- [x] Acquired the repo's standard cooperative write lock
      (`flock superboss-register.sqlite.writelock`) and ran
      `DROP TABLE wiring_registry_corrupted_orig_20260807T004638Z` on the live DB.
- [x] Independently re-verified from a **fresh** read-only connection (new process, not
      the dropping process's own exit code):
  - `PRAGMA integrity_check` -> **exactly `ok`**, zero rows/findings, whole database.
  - Live `wiring_registry` row count/breakdown unchanged and intact: 24,296 total
      (file 17,662; function 5,028; dispatch_event 676; supabase_table 444; ai_role 195;
      script 151; cron_job 72; engine 20; github_repo 15; gateway 10; governance_doc 10;
      route 6; browser_component 4; vercel_project 3), newest row `ts` =
      2026-08-07T05:14:19Z.
  - FTS5 shadow row count still matches live table exactly (24,296 = 24,296).
- [x] Recorded findings via `agent_work_briefing.py record-completion` (UMR-20260807-010907-6984).

## Conclusion
The SPEC's central claim -- "no real rebuild has actually happened" -- is false. A real,
validated rebuild happened in the immediately preceding task and was already fully
documented with evidence. The only genuinely real, previously-unaddressed issue was that
the whole-database `PRAGMA integrity_check` still surfaced the old renamed-aside table's
pre-existing corruption; that is now fixed (dropped the redundant, already-backed-up
table), so the DB-wide integrity check is now genuinely, independently verified clean.
This is the known recurring false-premise SPEC pattern in this repo (23+ prior cases, see
memory `veridian-task-prompt-false-premise-pattern`) layered on top of one real, narrow,
pre-existing cleanup opportunity that the prior task had explicitly already flagged as
optional.
