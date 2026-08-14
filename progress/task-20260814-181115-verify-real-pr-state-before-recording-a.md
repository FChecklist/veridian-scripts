# task-20260814-181115-verify-real-pr-state-before-recording-a

SPEC: make completion-recording (agent_work_briefing.py record-completion)
independently verify real PR/merge state before accepting a completed
claim, instead of trusting a worker's self-report at face value.

## Completed
- [x] Explored existing gates: superboss-register.py's
      `validate_umr_terminal_completion_evidence()` only checks that a
      cited commit/file *exists*, never whether it's real code vs
      docs-only -- confirmed the real gap the SPEC describes.
- [x] Found reusable real primitives already in resource_governor.py:
      `_run`, `GH_ORG`, `GH_PR_CHECK_TIMEOUT_SECONDS`,
      `_real_pr_state_for_backfill`, `_pr_changed_files_are_docs_only`
      (latter two not directly reusable as-is: their fail-open/fail-closed
      direction is inverted for our accept/reject use -- wrote a small
      dedicated fetch instead, reusing only the low-level `_run`/GH_ORG/
      timeout constants, same convention progress_completion_gate.py
      already established for importing resource_governor).
- [x] Implemented `_fetch_real_pr_state()` + `verify_real_completion_evidence()`
      in agent_work_briefing.py; wired into `record_completion()` so a
      status=completed/completed_unmerged claim is independently checked
      BEFORE calling mark-umr-terminal. On failure, umr_tasks is left
      untouched (never falsely written) and the result is explicitly
      labeled `"status": "unverified_self_report"`.
- [x] Added `--files-touched` CLI arg (repeatable) as the no-PR-cited
      evidence path.
- [x] Added regression test `test_verify_real_completion_evidence.py` with
      the two real-incident fixtures (docs-only-PR self-report;
      zero-PR/zero-files self-report) asserting both get downgraded.
- [x] Ran the new test for real, output captured below.
- [x] Committed and pushed.
- [x] Opened PR: https://github.com/FChecklist/veridian-scripts/pull/384

## Remaining
- [ ] none

## Real test output
```
$ python3 test_verify_real_completion_evidence.py
..........
----------------------------------------------------------------------
Ran 10 tests in 1.176s

OK
```
Also re-ran the pre-existing `test_agent_work_briefing.py` (unmodified) to
confirm no regression:
```
$ python3 test_agent_work_briefing.py
PASS: all agent_work_briefing.py checks held (honest empty briefing, path +
relationships wiring_registry matching, capability_registry matching,
prior-history surfacing, ai_agent_registry/umr_tasks/gtm_certification_categories
write-back, search-first wiring_registry dedup).
```
