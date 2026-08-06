# PROGRESS -- task-20260806-151747-root-cause-fix--dispatch-owner-task-sh-n

## Completed
- [x] Step one: read `/opt/veridian/scripts/dispatch-owner-task.sh` in full and independently verified the SPEC's "real evidence" against live state.
- [x] Verified the SPEC's premise is **false** (see Findings below) -- stopped per "stop if any step fails" rather than proceeding to steps two-seven.
- [x] Logged the false-premise finding into the register via the canonical `superboss-register.py insert-pm-decision-pending` (pm_decisions_pending row id 103, related-umr UMR-20260806-071025-1d28) so the sentinel/PM does not re-dispatch this identical ask blind to the fact it's already resolved.
- [x] No code change made, no PR opened (nothing to fix -- see Findings).

## Remaining
- [ ] None for this task. If a maintainer disagrees with the false-premise finding, re-open with fresh evidence against `origin/main` (not the stale `/opt/veridian/scripts` deploy copy) and a specific claim about what `origin/main`'s current design (PR #166) still gets wrong.

## Findings (why this task stops here)

**SPEC claim:** `/opt/veridian/scripts/dispatch-owner-task.sh` is 99 lines and never writes `ts_dispatched`, `ts_completed`, or an updated `status` onto the `umr_tasks` row it mints.

**Verified reality:**
1. The live deployed file is **196 lines** (not 99), at repo commit `60cbae1` (deployed copy is well behind origin/main -- see deploy-sync gap noted below). It already writes back:
   - `mark-umr-dispatched` on successful tmux relay (line 184 of the live copy) -- added by **UMR-20260806-085144-9c63 / PR #150**.
   - `mark-umr-terminal --status failed` on relay failure (lines 192-193 of the live copy) -- same PR.
   - A mandatory completion instruction embedded in the relayed prompt text itself naming the exact `mark-umr-terminal` command -- added by **UMR-20260806-112013-088f** because the doc-comment-only version of this instruction was found insufficient.
2. `origin/main` of `veridian-scripts` (already merged, commit `3498d8a`, **237 lines**) has gone further still: **PR #166 / UMR-20260806-115423-500d** ("dispatch-owner-task relay non-authoritative") deliberately *removed* the `mark-umr-dispatched`/`mark-umr-terminal` writes from the relay branches, because:
   - A successful `tmux send-keys` only proves keystrokes were written into a pane, never that a live process read/acted on them -- it is not proof of delivery.
   - Writing `status='dispatched'` or `status='failed'` from this script independently pulled the row out of `resource_governor.py`'s `next_queued_task()` query (`WHERE status='queued'`), the **real mechanical dispatch-tick.py pickup path** that spawns a `veridian-worker@*.service` regardless of tmux/interactive-session state -- so a relay that landed in a dead/wrong/busy pane got the row **permanently excluded** from the one channel that could still have picked it up. A real dead zone, confirmed by reading `next_queued_task()`/`_perform_spawn()` directly.
   - The corrected, already-shipped design instead calls a new `mark-umr-relay-attempted` subcommand that writes **only** `ts_relay_attempted`/`relay_outcome`/`relay_detail`, and never touches `status`/`ts_dispatched`/`ts_completed`. Rows stay at `status='queued'`, fully eligible for the real mechanical pickup, no matter what the tmux relay achieved.

**Conclusion:** implementing this SPEC's steps two/three/four as written -- making the wrapper write `status='dispatched'`/`status='failed'` straight onto the row after the tmux relay -- would **revert an already-merged, deliberate regression fix** and reintroduce the exact dead-zone bug UMR-20260806-115423-500d fixed. This is not a case of "root cause not yet found and fixed" -- it is a case of the root cause having already been found, fixed, found-flawed, and re-fixed with a materially different (and correct) design, upstream of this SPEC's evidence.

**Separately noted, not fixed here (out of scope / needs its own owner):** the live `/opt/veridian/scripts` deployed copy is far behind `origin/main` (`60cbae1` vs `3498d8a`, dozens of merged PRs behind, including #166 itself). This deploy-sync gap is plausibly *why* the sentinel's "real evidence" query saw stale `queued`/`null` rows this cycle even though the real fix has already merged -- worth a real UMR of its own, but is a deploy/ops concern, not a `dispatch-owner-task.sh` code defect, and was not touched here.

**Scope boundary respected:** did not touch `reconcile_owner_dispatch_status.py`, `apply_owner_dispatch_status_corrections.py`, PR #147, or branch `reconcile/owner-dispatch-status-UMR-20260806-075726-babc`.

**Hard limits respected:** no credential rotated, no repository deleted or archived, reconciliation script/branch owned by UMR-20260806-082646-3aba untouched.

**Pattern match:** this is another instance of the recurring "urgent PM SPEC with confident claims that don't match live state" pattern (11+ prior instances per standing memory) -- verified independently before any write, exactly as that memory prescribes. No write/restore/kill was performed against production state; the only write made was the canonical, non-destructive `insert-pm-decision-pending` finding record.
