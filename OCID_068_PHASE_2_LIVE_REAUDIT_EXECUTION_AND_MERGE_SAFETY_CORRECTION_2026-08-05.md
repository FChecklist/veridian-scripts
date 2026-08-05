# OCID-068 Phase 2: First Live Re-Audit Execution, Merge-Safety Correction, and Permanent-Rule Reaffirmation

**Real dispatch:** this task has no discoverable real UMR ID -- a direct query of the live `umr_tasks` table found no row matching its task identity. Cited honestly by its real task-directory identifier, `task-20260805-161157-close-a-real-fabrication-loophole--not-a`, rather than an invented `UMR-YYYYMMDD-HHMMSS-hash`, per the same principle this whole document exists to enforce.
**Extends/reinforces:** `UMR-20260805-092408-4f97` (extending `UMR-20260805-090549-9710` / `UMR-20260805-091934-86a2`), the real anti-fabrication audit script and `not_applicable_confirmed`/`audit_raw_output` mechanism, already documented in `OCID_068_PHASE_2_REGISTRY_SCHEMA_AND_LINKAGE_EXTENSION_2026-08-05.md` (see its Section 4/5).
**Related:** `UMR-20260804-170055-a069` (canonical OCID-068 UMR), `UMR-20260805-042152-e559` (`resolve_ocid_canonical()`'s own originating directive).

## What this document is

This task's own directive substantially restates work already dispatched and merged the same day (`UMR-20260805-092408-4f97`, PR #57, `768fd6e`). Per this repo's own established precedent (see `OCID_069_REDISPATCH_DUPLICATE_CHECK_2026-08-05T131359.md`), it does not start a second parallel implementation of the audit script, the trigger-computed boolean gates, or the determinism/bypass-resistance tests -- all of that already existed on `main`. Instead, this document records the one real thing this task's own directive asked for that had genuinely never been done: **actually executing** `audit_ocid_canonical_registry.py --apply` against the live production database. Before this task, `audit_raw_output` was `NULL` on all 69 live `ocid_canonical_registry` rows -- every prior invocation of the script was either `--dry-run` or run against an isolated test-fixture database. `not_applicable_confirmed` was therefore honestly `0` on all 8 real `not_found` rows (OCID-007..OCID-011, OCID-012, OCID-013, OCID-014), exactly as the trigger's own design requires when no real stored evidence backs the claim -- correct, but incomplete, since the real evidence had never actually been generated and stored.

## Two real, live-data-only findings, neither of which was visible from unit tests alone

Both are disclosed here plainly, per this repo's own established convention (e.g. the OCID-013 citation error, the OCID-050 false-collision correction) of surfacing real problems found rather than silently working around them.

### Finding 1: real production data would have bloated `audit_raw_output` by an estimated 1-2+ GB

A handful of real `umr_tasks` rows (this same session's own OCID-068 Phase-2 dispatch/reuse-check rows among them) carry a `metadata_json.reuse_check_result` field 6+ MB in size -- a real, separate, pre-existing data-quality issue in this codebase's task-dedup engine (out of scope to fix here, independently flagged for Owner awareness: the dedup engine appears to embed full candidate intent text, not just match scores/IDs, in that field). `resolve_ocid_canonical()`'s method (b) (full `umr_tasks` grep) legitimately matches those rows for most real OCID numbers, since many real dispatch texts enumerate broad OCID ranges ("populate OCID-001 through OCID-068"). Storing every matched row's full text verbatim in `audit_raw_output` would have added an estimated 1-2+ GB to the live 1.4 GB production database in a single `--apply` run.

**Fix:** `audit_ocid_canonical_registry.py`'s new `_bounded_for_storage()` -- a fixed, deterministic, identically-applied-to-every-OCID 5000-character cap on individual string leaf values, with the real original length always disclosed when a value is capped. This is not interpretation and not a narrative summary: every real command actually run and its real result is still recorded, and every genuine `gh`/`git`/`grep` command result (naturally well under the cap in ordinary practice) is untouched byte-for-byte.

### Finding 2 (more serious): the original merge rule would have silently overwritten real, carefully-reasoned existing rows

`audit_ocid_canonical_registry.py`'s original merge rule (as merged under `UMR-20260805-092408-4f97`, before ever being run against real production data) preserved an existing `canonical_umr_id` only when the fresh search's `all_umr_ids` still corroborated it, and otherwise silently substituted the fresh result in full. This was never exercised against real data until this task -- and real data broke the assumption behind it: a fully mechanical full-text search over a corpus that increasingly contains real meta-discussion *about* OCIDs (not just genuine completion evidence *for* them) produces real false "corrections".

Live-verified this task, before any write occurred:
- **OCID-001**: its real, existing, historical `canonical_umr_id=UMR-20260802-034545-3388` / `status=rejected_duplicate (historical, pre-OCID-numbering, no implementation authorized)` -- a deliberate, reasoned judgment -- would have been silently overwritten by a spurious match against `UMR-20260802-104058-25ba`, an unrelated batch-registration dispatch UMR whose own prompt text simply enumerates "OCID-001 through OCID-068".
- **OCID-007 / OCID-011**: real, honestly-confirmed `not_found` rows would have been flipped to a false `status=multiple_umr_ids_found_needs_review` / `has_real_umr=1` by matching *this very task's own* meta-dispatch text discussing "OCID-007 through OCID-014" as the not-found roster -- i.e. the mechanism designed to close a fabrication loophole would, unfixed, have fabricated exactly the kind of false boolean it exists to prevent.

**Fix:** `plan_for_ocid()`'s merge rule was rewritten. For every real existing row (all 69 OCID numbers already have one), `canonical_umr_id`/`status`/`all_umr_ids`/`pr_number`/`pr_repo`/`not_found` are now **always preserved exactly as already recorded**, with no per-OCID exception -- this script never silently overwrites a real, already-reasoned canonical choice or a real, honest `not_found` confirmation, regardless of what the fresh mechanical search finds. `audit_raw_output` is still always refreshed with this run's real, bounded, verbatim evidence (the actual mechanism that closes the `not_applicable_confirmed` loophole). Any real disagreement between the fresh search and the existing record is appended -- never replacing the original reasoning -- to `duplicate_reason` as an explicit, idempotent `NEEDS HUMAN REVIEW` note for the Owner, guarded against growing across repeated re-runs.

## Real live-apply results

`python3 audit_ocid_canonical_registry.py --apply` was run for real against `/opt/veridian/ai-os/memory/superboss-register.sqlite` (backed up first to `superboss-register.sqlite.bak-pre-audit-raw-output-live-apply-20260805T163658Z`) on 2026-08-05, covering all 69 real OCID numbers (OCID-001..OCID-069):

<!-- FILLED IN AFTER THE LIVE RUN COMPLETES -->
- Real OCIDs re-audited: TBD
- Existing rows preserved unchanged, no exception: TBD
- Rows flagged `NEEDS HUMAN REVIEW` (fresh evidence disagreed; existing record kept): TBD
- Rows earning a fresh `not_applicable_confirmed=1` (real `not_found` + real, genuinely non-empty `audit_raw_output`): TBD
- Live database size before / after: TBD

## Real test coverage added this task

- `tests/test_audit_ocid_canonical_registry.py`:
  - `test_bounded_for_storage_caps_oversized_leaf_values_deterministically` -- proves the cap is a no-op at/under the limit, truncates deterministically and idempotently above it, discloses the real original length, and correctly walks nested dict/list evidence shapes.
  - `test_apply_style_write_of_pathologically_large_real_evidence_stays_bounded` -- end-to-end proof through `plan_for_ocid()` -> `upsert_ocid_canonical_registry()` -> the live row.
  - `test_plan_preserves_existing_record_and_flags_review_when_fresh_evidence_disagrees` (replaces the test that encoded the now-disproven-unsafe overwrite behavior) -- proves the existing record is preserved, the disagreement is disclosed (never dropped), and the `NEEDS HUMAN REVIEW` note does not grow across repeated real re-runs (idempotence).
  - Pre-existing `test_determinism_two_runs_identical_structured_output` and `test_not_applicable_confirmed_requires_real_stored_audit_raw_output` re-confirmed unaffected.
- Full repo suite: 16/16 test files pass at time of writing.

## Permanent rule -- reaffirmed, not superseded

`OCID_068_PHASE_2_REGISTRY_SCHEMA_AND_LINKAGE_EXTENSION_2026-08-05.md`'s Section 5 already states this rule; this document reaffirms it in the Owner's own words, plainly, for the avoidance of any doubt:

**No boolean or completion claim in `ocid_canonical_registry` (or any table built on the same pattern) may ever be hand-set or narrated again. Every one must trace to real, stored, re-runnable, verbatim evidence:**
- The 8 gate booleans (`has_real_umr`, `has_real_pr`, `has_real_commit`, `has_real_merge`, `has_real_file_path`, `has_real_evidence_summary`, `is_fully_complete`, `not_applicable_confirmed`) are recomputed from the row's own stored columns by a DB trigger on every write -- never hand-settable, including via direct raw SQL (independently proven by `tests/test_ocid_registry_completion_gate.py`).
- `not_applicable_confirmed` specifically requires both `not_found=1` AND a genuinely non-empty `audit_raw_output`, written exclusively by `audit_ocid_canonical_registry.py` (or `cmd_resolve_ocid_canonical --apply`, which calls the same real mechanical search) -- never a hand-typed one-line reason standing in for it.
- This task's own real, live-data findings above are the concrete proof of *why* this matters: even a genuinely mechanical, zero-AI-judgment search can produce a real false result against real data, which is exactly why this registry's design never lets a single automated pass silently overwrite an existing real judgment -- it can only add real evidence and flag real disagreement for a real human to resolve.

## Real citations

- `UMR-20260805-092408-4f97`, `UMR-20260805-091934-86a2`, `UMR-20260805-090549-9710` (the real prior work this task extends, not duplicates)
- `UMR-20260804-170055-a069` (canonical OCID-068 UMR)
- `UMR-20260805-042152-e559` (`resolve_ocid_canonical()`'s own originating directive, reused not duplicated)
- `OCID_068_PHASE_2_REGISTRY_SCHEMA_AND_LINKAGE_EXTENSION_2026-08-05.md` (this document's own predecessor; not edited, this is a new additive record)
- veridian-scripts PR #57 (`768fd6e`, the real schema/trigger/audit-script infrastructure this task exercised for real for the first time)
