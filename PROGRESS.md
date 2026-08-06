# PROGRESS -- task-20260806-042813-corruption-recovery--fresh-clean-resume

Real Owner/PM approval executing: PM decision UMR-20260806-042322-994b, approving
owner-proposal id=5 (pm_decisions_pending), child UMR UMR-20260806-042004-e22f.
Relates to original 6-step recovery authorization UMR-20260806-025638-cbea and
resume authorization UMR-20260806-040944-704c. Target:
/opt/veridian/ai-os/memory/superboss-register.sqlite, corruption isolated to
`file_inventory` table (Hard Rule 8).

## Independent verification performed before any write (per standing practice
of not trusting SPEC dispatch narratives at face value)

**Confirmed TRUE:**
- Corruption really is isolated to exactly `file_inventory` (sqlite_master
  rootpage 38) + its unique autoindex `sqlite_autoindex_file_inventory_1`
  (rootpage 39). Full `PRAGMA integrity_check` on the live 1.6GB DB only
  reports `Tree 38`/`Tree 39` `btreeInitPage() returns error code 11` errors
  and `wrong # of entries in index sqlite_autoindex_file_inventory_1` --
  nothing else, confirmed via `SELECT type,name,rootpage FROM sqlite_master
  WHERE rootpage IN (38,39)` returning exactly `file_inventory` /
  `sqlite_autoindex_file_inventory_1`.
- `/home/rajat/.local/bin/sqlite3` is real: v3.53.4 (2026-07-24), `pragma
  compile_options` includes `ENABLE_DBPAGE_VTAB`.
- `_write_lock()` is real, at `superboss-register.py:193` (flock-based,
  `_WRITE_LOCK_PATH = DB_PATH + ".writelock"`, auto-released on kill).
- `pm_decisions_pending` id=5 and the referenced UMR chain are real rows in
  the live DB.
- The cited stale artifacts are real: `superboss-register.sqlite.20260806-pre-recover.bak`
  (+`-wal`) and `.bak-pre-file_inventory-recover-20260806T025938Z`, both
  ~02:57-02:59 UTC today, confirmed via `ls -l`.

**Confirmed FALSE (material, but does not change the corrective plan):**
pm_decisions_pending id=5's narrative claims prior worker
`task-20260806-041150` "self-checkpointed to status=pending_review... ONLY
real change is a 141-line PROGRESS.md trim... zero real .recover/verify/swap
commands were ever run, no PR was opened, no recovery artifacts exist." This
is false. Independently pulled that task's real `result.json`, `review.json`,
and `pr_url.txt`:
- It opened a real PR (github.com/FChecklist/veridian-scripts/pull/113),
  reviewed and correctly rejected -- but for a documentation gap (findings
  not written into PROGRESS.md), not "no work happened."
- Its `result.json` shows real independent verification work: it discovered
  a predecessor task `task-20260806-030104` had already run a real
  `.recover` producing `superboss-register.sqlite.recovered-20260806T025938Z`
  (real file on disk, 1,571,598,336 bytes, matches `ls -l`), with
  `file_inventory` genuinely recovered (27,249 rows) and `PRAGMA
  integrity_check` -> `ok`.
- Step 4 then correctly failed there: live `umr_tasks` had drifted from the
  02:59Z snapshot (179+ rows and still climbing at check time). This is the
  **same single attempt** referenced elsewhere as "6832 recovered vs 6854
  live" -- one recovered copy, checked at different moments as drift widened
  (6854 at original check, 7011 by the time 041150 re-checked).
- `review.json`'s own verdict confirms real substantive work occurred and no
  irreversible action was taken against production -- the defect was
  record-keeping, not absence of work.

This is the third time an urgent PM SPEC dispatch in this environment has
been found to mischaracterize live/prior state on independent check (see
standing note). Proceeding anyway because the SPEC's own prescribed fix --
fresh Step 1/2 snapshots taken immediately before Step 3, instead of reusing
the ~02:59Z copy -- is exactly the option (a) the 041150 task itself
recommended to the Owner, and stands on its own merits regardless of the
false framing. This makes the current run **attempt #2** of the
snapshot->recover->verify approach (attempt #1 = the 02:59Z chain that
failed at Step 4 above). Per protocol: if this attempt also fails
unexplainably at Step 4, STOP for good, no third attempt.

Baseline read-only counts taken 2026-08-06T04:38:05Z, immediately before
Step 1 (for later drift comparison):
```
ocid_canonical_registry        69
gtm_certification_categories   25
umr_tasks                      7056
pm_decisions_pending           4
capability_registry            11
```

