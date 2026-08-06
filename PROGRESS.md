# PROGRESS -- task-20260806-222550-resolve-the-two-stale-queued-rows-blocki

## Completed
- [x] Verified real DB path (`/opt/veridian/ai-os/memory/superboss-register.sqlite`, not the
      stale decoy at `scripts/superboss-register.sqlite`) and queried both cited rows directly.
- [x] Confirmed SPEC premise is **stale, not current**: `UMR-20260729-112414-3269` is
      `status='completed'` (dispatched 10:42:18Z, completed 11:17:18Z) and
      `UMR-20260804-064310-f247` is `status='killed'` (dispatched 10:42:22Z, reconciled
      14:30:55Z) -- both reconciled hours before this task was dispatched, via the canonical
      heartbeat-sweep path, each with reason+evidence recorded on the row.
- [x] Confirmed `pm_decisions_pending` rows 22/23 were real at 09:13:44Z but already
      `status='superseded'` by 16:51:21Z (folded into aggregate row 185, migration landed in
      already-merged PR #196 / commit e7fea42).
- [x] Confirmed the SPEC's Step 4 ask (auto-detect stale-queued rows) already exists and is
      live: `flag_stale_queued_tasks()` in `resource_governor.py`, threshold
      `MAX_QUEUED_AGE_SECONDS`=4.0h, wired into every `run_tick()`, using the canonical
      `insert_pm_decision_pending()` -- last ran 21:16:32Z today, 8 currently-stale rows
      flagged.
- [x] Confirmed no current resource starvation: 5 `veridian-worker@*` units active/running
      right now, load average 3.78 (not the claimed 11.21), 23 queued rows with the oldest
      only 12.0h old (not 189.8h).
- [x] Wrote `SPEC_VERIFICATION_2026-08-06T222550Z.md` with full claim-vs-real-state table.
- [x] No `umr_tasks` or `pm_decisions_pending` write made -- both requested terminal-status
      outcomes were already true; re-touching already-terminal rows would be redundant, and
      building a second stale-queue detector would duplicate the one already in production.

## Remaining
- [ ] Rebase onto latest origin/main, commit, push, open PR recording this verification
      (matching this repo's established convention for stale-premise findings, e.g. PR #227).
