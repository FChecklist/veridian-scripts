# OCID-020 GTM schema "standalone highest priority" task -- live-state verification

Task: `task-20260805-185211-ocid-020-gtm-schema-build--standalone-to`
Relates to: UMR-20260802-165606-4413 (OCID-020), UMR-20260805-131542-121f, UMR-20260805-142048-4edb,
UMR-20260805-093138-2bd0.

Per the standing memory on this repo's task-dispatch pipeline (urgent PM SPECs have repeatedly
carried confident, specific, "real"-laden claims that don't match live state -- see
`a901898`, `5ec37bf`/PR#83, and the PR#89/#90 OCID-020 GTM cases), every claim in this cycle's
SPEC was independently re-verified against live state (direct `psql` against the production
Supabase DB, direct `sqlite3` against the live `superboss-register.sqlite`, `gh` against both
`veridian-scripts` and `compliance-tracker`) before any write was made. **Result: item 1 is
accurate; items 2 and 3 are false relative to live state. No schema-build, no DB write, and no
"unstick" action was performed by this task.**

## Item 1 -- PR #959 (compliance-tracker): confirmed real, held as instructed

- Confirmed via `gh pr view 959 --repo FChecklist/compliance-tracker`: **OPEN**, **MERGEABLE**,
  branch `fix/broader-preauth-brand-tagline-footer-ocid020`, all CI checks pass (Lint, Type
  Check, Unit Tests, E2E Tests, Build, Security Pattern Check, Secret Scanning, etc.) except
  `Vercel` (`fail`, "Deployment rate limited" -- a preview-deploy quota issue, not a code
  failure). `reviewDecision: REVIEW_REQUIRED`, **0 reviews so far** -- not adopted, not merged.
- Files touched match the SPEC: `src/app/{pricing,contact,terms,privacy}/page.tsx` (+ their
  `.test.ts`), `src/components/LegalShell.tsx`, `src/app/login/login-form.tsx` (tagline/footer
  wiring), `src/lib/services/org-branding-service.ts` (+test), `src/lib/db/schema.ts`, and one
  migration: `drizzle/0313_preauth_brand_footer_column.sql`.
