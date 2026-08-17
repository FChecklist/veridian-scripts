# PROGRESS -- task-20260817-022956-finish-landing-the-progress-only-pull-re

SPEC: land the already-written, already-pushed work on
`worker/task-20260816-172131-stop-workers-opening-a-pull-request-for` (commits
199bd90 + d30891b, UMR-20260816-171513-5901) -- gates PR creation on a real
code/test/config/schema-relevant diff, closes any pre-existing docs-only PR,
preserves the progress note via `completed_docs_only` ->
`worker-exit-status-bridge.py` -> `completed_unmerged`. Then: (2) apply the
same named-switch guard to the second unconditional `gh pr create` call at
dispatch-owner-task.sh:761 + add a real test for both branches there. (3)
Deploy live to /opt/veridian/scripts (fast-forward, preserving any real local
diff) and prove both deployed files carry the switch via grep.

## Verification of the inherited branch's own claims (read real diff first)
- [x] Branch exists on origin (d30891b, HEAD). Base ef7100a is 6 commits
      behind current origin/main (cc59f1e) -- needs rebase, not a direct PR.
- [x] Read the real diff (`git --no-pager diff ef7100a 199bd90`): confirmed
      claims are accurate --
      - `supervisor-entrypoint.sh` DOCS-ONLY-PR-GUARD-BLOCK: named switch
        `VERIDIAN_GATE_PR_ON_CODE_CHANGE` (default `1`), runs
        `docs_only_diff_guard.py` before the paid Superboss review + `gh pr
        create`; on trip, closes any pre-existing PR the worker itself
        already opened, writes `docs_only_completion.json`, checkpoints
        `completed_docs_only`, exits 0. Audit/merge gates
        (`progress_completion_gate.py`, `quality-gate.sh`) genuinely
        untouched.
      - `docs_only_diff_guard.py`: reuses (not reimplements)
        quality-gate.sh's own live `DOCS_ONLY_EXT_PATTERN`/
        `DOCS_ONLY_NAME_PATTERN` regexes, fails closed (unrecognized ->
        code-relevant).
      - `worker-exit-status-bridge.py`: new `completed_docs_only` status
        bridges to `completed_unmerged` (not `completed`/not the no-op
        path) via the same real, independently-reverified
        `mark-umr-terminal` gate every other writer uses.
      - `worker-entrypoint.sh`/`AGENTS.md`: soft prompt instruction only,
        not itself an enforcement point.
      - Tests: `tests/test_supervisor_docs_only_pr_guard.py` (5 cases),
        `worker-exit-status-bridge` (+3), pre-existing
        `test_supervisor_no_op_branch_guard.py` fixture fixed
        (`REAL_WORK.md` -> `.py` so it isn't itself caught by the new
        docs-only classifier).

## Completed
- [x] Cherry-picked both real commits (199bd90, d30891b) onto current
      `origin/main` (cc59f1e) on this task's own branch -- only conflict was
      the disposable `PROGRESS.md` header stamp, resolved to this task's own
      id. Local: a290602 (feat), 3b97e09 (cleanup). No other files touched
      by the rebase.

- [x] Ran the real test suites (`test_supervisor_docs_only_pr_guard.py`,
      `test_worker_exit_status_bridge.py`, `test_supervisor_no_op_branch_guard.py`)
      before push -- 32/32 pass.
