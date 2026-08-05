# PROGRESS -- task-20260805-165226-clarification--real-precise-search-key-f

## Completed
- [x] Searched `umr_tasks` in the live DB (`/opt/veridian/ai-os/memory/superboss-register.sqlite`)
      by exact `task_identity = 'owner-task-20260805-115043-1829823'` (not a `umr_id` text
      search) per the PM's precise-key instruction.
- [x] Result: **found, exactly 1 row** -- not a zero-row anomaly. No registration/persistence
      gap during the tmux crash window.
      - `umr_id` = `UMR-20260805-115044-b481` (matches)
      - `task_identity` = `owner-task-20260805-115043-1829823` (matches)
      - `status` = `running`, `ts_submitted` = `2026-08-05T11:50:44Z`,
        `ts_dispatched` = `2026-08-05T16:52:20Z` (~5h after submission)
      - `outputs_json` shows a **successful** dispatch: `new_task_id` =
        `task-20260805-165217-urgent--stop-real-duplicate-workers-re-e`, `returncode: 0`,
        worktree prepared. So the original warning ("tmux session not found, delivery had not
        yet happened") was a transient/stale snapshot -- delivery has since genuinely happened,
        just ~5h late, not a registration failure.
      - Note: `instruction_id` (`INS-20260805-115043-c20a`) and `work_item_id`
        (`WRK-20260805-115044-1197`) from the PM's original capture are not columns on
        `umr_tasks` (no such fields in the schema) -- could not be cross-checked against this
        table; only the `umr_id`/`task_identity` fields were verifiable here.
- [x] Followed up on whether the duplicate-worker investigation (the content of
      `task-20260805-165217-...`, dispatched from this same UMR) is still needed:
      - Independently inspected all three suspect workspaces
        (`task-20260805-114126-...ocid-068...`, `task-20260805-114207-...pre-merge-gate`,
        `task-20260805-114214-...metadata-index-coverage...`): all three have **clean git
        status** (no crash-shaped uncommitted state) and each had **already self-identified**,
        on its own, as a duplicate dispatch of already-merged work and correctly stopped itself
        without redoing anything (114126 -> PR #58 merged, nothing left; 114207 -> merged its
        own docs-only correction PR, only an owner-actionable manual step remains; 114214 ->
        closed as duplicate, PR #932/#933/#934 already covered the fix, nothing pushed).
      - So "stop real duplicate workers" as an active intervention is **not needed** -- there is
        nothing running away or stuck that needs killing; all three already halted themselves
        correctly. This matches the "four running workers were never interrupted, no
        crash-shaped state" finding.
      - What **is** confirmed real and still open: the **bookkeeping-lag bug** the original
        owner task asked to root-cause. `umr_tasks.status` still shows `running` (no
        `ts_completed`) for both `UMR-20260805-024319-b1e6` (task 114126, merged via PR #58) and
        `UMR-20260805-032243-185e` (task 114214, closed duplicate) even though both are fully
        done. This is the same stale-status pattern flagged before, reproduced again -- a real,
        recurring finding worth root-causing/fixing (status never gets written back to
        `completed` on these self-resolved-duplicate closures), separate from any "stop the
        worker" action.

## Remaining
- [ ] None for this clarification task. Recommend: hand the bookkeeping-lag finding to whichever
      task owns the umr_tasks status-write path (worker exit / completion hook) so `status`/
      `ts_completed` get set correctly on self-resolved-duplicate closures, not just on
      code-producing completions.
