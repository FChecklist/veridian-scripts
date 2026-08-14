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

## Remaining
- [ ] Add `classify_merge_tier()` (reuses `risk-tier.py` via subprocess,
      same invocation shape `supervisor-entrypoint.sh` itself uses -- no
      reimplementation of the classifier).
- [ ] Gate `merge_and_reverify()` on tier: tier0/1 merge as before; tier2+
      returns a real `tier2_hold` outcome (`merged=False`,
      `hold_for_owner_signoff=True`) instead of calling `gh pr merge`.
- [ ] Update `compute_six_columns()`/`run_full_cycle()` to surface the hold
      as an honest non-certified terminal outcome (never a fake CERTIFIED).
- [ ] Add regression test: tier2-classified fixture PR is held, not merged.
- [ ] If quick: hard-gate the merge itself on real passing required checks
      (currently only recorded into TESTED *after* merging, not enforced
      before).
- [ ] If quick: separate retry counters for `dispatch_fix` vs
      `dispatch_audit_trigger` in `verify_with_retries()`/`decide_next_action()`.
- [ ] Run the real test suite (`tests/test_pm_lifecycle.py`) and confirm pass.
- [ ] Commit + push. Do NOT self-certify -- leave for a fresh independent
      AUDIT:PASS against the new head before any merge.
- [ ] `agent_work_briefing.py record-completion --umr-id UMR-20260814-193636-1e67`
