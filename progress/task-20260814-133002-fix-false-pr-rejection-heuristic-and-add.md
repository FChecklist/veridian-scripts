# Task: fix false PR-rejection heuristic + add filePaths field to task-tightening validator

Supersedes: UMR-20260814-125933-3377 (self-rejected as rejected_duplicate; real cause found and fixed below).

## Completed
- [x] Verified real state independently (not just trusting SPEC text):
  - `sqlite3 /opt/veridian/ai-os/memory/superboss-register.sqlite` row for
    UMR-20260814-125933-3377 confirmed: status=rejected_duplicate, reason
    starts "target-PR dispatch-time re-check: FChecklist/veridian-scripts#298
    ... state=CLOSED ...".
  - `gh pr view 298 --repo FChecklist/veridian-scripts` confirmed real:
    state=CLOSED, mergedAt=null.
- [x] Located the heuristic: target_pr_already_resolved() in
  resource_governor.py (called from dispatch_one()'s target-PR
  dispatch-time re-check block). Confirmed root cause: it blocked on ANY
  state == CLOSED, treating closed-without-merge identically to merged
  (real duplicate). Narrow fix applied: removed the state == CLOSED
  blocking branch; only state == MERGED now blocks. CLOSED-without-merge
  falls through to the same "not resolved, dispatch proceeds" path as OPEN.
  Docstring updated in place to document the real incident/rationale.
- [x] Updated tests/test_target_pr_dispatch_time_recheck.py:
  - Replaced test_closed_unmerged_target_pr_blocks (encoded the bug) with
    test_closed_unmerged_target_pr_does_not_block (real PR#298 shape).
  - Added test_dispatch_one_end_to_end_closed_unmerged_pr_is_not_rejected_by_this_guard
    (full dispatch_one() e2e, real UMR-20260814-125933-3377 shape).
  - All 15 tests in the file pass.

- [x] Added required filePaths field to tight_task_validation.py's
  validate_tight_task() -- array of real repo-relative paths, validated
  non-empty/non-placeholder using the file's existing check_field()/
  is_placeholder() helpers, no parallel validator. FILE_PATHS recognized
  locally in this module's FIELD_HEADER_RE/key_map (deliberately NOT added
  to the shared REQUIRED_TASK_SECTIONS in workflow_contract.py -- that also
  gates task-gateway.py/prompt_gateway/gateway.py, out of this task's
  scope). Added 1 passing + 2 failing test cases to
  test_tight_task_validation.py (9/9 tests pass).
- [x] Added a short header comment to dispatch-owner-task.sh (confirmed via
  Explore agent to be the real outermost dispatch entry point -- both the
  human/SSH path and the one live systemd-timer-driven path (pm-sentinel-
  tick.sh) converge on it) documenting the real submit -> capability-lookup
  -> queue-submit -> execute -> validate pipeline order (documentation
  only, no mechanism change).

## MAJOR FINDING (2026-08-14): the SPEC's ITEM 1 "redo the diff" premise is FALSE
Checked PR#298's own close comment + timeline (not just state/mergedAt) --
something the SPEC evidently did not do:
  `gh api repos/FChecklist/veridian-scripts/issues/298/timeline`
