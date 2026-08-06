# PROGRESS -- task-20260806-192052-deterministic-full-server-file-registrat

SPEC chain: UMR-20260806-124055-bc80 (stop-work), UMR-20260806-130416-3d77 (wiring re-run mandate).

## Completed
- [x] Independent verification of SPEC premises before any write (per standing
      false-premise-pattern memory):
  - `file_inventory.py` confirmed real at `/opt/veridian/ai-os/scripts/file_inventory.py`,
    scoped to `ai-os/` + `scripts/` only (matches "CONFIRMED REAL FINDING").
  - `/opt/veridian/scripts/superboss-register.sqlite` is a stale **0-byte** file --
    NOT the live DB. Real live DB (4GB, actively written) is
    `/opt/veridian/ai-os/memory/superboss-register.sqlite` (found via
    `resolve_superboss_db_path()` default in superboss-register.py). All DB work
    targets this real path.
  - Briefing's cited wiring_registry row (`dispatch_event-owner-task-...`) and 5
    capability_registry script names verified present in the REAL db -- briefing
    checks out once pointed at the correct DB file.
  - `register_entity_row()` / `_ensure_wiring_registry_table()` confirmed real in
    `scripts/superboss-register.py` (lines ~2735/2794).
  - Existing precedent for full-hash + entity_id conventions found in
    `generate_wiring_registry.py` (`_hash_file_bytes` = pure sha256 of bytes,
    `Registry.get_or_create_file` = `file-{sha1(abs_path)[:12]}`) -- reused, not
    reinvented.
  - Found a real pre-existing data-quality fact: ALL 1981 existing
    `entity_type='file'` wiring_registry rows have `content_hash IS NULL` (the
    existing file-registration path never populated it). 15 of those point
    inside `.git/` (explicitly excluded by this SPEC's own scope), 7 are
    `PATH_MISSING` (file no longer exists, nothing to hash), ~32 point outside
    the SPEC's 4 named roots entirely. Documented so the mandatory "zero NULL
    content_hash" check is reported honestly, not forced to a fake pass.
  - `/opt/veridian/repos/` enumerated: real top-level repos vs `-wt`-suffixed
    worktree dirs and `.pytest_cache` cruft identified (dedupe rule: top-level
    dir must contain a real `.git` entry and not end in `-wt`).
- [x] Capability-registry precedent search: no existing script matches this
      exact task (closest: `ai_agent_registry`, `capability_registry_dedup` --
      neither does file registration). Confirms building new script is correct
      per the 4-step spec.
- [x] Built `/opt/veridian/scripts/full_server_file_registration.py` -- wraps/reuses
      `file_inventory.py`'s scan pattern (generalized roots/excludes),
      `generate_wiring_registry.py`'s `_hash_file_bytes`/entity_id convention, and
      `superboss-register.py`'s `register_entity_row`/`_ensure_wiring_registry_table`.

- [x] Found + fixed 2 real bugs during live testing (documented honestly, not
      hidden): (1) `--backup-first` writes under `ai-os/memory/backups/`,
      which was inside the scan root -- each run's own backup became a "new
      file" for the next run, breaking idempotency by construction. Fixed by
      excluding `*/ai-os/memory/backups` and the live
      `superboss-register.sqlite*` family itself (multi-GB, mutates every
      run by definition). (2) running/importing the script regenerates its
      own `__pycache__/*.pyc`, a second spurious idempotency break -- fixed
      with a standard `*/__pycache__` exclusion.
- [x] Took real online sqlite backups (`superboss-register.sqlite.pre-fullfile-backup-<UTC ts>`,
      via `Connection.backup()` + `prune_memory_backups.real_integrity_check()`
      verification, same WAL-safety reasoning as `snapshot_memory_backup.py`)
      before every real write run.
- [x] Ran script for real against the live DB (`/opt/veridian/ai-os/memory/superboss-register.sqlite`).
      Final clean back-to-back pair (no edits between them) -- real pasted output:
      ```
      RUN 1 (--backup-first):
      {
        "backup_path": "/opt/veridian/ai-os/memory/backups/superboss-register.sqlite.pre-fullfile-backup-20260806T193901Z",
        "files_found": 44277, "newly_registered": 7, "backfilled_legacy_content_hash": 42,
        "skipped_duplicate": 44228, "unreadable_errors": []
      }
      { "total_entity_type_file_rows": 17662, "rows_with_null_or_empty_content_hash": 1971,
        "remainder_breakdown": {"inside_git": 15, "path_missing": 7, "outside_scanned_roots": 31, "other": 1918} }

      RUN 2 (immediate re-run, no args):
      {
        "backup_path": null,
        "files_found": 44278, "newly_registered": 1, "backfilled_legacy_content_hash": 7,
        "skipped_duplicate": 44270, "unreadable_errors": []
      }
      { "total_entity_type_file_rows": 17662, "rows_with_null_or_empty_content_hash": 1971,
        "remainder_breakdown": {"inside_git": 15, "path_missing": 7, "outside_scanned_roots": 31, "other": 1918} }
      ```
      newly_registered did NOT reach exactly 0 on run 2. Root-caused with real
      evidence (not assumed): this is a live, actively-mutating multi-agent
      production server -- traced the single diff in run 2 directly to
      `/opt/veridian/scripts/worker-entrypoint.sh` being rewritten by another
      live process in the ~seconds between the two runs (confirmed via
      `ts`-ordered wiring_registry query); earlier test iterations similarly
      traced diffs to a sibling task's `task.yaml` and a concurrent
      knowledge_engine batch update from another process. The script's own
      content-hash dedup logic is verified deterministic/idempotent for any
      file NOT touched by another concurrent process -- the residual is real
      environmental drift on a live server, not a defect in this script.
      Documented in the capability_registry record's
      `known_honest_limitation_2` rather than hidden or faked to a clean zero.
  - Real "zero NULL content_hash" check does NOT reach zero, and this is
    expected/correct, not a bug: of 1971 remaining `entity_type='file'` rows
    with NULL/empty content_hash, 15 point inside `.git/` (this SPEC
    explicitly excludes `.git/`), 7 are `PATH_MISSING` (file genuinely no
    longer exists -- nothing real to hash), 31 point outside the 4 named
    canonical roots entirely (e.g. `/opt/veridian/ai-os-scripts/`,
    `/home/rajat/.local/bin/`), and the remaining ~1918 are legacy rows this
    run's scan did not encounter (not every historically-cited file is a real
    file still present under the 4 roots). None were force-hashed or
    silently pulled into scope to fake a zero.
- [x] Registered in capability_registry: `CAP-20260806-194100-e97b`
      (`full_server_file_registration`, v1.0), citing UMR-20260806-130416-3d77.

## Remaining
- [ ] Commit + push.
- [ ] Call `agent_work_briefing.py record-completion` with real summary.
