# PROGRESS -- task-20260806-074622-real-bug--collision-detection-too-broad

## Completed
- [x] Independently verified the SPEC's premise against live repo state (per
      the recurring veridian-scripts task-dispatch false-premise pattern --
      confident SPEC claims have repeatedly not matched live state; do not
      write/fix without checking first).
- [x] Finding: **this exact bug is already fixed and merged.** PR #115
      (`9dc8b72`, UMR-20260806-041307-0bfd) introduced the naive Section 12
      collision detector that compared every open PR across the full
      historical backlog and flagged any shared common file
      (`package.json`, `ai-os/boss/ACTIVE-CLAIMS.yaml`,
      `ai-os/CONSTITUTION.yaml`, `src/lib/db/schema.ts`, etc.) as a
      collision -- flooding the report to **3.7MB / 13,960 lines**. This is
      documented verbatim in `generate_pm_report_v3.py`'s own module
      docstring (Section 12 block, line ~166).
- [x] That bug was found and fixed the same day in PR #120 (`c8981aa`,
      UMR-20260806-043900-8c48, merged 2026-08-06 10:24:40 +0530, ~20 min
      after PR #115 merged at 10:04:57). The fix is already an ancestor of
      this task's branch (`git merge-base --is-ancestor c8981aa HEAD` ->
      true) and is exactly what this SPEC asks for:
        - PRIMARY signal: pairwise UMR-id / task-identity citation-token
          match across PR title+body+branch (`detect_pr_citation_collisions`)
          and running worker/supervisor units (`detect_worker_umr_collisions`)
          -- no shared-file heuristic involved.
        - SECONDARY signal: file-overlap, narrowed to PRs opened in the last
          `COLLISION_FILE_OVERLAP_MAX_AGE_HOURS` (48h) only, with a fixed
          `COLLISION_FILE_OVERLAP_EXCLUDE_FILES` list already covering every
          file the SPEC names (`package.json`,
          `ai-os/boss/ACTIVE-CLAIMS.yaml`, `ai-os/CONSTITUTION.yaml`,
          `src/lib/db/schema.ts`, plus lockfiles/tsconfig.json/PROGRESS.md).
        - Hard cap: `COLLISION_CANDIDATE_CAP_TRIGGER=200` /
          `COLLISION_TOP_K=50` via `_cap_collision_candidates()` /
          `_rank_collision_candidates()`, with an honest
          "N found, showing top K" summary -- never an unbounded dump.
      `SCRIPT_VERSION` is already at 3.1.2, and its changelog comment names
      the 3.1.1 bump as exactly this fix.
- [x] Ran the full test suite: `python3 -m pytest test_generate_pm_report_v3.py -q`
      -> 69 passed, including 15 collision-specific tests covering the
      exclude list, the 48h age cutoff, the citation-match primary signal,
      and the cap/rank behavior.
- [x] No code change made -- there is nothing left to fix. Making a
      speculative second change on top of an already-correct, already-tested
      fix would only add risk.

## Remaining
- [ ] None. Recommend closing this task as a duplicate of the work already
      delivered in PR #120 (UMR-20260806-043900-8c48).
