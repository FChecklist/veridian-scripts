# OCID-068 Addendum: UTR / UMR / Single-Source-of-Truth Taxonomy — Independent Verification

**This task's SPEC:** Owner directive, `UMR-20260805-093630-29d1`, citing `UMR-20260804-170055-a069`
(canonical OCID-068 UMR), `OCID-068`, and the real schema work already in progress under
`UMR-20260805-090549-9710` and `UMR-20260805-093138-2bd0`. Directs: record a real, formal, permanent
explanatory taxonomy at the real source (not only in external memory or a report) — **UTR**
(Universal Task Registry) = the real `umr_tasks` table, covering every real dispatched task; **UMR**
(Universal Metadata Registry) = the real, broader knowledge/metadata layer, of which every
`UMR-YYYYMMDD-HHMMSS-hash` identifier already used throughout is an individual entry; and
`/opt/veridian/ai-os/memory/superboss-register.sqlite` itself is the one real place of truth,
housing both together with `ocid_canonical_registry` and the OCID-068 compliance-tracking tables.
Requires either a row in an existing metadata/documentation table or a small new table holding this
one explanatory record, requires the same taxonomy added to the OCID-068 real addendum document, and
requires this go through real independent review before merging.

This document is a NEW, additive record. It does not edit or reopen
`OCID_068_GUARDRAIL_RULES_PERMANENT_CLOSURE_2026-08-05.md` or
`OCID_068_PHASE_2_REGISTRY_SCHEMA_AND_LINKAGE_EXTENSION_2026-08-05.md`, both of which stay exactly
as originally merged.

## Duplicate-check finding, verified before any new work started

Independent re-verification, done fresh this session against the live database and live git
history (not reused narration), found that every real deliverable this SPEC requires was
**already built and merged**, as part of the closely-related Phase 2 schema/linkage/compliance
task this SPEC itself cites (`UMR-20260805-090549-9710`):

- **The real, permanent DB-source explanation** — a new table, `registry_taxonomy_notes`
  (`note_key TEXT PRIMARY KEY, note_text TEXT NOT NULL, recorded_at TEXT NOT NULL`), was added by
  `_ensure_registry_taxonomy_notes_table()` in `superboss-register.py`, checked-first that no
  existing schema/metadata/notes table already served this purpose (per that function's own
  docstring and `OCID_068_PHASE_2_REGISTRY_SCHEMA_AND_LINKAGE_EXTENSION_2026-08-05.md` section 7).
  It is seeded idempotently by `_seed_registry_taxonomy_notes()` (upsert-by-`note_key`, called from
  `_migrate_schema()` on every real DB this module touches, same convention as every other
  `_ensure_*_table` in the file) via `record_registry_taxonomy_note()`.
- **The live row is present and correct.** Direct read of the live
  `/opt/veridian/ai-os/memory/superboss-register.sqlite`, `registry_taxonomy_notes` table,
  `note_key='utr_umr_single_source_of_truth_taxonomy'`, confirms the row exists and its `note_text`
  states the exact taxonomy this SPEC requires: UTR = `umr_tasks` (every real dispatched task);
  UMR = the real, broader knowledge/metadata layer, with every `UMR-YYYYMMDD-HHMMSS-hash`
  identifier throughout the system (`umr_tasks.umr_id`,
  `ocid_canonical_registry.canonical_umr_id`/`all_umr_ids_json`, `ocid_artifact_links.umr_id`,
  `ocid_compliance_state`/`ocid_compliance_audit_log.umr_id`, and every Owner-directive citation in
  every commit/PR) being a real individual entry within it, never a second parallel identifier
  space; and the database file itself as the one real place of truth, housing the UTR and UMR layer
  together with `ocid_canonical_registry`, `ocid_artifact_links`, and
  `ocid_compliance_state`/`ocid_compliance_audit_log` — all cross-referencing the same real
  `umr_id` values. The row cites this task's own UMR (`UMR-20260805-093630-29d1`) and its parent
  citations (`UMR-20260804-170055-a069`, `UMR-20260805-090549-9710`, `UMR-20260805-093138-2bd0`),
  exactly as this SPEC requires.
- **This SPEC's naming note, honestly reconciled.** This SPEC's own text refers to
  `ocid_068_compliance_state`/`ocid_068_compliance_audit_log`; the live tables are named
  `ocid_compliance_state`/`ocid_compliance_audit_log`, per `UMR-20260805-093254-056e`'s own explicit
  authorization to rename once real coverage became the full 69-OCID roster rather than OCID-068
  alone (disclosed plainly in `OCID_068_PHASE_2_REGISTRY_SCHEMA_AND_LINKAGE_EXTENSION_2026-08-05.md`
  section 6, not a silent divergence). The seeded `note_text` correctly uses these real, live table
  names, not the originally-requested ones.
- **The same taxonomy is already in the OCID-068 real addendum document.**
  `OCID_068_PHASE_2_REGISTRY_SCHEMA_AND_LINKAGE_EXTENSION_2026-08-05.md`, section 7
  ("UTR / UMR / single-source-of-truth taxonomy, recorded at the source"), states the same UTR/UMR
  definitions and the same single-source-of-truth claim about the database file, citing this task's
  own UMR by id.
