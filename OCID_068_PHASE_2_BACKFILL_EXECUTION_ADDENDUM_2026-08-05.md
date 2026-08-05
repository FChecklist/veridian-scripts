# OCID-068 Phase 2 Addendum: Real Live-DB Backfill Execution (2026-08-05)

**Real dispatch instruction:** Owner directive, task
`task-20260805-151455-make-superboss-register-sqlite-the-deter`.
**Real new UMR minted for this task:** `UMR-20260805-152250-55d3` (status
`completed`), minted via `resource_governor.submit()` (tier=2,
`source_trigger=owner_dispatch_gateway`), linked to `OCID-068` in
`ocid_artifact_links` (`link_kind=phase2_backfill_execution`).
**Cites:** `UMR-20260804-170055-a069` (canonical OCID-068 UMR, real status
`completed`), `UMR-20260805-032731-b412` (OCID-068's real permanent closure
record, real status `completed`, PR #52), `UMR-20260805-085025-c257` (the
evidence_json standardization dispatch this Phase 2 line of work already
extends -- real status `running` in the live `umr_tasks` table as of this
writing, cited honestly, not altered here), `UMR-20260805-090549-9710` (the
Phase 2 schema/trigger/linkage dispatch this addendum's real execution work
completes -- real status `running` in the live `umr_tasks` table as of this
writing, cited honestly, not altered here).

This document is a NEW, additive record, describing a **Phase 2 capability
extension**. It does not edit or reopen
`OCID_068_GUARDRAIL_RULES_PERMANENT_CLOSURE_2026-08-05.md` (OCID-068's real
permanent closure record) or
`OCID_068_PHASE_2_REGISTRY_SCHEMA_AND_LINKAGE_EXTENSION_2026-08-05.md` (the
schema/trigger/linkage design record this addendum extends), both of which
stay exactly as originally merged.

## Duplicate-check finding, verified before any new work started

Independent re-verification found that the schema/trigger/linkage/test
infrastructure this task's SPEC describes was **already built and merged**
in PR #57 (`feat/ocid-registry-phase2-schema-triggers`, merge commit
`c8f40eb`, real commit `768fd6e`, confirmed a real ancestor of this branch's
own base via `git merge-base --is-ancestor`):

