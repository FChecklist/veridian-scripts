## What this is

This PR consolidates 9 already-merged-commit, already-AUDIT:PASS'd, `mergeable=CONFLICTING` PRs onto current `main`. Each source PR's real audit was independently re-verified (this task's own audit-state sweep) to be a genuine PASS posted **after** that PR's current head commit (commit-committer-date < audit-comment created_at in every case) -- not a stale audit against an older push.

All 9 source PRs are docs-only (PROGRESS.md + one new uniquely-named markdown file each) except #118, which also includes one already-executed, already-audited one-off repair script (`repair_file_inventory_20260806.py`, no ongoing behavior change -- the repair it performed already happened against the live DB prior to that PR being opened).

Each source PR's real commit(s) were rebased onto current `main` and cherry-picked here **preserving original authorship/commit messages** (not squashed). The only conflict in every case was the shared `PROGRESS.md` status-line/header collision (every worker branch stamps its own task name onto line 1) -- resolved by keeping each commit's own `PROGRESS.md` content, the established convention already cited by name in PR #198's own AUDIT:PASS finding. This is a pure mechanical rebase with no behavior change, so per this task's own SPEC I am self-certifying rather than requesting a fresh audit -- diff-stat before/after rebase was compared for every source PR and matches exactly (see this task's own progress file below).

## Source PRs (superseded by this PR, will be closed with a link to the real merge commit)

| PR | Title | Real AUDIT:PASS timestamp |
|----|-------|---------------------------|
| #233 | Merges must run inside a real worker unit... | 2026-08-06T23:59:46Z |
| #244 | docs: verify wiring_registry re-escalation SPEC is false-premise | 2026-08-07T05:28:35Z |
| #90 | docs(OCID-020): re-verify PR #954 adoption cycle SPEC | 2026-08-06T08:19:11Z |
| #93 | docs(OCID-020): re-verify standalone GTM-schema SPEC | 2026-08-05T19:01:59Z |
| #71 | docs(OCID-068): independent re-verification -- UTR/UMR taxonomy | 2026-08-05T16:28:29Z |
| #60 | docs(OCID-069): independent re-verification -- methodology extension | 2026-08-06T08:18:09Z |
| #99 | docs: owner directive report -- sqlite3 recovery | 2026-08-06T08:18:39Z |
| #118 | fix: repair live file_inventory corruption | 2026-08-06T08:20:08Z |
| #371 | Audit the two unaudited register-integrity fixes | 2026-08-14T12:49:46Z |

## Why a new PR instead of pushing to each original branch

This worker's own `pretooluse_worker_enforcement.py` hook mechanically blocks `git push` to any branch other than this task's own assigned branch (fail-closed, no override). This matches the established precedent already used in this same repo for the identical situation (see `progress/task-20260814-163404-*.md` and `progress/task-20260814-170148-*.md`: PR #374 -> new PR, PR #376 -> PR #379). Since all 9 of these source PRs are independent, non-overlapping, low-risk docs/record-keeping changes, they are consolidated into one PR here rather than requiring 9 separate follow-up worker dispatches.

Real audit-state sweep evidence, and the remaining (higher-risk, real-code) CONFLICTING+PASS-audited PRs not included here, are documented in `progress/task-20260814-183604-sweep-veridian-scripts-for-real-audited.md` in this diff.
