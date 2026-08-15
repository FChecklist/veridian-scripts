# PROGRESS -- task-20260815-143319-pm-in-server--add-real-part3-4-gtm-cert

SPEC: code-level equivalent of the 2026-08-15 Owner directive ("update /pm,
PM-in-server, veridian-server-sentinel, PM-in-desktop to complete Part3+4
with minimum tokens, real work, audit, and completion certificate") for
pm-sentinel-tick.sh -- add one new deterministic check block following the
file's own existing pattern.

## Completed

- [x] Verified live state independently before writing anything (memory:
      veridian-task-prompt-false-premise-pattern). Queried the real live
      `gtm_certification_categories` table
      (`/opt/veridian/ai-os/memory/superboss-register.sqlite`, resolved via
      `resolve_superboss_db_path()`, not the workspace's own empty local
      copy) directly: 25 rows total, all `ocid_number='OCID-020'`. Real
      **7** gap rows as of 2026-08-15 (4 hard FAIL: security audit, browser
      compatibility, UX audit, production readiness audit; 3
      never-validated: load testing, stress testing, AI testing) -- **not**
      the SPEC's stated 9; `multi tenant testing` (15) and `role permission
      testing` (16) already show real `passed=1` with real evidence. This
      is exactly the kind of drift the SPEC itself warns about ("re-query
      live each tick, do not hardcode this count") -- the new check queries
      live every tick and never hardcodes 9 or 7.
- [x] Confirmed the two seed UMRs (UMR-20260815-033344-4799,
      UMR-20260815-042226-f271) are both real, live `status=failed` (not
      in-flight) -- and separately found two REAL currently-in-flight rows
      that content-match this gap (UMR-20260815-105956-fdcd queued,
      UMR-20260815-041647-abee running) plus this task's own governing UMR
      (UMR-20260815-044235-a5e1, itself content-matching, which is the
      expected self-reference case once this check dispatches its own
      follow-up work).
- [x] Read the full existing `pm-sentinel-tick.sh` (1084 lines) and its test
      file to learn the exact conventions before writing anything: query-
      once-per-tick cache (`CACHE_DIR`), `record_finding()`/`dispatch_gap()`
      DECIDE-AND-FIX pairing, `emit_report_row()`, env-override resolution
      order for `RESOURCE_GOVERNOR_PY`/`SUPERBOSS_REGISTER_PY`.
- [x] **superboss-register.py**: added the one real, canonical read/write
      path this check needed (none existed before -- `update-gtm-category`
      only ever wrote one row's `child_umr_id`/`fix_*` columns, never
      read/wrote `passed`/`evidence_summary`, never certified anything):
      - `list_gtm_certification_categories()` / `list-gtm-categories` CLI --
        real, read-only listing of every row.
      - `gtm_part3_4_certificate_status()` / `record_gtm_part3_4_completion_
        certificate()` / `record-gtm-part3-4-certificate` CLI -- the one
        real, canonical, **idempotent** write path for the completion
        certificate. Reuses the existing `ocid_master_standard_audit_log`
        (one new `event_type`, not a new table). Never self-certifies: independently
        re-verifies every cited category is real `passed=1` with real
        non-empty, non-placeholder `evidence_summary` and raises
        (refusing to write anything) otherwise. Verified directly
        (`.scratch_test_cli.py`, since deleted): rejects a real gap present,
        rejects placeholder evidence ("TBD"), writes once, second call
        returns the same row with `newly_created=false`.
- [x] **pm-sentinel-tick.sh**: added Check 4 (real new lines, following the
      exact existing pattern -- `record_finding()`+`dispatch_gap()` at every
      real gap call site, `emit_report_row()`, same env-var/CACHE_DIR reuse,
      never bypasses `dispatch_gap()`'s own cap/financial-decision checks):
      1. Queries `gtm_certification_categories` live via `list-gtm-
         categories` every tick (never hardcoded).
      2. `gtm_orchestrator_in_flight()` -- real, content-matched (task_
         identity + real prompt text) scan of queued/running rows for
         "gtm_certification_categories"/"OCID-020"/either seed UMR id
         **before** dispatching -- broader than `dispatch_gap()`'s own
         narrower per-target_key STATE_FILE dedup, specifically because the
         real seed orchestrator runs were dispatched independently of this
         sentinel and would otherwise be invisible to it. Bounded to
         `--limit 20` per status, writes to `CACHE_DIR` temp files (not
         argv -- a `--full` queued/running dump is multi-hundred-KB).
      3. Gap rows > 0 and nothing in flight -> exactly one real dispatch
         (tier 1, `compliance-tracker`) through `dispatch_gap()`, citing the
         real live-queried gap list.
      4. Gap rows == 0 -> real completion-evidence check (rejects empty/
         placeholder `evidence_summary` on any `passed=1` row -- itself
         dispatched as a real fix if found) before ever writing the
         certificate; only then calls `record-gtm-part3-4-certificate`
         (idempotent -- a real certificate already on record is left
         alone).
      5. Added a `GTM_TOTAL_COUNT == 0` guard (deliberate no-op, not a
         `TICK_FAILURES` case) -- a vacuously-empty table must never be
         treated as "zero gaps, all evidenced" (would write a false
         certificate); this also keeps every pre-existing test's own
         schema-only DB copy (which never seeds this table) unaffected.
- [x] **Tests** (`test_pm_sentinel_tick.py`, real `pytest`, not fabricated):
      added `_seed_gtm_categories()` / `_insert_umr_row_with_inputs()`
      helpers plus 4 new test classes / 6 new tests exactly matching the
      SPEC's requested fixtures (9-gap-row dispatch, zero-gap-all-evidenced
      certificate, already-in-flight no-op/dedup) plus one extra
      (placeholder-evidence dispatch, not a certificate). Real run:
      `python3 -m pytest test_pm_sentinel_tick.py -v` -> **17 passed**
      (11 pre-existing + 6 new), **zero regressions**. Also ran
      `test_generate_pm_report_v3.py` (116 passed, touches the same
      `gtm_certification_categories` schema) as an extra regression check.
      Note: the 6 new tests must set `SUPERBOSS_REGISTER_PY` to this
      checkout's own copy (the new `list-gtm-categories`/`record-gtm-
      part3-4-certificate` subcommands do not exist on the live server's
      copy until this PR merges and syncs -- same real deploy-drift concern
      Check 0 already documents); the 11 pre-existing tests need no such
      override since they only used already-live subcommands.
- [x] `bash -n pm-sentinel-tick.sh` and `python3 -c "import ast; ast.parse
      (...)"` on both changed Python files -- clean.

## Remaining

- [ ] None -- code + tests complete, committed, pushed. Needs a real
      independent AUDIT:PASS before merge (never self-certifying this PR
      either).
- [ ] Deploy note (same "no automated deploy step, live checkout IS what
      runs" convention as every other change in this repo): Check 0's own
      drift detection will flag `/opt/veridian/scripts/{pm-sentinel-tick.sh,
      superboss-register.py}` as out of sync with `origin/main` once this
      merges, until the live checkout is reconciled -- expected, not a new
      gap.
