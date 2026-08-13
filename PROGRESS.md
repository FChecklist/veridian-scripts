# PROGRESS -- task-20260813-215756-supervisor-hard-fails-every-no-op-branch

UMR-20260813-215742-db64: supervisor-entrypoint.sh treats an empty PR_URL as an
unconditional hard failure (exit 1) with no way to distinguish real `gh`/plumbing
breakage from a legitimate no-op (a worker branch with 0 commits ahead of its base
because the real deliverable was already merged by a prior task). Real, observed
impact: 44/147 task dirs on 2026-08-13 died at exactly this path (30% of all runs);
the false failure fed an unbounded paid-AI RCA re-dispatch loop (RCA for
UMR-20260807-151622-15cd dispatched twice; RCA for UMR-20260813-195852-aa85 dispatched
even though its real fix had already merged as PR #323).

## Completed
- [x] Read the real target file (`supervisor-entrypoint.sh`, PR_URL hard-fail at line
      207) and traced the real consumer chain (`worker-exit-status-bridge.py`
      ExecStopPost hook -> `superboss-register.py mark-umr-terminal` -> `umr_tasks` ->
      `pm-sentinel-tick.sh`'s 3 RCA-dispatch checks) via a dedicated research agent,
      not guessed.
- [x] `supervisor-entrypoint.sh`: added a real NO-OP-BRANCH-GUARD-BLOCK right after
      `DEFAULT_BRANCH` is resolved (before the AI review call, before `gh pr create`
      is ever attempted): `git fetch origin "$DEFAULT_BRANCH"` then
      `git rev-list --count origin/$DEFAULT_BRANCH..HEAD`. Ahead-count 0 -> writes a
      real, structured `no_op.json` marker (base_sha/branch_sha/base_branch/branch/
      reason, values passed via env vars into the `python3 -c` writer, never
      interpolated into source text -- same safer convention this file's own
      OCID-linkage block already established), checkpoints task.yaml
      `status=completed_no_change` via the existing `veridian-task.py checkpoint` CLI,
      and exits 0 -- no PR created, no AI review paid for. Ahead-count > 0 falls
      through unchanged to the existing PR-creation/hard-fail path (still exits 1 on a
      genuine `gh`/plumbing failure).
- [x] `worker-exit-status-bridge.py` (the real, already-existing, single umr_tasks
      terminal-write chokepoint): added `_bridge_no_op_completion()` -- recognizes
      task.yaml's last checkpoint `status=completed_no_change`, reads the real
      `no_op.json` marker (never re-derived), and bridges to
      `umr_tasks.status='completed'` via the existing `mark-umr-terminal --status
      completed --commit-sha <branch_sha>` real-evidence gate (the branch tip is,
      by construction, always a real ancestor of the base -- exactly what that gate
      already requires), reusing the existing enum value with a distinct, greppable
      reason string rather than a risky live CHECK-constraint migration for a new
      status value. Missing/incomplete marker fails safe (leaves the row at
      `running` for a human or the STEP 3 reconciler, never guesses). Moving the row
      off `running` to a real terminal, non-`killed` status is what stops all 3 of
      `pm-sentinel-tick.sh`'s RCA-dispatch checks (verified against its real source,
      not assumed).
- [x] Real regression tests, no mocked git:
      - `tests/test_supervisor_no_op_branch_guard.py` (new): builds a real bare
        `origin` repo + two real pushed branches (0-ahead and 1-real-commit-ahead),
        invokes the REAL, installed `supervisor-entrypoint.sh` as a real subprocess
        (only `gh`/`claude` faked via a scratch `$HOME/.local/bin`, matching this
        suite's own `test_worker_exit_status_bridge.py` precedent for a real
        ExecStopPost entrypoint). Asserts: 0-ahead exits 0, writes the real
        `no_op.json` marker with the real SHAs, checkpoints
        `completed_no_change`, and `gh` is never invoked at all (its own fake
        call-log file never gets created). 1-ahead falls through, genuinely invokes
        `gh pr create` (which the fake makes fail, simulating the real
        "No commits between" GraphQL error), and still exits 1 with checkpoint
        `blocked` -- proving the fix does not weaken the genuine-failure path.
      - `tests/test_worker_exit_status_bridge.py` (extended): 3 new tests for
        `completed_no_change` -- real marker bridges to `umr_tasks.status='completed'`
        end-to-end against a real local git ancestor check (reusing a real, live
        `origin/main` commit sha from `/opt/veridian/repos/veridian-scripts`, same
        convention `test_mark_umr_terminal_structured_evidence.py` already uses);
        missing marker and marker-without-branch_sha both fail safe (left at
        `running`).
- [x] Ran the real test suite (paste of real exit codes below) -- all green, zero
      regressions in the pre-existing bridge/mark-umr-terminal/checkpoint suites.
- [x] Confirmed no duplicate work: wiring_registry hit
      (`dispatch_event-owner-task-20260813-215738-3913076`) was this task's own
      dispatch record, not a prior fix; no other open PR touches
      `supervisor-entrypoint.sh`'s PR_URL guard.

### Real test output (pasted verbatim)
```
$ python3 -m pytest tests/test_supervisor_no_op_branch_guard.py tests/test_worker_exit_status_bridge.py -v
...
21 passed in 13.43s

$ python3 -m pytest tests/test_mark_umr_terminal_structured_evidence.py tests/test_checkpoint_prunes_node_modules_on_terminal_status.py tests/test_worker_unit_execstoppost_never_fails.py -q
......................
22 passed in 3.75s

$ python3 -m pytest tests/ --collect-only -q
628 tests collected in 1.00s   # zero collection errors from the new/changed files
```

## Remaining
- [ ] None for this UMR's own scope. `agent_work_briefing.py record-completion` to be
      called after this PR is opened.
