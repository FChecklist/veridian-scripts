# PROGRESS -- task-20260813-182013-supervisor-self-blocks-and-destroys-real

## SPEC (as received)
GOVERNING CHAIN: UMR-20260806-171945-5767. Claimed a REAL DEFECT: worker for
UMR-20260813-155201-da76 ran a real 1454s session, terminated with task.yaml
status=blocked because supervisor could not resolve a PR for branch
`worker/task-20260813-163237-unwedge-dispatch--stale-swap-ratchet-blo`, and
that `reconcile_stale_running_workers.py` STEP 3 then "destroyed an entire
paid AI session of real work" by marking the row terminal failed. Requested:
retry+backoff around `gh pr create`, record real stderr, and "preserve and
report the real completed work rather than discard it."

## Completed
- [x] Independently re-verified every claim in the SPEC against the real
      task dir, real `supervisor.log`, real `result.json`/`review.json`,
      the real umr_tasks row, and `gh` -- before writing any code (per the
      known recurring Veridian task-dispatch false-premise pattern: SPECs in
      this repo have repeatedly arrived with confident claims that don't
      match live state).
- [x] Read the real `supervisor.log` at
      `/opt/veridian/ai-os/tasks/task-20260813-163237-unwedge-dispatch--stale-swap-ratchet-blo/supervisor.log`.
      Real stderr, verbatim:
      `pull request create failed: GraphQL: Head sha can't be blank, Base sha
      can't be blank, No commits between master and
      worker/task-20260813-163237-unwedge-dispatch--stale-swap-ratchet-blo,
      Head ref must be a branch (createPullRequest)`.
      This is **not** auth, rate-limit, or an unpushed-branch transient --
      it is a real, permanent, correctly-reported fact: that branch, in
      `claude-control` (this task's assigned repo), has **zero commits**
      ahead of master. No amount of retry/backoff changes that.
- [x] Read the real `worker.log`/`result.json`/`review.json` for that same
      task dir and found the reason the branch has zero commits: the worker
      **correctly determined the real fix belonged in a different repo**
      (`FChecklist/veridian-scripts`, `resource_governor.py`) rather than
      its assigned `claude-control` repo, per its own SPEC's stop-work-order
      constraint on `dispatch_core.py`. It built, tested (15 new regression
      tests + full suite), and **pushed real commits, and opened a real PR
      (#309, FChecklist/veridian-scripts)** for that fix. `review.json`
      (verdict: `approve`) independently confirms PR #309 is real,
      substantive, and correct, and explicitly notes the `claude-control`
      diff is a legitimate no-op because the fix lives elsewhere.
- [x] Confirmed supervisor's refusal to fall back to an unrelated PR is
      **working as designed, not a bug** -- the same log line cites a real
      past incident (PR #84, 2026-07-26) that this exact guard exists to
      prevent. The SPEC itself says to keep this behavior.
- [x] Confirmed via `superboss-register.py reconcile-umr-status --umr-id
      UMR-20260813-155201-da76` that the row's current DB status is
      `failed`, and that `mark-umr-terminal` (which
      `reconcile_stale_running_workers.py` STEP 3 exclusively calls, per its
      own docstring) only ever writes a status field via the existing CLI --
      it never touches git objects, the task directory, or PR #309. The
      "entire paid AI session of real work" the SPEC claims was "destroyed"
      is **not destroyed**: it is sitting intact, reviewed, and approved at
      https://github.com/FChecklist/veridian-scripts/pull/309, completely
      independent of what this tracking row's status field says.
- [x] Conclusion: **the SPEC's central premise is false.** Nothing was
      "destroyed." The supervisor correctly (a) found zero real commits in
      its own assigned repo/branch, because the real fix intentionally
      shipped to a different repo; (b) correctly refused to gh-pr-create a
      PR for a branch with no commits (retrying would not help -- there is
      nothing to create a PR from); (c) correctly refused to guess at an
      unrelated existing PR, per a real documented past incident; and (d)
      the worker itself self-reported the honest terminal state `blocked`
      for *this* task's own (empty) scope, which
      `reconcile_stale_running_workers.py` STEP 3 then deterministically
      converted to DB status `failed` per its own documented, narrow logic
      (self-reported negative outcome -> `mark-umr-terminal --status
      failed`). That is accurate bookkeeping of an empty local diff, not
      data loss -- the real deliverable (PR #309) was never at risk.
- [x] Declined to implement the requested retry/backoff around `gh pr
      create` or a "preserve completed work" mechanism: both would be
      solving a defect that does not exist. Retrying a `gh pr create` that
      fails because there are truly zero commits cannot succeed by
      construction, and "preserving" work that was never discarded (PR #309
      is live, reviewed, approved) would add no real protection while adding
      real risk (the exact PR-#84-style "guess at the wrong PR" failure mode
      the current refusal exists to prevent). Per this repo's own established
      pattern (see 2fe795f, a899b7a) for re-verified-already-correct SPECs:
      documenting the real evidence and declining unneeded/risky code churn
      is the correct action, not writing code to satisfy a false premise.
- [x] `record-completion` written back to this task's own UMR
      (UMR-20260813-175225-1c07) with the real, honest summary above.

## Remaining
- [ ] None from this SPEC (false premise, documented and declined). Real,
      independent follow-up worth Owner visibility (not implemented here,
      out of this task's scope): the umr_tasks status vocabulary has no way
      to represent "this row's own repo diff is intentionally empty because
      the real fix shipped as an external cross-repo PR" -- `failed` is
      technically accurate for *this row's* empty local diff but reads as
      alarming without the review.json context. A future UMR could consider
      a `completed_external_pr`-style status or a required outputs_json
      cross-reference field so sweeps like `reconcile_stale_running_workers.py`
      surface the real external PR link automatically instead of only in
      free-text `result.json`.
