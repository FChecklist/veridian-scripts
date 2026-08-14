# Task: fix false PR-rejection heuristic + add filePaths field to task-tightening validator

Supersedes: UMR-20260814-125933-3377 (self-rejected as rejected_duplicate; real cause found and fixed below).

## Completed
- [x] Verified real state independently (not just trusting SPEC text):
  - `sqlite3 /opt/veridian/ai-os/memory/superboss-register.sqlite` row for
    UMR-20260814-125933-3377 confirmed: status=rejected_duplicate, reason
    starts "target-PR dispatch-time re-check: FChecklist/veridian-scripts#298
    ... state=CLOSED ...".
  - `gh pr view 298 --repo FChecklist/veridian-scripts` confirmed real:
    state=CLOSED, mergedAt=null.
- [x] Located the heuristic: target_pr_already_resolved() in
  resource_governor.py (called from dispatch_one()'s target-PR
  dispatch-time re-check block). Confirmed root cause: it blocked on ANY
  state == CLOSED, treating closed-without-merge identically to merged
  (real duplicate). Narrow fix applied: removed the state == CLOSED
  blocking branch; only state == MERGED now blocks. CLOSED-without-merge
  falls through to the same "not resolved, dispatch proceeds" path as OPEN.
  Docstring updated in place to document the real incident/rationale.
- [x] Updated tests/test_target_pr_dispatch_time_recheck.py:
  - Replaced test_closed_unmerged_target_pr_blocks (encoded the bug) with
    test_closed_unmerged_target_pr_does_not_block (real PR#298 shape).
  - Added test_dispatch_one_end_to_end_closed_unmerged_pr_is_not_rejected_by_this_guard
    (full dispatch_one() e2e, real UMR-20260814-125933-3377 shape).
  - All 15 tests in the file pass.

- [x] Added required filePaths field to tight_task_validation.py's
  validate_tight_task() -- array of real repo-relative paths, validated
  non-empty/non-placeholder using the file's existing check_field()/
  is_placeholder() helpers, no parallel validator. FILE_PATHS recognized
  locally in this module's FIELD_HEADER_RE/key_map (deliberately NOT added
  to the shared REQUIRED_TASK_SECTIONS in workflow_contract.py -- that also
  gates task-gateway.py/prompt_gateway/gateway.py, out of this task's
  scope). Added 1 passing + 2 failing test cases to
  test_tight_task_validation.py (9/9 tests pass).
- [x] Added a short header comment to dispatch-owner-task.sh (confirmed via
  Explore agent to be the real outermost dispatch entry point -- both the
  human/SSH path and the one live systemd-timer-driven path (pm-sentinel-
  tick.sh) converge on it) documenting the real submit -> capability-lookup
  -> queue-submit -> execute -> validate pipeline order (documentation
  only, no mechanism change).

## Remaining
- [ ] Determine why PR#298 was closed (close comment / CI / review) and
  either reopen+rebase or redo the 696-line diff on a fresh branch.
- [ ] Get fresh AUDIT:PASS matching head, merge.
- [ ] Update superboss-register row(s) for UMR-20260813-102459-10c3 (real
  merged PR, not completed_unmerged) and UMR-20260814-125933-3377.
- [ ] Commit + push this heuristic fix (resource_governor.py +
  tests/test_target_pr_dispatch_time_recheck.py) as a first meaningful unit.
- [ ] record-completion write-back to agent_work_briefing.py for
  UMR-20260814-132703-a1f9.
