# PROGRESS -- task-20260814-092520-build-cross-repo-pr-lookup--prevent-repo

## Completed
- [x] Explored repo: this workspace IS veridian-scripts (origin=FChecklist/veridian-scripts);
      resource_governor.py already has a narrower 4-repo PR-guard (GH_PR_CHECK_REPOS,
      find_pr_for_task_identity()) used only for the dispatch-time duplicate-PR guard --
      confirmed no existing code searched all 8 real org repos or wired a cross-repo
      fallback into --query-umr.
- [x] Added `ALL_KNOWN_REPOS` (8 repos: compliance-tracker, claude-control,
      veridian-scripts, projexa, veda-advisors, global-revenue-engine, veridian-brain,
      sumeet-spec) to resource_governor.py, env-override preserved (VERIDIAN_GOVERNOR_ALL_KNOWN_REPOS).
- [x] Added `find_real_pr_across_repos(query_text, known_repos=None)` to
      resource_governor.py: real `gh pr list --search` per repo, returns EVERY real
      match across ALL repos searched (not just the first), fails open per-repo.
- [x] Added `_umr_cross_repo_pr_check()` helper: checks the row's own
      originally-dispatched repo (inputs_json['repo'], same field
      find_pr_for_task_identity() already uses as hint_repo) first; falls through to
      the other 7 ALL_KNOWN_REPOS repos automatically when that repo has no match.
- [x] Wired `_umr_cross_repo_pr_check()` into the `--query-umr` reporting path
      (resource_governor.py main(), args.query_umr branch): for an exact `--umr-id`
      single-row lookup, the JSON response now always includes a `cross_repo_pr_check`
      key -- default behavior, no opt-in flag.
- [x] Added a real test (test_find_real_pr_across_repos.py) proving
      find_real_pr_across_repos() finds a PR that exists ONLY in a different repo than
      the one searched first (mocked `gh` subprocess calls keyed by --repo), plus a test
      of `_umr_cross_repo_pr_check()`'s dispatched-repo-first / fallback-to-others wiring.
- [x] Ran the new test file: all tests pass.
- [x] Committed + pushed.

## Remaining
- [ ] (optional follow-up, out of this task's scope) extend the same cross-repo check to
      the plain-listing (no --umr-id) --query-umr path if a future incident shows PM
      tiers rely on that shape too.
