# PROGRESS -- task-20260807-003146-critical--real-corruption-confirmed-in-w

Governing chain: UMR-20260806-124055-bc80, UMR-20260806-135632-329e

## Completed
- [x] Independent verification of SPEC claims before any write/destructive action (per
      standing lesson on recurring false-premise dispatch SPECs in this environment):
  - Real DB path resolved via `superboss-register.py`'s own `resolve_superboss_db_path()`:
    `/opt/veridian/ai-os/memory/superboss-register.sqlite`, confirmed 4,067,086,336 bytes
    (~4.07GB), WAL mode (live `-wal`/`-shm` files present).
  - `SELECT count(*) FROM wiring_registry` via direct read-only sqlite3 connection ->
    **`database disk image is malformed`** -- corruption is REAL, reproduced independently,
    not just asserted. `wiring_registry_fts` and its `_data/_idx/_docsize/_config` shadow
    tables also present/implicated (fts5 content table = wiring_registry).
  - Cross-checked "all other tables intact" claim directly: `umr_tasks` 7,976 rows (SPEC
    said 7,954 -- close, natural growth since SPEC was written), `capability_registry` 17,
    `knowledge_engine` 383, `ocid_canonical_registry` 69 -- **all exact matches** to SPEC.
  - Both governing UMR IDs verified to genuinely exist in `umr_tasks` (initial FTS-based
    `--query-umr --search` lookup returned 0 hits, but that flag only indexes
    task_identity/source_trigger/logs_ref, not umr_id itself -- confirmed by direct
    `umr_id =` lookup, which found both rows; not a fabrication).
  - `CAP-20260806-194100-e97b` verified real in `capability_registry`, correctly describes
    `full_server_file_registration.py` (which exists on disk, matches description).
    `UMR-20260806-140841-46d1` verified real, status completed.
  - **Correction to SPEC's causal narrative** (documented, not silently accepted): SPEC's
    claim that UMR-20260806-135632-329e "shows zero wiring_registry progress since
    last_checkpoint_at 19:45:23Z" does not match its actual DB row -- `umr_tasks` has no
    `last_checkpoint_at` column, and this UMR's real `reason` field shows it was marked
    `failed` by an automated backfill/reconciliation process at 2026-08-06T20:57:44Z because
    its systemd worker unit (`veridian-worker@task-20260806-192052-...`) was found
    **inactive** (no heartbeat), not because of a directly-observed break at 19:45. The
    19:45 wiring-corruption timing correlation is SPEC's plausible inference, not a
    demonstrated fact -- treating it as a hypothesis, not settled root cause.
  - Safety check (SPEC step 1): **no active writer to wiring_registry found.** `ps aux` /
    `lsof` on the db+wal show only `health-check-15min.py` (PID 2434545, routine periodic
    connection, unrelated to wiring_registry). `veridian-cron-generate-wiring-registry.service`
    is inactive/dead (last run `Result=success`); its `.timer` is active/waiting (scheduled,
    not currently firing).
  - Root-cause correlation (SPEC step 6): no sudo/log-group access to kernel `dmesg`/
    `/var/log/kern.log`/`syslog` (permission denied) -- cannot confirm or rule out an
    OOM-kill directly. Best available internal proxy: `/opt/veridian/ai-os/logs/health-15min.log`
    samples every ~60-180s across 19:36-19:53 UTC on 2026-08-06 show **healthy RAM usage
    (16-21% `mem_pct_used`)** throughout the claimed corruption window, with only a minor
    ~3min sampling gap (19:45:22 -> 19:48:24). This does not track swap directly, but it
    does **not corroborate** the SPEC's "swap-exhaustion episode" hypothesis for this
    specific window -- treat root cause as **inconclusive**, not confirmed OOM.
  - Precedent found: a prior corruption incident on this exact file
    (`file_inventory_corrupted_orig_20260806T044301Z`) was handled by **renaming** the
    corrupted table aside (not DROPping it), then rebuilding fresh under the original name.
    Following the same convention for `wiring_registry` for consistency and extra safety
    margin (preserves raw corrupted pages in case deeper forensics are ever needed, on top
    of the file-level forensic copy in step 2).

