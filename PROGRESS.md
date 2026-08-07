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
