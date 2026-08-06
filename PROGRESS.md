# PROGRESS -- task-20260806-025647-owner-authorization--execute-sqlite3-dot

Real Owner authorization to run `sqlite3 .recover` on
`/opt/veridian/ai-os/memory/superboss-register.sqlite`, relates to
UMR-20260805-163026-14f1 / pm_decisions_pending row id=1.

## Pre-flight independent verification (done before touching anything)

- [x] Confirmed UMR-20260805-163026-14f1 is a real row in `umr_tasks`
      (task_identity `owner-task-20260805-163025-2908944`, tier 1, status `killed`).
- [x] Confirmed `pm_decisions_pending` id=1 is real, `status=open`,
      `related_umr=UMR-20260805-163026-14f1`, recommends `sqlite3 .recover`.
- [x] Independently confirmed corruption scope via a direct per-table
      `SELECT count(*)` sweep across all tables (not just trusting the SPEC
      text or `PRAGMA integrity_check`'s tree numbers) -- **only
      `file_inventory` fails**; all other tables read fine.
- [x] Confirmed row-count baselines on the live file:
      `ocid_canonical_registry`=69 ✓, `gtm_certification_categories`=25 ✓,
      `umr_tasks`=6832 (baseline the recovered file must meet/exceed).
- [x] Confirmed live DB is actively receiving writes (WAL mode, 2 active
      python3 PIDs with open FDs) -- confirms the "never run .recover
      against the live path" caution is real, not hypothetical.
- [x] Noted discrepancy: SPEC/PM-decision text says "eighty eight tables";
      actual count is 90 (or 50 excluding FTS5 shadow tables). Immaterial --
      verified the single-table-corruption claim directly rather than
      relying on that count.

## Completed

- [x] Step 1: Fresh timestamped backup of the live file, taken via
      `sqlite3 <live> ".backup <dest>"` (not raw `cp`, since the live DB is
      in WAL mode with active writers -- a filesystem-level copy could miss
      committed-but-not-checkpointed pages). Confirmed non-zero size
      (1,574,633,472 bytes, matches live).
      -> `superboss-register.sqlite.bak-pre-file_inventory-recover-20260806T025938Z`
- [x] Step 2: Separate working copy made the same way, live path never
      opened for write. Confirmed non-zero size (1,574,633,472 bytes).
      -> `superboss-register.sqlite.working-copy-20260806T025938Z`
      Sanity check: working copy reproduces the same `file_inventory`
      corruption as live (proves it's a faithful replica).

## STOPPED -- Step 3 failed, live file untouched

- [ ] Step 3: `sqlite3 <working-copy> ".recover"` **failed**:
      `sql error: no such table: sqlite_dbpage (1)` -- the installed sqlite3
      CLI (`~/.local/bin/sqlite3`, v3.45.1, alt1 build) was compiled without
      the `sqlite_dbpage` virtual table, which `.recover` depends on
      internally. Confirmed root cause: even `CREATE TABLE t(x); SELECT *
      FROM sqlite_dbpage` fails the same way in a fresh in-memory DB, and
      there is no alternate `sqlite3` binary on the host (`/usr/bin/sqlite3`
      does not exist) to fall back to.
- [ ] Steps 4-6: not started (blocked on step 3).

Per the SPEC's explicit instruction ("If any real check at any real step
fails, stop immediately, do not proceed further, leave the real live file
completely untouched, and report the real specific failure back to me
instead of improvising further") and the standing circuit-breaker rule,
stopped here rather than attempting a workaround (e.g. building/installing
a different sqlite3 with dbpage-vtab support). **Live file
`/opt/veridian/ai-os/memory/superboss-register.sqlite` is confirmed
untouched** -- only read from throughout steps 1-2.

`pm_decisions_pending` id=1 left as `status=open` (not marked resolved --
recovery did not complete).

## Remaining (needs Owner decision before any retry)

- [ ] Owner to decide: install/build a `sqlite3` with `sqlite_dbpage`
      support (e.g. from source, or a different packaged binary), or use
      Python's own `sqlite3` module's backup/recover-adjacent tooling
      instead of the CLI's `.recover`, or pursue a different recovery path
      entirely -- then resume at Step 3.
