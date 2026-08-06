# PROGRESS -- task-20260806-043900-collision-signal-narrow-umr-match

SPEC: UMR-20260806-043900-8c48 (real PM finding, confirmed live in
umr_tasks), relates to UMR-20260806-041307-0bfd and real merged PR #115.
Section 12 (deterministic collision detection) was too broad: raw file-path
overlap across the FULL open-PR historical backlog, no exclude list, no
recency scoping -- flooded the section with ~12,800 false-positive lines
(unrelated PRs merely both touching a normal shared file like
PROGRESS.md/package.json), pushing the whole report to 3.7MB/13,960 lines.

## Fix (PM's exact spec, all 4 items)

1. **Primary signal redefined**: two open PRs (or two running
   veridian-worker@*/veridian-supervisor@* units) citing the same real
   UMR-YYYYMMDD-HHMMSS-xxxx id OR the same real task-YYYYMMDD-HHMMSS-<slug>
   task-identity token, anywhere in title+body+branch-name (PRs) or
   prompt.txt (workers) -- `extract_citation_tokens()`,
   `detect_pr_citation_collisions()`. This is what every real collision
   this session (PR #98/#100, #102/#103, #110/#111, #115/#116) actually
   looked like.
2. **File-overlap demoted to secondary, time-scoped**: `detect_pr_file_collisions()`
   now only considers PRs with `createdAt` within the last
   `COLLISION_FILE_OVERLAP_MAX_AGE_HOURS` (48) hours -- not the full
   historical backlog. PRs with missing/unparseable `createdAt` are treated
   as NOT recent (conservative default).
3. **Fixed exclude list added**: `COLLISION_FILE_OVERLAP_EXCLUDE_FILES` --
   `PROGRESS.md`, `package.json`, `package-lock.json`, `bun.lock`,
   `yarn.lock`, `pnpm-lock.yaml`, `ai-os/boss/ACTIVE-CLAIMS.yaml`,
   `ai-os/CONSTITUTION.yaml`, `src/lib/db/schema.ts`, `tsconfig.json` (the
   PM's named list plus `yarn.lock`/`pnpm-lock.yaml`/`tsconfig.json`, found
   while implementing -- `bun.lock` confirmed present as
   `compliance-tracker`'s real lockfile).
4. **Hard output cap added regardless of 1-3**: `_rank_collision_candidates()`
   (primary before secondary, fixed order) + `_cap_collision_candidates()`
   (`COLLISION_CANDIDATE_CAP_TRIGGER=200`, `COLLISION_TOP_K=50`) -- if
   candidates exceed the trigger, only the top 50 render plus an honest
   "N candidates found, showing top K" summary line.

`SCRIPT_VERSION` bumped 3.1.0 -> 3.1.1.

## Real before/after verification

Ran the updated script for real (`--no-db-write`) against the live server:

| | before (PR #115, merged) | after (this fix) |
|---|---|---|
| total report lines | 13,668 | 877 |
| total report size | ~3.79 MB | 312,548 bytes (~305 KB) |
| Section 12 lines alone | ~12,844 | 61 |
| Section 12 candidates | unbounded raw file-overlap dump | total=1263, primary=1020, secondary=243, capped, 50 shown |

Section 12 itself (this fix's exact scope) is now bounded and correct:
61 lines, well under "a few hundred". The remaining ~305KB is dominated by
Section 11 (649 real stuck tasks, one line each -- the original
UMR-20260806-041307-0bfd spec's own "fold one real line per real stuck
task" requirement, already independently reviewed and merged in PR #115,
not part of this task's diagnosed root cause). Flagging as a real, honest
observation for a future PM decision if that section also needs a cap --
not silently touched here, same "AI does not decide novel scope for
itself" discipline as the prior task's report.

## Tests

18 new/rewritten tests: `extract_citation_tokens()` (UMR + task-identity),
`_pr_is_recent()` (in-window/out-of-window/missing-or-unparseable
`createdAt`), `detect_pr_citation_collisions()` (real match, no match,
task-identity-only match), `detect_pr_file_collisions()` (excludes known
common files, real code-file overlap still flags, ignores PRs outside the
48h window and never even fetches their diffs), `detect_worker_umr_collisions()`
(updated field names), `_rank_collision_candidates()`,
`_cap_collision_candidates()` (under/over trigger), and 3 real end-to-end
`get_collision_detection_section()` tests (combines primary+secondary
correctly, PROGRESS.md-only overlap produces zero secondary collisions, and
a real 60-PR same-UMR fixture proves the hard cap actually bounds output to
`COLLISION_TOP_K`). Full suite: **242/242 passing**, zero regressions.

## Completed
- [x] Root cause understood and independently re-derived from the real
      before-fix output (not just taken on faith).
- [x] All 4 required fix items implemented, each documented in the module
      docstring/inline comments citing UMR-20260806-043900-8c48.
- [x] `SCRIPT_VERSION` bumped 3.1.0 -> 3.1.1.
- [x] Real before/after size comparison captured (see table above).
- [x] Tests added/rewritten, 242/242 full suite passing.

## Remaining
- [ ] Open PR, get independent review (adopt + supervisor), confirm real
      merge lands, deploy to live `/opt/veridian/scripts`, run for real once
      more and capture final evidence.
