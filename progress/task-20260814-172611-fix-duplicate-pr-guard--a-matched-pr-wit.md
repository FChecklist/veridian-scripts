# PROGRESS -- task-20260814-172611-fix-duplicate-pr-guard--a-matched-pr-wit

## Completed
- [x] Confirmed real bug in `resource_governor.py`'s duplicate-PR guard: both
      the Stage 4/5 exact-branch match and the Stage 6 title-reference match
      in `find_pr_for_task_identity()` return a matched PR as "already
      resolves this dispatch target" without ever checking what that PR
      actually changed.
- [x] Added `_pr_changed_files_are_docs_only(pr_number, repo)` helper
      (`resource_governor.py:2969`) -- calls `gh pr view --json files` on the
      matched PR and returns `True` only if every changed path is
      `progress/*.md`, `PROGRESS.md`, or generically `*.md` (no other file
      type present). Fails safe (`False`, i.e. still block) on any gh error,
      timeout, malformed output, or empty file list.
- [x] Wired the helper into both guard paths in `find_pr_for_task_identity()`:
      - branch-lineage (Stage 4/5) match loop (`resource_governor.py:~3147`):
        skip a docs-only matched PR and keep scanning instead of returning it.
      - title-reference (Stage 6) match loop (`resource_governor.py:~3254`):
        same waiver, applied after the existing disclosure-citation check.
      Non-docs (real code) matches are unaffected and still block exactly as
      before.
- [x] Updated `find_pr_for_task_identity()`'s docstring to document the new
      docs-only waiver.
- [x] Added regression tests in
      `tests/test_dupguard_docs_only_pr_not_blocking.py`:
      - `test_branch_match_on_docs_only_pr_does_not_block` -- fixture PR
        whose only changed file is `progress/<this-task>.md`; asserts the
        guard does NOT block (`dup_pr is None`).
      - `test_branch_match_on_real_code_pr_still_blocks` -- same branch
        match, but the matched PR's diff includes `resource_governor.py`;
        asserts the guard still correctly blocks (`dup_pr == 901`).
      - `test_title_reference_match_on_docs_only_pr_does_not_block` -- Stage 6
        path, matched PR's only file is `PROGRESS.md`; asserts no block.
      - `test_pr_changed_files_are_docs_only_unit` -- direct unit coverage of
        the new helper's classification (docs-only / real-code / empty /
        gh-error cases).
      All 4 tests pass:
      ```
      PASS: test_branch_match_on_docs_only_pr_does_not_block
      PASS: test_branch_match_on_real_code_pr_still_blocks
      PASS: test_title_reference_match_on_docs_only_pr_does_not_block
      PASS: test_pr_changed_files_are_docs_only_unit

      4/4 passed
      ```
- [x] Updated two pre-existing tests
      (`tests/test_stage6_duplicate_pr_citation_guard.py`,
      `tests/test_dupguard_overbroad_scope_fix.py`) whose fixture PRs
      represent genuine duplicates -- their mocks now also answer the new
      `gh pr view --json files` call with a real (non-docs) file, since the
      guard now fetches it before blocking. Confirmed still-blocking behavior
      unchanged: 3/3 and 8/8 pass respectively.
- [x] Verified no unrelated regression: ran
      `tests/test_run_tick_continues_past_row_resolved_skip.py` and
      `tests/test_target_pr_dispatch_time_recheck.py`; the one failure in the
      former (`test_run_tick_still_stops_on_row_independent_block`) is
      pre-existing (reproduced identically on `git stash` back to the base
      commit, before any of this task's changes) and unrelated to this fix.

## Remaining
- [ ] None -- fix implemented, tested, and verified against pre-existing
      duplicate-PR guard test suites.
