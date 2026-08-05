# PROGRESS -- task-20260805-161157-close-a-real-fabrication-loophole--not-a

## Duplicate-check finding (real, disclosed up front)

This task's directive is, in substance, a near-verbatim restatement of
Owner directives already dispatched and already fully implemented/merged
earlier the same day: `UMR-20260805-092408-4f97` (extending
`UMR-20260805-090549-9710` / `UMR-20260805-091934-86a2`), merged via PR #57
(`768fd6e`). The `audit_raw_output` column, the trigger-computed
`not_applicable_confirmed` gate, the 7 `has_real_*`/`is_fully_complete`
gates, `audit_ocid_canonical_registry.py`, and tests proving determinism +
bypass-resistance all already existed on `main` before this task started.
Per this repo's own established precedent (OCID-069 redispatch duplicate
check), this task does **not** start a second parallel implementation --
it re-verifies the existing real work, then does the one real thing this
task's own SPEC asks for that had genuinely never been done: **actually
executing** the audit script for real against live production data (every
prior run was either `--dry-run` or against a scratch test DB --
`audit_raw_output` was NULL on all 69 live rows before this task).

**This task itself has no discoverable real UMR ID**: a direct query of
live `umr_tasks` found no row matching this task's identity. Rather than
invent a plausible-looking `UMR-YYYYMMDD-HHMMSS-hash` citation, all new
code/tests/docs cite it honestly by its real task-directory identifier
(`task-20260805-161157-close-a-real-fabrication-loophole--not-a`) with that
caveat stated -- fitting, given the task's own subject.

## Completed
- [x] Re-verified all pre-existing real work; 16/16 test files pass
- [x] Confirmed live DB had `audit_raw_output` NULL on all 69 rows -- never
      actually `--apply`'d against production before this task
- [x] **Real finding #1**: production `umr_tasks` rows carry multi-MB
      `metadata_json` (separate pre-existing dedup-engine data-quality
      issue, not caused by this task) that method (b) legitimately matches
      -- would have added an estimated 1-2+ GB to the live DB in one
      `--apply`. **Fixed**: deterministic, disclosed, identical-per-OCID
      `_bounded_for_storage()` char cap (5000 chars/leaf value), not
      interpretation. 2 new tests.
- [x] **Real finding #2 (more serious)**: a live, unattended full-text
      re-run's "otherwise use fresh result in full" fallback would have
      SILENTLY OVERWRITTEN real, carefully-reasoned existing rows --
      live-verified: OCID-001's historical `rejected_duplicate` judgment
      would have been replaced by a spurious match against an unrelated
      batch-registration UMR that merely enumerates "OCID-001 through
      OCID-068" in its own prompt text; OCID-007/011 (real, honestly
      confirmed `not_found`) would have been flipped to a false "found"
      by matching this very task's own meta-discussion text. **Fixed**:
      `plan_for_ocid()`'s merge rule rewritten -- existing
      canonical_umr_id/status/all_umr_ids/pr_number/pr_repo/not_found are
      now ALWAYS preserved for every real existing row, no exception;
      `audit_raw_output` is always refreshed; any disagreement is appended
      (never replacing prior reasoning) as an explicit, idempotent NEEDS
      HUMAN REVIEW note. Rewrote the test that encoded the old (now-proven
      unsafe) behavior; added an idempotence proof.
- [x] Caught and fixed a fabricated-looking self-citation in my own new
      code before it could be written to the live DB (killed an in-flight
      `--apply` run mid-computation, before any write occurred, once this
      was found) -- replaced with an honest task-directory citation.
- [x] Full 16-file test suite passes after all fixes
- [x] Live production DB backed up:
      `superboss-register.sqlite.bak-pre-audit-raw-output-live-apply-20260805T163658Z`
- [x] Committed + pushed WIP

## Remaining
- [ ] Re-run the live `--apply` across all 69 OCIDs with the corrected script
      (in progress / re-launching)
- [ ] Confirm post-apply: all 8 real not_found rows earn
      `not_applicable_confirmed=1`; no existing canonical choice silently changed
- [ ] Spot-check DB size delta is bounded (expect low tens of MB, not GB)
- [ ] Write/extend the OCID-068 addendum documenting this task's real
      findings + the permanent "no boolean/claim ever hand-set" restatement
      (existing addendum Section 5 already states this near-verbatim --
      confirm/extend, don't duplicate)
- [ ] Independent review before merge
- [ ] Final commit + push, open PR
