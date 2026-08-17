# PROGRESS -- task-20260817-091427-repair-the-execution-harness-so-worker-r

## Completed
- [x] Verified all 4 named PRs exist (not fabricated): ai-os#6, ai-os#7, ai-os#9, scripts#422, scripts#401, scripts#416.
- [x] **FALSE PREMISE on item 1 (ai-os PR#6)**: SPEC claims PR#6 (+857 lines) is
      "THE ROOT CAUSE" fix and must land first. `gh pr diff 6 --patch` proves the
      diff touches ONLY `PROGRESS.md` (106 lines) + a JSON evidence file (751
      lines) -- zero code; the PR's own body even carries a prior "Superboss
      review" comment saying so. The REAL fix (bridge worker exit status to
      umr_tasks) is already merged to **veridian-scripts** main (commits
      `ce64927`, `b9522a4`) and refined by **veridian-scripts PR#425** (MERGED
      2026-08-16T09:32:42Z, before this SPEC was issued). **PR ai-os#6 will NOT
      be merged** -- documentation-only, stale, superseded. See
      `[[veridian-task-prompt-false-premise-pattern]]`.
- [x] Confirmed ai-os#7 (re-adjudicate 320 rows) and ai-os#9 (backup/retention)
      are Owner-reserved/out of scope -- untouched.
