# PROGRESS -- UMR-20260806-031558-4dbd (pm_decisions_pending writer, v2 re-dispatch)

Owner standing SOP, explicit, do not deviate: exactly ONE deterministic
script (`superboss-register.py`) is the canonical read/write surface for
`superboss-register.sqlite`. This adds the one real missing write path --
`insert_pm_decision_pending()` / `resolve_pm_decision_pending()` -- directly
to that script, matching its existing function-library convention exactly
(read `record_ocid_master_standard_audit_event()`,
`insert_ocid_artifact_link()`, `update_umr_task()` first). Re-dispatch of
UMR-20260805-190440-ebe8 after its worker crashed 3x on a real Anthropic
weekly usage-limit 429 (root cause fixed, unrelated to this task's own
scope, veridian-scripts PR #98). Related: UMR-20260805-185000-e94f (parent),
UMR-20260802-165606-4413 (OCID-020), UMR-20260806-031211-64de.

## Completed

- [x] Read `record_ocid_master_standard_audit_event()`,
      `insert_ocid_artifact_link()`, `update_umr_task()`,
      `_ensure_ocid_artifact_links_table()` in `superboss-register.py` for
      the real established convention (signature shape, docstring style,
      `_connect()`/`_write_lock()` discipline, callers own their own
      commit).
- [x] Confirmed the real live schema for `pm_decisions_pending` directly
      against `/opt/veridian/ai-os/memory/superboss-register.sqlite`
      (already merged live, per `migrate_2026-08-05_pm_report_tables.py`,
      branch `feat/pm-report-v3-schema-umr20260805181636`) -- columns id,
      opened_ts, title, detail, options_json, recommended_option,
      related_umr, status, closed_ts, closed_by, closed_note, matches this
      task's own spec exactly.
- [x] Added `_ensure_pm_decisions_pending_table(conn)` -- defensive,
      idempotent `CREATE TABLE IF NOT EXISTS`, same convention as
      `_ensure_ocid_artifact_links_table()`.
- [x] Added `insert_pm_decision_pending(conn, title, detail, options=None,
      recommended_option=None, related_umr=None)` -- returns the new row's
      real id, does not commit (caller owns the transaction), JSON-encodes
      `options` automatically.
- [x] Added `resolve_pm_decision_pending(conn, decision_id, closed_by,
      closed_note=None, status="resolved")` -- raises `ValueError` on an
      unknown `decision_id` rather than silently updating 0 rows.
- [x] Wired two new CLI subcommands matching the existing argparse pattern
      (`reconcile-umr-status`, `certify-pr-merge`):
      `insert-pm-decision-pending` and `resolve-pm-decision-pending`, each
      with its own `cmd_*` wrapper handling `_connect()`/`_write_lock()`/
      JSON output, dispatched from `main()`'s existing `elif args.cmd ==`
      chain.
- [x] Real tests: `tests/test_pm_decisions_pending.py`, 7 real pytest
      tests (insert/resolve library-function behavior, NULL-options
      handling, idempotent table creation, unknown-id ValueError, and a
      full real CLI subprocess round trip: `init` -> insert -> resolve ->
      a real failure case). Every test uses a real, isolated, temp-file
      SQLite DB, pre-seeded via this script's own real
      `_ensure_umr_table()`/`_ensure_pm_decisions_pending_table()` before
      `SUPERBOSS_REGISTER_DB` ever points at it (never the live DB).
- [x] Ran the full existing test suite (`tests/` + top-level `test_*.py`,
      130 + 46 = 176 tests outside the one pre-existing, unrelated `vt`
      fixture error in `test_ocid063_handoff_envelope.py`, independently
      confirmed to pre-exist on `main` before this branch, not caused by
      this change) -- zero regressions.
- [x] **Real incident, caught and corrected during this task**: an early
      manual smoke-test script set `SUPERBOSS_REGISTER_DB` to a
      not-yet-existing scratch path before importing the module --
      `resolve_superboss_db_path()`'s own documented step 2 (real path
      must already exist and be non-zero) correctly fell through to the
      real live default path instead, so that smoke test's insert+resolve
      briefly wrote a real, then-resolved test row (id=2, "test decision")
      into the real live production
      `/opt/veridian/ai-os/memory/superboss-register.sqlite`. Caught
      immediately by an independent re-read of the live table; removed via
      a real `DELETE FROM pm_decisions_pending WHERE id=2` through this
      same script's own `_connect()`/`_write_lock()`, then independently
      re-verified: `pm_decisions_pending` back to exactly its original
      single real row (id=1), `ocid_canonical_registry`=69,
      `gtm_certification_categories`=25 (both match this session's own
      documented baselines), `umr_tasks` row count consistent with normal
      ongoing activity from other real running workers. The one broken
      table on this DB, `file_inventory`, is the separately pre-existing,
      already-documented corruption (Hard Rule 8 hold,
      UMR-20260805-163026-14f1) -- confirmed untouched throughout, not
      newly caused by this incident. Every formal test in
      `tests/test_pm_decisions_pending.py` was rewritten afterward to use
      the pre-seed-before-pointing-env-var-at-it pattern
      `test_ocid_artifact_links.py` already documents for exactly this
      reason, so this class of mistake cannot recur in this suite.

## Remaining

- [ ] Push branch, open real PR against `FChecklist/veridian-scripts`
      `main`.
- [ ] `veridian-task.py adopt` for real independent review.
