# PROGRESS -- task-20260807-142156-fix-pr-256-real-audit-fail--memoize-owne

Governing chain: UMR-20260807-070904-736a, UMR-20260807-070110-5ea7.
Real PR: FChecklist/veridian-scripts#256 (same PR, not a new one).

## Pre-work verification (before any write -- standing false-premise-check policy)

- [x] Fetched PR #256's real head at task start (`d890bae`, committed
      2026-08-07T11:58:27Z) and read `advance_owner_priority_phases()` as
      it actually exists there.
- [x] **Found the SPEC's quoted "exact real finding" was stale**: it is
      verbatim PR #256's *first* audit-fail review comment (posted
      2026-08-07T08:48:49Z, i.e. before `d890bae`). `d890bae` already fixed
      that exact finding (memoization via `confirmed_complete_members`,
      per-tick cap `OWNER_PRIORITY_PHASE_MAX_EVALUATIONS_PER_TICK=25`,
      short-circuit in `validate_umr_terminal_completion_evidence` when
      `file_ok` already true), with an existing passing regression test.
      Confirmed via `gh api repos/FChecklist/veridian-scripts/issues/256/comments`
      (full, untruncated bodies) that a **second, more recent** audit-fail
      comment exists (posted 2026-08-07T12:06:52Z, after `d890bae`) with a
      real, different, more severe finding the SPEC never mentioned.
- [x] Read the real second/current audit-fail finding in full: round 1's
      fix still ran the entire real evidence-check loop (real 60s-timeout
      `git fetch`/`cat-file`/`merge-base` subprocess calls for
      commit_sha-backed members) while `resource_governor.py`'s
      `_advance_owner_priority_phases_safe()` held superboss-register.py's
      cross-process `_write_lock()` -- the same flock every other
      write-path invocation of the script (dispatch, submit, mark-terminal)
      system-wide must also acquire. Worse than the original bug: a
      degraded network during an active large phase (3/4: 179/70 real
      members) could block every other worker's write for the whole
      Superboss Register, not just this feature's own dispatch loop.
      (Minor, same review: no `finally` around `conn.close()` in
      `_advance_owner_priority_phases_safe`, leaking the connection on an
      exception path.)
- [x] Independently re-read the actual current code myself and confirmed
      both findings are real (not just trusting the review text): the
      entire `sbr.advance_owner_priority_phases(conn, now=now)` call was
      wrapped in `with sbr._write_lock():`, and `conn.close()` had no
      `try`/`finally`.
- [x] Decision: fix the real, current (second) audit finding, not
      re-apply the already-fixed first one the SPEC quoted -- this matches
      the SPEC's own actual intent ("real fix required", "fresh audit
      against the new head") even though its quoted finding text was
      stale. Matches this environment's known recurring false-premise
      pattern (see memory: `veridian-task-prompt-false-premise-pattern`).

## Completed

- [x] Fetched PR #256's real branch
      (`worker/task-20260807-081913-amendment-to-umr-20260807-070110-5ea7--s`)
      and worked directly on it (not a new branch/PR).
- [x] `superboss-register.py`: restructured `advance_owner_priority_phases()`
      to acquire `_write_lock()` itself in two short, separate critical
      sections around the real reads/writes only -- the real evidence-check
      loop in between (the only part that can shell out to real git
      subprocess calls) now runs with **no lock held at all**. The final
      write section re-reads `confirmed_complete_members` fresh
      immediately before writing and unions it with this call's own
      newly-confirmed members (never clobbers a real concurrent writer),
      and re-verifies the phase is still genuinely `'active'` before
      transitioning it to `'complete'`.
- [x] Removed the now-redundant (and, via `_write_lock()`'s own real
      reentrancy, actively harmful if left) outer `_write_lock()` wrap at
      both real call sites: `resource_governor.py`'s
      `_advance_owner_priority_phases_safe()` and `superboss-register.py`'s
      `cmd_advance_owner_priority_phases()`.
- [x] `resource_governor.py`: fixed the real connection-leak-on-exception
      minor finding too -- `conn` now opened before, and closed in a real
      `finally` after, the call.
- [x] Added `test_phase3_4_scale_git_subprocess_calls_bounded_and_lock_not_held`
      to `test_owner_priority_sequence.py` (real phase-3/4-scale test per
      the original SPEC's own ask): 150-member synthetic phase, real
      `umr_tasks` rows, real commit_sha-only evidence; monkeypatches the
      one real subprocess entry point (`_default_ocid_resolver_runner`)
      with a counting fake that also records `sbr._write_lock_depth[0]` at
      call time. Asserts (a) no single tick issues more than a fixed bound
      of real git subprocess calls regardless of total member count --
      stays flat across repeated ticks, never grows unbounded -- and (b)
      not one of those calls happened while `_write_lock()` was held.
      **Verified the test actually catches the real regression**:
      temporarily re-wrapped `advance_owner_priority_phases()` in an outer
      `_write_lock()` (the exact old bug) and confirmed the test fails,
      recording 600 real subprocess calls at `lock_depth=1`; reverted,
      confirmed clean pass.
- [x] Full real suite: `pytest test_owner_priority_sequence.py
      test_resource_governor_owner_priority_advance.py -v` -> **10 passed**
      (including `test_live_db_untouched` -- live DB provably never
      touched).
- [x] Committed (`ed18209`) and pushed to PR #256's existing branch --
      same PR, no new PR opened.

## Remaining

- [ ] Fresh audit against new head commit `ed18209` (required before
      merge, per SPEC -- not this task's job).
- [ ] Merge (explicitly out of scope -- SPEC says do not merge it myself).
- [ ] Record completion via `agent_work_briefing.py record-completion`
      for UMR-20260807-092150-7bdb (this task's own umr_id).
