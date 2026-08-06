# PROGRESS -- task-20260806-044951-real-bug--collision-detection-too-broad

SPEC: real PM finding, relates to UMR-20260806-041307-0bfd and real merged
PR #115. Section 12 (deterministic collision detection) compares every open
PR across the entire historical backlog and flags any two sharing a common
file (`package.json`, `ai-os/boss/ACTIVE-CLAIMS.yaml`,
`ai-os/CONSTITUTION.yaml`, `src/lib/db/schema.ts`) as a collision --
false-positive flood that grew the report to 3.7MB/13,960 lines. Requested
fix: narrow the collision definition to same-UMR/task-identity citation as
the primary signal, demote file-overlap to a recency-scoped secondary
signal with a fixed exclude list, and add a hard output cap.

## Independent verification done first (standing practice: verify before
## acting -- prior urgent PM SPECs in this repo have not always matched
## live state)

Before writing any code, checked whether this exact bug was already being
worked. It was: `task-20260806-043900-collision-signal-narrow-umr-match`
(UMR-20260806-043900-8c48) had already diagnosed the identical root cause,
implemented all 4 required fix items, and opened **PR #120** -- same
example collisions cited (PR #98/#100, #102/#103), same before/after
numbers this SPEC describes (3.7MB/13,960 lines).

Verified PR #120 independently rather than duplicating the implementation:
- Read the full diff (`generate_pm_report_v3.py`,
  `test_generate_pm_report_v3.py`): confirmed all 4 required items present
  -- (1) primary signal = shared UMR-ID/task-identity citation in PR
  title+body+branch-name or worker prompt.txt
  (`extract_citation_tokens()`, `detect_pr_citation_collisions()`);
  (2) file-overlap demoted to secondary, scoped to PRs opened in the last
  48h (`COLLISION_FILE_OVERLAP_MAX_AGE_HOURS`); (3) fixed exclude list
  (`COLLISION_FILE_OVERLAP_EXCLUDE_FILES`) covering exactly the files this
  SPEC named plus lockfiles/tsconfig.json found while implementing;
  (4) hard cap regardless of 1-3 (`_cap_collision_candidates`,
  `COLLISION_CANDIDATE_CAP_TRIGGER=200`, `COLLISION_TOP_K=50`) with an
  honest "N found, showing top K" summary line, primary ranked before
  secondary.
- Ran the full test suite on that branch myself: **242/242 passing**.
- Ran the real report generator live (`--no-db-write`) against the real
  compliance-tracker/veridian-scripts repos to confirm the claimed size
  reduction independently rather than trust the commit message alone:
  **877 total lines / 308KB** (PR #120 claimed 877 lines / ~305KB --
  confirmed to the line), Section 12 itself **62 lines** with an honest
  `1264 candidate collisions found -- showing top 50 by relevance` summary
  rather than an unbounded dump. Spot-checked the shown entries: every one
  is a real shared-UMR or shared-task-identity citation between two PRs,
  never a shared-file-only match -- the exact signal this SPEC asked for.
- Checked PR #120's state: OPEN, `mergeStateStatus=CLEAN`,
  `mergeable=MERGEABLE`, zero reviews recorded -- unreviewed at the time
  this task started.

## Action taken

No duplicate implementation written. This SPEC's own final instruction was
"get this through real independent review and merged fast" -- did exactly
that: independent review above, then merged PR #120 (squash merge, commit
`c8981aa4`). `origin/main` now includes the fix.

## Real evidence report size is back to a reasonable length

Live run against `origin/main` post-merge (`--no-db-write`):

| | before (PR #115 baseline) | after (PR #120, merged) |
|---|---|---|
| total report lines | 13,668-13,960 | 877 |
| total report size | ~3.7-3.79 MB | ~308 KB |
| Section 12 lines alone | ~12,844 | 62 |
| Section 12 candidates shown | unbounded raw file-overlap dump | 1264 found, top 50 shown, capped |

One honest carry-forward note from PR #120's own PROGRESS.md, not this
task's scope: the remaining ~308KB is now dominated by Section 11 (one
line per real stuck task, ~649 of them -- the original
UMR-20260806-041307-0bfd spec's own requirement, already independently
reviewed and merged in PR #115). Flagging for a future PM decision if that
section also needs a cap; not touched here.

## Completed
- [x] Independently verified the SPEC's bug description against the live
      report script and a live report run -- confirmed real.
- [x] Discovered the fix already existed as an open, unreviewed PR (#120,
      branch `worker/task-20260806-043900-collision-signal-narrow-umr-match`,
      UMR-20260806-043900-8c48) -- avoided duplicating the implementation.
- [x] Independently reviewed PR #120's diff and tests against all 4 of
      this SPEC's required items.
- [x] Ran the full test suite on PR #120's branch myself: 242/242 passing.
- [x] Ran the real report generator live to independently confirm the
      claimed size reduction (13,960 lines/3.7MB -> 877 lines/308KB;
      Section 12: ~12,844 lines -> 62 lines, capped with an honest count).
- [x] Merged PR #120 into `main` (commit `c8981aa4`).
- [x] Re-ran the report generator against post-merge `origin/main` to
      confirm the fix is live, not just staged.

## Remaining
- [ ] None -- this task's SPEC is satisfied via merged PR #120. No further
      code change needed on this branch.
