# PROGRESS -- task-20260806-234552-merges-must-run-inside-a-real-worker-uni

Governing UMR: UMR-20260806-071025-1d28. Continues UMR-20260806-123547-e503.
Full detail: SPEC_VERIFICATION_2026-08-06T235757Z.md

## Completed
- [x] Step 1: confirmed genuinely executing inside a real worker unit --
      `veridian-worker@task-20260806-234552-merges-must-run-inside-a-real-worker-uni.service`
      (systemd --user, active/running; INVOCATION_ID + real /proc/self/cgroup both verified against the
      guard's own source at ~/.claude-interactive-session-guard.bashrc-snippet)
- [x] Step 2: independently re-verified PRs 169/167/165 -- SPEC premise found false for 2 of 3:
      - PR #169: still OPEN, but real AUDIT:FAIL posted 2026-08-06T13:27:16Z (after the "review approved"
        comment the SPEC cited) -- live-confirmed guard bypasses (sudo -u/nice -n/ionice value flags, bare
        `&`). Not mergeable in good faith.
      - PR #167: already MERGED 2026-08-06T16:46:39Z (commit f6ab6114...) by a separate re-dispatch.
      - PR #165: already CLOSED 2026-08-06T16:31:55Z, correctly -- 25 commits stale, would regress shipped
        functionality.
- [x] Step 3: no merge attempted for #169 or #165 (both would be wrong); #167 needed none (already merged)
- [x] Step 4: confirmed #167's merge commit is a real ancestor of origin/main AND that
      /opt/veridian/scripts/reconcile_dispatched_dead_zone.py is byte-identical to the merged blob (real
      deployment confirmed)
- [x] Step 5: real remaining open PR count = 56 (includes #169, still open/FAIL-audited)
- [x] Step 6: recorded real evidence via `superboss-register.py insert-owner-proposal` (id 297, child UMR
      UMR-20260806-235757-087c) -- canonical script, no raw SQL. Did NOT call `mark-umr-terminal
      --status completed` on UMR-20260806-071025-1d28 or UMR-20260806-123547-e503: both already read
      `status=failed` accurately (confirmed via `reconcile-umr-status`, `is_stale: false`), and the SPEC's
      basis for asking for `completed` (all three PRs ready, just needed the merge) does not hold for 2 of 3.

## Remaining
- [ ] None for this task. PR #169 needs its own follow-up fix (wrapper-flag-value + bare `&` bypasses) and
      a fresh audit before merge is attempted again -- out of scope here, recorded in the owner-proposal
      for the next dispatch.
