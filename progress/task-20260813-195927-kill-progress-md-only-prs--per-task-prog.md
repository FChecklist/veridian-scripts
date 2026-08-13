# PROGRESS -- task-20260813-195927-kill-progress-md-only-prs--per-task-prog

## SPEC
UMR-20260813-195922-f548 (governing chain UMR-20260806-171945-5767): stop
workers recording progress by rewriting a single shared PROGRESS.md, which
let doc-only "fixes" ship as real ones (PR #315/#317/#321) and put every
long-lived branch that touched it into a mechanical merge conflict with
every other one.

## Completed
- [x] Verified live via `gh pr view --json files` against
      FChecklist/veridian-scripts: PR #317 and #321 are PROGRESS.md-only
      diffs; their titled objectives (dispatch_core.py,
      pm-sentinel-tick.sh) are untouched. PR #315 is also PROGRESS.md-only.
- [x] Corrected the dispatching SPEC's own claim: PR #297 is NOT explained
      by this defect -- its diff never touches PROGRESS.md at all (real
      code files, real merge conflict, unrelated cause).
- [x] Quantified the blast radius: of 25 parseable open+DIRTY PRs on
      veridian-scripts, 17 are PROGRESS.md-only diffs stuck CONFLICTING.
- [x] Found and quoted the real worker template line
      (worker-entrypoint.sh's `PROGRESS_INSTRUCTION`) that told every
      worker to maintain one shared PROGRESS.md.
- [x] Rewrote `PROGRESS_INSTRUCTION` (and the RESUME prompt) to point at a
      per-task `progress/<task_id>.md` file instead.
- [x] Added `progress_completion_gate.py` (check-completion + rollup) and
      wired `check-completion` into `worker-entrypoint.sh` as a real,
      mechanical gate before pending_review -- rejects a doc-only diff for
      a code-named objective with an explicit blocked status, never a
      silent success.
- [x] Added `tests/test_progress_completion_gate.py` (10 tests): real git
      merges proving two concurrent per-task progress files never conflict
      (plus a control case proving the OLD shared-file scheme really did),
      and the completion gate rejecting/accepting real diffs. `python3 -m
      pytest tests/test_progress_completion_gate.py -q` -> exit code 0, 10
      passed.
- [x] Added a deprecation banner to the legacy root PROGRESS.md pointing at
      the new convention, left as historical record (not deleted).
- [x] Opened PR against FChecklist/veridian-scripts with real code changes
      and this evidence in the body; recommended closing #315/#317/#321 as
      empty (PROGRESS.md-only) fixes.

## Remaining
- [ ] Human/supervisor review + merge of the veridian-scripts PR.
- [ ] Once merged, someone should close #315/#317/#321 (empty fixes) and
      re-dispatch their real objectives (dispatch_core.py swap-gate fix,
      pm-sentinel-tick.sh positional systemctl parse fix) as fresh tasks --
      out of scope for this fix itself, which only closes the mechanical
      hole that let them ship empty.
