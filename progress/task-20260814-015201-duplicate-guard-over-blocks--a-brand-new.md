# task-20260814-015201-duplicate-guard-over-blocks--a-brand-new

GOVERNING CHAIN: P1 UMR-20260806-171945-5767. Opposite failure mode from
UMR-20260813-220216-2e2b (which ADDED target-identifier matching) -- this
task fixes the duplicate-PR / duplicate-work guards being OVER-broad and
silently killing legitimate brand-new work.

Real code: `resource_governor.py` (repo `FChecklist/veridian-scripts`).

## Completed

- [x] Located the exact Stage 4/5/6 duplicate-PR guard that emitted the
      reported reason string: `find_pr_for_task_identity()` in
      `resource_governor.py`, called from `dispatch_one()` around the
      `"duplicate-PR guard (Stage 4/5/6)"` reason string.
- [x] Root-caused Bug 1: Stage 6's title-reference check extracted a bare
      PR number from the task's own title via `_referenced_pr_number()`,
      then scanned **every** repo in `GH_PR_CHECK_REPOS` (compliance-tracker,
      projexa, veridian-scripts, claude-control) for any PR whose title also
      referenced that number -- a same-numbered PR in an unrelated repo
      (PR numbers are per-repo sequences) was treated as evidence of
      duplication. Real incident: UMR-20260814-010152-7981
      (task_identity=owner-task-20260814-010149-432146, brand-new, zero
      prior branches) rejected against claude-control#185, an unrelated PR.
- [x] Fixed Bug 1: Stage 6 now resolves which repo the task's own
      PR-number reference actually names -- an explicit repo-qualified
      reference (`_repo_qualified_pr_ref()`, e.g. `veridian-scripts#185` or
      a full GitHub URL) is checked against exactly that repo; a bare
      "PR NNN" with no repo qualifier is only ever checked against
      `hint_repo` (the SAME repo this task's own work targets, i.e.
      `row_inputs["repo"]`), never scanned across every configured repo.
      If neither is available, Stage 6 is skipped rather than guessed.
- [x] Root-caused Bug 2: `_orchestrator_reuse_verdict_gate()` (Step 2, backed
      by `reuse_verdict_engine.assess()`) ran unconditionally for **every**
      dispatched row, including `task_kind='systemctl_action'` resume/retry
      rows (`dispatch-tick.py`'s `resume_interrupted_workers_tick()`).
      These rows carry no descriptive `title` input at all, so `intent_text`
      fell back to the bare `task_identity` slug -- a task-id string with no
      relation to "is this capability already built" -- and got
      cosine-similarity-matched against `wiring_registry` rows of
      `entity_type='file'` (arbitrary tracked source files), a fundamentally
      incomparable record type for a resume/retry action. Confirmed live in
      production DB: 554/554 (100%) of the last 7 days' `reuse_verdict_engine`
      rejections were `task_kind='systemctl_action'`, and every one matched a
      `kind='file'` wiring_registry row.
- [x] Fixed Bug 2: `_orchestrator_reuse_verdict_gate()` now returns
      `(False, None)` immediately for any row whose `task_kind` is not
      `'veridian_task_create'`, before ever loading a candidate set or
      calling `reuse_verdict_engine.assess()`. Genuine new-work proposals
      (`veridian_task_create` rows) are unaffected.
- [x] Added `tests/test_dupguard_overbroad_scope_fix.py`, 6/6 real tests,
      covering all 4 required regression shapes:
      (a) brand-new task_identity + PR number in title + no prior branch ->
          not blocked;
      (b) cross-repo same-number PR -> not blocked (asserts the Stage 6
          broad `gh pr list` call is never even issued against the
          unrelated repo);
      (c) genuine same-repo same-target duplicate -> still blocked (both
          the bare-number+hint_repo shape and the explicit repo-qualified
          shape);
      (d) a task/resume intent (`task_kind='systemctl_action'`) never calls
          `reuse_verdict_engine` at all, plus a positive control proving a
          genuine `veridian_task_create` row is unaffected.
      Real run output:
      ```
      PASS: test_brand_new_identity_with_pr_number_in_title_not_blocked
      PASS: test_cross_repo_same_number_pr_does_not_block
      PASS: test_genuine_same_repo_duplicate_still_blocked
      PASS: test_explicit_repo_qualified_reference_still_blocked_same_repo
      PASS: test_resume_intent_never_calls_reuse_verdict_engine
      PASS: test_new_work_intent_still_goes_through_reuse_verdict_engine

      6/6 passed
      ```
      Pre-existing `tests/test_stage6_duplicate_pr_citation_guard.py` (3/3)
      and `tests/test_resume_interrupted_workers_bounded_retry.py` (2/2)
      re-run clean against the fix, no regressions.
- [x] Quantified real damage, live production DB
      (`/opt/veridian/ai-os/memory/superboss-register.sqlite`), last 7 days:
      - **569** `umr_tasks` rows with `status='rejected_duplicate'`.
      - **565** of those have `ts_dispatched IS NULL` (never spawned).
      - **6** are Stage 4/5/6 PR-guard rejections whose own reason string
        reports `prior real branch(es) []` (Bug 1 class -- a guaranteed
        false positive, since an identity with zero prior branches cannot
        have a genuine pre-existing PR).
      - **554** are `reuse_verdict_engine.assess()` rejections (Bug 2
        class), of which **100%** (554/554) are `task_kind='systemctl_action'`
        and **100%** (550/550 of the never-dispatched subset) matched a
        `wiring_registry` row with `kind='file'`.
      - Combined: **560 of 569** (98.4%) of the last 7 days'
        `rejected_duplicate` rows fall into one of the two false-positive
        classes this fix closes.

## Remaining

- [ ] Push branch, open PR against `FChecklist/veridian-scripts`.
- [ ] Get a real independent Tier-1 audit at the PR's head SHA.
- [ ] Merge only on a fresh posted `AUDIT: PASS`.
- [ ] Run `agent_work_briefing.py record-completion` for
      `UMR-20260814-010944-2e16`.
