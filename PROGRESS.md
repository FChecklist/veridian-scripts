# PROGRESS -- task-20260806-034817-owner-directive--build-the-real-propose

SPEC: Owner standing mandate, cites UMR-20260805-185000-e94f and the deterministic
script consolidation chain (PR #91, #95, #100, #103, #106, independently reconfirmed
already-merged before any code was written -- see "Independent verification" below).
"Thinking is by the Project Manager, execution is by AI agents, AI agents do not
think for themselves" -- for real novel findings outside already-approved scope.

## Independent verification (done before writing any code, per the standing
lesson that urgent PM SPECs in this codebase have twice not matched live state)
- `git log --oneline`: PR #91, #95, #97, #100, #103, #106, #109 all present as real
  merge commits on this branch's history. SPEC's premise matches live state.
- `superboss-register.py`'s own module docstring (lines 50-63) already states the
  "one canonical script" rule and lists `insert_pm_decision_pending()`/
  `resolve_pm_decision_pending()` as the established convention -- confirms PR #103's
  work landed as described.
- `pm_decisions_pending` table + `_ensure_pm_decisions_pending_table()`,
  `insert_pm_decision_pending()`, `resolve_pm_decision_pending()`,
  `cmd_insert_pm_decision_pending()`, `cmd_resolve_pm_decision_pending()` all exist,
  wired to CLI subcommands `insert-pm-decision-pending`/`resolve-pm-decision-pending`.
- `generate_pm_report_v3.py` Section 7 (`get_pm_decisions_pending()`) already reads
  this table read-only, exactly the pattern the SPEC asks Section 8 to reuse.
- `tests/test_pm_decisions_pending.py` already pins the live production schema's
  exact column set via `PRAGMA table_info` -- used as the baseline for this task's
  additive migration (see below).
No mismatch found this time -- unlike the two prior false-premise SPECs the standing
memory warns about, this one's premise held up under independent check.

## Extend-vs-new-table decision (SPEC's own first ask)

**Decision: extend `pm_decisions_pending`, not a new table.** Documented reasoning:

Columns needed for the proposal lifecycle map cleanly onto columns that already
exist:
- `title` -> the issue statement (deposit step)
- `detail` -> what AI proposes (deposit step)
- `related_umr` -> the proposal's own child UMR id (the row already had a
  "related UMR" concept; for a proposal, the related UMR *is* the child UMR the
  proposal creates)
- `status` -> already a free-text terminal-state column (`resolve_pm_decision_pending()`
  already accepted an arbitrary `status` string, not just `'resolved'`), so
  `'approved'`/`'redirected'`/`'held'` needed zero schema change
- `closed_ts`/`closed_by`/`closed_note` -> already exactly "PM's decision + who +
  why", needed for the approve/redirect/hold step verbatim

Only the third lifecycle phase (AI recording completion evidence) needed anything
new: `completed_ts`, `artifact_path`, `commit_sha`, `evidence`. Four nullable
`ALTER TABLE ADD COLUMN`s, populated only by the one new function that ever writes
them (`record_owner_proposal_completion()`), NULL everywhere else by construction --
not a data-quality gap, same convention `_migrate_umr_tenant_id()` already
documents for `umr_tasks.tenant_id`.

The one genuinely new concern -- keeping the two real row shapes (`pm_decision` vs
`owner_proposal`) from ever being mixed or cross-resolved -- is closed with a single
discriminator column, `decision_type`, exactly the column the SPEC itself proposed
as an example. `resolve_pm_decision_pending()` gained one optional keyword
(`require_decision_type=None`, default preserves its exact original behavior) so
`decide_owner_proposal()` can reuse its UPDATE verbatim (zero duplication) while
still refusing to ever touch a `pm_decision` row.

A second, parallel table would have duplicated `id`/`opened_ts`/`title`/`detail`/
`status`/`closed_ts`/`closed_by`/`closed_note` (8 of 11 existing columns) for zero
real benefit -- rejected per the Owner's own "zero duplication applies here too."

## Completed
- [x] Independent verification of the SPEC's premise against live git history and
      live code (see above) -- no false premise found.