PR#298's real close comment (2026-08-13T14:09:29Z, by FChecklist):
"Superseded by #299, which now carries this PR's full content forward as a
strict superset (script + tests + these systemd units, verified
byte-identical via diff -- commit 5e3eeeb on #299 restored the two systemd
files this PR added that #299's earlier squash had dropped) ... Real
verification before closing: fresh clone of #299's branch, full
test_pm_sentinel_tick.py suite, 6/6 passed."
Confirmed independently: PR#299 is real, MERGED 2026-08-13T18:49:15Z
(commit ae48cf0), contains exactly pm-sentinel-tick.sh + systemd unit +
timer + test_pm_sentinel_tick.py, and IS on origin/main (`git log
origin/main -- pm-sentinel-tick.sh` shows ae48cf0, plus 2 further already-
merged fix commits since: 7dac937 #323, f9b4101 #341). The 696-line diff
did NOT "never land" -- it landed via #299, and has been maintained since.
A prior, unrelated task chain (UMR-20260813-145511-5aca /
UMR-20260813-170956-5385, commit b22bf55, 2026-08-14T00:20:52+05:30)
already independently found and documented this same "SPEC premise false,
real fix on PR#299" conclusion in PROGRESS.md, BEFORE UMR-20260814-125933-
3377 was even created -- that finding was evidently missed/not searched
for by whoever wrote today's SPEC/UMR-20260814-125933-3377's prompt text.

Consequence: reopening PR#298 or redoing its diff on a fresh branch would
be pure duplicate, wasted work. NOT done, on purpose, with this evidence
recorded here. Per this task's own DONE CRITERIA wording -- "PR#298's real
state changes ... (or a real successor merged in its place)" -- PR#299 IS
that real successor, already merged, already satisfying this criterion.

This also means the item-1 heuristic fix needed one more real refinement
beyond the SPEC's literal ask (which itself assumed CLOSED-unmerged always
means "real open gap" -- also not universally true): CLOSED-without-merge
must NOT block by itself, UNLESS the PR's own close comment names a real,
MERGED successor (exactly PR#298's real case) -- see
_closed_pr_superseded_by_merged_pr() added to resource_governor.py, with
6 new/revised tests (18/18 pass in tests/test_target_pr_dispatch_time_recheck.py).

## Completed (continued)
- [x] Corrected the stale register row: UMR-20260813-102459-10c3 was
  status=completed_unmerged citing PR#298 (open, not merged) -- corrected
  via `superboss-register.py mark-umr-terminal --status completed
  --commit-sha ae48cf005e522e7b3be4f1ab7bedb87620c357c4 --pr-number 299`,
  now reflects the real merged state.
- [x] Enhanced the target_pr_already_resolved() fix with
  _closed_pr_superseded_by_merged_pr(): a CLOSED PR whose own close
  comment cites a real MERGED successor still blocks (correctly, for the
  real reason) -- prevents the narrow CLOSED-unblock fix from itself
  becoming a false-non-rejection for exactly PR#298's real shape. 6 new/
  revised tests, 18/18 total pass.
- [x] Attempted test_pm_sentinel_tick.py against current origin/main HEAD
  to produce a fresh, real audit artifact for PR#299 (no AUDIT:PASS
  comment exists on record for #299 -- only an AUDIT:FAIL at an earlier
  head SHA b6fbed3, before 3 more already-merged fix commits landed on
  top; that same AUDIT:FAIL comment itself notes "only 3 of 8 [test
  classes] could be safely executed" -- some real test classes appear to
  need a live systemd/production environment this task workspace does not
  have). Two attempts (120s foreground timeout, then background) did not
  complete within this session's remaining time/budget -- honestly
  reporting this as NOT completed, not fabricating a pass. `bash -n
  pm-sentinel-tick.sh` syntax check does pass clean, and `python3 -c
  "import resource_governor"` confirms the resource_governor.py changes
  themselves import cleanly.

## Remaining
- [ ] A fresh, complete AUDIT:PASS re-verification of PR#299's current
  origin/main state was NOT completed in this session (see note above --
  test_pm_sentinel_tick.py's full suite did not finish within the
  available time; likely needs a live/production-adjacent environment for
  some of its 10 test classes). The done-criterion "PR#298's real state
  changes ... or a real successor merged in its place" IS satisfied by
  PR#299 (real, merged, already on origin/main) -- what remains open is
  only a NEW fresh audit comment beyond the evidence already on record
  (the prior AUDIT:FAIL + the close-comment's own "6/6 test_pm_sentinel_tick.py
  passed" claim, both pre-dating 2 further already-merged fix commits).
  A follow-up session with more time/budget (or direct access to a live
  systemd test environment) should finish this.
- [ ] UMR-20260814-125933-3377 itself is already terminal
  (status=rejected_duplicate) -- mark-umr-terminal only accepts
  {completed,completed_unmerged,failed,killed}, so its row is not
  rewritten (it was a correct real-world auto-rejection, just for an
  incomplete reason at the time); this task's own commits/progress file
  are the citation that supersedes it, per the SPEC's own instruction.
- [ ] record-completion write-back to agent_work_briefing.py for
  UMR-20260814-132703-a1f9.
- [ ] Final commit + push of any remaining changes (register corrections
  are DB writes, not repo diffs -- no commit needed for those).
