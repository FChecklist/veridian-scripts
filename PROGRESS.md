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

- [x] Opened **PR #329** (https://github.com/FChecklist/veridian-scripts/pull/329)
      against `FChecklist/veridian-scripts` base `main`, 5 files changed.
- [x] Called `agent_work_briefing.py record-completion --umr-id
      UMR-20260813-215742-db64` with a real summary of the work above.

## Remaining
- [ ] None for this UMR's own scope.

---

# PROGRESS -- task-20260813-223359-phase-2-sub-phase-1-remainder--wire-git

## Verification (before any code change)

Independently re-verified the SPEC's claims against live state (per the
established false-premise pattern for these dispatches) before writing code:

- ✅ Matrix issue #921 text: verified verbatim against
  `/opt/veridian/ai-os/UMR_5767_ISSUE_RESOLUTION_MATRIX.json`, real, matches
  the SPEC's quote exactly.
- ✅ Zero `hash_object`/`hash-object` references anywhere in
  `/opt/veridian/scripts/*.py` at task start (live grep) -- confirmed.
- ✅ `full_server_file_registration.py`'s `content_hash_of()` used plain
  `hashlib.sha256(bytes)` (via `generate_wiring_registry.py`'s
  `_hash_file_bytes()`) -- confirmed, not git's blob model.
- ✅ `document_engine.py`'s `detect_duplicate_documents_by_hash()` takes a
  pre-supplied `contentHash`, does not compute one -- confirmed.
- ⚠️ **Discrepancy found and worth flagging**: the SPEC cited stop-work-order
  entry id `stop-work-order-lifted-2026-08-08-v2` at `2026-08-08T11:01:00Z`.
  The real entry in `ai-os/OWNER_DECISIONS_NEEDED_2026-07-23.yaml` is id
  `stop-work-order-lifted-2026-08-08` (no `-v2`), decided at
  `2026-08-08T09:55:38.639558Z` -- both the id and timestamp the SPEC cited
  are wrong. Substance also doesn't fully support the SPEC's "no stop-work
  blocker remains" framing: that real entry's scope is limited to exactly 4
  files (`resource_governor.py`, `superboss-register.py`, `task-gateway.py`,
  `resource_governor_tick_loop.sh`) -- **not** `document_engine.py` or
  `full_server_file_registration.py`. However, an earlier, separately real
  and approved entry (`phase2-subphase1-stop-work-order-exemption`, approved
  2026-08-07T14:55:00Z) broadly exempts "Phase 2 sub-phase-1 build/PR work
  (UMR-20260807-110133-205d and its real amendments)" with no per-file
  restriction, and this task's governing UMR (5767) is independently
  corroborated (via capability_registry metadata for
  `single_deterministic_orchestrator_pipeline`, unrelated to this SPEC) to
  chain into 205d. Proceeded on that independently-verified basis, not on
  the SPEC's own (partly inaccurate) citation. This is a real, bounded,
  reversible PR (not a merge, not a DB write/restore/kill), consistent with
  the low-risk end of the SPEC's own ask.

## Completed
- [x] Verified all technical + authorization claims independently (see above)
- [x] Verified the local git-blob-hash algorithm (`sha1('blob '+len+'\0'+content)`)
      is byte-identical to real `git hash-object` output (both ad hoc and in
      new pytest tests)
- [x] `full_server_file_registration.py`: added `git_hash_object_of()`
      (streaming, in-process git blob-hash), swapped `content_hash_of()` to
      use it instead of `generate_wiring_registry.py`'s plain-sha256
      `_hash_file_bytes()`; removed the now-unused `gwr()` loader; updated
      module docstring's reuse list
- [x] `document_engine.py`: added `git_hash_object_of()` (same algorithm) as
      the real "thin lookup"; added `--files` mode to `detect-duplicates`
      (computes real contentHash via git's blob model instead of requiring a
      pre-supplied one) and a standalone `hash-object` subcommand;
      `detect_duplicate_documents_by_hash()` itself left unchanged (its
      pre-supplied-contentHash contract is the real field-for-field TS port
      fidelity these tests + `resource_governor.py`'s Step 10 direct
      in-process call already depend on)
- [x] Updated `test_full_server_file_registration.py` and
      `test_document_engine.py` for the new algorithm; added real-boolean-test
      coverage (identical content, different filenames -> identical hash) and
      real `git hash-object` subprocess cross-checks
- [x] Full local test run: `test_full_server_file_registration.py` (21/21) +
      `test_document_engine.py` (18/18, incl. 7 new) all pass
- [x] Confirmed `resource_governor.py`'s only real caller of
      `document_engine.py` (Step 10, `_document_engine()` ->
      `detect_duplicate_documents_by_hash()` direct in-process call) is
      unaffected -- that function's signature/behavior is unchanged

- [x] Committed, pushed branch, opened PR #330:
      https://github.com/FChecklist/veridian-scripts/pull/330
- [x] Recorded completion via agent_work_briefing.py

## Remaining
- (none -- awaiting real human PR review/merge, out of scope for this task)
