## What this is

Lands 2 more real, already-AUDIT:PASS'd, `mergeable=CONFLICTING` PRs, superseding them for the same structural reason as #387/#388/#390 (this worker's branch-enforcement hook blocks pushing directly onto another PR's own head branch).

| PR | Title | Real AUDIT:PASS timestamp |
|----|-------|---------------------------|
| #247 | feat: real embedding/vector-similarity semantic search over capability_registry + wiring_registry | 2026-08-07T05:46:06Z |
| #200 | feat(wiring-health-check): real, standing, deterministic wiring health check | 2026-08-06T17:31:40Z |

Both rebased with **zero code conflicts** -- the only conflict in either case was the shared `PROGRESS.md` header stamp (mechanical, resolved by keeping each commit's own content). `generate_pm_report_v3.py` (touched by #200) auto-merged cleanly with no manual intervention. Post-rebase diff-stat for each PR's real files (excluding PROGRESS.md, which necessarily differs due to the header-stamp convention) is unchanged from pre-rebase.

Re-ran each PR's own real tests post-rebase, all passing:
- `pytest test_capability_semantic_search.py` -- 8/8 passed (#247, network calls mocked)
- `pytest test_wiring_health_check.py` -- 12/12 passed (#200)
- `py_compile` clean on every touched `.py` file

Self-certifying per this sweep task's SPEC's mechanical-rebase escape clause -- not re-litigating either PR's original AUDIT:PASS content (including #200's own audit-disclosed, non-blocking, low-severity known issue in its hard-freeze branch matching, which is unchanged by this rebase).

Full sweep evidence: `progress/task-20260814-183604-sweep-veridian-scripts-for-real-audited.md` in this diff.
