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

## Remaining
- [ ] Step 1 (safety): pause `veridian-cron-generate-wiring-registry.timer` for the duration
      of the repair (reversible), confirm no writer attaches mid-repair.
- [ ] Step 2: real forensic copy of the live file (main + `-wal` + `-shm`, WAL-mode aware) to
      `superboss-register.sqlite.corrupt-wiring-registry-real-<UTC timestamp>`.
- [ ] Step 3: attempt real recovery via `sqlite3 .recover` against the **copy** (not live db,
      to avoid extra load/lock risk on production while other processes use it) -- report
      real recovered row count for wiring_registry if any.
- [ ] Step 4: rename corrupted `wiring_registry` table aside (precedent-consistent), drop
      corrupted `wiring_registry_fts` + shadow tables, recreate fresh schema via
      `superboss-register.py`'s own `_ensure_wiring_registry_table()` (single source of
      truth for the DDL -- reuse, don't reimplement).
- [ ] Step 5: re-run `full_server_file_registration.py` (CAP-20260806-194100-e97b),
      the Vercel/GitHub/Supabase registration logic from UMR-20260806-140841-46d1, and
      `generate_wiring_registry.py` for the other 8 sources -- reusing existing
      content_hash dedup logic.
- [ ] Step 6: real evidence -- `PRAGMA integrity_check` clean, final row count by
      entity_type, confirm no other table affected (re-verify row counts post-rebuild).
- [ ] Step 7: finalize root-cause note for the record (documented above; will restate in
      final summary).
- [ ] Resume normal timer schedule; commit + push.
