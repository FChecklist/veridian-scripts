# PROGRESS -- task-20260805-151450-standardize-and-backfill-evidence-json-s

Real dispatch: standardize evidence_json's required shape for
ocid_canonical_registry rows going forward, enforce it as a real gate
inside the OCID Master Standard v6 audit/certification refusal machinery
(PR #54), and backfill the 69 real existing rows honestly (real
commit_sha/file_path recovered from cited PR numbers where possible, null
where genuinely not recoverable). Citing UMR-20260804-170055-a069 and
UMR-20260805-032326-becc.

## Completed
- [x] Independently confirmed the real gap against the live DB: OCID-068
      Phase 2's dedicated commit_sha/file_name/file_path/merge_status/
      evidence_summary columns exist (PR #57) but were never actually
      backfilled -- all 69 rows had them NULL; evidence_json itself remains
      a free-form per-search-method text dump with no fixed shape.
- [x] Defined `EVIDENCE_JSON_REQUIRED_KEYS` (commit_sha, file_name,
      file_path, merge_status, umr_id, ocid_number, pr_number, pr_repo,
      evidence_summary) and `validate_evidence_json_schema()` in
      superboss-register.py.
- [x] Added `_status_claims_verified_or_completed()` -- real, deterministic
      whole-word/non-negated detector, independently verified against all
      69 real existing rows (11 real matches, correctly excludes the 2 real
      false-positive traps: "running, never completed" / "ts_completed=
      null"+"NOT_VERIFIED").
- [x] Added `refuse_ocid_registry_completion_if_evidence_incomplete()` --
      pure, zero-I/O, second independent gate, alongside (not replacing)
      `refuse_certification_if_merged_without_required_checks()` from PR 54.
- [x] Wired the gate into `upsert_ocid_canonical_registry()` itself: refuses
      (raises `OcidEvidenceSchemaRefused`, writes nothing, records a real
      'evidence_schema_refused' audit event via
      `record_ocid_master_standard_audit_event()`) whenever a row's status
      genuinely claims completed/verified and evidence_json is incomplete.
      Rows not claiming completion are unaffected.
- [x] Added `tests/test_evidence_json_schema_gate.py` (11 real tests).
- [x] Fixed 3 already-merged tests whose seed fixtures used status=
      "completed" with legacy-shape evidence (test_audit_ocid_canonical_registry.py
      x2, test_ocid_canonical_registry.py x1) -- updated to schema-complete
      evidence, preserving each test's original assertions/intent.
- [x] Full suite green: 113 passed.
- [x] Committed + pushed the schema/gate change.

- [x] Wrote backfill_evidence_json_schema.py. Discovered mid-task, live: a
      separate, already-real, concurrently-running backfill
      (backfill_ocid_registry_phase2_columns.py, OCID-068 Phase 2, PR #57)
      had just actually executed against the same live production DB and
      genuinely populated the dedicated commit_sha/file_name/file_path/
      merge_status/evidence_summary columns for all 69 rows (49/69
      commit_sha, 23/69 file_path, 57/69 merge_status, 69/69
      evidence_summary, honest nulls elsewhere). Rather than duplicate
      that real `gh pr view` recovery a second time, this script reads
      those now-real dedicated columns directly and folds them into the
      new standardized evidence_json shape (9 required keys + this row's
      own pre-existing evidence_json preserved verbatim under
      "legacy_evidence") -- zero duplicate implementation, per this
      codebase's own convention.
- [x] Took a real pre-write DB backup
      (superboss-register.sqlite.bak-pre-evidence-json-schema-backfill-20260805T152822Z).
- [x] Dry-run reviewed the full 69-row plan; every row validated clean
      against the new schema; 0 "never fetched by Phase 2 backfill"
      warnings.
- [x] Applied the backfill to the live DB (--apply). Verified after:
      all 69 rows schema-compliant; the 11 rows whose status genuinely
      claims completed/verified (OCID-002, 003, 038, 047-052, 068, 069)
      all pass the enforced gate.
- [x] Full suite green: 113 passed, after the live backfill.
- [x] Committed + pushed the backfill script + PROGRESS.md.
- [x] Opened PR #64 citing UMR-20260804-170055-a069:
      https://github.com/FChecklist/veridian-scripts/pull/64
- [x] Real independent review pass (this cycle) found and fixed a CONFIRMED
      severe bug before merge: the refusal path's own `with _write_lock():`
      around its audit-log insert deadlocked (independently reproduced with
      a real `timeout` wrapper -- hung indefinitely) when
      upsert_ocid_canonical_registry() is called from inside a caller that
      already holds `_write_lock()` -- which is how every current real
      production call site invokes it (audit_ocid_canonical_registry.py,
      backfill_ocid_registry_phase2_columns.py,
      backfill_evidence_json_schema.py, this file's own CLI command).
      flock() is per-open-file-description, not re-entrant. Fixed: the
      refusal path no longer self-locks, matching this function's own
      established "caller owns the transaction/lock" convention (same as
      its main INSERT). Added a real regression test (subprocess + hard
      timeout) that independently reproduces the pre-fix hang and confirms
      the fix. Also documented a known, real, un-fixed minor limitation of
      the negation regex (hyphen/no-separator/multi-word negation forms
      aren't excluded -- none of the 69 real rows use those forms today).
- [x] Full suite green after the fix: 114 passed.
- [x] Committed + pushed the fix.

- [x] Merged: https://github.com/FChecklist/veridian-scripts/pull/64
      (merge commit d21659e123e7650ec8ec47e55f5b788412b456a2).

## Remaining
- [ ] None. Task complete.
