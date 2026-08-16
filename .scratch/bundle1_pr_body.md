## Supersedes 10 conflicting open PRs (bundle 1 of the 2026-08-16 "land all conflicting PRs" dispatch, task-20260816-094442)

**Why a new PR instead of updating each original branch directly:** this
worker's PreToolUse branch-enforcement hook fail-closed blocks `git push` to
any branch other than this task's own single assigned branch. Same
precedent as PR#374 -> PR#377 (see
`progress/task-20260814-163404-rebase-veridian-scripts-pr374----real-au.md`).

Each of the 10 original PRs is real-merged here via `git merge --no-ff`
(one merge commit per PR, all original commits preserved, none squashed).
Every one of the 10 had exactly one conflicting file against current
`main` (`7330012`): `PROGRESS.md`, a disposable per-worker status stub (not
real content -- confirmed by reading its current content and several of the
source PRs' full diffs). Resolved by keeping the accumulating branch's copy
each time; every other file in every PR applied clean.

Diffstat vs `main`: 20 files changed, 2451 insertions(+), 6 deletions(-),
all real files from the 10 source PRs plus this task's own progress file.
`py_compile` clean on all touched `.py`, all touched `.json` valid,
21/21 relevant new tests passing.

### Originals superseded (real prior commits, not redispatched):
- #78 -- feat(OCID-020 cat19): sqlite_daily_backup.py + systemd unit
- #266 -- Register the real 965-issue resolution matrix in capability_registry
- #331 -- docs-only: RCA for UMR-20260808-215121-1e87 (killed)
- #332 -- docs-only: RCA for UMR-20260807-101751-68ff (killed)
- #370 -- fix(phase-continuation-tick): wire stale-swap-ratchet override
- #410 -- Phase 0: real stale-backlog reconciliation, 767 stuck tasks confirmed
- #412 -- fix(resource-governor): land next_queued_task owner-priority consumption
- #415 -- docs(audit): real zero-gap/zero-duplication audit re-run
- #428 -- docs-only: verify SPEC premise false for GTM cert Part3/4 Check 4
- #430 -- docs: document real owner_dispatch_gateway queued-row dispatch tick mechanism

#331, #332, #415, #428, #430 are documentation-only (no code files in their
diff) -- flagged docs-only, not recorded as fixes.

Requesting a real independent audit against this exact head SHA before merge
(never self-certified).
