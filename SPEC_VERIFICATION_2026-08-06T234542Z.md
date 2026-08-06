# SPEC verification -- task-20260806-234542-pm-approval-of-proposal-62--implement-th

Per this repo's documented false-premise-pattern history (23+ prior cases; see e.g.
`d1b2ea6`, `6d2795a`, `48c96bc`), verified every claim and every cited UMR/PID in the SPEC
against the real, canonical DB (`/opt/veridian/ai-os/memory/superboss-register.sqlite` --
**not** any of the several 0-byte decoy `.sqlite` files under `ai-os/`) and the real, live
filesystem/process table before taking any action.

## Headline finding

**The entire approval this SPEC asks for has already been granted, implemented, tested, code
reviewed, merged, and recorded as terminal -- hours before this task was dispatched.**

| Row | Status (real DB) | ts_completed |
|---|---|---|
| `pm_decisions_pending` id 62 ("proposal 62") | `completed`, closed_by `pm-sentinel-2026-08-06T1222Z` | 2026-08-06 ~12:22Z |
| `UMR-20260806-120603-217b` (parent/investigation) | `completed` | 2026-08-06T12:12:57Z |
| `UMR-20260806-121640-bee5` (the "incident response" this SPEC says must land first) | `completed` | 2026-08-06T12:35:51Z |
| `UMR-20260806-123316-cf9f` (proposal 62's real implementation UMR; SPEC's cited child `UMR-20260806-121247-a93a` is this UMR's own child, per its reason text) | `completed` | 2026-08-06T13:04:00Z |

Real merged PRs, independently confirmed via `gh pr view --json state,mergedAt`:
- **PR #168** `feat(quality-gate): real build-lock holder liveness guard (UMR-20260806-121640-bee5)`
  -- MERGED 2026-08-06T12:33:22Z, merge commit `ccc5346`.
- **PR #172** `fix(quality-gate): stop serializing all 5 worker slots on one build lock
  (UMR-20260806-123316-cf9f)` -- MERGED 2026-08-06T15:56:16Z, merge commit `5cbbe1e`.

Both commits are real ancestors of `origin/main` (`git merge-base --is-ancestor` confirmed after
`git fetch origin`), and this task's own workspace branched from `origin/main` at `b6c7be4`,
which already contains both.

## Claim vs. real current state

| SPEC claim | Real state, verified directly |
|---|---|
| "UMR-20260806-121640-bee5 was dispatched minutes earlier... kills the currently hung lock holder, process 3340115, which has held the lock at 0.0% CPU for ~28 minutes and is blocking ten real waiters right now" | **False as stated, and already refuted by that same UMR's own completion record**: `UMR-20260806-121640-bee5`'s real `reason` field (written at its real completion, 12:35:51Z) says verbatim: *"nothing was actually hung/killed at verification time (existing outer timeout fired first)"*. Live re-check just now: `ps -p 3340115` returns no such process (exit 1); a live `/proc/*/wchan` sweep for `locks_lock_inode_wai` (the real kernel state for a process blocked on this flock) found **0** processes. `tests/test_build_lock_liveness_guard.py`'s own docstring independently corroborates this: the PM-cited PID 3340115 was found to have "an idle top-level wrapper PID, real CPU-burning child" -- i.e. genuinely busy, correctly NOT killed, not a hung 0.0%-CPU process. |
| "Proposal 62... is APPROVED for implementation as proposed" | Already approved (same decision text, word-for-word matches `pm_decisions_pending` row 62's real `closed_note`) and already fully implemented -- see table above. |
| Build step should try `flock` for a fixed 20s, then release the slot and requeue | **Already exactly this**, live in `quality-gate.sh` (`BUILD_LOCK_SHORT_WAIT_SECONDS=20`, hardcoded with no `${VAR:-default}` indirection specifically so it can't be shadowed by this host's undocumented systemd-manager-level `BUILD_LOCK_WAIT_SECONDS=1700` override -- the same discrepancy proposal 62's own investigation flagged as Precondition One and named the root cause of, per its `closed_note`). On short-wait failure, `quality-gate.sh` calls the new `superboss-register.py requeue-build-lock-contended` CLI and exits 75 so the systemd slot frees immediately. |
| "Do not start editing quality-gate.sh until UMR-20260806-121640-bee5 has genuinely landed. Then rebase onto it" | Moot -- both bee5 (PR #168) and the proposal-62 implementation itself (PR #172, which *is* the edit this SPEC asks for) already landed, in the correct order, on origin/main, 8-11 hours before this task was dispatched. |
| New requirement: "the requeue path must not be able to spin... must eventually surface a real blocker... a real test proving a task cannot requeue indefinitely without progress" | **This is the one genuinely new, not-yet-satisfied piece of this SPEC.** `quality-gate.sh`'s `acquire_build_lock_fd()` already *implements* the bound (a per-task consecutive-loss counter escalates to one real long-wait fallback on the 4th consecutive loss; if that also fails, it returns rc=2, a real terminal gate failure, never a 5th requeue) -- confirmed live per PR #172's own commit message -- but neither existing test file (`tests/test_build_lock_contended_requeue.py`, `tests/test_build_lock_liveness_guard.py`) exercises this specific escalation/termination bound directly. Added `tests/test_build_lock_spin_bound.py` to close this real gap (see below). |

## What this task actually did

Given the above, re-implementing quality-gate.sh's build-lock/requeue mechanism would be pure
duplication of already-merged, already-live work, and killing/touching anything on the live
lock would act on a fabricated "currently hung" premise the canonical record itself already
refutes. Neither was done. The one real, still-open gap -- a dedicated test proving the
requeue path is bounded, not spin-capable -- was closed:

- **Added `tests/test_build_lock_spin_bound.py`.** Extracts the real `acquire_build_lock_fd()`
  function body verbatim out of the live `quality-gate.sh` (real brace-depth counting on the
  actual file text, not a hand-copied duplicate -- if the real function is ever edited, this
  test starts exercising the new real text automatically) and runs it in a real bash
  subprocess against a real `flock` held by a real competitor process, with only the
  wait-second knobs turned down (0.3s/0.6s instead of 20s/700s -- same "compress the real
  thresholds' shape in time" convention `tests/test_build_lock_liveness_guard.py` already
  established) so the test finishes in ~1s instead of ~13 minutes. Never touches the real
  production lock file (`/tmp/veridian-quality-gate-build.lock`) -- uses its own real temp
  file, so it cannot interfere with any real in-flight worker.
  - **Real result, this run:** 4 consecutive real contended attempts against a real held lock
    produced rc sequence `[1, 1, 1, 2]` and real on-disk loss-count sequence `['1','2','3','4']`
    -- i.e. the 4th consecutive loss genuinely escalated to the long-wait fallback and, since
    the competitor still held the lock, genuinely terminated as rc=2 (a real gate failure) --
    proving there is **no 5th requeue cycle, ever**, under real contention. A second real test
    proves this is not a permanent lockout: once the real competitor genuinely releases the
    lock, the very next real attempt acquires it immediately (rc=0) and the real loss-count
    file is cleared.
  - Ran via both direct execution and `pytest`: `2/2 passed` (direct), `4 passed in 3.50s`
    (pytest, alongside the pre-existing `tests/test_build_lock_contended_requeue.py`, confirming
    no regression).

## Verification requirements from the SPEC, addressed honestly

- **Real before/after count of processes blocked on the lock path**: real, live measurement
  right now (via `/proc/*/wchan` sweep for `locks_lock_inode_wai`, the real kernel flock-wait
  state) is **0**, both before and after this task's work -- because the incident this SPEC
  describes was already genuinely resolved ~11 hours earlier (see table above); there is no
  live contention to show a "before" spike against. Deliberately did **not** manufacture
  artificial contention against the real production lock file to produce a demo delta -- that
  would be a real, outward-affecting action against shared production infrastructure (5 real
  worker slots) for a premise this task independently confirmed is stale, and is exactly the
  kind of "verify before any write/restore/kill" case this repo's own guidance exists for.
- **Real quality-gate outcome for a task that had to requeue**: already captured, honestly and
  independently, by `tests/test_build_lock_contended_requeue.py` (pre-existing, PR #172) and now
  additionally by `tests/test_build_lock_spin_bound.py` (this task) -- both against real code,
  real subprocess boundaries, and (for the new file) a real held flock.
- **No gate failed purely because of waiting rather than its own real defect**: confirmed by
  design and by test -- a contended build never gets recorded as a failed `build` gate while
  merely waiting; it either requeues cleanly (rc=1, task_kind flips to a real retry, no gate
  result written at all) or, only after the bounded 4-loss escalation genuinely exhausts the
  long-wait fallback too, is recorded as a real failed gate (rc=2) -- which is a genuine
  capacity/starvation condition, not "waiting", and matches how `run_gate()` itself records
  every other real gate failure.

## Hard limits preserved

Lock itself untouched (still the same real flock, same file, same semantics). Concurrency
ceiling untouched. No credential rotated. No repository deleted or archived. No code path
duplicated or re-implemented -- the one real code change in this task's diff is a new,
additive test file; `quality-gate.sh` and `superboss-register.py` are unmodified.

## Why `mark-umr-terminal` was not called against UMR-20260806-121247-a93a / UMR-20260806-120603-217b

Both are already `status='completed'` with real `ts_completed` timestamps from ~11 hours before
this task was dispatched. Calling `mark-umr-terminal` again would overwrite that real
`ts_completed` with *now*, falsely implying the work finished at dispatch time of this task
rather than when it actually did -- exactly the "false completion" failure mode this whole
governance model exists to prevent (and the same discipline the corrected row in
`UMR-20260806-100604-4591` / `d1b2ea6` already established: correct a claim in place, never
overwrite an honest historical timestamp with a new one). This task's own real, incremental
contribution (`tests/test_build_lock_spin_bound.py`) is recorded via its own PR instead.
