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

## Remaining

- [ ] Step 4 (verification -- integrity_check + row counts; STOP here if
      unexplained mismatch)
- [ ] Step 5 (final pre-swap backup, only if Step 4 passes cleanly)
- [ ] Step 6 (atomic swap under `_write_lock()` + post-swap re-verify)
