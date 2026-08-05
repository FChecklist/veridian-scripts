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

---

# PROGRESS -- UMR-20260805-165906-0923 (child-umr-ocid020-gtm-remaining-8-category-scripts, OCID-020 GTM certification)

Executes the 8-category continuation task queued at UMR-20260805-165906-0923 (parent
UMR-20260802-165606-4413 / OCID-020), per PM instruction UMR-20260805-171657-01de. Categories
5, 6, 7, 9, 15, 16, 24, 25 -- 25 total categories, 13 already had real scripts before this task.
Before any DB write this session, independently confirmed (own grep, not assumed):
`gtm_write_category_result.py` and `superboss-register.py`'s `_connect()`/`_write_lock()` contain
zero references to `file_inventory` -- the one real, confirmed-corrupted table held under Hard
Rule 8 -- and no `PRAGMA integrity_check` was run against the whole database this session, only a
scoped, read-only probe confirming `file_inventory` alone still fails (`database disk image is
malformed`), matching the standing hold exactly. This task is being executed across several small
branches/PRs (one per natural category grouping, same pattern as the prior 13-category PRs). So
far this session (real, independently DB-verified after each write):
- category_index=5 (UI testing): pass. category_index=6 (end to end testing): pass. Branch
  feat/gtm-checks-ui-e2e-testing, PR #79.
- category_index=7 (regression testing): pass (fresh clone of compliance-tracker origin/main,
  bun test -> 2512 pass, 0 fail). Branch feat/gtm-checks-regression-testing, PR #81.
- category_index=9 (performance testing): pass (real lighthouse score 0.91). category_index=24
  (lighthouse audit): pass (real scores, all categories >= 0.5). Branch
  feat/gtm-checks-performance-lighthouse, PR #84.

## Completed (this branch)
- [x] category_index=15 (multi tenant testing) and category_index=16 (role permission testing):
      **blocked**, real, deliberately -- both genuinely require an authenticated session on the
      live product to test cross-tenant isolation / per-role permission boundaries.
      `gtm_check_multi_tenant_testing.py` / `gtm_check_role_permission_testing.py` do NOT hardcode
      "always blocked": every real run genuinely checks (1) environment variables plausibly naming
      a GTM/tenant/role test credential, (2) compliance-tracker's real `.env.local` key names for
      an Owner-provisioned test identity, (3) a real, bounded-depth (max depth 3, not an unbounded
      recursive glob -- confirmed this session that an unbounded `glob.glob(..., recursive=True)`
      over `/opt/veridian/ai-os/**` does not return within 15s) filename scan under
      `/opt/veridian/ai-os` for an explicit Owner go-ahead doc. All three checks came back
      genuinely empty this run (see evidence_json on each row for the full checked list: 0 env var
      matches, 13 real `.env.local` keys scanned with 0 matches, 0 owner-doc matches). Per the
      standing absolute rule (never enter a password/credential into any login/signup field, no
      exceptions, no case-by-case judgment), both are `--result blocked` citing exactly that rule
      -- neither script ever attempted a login.
- [x] Both results independently re-verified by reading the `gtm_certification_categories` rows
      (category_index 15 and 16) back directly from `/opt/veridian/ai-os/memory/superboss-register.sqlite`:
      `passed IS NULL` and `validated_at IS NULL` on both, matching the blocked semantics exactly
      (never trusted from script stdout alone).
- [x] Branch: feat/gtm-checks-tenant-role-blocked.

## Remaining (this task, so far)
- [ ] category_index=25 (production readiness synthesis, built last, depends on all others above)
      -- not yet reached in this session.
- [ ] All PRs opened so far (#79, #81, #84, and this branch's) need `supervisor-sweep.sh` pickup
      and a real independent audit verdict before merge, same standing discipline as every other
      open PR in this repo.
