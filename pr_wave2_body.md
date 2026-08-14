## What this is

Lands 2 more real, already-AUDIT:PASS'd, `mergeable=CONFLICTING` PRs, superseding them for the same structural reason as #387/#388 (this worker's branch-enforcement hook blocks pushing directly onto another PR's own head branch).

| PR | Title | Real AUDIT:PASS timestamp | Diff (unchanged by rebase) |
|----|-------|---------------------------|------------------------------|
| #385 | feat(task-gateway,superboss-register,resource_governor): real per-dispatch token-usage measurement | 2026-08-14T18:33:56Z | 638 insertions across 6 files |
| #384 | feat(agent_work_briefing): independently verify real PR/merge state before recording completion | 2026-08-14T18:25:04Z | 507 insertions across 4 files |

Both were opened today; rebasing onto current `main` produced **zero code conflicts** in either -- the only conflict in both cases was the shared `PROGRESS.md` header stamp (mechanical, resolved by keeping each commit's own content, matching the established convention). Post-rebase diff-stat for each PR is byte-identical to its pre-rebase diff-stat (confirmed via `git diff origin/main...<branch> --stat`), confirming this really is a pure mechanical rebase with no behavior change.

Re-ran each PR's own real tests post-rebase, all passing:
- `pytest tests/test_token_usage_measurement.py` -- 12/12 passed (#385)
- `pytest test_verify_real_completion_evidence.py` -- 10/10 passed (#384)
- `python3 test_agent_work_briefing.py` (pre-existing, adjacent coverage for #384's changes) -- PASS, no regression
- `py_compile` clean on every touched `.py` file

Self-certifying per this sweep task's SPEC's mechanical-rebase escape clause -- not re-litigating either PR's original AUDIT:PASS content.

Full sweep evidence: `progress/task-20260814-183604-sweep-veridian-scripts-for-real-audited.md` in this diff.