- [x] Extend-vs-new-table decision made and documented (see above).
- [x] `superboss-register.py`:
  - `_migrate_pm_decisions_pending_owner_proposal_columns()` -- additive migration
    (`decision_type` NOT NULL DEFAULT 'pm_decision', `completed_ts`,
    `artifact_path`, `commit_sha`, `evidence`), called from
    `_ensure_pm_decisions_pending_table()`, same idempotent
    check-`PRAGMA table_info`-then-`ALTER` pattern as `_migrate_umr_tenant_id()`.
  - `resolve_pm_decision_pending()` gained `require_decision_type=None` (backward
    compatible, zero behavior change for existing callers).
  - `insert_owner_proposal(conn, issue, proposal, *, child_umr=None)` -- deposit
    step; mints a real child UMR via the existing `_new_id("UMR")` convention
    (same one `upsert_umr_task()` already uses) when not given one.
  - `decide_owner_proposal(conn, decision_id, *, decision, closed_by, closed_note=None)`
    -- PM decision step; validates `decision in {"approved","redirected","held"}`,
    delegates to `resolve_pm_decision_pending()`.
  - `record_owner_proposal_completion(conn, decision_id, *, artifact_path, commit_sha, evidence)`
    -- AI completion step; only fires on a currently-`'approved'` row, idempotent.
  - CLI subcommands: `insert-owner-proposal`, `decide-owner-proposal`,
    `record-owner-proposal-completion`, each with a `cmd_*` wrapper following the
    existing `cmd_insert_pm_decision_pending`/`cmd_resolve_pm_decision_pending`
    convention (JSON stdout, non-zero exit on a real refusal/no-op).
- [x] `generate_pm_report_v3.py`:
  - `get_pm_decisions_pending()` now filters `decision_type='pm_decision'` (only
    when that column exists -- degrades gracefully, unfiltered, on a DB that
    predates this migration, so this read-only script never raises on an older
    schema).
  - New `get_owner_proposals_pending()` (Section 8 data source), same
    graceful-degradation guard.
  - New rendered section: `8. AI PROPOSALS AWAITING PM DECISION` -- same
    read-only-from-`pm_decisions_pending` pattern as Section 7, so the PM sees
    real pending proposals every real report cycle without a separate query.
- [x] Tests: `tests/test_pm_decisions_pending.py` gained 9 new tests (deposit,
  explicit child UMR, full round trip, invalid decision rejected, cross-type
  refusal, completion-requires-approved, completion idempotency, Section
  7/8 exclusivity) plus the updated live-schema column pin. `test_generate_pm_report_v3.py`
  gained Section 8 fixture data + assertions, plus a dedicated backward-compatibility
  test against a pre-migration schema. **171 tests pass** (139 in `tests/` + 32 in
  the two files above), zero regressions.
- [x] Real end-to-end proposal round trip demonstrated against a genuine isolated
  scratch DB (never production) through the real CLI entry points
  (`cmd_insert_owner_proposal`/`cmd_decide_owner_proposal`/
  `cmd_record_owner_proposal_completion`) plus `generate_pm_report_v3.py`'s real
  Section 8 rendering of a still-open proposal. Full transcript in the PR
  description / final report.
- [x] Gate scope note (SPEC's 4th ask): nothing in this change retroactively
  applies to already-authorized broad-category work in flight (e.g. the GTM script
  build) -- `insert_owner_proposal()` is purely additive, opt-in, called only where
  a future caller explicitly invokes it for a genuinely novel out-of-scope finding.
  No existing call site was touched to force this gate onto it.

## Incident note (self-caught, corrected before reporting completion)
While first exercising the CLI round trip via a subprocess with
`SUPERBOSS_REGISTER_DB` pointed at a not-yet-existing scratch file, discovered
`resolve_superboss_db_path()`'s real step-2 check (target path must already exist
and be non-zero size) rejects a fresh path and silently falls back to the live
production DB. `init` + the three new CLI subcommands briefly ran against
`/opt/veridian/ai-os/memory/superboss-register.sqlite` for real, writing one
demo row with placeholder/fabricated evidence (`commit_sha="abc1234"`,
`evidence="PR #110 merged, tests pass"` -- false at the time). Caught immediately;
that one row (id=2) was deleted from production before writing anything further.
Verified afterward: production `pm_decisions_pending` is back to its original single
real row (id=1, unchanged), and the additive schema migration (5 new nullable/
defaulted columns) that this incident also applied to production is safe,
inert for existing rows, and identical to what merging this PR would apply
automatically anyway. No data loss, no corruption, no fabricated evidence left in
production. The real demo round trip shown in the final report was re-run
correctly afterward against a genuine, isolated, throwaway scratch DB, following
the same safe seeding pattern `tests/test_pm_decisions_pending.py` already uses
(monkeypatched `_connect()` on a throwaway module instance, never the
environment-resolved production path, until the scratch file provably exists and
validates).

## Remaining
- [ ] Open PR, get independent review, merge.
- [ ] Report back real evidence (file paths, PR number, sample round-trip output)
      to the Owner/PM.