## Completed

- [x] Step 1 (fresh backup) -- 2026-08-06T04:38:18Z
  ```
  $ cp /opt/veridian/ai-os/memory/superboss-register.sqlite \
       /opt/veridian/backups/sqlite-daily/superboss-register.sqlite.20260806T043818Z-pre-file_inventory-recover-fresh.bak

  $ ls -l /opt/veridian/ai-os/memory/superboss-register.sqlite
  -rw-r--r-- 1 rajat rajat 1638092800 Aug  6 04:38 /opt/veridian/ai-os/memory/superboss-register.sqlite

  $ ls -l /opt/veridian/backups/sqlite-daily/superboss-register.sqlite.20260806T043818Z-pre-file_inventory-recover-fresh.bak
  -rw-r--r-- 1 rajat rajat 1638092800 Aug  6 04:38 /opt/veridian/backups/sqlite-daily/superboss-register.sqlite.20260806T043818Z-pre-file_inventory-recover-fresh.bak

  $ sha256sum /opt/veridian/ai-os/memory/superboss-register.sqlite
  2b5cb2824682eb1136bb0fe926ee71e6d925b493d33ed6483833d260c1688f5c  /opt/veridian/ai-os/memory/superboss-register.sqlite

  $ sha256sum /opt/veridian/backups/sqlite-daily/superboss-register.sqlite.20260806T043818Z-pre-file_inventory-recover-fresh.bak
  2b5cb2824682eb1136bb0fe926ee71e6d925b493d33ed6483833d260c1688f5c  /opt/veridian/backups/sqlite-daily/superboss-register.sqlite.20260806T043818Z-pre-file_inventory-recover-fresh.bak
  ```
  Checksums match -- clean copy confirmed.

- [x] Step 2 (separate working copy) -- 2026-08-06T04:39Z
  Working copy placed at `/tmp/veridian-recovery-work/` (outside the live
  path, outside the backups dir, and outside the git workspace itself to
  avoid ever accidentally committing a 1.6GB binary artifact).
  ```
  $ cp /opt/veridian/backups/sqlite-daily/superboss-register.sqlite.20260806T043818Z-pre-file_inventory-recover-fresh.bak \
       /tmp/veridian-recovery-work/superboss-register.sqlite.working-copy-fresh.sqlite

  $ ls -l /opt/veridian/backups/sqlite-daily/superboss-register.sqlite.20260806T043818Z-pre-file_inventory-recover-fresh.bak
  -rw-r--r-- 1 rajat rajat 1638092800 Aug  6 04:38 ...pre-file_inventory-recover-fresh.bak

  $ ls -l /tmp/veridian-recovery-work/superboss-register.sqlite.working-copy-fresh.sqlite
  -rw-r--r-- 1 rajat rajat 1638092800 Aug  6 04:39 .../superboss-register.sqlite.working-copy-fresh.sqlite

  $ sha256sum /opt/veridian/backups/sqlite-daily/superboss-register.sqlite.20260806T043818Z-pre-file_inventory-recover-fresh.bak
  2b5cb2824682eb1136bb0fe926ee71e6d925b493d33ed6483833d260c1688f5c  ...pre-file_inventory-recover-fresh.bak

  $ sha256sum /tmp/veridian-recovery-work/superboss-register.sqlite.working-copy-fresh.sqlite
  2b5cb2824682eb1136bb0fe926ee71e6d925b493d33ed6483833d260c1688f5c  .../working-copy-fresh.sqlite
  ```
  Checksums match Step 1's backup exactly.

- [x] Step 3 (.recover to new file) -- 2026-08-06T04:40:25Z - 04:42:01Z
  ```
  $ /home/rajat/.local/bin/sqlite3 /tmp/veridian-recovery-work/superboss-register.sqlite.working-copy-fresh.sqlite ".recover" \
      > /tmp/veridian-recovery-work/recovered-fresh.sql 2> /tmp/veridian-recovery-work/recovered-fresh.sql.err
  start: 2026-08-06T04:40:25Z
  end:   2026-08-06T04:41:34Z
  exit code: 0
  stderr: (empty)

  $ wc -l /tmp/veridian-recovery-work/recovered-fresh.sql
  179180 /tmp/veridian-recovery-work/recovered-fresh.sql

  $ /home/rajat/.local/bin/sqlite3 /tmp/veridian-recovery-work/recovered-fresh.sqlite \
      < /tmp/veridian-recovery-work/recovered-fresh.sql
  build exit code: 0
  end: 2026-08-06T04:42:01Z

  $ ls -l /tmp/veridian-recovery-work/recovered-fresh.sqlite
  -rw-r--r-- 1 rajat rajat 1637773312 Aug  6 04:42 recovered-fresh.sqlite
  ```
  Recovery emitted 179,180 lines of SQL and built cleanly; final recovered
  file is 1,637,773,312 bytes (Step 2 working copy was 1,638,092,800 bytes --
  expected small delta, `.recover` does not preserve exact page layout/free
  space). Working copy left untouched at
  `/tmp/veridian-recovery-work/superboss-register.sqlite.working-copy-fresh.sqlite`.
  Total elapsed for Steps 1-3: ~4 minutes (04:38:18Z-04:42:01Z), far tighter
  than the drift window that caused attempt #1's Step 4 failure.