- [x] Pushed, opened **PR #444** against `origin/main`
      (https://github.com/FChecklist/veridian-scripts/pull/444).
- [x] SECOND site fixed on the same branch/PR: guarded the unconditional
      `gh pr create` at `dispatch-owner-task.sh` (was line 761, the
      `claude_code_cli_headless` tier-3/4 direct-execution branch, which
      never goes through `supervisor-entrypoint.sh` so the first guard
      can't cover it) with the SAME `VERIDIAN_GATE_PR_ON_CODE_CHANGE`
      switch -- not a second flag. Real test added:
      `test_dispatch_owner_task_docs_only_pr_guard.py` (3 cases: docs-only
      no PR + note preserved via `mark-umr-terminal --reason`,
      code-touching still opens a PR, switch=0 reverts) -- built/run
      against a real disposable repo under `/opt/veridian/repos/` (the one
      real hardcoded path this script's own `REPO_PATH` uses), torn down
      after. 40/40 total tests green across every touched suite, no
      regressions. Pushed (56cf6bc -> 4b3374e).
- [ ] Dispatch a REAL independent audit against PR #444's current head SHA
      (never self-certify). **Attempt 1** (UMR-20260817-023451-4f45, title
      "Independent audit of PR #444 (veridian-scripts)", the exact
      `pm_lifecycle.dispatch_independent_audit()` template) was
      auto-rejected by the box's own `reuse_verdict_engine.assess()`
      dedup gate BEFORE reaching a worker: `verdict=duplication_blocked`,
      score=0.8438, matched against `wiring_registry` entity
      `file-dd3247bd960c` -- a completely unrelated prior task's
      `task.yaml` path (`.../task-20260731-044728-independent-audit-of-pr-652/`).
      Verified real (not assumed): looked up that wiring_registry row
      directly -- it's a generic `full_server_file_registration.py`
      file-registration entry for a DIFFERENT PR's audit task dir, a real,
      confirmed cross-type false-positive (the templated title text
      "Independent audit of PR #N" embeds near-identically against any
      historical `...-independent-audit-of-pr-NNN` task directory name --
      same false-positive class `resource_governor.py`'s own
      `_orchestrator_reuse_verdict_gate` docstring already documents for
      task-resume intents, just not yet closed for this title shape). Not
      this task's scope to fix reuse_verdict_engine itself.
      **Attempt 2** (UMR-20260817-024311-5912, distinctive title) --
      passed dedup, but hit a SECOND real, pre-existing, unrelated bug at
      preflight: `PRE-FLIGHT HARD STOP (tight_task_schema_violation):
      Complexity tier "moderate" is not recognized` --
      `pm_lifecycle.build_tightened_prompt()`'s own default
      `complexity_tier="moderate"` (and `dispatch_independent_audit()`'s
      own hardcoded call site) is not a member of
      `tight_task_validation.VALID_TIERS` (mechanical/integrative/
      judgment) -- the exact poisoning this same file's own
      task-20260816-092554 comment documents fixing for the CLI default,
      just never applied to this call site. **Attempt 3**
      (UMR-20260817-024451-221d, `complexity_tier="judgment"`) -- passed
      both dedup and the complexity-tier gate, reached a real running
      worker, but hit a THIRD real, pre-existing bug at preflight:
      `no_runnable_verification_command_in_success_criteria` --
      `dispatch_independent_audit()`'s own SUCCESS_CRITERIA template folds
      its real `gh pr view`/`gh pr comment` commands into one flowing
      prose paragraph with no backticks/line breaks, so
      `tight_task_validation._line_is_runnable_command()`'s heuristic
      (first token in a closed COMMAND_WORDS list, or a backtick-quoted
      command-shaped span) never recognizes it as a real command. None of
      these 3 are this task's scope to fix in `pm_lifecycle.py` itself
      (out of scope, would be a 4th, unrelated change) -- routed around
      them in my own dispatch script instead (`.scratch/dispatch_audit_444_v4.py`):
      distinctive title (avoids dedup), `complexity_tier="judgment"`,
      SUCCESS_CRITERIA as two lines, each with a real backtick-quoted `gh`
      command. **Attempt 4** (UMR-20260817-024638-9154) -- dispatched,
      verified locally against the real
      `tight_task_validation.check_success_criteria_has_runnable_command()`
      function before dispatch this time (returns `None` = valid).
      Awaiting real AUDIT:PASS/FAIL.
- [ ] On genuine PASS: merge PR #444. On real findings: fix on this same
      branch (preserve commits), re-audit.
- [ ] THIRD: deploy live -- fast-forward /opt/veridian/scripts to the
      merged commit, preserving any real local modification (back up,
      don't discard, any blocking untracked file). NOTE: live checkout
      already has an untracked `docs_only_diff_guard.py` (byte-identical
      to this PR's own copy, dated Aug 16 17:37) plus other untracked
      files (`quality-gate.sh.rollback-...`, `superboss-register.sqlite`,
      `.empty-stub-superseded-...`) predating this task -- account for
      these during fast-forward, don't let them silently block or get
      discarded.
- [ ] Prove via grep that the deployed worker entrypoint
      (`supervisor-entrypoint.sh`) and dispatch script
      (`dispatch-owner-task.sh`) both carry `VERIDIAN_GATE_PR_ON_CODE_CHANGE`.
- [ ] Final report: PR number + real mergedAt, switch name/default, second
      site fix, live commit SHA, grep proof.
- [ ] `agent_work_briefing.py record-completion`.
