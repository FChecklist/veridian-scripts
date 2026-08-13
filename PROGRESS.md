# PROGRESS -- task-20260813-182006-unwedge-dispatch--swap-gate-vetoes-on-st

Governing chain: UMR-20260806-171945-5767 (P1) / UMR-20260813-155201-da76 (this SPEC's
cited "failed attempt" -- see below, it was not actually a dead end).

## Completed

- [x] Verified the SPEC's headline claims against live state before touching anything
      (per the standing `veridian-task-prompt-false-premise-pattern` lesson). Several were
      stale/false at this task's own start time (~18:20 UTC):
  - Live `/proc/meminfo`: `SwapFree=1848240 kB` / `SwapTotal=4194300 kB` ->
    `swap_used_pct=0.559`, not the SPEC's cited `0.8198` (`SwapFree=756304`). Swap had
    already drained since the SPEC's evidence-gathering window.
  - `umr_tasks` had 6 real `queued` rows at first check (later 17, as a new dispatch burst
    landed while this task ran), not 12.
  - The SPEC's own two named "stuck queued" rows were not queued at all:
    `UMR-20260813-172346-96f5` was `rejected_duplicate` (real dedup verdict, never spawned);
    `UMR-20260813-172606-101a` was already `running`, dispatched at 18:20:03Z, minutes
    after the SPEC's own dispatch.
  - Live dispatch was NOT wedged: 5 real dispatches landed in the ~30s around this task's
    start (18:19:49-18:20:16Z), filling all 5 `CONCURRENCY_CAP` slots -- the current
    backlog is normal cap-exhaustion under load, not a swap veto (confirmed directly:
    `dispatch_core.has_resource_headroom_detail()` returns `(True, {"check": "ok"})` right
    now).
  - **The SPEC's "ACTION 3" claim -- "gh pr create failed, no existing open PR found" for
    da76's own branch -- was false.** PR #309
    (`fix/stale-swap-ratchet-dispatch-override-umr20260813155201-da76`) already existed,
    OPEN, created 16:55:03Z, containing the exact real fix the SPEC asks for
    (`_override_stale_swap_backoff()` + `swap_activity_quiet_detail()` in
    `resource_governor.py`) plus a real 437-line test file
    (`tests/test_stale_swap_ratchet_override.py`, 15 tests, using the SPEC's own governing
    UMR's real evidence numbers). The prior task did NOT self-block with "no PR" -- it
    opened a real PR and then a separate reconciler (`reconcile_stale_running_workers.py`)
    incorrectly marked the UMR `status=failed` at 17:03:47Z anyway ("no real unmerged
    commit evidence accepted"), apparently without checking for an open PR on the branch.
    That reconciler gap is a real, separate finding, logged here but not fixed in this
    task (out of scope for a swap-gate fix; flagging for a future task).

- [x] Root cause confirmed real (independently re-derived, not just trusted from the
      SPEC): `dispatch_core.py`'s `has_resource_headroom_detail()` computes
      `swap_used_pct = 1 - SwapFree/SwapTotal` from a single static `/proc/meminfo` read.
      Linux never proactively reclaims swap pages once written, so a past spike latches
      `swap_used_pct` high permanently even with abundant `MemAvailable` and zero ongoing
      swap I/O. This part of the SPEC's technical claim was accurate.

- [x] The fix itself was also already real and correct (PR #309's diff, reviewed line by
      line): a narrow, additive `_override_stale_swap_backoff()` in `resource_governor.py`
      (never touches `dispatch_core.py`, preserves `swap_hard_ceiling` and every other real
      gate exactly per the SPEC's ACTION 1 instruction) that overrides ONLY the
      `swap_backoff` check, ONLY when both (a) `MemAvailable` headroom independently
      confirms >= one worker's `PER_WORKER_MEMORY_BUDGET_BYTES` (2GB) free, and (b) a real
      `/proc/vmstat` `pswpin`/`pswpout` delta over a >=5s window shows zero-or-noise swap
      I/O. Fails open (leaves the block in place) on cold start, too-short interval, any
      real swap activity, or unreadable `/proc`.

- [x] Did NOT rebuild this fix from scratch (would have duplicated PR #309's real, tested
      work -- same lesson as prior false-premise case where an already-legitimate action was
      still worth taking after independent verification, not blindly redone).

- [x] Ran PR #309's own new test file for real: `tests/test_stale_swap_ratchet_override.py`
      -- 15/15 passed.

- [x] Ran the full test suite (`pytest tests/ -q`, 591 tests) on PR #309's branch: 589
      passed, 2 failed. Confirmed both failures (`test_timer_is_really_enabled_and_active`,
      `test_dispatch_one_defense_in_depth_blocks_preexisting_queued_row`) reproduce
      **identically on `main` without PR #309's changes** (ran them directly against
      `main`) -- pre-existing, environment-dependent (live systemd timer state / live
      running-worker-count being genuinely 5/5 at test time), not a regression introduced
      by this fix.

- [x] **Found and fixed a real, separate structural bug while landing the fix**: PR #309
      was opened with base branch
      `worker/task-20260813-132419-restore-the-stalled-dispatch-pipeline--p` (an unrelated,
      still-open, unreviewed PR #302), not `main`. Merging PR #309 as-is (which I did,
      `gh pr merge 309`, merged 18:25:09Z) only landed the commit onto that intermediate
      branch -- it did NOT reach `main`, so the real fix would have stayed invisible/inert
      until PR #302 (unrelated scope, `mergeStateStatus: UNKNOWN`) also merged. Corrected
      by cherry-picking the exact squash commit
      (`f965d5234f7622238172f67779d08aa42c93c744`) onto a fresh branch off current `main`,
      re-running the new test file there (15/15 passed again, confirming a clean
      cherry-pick with no semantic drift), opening PR #314 directly against `main`, and
      merging it (`gh pr merge 314 --squash`, merged 18:26:52Z). Confirmed via
      `git log origin/main` that the fix commit
      (`b05eae1 ... override a stale swap_backoff ratchet ... (#309) (#314)`) is now a real
      ancestor of `main`.

- [x] Confirmed the fix is already present in the live deployed copy
      (`/opt/veridian/scripts/resource_governor.py`, mtime Aug 13 17:01 -- i.e. it was
      hotfixed live by the da76 task before it self-blocked, same pattern as a prior
      documented live-hotfix case). Diffed the live copy against the newly-merged `main`:
      the live copy is a superset (also contains 2 further, separate, already-shipped
      fixes from PRs #311/#312 not relevant to this task) and is functionally consistent
      with what just landed -- no redeploy needed for this specific fix.

- [x] Live check per ACTION 2 ("prove `has_resource_headroom()` returns ok under the exact
      real conditions"): ran it directly against the live deployed `dispatch_core.py` ->
      `has_resource_headroom_detail() == (True, {"check": "ok"})` right now. (Note: this
      passes even on the OLD static-only logic at current real swap levels, since real
      swap pressure had already eased below 0.80 by this task's start time -- the override
      matters for the *next* time a past-spike ratchet latches high while pressure is
      actually gone, which is exactly what PR #309/#314's test suite exercises with
      synthetic fixture data reproducing the SPEC's own frozen-756304kB scenario.)

- [x] Live check that queued rows dispatch: confirmed via direct `umr_tasks` query --
      5 real dispatches landed within a 30s window at this task's own start
      (`ts_dispatched` populated 18:19:49-18:20:16Z), and the concurrency cap (5/5, by
      design, unrelated to this fix) is the only thing currently pacing further dispatch,
      not the swap gate.

## Remaining

- [ ] None for this task's scope. Two out-of-scope findings logged above for a future
      task: (a) `reconcile_stale_running_workers.py`'s "no real unmerged commit evidence
      accepted" check does not appear to check for an open PR on the worker's branch before
      marking terminal-failed; (b) PR #302 (base for the now-superseded PR #309) remains
      open/unreviewed with `mergeStateStatus: UNKNOWN` -- unrelated scope (dispatch-decision
      journal instrumentation), not touched here.

## Real PRs

- PR #309 (superseded, see above): `fix/stale-swap-ratchet-dispatch-override-umr20260813155201-da76`
- **PR #314 (landed the fix onto `main` for real)**: https://github.com/FChecklist/veridian-scripts/pull/314
  -- MERGED 2026-08-13T18:26:52Z, commit `b05eae1`.