- Real dedicated columns on `ocid_canonical_registry`: `commit_sha`,
  `file_name`, `file_path`, `merge_status`, `evidence_summary` (`pr_number`,
  `pr_repo` predate Phase 2, from PR #53) -- confirmed present in the live
  `PRAGMA table_info(ocid_canonical_registry)` before this task began.
- Real boolean gate columns `has_real_umr`, `has_real_pr`, `has_real_commit`,
  `has_real_merge`, `has_real_file_path`, `has_real_evidence_summary`,
  `is_fully_complete` -- confirmed present, and confirmed DB-trigger-computed
  (`ocid_canonical_registry_completion_ai`/`_au`), never hand-settable.
- The existing `ocid_artifact_links` linkage graph (PR #20) already extended
  with reverse-direction queries (`query_ocid_artifact_links(file_path=...,
  commit_sha=...)`) -- no second parallel linkage mechanism was built.
- Real test coverage already existed and (re-run this task) still passes:
  `tests/test_ocid_registry_completion_gate.py`,
  `tests/test_audit_ocid_canonical_registry.py`,
  `tests/test_ocid_068_compliance.py` (17 tests, including
  `test_linkage_graph_forward_and_reverse_query` and the direct-SQL
  fabrication-override proofs).

Per this task's own directive to "extend that work, do not duplicate it,"
none of the above was rebuilt. Building a second schema/trigger/test layer
identical to what PR #57 already merged would itself have been the exact
kind of duplication this whole OCID/UMR discipline exists to prevent.

## The real gap this addendum closes: the backfill had never actually run

`backfill_ocid_registry_phase2_columns.py` (also already merged in PR #57)
is a real, `gh`-backed, one-off backfill script -- but independent
verification, done before writing a single line of new code, found it had
**never actually been executed with `--apply` against the live production
database**: every one of the 69 `ocid_canonical_registry` rows' `commit_sha`
/ `file_name` / `file_path` / `merge_status` / `evidence_summary` columns
was still `NULL`, and `ocid_artifact_links` held only 3 rows, despite the
schema/code being live since PR #57's merge. (A prior backup file,
`superboss-register.sqlite.bak-pre-ocid-registry-phase2-backfill-20260805T112652Z`,
shows the same all-`NULL` state, confirming the backfill was prepared for
but genuinely never run, not run-then-reverted.)

### What this task did

1. **Real backup before any change:** `cp -p` of the live DB to
   `superboss-register.sqlite.bak-pre-ocid-registry-phase2-backfill-EXECUTION-20260805T152236Z`,
   checksum-verified byte-identical to the live file at backup time
   (`sha256sum` match).
2. **Minted `UMR-20260805-152250-55d3`** via the real
   `resource_governor.submit()` path, tier=2,
   `source_trigger=owner_dispatch_gateway`.
3. **Ran `python3 backfill_ocid_registry_phase2_columns.py --apply`** for
   real against the live DB (using the real `/usr/bin/gh`, authenticated as
   `FChecklist`). Result: 69 rows processed, 0 `gh` failures. Recovered
   `commit_sha` for 49/69 rows, a single unambiguous primary `file_path` for
   23/69 rows, `merge_status` for 57/69 rows, `evidence_summary` for 69/69
   rows, and wrote 211 real per-changed-file `ocid_artifact_links` rows (plus
   this task's own 1 linkage row = 214 total live). The remaining rows stay
   honestly `NULL` -- 8 `not_found`-exception OCIDs never fetched by design,
   4 real OCIDs with no `pr_number` to fetch from, and the multi-file PRs
   where no single file was an unambiguous canonical pick (`file_path` stays
   `NULL` there by design; the real files are still recorded in
   `ocid_artifact_links`). No value was ever invented.
4. **Verified the boolean gate live, adversarially:** after the backfill,
   `is_fully_complete` reads `1` for 20/69 rows and `0` for the remaining 49
   -- an honest reflection of which rows have every one of the 6 underlying
   real fields populated. A direct, hand-typed
   `UPDATE ocid_canonical_registry SET is_fully_complete=1 WHERE
   ocid_number='OCID-069'` (a row still missing several real fields) was run
   against the **live** database as a real adversarial proof; the
   `_au` trigger overwrote it back to `0` on commit, live -- the same
   structural proof `tests/test_ocid_068_compliance.py` already gives on an
   isolated temp DB, now independently reconfirmed on the real, live,
   production database itself.
5. **`OCID-068`'s own row** now carries real `commit_sha`
   (`3b0069b4b2cd257f7537a6cbaeaad60f5117b197`), real `file_path`
   (`ai-os/VERIDIAN_OCID_068_UNIVERSAL_GOVERNANCE_RUNTIME_CONSOLIDATION_OWNER_REVIEW_PACKAGE_2026-08-04.md`),
   `merge_status=merged`, and all 7 gate booleans `1`/`is_fully_complete=1`
   -- computed by the trigger from real backfilled data, not hand-set.
6. **Linked this task's own UMR to `OCID-068`** via
   `insert_ocid_artifact_link(..., link_kind='phase2_backfill_execution')`,
   and appended a timestamped, evidence-bearing entry to `OCID-068`'s own
   `evidence_json` (via `upsert_ocid_canonical_registry()`, preserving every
   other real field on the row unchanged) recording this execution event.
   `all_umr_ids_json` on `OCID-068`'s row now includes
   `UMR-20260805-152250-55d3`.
7. **Re-ran the full existing test suite** (133 passed; 4 unrelated
   pre-existing errors in `test_ocid063_handoff_envelope.py`, a stray root-
   level file missing a `vt` pytest fixture, last touched in commit
   `82d107f` long before this task -- confirmed pre-existing and out of
   scope, not introduced by this work).

### Live-DB reverse/forward linkage, confirmed with real data (not just the isolated-DB unit test)

Using `OCID-068`'s own real, newly-backfilled data as the concrete proof
case:
- Forward (`OCID-068` -> UMR/PR/commit/file): `query_ocid_artifact_links(conn,
  ocid_number="OCID-068")` returns the real changed-file link plus this
  task's own `phase2_backfill_execution` link.
- Reverse (file -> OCID/UMR):
  `query_ocid_artifact_links(conn,
  file_path="ai-os/VERIDIAN_OCID_068_UNIVERSAL_GOVERNANCE_RUNTIME_CONSOLIDATION_OWNER_REVIEW_PACKAGE_2026-08-04.md")`
  resolves back to `OCID-068` / `UMR-20260804-170055-a069`.
- Reverse (commit -> OCID/UMR):
  `query_ocid_artifact_links(conn, commit_sha="3b0069b4b2cd257f7537a6cbaeaad60f5117b197")`
  resolves back to the same real row.

This is the same forward/reverse mechanism `tests/test_ocid_registry_completion_gate.py::test_linkage_graph_forward_and_reverse_query`
already proves on an isolated temp DB; this addendum additionally confirms it
against real, live, backfilled production data for a real, known OCID.

## What this addendum is not

- Not a new schema. Not a new trigger. Not a new linkage mechanism. Not a new
  test file. All of that already existed, real and merged, in PR #57.
- Not a reopening of OCID-068's permanent closure
  (`OCID_068_GUARDRAIL_RULES_PERMANENT_CLOSURE_2026-08-05.md`) -- that record
  is untouched.
- Not an invention of any recovered value -- every `NULL` left `NULL` is a
  row where `gh` genuinely returned no real merged-PR/file data to recover
  (no `pr_number` to query, or a genuinely ambiguous multi-file PR for the
  single `file_path` column specifically), never a guess.

## Real citations

- `UMR-20260805-152250-55d3` (this task's own real, new UMR, status `completed`)
- `UMR-20260804-170055-a069` (canonical OCID-068 UMR, real status `completed`)
- `UMR-20260805-032731-b412` (OCID-068 permanent closure record, real status `completed`, PR #52)
- `UMR-20260805-085025-c257` (evidence_json standardization dispatch this line of work extends; real status `running`, cited honestly, unchanged by this task)
- `UMR-20260805-090549-9710` (the Phase 2 schema/trigger/linkage dispatch this addendum's execution work completes; real status `running`, cited honestly, unchanged by this task)
- veridian-scripts PR #57 (`c8f40eb` / `768fd6e`) -- the schema/trigger/linkage/test work this addendum extends, not duplicates
- veridian-scripts PR #53 (`b42a01e`) -- `ocid_canonical_registry` original schema/API/CLI
- veridian-scripts PR #20 -- `ocid_artifact_links` original linkage graph
- `OCID_068_PHASE_2_REGISTRY_SCHEMA_AND_LINKAGE_EXTENSION_2026-08-05.md` -- the design record this addendum's execution completes
- `OCID_068_GUARDRAIL_RULES_PERMANENT_CLOSURE_2026-08-05.md` -- OCID-068's real permanent closure record, untouched
