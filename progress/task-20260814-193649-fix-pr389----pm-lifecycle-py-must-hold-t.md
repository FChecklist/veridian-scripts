# PROGRESS -- task-20260814-193649-fix-pr389----pm-lifecycle-py-must-hold-t

Real safety-critical fix to `pm_lifecycle.py`'s step 7 merge gate (found by
PR#389's own real AUDIT:FAIL): `merge_and_reverify()` called
`gh pr merge --merge` unconditionally on any fresh AUDIT:PASS PR, with no
risk-tier classification first.

PR#389 (worker/task-20260814-183228-build-single-command-full-lifecycle-orch)
is still OPEN (not merged into main) -- `pm_lifecycle.py` does not exist on
`main` yet. Merged `pr389-branch` (PR#389's real head, a1d80e9) into this
task's own branch first so the file exists in a real committed diff here
(commit dc6c4ce), then applying the safety fix on top.

## IMPORTANT independent-verification finding (logged, not silently acted on)

SPEC's premise -- "supervisor-entrypoint.sh ... holds tier2+ PRs for explicit
human sign-off, never auto-merging them regardless of audit verdict" -- is
**false against current live/main state**. `supervisor-entrypoint.sh`
(both this repo's `main` HEAD and the live `/opt/veridian/scripts` deploy)
currently auto-merges tier2 too (`if [ "$VERDICT" = "approve" ] && [ "$SCOPE_OK" = "1" ]`
merges regardless of tier; the `HOLD_FOR_OWNER_SIGNOFF=True || TIER=tier2`
branch only sends a **post-hoc informational notification**, it does not
hold). This was introduced by commit `e1aa1f2` ("recover: real undocumented
local hotfixes found on live server, pre-PR20", 2026-08-04), citing an
"Owner directive, quoted verbatim in AGENTS.md Rule 12" -- **no such Rule 12
exists in any real AGENTS.md I can find** (checked `veridian-scripts/AGENTS.md`,
`compliance-tracker/AGENTS.md`, `claude-control/AGENTS.md`, and full git
history of all three for the string "Rule 12" -- zero hits outside the
scripts that cite it). That commit's own message explicitly flagged itself
as needing independent review before merge ("Its original authorship and
validation history are unknown ... not merged by this commit or this
session") -- but it WAS later merged to `main` via PR #334
(`8544da6`/`3420dcc`) without that citation apparently ever being verified.

This looks like the same false-premise pattern as
`[[veridian-task-prompt-false-premise-pattern]]`, but worse: a fabricated
citation is embedded in currently-deployed production code that removed a
real safety gate repo-wide, not just in one SPEC's claim. Out of scope for
this task (SCOPE is `pm_lifecycle.py` only) -- implementing this task's
literal, independently-justified directive anyway ("if tier2 or higher, do
NOT merge") makes `pm_lifecycle.py` a real, unconditional tier2 hold
regardless of what `supervisor-entrypoint.sh` currently does, since the
safety argument for holding tier2 stands on its own merits independent of
whether the citation backing `supervisor-entrypoint.sh`'s own bypass is
real. Flagging `supervisor-entrypoint.sh`'s fabricated-citation tier2-bypass
as a separate, real, high-severity finding for a follow-up task -- not
fixing it here (out of this task's SCOPE, and touching the live merge gate
for every task deserves its own dedicated, reviewed task).

## Completed
- [x] Independently re-verified PR#389's own AUDIT:FAIL finding against the
      real `pm_lifecycle.py` source (`merge_and_reverify()`/`run_full_cycle()`
      step 7) -- confirmed real and accurate: no tier classification before
      `gh pr merge --merge`.
- [x] Independently checked the SPEC's supporting premise about
      `supervisor-entrypoint.sh` against live `main` + the live
      `/opt/veridian/scripts` deploy -- found it false (see above); proceeding
      with the fix on its own independent safety merits, not because it
      matches current `supervisor-entrypoint.sh` behavior.
- [x] Merged PR#389's real branch (`pr389-branch`, a1d80e9) into this task's
      branch so `pm_lifecycle.py` + `tests/test_pm_lifecycle.py` exist in a
      real committed diff here (commit dc6c4ce).

- [x] Added `classify_merge_tier()` -- reuses `policy_decision.classify_risk_tier()`
      directly (the exact function `risk-tier.py`'s own CLI wraps, and what
      `supervisor-entrypoint.sh`'s `TIER=$(python3 risk-tier.py ...)` call
      resolves to), fed from `gh pr view --json files` (real
      additions/deletions/path per file) instead of a local `git diff
      --numstat` -- this orchestrator has no guaranteed local checkout of
      an arbitrary PR's branch, unlike supervisor-entrypoint.sh. Fails
      CLOSED to tier2 on any real classification error.
- [x] Gated `merge_and_reverify()` on tier: real tier1 merges as before;
      anything else returns `{"merged": False, "hold_for_owner_signoff":
      True, "tier_classification": {...}}` instead of ever calling
      `gh pr merge` -- verified via a test that makes `gh pr merge`
      raise if invoked on a tier2 fixture.
- [x] `run_full_cycle()` now surfaces `report["reason"]` for both the
      tier2 hold and the checks-not-passing hold -- `compute_six_columns()`
      already produced an honest non-certified outcome unchanged (no
      fake CERTIFIED path existed to begin with).
- [x] Regression test added:
      `test_merge_and_reverify_holds_real_tier2_pr_never_calls_gh_merge`
      (THE required test) + 3 more `classify_merge_tier` tests + 2 more
      `merge_and_reverify` tests (tier1 merges, already-merged skips the
      gate).
- [x] Secondary fix done (was quick): hard-gated the merge itself on real
      passing required checks -- `merge_and_reverify()` now fetches a
      pre-merge `statusCheckRollup` and refuses to call `gh pr merge` if
      any real check isn't SUCCESS/NEUTRAL/SKIPPED (was previously only
      recorded into TESTED *after* merging). Also fixed `checks_evidence()`
      itself: a real pending check (conclusion=None) was being silently
      exempted from "bad" -- now only a real terminal
      SUCCESS/NEUTRAL/SKIPPED counts as passing.
- [x] Secondary fix done (was quick): separated `dispatch_fix` and
      `dispatch_audit_trigger` retry counters (`fix_retries`/
      `audit_retries` in `verify_with_retries()`, `decide_next_action()`
      now takes both and caps each independently) -- previously a shared
      counter meant an audit-trigger dispatch could exhaust the cap a
      subsequent real fix retry needed.
- [x] Full real test suite run: `pytest tests/test_pm_lifecycle.py` --
      25/25 passed (15 pre-existing + 10 new). `python3 -m py_compile`
      clean on both changed files. Sanity-checked `test_decision_service.py`
      (the only other real consumer of `policy_decision.classify_risk_tier()`)
      still passes unchanged (9/9).
- [x] Committed + pushed.

## Remaining
- [ ] `agent_work_briefing.py record-completion --umr-id UMR-20260814-193636-1e67`
      (after this commit is pushed).
- [ ] Do NOT self-certify -- this needs a fresh independent AUDIT:PASS
      against the new head before any merge (not performed by this task;
      leaving for the standard audit pipeline / a follow-up
      audit-trigger task, per this task's own SPEC).
- [ ] Separate, out-of-scope, real finding logged above (not fixed here):
      `supervisor-entrypoint.sh`'s own tier2 auto-merge bypass cites a
      fabricated "AGENTS.md Rule 12" that does not exist anywhere in this
      repo's real history -- worth a dedicated follow-up task.
