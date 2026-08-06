# PROGRESS -- task-20260806-181146-critical-amendment--every-task-must-sear

SPEC: critical amendment to UMR-20260806-124327-6ffb and stop work order
UMR-20260806-124055-bc80 (this task's own scoped UMR: UMR-20260806-124654-a8d6).
Required deterministic-first sequence for every task, before any AI
involvement: step one, exact capability_registry script match, no AI, stop.
Step two, if no exact match, real cross-history search over past umr_tasks
for similar work already done, report the script/agent ids used. Step three,
only then does AI work proceed, under a UMR-scoped agent_id (already
specified elsewhere). Step four -- **the real critical new requirement this
amendment adds** -- the moment AI work completes, a mandatory deterministic
evaluation must run: can this become a permanent script; if yes, build +
register it and record which UMR/agent_id it graduated from; if no, record
the honest judgment-required reason plainly, never skip this step.

Independent verification before building (per the recurring false-premise
dispatch-storm pattern already in memory): confirmed both cited UMRs are
real and live (`UMR-20260806-124055-bc80` -> merged PR #201;
`UMR-20260806-124327-6ffb` -> spawned sibling task
`task-20260806-181141-real-priority-conflict-found--five-2026`, dispatched
5 seconds before this task, itself chartered to build steps one-three's
unified orchestrator). Found a live dispatch storm: 5 tasks
(181141/181146/181150/181155/181159) all fired within 18 seconds of each
other under the same theme. To avoid duplicating work already in flight on
two other open, unmerged PRs (#194, #199 -- ai_agent_registry/
agent_work_briefing, confirmed unmerged by direct inspection) and the
concurrently-dispatched sibling task, this task deliberately does **not**
touch the ai_agent_registry table/agent_id lifecycle (step three) -- it
builds steps one, two, and four only, as clean, additive, well-tested
extensions to `superboss-register.py` (the repo's own confirmed canonical
read/write script for this database).

## Completed
- [x] Verified UMR-20260806-124327-6ffb / UMR-20260806-124055-bc80 are real,
      live rows in `umr_tasks` (not a false premise) and traced their real
      lineage/sibling tasks before writing anything.
- [x] Added `capability_graduation_log` table (11th tree) to
      `superboss-register.py`'s `init_db()` schema + standalone
      `_ensure_capability_graduation_log_table()`, same dual-definition
      convention as every other table in this file.
- [x] Built `search_task_precedent()` / `search-task-precedent` CLI (steps
      one + two): exact-then-FTS match against `capability_registry` (same
      two-stage `resolution_order` as `lookup_capability()`, with an honest
      `broad_keyword_overlap` flag for low-specificity FTS hits, mirroring
      the real imprecision `agent_work_briefing.py` independently found and
      fixed the same way), then a real cross-history search over
      `umr_tasks` + `capability_graduation_log` (not scoped to one UMR).
- [x] Built `record_capability_graduation()` / `record-graduation` +
      `list-graduations` CLI (step four): mandatory, never-skippable
      post-AI-work evaluation. `decision='graduated'` is refused
      (`ValueError`/CLI exit 1) without a real, already-registered
      `capability_id` + `script_path`; `decision='judgment_required'` is
      refused a `capability_id`/`script_path` -- no script implied without
      one actually built. Insert-only, full history stays queryable.
- [x] `tests/test_capability_graduation.py` -- 13/13 passing, covering both
      steps' happy paths, the exact-vs-keyword resolution stage, all
      constraint-violation error paths, and full CLI round trips.
- [x] Full existing suite re-run: 361/361 passing (no regressions).
- [x] Dogfooded step four for real on this task's own live output: both new
      capabilities registered in the live `capability_registry`
      (`task_precedent_search` = CAP-20260806-182313-9028,
      `capability_graduation_recording` = CAP-20260806-182326-0e3e), and one
      real `capability_graduation_log` row recorded
      (GRAD-20260806-182333-2e4e, decision=graduated, umr_id=
      UMR-20260806-124654-a8d6) -- verified live via `list-graduations`.
      `agent_id` on that row is an honest placeholder
      (`AGENT-UNASSIGNED-ai_agent_registry-cli-not-yet-merged-see-PR-194-199`)
      since `ai_agent_registry` has zero rows and no CLI wiring merged to
      main at build time -- recorded plainly rather than fabricated.

## Remaining
- [ ] Step three (UMR-scoped agent_id reuse/mint) is intentionally left to
      PR #194 / #199 / the concurrently-running orchestrator task
      (task-20260806-181141) -- once one of those merges, this task's own
      graduation row's placeholder `agent_id` should be reconciled to the
      real minted agent_id (pointer already left in that row's own
      `metadata_json`/capability metadata for whoever does that).
