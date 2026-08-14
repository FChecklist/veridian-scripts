# PROGRESS -- task-20260813-235515-re-audit-veridian-scripts-pr-308-at-curr

Preserved from PR #339 (head `f53e3138`), which carried this content as a
doc-only diff against the now-superseded shared `PROGRESS.md` convention
(see `progress/task-20260813-195927-kill-progress-md-only-prs--per-task-prog.md`
and PR #322). PR #339 will be closed as superseded by this file once this
PR merges; this file is the durable home for its real findings.

Note on PR #339's own title ("fix remaining audit objections, then merge"):
by the time PR #339 was opened, PR #308 had **already been merged**
(squash commit `989fb5d5`, now `origin/main`'s tip) with a Tier-1
`AUDIT:PASS` posted at head `6d1aaa87` -- see the "Completed" section below,
which is PR #339's own real, contemporaneous record of doing that audit and
merge. PR #339's branch itself carries zero code changes (by design -- see
below) and its title is only misleading if read in isolation from this
content; the real work happened on PR #308's own branch, not PR #339's.

Governing UMR: UMR-20260813-235507-1710. Real deliverable landed as commits
on `veridian-scripts` PR #308's own branch
(`worker/task-20260813-145820-guard-register-cli-invocations--one-quer`),
now merged to `main` -- see that branch's own PROGRESS history ("Addendum 2:
re-audit continuation") for the full narrative. This task's own branch
carried no code changes (the real work was correctly pushed straight to PR
#308's branch, per the SPEC's own instruction to fix the plumbing rather
than redo the work).

## Verification (before any code change)
Per the SPEC's own instruction, read `task.yaml` +
`supervisor.log`/`supervisor-result.json`/`review.json` for the prior
dispatch (UMR-20260813-225704-6195, task dir
`task-20260813-225731-close-the-live-audit-fail-and-conflictin`) first:
- Confirmed real: it fixed the original `AUDIT:FAIL`'s `_ensure_umr_table()`
  crash correctly (commit `75c12f2`, pushed straight to PR #308's branch
  from a `/tmp` worktree -- real, verifiable, not fabricated).
- Confirmed real: it died `blocked` on `gh pr` plumbing (its own
  `claude-control` task branch had 0 commits ahead of base, since the real
  fix went to `veridian-scripts`'s PR branch instead -- not the branch the
  supervisor was looking for a PR on), never posting a new audit or merging.
- **Found independently, not disclosed by the dispatching SPEC**: that same
  prior task's own internal reviewer had already run and returned
  **REJECT** at that exact head (`4380f7f9`) -- `review.json` in its task
  dir documents a real, distinct regression: `query_umr_tasks()`'s new
  `full=False` default silently defeated `find_target_identifier_duplicate()`
  (the deterministic duplicate-dispatch guard `dispatch-owner-task.sh` calls
  before every real dispatch). Verified this myself before trusting it:
  read the code directly, then ran `tests/test_target_identifier_dedup.py`
  at that head -- 5 failed/8 passed, matching the reviewer's own count
  exactly.

## Completed
- [x] Independently verified the SPEC's PR-state claims (head SHA, files
      changed, stale-audit SHA, mergeable state) against live `gh api`
      output before acting -- all matched.
- [x] Read prior dispatch's task.yaml/supervisor.log/review.json; root-caused
      its real failure (gh-pr-plumbing on the wrong branch, not the work).
- [x] Enumerated the original `AUDIT:FAIL`'s objections (1 actionable) and
      confirmed it was already fixed at the head this task started from.
- [x] Found and independently verified a second, real, still-open regression
      (query_umr_tasks() full=False defeating find_target_identifier_duplicate())
      that the dispatching SPEC never mentioned, via the prior task's own
      unlanded reviewer verdict plus my own direct test run.
- [x] Fixed it for real in `superboss-register.py`
      (`find_target_identifier_duplicate()` now passes `full=True`), plus a
      real bug in `tests/test_query_umr_limit_clamp_and_ensure_table_regression.py`
      (missing `VERIDIAN_SCRIPTS_DIR` in a subprocess test env, silently
      testing the stale live-deployed copy). Commits `42a56d3`, `6d1aaa8`,
      `89602b7` on PR #308's own branch.
- [x] Ran the full test suite (`VERIDIAN_SCRIPTS_DIR=<this checkout>
      python3 -m pytest -q`): real exit code 1, `15 failed, 1320 passed in
      878.36s`. Independently confirmed all 15 failures pre-existing/
      environmental by reproducing them byte-identically against
      `origin/main` in a disposable `git worktree` -- none diff-caused.
- [x] Posted a new Tier-1 `AUDIT:PASS` comment on PR #308 citing the real
      current head (`6d1aaa87`):
      https://github.com/FChecklist/veridian-scripts/pull/308#issuecomment-5287967734
- [x] Merged PR #308 to `main` (squash, merge commit `989fb5d5`).
- [x] Called `agent_work_briefing.py record-completion --umr-id
      UMR-20260813-235507-1710` with a real summary of the work above.

## Remaining
- [ ] None for this UMR's own scope. PR #308 is merged with a real
      Tier-1 AUDIT:PASS at head `6d1aaa87`; there was no re-audit
      outstanding by the time PR #339 (this task's own PR) was opened.
