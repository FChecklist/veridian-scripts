Lands the already-written, already-audited-pending work from
worker/task-20260816-172131-stop-workers-opening-a-pull-request-for
(commits 199bd90 + d30891b), rebased onto current main (only conflict:
the disposable PROGRESS.md header stamp). That dispatch (UMR-20260816-171513-5901)
exhausted its budget after pushing but before audit/merge/deploy; this PR
picks that up, verified independently against the real diff before trusting
its own claims (see progress/task-20260817-022956-finish-landing-the-progress-only-pull-re.md).

## What it does
- supervisor-entrypoint.sh DOCS-ONLY-PR-GUARD-BLOCK: gates PR creation (and
  closes any PR the worker's own session already self-opened) on a real,
  deterministic classification of the diff -- named switch
  VERIDIAN_GATE_PR_ON_CODE_CHANGE, default 1. Audit/merge gates
  (progress_completion_gate.py, quality-gate.sh) are untouched.
- docs_only_diff_guard.py: reuses quality-gate.sh's own live DOCS_ONLY
  allowlist rather than reimplementing it; fails closed.
- worker-exit-status-bridge.py: new completed_docs_only status bridges to
  completed_unmerged (a real commit, honestly not merged) via the same
  independently-reverified mark-umr-terminal gate every other writer uses --
  the progress note is preserved through this durable channel, never
  discarded.
- worker-entrypoint.sh/AGENTS.md: soft prompt instruction not to
  self-run gh pr create for a docs-only diff.
- Tests: tests/test_supervisor_docs_only_pr_guard.py (5), 3 new
  worker-exit-status-bridge cases, and a pre-existing fixture fix in
  test_supervisor_no_op_branch_guard.py (.md -> .py so it isn't itself
  caught by the new classifier).

Full local suite for the touched areas green post-rebase:
tests/test_supervisor_docs_only_pr_guard.py tests/test_worker_exit_status_bridge.py tests/test_supervisor_no_op_branch_guard.py -- 32/32 passed.

Real independent audit requested via the box-native dispatch mechanism, not
self-certified.
