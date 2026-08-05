# PROGRESS -- task-20260805-175309-ocid-020-cycle-decision--adopt-pr-954-on

SPEC: real PM decision cycle re: PR #954 adoption, GTM certification schema, OCID-068 Phase 2
backfill worker status, `ocid_artifact_links` deprecation, and a broader pre-auth brand gap
follow-up. Every one of the SPEC's 6 items was independently checked against live state (GitHub
API + live `superboss-register.sqlite`) before any action -- all 6 turned out to already be done or
false relative to live state. See
`OCID_020_CYCLE_DECISION_PR954_ADOPTION_REVERIFICATION_2026-08-05.md` for full evidence.

## Completed
- [x] Item 1 (adopt PR #954): already adopted + tier1-reviewed hours ago
      (`task-20260805-142559-child-umr-8cfe-pr954-adoption`); real remaining blocker is a
      human-only GitHub App provisioning gap (OCID-070), not a worker-slot gate. No action taken.
- [x] Item 2 (mint child UMR for PR #954): already exists, `UMR-20260805-142559-8cfe`. No new UMR
      minted (would have violated Rule 3, no-premature-minting).
- [x] Item 3 (GTM 25-category schema "not built"): false -- `gtm_certification_categories` has 25
      real rows already. No schema work done (none needed).
- [x] Item 4 (`ocid_compliance_state` "zero rows" / stalled worker): false -- 113 real rows exist;
      `UMR-20260805-093138-2bd0` is `rejected_duplicate` (correct, not stalled). Not restarted --
      restarting would have re-run already-completed OCID-068 Phase 2 work.
- [x] Item 5 (`ocid_artifact_links` "legacy, 3 rows, deprecate"): false -- 215 real rows, actively
      written today (15:24:55Z), 8+ dedicated tests, not superseded by `ocid_canonical_registry`.
      No deprecation doc edit made (would have been false/harmful).
- [x] Item 6 (open broader pre-auth brand follow-up + child UMR): already exists -- PR #959 +
      `UMR-20260805-142629-8087`. No new PR/UMR opened.
- [x] New finding (not in SPEC): PR #954 vs PR #965 file-scope collision on `src/app/signup/*`,
      flagged for next real decision cycle, not resolved unilaterally here.
- [x] Wrote full evidence doc, committed, pushed, opened PR.

## Remaining
- [ ] Human-only GitHub App provisioning (OCID-070) -- outside this task's/any worker's reach.
- [ ] Owner/next cycle: resolve PR #954 vs PR #965 duplicate file-scope collision.
