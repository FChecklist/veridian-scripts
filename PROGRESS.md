# PROGRESS -- task-20260805-172731-build-a-real-deterministic-deposit-and-r

## Completed
- [x] Surveyed existing OCID-068 infrastructure: `ocid_canonical_registry` (deposit via
      `upsert_ocid_canonical_registry`, compute via `ocid_canonical_registry_completion_ai/_au`
      SQLite triggers deriving `has_real_*`/`is_fully_complete`/`not_applicable_confirmed`) and
      `ocid_compliance_state`/`ocid_compliance_audit_log` (deposit via
      `record_ocid_compliance_audit`, compute via `ocid_compliance_state_derive_ai/_au` triggers
      deriving all 7 `rule_*_verified` booleans) already implement the real deposit/compute
      separation this task's spec calls for -- confirmed no caller-settable path exists for any
      of these booleans (re-confirmed by existing test suites, all still passing).
- [x] Added `query_ocid_compliance_state()` (superboss-register.py) -- real, read-only lookup of
      the already-trigger-computed `ocid_compliance_state` rows; zero writes, zero re-audit.
- [x] Added the real **report command**: `audit_ocid_compliance.py --report` -- read-only, no
      gh/git subprocess calls, no writes; prints the current compliance analysis (all 7 rules +
      completion booleans) verbatim via `build_compliance_report()`, so the PM runs this flag and
      reports its output directly instead of interpreting anything itself.
- [x] Fixed `audit_ocid_canonical_registry.py`'s real terminal-output ambiguity (the exact root
      cause of this cycle's false data-corruption alarm): every line in both modes is now tagged
      `[DRY_RUN]`/`[APPLY]`, wrapped in an explicit mode banner, and stdout's JSON is wrapped in a
      `{"mode": ..., "wrote_to_database": ...}` envelope. Applied the same labeling fix to
      `audit_ocid_compliance.py`'s dry-run/apply paths for consistency (zero-exception guardrail).
      Extracted the formatting into pure, directly-testable functions
      (`format_mode_banner`/`format_ocid_line`/`format_summary_line`/`format_changed_line`/
      `format_completion_line`/`format_stdout_envelope`).
- [x] Added real automated tests:
  - `tests/test_audit_ocid_canonical_registry.py::test_dry_run_and_apply_terminal_output_are_unambiguously_labeled`
    -- proves every real line in both modes carries its own explicit tag, the two modes' lines are
    never identical, and the stdout envelope alone (no stderr) disambiguates mode.
  - `tests/test_audit_ocid_compliance_report.py` (new) -- proves `--report` reads back real
    trigger-computed state verbatim, never fabricates a pass for an unaudited pair, and (the
    spec's explicit compute-determinism requirement) that the real compute step
    (`query_ocid_compliance_state`) and the `--report` output are byte-identical across two
    separate real runs against the same unchanged data.
  - All pre-existing test suites (18 files) re-run and still pass; zero regressions.
- [x] Consolidated into the two existing scripts (`audit_ocid_canonical_registry.py`,
      `audit_ocid_compliance.py`) per zero duplication -- no third parallel script built.
- [x] Independently confirmed (real `gh api` call, not narrated) UMR-20260805-112247-3ad0: the
      real, live `compliance-tracker` `main` branch protection `required_approving_review_count`
      is currently `1` (restored) and `enforce_admins` is `true`. Recorded in
      `UMR_20260805_112247_3ad0_BRANCH_PROTECTION_REVERIFICATION_2026-08-05.md`.

- [x] Committed, pushed, and opened PR for independent review before merge (per Owner directive
      -- "sacrosanct infrastructure", must not merge without independent review):
      https://github.com/FChecklist/veridian-scripts/pull/82

## Remaining
- [ ] Await independent review/approval on PR #82 before merge.
