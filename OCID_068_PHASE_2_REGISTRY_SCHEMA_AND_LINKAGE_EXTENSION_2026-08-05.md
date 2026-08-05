# OCID-068 Phase 2: Registry Schema, Completion Gate, Linkage Extension, Anti-Fabrication Audit Script, and Seven-Rule Compliance Tracking

**Real dispatch instruction:** `UMR-20260805-090549-9710` (Owner directive), extending the now-superseded `UMR-20260805-085025-c257`.
**Real reinforcements/corrections to the same task (all cited below, none started a second parallel task):** `UMR-20260805-091934-86a2`, `UMR-20260805-092408-4f97`, `UMR-20260805-093138-2bd0`, `UMR-20260805-093254-056e`, `UMR-20260805-093630-29d1`.
**Related:** `UMR-20260804-170055-a069` (canonical OCID-068 UMR, real status `completed`), `UMR-20260805-032731-b412` (OCID-068's permanent closure record, real status `completed`, PR #52).

This document is a NEW, additive record. It does not edit `OCID_068_GUARDRAIL_RULES_PERMANENT_CLOSURE_2026-08-05.md` or `OCID_MASTER_STANDARD_V6_PHASE1_2026-08-05.md`, both of which stay exactly as originally merged.

## What this Phase 2 work is

The Owner's goal: make `/opt/veridian/ai-os/memory/superboss-register.sqlite` the single real place of truth for OCID/UMR/PR/commit/file completion evidence, with zero open-ended free-text assumptions and zero hand-settable completion claims. This directive arrived and was reinforced/corrected across six sequential real dispatches in the same session (all independently confirmed real rows in the live `umr_tasks` table, each one's own `inputs_json` verified to substantively corroborate what it asked for before being acted on) -- summarized here in the order they arrived.

## 1. New real columns + DB-enforced completion gate on `ocid_canonical_registry` (`UMR-20260805-090549-9710`)

