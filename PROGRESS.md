# PROGRESS -- task-20260806-031857-extend-superboss-register-py-with-pm-dec

Re-dispatch of UMR-20260805-190440-ebe8 (prior worker crashed 3x on a real
Anthropic weekly usage-limit 429, unrelated to this task's own scope).
Owner's corrected, narrowed design: add `insert_pm_decision_pending()` and
`resolve_pm_decision_pending()` directly to `superboss-register.py` (repo:
veridian-scripts) -- no separate standalone script, per the Owner's standing
SOP that this one script is the canonical read/write surface for
`superboss-register.sqlite`.

## Independent verification (done before writing any code)

- [x] Confirmed the live database
      (`/opt/veridian/ai-os/memory/superboss-register.sqlite`) really does
      have `pm_decisions_pending` (and `pm_report_snapshots`) already, with
      exactly the columns the SPEC named, and the one real backfilled row
      (id=1, UMR-20260805-163026-14f1).
- [x] **Found a real SPEC/live-state mismatch** (matching this repo's known
      false-premise pattern): the SPEC says the schema is "already merged,
      `migrate_2026-08-05_pm_report_tables.py`" -- but that migration script
      and its commit (4797b71) only exist on an **unmerged** remote branch
      (`feat/pm-report-v3-schema-umr20260805181636`), never landed on `main`.
      Current `main`/HEAD has zero references to `pm_decisions_pending`
      anywhere in `superboss-register.py`. The schema was applied to the
      live DB directly at some point, outside of any merged PR. This does
      not block this task (the table already exists and is usable), but the
      repo's own git history does not yet reflect that schema -- documented
      in `_ensure_pm_decisions_pending_table()`'s own docstring so this
      doesn't get silently re-assumed "merged" again later.
- [x] Confirmed that unmerged branch's other, unrelated change to
      `superboss-register.py` (`query_ocid_compliance_state`) does not
      conflict with anything added here.
- [x] Read `record_ocid_master_standard_audit_event()`, `insert_ocid_artifact_link()`,
      `update_umr_task()`, their paired `_ensure_*_table()` helpers, and the
      `cmd_*`/argparse subcommand wiring (`reconcile-umr-status`,
      `certify-pr-merge`) to match this repo's real established convention
      exactly, rather than inventing a new shape.

## Completed

- [x] Added `_ensure_pm_decisions_pending_table(conn)` (idempotent
      `CREATE TABLE IF NOT EXISTS`, matches the live schema exactly) and
      wired it into `_migrate_schema()`.
- [x] Added `insert_pm_decision_pending(conn, title, detail, *, options=None,
      recommended_option=None, related_umr=None)` -- caller owns
      conn/commit, same convention as `insert_ocid_artifact_link()`/
      `update_umr_task()`.
- [x] Added `resolve_pm_decision_pending(conn, decision_id, *, closed_by,
      closed_note=None, status="resolved")` -- idempotent
      (`WHERE status='open'` guard), returns `True`/`False`, never
      overwrites an already-closed row.
- [x] Wired two CLI subcommands, matching the existing `cmd_*`/argparse
      pattern: `insert-pm-decision-pending` (`--title --detail
      --options-json --recommended-option --related-umr`) and
      `resolve-pm-decision-pending` (`--id --closed-by --closed-note
      --status`), both under `_write_lock()`.
- [x] Real tests: `tests/test_pm_decisions_pending.py`, 8/8 passing --
      direct library-function round trips, idempotent-resolve, unknown-id
      handling, a schema-column pin test (guards against drift from what's
      already live in production / what `generate_pm_report_v3.py` reads),
      and two CLI-level (`cmd_*`) end-to-end tests.
- [x] Ran the full existing test suite (`tests/test_*.py`, 17 files) after
      the change -- all still pass.
- [x] **Self-caught and fixed a real mistake**: an early ad-hoc manual test
      (outside the committed test file) connected to the live production DB
      instead of a scratch DB, because setting a module attribute before
      `exec_module()` doesn't override the module-level `DB_PATH =
      resolve_superboss_db_path()` line that runs during `exec_module`.
      This inserted and then resolved one test row (id=3) in the live
      `pm_decisions_pending` table. Caught immediately, deleted that row
      and its `sqlite_sequence` entry, and re-verified the live table is
      back to exactly its original single real row (id=1, untouched). The
      committed test file uses the repo's own safe isolation convention
      (pre-seed a real scratch file, `SUPERBOSS_REGISTER_DB` env override
      set *before* `exec_module()`) throughout, same as
      `tests/test_ocid_artifact_links.py`.
- [x] `python3 -m py_compile superboss-register.py` clean.

- [x] Committed (`d69a40b`), pushed
      `worker/task-20260806-031857-extend-superboss-register-py-with-pm-dec`,
      opened real PR: https://github.com/FChecklist/veridian-scripts/pull/103

## Remaining

- [ ] Independent review before merge.
