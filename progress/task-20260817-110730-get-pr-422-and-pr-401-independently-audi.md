# Progress: Task 20260817-110730 - Get PR 422 and PR 401 independently audited

## Overview
- PR 422: fix(worker): stop the systemic ~2s worker fast-exit (52/70 failed rows)
- PR 401: (to be determined)
- Status: Starting verification

## STEP 1: Verify tests pass independently for each PR

### PR 422
- [x] Get diff
- [x] Identify files touched
- [x] Run tests
- [x] Verify pass - 31 tests passed (test_reconcile_stale_running_workers.py + test_reconcile_owner_dispatch_status.py)

### PR 401  
- [x] Get diff
- [x] Identify files touched
- [x] Run tests
- [x] Verify pass - 38 tests passed (test_progress_completion_gate.py)

## STEP 2: Get independent audit verdicts

### PR 422
- [x] Review existing audit verdict
- [x] Verdict: AUDIT: FAIL (posted 2026-08-16T09:42:46Z)
- [x] Head commit: e516efc873fdff46f98c04a6d21815ca2e4f4777
- [x] Document result: See STEP 2 findings below

### PR 401
- [x] Review existing audit verdict
- [x] Verdict: AUDIT: FAIL (posted before 2026-08-16T09:40:48Z)
- [x] Head commit: df8bac4787e04b31bf2c24a299ff549f58866cfe
- [x] Document result: See STEP 2 findings below

## STEP 2 Findings - AUDIT: FAIL for both PRs

### PR 422 - AUDIT: FAIL
**Verdict**: Fail - Do NOT merge

**Issues Found**:
1. dispatch_audit_fix() and dispatch_independent_audit() in pm_lifecycle.py now unconditionally raise ValueError from build_tightened_prompt()'s new validate_tight_task() call, because their real success_criteria text embeds the verification command mid-sentence instead of as its own recognized command line -- confirmed by executing the actual functions' real argument strings against the actual validator.

2. verify_with_retries() has no try/except around dispatch_fix_fn/dispatch_audit_fn, so this ValueError propagates out of the retry loop and is only caught by main()'s generic top-level handler, discarding the run's real partial report instead of failing gracefully.

3. No unit test exists (no test_pm_lifecycle.py) exercising build_tightened_prompt(), dispatch_audit_fix(), or dispatch_independent_audit() with real argument text, which is why this regression was not caught before merge.

4. Minor: CLI default --complexity-tier changed to "integrative", which validate_tight_task() requires known_context for; any pm_lifecycle.py run invocation that omits --known-context now fails fast with a ValueError.

**Severity**: Medium

**Corrective Action Owner**: Worker to address the findings listed above and resubmit.

### PR 401 - AUDIT: FAIL  
**Verdict**: Fail - Do NOT merge

**Issues Found**:
1. _CLI_INVOCATION_RE in progress_completion_gate.py (added ~line 287) lacks a leading word boundary before the (?:python3?|bash|sh) alternation, so it matches 'sh' as a suffix of ordinary words (smash/polish/finish/establish/wish/publish/refresh/flash/trash/crash/...) immediately followed by a .py/.sh path -- verified live with reproducible examples, e.g. 'please polish /opt/veridian/scripts/foo.py before merging' matches as a fake CLI invocation and would incorrectly exclude foo.py from the completion gate's required-file set.

2. None of the 3 new tests in tests/test_progress_completion_gate.py probe this word-boundary gap -- all use literal 'python3 <path>' text, so the bug ships undetected by the diff's own test coverage.

3. Net effect: this change can widen the completion gate's false-negative surface (silently accepting a doc-only diff for an objective that genuinely named a code file) beyond what the PR's stated intent and prior precedent in this file (narrow boilerplate-tool allowlist / unambiguous citation formats) established -- a real regression risk in a tier1-auto-mergeable safety gate.

**Severity**: Medium

**Corrective Action Owner**: Worker to address the findings listed above and resubmit.

## STEP 3: Merge and deploy

### PR 422
- [x] Did NOT merge - BLOCKED on STEP 2 (audit FAIL)

### PR 401
- [x] Did NOT merge - BLOCKED on STEP 2 (audit FAIL)

## STEP 4: Final Report

### Status per Task Spec Definition of Done

**a) Real, independently-run test results for both pull requests:**
- [x] PR 422: 31 tests passed (test_reconcile_stale_running_workers.py: 15 passed, test_reconcile_owner_dispatch_status.py: 16 passed)
- [x] PR 401: 38 tests passed (test_progress_completion_gate.py: 38 passed)

**b) A real audit verdict for each, citing a head commit hash:**
- [x] PR 422: AUDIT: FAIL at commit e516efc873fdff46f98c04a6d21815ca2e4f4777
- [x] PR 401: AUDIT: FAIL at commit df8bac4787e04b31bf2c24a299ff549f58866cfe

**c) Every pull request that passed both gates is merged and its presence proven in the live deployed checkout:**
- [x] N/A - Neither PR passed STEP 2 audit gate

**d) A one-line-per-PR final status using exactly the three outcomes defined in Step 4:**
- PR 422: Blocked on failing audit with verdict text (AUDIT: FAIL - dispatch_audit_fix/dispatch_independent_audit ValueError regression)
- PR 401: Blocked on failing audit with verdict text (AUDIT: FAIL - _CLI_INVOCATION_RE word-boundary regex defect)
