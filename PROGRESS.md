# PROGRESS -- task-20260805-172722-urgent--real-database-lock-contention-bl

Related: UMR-20260805-121654-4b77 (blocked corruption-fix investigation)
Target DB: /opt/veridian/ai-os/memory/superboss-register.sqlite (1.4GB, WAL mode)

## Completed
- [x] Checked for processes holding the DB file open (`fuser -v`, `lsof`): only
      one process, PID 3095615 (`/opt/veridian/scripts/health-check-15min.py`,
      the legitimate 15-min periodic health-check job, started 17:19:41,
      state `S` sleeping/blocked in `do_poll`, i.e. idle, not stuck).
- [x] Checked actual byte-range locks with `lslocks` (authoritative for
      SQLite's real lock state, unlike `fuser`'s coarse open-fd view): PID
      3095615 holds only **POSIX READ locks** on the db file and `-shm` file
      (normal WAL-mode reader-mark locks) plus its own `flock` on
      `.health-check-15min.lock` (its private run-lock, unrelated to the DB).
      **No exclusive/write lock held by anyone.**
- [x] Investigated `superboss-register.sqlite.writelock`: read the live
      `_write_lock()` implementation in `/opt/veridian/scripts/superboss-register.py`
      (lines 172-203). It's an `fcntl.flock` advisory lock acquired *before*
      opening any write connection, added 2026-07-23 specifically so a killed
      waiter can never hold the DB mid-transaction -- flock is released
      automatically by the kernel if the holder dies, so it cannot leave an
      orphaned/stuck lock. Confirmed via `lslocks` no process currently holds
      this flock either.
- [x] Root-caused the 2026-07-23 corruption incidents referenced in that code
      comment: caused by an *outer* caller (`veridian-task.py`'s
      `_log_to_register()`) SIGKILLing a child after only a 10s wait while it
      was still blocked acquiring SQLite's internal write lock -- a kill
      landing mid-transaction/mid-WAL-checkpoint left torn b-tree pages. This
      is exactly the failure mode the `.writelock` flock (see above) was
      built to make impossible, and it predates/is unrelated to today's
      report.
- [x] Verified: no stuck or orphaned transaction/process was found. Nothing
      was force-killed (none needed -- no process was in fact holding a
      blocking lock at time of check).
- [x] Ran the actual blocked query for real:
      `SELECT umr_id, status, tier, ts_submitted, ts_completed FROM umr_tasks
      WHERE umr_id = 'UMR-20260805-121654-4b77';`
      Result: returned in **9ms** (`status=running`, `tier=1`,
      `ts_submitted=2026-08-05T12:16:54`, `ts_completed` empty). Re-ran a
      second time (`SELECT COUNT(*) FROM umr_tasks` = 5618 rows) in 8ms.
      Confirms the query is unblocked and the corruption investigation can
      proceed.
- [x] Noted longer-term finding (not implemented, per instruction): see below.

## Remaining
- [ ] None for this task -- investigation complete, query confirmed
      unblocked. Longer-term DB-health note handed off to PM/owner (see
      report), not actioned here.

## Findings summary (for PM)

**No stuck lock was found or cleared.** By the time this was investigated,
`superboss-register.sqlite` had zero exclusive/write locks held by any
process (verified with `lslocks`, which reads real POSIX/flock state, not
just open-fd lists). The only process touching the file was the legitimate
15-min health-check job holding ordinary WAL read locks. The `.writelock`
mechanism that guards writers was specifically engineered on 2026-07-23 to be
unable to strand a lock if its holder is killed (kernel auto-releases
`flock`), so "leftover write transaction from an earlier run" is not
mechanically possible with the current code path.

Direct proof: the actual PK-lookup query from the corruption investigation,
run for real against the live DB, returned in 9ms -- not 100+ seconds. The
reported 1m40s+ hang was real but was **transient contention**, not a
persistent stuck lock: most likely a momentary writer holding SQLite's
internal write lock under the existing 30s `busy_timeout` (already set in
`superboss-register.py`'s `_connect()`), compounded by this being a 1.4GB
file with a 13MB WAL under concurrent access from multiple simultaneous
tasks this session. It had cleared on its own by the time of this check.

**Longer-term finding, noted per instruction, not implemented:** the DB is
already in WAL mode and the primary write-path script
(`superboss-register.py`) already sets `busy_timeout=30000`, so those two
specific mitigations exist at the application level already. However, at
1.4GB+ and growing, under heavy concurrent access from many simultaneous
tasks this session, other/ad-hoc readers (e.g. plain `sqlite3` CLI calls,
which default to `busy_timeout=0`) can still hit instant `SQLITE_BUSY` or
contend for the write lock without backing off. Recommend auditing all
callers (not just `superboss-register.py`) to consistently set a
`busy_timeout`, and considering `PRAGMA wal_autocheckpoint` tuning /
periodic `wal_checkpoint(TRUNCATE)` given the 13MB WAL, as a longer-term
follow-up. Not implemented now, per instruction.
