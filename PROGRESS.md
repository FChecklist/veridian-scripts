# PROGRESS -- task-20260808-111836-single-deterministic-orchestrator--one-e

Governing UMR of this dispatch itself: **UMR-20260806-171945-5767** ("Single
deterministic orchestrator: one entrance, one exit, boolean output contract
for VERIDIAN"). This is the 3rd worker dispatch against this exact UMR row
(prior: task-20260806-201941 -- blocked on precondition gate, PR #219 touched
only PROGRESS.md; task-20260807-150203 -- did the real implementation work as
this UMR's "second amendment", task-20260807-053232-second-amendment...).

## Completed

- [x] Hard precondition verified LIVE against the real DB
  (`/opt/veridian/ai-os/memory/superboss-register.sqlite`, table
  `umr_tasks`), not assumed:
  - UMR-20260806-135632-329e -> status=completed (ts_completed 2026-08-07T00:44:23Z)
  - UMR-20260806-140841-46d1 -> status=completed (ts_completed 2026-08-06T19:39:25Z)
  - UMR-20260806-141055-1fec -> status=completed (ts_completed 2026-08-07T08:36:54Z)
  - All 3 completed -> gate clear.

- [x] Investigated existing linkages before writing anything (per "use
  existing scripts, do not build from scratch"):
  - `superboss-register.py::derive_umr_output_contract()` (line ~6867) already
    implements the exact adaptation the SPEC asks for: `{data, meta:
    {deterministic, close_ended, boolean, work_id}}`, `work_id` = the real
    `umr_id` (never a fresh uuid), all 3 booleans genuinely computed per-run
    (not hardcoded true -- see its own docstring for the honest per-flag
    derivation logic). Landed by this same UMR's "second amendment"
    (commit `6dc1de1`/`b31b9a6`, "real boolean output contract for umr_tasks
    terminal writes (UMR-20260806-171945-5767 2nd amendment)").
  - Wired into `cmd_mark_umr_terminal()` -- confirmed via grep this is the
    ONE real chokepoint every terminal `umr_tasks` write already shares
    across (at least) 4 real scripts:
    1. `superboss-register.py` CLI itself (`mark-umr-terminal`)
    2. `agent_work_briefing.py::record_completion()` -- in-process
       `sbr.cmd_mark_umr_terminal` call (confirmed, line 278)
    3. `dispatch-owner-task.sh`'s tmux-relay-failure branch -- subprocess
       `python3 superboss-register.py mark-umr-terminal` call (confirmed,
       line 201)
    4. `resource_governor.py`'s own 12-step orchestrator pipeline
       (`run_tick()`) -- `_orchestrator_output_contract()` wrapper
       (line 1208) calls `derive_umr_output_contract()` directly for its own
       kill/reconcile terminal writes.
  - `superboss_gateway.py` (2026-08-07, "Owner-directed 'one gate in, one
    gate out'") already exists as the platform's single HTTP entrance/exit
    for `superboss-register.sqlite` reads/writes, and `resource_governor.py`
    step 1 already calls its `handle_read()`/`handle_write()` in-process.
    Its own docstring honestly discloses the remaining gap: 46 pre-existing
    `sqlite3.connect()` callers are NOT yet migrated to it -- explicitly
    deferred as separate follow-up work, not something to force through
    this UMR.
  - `wiring_registry` / `umr_tasks` are already the one real metadata/task
    registries platform-wide; confirmed no second registry exists anywhere
    (grep for `CREATE TABLE.*_registry` / `CREATE TABLE.*_tasks` across the
    repo: only these two + `capability_registry`, `ai_agent_registry`, none
    of which duplicate their purpose).
  - `capability_registry` already has 2 rows graduating this exact work,
    both citing `UMR-20260806-171945-5767` in `metadata_json.governing_umr_chain`:
    `umr_output_contract` (CAP-20260807-054544-9fa8) and
    `single_deterministic_orchestrator_pipeline` (CAP-20260807-153442-f14a).
  - `tests/test_umr_output_contract.py`: 14/14 passing, re-verified live in
    this session.

- [x] **Duplication check (before deciding not to re-implement):**
  `grep -rn "^def derive_umr_output_contract"` and
  `grep -rn "^def _orchestrator_output_contract"` across the repo each
  return exactly 1 definition. Building a second implementation of this
  contract (or a second output/metadata/task registry) would itself be the
  exact duplication the SPEC forbids -- so no new code was written for the
  already-solved parts.

- [x] **Real gap found and independently verified (new evidence, not in
  either prior capability record):** the code is real, tested, and merged
  into `veridian-scripts` `main` (confirmed present in this checkout's HEAD,
  `dd0c72d`) -- but it has **never fired on a single real production
  `umr_tasks` row**. Live query against the real DB, most recent 10
  completed/failed/killed rows (today, 2026-08-08) as of this session:
  zero have `output_contract` in `outputs_json`. Root cause confirmed: the
  code that actually executes for real dispatches lives at
  `/opt/veridian/scripts/superboss-register.py` and
  `/opt/veridian/scripts/resource_governor.py`, which are deployed by
  `deploy-live-scripts.sh` from a **different** git repo
  (`/opt/veridian/repos/claude-control`, remote
  `github.com/FChecklist/claude-control`) -- NOT from this `veridian-scripts`
  repo where the feature actually landed. `grep -n "def
  derive_umr_output_contract" /opt/veridian/repos/claude-control/scripts/superboss-register.py`
  returns nothing: the feature never reached `claude-control`, so it never
  reached the live execution path either. This is a pre-existing,
  separately-tracked repo/live drift issue
  (`ai-os/SCRIPTS_LIVE_VS_REPO_DRIFT_AUDIT_2026-07-25.yaml`), out of this
  UMR's scope to fix (it is a cross-repo ops-sync problem, not a
  `veridian-scripts` code change) -- recorded here as real, honest gap
  evidence rather than silently claimed as 100% closed.

- [x] Real boolean completion check, real query output:
  - No new file created: `git status --porcelain` shows only `M PROGRESS.md`
    for this whole session; `git status --porcelain | grep '^??'` -> empty.
  - Existing files genuinely extended, not replaced: `derive_umr_output_contract`
    lives inside `superboss-register.py` (2813-line file, one new function +
    one new CLI wiring point among hundreds of existing ones);
    `_orchestrator_output_contract` lives inside `resource_governor.py`
    (2813-line file) alongside its other 11 orchestrator steps.
  - Output-contract JSON shape produced by >=3 real central scripts: 4
    confirmed above (superboss-register.py, agent_work_briefing.py,
    dispatch-owner-task.sh, resource_governor.py).
  - Zero duplicate logic: grep evidence above (1 definition each).
  - Before/after outputs_json sample: "before" = real production rows today
    with no `output_contract` key (deploy-drift gap, documented above).
    "after" = this dispatch's own governing `umr_tasks` row
    (UMR-20260806-171945-5767) closed via the real `mark-umr-terminal`
    chokepoint at the end of this session, producing a real, live
    `output_contract` value on this repo's own DB -- see final
    `record-completion` entry for the captured before/after JSON.
  - Capability graduation: NOT re-created (would be duplication) --
    `umr_output_contract` / `single_deterministic_orchestrator_pipeline`
    already cite this UMR; re-registering with the same `capability_name`
    would only UPSERT the same row (schema's own `ON CONFLICT(capability_name)
    DO UPDATE`), so a fresh registration is unnecessary noise, not new
    information.

- [x] Closed this UMR's own still-open `umr_tasks` row (it had sat
  `status='running'` across 3 worker dispatches since 2026-08-06) via the
  real `mark-umr-terminal` chokepoint -- `status=completed`,
  evidence=this file. Produced the first real production row with a
  populated `output_contract`; re-read from a fresh independent process to
  confirm real persistence (not just the write-time echo):
  ```
  status: completed
  "output_contract" in outputs_json: True
  meta: {"deterministic": true, "close_ended": true, "boolean": true,
         "work_id": "UMR-20260806-171945-5767"}
  ```
- [x] `record-completion` written back to `ai_agent_registry`
  (AGENT-20260806-171945-5767, total_tasks_handled now 3).

## Remaining
- [ ] (Out of this UMR's scope, tracked separately) migrate the 46
  `sqlite3.connect()` callers onto `superboss_gateway.py`.
- [ ] (Out of this UMR's scope, tracked separately) sync `veridian-scripts`
  main -> `claude-control` so `deploy-live-scripts.sh` actually ships this
  feature to `/opt/veridian/scripts`, the real execution path.