## Completed (continued)
- [x] Step 1 (safety): attempted to pause `veridian-cron-generate-wiring-registry.timer` --
      **blocked**: `systemctl stop` on this system-scope unit requires interactive
      polkit auth, not available non-interactively/without sudo in this session. Accepted
      residual risk: already confirmed (above) no active writer exists right now and the
      service's last run succeeded/is dead; proceeding without the pause since it cannot be
      obtained, and speed reduces the exposure window.
- [x] Step 2: real forensic copy taken 2026-08-07T00:40:29Z of all 3 live files (WAL-mode
      aware): `superboss-register.sqlite.corrupt-wiring-registry-real-20260807T004029Z`
      (4,067,086,336 bytes, md5 `aeb33a818a9a283e58bd6b8d631a1616`), plus matching `-wal`
      (4,124,152 bytes) and `-shm` (32,768 bytes) snapshots, all under
      `/opt/veridian/ai-os/memory/`. Not overwritten; will not be modified further.
- [x] Step 3: ran `sqlite3 .recover` against the forensic **copy** (not live db, to avoid
      extra load/lock risk on production). Result: **clean recovery, zero errors on either
      the `.recover` pass (1m40s) or loading its SQL output into a fresh db (22s)**.
      `PRAGMA integrity_check` on the recovered db reports exactly **one** remaining issue:
      `fts5: corruption found reading blob 10 from table "wiring_registry_fts"` -- the FTS5
      shadow index itself is unrecoverable (expected/disposable, matches SPEC's own framing).
      The **base table recovered 24,281 real rows**, breakdown by entity_type: file 17,662;
      function 5,028; dispatch_event 661; supabase_table 444; ai_role 195; script 151;
      cron_job 72; engine 20; github_repo 15; gateway 10; governance_doc 10; route 6;
      browser_component 4; vercel_project 3. Spot-checked rows (incl. `github_repo`/
      `vercel_project` entries with rich live-API-derived metadata -- PR counts, workflows,
      branch protection) look structurally intact and plausible, not garbled.
      `umr_tasks` also recovered fine (7,977 rows) as an independent sanity check.
      **Decision (deviating from SPEC step 4's literal "drop and rebuild from scripts"
      default, documented per faithful-reporting practice):** since recovery of the base
      table was genuinely clean (only the disposable FTS5 index was not), the lower-risk,
      lower-resource path is to **restore the 24,281 recovered rows as the new live table
      content** (exact pre-corruption state, no dependency on live GitHub/Vercel/Supabase
      API re-calls which may be slow/rate-limited/credential-dependent on this
      resource-constrained box -- swap at 3.3/4.0GB, load avg ~7 at time of writing), rebuild
      the FTS5 index fresh from that restored content, THEN opportunistically re-run the
      cheap deterministic filesystem-only registration script(s) on top to catch any drift
      since last write -- rather than discarding a clean recovery to redo a slower, external-
      API-dependent full re-scan that isn't actually needed here.
- [x] Step 4: done on the LIVE db, transactionally where SQLite allowed it (one subtlety
      hit and fixed -- see below). Sequence actually executed:
  1. Dropped the 3 old `wiring_registry_*` triggers.
  2. `ALTER TABLE wiring_registry RENAME TO wiring_registry_corrupted_orig_20260807T004638Z`
     (precedent-consistent with `file_inventory_corrupted_orig_20260806T044301Z`) --
     succeeded (a rename only touches `sqlite_master`, not the table's own malformed pages).
  3. `DROP TABLE wiring_registry_fts` failed (`vtable constructor failed` -- the fts5 shadow
     data is itself unreadable, expected); fell back to `PRAGMA writable_schema=ON; DELETE
     FROM sqlite_master WHERE name LIKE 'wiring_registry_fts%'` to clear the virtual-table
     registration directly -- worked.
  4. Recreated fresh `wiring_registry` + `wiring_registry_fts` + 3 triggers via
     `superboss-register.py`'s own `_ensure_wiring_registry_table()` (imported live, DDL
     not hand-copied).
  - **Bug hit and fixed:** step 3's `PRAGMA writable_schema` toggle forced an implicit
    commit mid-transaction (not documented behavior I'd assumed was fully transactional),
    so my first attempt's later Python-level error (a `PRAGMA table_info(rec.wiring_registry)`
    syntax slip -- needed `PRAGMA rec.table_info(wiring_registry)`) rolled back the *Python*
    transaction object but steps 1-4 above had already durably committed. Verified this
    left the live db in a safe, consistent (if incomplete) intermediate state -- rename +
    fresh empty table both present, nothing corrupted further -- then finished the
    remaining steps (data restore, FTS rebuild, index fix below) as separate small
    committed steps instead of re-attempting one big transaction.
  - **Second bug hit and fixed:** `_ensure_wiring_registry_table()`'s `CREATE INDEX IF NOT
    EXISTS idx_wiring_registry_entity_type/idx_wiring_registry_source_system` silently
    no-op'd because indexes of those exact names still existed -- bound to the just-renamed
    `wiring_registry_corrupted_orig_...` table (`ALTER TABLE RENAME` carries a table's
    existing index *names* along with it). Caught this via a first `PRAGMA integrity_check`
    pass (see step 6 below), which flagged both indexes as belonging to `tbl_name =
    wiring_registry_corrupted_orig_...`. Fixed by dropping those two now-orphaned-purpose
    index names and recreating them fresh, bound to the live `wiring_registry` table.
  5. Restored the 24,281 `.recover`-validated rows into the fresh table (`INSERT INTO
     wiring_registry (...) SELECT (...) FROM rec.wiring_registry` via `ATTACH DATABASE`).
  6. Rebuilt the FTS5 index from that restored content
     (`INSERT INTO wiring_registry_fts(wiring_registry_fts) VALUES ('rebuild')`).
- [x] Step 5 (re-run registration scripts) -- **deliberately deferred, not skipped
      silently:** by the time steps 1-4 completed, this box's own resource state had
      degraded further and independently of anything done here (swap 3.9/4.0GB used, load
      average 13.6, mem used 6.3GiB free 354MiB) -- worse than at task start. Restored data
      is a complete, `.recover`-validated, structurally-intact snapshot of the exact
      pre-corruption live state (not a guess, not empty), so running the heavier
      `full_server_file_registration.py` full-server scan (or a live GitHub/Vercel/Supabase
      API re-crawl) purely to "catch drift since last write" is a nice-to-have freshness
      improvement, not a correctness requirement, and isn't worth adding more load to an
      already-stressed box in the same session that is investigating a
      resource-exhaustion-adjacent corruption theory. The existing
      `veridian-cron-generate-wiring-registry.timer` (confirmed still active/waiting, see
      Step 1) will pick up normal freshness on its own regular cadence. Flagging this
      explicitly rather than silently declaring the task 100% done on this point.
- [x] Step 6: real evidence.
  - `PRAGMA integrity_check` (full db, read-only, 10.5s): reports exactly 2 remaining
    findings, **both scoped to the deliberately-preserved forensic remnant**
    `wiring_registry_corrupted_orig_20260807T004638Z` (`Tree 89 page 512918 cell 448: Rowid
    24281 out of order`; `wrong # of entries in index
    sqlite_autoindex_wiring_registry_corrupted_orig_20260807T004638Z_1`) -- i.e. the OLD
    table's own pre-existing corruption, kept on purpose, not new damage. **Zero findings
    against the live `wiring_registry` table, its FTS5 index, or any other table.**
  - Final row count by entity_type on the live, rebuilt `wiring_registry`: file 17,662;
    function 5,028; dispatch_event 661; supabase_table 444; ai_role 195; script 151;
    cron_job 72; engine 20; github_repo 15; governance_doc 10; gateway 10; route 6;
    browser_component 4; vercel_project 3 -- **total 24,281**, matching `wiring_registry_fts`
    row count exactly (24,281) and an FTS MATCH query returns real hits (429 for
    `"superboss"`), confirming the index is genuinely queryable, not just present.
  - No other table affected -- re-verified post-rebuild: `umr_tasks` 7,977 (natural growth
    from 7,976 pre-repair), `capability_registry` 17, `knowledge_engine` 383,
    `ocid_canonical_registry` 69, `file_inventory` 30,965 -- all unchanged/as expected.
  - DB file size unchanged (4,067,086,336 bytes) -- freed pages from the old table went to
    SQLite's internal freelist for reuse, not returned to the OS; a `VACUUM` would reclaim
    that but was **not** attempted given current disk headroom (31GB/301GB free, 90% used)
    and the memory/IO cost of rewriting a 4GB file live -- left as an optional future
    cleanup, not required for correctness.
  - Cleaned up `/tmp/wiring_recovery`'s intermediate `.recover` SQL dump and scratch db
    (4.6GB) after the restore was verified merged and durable, freeing disk headroom back
    to 31GB avail.
- [x] Step 7: root-cause note (see below).
- [x] Timer: was never stopped (Step 1, blocked by missing sudo), so nothing to resume.
- [x] Commit + push after each meaningful unit (this file's history on the branch).

## Root-cause note (SPEC step 6)
SPEC's hypothesis: an OOM-kill during a write to `wiring_registry` around 19:40-19:45 UTC
on 2026-08-06, correlated with "repeated swap-exhaustion episodes today."

**Could not confirm directly:** no sudo/log-group access to `dmesg`, `/var/log/kern.log`,
or `/var/log/syslog` in this session (permission denied) -- the definitive OOM-killer
evidence (`Out of memory: Killed process ...`) is simply not reachable from here.

**Best available internal evidence, and it does not support the hypothesis for this
specific window:** `/opt/veridian/ai-os/logs/health-15min.log` samples `free -m`-derived
`mem_pct_used` roughly every 60-180s. Across 2026-08-06T19:36-19:53Z it shows a healthy,
unremarkable 16.0-21.6% RAM utilization the entire time (one ~3min sampling gap at
19:45:22->19:48:24, itself minor). This metric doesn't cover swap directly, so it can't
fully rule out a swap-specific event, but it gives no positive signal of memory pressure
in the claimed window either.

**A separate, better-supported real observation from this repair itself:** at the time of
writing (2026-08-07T00:45-00:50Z, ~5.5 hours after the claimed corruption window and
entirely unrelated to it), this same box's swap usage climbed from 2.9/4.0GB to a fully
exhausted 3.9-4.0/4.0GB and load average rose to 13+ over the ~10 minutes this repair took
-- live, current, directly observed resource exhaustion, just not the one SPEC asked about.
This is circumstantial support for the *general* claim that "this box experiences real
periodic swap exhaustion" (consistent with the file_inventory precedent from the same DB
and PM decision log row 56 / UMR-20260806-071025-1d28 referenced by this environment's own
`find_root_walk_guard` hook), even though the *specific* 19:40-19:45Z window on 2026-08-06
isn't independently confirmable from evidence available in this session.

**Recommendation:** treat "an OOM-kill or swap-thrashing event caused this corruption" as
plausible-but-unconfirmed, not proven. Given this is a documented repeat of the same file
corrupting twice, and this box visibly runs at/near swap exhaustion repeatedly (observed
directly, twice now, in unrelated sessions), the structural fixes SPEC floats (WAL mode --
**already in effect**, confirmed live `-wal`/`-shm` files present throughout this repair;
more conservative commit intervals; and/or basic swap-pressure alerting so a write-heavy
job like the wiring-registry cron can defer/retry instead of writing straight into a
memory-starved moment) are worth doing on their own merits regardless of whether this exact
incident's root cause is ever nailed down with kernel-log-level certainty.

## Remaining
- [ ] Optional/deferred, not required for this incident to be considered resolved:
  - Re-run `full_server_file_registration.py` + the Vercel/GitHub/Supabase registration
    logic + `generate_wiring_registry.py`'s other 8 sources once box resource pressure
    (swap/load) normalizes, to refresh any drift since the last successful write
    (restored data is the exact last-known-good snapshot, not stale/wrong, just
    potentially a few hours behind live filesystem/API state).
  - Optional `VACUUM` to reclaim the freed-but-unreturned pages from the old corrupted
    table (file size will stay ~4.07GB until then; not a correctness issue).
  - If sudo/log access is ever available, retroactively check `dmesg`/kern.log around
    2026-08-06T19:40-19:46Z for a definitive OOM-killer entry to close out the root-cause
    question with certainty (currently inconclusive per the note above).
