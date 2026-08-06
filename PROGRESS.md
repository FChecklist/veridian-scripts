# PROGRESS -- task-20260806-070019-register-real-umr-for-pm-self-audit-and

SPEC: Owner directive, mint the real permanent UMR for two real standing
items queued before the lock incident -- (1) citation-only UMR for the PM
self audit already answered in chat, (2) the PROJECT MANAGER IN SERVER
directive (real always-on server-side deterministic orchestration, real
agent identity + persistent memory, real script-vs-agent routing) --
analysis and design only, no build, investigate existing architecture
first to avoid duplication, deposit findings for review.

## Independent verification done first (standing practice: verify before
## acting -- prior urgent PM SPECs in this repo have not always matched
## live state -- see MEMORY note veridian-task-prompt-false-premise-pattern)

Before doing any new work, checked whether this exact SPEC was already
being worked, because 4 near-identical `owner-task-20260806-065103-*`
dispatch rows exist in `umr_tasks`, all minted the same second
(065104), all titled "Register real UMR for PM self audit and
PROJECT MANAGER IN SERVER orchestration directive" (or the near-identical
"...and orchestration directive"):

| UMR | unit_name (this task's dispatch) |
|---|---|
| `UMR-20260806-065104-c69a` | **this task**, `task-20260806-070019-...` |
| `UMR-20260806-065104-844e` | `task-20260806-070026-...` |
| `UMR-20260806-065104-598e` | `task-20260806-070148-...` |
| `UMR-20260806-065104-4432` | `task-20260806-070143-...` |

All 4 are the same real Owner dispatch, duplicated 4x by the dispatch
gateway (same pattern as OCID-053/054/055 documented in
`OCID_001_069_CANONICAL_UMR_MAPPING_METHODOLOGY_2026-08-05.md`). All 4
are now `status=running` under 4 separate worker units/tasks -- a live
4-way collision risk on the exact same deliverable.

**Found the substantive work already done, under this same task, in an
earlier context window before a reset:**
- `pm_decisions_pending` row **id=6** (`related_umr=UMR-20260806-065104-c69a`,
  `decision_type=owner_proposal`, `status=open`) -- the full
  PROJECT MANAGER IN SERVER investigation + gap analysis + proposed
  additive design (`orchestrator_router.py` + new `agent_identity` table),
  citing real files/lines throughout (`resource_governor.py`,
  `dispatch_core.py`, `veridian-task.py`, `generate_pm_report_v3.py`,
  `wiring_registry`, PR #114).
- Full findings/design doc:
  `/opt/veridian/workspace/UMR-20260806-065104-c69a_PROJECT_MANAGER_IN_SERVER_FINDINGS_AND_DESIGN_2026-08-06.md`
  (23KB, real file:line citations for every "already exists" claim,
  explicitly labeled "ANALYSIS AND DESIGN ONLY").
- That document itself already identifies and dismisses the 4-way
  dispatch duplication as a non-issue (`-844e`/`-598e`/`-4432` are
  duplicate rows of the same dispatch, not separate scope) -- confirmed
  still true on re-check just now (all 4 still exist, all `running`, none
  `completed`, `pm_decisions_pending id=6` still `open`, unchanged).

**Re-verified independently, current state:**
- `pm_decisions_pending id=6`: still `status=open`, `closed_ts=NULL` --
  unmodified since deposit, awaiting real PM/Owner review, not decided.
- The 4 duplicate `umr_tasks` rows: all still `running`, none
  `unit_name`-linked to a completed run, no `ts_completed`.
- This workspace (`worker/task-20260806-070019-...`): `git status` shows
  no changes beyond this `PROGRESS.md`; branch HEAD == `main` tip
  (`87aeb74`) -- **zero build/code changes made**, confirming the prior
  session's "no branch, no commit, no PR" claim still holds.
- No PR exists for this branch (`gh pr list --head
  worker/task-20260806-070019-...` -> empty).

## Completed
- [x] Independently verified the 4-way duplicate dispatch is real but
      already reconciled (single canonical UMR already established)
- [x] Confirmed the real, permanent, canonical UMR: **`UMR-20260806-065104-c69a`**
      -- serves as citation for item 1 (PM self audit, already answered
      in chat -- no further artifact needed from this side, the UMR's
      existence *is* the citation, per the SPEC's own framing)
- [x] Confirmed item 2 (PROJECT MANAGER IN SERVER) findings + design
      already deposited for PM/Owner review: `pm_decisions_pending id=6`
      + `/opt/veridian/workspace/UMR-20260806-065104-c69a_PROJECT_MANAGER_IN_SERVER_FINDINGS_AND_DESIGN_2026-08-06.md`
- [x] Confirmed no build/implementation started anywhere under this UMR
      (DB-only proposal row + a scratch-dir doc, zero git changes, zero PRs)
- [x] docs-only commit + push (this file)

## Remaining
- [ ] None from this task's scope. Awaiting real PM/Owner decision on
      `pm_decisions_pending id=6` before any `orchestrator_router.py`
      build is authorized.
