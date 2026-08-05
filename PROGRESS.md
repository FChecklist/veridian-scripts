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

## Remaining
- [ ] Write backfill_evidence_json_schema.py: for each of the 69 real
      existing rows, real `gh pr view` fetch against its own cited
      pr_number/pr_repo to recover commit_sha/merge_status/file_path;
      honest null wherever genuinely unrecoverable (no pr_number, gh
      failure, or an ambiguous multi-file PR with no unambiguous single
      primary artifact); build the new structured evidence_json (9
      required keys + preserved legacy free-text evidence nested
      alongside); dry-run review before --apply.
- [ ] Apply the backfill to the live DB; verify all 69 rows carry the full
      evidence_json schema and the gated "completed" rows pass the gate.
- [ ] Commit + push the backfill.
- [ ] Open the real PR citing UMR-20260804-170055-a069, get independent
      review, merge.
