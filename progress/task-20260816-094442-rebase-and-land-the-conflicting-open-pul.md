# PROGRESS -- task-20260816-094442-rebase-and-land-the-conflicting-open-pul

SPEC: land ALL open conflicting pull requests of FChecklist/veridian-scripts
(this dispatch owns the CONFLICTING half; a sibling dispatch owns the
cleanly-mergeable half; a third dispatch owns the lifecycle orchestrator
module -- avoided editing outside branches being resolved here).

## Real live list re-derived (2026-08-16, ~10:00Z)
`gh pr list --state open` showed 34 open PRs, 30 with mergeable=CONFLICTING
(SPEC's cited snapshot said 28 at 09:35Z -- drift is expected, more PRs
opened/closed since; all 10 numbers SPEC explicitly named -- 412, 410, 405,
370, 357, 355, 332, 331, 276, 273 -- are present in the live 30).

Conflicting set (30): 8, 61, 65, 72, 78, 79, 198, 204, 266, 273, 276, 331,
332, 355, 357, 370, 405, 410, 412, 415, 416, 417, 419, 422, 423, 424, 428,
429, 430, 435.

**Structural constraint discovered (mechanical, not a workaround):** the
PreToolUse worker-branch-enforcement hook (task-20260814-132651) fail-closed
blocks `git push` to any branch other than this task's own single assigned
branch (`worker/task-20260816-094442-...`). This matches the precedent in
`progress/task-20260814-163404-rebase-veridian-scripts-pr374----real-au.md`
(PR#374 -> PR#377): a conflicting PR's own head branch cannot be pushed to
directly by a dispatched worker. Applied the same precedent here, adapted
for volume: resolved-conflict commits are pushed to this task's own single
branch, opened as a new PR that supersedes the original(s), each original PR
closed afterward with a comment pointing at the merged replacement. Given
only one pushable branch exists for this whole task, PRs whose real files
don't overlap are bundled into one superseding PR per cycle (still
preserving every original commit via real `git merge --no-ff`, never
squashed) to keep the independent-audit-per-cycle cost tractable; PRs
touching overlapping files are processed individually/in small groups.

`git merge-tree --write-tree` triage of all 30 (base=merge-base with
origin/main, real 3-way, no working-tree checkout needed) found: for every
one of the 30, `PROGRESS.md` is a real, deterministic conflict (each worker
branch fully overwrote its content; current main's copy is a disposable
per-worker stub, not real content -- confirmed by reading main's own copy
and several PRs' diffs, e.g. #331/#332 whose ENTIRE diff is a full
overwrite of that same disposable stub with an already-acted-on RCA
writeup). Resolution rule applied uniformly: keep the accumulating branch's
(main-descended) PROGRESS.md, drop the incoming PR's version -- confirmed
by diffstat, real diffstat, no real file is dropped, matches the file
this repo's own current protocol says not to use).

12 of the 30 have PROGRESS.md as their ONLY conflicting file (real code
auto-merges clean against current main): 78, 266, 331, 332, 370, 410, 412,
415, 419, 428, 429, 430. Of those, 419 and 429 mutually touch
`queue-manager.py`/`timer-manager.py` so were pulled out of the first
bundle and handled separately. The other 18 have a real code conflict in
at least one file needing manual read of both sides.

## Completed
- [x] Re-derived live conflicting-PR list via `gh pr list` + verified against
      SPEC's named numbers (see above).
- [x] Full `git merge-tree` triage of all 30 PRs vs current origin/main
      (7330012): per-PR conflicting-file lists captured in
      `.scratch/triage_results2.json` (scratch, not part of the real diff).
- [x] Bundle 1 (10 non-overlapping PROGRESS.md-only-conflict PRs: #78, #266,
      #331, #332, #370, #410, #412, #415, #428, #430) real-merged via
      `git merge --no-ff` into this branch, one real merge commit per
      original PR (all original commits preserved, not squashed).
      PROGRESS.md resolved by keeping the accumulating branch's copy each
      time (confirmed cosmetic-only via diffstat: 20 files changed, all real
      files, PROGRESS.md the only 2-line entry). `py_compile` clean on all
      touched `.py` files, all touched `.json` valid, 21/21 relevant new
      tests passing (`test_sqlite_daily_backup.py`,
      `test_resource_governor_next_queued_task_owner_priority.py`,
      `test_phase_continuation_tick_stale_swap_override.py`).
- [x] docs-only labeling: #331 and #332 are pure RCA writeups (their entire
      diff is the disposable PROGRESS.md stub, no code) -- recorded as
      docs-only below, not as fixes.

## Remaining
- [ ] Push bundle-1 branch, open superseding PR, get a real independent
      audit (separate agent instance, never self-certified) against the
      exact pushed head SHA, merge only on a genuine PASS citing that SHA.
- [ ] Close original PRs #78, #266, #331, #332, #370, #410, #412, #415,
      #428, #430 pointing at the merged replacement.
- [ ] Process #419 + #429 (mutual file overlap: queue-manager.py,
      timer-manager.py) as their own cycle.
- [ ] Process the 18 remaining real-code-conflict PRs: 8, 61, 65, 72, 79,
      198, 204, 273, 276, 355, 357, 405, 416, 417, 422, 423, 424, 435 --
      prioritise newest-first per SPEC ("oldest-conflicting last"), read
      both sides of every real conflict, never discard either wholesale.
- [ ] Final report table (number, outcome, mergedAt/blocking reason, main
      SHA per merge); report explicitly which numbers were not reached if
      budget/time runs out before all 30.

## Outcome
In progress -- see Completed/Remaining above.