- **Real independent review of the migration file, as instructed, before any adoption/apply:**
  ```sql
  ALTER TABLE platform.product_branches ADD COLUMN IF NOT EXISTS footer text;
  ```
  Single nullable `text` column add, `IF NOT EXISTS`-guarded (idempotent, safe to re-run),
  reuses the existing `product_branches` table (no new parallel brand-config source), matches
  the same-file precedent of `0312_stage1_preauth_brand_host_lookup.sql`. `schema.ts`,
  `org-branding-service.ts`, and both test files were read in full and are internally
  consistent with the migration (new `footer: text('footer')` field; resolver now selects
  `tagline`/`footer` columns and returns them as nullable, never fabricating copy when NULL).
  This is a low-risk, additive, backward-compatible schema change -- but it is a live
  production-schema write, so per the SPEC it is **held, not applied, by this task**.
  Independently confirmed via direct `psql` against the live production DB
  (`platform.product_branches`) that the `footer` column **does not exist yet** -- the PR's own
  disclosure ("not yet applied ... this session is a fresh, isolated clone with no
  DATABASE_URL/Supabase credentials") is accurate and matches live reality. Decision to
  merge/apply is left to the normal review process (a human or DB-credentialed reviewer
  approval), consistent with not repeating the PR #954 auto-adoption pattern.
- **No merge, no approval, no `ALTER TABLE` was issued by this task.**

## Item 2 -- UMR-20260805-093138-2bd0: NOT stalled, already terminal

Direct query against the live `umr_tasks` table in
`/opt/veridian/ai-os/memory/superboss-register.sqlite`:

| umr_id | status | ts_submitted | ts_completed | reason (truncated) |
|---|---|---|---|---|
| UMR-20260805-093138-2bd0 | `rejected_duplicate` | 2026-08-05T09:31:38Z | 2026-08-05T16:12:00Z | superseded: OCID-068 ... already has real, newer evidence in `ocid_artifact_links` -- `umr_id='UMR-20260805-152250-55d3'` ... |

- Status is **`rejected_duplicate`**, not `queued`, and it has a real `ts_completed`
  (2026-08-05T16:12:00Z) -- it reached a terminal state **almost 3 hours before this task was
  even dispatched** (18:52Z).
- `ocid_compliance_state` (the table the SPEC claims has "zero real rows"): **113 rows**, not
  zero.
- This exact claim (zero rows / genuine stall) was already independently checked and disproved
  hours ago in `veridian-scripts` PR #90 ("`ocid_compliance_state` 'zero rows' / stalled worker:
  false -- 113 real rows exist; UMR-20260805-093138-2bd0 is a correct `rejected_duplicate`, not
  stalled"), itself already merged into `main` (`5e5ff3a`).
- **Diagnosis of "the real specific reason real work never started":** it isn't that work
  never started -- work completed and the task was correctly rejected as a duplicate of
  already-completed OCID-068 evidence. There is nothing to unstick. No restart/requeue action
  was taken (that would re-run already-completed, already-superseded work).
- Note: sibling task `task-20260805-185216-...-tier-bump-plus` (dispatched 5s after this one,
  in progress) carries a *different* framing of this same UMR ("position nine of thirty four in
  a real FIFO... bump tier one to tier zero"), which itself doesn't fully match the terminal
  `rejected_duplicate` status found here. That reconciliation belongs to that sibling task's own
  scope, not repeated here -- flagged for the next cycle in case the two findings need
  reconciling.

## Item 3 -- GTM 25-category schema: already built, populated, and actively maintained

This is the SPEC's own "most important" claim, and it is the most clearly false one.

Direct query against the live `superboss-register.sqlite`:

- Table `gtm_certification_categories` **already exists** with **exactly 25 rows** (one per
  real GTM category), schema: `category_index, category_name, ocid_number, parent_umr_id,
  child_umr_id, passed, evidence_summary, evidence_json, fix_commit, fix_file_path,
  fix_pr_number, validated_at, created_at, last_updated_at`.
- **Every row** has `parent_umr_id = 'UMR-20260802-165606-4413'` (exactly the parent UMR named
  in this SPEC) and a non-null `child_umr_id` (`UMR-20260805-142958-ddd8` for all 25 rows in the
  base build; several categories have since been advanced by further child work).
- `last_updated_at` values range up to **2026-08-05T18:26:13Z -- 26 minutes before this task's
  own dispatch (18:52:11Z)**. This table is not stalled or deprioritized; it is the single most
  recently and actively written table in the whole system at dispatch time.
- **Real commit / file / PR for this schema, as requested:**
  - `veridian-scripts` commit `7c3e7c5` -- "feat: gtm_certification_categories schema, 25 real
    rows under OCID-020", file `migrate_2026-08-05_gtm_certification_categories.py`, branch
    `feat/gtm-certification-categories-schema-umr20260805145042`, **PR #62 (OPEN)**.
  - Follow-up commit `5a775f0` -- "fix: revert narrated governance-testing passed=1 to honest
    pending" (same file), addressing a boolean-narration correctness concern on category 14.
  - Both commits are real, present in `veridian-scripts` git history, not yet merged to `main`
    (open PR #62) but their DB effects were already applied live per each commit's own message
    and independently confirmed by the direct `sqlite3` read above.
- **No new table, no new rows, and no schema migration were created by this task** -- doing so
  would have produced a duplicate/conflicting definition of an already-live, already-populated
  table.

### Critical concurrency-risk finding (not requested, but real and urgent)

Four other tasks were dispatched in the same batch, seconds apart
(`ls /opt/veridian/ai-os/tasks/ | grep 20260805-18`):

- `task-20260805-185156-...-register-chi` (PR #954 child-UMR registration)
- `task-20260805-185202-...-fix-pre-auth` (PR #959's own child-UMR)
- `task-20260805-185207-ocid-020-gtm-certification--build-the-re` -- **in progress**, whose own
  `prompt.txt` states verbatim: *"real independent verification confirmed zero tables named for
  gtm/certification/OCID-020 exist yet"* -- the **identical false premise** as this task's SPEC,
  and it is actively trying to build a new schema/table for the same purpose right now.
- `task-20260805-185216-...-tier-bump-plus` -- **in progress**, whose own `prompt.txt` instead
  states *"real independent verification found the new real gtm_certification_categories table,
  twenty five real rows ... confirmed"* -- i.e. **directly contradicts task 185207 (and this
  cycle's own SPEC) within the same dispatch batch**, and correctly matches live reality as
  independently confirmed here.

This DB (`superboss-register.sqlite`) already has one documented same-day incident,
`superboss-register.sqlite.bak-ACCIDENTAL-PREMATURE-SCHEMA-CHANGE-pre-revert-20260805T091815Z`,
of a premature schema change needing a revert. If `task-20260805-185207` proceeds to
`CREATE TABLE` a duplicate `gtm_certification_categories`-equivalent under its own false premise,
it risks a real collision on this exact table. This is flagged here for the next PM/Owner
decision cycle to reconcile (same pattern as PR #90's own flagged-not-unilaterally-resolved
PR #954/#965 duplicate-dispatch collision); it was not resolved unilaterally by this task since
this task has no authority to stop a sibling systemd-managed worker.

## Summary

| Item | SPEC claim | Live-state finding | Action taken |
|---|---|---|---|
| 1: PR #959 | real, open, migration not yet applied | **Confirmed true** | Reviewed migration file; held, not merged/applied |
| 2: UMR-093138-2bd0 | queued 5+ hrs, zero rows, genuine stall | **False** -- `rejected_duplicate`, terminal since 16:12Z, 113 rows exist | None (nothing to unstick); documented specific reason |
| 3: 25-category GTM schema | zero new tables across 4 cycles | **False** -- table exists, 25/25 rows, updated 26 min before dispatch | None (no duplicate build); cited real commit/PR/file/row-count; flagged live sibling-task race risk |

🤖 Generated with [Claude Code](https://claude.com/claude-code)