Added via an idempotent `_migrate_ocid_canonical_registry_completion_columns()` (checks `PRAGMA table_info` first, only `ALTER TABLE ADD COLUMN`s what's missing):

- `commit_sha`, `file_name`, `file_path`, `merge_status`, `evidence_summary` -- real dedicated evidence columns, not a duplicate of `evidence_json`.
- `has_real_umr`, `has_real_pr`, `has_real_commit`, `has_real_merge`, `has_real_file_path`, `has_real_evidence_summary`, `is_fully_complete` -- 7 real boolean gate columns.
- `not_applicable_confirmed`, `audit_raw_output` -- added in step 3 below.

**These booleans are never hand-set.** A real `AFTER INSERT`/`AFTER UPDATE` pair of triggers (`ocid_canonical_registry_completion_ai`/`_au`) recomputes and overwrites all of them from the row's own real underlying columns on every write, regardless of what any caller (including a direct raw SQL `UPDATE`) supplies. SQLite's default `PRAGMA recursive_triggers=OFF` means the trigger's own internal `UPDATE` does not recursively re-fire itself -- independently verified by a real timing-bounded test (`tests/test_ocid_registry_completion_gate.py::test_no_infinite_trigger_recursion_or_hang_on_insert_and_update`), not merely assumed.

`upsert_ocid_canonical_registry()` accepts the new data fields as parameters but does **not** accept `has_real_*`/`is_fully_complete`/`not_applicable_confirmed` as caller-settable parameters at all.

## 2. Extended the EXISTING linkage graph, not a second one (`UMR-20260805-090549-9710`, `UMR-20260805-091934-86a2`)

`ocid_artifact_links` (PR #20) already supported forward queries (by `ocid_number`/`umr_id`/`repo`/`pr_number`). `query_ocid_artifact_links()` now also accepts `file_path` and `commit_sha`, answering the reverse direction: given a real file or commit, find every real OCID/UMR it belongs to.

## 3. Mandatory active file-fetching, never passive (`UMR-20260805-091934-86a2`)

`backfill_ocid_registry_phase2_columns.py` actively calls the real `/usr/bin/gh` binary (`gh pr view <pr_number> --repo FChecklist/<repo> --json mergeCommit,files,state,mergedAt`) for every row with a real `pr_number` -- never passively trusting `evidence_json`. Every real changed file in a PR is recorded as its own linkage row in `ocid_artifact_links` via `insert_ocid_artifact_link(..., link_kind='changed_file')` -- one OCID can link to many real files, not just the single "primary artifact" `file_path` column (which stays a best-single-file pick, left `NULL` when genuinely ambiguous across multiple files, never guessed).

The 8 real `not_found` rows (`OCID-007`..`OCID-011`, `OCID-012`, `OCID-013`, `OCID-014`) are the only exception -- no fetch attempted, since no file path can genuinely apply to an OCID that was never real / never registered.

## 4. Real, re-runnable, zero-AI-judgment audit script replaces prose reasons (`UMR-20260805-092408-4f97`)

**The real problem:** a `not_applicable_confirmed` boolean plus a hand-typed one-line reason could be fabricated by any AI process without ever re-running the real search, and nothing in the database would tell the difference.

**The fix:** `not_applicable_confirmed` is trigger-computed as `1 iff not_found = 1 AND audit_raw_output IS NOT NULL AND length(audit_raw_output) > 0` -- a bare `not_found=True` claim with no real stored evidence behind it no longer earns the marker. `audit_raw_output` is a real, verbatim, JSON-encoded dump of `resolve_ocid_canonical()`'s own `evidence` dict -- the same already-merged (`UMR-20260805-042152-e559`), zero-AI-judgment 6-method mechanical search this codebase already ran (`umr_tasks` substring match, full-table grep, `gh pr list` x3 repos, `git log --grep` x3 repos as cross-check, UMR-ID regex extraction, `MASTER-TRACKER.yaml`/`ACTIVE-CLAIMS.yaml` grep as last resort). This function was reused, not duplicated.

`audit_ocid_canonical_registry.py` is the new, real, standalone batch driver: it re-runs `resolve_ocid_canonical()` for every real OCID and applies one fixed, documented merge rule (identical for every OCID, never a per-row judgment call) -- preserve a real, existing, still-corroborated canonical choice; use the fresh result in full only when no longer corroborated; always refresh `not_found`/`audit_raw_output`. This avoids silently downgrading a carefully-reasoned prior canonical choice (e.g. OCID-068's own, which is explicitly not "chronologically earliest UMR") to `resolve_ocid_canonical()`'s own simpler automatic default.

`cmd_resolve_ocid_canonical`'s own existing `--apply` CLI path was also upgraded to write `audit_raw_output`, so any real use of that command structurally earns the gate too.

**Determinism independently verified**: `tests/test_audit_ocid_canonical_registry.py::test_determinism_two_runs_identical_structured_output` runs the real search twice against unchanged data and asserts byte-identical structured JSON output.

## 5. Permanent rule (this section, `UMR-20260805-092408-4f97` + `UMR-20260805-093138-2bd0`)

**No boolean or completion claim in this registry may ever be hand-set or narrated again.** Every one must trace to real, stored, re-runnable, verbatim evidence:
- `ocid_canonical_registry`'s 8 gate booleans (`has_real_*`, `is_fully_complete`, `not_applicable_confirmed`) are recomputed from the row's own stored columns by a DB trigger on every write.
- `ocid_compliance_state`'s 13 gate booleans (below) are recomputed from `ocid_compliance_audit_log`'s own real, append-only evidence rows by a DB trigger on every write.
- The only field in this whole Phase 2 extension permitted real synthesis rather than a pure mechanical boolean is `ocid_compliance_state.file_details` (a 100-character synopsis) -- even that must be grounded in real file content actually read, never invented, and is flagged in this task's own PR description as needing extra real scrutiny during review.

## 6. Seven-rule compliance tracking, full 69-OCID roster (`UMR-20260805-093138-2bd0`, scope-clarified by `UMR-20260805-093254-056e`)

Two new real tables, covering every real OCID already in `ocid_canonical_registry` (all 69, `OCID-001`..`OCID-069`) and every real UMR associated with each one via `all_umr_ids_json` -- not only OCID-068 itself, not only new work going forward.

**Naming note (disclosed plainly, not a silent divergence):** these were originally requested as `ocid_068_compliance_state`/`ocid_068_compliance_audit_log`. `UMR-20260805-093254-056e` explicitly authorized renaming for clarity once real coverage became the full roster rather than OCID-068 alone. They are named `ocid_compliance_state` / `ocid_compliance_audit_log`; `audit_ocid_compliance.py` (the batch driver) follows the same rename.

### `ocid_compliance_state`
One real row per real `(ocid_number, umr_id)` pair (composite PRIMARY KEY). 7 rule booleans (`rule_1_umr_reuse_verified` .. `rule_7_structured_evidence_verified`), 6 file-tracking booleans (`file_path_checked`, `file_checked`, `file_path_available`, `file_path_validated`, `file_existing`, `file_work_implemented`), plus `file_path`, `file_details` (<=100 chars, the one real-synthesis exception), `file_created_date`, `file_last_reviewed_date`, `version_history`, `version_date`, `status_one_word`, `status_one_sentence`, `audit_done`, `audit_passed`, `last_audit_timestamp`.

Each of the 7 rules is grounded in a real, re-runnable mechanical check (not a static true because the rule was merged once), reusing existing real infrastructure where it already exists (Rule 6 reuses `find_active_umr_by_ocid()`, the exact real, already-merged Rule 6 mechanism function; Rule 7 checks for a real `ocid_artifact_links` row). Where a rule's own real mechanism did not exist yet as of a given UMR's real `ts_submitted` (independently confirmed via `gh pr view --json mergedAt` for each of PRs #26/#29/#30/#32/#33/#34/#35), that rule is recorded honestly `false` with a real explanation naming the real PR and merge date -- never `true`, never silently null.

### `ocid_compliance_audit_log`
Real, append-only, never updated or deleted in place. One real row per real field/rule per real audit run: `id`, `ocid_number`, `umr_id`, `audit_timestamp`, `rule_or_field_name`, `result`, `raw_output` (real verbatim evidence), `audited_by`.

### Anti-fabrication enforcement (structural, not merely a Python convention)
All 13 boolean columns on `ocid_compliance_state`, plus `audit_done`/`audit_passed`, are recomputed by a real `AFTER INSERT`/`AFTER UPDATE` trigger pair (`ocid_compliance_state_derive_ai`/`_au`) that looks up the MOST RECENT matching row in `ocid_compliance_audit_log` for each `(ocid_number, umr_id, rule_or_field_name)` via a correlated subquery -- the same structural pattern already proven for `ocid_canonical_registry`'s own gate columns, applied here across two tables instead of within one row. A field with zero real audit-log evidence derives to a real, honest `0`/false, never a fabricated pass. Independently proven by `tests/test_ocid_068_compliance.py::test_direct_sql_fabrication_of_compliance_state_is_overridden_by_trigger`: a bare, hand-typed `INSERT` claiming full compliance for a pair with zero real audit-log rows has every one of its 13 booleans overridden back to `0` by the trigger.

`record_ocid_compliance_audit()` is the only real write path exposed for these tables, and always writes the matching `ocid_compliance_audit_log` rows in the same real transaction as the `ocid_compliance_state` upsert -- current state and full history can never drift apart.

`audit_ocid_compliance.py` is the real batch driver covering the full historical roster; default is a dry run, `--apply` writes for real inside `_write_lock()`.

## 7. UTR / UMR / single-source-of-truth taxonomy, recorded at the source (`UMR-20260805-093630-29d1`)

A new real table, `registry_taxonomy_notes` (checked first that no existing schema/metadata/notes table already served this purpose), holds one real, permanent explanatory row:

- **UTR (Universal Task Registry)** = the real `umr_tasks` table, covering every real dispatched task.
- **UMR (Universal Metadata Registry)** = the real, broader knowledge/metadata layer; the `UMR-YYYYMMDD-HHMMSS-hash` identifiers already used throughout this whole system are the real individual entries within it, not a separate identifier scheme.
- `/opt/veridian/ai-os/memory/superboss-register.sqlite` itself is the one real place of truth, housing the UTR and UMR layer together with `ocid_canonical_registry`, `ocid_artifact_links`, and `ocid_compliance_state`/`ocid_compliance_audit_log` -- all cross-referencing the same real `umr_id` values.

Seeded idempotently via `record_registry_taxonomy_note()`, same upsert-by-key convention as the rest of this file.

## Real test coverage added this task

- `tests/test_ocid_registry_completion_gate.py` (6 tests)
- `tests/test_audit_ocid_canonical_registry.py` (4 tests, including the determinism proof)
- `tests/test_ocid_068_compliance.py` (7 tests, including the fabrication-bypass proof and transactional-pairing proof)

All real, isolated, temp-file SQLite databases -- never the live production database. Full repo suite: 102 passed, 0 failed at time of writing.

## Real citations

- `UMR-20260805-090549-9710` (this task's own real, already-existing dispatch UMR)
- `UMR-20260805-091934-86a2`, `UMR-20260805-092408-4f97`, `UMR-20260805-093138-2bd0`, `UMR-20260805-093254-056e`, `UMR-20260805-093630-29d1` (real reinforcements/corrections to the same task, all independently confirmed real rows in `umr_tasks` before being acted on)
- `UMR-20260804-170055-a069` (canonical OCID-068 UMR, real status `completed`)
- `UMR-20260805-032731-b412` (OCID-068 permanent closure record, real status `completed`, PR #52)
- `UMR-20260805-085025-c257` (now-superseded predecessor this task extends)
- `UMR-20260805-042152-e559` (`resolve_ocid_canonical()`'s own originating directive, reused not duplicated)
- veridian-scripts PR #20 (`ocid_artifact_links`), PR #26/#29/#30/#32/#33/#34/#35 (the seven guardrail rules), PR #53 (`ocid_canonical_registry`)