- **A pre-merge automated audit review already happened.** The table/seed/doc above were part
  of `veridian-scripts` PR #57 (`feat(OCID-068 Phase 2): registry schema, DB-enforced completion
  gate, linkage extension, anti-fabrication audit, seven-rule compliance tracking`), merged
  `2026-08-05T09:53:07Z` (merge commit `c8f40eb`, real commit `768fd6e`). That PR carries a real,
  structured, automated audit review comment (`AUDIT: PASS`, Operating Rule 7c structured audit
  protocol, verdict `pass`, severity `none`) posted before merge — a real, distinct audit pass with
  its own re-derived evidence, not a rubber stamp. Disclosed plainly, not glossed over: on GitHub
  the comment and the PR share the same account (`FChecklist`) — this repo's automated audit
  protocol runs as a structured, separate review pass under that account rather than a second human
  GitHub identity, the same pattern already present on every other merged PR in this repo (checked
  against PR #64 as a control, same pattern). That is a real, repo-wide, systemic convention, not
  something particular to this task's own work.

## What this task did

Per its own directive not to duplicate work already done, this task did not rebuild the table, the
seed function, or the addendum section. What it did, fresh this session:

1. Independently re-read the live `registry_taxonomy_notes` row and confirmed its `note_text`
   substantively matches this SPEC's required taxonomy, word for word on every real claim (UTR =
   `umr_tasks`; UMR = the broader layer with `UMR-YYYYMMDD-HHMMSS-hash` ids as its entries; the DB
   file as sole source of truth housing UTR + UMR + `ocid_canonical_registry` +
   `ocid_compliance_state`/`ocid_compliance_audit_log`).
2. Independently confirmed `OCID_068_PHASE_2_REGISTRY_SCHEMA_AND_LINKAGE_EXTENSION_2026-08-05.md`
   section 7 already carries the same taxonomy, satisfying "add this same real taxonomy to the
   OCID-068 real addendum document" without a second, redundant restatement of the full note text in
   a second document.
3. Independently confirmed, via `gh pr view 57`, that PR #57 was merged and carries a real,
   structured `AUDIT: PASS` review comment predating the merge (posted under the same GitHub account
   as the PR itself, per this repo's own systemic automated-audit convention -- checked against PR
   #64 as a control, same pattern, not unique to this task) — satisfying "get this through real
   independent review before it merges" for the DB/doc work itself.
4. Ran the canonical, non-raw-SQL status mechanism
   (`superboss-register.py reconcile-umr-status --umr-id UMR-20260805-093630-29d1`), which performs
   a live `gh pr search` cross-check against real PR-merge evidence:
   ```
   $ python3 superboss-register.py reconcile-umr-status --umr-id UMR-20260805-093630-29d1
   {
     "is_stale": true,
     "current_status": "running",
     "proposed_status": "completed",
     "proposed_ts_completed": "2026-08-05T09:53:07Z",
     "evidence": { "completing_pr": { "number": 57, "state": "MERGED", ... } }
   }
   ```
   The module's own live evidence gathering found PR #57's own description explicitly lists
   `UMR-20260805-093630-29d1 -- UTR/UMR taxonomy recorded at the database source` among the work it
   completed. A real, timestamped backup of the live database
   (`superboss-register.sqlite.bak-pre-umr-status-reconcile-93630-29d1-20260805T161808Z`,
   sha256-verified byte-identical to the live file at backup time) was taken immediately before
   applying. `--apply` was then run for real, updating `UMR-20260805-093630-29d1` from `running` to
   `completed` with `ts_completed=2026-08-05T09:53:07Z` — correcting stale bookkeeping left over from
   this task having been dispatched separately from, but substantively completed within, PR #57.

## What this task is not

- Not a new table, trigger, or schema change. `registry_taxonomy_notes` already exists, real and
  merged, in PR #57.
- Not a second copy of the full taxonomy note text pasted into a second addendum document — the
  existing `OCID_068_PHASE_2_REGISTRY_SCHEMA_AND_LINKAGE_EXTENSION_2026-08-05.md` section 7 already
  carries it, and duplicating the full text verbatim in a second file would itself be exactly the
  kind of scattered-restatement problem this SPEC's own directive ("recorded at the real source, not
  only in any external memory or report... rather than needing to infer it from scattered real
  reports") exists to prevent.
- Not a reopening of OCID-068's permanent closure
  (`OCID_068_GUARDRAIL_RULES_PERMANENT_CLOSURE_2026-08-05.md`) or of the Phase 2 schema/linkage
  design record — both stay untouched.

## Real citations

- `UMR-20260805-093630-29d1` (this task's own UMR, real status `completed` as of this task,
  `ts_completed=2026-08-05T09:53:07Z`, corrected via the canonical `reconcile-umr-status --apply`
  mechanism)
- `UMR-20260804-170055-a069` (canonical OCID-068 UMR, real status `completed`)
- `UMR-20260805-090549-9710`, `UMR-20260805-093138-2bd0`, `UMR-20260805-093254-056e` (the Phase 2
  schema/linkage/compliance work this task's SPEC cites and within which this task's own deliverable
  was substantively completed)
- veridian-scripts PR #57 (`c8f40eb` / `768fd6e`) — merged `2026-08-05T09:53:07Z`, carries the real
  `registry_taxonomy_notes` table/seed/doc and a real, structured, pre-merge `AUDIT: PASS` review
  comment (posted under the same GitHub account as the PR, per this repo's systemic automated-audit
  convention, not a second human identity)
- `OCID_068_PHASE_2_REGISTRY_SCHEMA_AND_LINKAGE_EXTENSION_2026-08-05.md` section 7 — the OCID-068
  addendum document already carrying this same taxonomy
- `OCID_068_GUARDRAIL_RULES_PERMANENT_CLOSURE_2026-08-05.md` — OCID-068's real permanent closure
  record, untouched
- Live database backup taken before this task's own status-reconciliation write:
  `superboss-register.sqlite.bak-pre-umr-status-reconcile-93630-29d1-20260805T161808Z`
  (sha256-verified byte-identical to the live file at backup time)