- [x] **Found a second false premise**: a prior, real, independent audit
      (`/opt/veridian/scripts/progress/task-20260816-171304-continue-landing-
      and-disposing-the-remai.md`, dated 2026-08-16, same head commits as
      today -- no commits pushed to #401/#416/#422 since) already found REAL
      DEFECTS in all three veridian-scripts PRs this SPEC calls ready-to-land
      fixes. Today's SPEC's framing ("fixes for both behaviours ALREADY
      EXIST... never been merged... repeatedly failing on bugs it already has
      fixes for") omits that those existing fixes were already found broken.
- [x] **scripts PR#422 (item 2, fast-exit fix)**: cherry-picked (e516efc8) onto
      current main, resolved 2 real conflicts (pm_lifecycle.py,
      worker-exit-status-bridge.py) by preserving BOTH sides' behaviour (kept
      main's tested None-safe complexity_tier default AND PR422's new
      preflight-equivalent validation; kept main's real preflight-denial-reason
      propagation AND PR422's new logs_ref fix). **Fixed the real defect the
      2026-08-16 audit found**: `dispatch_audit_fix()`/`dispatch_independent_
      audit()` built a `success_criteria` with an inline, non-backtick'd `gh
      pr ...`/`git ...` command in a prose sentence -- once build_tightened_
      prompt() started validating via tight_task_validation.validate_tight_
      task(), every real call raised an uncaught ValueError, aborting the
      whole audit-fix/independent-audit cycle (reproduced directly, then
      fixed by putting each command on its own backtick-wrapped line). Also
      found+fixed a SECOND regression the same validation change caused: the
      pre-existing `test_build_tightened_prompt_has_labeled_sections` test's
      own example success_criteria ("run: python3 -m pytest tests/") is
      exactly the prose-prefixed-pseudo-command shape the new gate rejects --
      fixed the test, added 2 new regression tests. Real test output:
      `python3 -m pytest tests/test_pm_lifecycle.py
      tests/test_mark_umr_terminal_structured_evidence.py -q` -> **44 passed**.
      `tests/test_worker_exit_status_bridge.py`: 19 passed, 6 failed --
      confirmed via `git stash` A/B that all 6 failures are byte-identical,
      pre-existing on main HEAD 74e9a71 (real systemd --user / subprocess
      sandbox limitations), unchanged by this fix. Committed: `4aa86a7`,
      `955e161`. Pushed to this task's own branch.
- [x] **scripts PR#401 (item 3, completion gate)**: cherry-picked (df8bac47)
      onto current main -- 0 conflicts, 38/38 pre-existing tests pass
      unchanged. Fixed the real defect the 2026-08-16 audit found:
      `_CLI_INVOCATION_RE` had no leading word boundary, matching "sh" as a
      bare suffix of "finish"/"polish"/"smash"/etc. Verified this PR does
      **NOT** cover the SPEC's prose/prohibition cases and extended it for
      all three real failures named in the SPEC:
        1. framework name ending in a code extension ("Vue.js"/"Chart.js") --
           added `_FRAMEWORK_NAME_EXCLUDED`.
        2. file named only inside a prohibition clause ("do not touch X") --
           added `_PROHIBITION_RE`/`_prohibition_spans()`.
        3. correct fix landed in a different file than the objective named --
           added a small, explicit `FROZEN_FILE_OVERRIDES = {"dispatch_core.py":
           "resource_governor.py"}` allow-list (never a free-text "any file is
           fine" loophole), reusing this codebase's own established
           stop-work-order-wrapper convention
           (`[[veridian-dispatch-core-py-frozen-stop-work-order]]`).
      Added one regression test per real failure, plus one for the CLI-
      invocation word-boundary defect, plus 2 "still-caught" tests proving
      the new exclusions don't overreach. Real test output: `python3 -m
      pytest tests/test_progress_completion_gate.py tests/test_pm_lifecycle.py
      tests/test_mark_umr_terminal_structured_evidence.py -q` -> **88 passed**
      (44 in test_progress_completion_gate.py: 38 pre-existing + 6 new).
      Committed: `b4c9c8f`. Pushed to this task's own branch.
- [x] Confirmed real, hard fleet-wide constraint: workers may push ONLY to
      their own assigned branch (verified live via a blocked
      `pretooluse_worker_enforcement` push attempt to PR#422's own branch) --
      the existing PRs #422/#401/#416 on GitHub cannot be updated in place by
      this task. My fix commits live on this task's own branch
      (`worker/task-20260817-091427-repair-the-execution-harness-so-worker-r`)
      and, per this repo's own established pattern for exactly this situation
      (see PR#442 superseding PR#355 in the 2026-08-16 progress note), become
      the real superseding fix once the automated pipeline opens a PR for
      this branch.

## Remaining -- NOT DONE, stopped honestly on budget, not silently skipped
- [ ] **scripts PR#416 (item 4, stale-skip gate)**: NOT started. The
      2026-08-16 independent audit already found a real defect (age never
      resets once a row crosses MAX_QUEUED_AGE_SECONDS -> permanent
      starvation; docstring's claimed PM-decision recovery path isn't
      implemented; no dedicated test) against its current, unchanged head
      (cdfb4d40). This is a real, non-mechanical fix (a starvation/aging
      algorithm change), not a rebase-only task, and there was no remaining
      session budget to do it responsibly (design + implement + test +
      independently audit).
- [ ] **No fresh independent audit obtained for the PR#422/#401 fixes above.**
      The 2026-08-16 audit's verdicts were against the PRs' OLD heads, which
      this task's own fix commits have now changed -- a stale verdict does
      not carry over per this SPEC's own rule ("A verdict citing no head
      commit hash is not a verdict"; by the same logic a verdict against a
      different head is not a verdict for THIS head either). Dispatching a
      real independent audit via the box-native adopt-then-sweep mechanism
      (`dispatch-owner-task.sh` / `pm_lifecycle.py dispatch_independent_
      audit()`) and waiting for it to land is a real, possibly long-running
      operation (the 2026-08-16 precedent queued for hours behind the 5/5
      concurrent-worker cap) that this session's remaining budget could not
      safely absorb. **Per this SPEC's own stop condition ("No independent
      verdict can be obtained") and prohibition ("NEVER SELF-CERTIFY"), the
      PR#422 and PR#401 fixes above are NOT merged.** They are real, tested,
      committed, and pushed to this task's own branch, ready for a
      follow-up task/audit cycle.
- [ ] Nothing was merged. Nothing was deployed live. The live checkout
      (`/opt/veridian/scripts`) was not touched (it is currently in a
      detached-HEAD state 4 commits ahead of origin/main, left by a separate,
      apparently-finished task `worker/task-20260817-045523-establish-ground-
      truth-for-every-non-ter` -- confirmed no live worker process is running
      against it, but it was not this task's place to fast-forward it when
      nothing here was merged to main yet).
- [ ] End-to-end proof (dispatch trivial task, assert both harness
      assertions) NOT attempted -- would be meaningless before any real fix
      is actually merged+live, and this task's remaining budget could not
      safely absorb a real dispatch-and-wait cycle on top of the above.

## Notes / corrections to the dispatching SPEC
- Item 1 (ai-os PR#6) is not real code and its underlying bug is already
  fixed elsewhere; treat the SPEC's "land it first, root cause" framing as
  false. See `[[veridian-task-prompt-false-premise-pattern]]`.
- Items 2-4's framing ("fixes already exist, just land them") also
  undersells reality: existing independent-audit evidence
  (`/opt/veridian/scripts/progress/task-20260816-171304-...md`) already
  found real, unfixed defects in all three at the exact heads this SPEC
  points at. This task fixed 2 of 3 for real (#422, #401) but could not
  obtain a fresh audit or complete #416 within budget.
