# task-20260814-075401-trigger-fresh-supervisor-audit-for-open

Objective: run the real supervisor-sweep audit process against veridian-scripts
PR #356 (branch `fix/stage6-citation-only-query-title-umr-20260814-060148`,
head 65c94faa3ddf0f11c36faf2eb983f7a64cdbcd38), post a genuine AUDIT verdict
comment, and merge if it's a real PASS.

## Completed
- [x] Verified PR #356 state independently via `gh pr view`/`gh api`: OPEN,
      MERGEABLE, base=main, head=65c94faa..., files=resource_governor.py
      (+53/-1), tests/test_dupguard_overbroad_scope_fix.py (+65/-1). Zero
      existing comments confirmed via `gh api .../issues/356/comments`.
- [x] Located the real audit mechanism: `supervisor-entrypoint.sh` +
      `risk-tier.py` (deterministic tier classifier) + structured
      `AUDIT: PASS/FAIL` comment format (8 labeled fields) per
      `mandatory-audit-check.yml` / AGENTS.md Operating Rule 7c.
- [x] Checked out PR head into an isolated worktree (`/tmp/pr356-review`,
      detached at 65c94fa) — never touched this task's own workspace branch.
- [x] Computed risk tier deterministically: `risk-tier.py` -> `tier1`
      (additive: new helper function + docstring updates + new test file,
      no migrations/auth/payment/security/.env paths, no heavy deletion).
- [x] Read the actual diff in full (both files) — reviewed
      `_title_pr_reference_is_citation_only()` and its call site inside
      `find_pr_for_task_identity()` for correctness/security.
- [x] Independently ran the new test file: `pytest
      tests/test_dupguard_overbroad_scope_fix.py -v` -> real 8/8 PASS
      (matches the worker's self-report, verified myself, not trusted
      blindly).
- [x] Ran broader regression tests in the same module family
      (`test_resource_governor_queue_management.py`,
      `test_resource_governor_owner_priority_advance.py`,
      `test_resource_governor_telemetry_retention.py`,
      `tests/test_resource_governor_stuck_task_scope.py`) for regressions
      (in progress in background, results pending below).

## Remaining
- [ ] Confirm broader regression run is clean (no failures introduced)
- [ ] Post the real structured `AUDIT: PASS`/`FAIL` comment to PR #356
      based on the actual verdict
- [ ] If PASS: merge PR #356 (tier1); if FAIL: report cited issues instead
- [ ] record-completion write-back to agent_work_briefing (UMR-20260814-073220-e363)
- [ ] final commit + push of this progress file