- [x] Step 4 (verification) -- 2026-08-06T04:42:42Z -- PASSED
  ```
  $ /home/rajat/.local/bin/sqlite3 /tmp/veridian-recovery-work/recovered-fresh.sqlite "PRAGMA integrity_check;"
  ok

  $ # recovered-fresh.sqlite counts
  ocid_canonical_registry        69
  gtm_certification_categories   25
  umr_tasks                      7056
  pm_decisions_pending           4
  capability_registry            11
  file_inventory                 27249

  $ # live counts, fresh read at 2026-08-06T04:42:42Z
  ocid_canonical_registry        69
  gtm_certification_categories   25
  umr_tasks                      7057
  pm_decisions_pending           4
  capability_registry            11
  file_inventory                 -- Error: database disk image is malformed (expected; this is the table being recovered)
  ```
  `ocid_canonical_registry`, `gtm_certification_categories`,
  `pm_decisions_pending`, `capability_registry`: exact match.
  `umr_tasks`: recovered 7056 vs live 7057, off by exactly 1. Identified the
  specific real row responsible:
  ```
  $ sqlite3 live "SELECT umr_id, task_identity, ts_submitted, status, source_trigger FROM umr_tasks
                   WHERE ts_submitted > '2026-08-06T04:38:18' OR last_heartbeat > '2026-08-06T04:38:18';"
  umr_id:         UMR-20260806-043900-8c48
  task_identity:  owner-task-20260806-043858-1384521
  ts_submitted:   2026-08-06T04:39:00.922560+00:00
  status:         queued
  source_trigger: owner_dispatch_gateway
  ```
  This UMR was submitted 42s after the Step 1 backup (04:38:18Z) and well
  before this Step 4 read (04:42:42Z) -- a real write landing squarely
  inside the unavoidable snapshot-to-verify window, and it is the only row
  in that window. Exactly accounts for the +1 drift; no other table shows
  any mismatch. `file_inventory` recovered row count (27,249) matches the
  count independently reported by the earlier task-030104/041150 recovery
  attempt, corroborating this recovery is consistent and correct.

  **Step 4 verdict: PASS.** Proceeding to Step 5.

- [x] Step 5 (final pre-swap backup) -- 2026-08-06T04:43:25Z
  ```
  $ cp /opt/veridian/ai-os/memory/superboss-register.sqlite \
       /opt/veridian/backups/sqlite-daily/superboss-register.sqlite.20260806T044325Z-pre-swap-fresh.bak

  $ ls -l /opt/veridian/ai-os/memory/superboss-register.sqlite
  -rw-r--r-- 1 rajat rajat 1661845504 Aug  6 04:39 superboss-register.sqlite

  $ ls -l /opt/veridian/backups/sqlite-daily/superboss-register.sqlite.20260806T044325Z-pre-swap-fresh.bak
  -rw-r--r-- 1 rajat rajat 1661845504 Aug  6 04:43 ...pre-swap-fresh.bak

  $ sha256sum /opt/veridian/ai-os/memory/superboss-register.sqlite
  a3acf8c54d829caafe98210821aeed1a4005b8f1ad98b05bdb398d9cbf9b300f  superboss-register.sqlite

  $ sha256sum /opt/veridian/backups/sqlite-daily/superboss-register.sqlite.20260806T044325Z-pre-swap-fresh.bak
  a3acf8c54d829caafe98210821aeed1a4005b8f1ad98b05bdb398d9cbf9b300f  ...pre-swap-fresh.bak
  ```
  Checksums match. Note live file grew from 1,638,092,800 bytes (Step 1) to
  1,661,845,504 bytes here (~23MB in ~5 min), consistent with the
  confirmed real ongoing write activity noted throughout.

## STOPPED before Step 6 -- do not proceed, do not self-report success

