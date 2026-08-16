# PROGRESS -- task-20260816-024853-critical-real-fix--the-completion-report

## Completed

- [x] Located the exact real script printing the rejection text: `credit-accountant.py`'s
  `cmd_report()` (line ~316), `{"approved": False, "reason": "no matching approved plan
  for this task_id/increment -- report rejected"}`.
- [x] Confirmed live against the real `credit-ledger.sqlite` that all 5 real UMRs named in
  the SPEC (UMR-20260815-052932-e80b, -111843-28fc, -162536-cb95, -162806-1492,
  -070818-d173) have a real `credit_increments` row for increment 1 with
  `plan_verdict="rejected"` and `plan_reasoning="existing software/mechanism already
  covers this (system_index match) -- use it instead of spending AI credits"` -- i.e. the
  bug is NOT in `cmd_report()`'s own task_id/increment matching (confirmed correct,
  unchanged), it's that `propose()` was itself incorrectly rejecting genuinely novel plans
  as false-positive duplicates.
- [x] Root-caused the false positive to `resource_governor.py`'s
  `_seed_credit_accountant_plan()` (the real caller that seeds increment 1 for every
  `veridian_task_create` spawn): it built `search_terms` as a bare, UNQUOTED
  space-join of extracted keywords or the raw task title, which
  `check_existing_capability()` -> `superboss-register.py check-duplicate` ->
  `_fts_query()` OR's word-by-word across system_index/wiring_registry (7,783+ rows).
  Same class of bug already fixed twice elsewhere in this exact codebase
  (2026-08-02, 2026-08-13, both at worker-entrypoint.sh's own auto-fix call site) but
  never propagated to this call site, added one day after the 2026-08-13 fix landed.
- [x] Live-reproduced the false positive directly against the real, live registry:
  - This task's own generic title `"critical real fix the completion report"` unquoted:
    `found=3445`. Wrapped as one FTS5 quoted phrase: `found=0`.
  - The REAL stored `search_terms` blob for UMR-...-d173's real task
    (`task-20260815-154633-fix-real-starvation-bug--interrupted-wor`) unquoted:
    `found=6468`. Same string wrapped as one quoted phrase: `found=0`.
- [x] Fixed the real root cause in `resource_governor.py`'s `_seed_credit_accountant_plan()`:
  each individually-extracted keyword/phrase is now wrapped in its own FTS5 quoted-phrase
  clause (still a real multi-term OR search, just over exact phrases instead of exploded
  bare words -- not a single big adjacency phrase spanning everything, which would have
  turned the check into a rubber stamp). The title fallback (used when no
  quoted-string/file-path/identifier/rule-id keywords are extractable from the prompt --
  the common case for plain-prose titles) is wrapped as one quoted phrase.
  Does NOT touch `credit-accountant.py`'s own matching/deny logic -- that was always
  correct, per the SPEC's own instruction not to weaken the check.
- [x] Added/extended regression tests in
  `tests/test_perform_spawn_seeds_credit_accountant_plan.py`:
  `test_seed_credit_accountant_plan_quotes_generic_title_fallback` and
  `test_seed_credit_accountant_plan_quotes_each_extracted_keyword_individually`. All 4
  tests in that file, plus the 3 in `tests/test_credit_accountant_report_approval.py`,
  pass.
- [x] COMPLETION GATE: ran a real, unmocked end-to-end `propose` -> `report` cycle
  against `credit-accountant.py` (only `claude_judgment_call` stubbed to avoid spending
  real subscription budget; `check_existing_capability` ran for real against the live
  registry) using the fixed search_terms shape for a generic, realistic plan/outcome --
  `propose` approved (exit 0), `report` approved (exit 0). The exact rejection text no
  longer fires for genuinely matching real work.
- [x] RECOVERY: verified all 5 real UMRs' branches/commits are still real and reachable:
  - UMR-...-e80b (`task-20260815-145619-fix-real-false-positive-in-target-identi`):
    PR #420, already MERGED.
  - UMR-...-070818-d173 (`task-20260815-154633-fix-real-starvation-bug--interrupted-wor`):
    PR #421, already MERGED. (Its `umr_tasks` row is still `status=running` -- a real
    stale/phantom-running row post-merge, a reconciliation-layer issue, NOT a
    completion-report-approval issue; out of this task's TARGET scope, flagged here for
    a separate dispatch rather than hand-edited.)
  - UMR-...-162536-cb95 (`task-20260815-231808-real-audit---merge-only--no-new-code--fo`):
    PR #427, already MERGED.
  - UMR-...-162806-1492 (`task-20260815-231949-real-redispatch-of-gtm-cert-part3-4-trac`):
    PR #428, already OPEN (not merged) -- left alone per SPEC instruction, an audit+merge
    dispatch's own job.
  - UMR-...-111843-28fc (`task-20260815-220852-document-the-real-dispatch-tick-architec`):
    genuinely had NO PR yet (2 real commits, 25b12bf + dd2d6bc, pushed to origin but never
    opened as a PR) -- opened **PR #430** to preserve the real work, without
    re-implementing anything.
  - Note: the SPEC's specific claims about which UMR had which commit SHA/PR did not all
    match live state (e.g. it described d173 as still orphaned/unmerged with a stuck
    running row that needed a PR; live state shows PR #421 already merged) -- verified
    independently against the real DB/GitHub state rather than trusted at face value, per
    the established false-premise pattern for this task family.

## Remaining
- [ ] None for this task's TARGET/RECOVERY/COMPLETION GATE. Optional follow-up (separate
  scope, not done here): reconcile UMR-...-070818-d173's stale `status=running` row now
  that PR #421 is confirmed merged (belongs to the existing
  `reconcile_stale_running_workers.py` / `reconcile_owner_dispatch_status.py` class of
  scripts, not this task's credit-accountant target).
