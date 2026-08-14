# PROGRESS -- task-20260813-235702-stop-the-resume-interrupted-workers-retr

## Verification (memory: veridian task-dispatch false-premise pattern recurs often -- checked independently before any write)
- [x] Confirmed defect is REAL, not a false premise (unlike ~23 prior SPECs). Live DB
      (correct path: `/opt/veridian/ai-os/memory/superboss-register.sqlite`, NOT
      `/opt/veridian/scripts/superboss-register.sqlite` which has no umr_tasks table --
      a wrong-DB-file trap) shows 6638 `rejected_duplicate` rows from
      `source_trigger='dispatch-tick:resume_interrupted_workers'`, 40 each for the 10 named
      task identities, last written 2026-08-13T23:52:47Z (~5 min before this task started),
      confirming the loop is still live and active.
- [x] Confirmed the earlier fix (UMR-20260806-103711-bf00, PR #163, `_existing_active_umr()`)
      does NOT cover this defect: that fix only short-circuits when a live ACTIVE umr_tasks
      row exists for the same task_identity. The actual rejection mechanism here is
      `reuse_verdict_engine.assess()` returning `verdict=duplication_blocked` against an
      unrelated wiring_registry/capability_registry entity -- a wholly separate code path.
- [x] Confirmed live ExecStart: `~/.config/systemd/user/veridian-cron-dispatch-tick.service`
      -> `/usr/bin/python3 /opt/veridian/scripts/dispatch-tick.py` (timer fires ~every 10 min,
      confirmed via `systemctl --user list-timers`). This is the file that matters, not the
      `.bak-predeploy-*` copies also present in that directory.

## Completed
- [x] Implemented bounded-retry policy in workspace copy:
      - `superboss-register.py`: new `resume_dead_letter` table +
        `_ensure_resume_dead_letter_table()`, `is_resume_dead()`, `record_resume_outcome()`,
        `mark_resume_dead()`.
      - `dispatch-tick.py`: named constant `MAX_CONSECUTIVE_RESUME_REJECTIONS = 3`,
        `_is_permanently_dead_resume()` (checked BEFORE `_existing_active_umr()` and BEFORE
        `resource_governor.submit()` -- zero cost once dead: no umr_tasks row, no similarity
        scan), `_record_resume_outcome()` wired into `resume_interrupted_workers_tick()`.
- [x] Added regression tests: `tests/test_resume_interrupted_workers_bounded_retry.py`
      (2 tests: N consecutive rejections -> attempt N+1 skips `submit()` entirely and is
      reported `skipped_dead`; a genuine acceptance clears the streak). Both pass.
      Pre-existing `tests/test_resume_interrupted_workers_no_duplicate_row.py` (2 tests) and
      `tests/test_umr_reuse_on_resume.py` (7 tests) still pass unmodified -- no regression.
- [x] Ran real test suite: `python3 -m pytest -q` (repo root). See below for exit code once
      the full run completes (backgrounded, >120s).

## Test suite (step 6)
- [x] `cd workspace && python3 -m pytest -q` -> 1325 passed, 16 failed, 887.65s. Real (non-piped)
      exit code is nonzero (16 failures). All 16 confirmed PRE-EXISTING and unrelated to this
      change, verified two ways: (a) isolated single-file reruns reproduce identical
      pass/fail results independent of this branch's changes (e.g.
      `test_resource_governor_queue_management.py::test_move_down_never_crosses_a_tier_boundary`
      fails identically on HEAD with zero working-tree diff); (b) failures are all
      environment-dependent (real `systemctl --user is-enabled` state, real host `load1` value
      8.09 tripping a different real gate branch, `deploy-live-scripts.sh` absent from this git
      checkout as a tracked file, real subprocess-call-count/tier-ordering assertions) or
      test-order-dependent (e.g. `test_worker_boot_activation_and_resume.py` passes 2/2 in
      isolation, fails only inside the full 1341-test run) -- none touch
      `resume_interrupted_workers_tick`, `resume_dead_letter`, or any function this fix added.
- [x] Directly relevant tests, isolated: `python3 -m pytest
      tests/test_resume_interrupted_workers_bounded_retry.py
      tests/test_resume_interrupted_workers_no_duplicate_row.py
      tests/test_umr_reuse_on_resume.py -q` -> **11 passed, exit code 0**.

## Live deployment (step 1 target confirmed, step 3 wired)
- [x] Confirmed live ExecStart via `~/.config/systemd/user/veridian-cron-dispatch-tick.service`:
      `/usr/bin/python3 /opt/veridian/scripts/dispatch-tick.py` (10-min timer cadence).
- [x] Applied the identical function-level patch directly to
      `/opt/veridian/scripts/superboss-register.py` and `/opt/veridian/scripts/dispatch-tick.py`
      (not the `.bak-predeploy-*` copies). `ast.parse()` syntax-check passed on both. A live-file
      smoke test (scratch DB, live files loaded via the same importlib technique the test suite
      uses) confirmed: 3 consecutive rejections -> attempt 4 correctly `skipped_dead`, zero
      further `resource_governor.submit()` calls.
      NOTE: workspace and `/opt/veridian/scripts` have independently drifted (workspace is ahead
      on some unrelated code, live is ahead on none relevant here) -- this fix was hand-applied
      to both rather than run through `deploy-live-scripts.sh` wholesale, to avoid deploying
      unrelated pending drift outside this task's scope.

## Step 4 deviation (evidence-based, not blind SPEC execution -- see memory:
## veridian task-dispatch false-premise pattern -- verify independently before any write)
- [x] **Did NOT execute the literal one-time cleanup** ("mark the 10 identities permanently
      dead"). Live re-query at 2026-08-14T00:13-00:22Z (after the SPEC was written, before this
      fix went live) showed the defect's *rejection* mechanism is real (2 of 10 identities did
      bounce back to `rejected_duplicate` within the same ~10 min window) but the SPEC's premise
      that these 10 are *permanently, uniformly* stuck no longer holds: all 10 transitioned to a
      real `status=queued` row at the pre-fix 00:13 and 00:22 ticks -- i.e. real forward
      progress, not a permanent block. Force-marking them dead right now would have killed
      possibly-legitimate in-flight work based on a stale snapshot, contradicting the recorded
      false-premise pattern's own lesson (independent live verification before any write).
      Instead: the bounded-retry ledger (already deployed) will mark any of these dead
      organically, based on real behavior going forward, the moment any of them actually
      racks up 3 fresh CONSECUTIVE rejections post-fix -- same real stopping outcome the SPEC
      wanted, without acting on stale evidence.
- [x] Snapshot taken immediately before the fix's first live tick (00:32:36 UTC), saved to
      `/tmp/before_snapshot.json`: all 10 identities at 42 historical `rejected_duplicate` rows
      each (6658 total across all identities for this source_trigger), latest status `queued`
      as of the 00:22 (pre-fix) tick.

## Remaining
- [ ] Observe >= 2 real subsequent ticks (next: 00:32:36 UTC, then ~00:42) against the live DB,
      confirm the bounded-retry mechanism is functioning (no unbounded fresh
      `rejected_duplicate` rows for these identities beyond the 3-consecutive cap). Report real
      before/after row counts.
- [ ] Commit + push this update, open PR (branch already pushed after step 1's commit).
- [ ] `agent_work_briefing.py record-completion` for UMR-20260813-235643-3e3d.
