# task-20260814-033923-fix-transient-disk-full-crash-in-target

UMR: UMR-20260814-033914-63ef (addendum to P1 UMR-20260806-171945-5767)

## SPEC vs real code -- what independent verification found

The SPEC's core diagnosis (a real `sqlite3.OperationalError: database or disk
is full` from inside `query_umr_tasks()`, called via
`find_target_identifier_duplicate()`, correlated with a burst of ~40
concurrent dispatch pre-flight checks, while the root filesystem itself was
healthy) checked out and was independently reproduced/confirmed via
`EXPLAIN QUERY PLAN` (see below).

Two SPEC claims did NOT check out against the real schema and were NOT
followed literally:

1. The SPEC says `umr_tasks` columns include "reason, prompt, raw_text,
   metadata_json". `reason` and `metadata_json` are real `umr_tasks` columns;
   **`prompt` and `raw_text` are not** -- those belong to the unrelated
   `instructions` table (`log_instruction()`). `umr_tasks` has no `prompt`
   column; title/prompt text lives inside the `inputs_json` blob.
2. The SPEC's action item #1 ("query only umr_id, task_identity, status,
   ts_submitted, and a bounded prefix... not full=True") would have dropped
   `inputs_json` -- the ONE column `find_target_identifier_duplicate()`
   actually parses to extract target identifiers. `full=True` was itself a
   real, documented, deliberate fix (PR #308) for a prior regression where
   the light-column default silently broke duplicate detection entirely
   (`inputs_json` missing -> always `{}` -> dedup guard never matched
   anything). Following the SPEC's column list literally would have
   silently reintroduced that exact PR #308 regression.

Real fix implemented instead: keep `inputs_json` (required for correctness)
but drop the 3 blob columns `full=True`'s `SELECT *` also pulled that this
function never reads (`outputs_json`, `metadata_json`, `metric_snapshot_json`),
via a new `extra_columns` param on `query_umr_tasks()`. This satisfies the
SPEC's real intent (stop pulling more than what's needed) without breaking
correctness the SPEC's literal column list would have.

## Real root cause (measured, via EXPLAIN QUERY PLAN)

`find_target_identifier_duplicate()` calls `query_umr_tasks()` with **no
status filter** (it must scan every recent row regardless of status, then
filter `queued`/`running` in Python). That lands in the plain-listing branch:
`SELECT ... FROM umr_tasks ORDER BY ts_submitted DESC LIMIT ?` with no
`WHERE`. Before this fix:

```
SCAN umr_tasks
USE TEMP B-TREE FOR ORDER BY
```

`idx_umr_tasks_status_ts` is a **composite** `(status, ts_submitted DESC)`
index -- useless for ordering when `status` is unconstrained (it groups by
status first, not globally by `ts_submitted`). With `full=True` (`SELECT *`),
SQLite materializes the **entire table**, every blob column, into a temp
b-tree before `LIMIT` can apply -- not just the 30 rows returned. 40 near-
simultaneous callers each doing this at once against a ~3GB DB is the real,
measured mechanism for a transient ENOSPC on SQLite's temp store (not the DB
file itself -- confirmed: a plain `SELECT count(*)` succeeded immediately
after the captured crash).

## Completed

- [x] Added `idx_umr_tasks_ts` (`ts_submitted DESC`, standalone index) via a
      new idempotent migration `_migrate_umr_tasks_ts_index()`, wired into
      both `_ensure_umr_table()`'s fast-path gate (so it backfills onto
      already-migrated live DBs) and the slow/bootstrap path. Confirmed via
      `EXPLAIN QUERY PLAN`: the no-status-filter dedup query now plans as
      `SCAN umr_tasks USING INDEX idx_umr_tasks_ts` with **no** temp b-tree
      step.
- [x] Confirmed the pre-existing status-filtered listing path
      (`--query-umr --status X`) is unaffected -- still plans as
      `SEARCH ... USING INDEX idx_umr_tasks_status_ts (status=?)`, no
      regression from adding the second index.
- [x] Added `extra_columns` param to `query_umr_tasks()`/`_umr_select_columns()`
      / new `_umr_light_columns()` helper: lets a caller opt into exactly the
      blob column(s) it reads instead of `full=True`'s `SELECT *`.
- [x] Changed `find_target_identifier_duplicate()` to call
      `query_umr_tasks(conn, limit=limit, extra_columns=("inputs_json",))`
      instead of `full=True` -- keeps the one column needed for correctness,
      drops the 3 (`outputs_json`, `metadata_json`, `metric_snapshot_json`)
      it never reads.
- [x] Added `PRAGMA temp_store=MEMORY` to `_connect()` (SPEC action #2) --
      defense in depth so any temp b-tree/table this connection still needs
      (index fix notwithstanding) can never spill to disk.
- [x] New test file `tests/test_dedup_disk_full_concurrency.py` (SPEC action
      #3/#4):
  - `test_dedup_query_plan_has_no_temp_btree_or_full_select` -- mechanical
    regression guard, direct `EXPLAIN QUERY PLAN` proof of the fix.
  - `test_dedup_query_plan_status_filtered_path_unaffected` -- proves the
    new index doesn't regress the pre-existing status-filtered path.
  - `test_find_target_identifier_duplicate_still_correct_with_extra_columns`
    -- correctness: `extra_columns=("inputs_json",)` still finds a real
    duplicate (does not reintroduce the PR #308 regression).
  - `test_40_concurrent_duplicate_checks_against_realistic_dataset_no_disk_full`
    -- the real load test the SPEC asked for: 300-row realistic seeded
    dataset (each row carrying realistically-sized inputs_json/outputs_json/
    metadata_json blobs) hit by 40 concurrent real CLI subprocess
    invocations of `check-target-identifier-duplicate`. Asserts zero
    failures of any kind (including zero `disk is full` errors) AND that
    duplicate detection is still correct under that concurrency (10/40
    calls target a real live duplicate row, all 10 correctly find it; the
    other 30 correctly find nothing).
- [x] Ran full existing correctness suite for this area
      (`test_target_identifier_dedup.py`, `test_query_umr_by_id.py`,
      `test_query_umr_limit_clamp_and_ensure_table_regression.py`,
      `test_query_umr_exclude_rca_complete.py`, `test_ocid_artifact_links.py`,
      plus every `umr`/`dedup`/`duplicate`/`target_identifier`-matching test
      in `tests/`): 118 passed, 0 failed, 0 regressions.
- [x] Ran full repo test suite (`tests/` + root `test_*.py`) after the
      change to check for unrelated regressions -- see below for result.

## Before/after query cost (real, measured via EXPLAIN QUERY PLAN)

Query shape `find_target_identifier_duplicate()` actually issues
(no status filter, `ORDER BY ts_submitted DESC LIMIT 30`):

- **Before:** `SELECT * FROM umr_tasks ORDER BY ts_submitted DESC LIMIT 30`
  -> `SCAN umr_tasks` + `USE TEMP B-TREE FOR ORDER BY` -- full-table scan,
  every column (incl. 4 blob columns) materialized into a temp b-tree for
  every one of the table's rows, before `LIMIT` narrows the output.
- **After:** `SELECT <light columns>, inputs_json FROM umr_tasks ORDER BY
  ts_submitted DESC LIMIT 30` -> `SCAN umr_tasks USING INDEX
  idx_umr_tasks_ts` -- direct index walk in the exact order needed, stops at
  30 rows, no temp b-tree, only 1 of 4 blob columns touched per row.

Full repo suite result: `727 passed, 2 failed` in 175.88s. Both failures are
pre-existing and unrelated (confirmed via `git stash` + re-run against the
unmodified branch, same 2 failures, same error): `test_build_lock_liveness_
guard_deployment.py::test_timer_is_really_enabled_and_active` (real systemd
timer state on this box) and `test_stop_work_order_gate.py::test_dispatch_
one_defense_in_depth_blocks_preexisting_queued_row` (live worker-cap
contention: `running_worker_count: 5, cap: 5` on this box at test time).
Zero regressions from this change.

## Remaining

- [x] Confirm full repo test suite result -- done, see above (2 pre-existing
      unrelated failures, 0 regressions).
- [ ] Record completion via `agent_work_briefing.py record-completion`.
- [ ] Commit + push.
