# PROGRESS -- task-20260806-042805-pm-approves-proposal-5--fresh-clean-corr

Real PM approval + fresh clean re-dispatch of the `file_inventory` corruption
recovery on `/opt/veridian/ai-os/memory/superboss-register.sqlite`, relates
to UMR-20260806-042004-e22f / `pm_decisions_pending` row id=5.

## Pre-flight independent verification (per standing false-premise-pattern guidance)

- [x] Re-checked `pm_decisions_pending` id=5 directly on the live DB before
      acting: **the SPEC's "First" instruction (`decide-owner-proposal --id 5
      --decision approved --closed-by PM --closed-note approved`) was
      already executed** -- row already shows `status=approved`,
      `closed_by=PM`, `closed_note=approved`, `closed_ts=2026-08-06T04:24:11Z`.
      `decide_owner_proposal()`/`resolve_pm_decision_pending()` in
      `superboss-register.py` gate the UPDATE on `WHERE status='open'` and
      are explicitly documented as idempotent (a second call is a no-op,
      CLI exits 1). Did **not** re-run it -- would only have produced a
      misleading non-zero exit with no effect. Treating this as another
      instance of the standing memory note
      (`veridian-task-prompt-false-premise-pattern`): the "First" step
      premise did not match live state; verified independently before
      writing anything.
- [x] Confirmed `/home/rajat/.local/bin/sqlite3` is now v3.53.4
      (2026-07-24 build) and **does** expose the `sqlite_dbpage` virtual
      table (`SELECT * FROM pragma_module_list WHERE name='sqlite_dbpage'`
      returns a row) -- the v3.45.1 alt1 build that blocked Step 3 in the
      prior real attempt (commit `cbbfc11`) has been superseded.
- [x] Confirmed Steps 1-2 (fresh live backup + separate working copy, both
      `sqlite3 .backup`, WAL-safe) were already done in a prior session,
      both 1,574,633,472 bytes:
      -> `superboss-register.sqlite.bak-pre-file_inventory-recover-20260806T025938Z`
      -> `superboss-register.sqlite.working-copy-20260806T025938Z`
- [x] **Contrary to the prior task's false "awaiting review" self-report
      (commit `35c67a9`, which only trimmed 139 lines out of PROGRESS.md and
      did zero real recovery work)**, independently discovered that Step 3
      had, separately, actually already been run successfully at
      2026-08-06T03:13 (`RECOVER_SQL_EXIT=0`, `IMPORT_EXIT=0` in the
      recover's own `.err` sidecar) against the Step-2 working copy,
      producing:
      -> `superboss-register.sqlite.recover-sql-20260806T025938Z.sql` (recover SQL dump)
      -> `superboss-register.sqlite.recovered-20260806T025938Z` (1,571,598,336 bytes)
      Verified this artifact directly: `file_inventory` now reads
      **27,249 rows** (was 100% unreadable/`malformed` before), all 90
      tables present (matches the Step-1 backup's table count), schema for
      `file_inventory` identical to live's.
- [x] Root-caused why the prior real attempt's Step 4 (row-count verify)
      failed: it compared the recovered snapshot's `umr_tasks` count
      (6,832, frozen at the 02:59:38Z snapshot) against **current live**
      `umr_tasks` (7,056 now) -- live is a continuously-written, moving
      target (WAL, active writers), so that comparison was structurally
      unable to pass and was not a sign of bad recovery. Correct check is
      recovered-vs-snapshot-baseline, not recovered-vs-current-live.
- [x] Re-ran `PRAGMA quick_check` against live now: corruption is still
      isolated to exactly **Tree 38 (`file_inventory`) and Tree 39
      (`sqlite_autoindex_file_inventory_1`)** -- same scope as originally
      diagnosed, nothing has worsened, no other table affected.
- [x] **Rehearsed the actual repair on a disposable copy** of the working
      copy (`/tmp/rehearsal-corrupted.sqlite`, deleted after) before ever
      touching live:
      - `DROP TABLE file_inventory` on the corrupted table **fails**
        (`database disk image is malformed`) and SQLite auto-rolls back the
        whole transaction (confirmed the original table was still intact
        afterward -- safe failure mode, but DROP is not usable here).
      - Rename-swap instead (`CREATE file_inventory_new` + `INSERT ...
        SELECT FROM recovered.file_inventory` + `ALTER TABLE file_inventory
        RENAME TO file_inventory_corrupted_orig_<ts>` + `ALTER TABLE
        file_inventory_new RENAME TO file_inventory`) **works cleanly**:
        `ALTER TABLE RENAME` only touches the schema catalog, never the
        corrupted table's data pages, so it succeeds where DROP can't.
        Verified on the rehearsal copy: new `file_inventory` reads 27,249
        rows, old corrupted tree survives untouched under the renamed name
        (harmless, quarantined, matches this repo's existing convention of
        keeping `*.CORRUPTED-*` artifacts rather than deleting them).
- [x] Explicitly rejected a full-file swap (recovered file -> live path):
      live has kept accepting real writes since the 02:59:38Z snapshot
      (`umr_tasks` alone gained 224 rows by now) across all 90 tables: a
      full swap would silently roll back ~90+ minutes of real production
      data to fix one table. Real fix is a targeted, in-place table-level
      repair of just `file_inventory`, leaving every other live table
      exactly as-is.

## Completed

- [x] Step 1: fresh live backup (prior session, verified above).
- [x] Step 2: working copy (prior session, verified above).
- [x] Step 3: `sqlite3 <working-copy> ".recover"` (prior/concurrent session
      at 2026-08-06T03:13, verified above: exit 0/0, 27,249 `file_inventory`
      rows recovered, all 90 tables intact).
- [x] Step 4: verify recovered artifact against the correct (snapshot-time)
      baseline, not a moving live target; rehearsed the live repair
      end-to-end on a disposable copy first (see above).

- [x] Step 5: applied the rehearsed rename-swap repair to the real live
      file (`/tmp/repair_file_inventory.py`, run once, under the script's
      own `_write_lock()` flock convention -- same `fcntl.flock` on
      `superboss-register.sqlite.writelock` `_write_lock()` itself uses,
      30s busy_timeout). Immediately preceded by one more fresh timestamped
      live backup (taken *before* any write, current-state safety net):
      -> `superboss-register.sqlite.bak-pre-file_inventory-live-repair-20260806T044301Z`
      (1,661,845,504 bytes)
      Repair itself: `CREATE TABLE file_inventory_new` (identical schema)
      + `INSERT ... SELECT` all 27,249 rows from the recovered artifact
      (attached read-only) + `ALTER TABLE file_inventory RENAME TO
      file_inventory_corrupted_orig_20260806T044301Z` (quarantines the old
      corrupted tree by catalog rename only -- never touches its data
      pages, so it can't hit the same `database disk image is malformed`
      error `DROP TABLE` hit in rehearsal) + `ALTER TABLE file_inventory_new
      RENAME TO file_inventory` + commit. In-script guard: row count from
      the recovered source vs. rows actually inserted checked before the
      renames/commit; would have rolled back untouched on any mismatch
      (none occurred). Total transaction time: 0.686s.
- [x] Step 6: post-repair verification, all real, all on the live file:
      - `file_inventory` now reads real data, **27,249 rows** (was
        `database disk image is malformed`, 0 readable rows, before).
      - `file_inventory` is now a fresh B-tree (rootpage 405938, fresh
        autoindex rootpage 405939) -- confirmed via `PRAGMA quick_check`
        that this new tree reports **zero** errors.
      - The old corrupted tree (rootpages 38/39) still exists, but now
        harmlessly quarantined under
        `file_inventory_corrupted_orig_20260806T044301Z` / its autoindex --
        `quick_check` still (expectedly, harmlessly) flags *that* renamed
        table's tree, not live `file_inventory`.
      - Every other table verified untouched and reflecting continued live
        growth, not a rollback: `umr_tasks`=7,078 (was 7,056 pre-repair,
        6,832 at the original snapshot -- growth preserved throughout),
        `ocid_canonical_registry`=69, `gtm_certification_categories`=25
        (both match known baselines). `sqlite_master` table count = 91
        (90 original + 1 quarantined corrupted-orig table, expected).
      - Live file size after repair: 1,673,711,616 bytes.

- [x] `record-owner-proposal-completion` run on `pm_decisions_pending`
      id=5, citing `repair_file_inventory_20260806.py` (PR
      https://github.com/FChecklist/veridian-scripts/pull/118), commit
      `f80da2c7ee2b7e99954f2b46f2105ef5a9034584`, and the full evidence
      string (row counts/byte sizes/verification detail above). Confirmed
      on the live row: `status=completed`,
      `completed_ts=2026-08-06T04:45:39Z`.

## Remaining

- [ ] None -- all 6 recovery steps + the proposal completion record are
      real and done. PR #118 open for review/merge.
