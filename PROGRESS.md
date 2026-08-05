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

## Completed
- [x] Re-verified all 4 test files covering this area pass (16/16 repo-wide test files pass)
- [x] Confirmed live DB (`/opt/veridian/ai-os/memory/superboss-register.sqlite`) had
      `audit_raw_output` NULL on all 69 rows -- the real script had never actually
      been `--apply`'d against production data before this task
- [x] **Real, serious finding from the first live run**: real production `umr_tasks`
      rows now carry multi-MB `metadata_json` (a separate, pre-existing dedup-engine
      data-quality issue, not caused by this task) that method (b)'s full-table grep
      legitimately matches -- would have added an estimated 1-2+ GB to the live DB
      in one `--apply` run. Fixed with a deterministic, disclosed, identically-applied
      per-OCID char cap (`_bounded_for_storage()`), not interpretation/narrative.
- [x] **Second, more serious real finding**: for the real, already-honestly-confirmed
      `not_found` OCIDs (007-014), a fresh live run's method (b) full-table grep
      matches this task's *own meta-dispatch UMRs* (which merely *discuss* "OCID-007
      through OCID-014 not_found" in their prompt text) as if they were real evidence
      FOR OCID-007 -- flipping `not_found=True` to a false `status=multiple_umr_ids_found_needs_review`
      / `has_real_umr=1`. Applying this blindly would have fabricated exactly the kind
      of false boolean this whole task exists to prevent. **Investigating a deterministic
      fix before any live `--apply` run** (see Remaining).
- [x] Added `_bounded_for_storage()` + doc + tests (2 new tests,
      `tests/test_audit_ocid_canonical_registry.py`, now 6/6 passing)

## Remaining
- [ ] Design + implement a deterministic (no AI judgment) guard against the
      self-referential-contamination false positive found above, before
      running any live `--apply`
- [ ] Full dry run across all 69 real OCIDs against live data, inspect every
      proposed change by hand before applying anything
- [ ] `--apply` for real against the live production DB (fresh, current
      evidence for every OCID, per this task's SPEC)
- [ ] Confirm post-apply: all 8 real not_found rows correctly earn
      `not_applicable_confirmed=1` with genuine, bounded, non-contaminated evidence
- [ ] Add/extend automated tests proving the boolean cannot be set true
      through any path that doesn't call this real script (mostly already
      covered by existing tests -- confirm coverage of the new contamination guard)
- [ ] Update the OCID-068 addendum: plain statement that no boolean/completion
      claim may ever be hand-set or narrated again (existing Section 5 already
      says this near-verbatim -- confirm/extend, don't duplicate)
- [ ] Independent review before merge
- [ ] Commit + push, open PR
