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

## Remaining
- [ ] Run the real test suites (`test_supervisor_docs_only_pr_guard.py`,
      `test_worker_exit_status_bridge.py`, `test_supervisor_no_op_branch_guard.py`)
      before push.
- [ ] Push, open PR against `origin/main`.
- [ ] Dispatch a REAL independent audit (`dispatch-owner-task.sh`,
      box-native adopt-then-sweep mechanism) against the PR's current head
      SHA. Never self-certify.
- [ ] On genuine PASS: merge. On real findings: fix on this same branch
      (preserve commits), re-audit.
- [ ] SECOND site: guard the unconditional `gh pr create` at
      dispatch-owner-task.sh:761 with the SAME `VERIDIAN_GATE_PR_ON_CODE_CHANGE`
      switch (not a second flag). Add a real test: code-touching task still
      opens a PR, progress-only task doesn't (note preserved).
- [ ] THIRD: deploy live -- fast-forward /opt/veridian/scripts to the merged
      commit, preserving any real local modification (back up, don't
      discard, any blocking untracked file). Prove via grep that the
      deployed worker entrypoint and dispatch script carry the switch.
- [ ] Final report: PR number + real mergedAt, switch name/default, second
      site fix, live commit SHA, grep proof.
- [ ] `agent_work_briefing.py record-completion`.