Immediately before attempting the atomic swap, `ls -la` on the live memory
directory turned up a file I never created:
`superboss-register.sqlite.bak-pre-file_inventory-live-repair-20260806T044301Z`
(timestamp 2026-08-06T04:43:04Z). That is exactly the kind of "mismatch you
cannot fully explain" this task's protocol requires stopping for, so Step 6
was not attempted. Investigated instead of proceeding:

- Found a second, concurrent task directory,
  `task-20260806-042805-pm-approves-proposal-5--fresh-clean-corr`
  (`created_at: 2026-08-06T04:28:06Z`, i.e. dispatched *before* this task),
  independently working the exact same `pm_decisions_pending` id=5 /
  UMR-20260806-042004-e22f approval.
- Its `task.yaml`/`PROGRESS.md` show it completed all 6 real steps and
  `record-owner-proposal-completion` at 2026-08-06T04:45:39Z, via
  https://github.com/FChecklist/veridian-scripts/pull/118 (commit
  `f80da2c7ee2b7e99954f2b46f2105ef5a9034584`) -- **using a materially
  different, better repair strategy than this SPEC's Step 6**: an in-place,
  single-table rename-swap (`CREATE file_inventory_new` + `INSERT ...
  SELECT` from its own recovered artifact + `ALTER TABLE ... RENAME` to
  quarantine the corrupted original + rename the new table into place),
  rather than a full-file swap. It explicitly rejected the full-file-swap
  approach this SPEC specifies, reasoning that live had kept accepting
  real writes across all ~90 tables since the original snapshot and a full
  swap would silently roll all of that back to fix one table.
- Independently re-verified all of this directly against the live DB just
  now, not just trusting that task's self-report:
  ```
  $ sqlite3 live "SELECT COUNT(*) FROM file_inventory;"
  27249

  $ sqlite3 live "SELECT type,name,rootpage FROM sqlite_master WHERE name LIKE 'file_inventory%';"
  table|file_inventory_corrupted_orig_20260806T044301Z|38
  table|file_inventory|405938

  $ sqlite3 live "SELECT id,status,closed_by,closed_ts FROM pm_decisions_pending WHERE id=5;"
  5|completed|PM|2026-08-06T04:24:11.871033+00:00

  $ sqlite3 live "SELECT COUNT(*) FROM umr_tasks;"
  7078

  $ gh pr view 118 --repo FChecklist/veridian-scripts --json commits
  -- real commit, authored 2026-08-06T04:42:49Z
  ```
  `file_inventory` really is readable and correct on live now; the old
  corrupted tree really is quarantined (harmless, matches this repo's
  existing `*.CORRUPTED-*` convention of preserving rather than deleting);
  `pm_decisions_pending` id=5 really is `status=completed`; `umr_tasks`
  really is at 7078, consistent with continued organic growth, not a
  rollback. **The corruption recovery this task was dispatched to perform
  is already done, correctly, by someone else.**

**Why I did not proceed to Step 6 anyway:** this SPEC's Step 6 is a
full-file `rename` of `recovered-fresh.sqlite` (a snapshot frozen at
2026-08-06T04:38:18Z, Step 1 of *this* run) directly over the live path.
Doing that now would silently destroy the already-completed, already
independently-verified repair described above, and roll back every one of
the ~90 tables in the live DB to the 04:38:18Z snapshot -- discarding all
real writes made since, including the very
`record-owner-proposal-completion` record that closed this same proposal at
04:45:39Z. That is a real, severe, irreversible data-loss action against
production. Not attempted.

This task's own artifacts (Steps 1-5: fresh backup, working copy,
`recovered-fresh.sqlite`, second pre-swap backup) never touched the live
file -- everything through Step 5 was copies/reads only. The two backup
files this task created under `/opt/veridian/backups/sqlite-daily/`
(`*20260806T043818Z-pre-file_inventory-recover-fresh.bak`,
`*20260806T044325Z-pre-swap-fresh.bak`) are left in place as harmless
historical record, matching this project's existing backup-retention
convention. Scratch working files under `/tmp/veridian-recovery-work/`
(the working copy, recover SQL, and `recovered-fresh.sqlite`) have been
deleted -- they were superseded the moment the concurrent task's repair
landed, and disk on `/` was at 94-96% full.

**Status: STOPPED, not pending_review.** Steps 1-5 were performed for
real (see logs above) but are now moot -- the underlying corruption they
were built to fix is already fixed, by a concurrent task, via a better
approach. Not calling `record-owner-proposal-completion` (already called,
correctly, by the other task). No further action taken by this task.
