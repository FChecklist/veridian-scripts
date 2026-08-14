# PROGRESS -- task-20260814-163143-fix-pr376----filepaths-hard-fail-would-b

TARGET: fix veridian-scripts PR#376's AUDIT:FAIL finding -- the new required
`filePaths` field in `tight_task_validation.py`'s `validate_tight_task()` was
wired as a HARD preflight/submit-time failure with no real prompt generator
emitting a `## FILE_PATHS` section yet, which would have aborted every task
dispatched by phase-continuation-tick.py, task-gateway.py, prompt_gateway/
gateway.py, zai_agent_loop.py, status-remediation-tick.py,
veridian_remediation_dispatcher.py, veridian-task-watchdog.py, and
auto_phase_continuation.py at merge time.

Work happens directly on PR#376's own branch
(`worker/task-20260814-133002-fix-false-pr-rejection-heuristic-and-add`,
checked out locally as `pr376-fix`), per SPEC ("same branch"), not a new PR.

## Completed

- [x] Independently verified the audit claim: confirmed via `gh pr view 376`
      that the PR body itself states the AUDIT:FAIL finding; confirmed by
      reading preflight-guard.py's `check_tight_task_schema()` (direct import,
      preflight-time) AND task-gateway.py's `cmd_start()` (subprocess CLI call
      to tight_task_validation.py, submit-time) that BOTH real enforcement
      points read `validate_tight_task()` and would hard-fail; confirmed by
      reading phase-continuation-tick.py's `build_prompt()` (a live cron-driven
      generator) that it emits 7 `## HEADER` sections and none of them is
      `FILE_PATHS`.
- [x] Chose option (a) from the SPEC: made `check_file_paths()`'s result
      ADVISORY ONLY inside `validate_tight_task()` -- surfaced as a
      `warnings` list on the result dict instead of flipping `valid` to
      False -- with a tracked follow-up documented in the module docstring
      (flip back to hard-required once every listed generator emits
      `## FILE_PATHS`). Chose (a) over (b) (migrating all 8 generators in
      this same pass) as the safer/faster option given the number and
      variety of real generators involved.
- [x] Updated `preflight-guard.py`'s `check_tight_task_schema()` and
      `task-gateway.py`'s `cmd_start()` to log (stderr, non-fatal) any
      `warnings` from `validate_tight_task()` instead of silently dropping
      them.
- [x] Updated `test_tight_task_validation.py`'s two filePaths-hard-fail tests
      (`test_file_paths_missing_fails`, `test_file_paths_placeholder_entry_fails`)
      to their new advisory-only behavior (renamed, now assert `valid is True`
      + a `warnings` entry).
- [x] Added a real, non-hardcoded-fixture test
      (`test_real_phase_continuation_tick_prompt_is_not_hard_rejected_for_missing_file_paths`)
      that calls the real `phase-continuation-tick.py` `build_prompt()`,
      feeds its real output through `parse_labeled_fields()` ->
      `validate_tight_task()`, and asserts the full real path does not
      hard-fail -- closing the test gap the audit found (the PR's own tests
      only ever hardcoded a filePaths-bearing fixture).
- [x] Manually smoke-tested `preflight-guard.py` end-to-end against a real
      generator-shaped prompt.txt (no FILE_PATHS section) in a scratch task
      dir: confirmed it now proceeds past `check_tight_task_schema` with only
      a warning printed, no `tight_task_schema_violation` abort.
- [x] Lower-severity note (optional per SPEC, done since quick): tightened
      `resource_governor.py`'s `_SUPERSEDED_BY_RE` to anchor "superseded by
      #NNN" to a sentence/line start instead of matching anywhere in PR
      body/comment text, closing the false-match risk the audit flagged
      (an unrelated mid-sentence mention of another PR's supersession could
      previously false-match). Added a regression test
      (`test_closed_pr_with_unrelated_mid_sentence_superseded_mention_does_not_block`)
      proving the real #298/#299 incident text still matches while the
      unrelated mid-sentence case no longer does.
- [x] Ran the full relevant test suite: `test_tight_task_validation.py` (10
      passed), `tests/test_target_pr_dispatch_time_recheck.py` (19 passed),
      `tests/test_ocid_evidence_supersession.py` +
      `tests/test_stage6_duplicate_pr_citation_guard.py` (unaffected, still
      passing) -- 37 total passed.
- [x] Reverted an unrelated, stray local edit to the shared `PROGRESS.md`
      (header line rewritten to this task's name by scaffolding) before
      starting real work -- per standing guidance, this task's own progress
      file is this one, not the shared `PROGRESS.md`.

## Remaining

- [ ] Push this branch to `origin/worker/task-20260814-133002-fix-false-pr-rejection-heuristic-and-add`
      so PR#376 picks up the fix.
- [ ] Get a fresh AUDIT:PASS matching the new head before merge (per SPEC --
      cannot self-certify).
- [ ] Real follow-up (tracked, not this task's scope): migrate
      phase-continuation-tick.py / task-gateway.py / prompt_gateway/gateway.py
      / zai_agent_loop.py / status-remediation-tick.py /
      veridian_remediation_dispatcher.py / veridian-task-watchdog.py /
      auto_phase_continuation.py to emit a real `## FILE_PATHS` section, then
      flip `check_file_paths()`'s result back to a hard failure in
      `validate_tight_task()` (see the 2026-08-14 correction note in
      `tight_task_validation.py`'s module docstring).
- [ ] Record completion via `agent_work_briefing.py record-completion` for
      UMR-20260814-163138-d651 once pushed.
