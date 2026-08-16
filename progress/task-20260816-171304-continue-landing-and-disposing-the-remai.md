# PROGRESS -- task-20260816-171304-continue-landing-and-disposing-the-remai

SPEC: continue the disposition campaign on FChecklist/veridian-scripts' remaining
open PRs (16 live at re-derivation time, matching SPEC's "16" claim). Merge
genuine PASS-against-current-head; classify the rest into
Superseded/Real-defect/Owner-decision/Infrastructure-blocked and act
honestly; obtain missing audits via the box-native adopt-then-sweep
dispatch-owner-task.sh mechanism, never self-certify.

## Real live list re-derived (2026-08-16, ~17:20Z, via `gh api .../pulls -f state=open -f per_page=100`)
16 open: 435, 423, 422, 417, 416, 405, 401, 357, 355, 276, 273, 213, 204, 190,
72, 8. (`gh pr list` itself returns truncated/corrupted output in this
sandbox -- confirmed via byte count; `gh api` with `--method GET` does not
have this problem and was used for everything list-shaped below. Similarly
`git diff`/`git show` without `--no-pager` returned a bogus compacted
summary in this sandbox -- confirmed by comparing to `git --no-pager
diff`/`git cat-file -p` output; used `--no-pager`/`cat-file` for every real
diff/content read below.)

## Completed
- [x] Re-derived live 16-PR list, confirmed each PR's own audit comments (if
      any) are against its CURRENT head SHA (no commits pushed since the
      cited audit for any of the 16 -- verified via `pulls/{n}/commits`).
- [x] Read every existing genuine "adopt-then-sweep" audit verdict already on
      record (posted 2026-08-16 09:38-09:44Z, plus 3 same-day reaudits at
      12:2x for #357/#8, plus 3 older ones for #72/#276) -- these are the
      real, independently-dispatched worker audits the SPEC refers to; none
      needed re-requesting EXCEPT the one landed via #442 below.
- [x] Independently re-verified 5 of the audits' own central "already
      merged/duplicate" claims against CURRENT main (not just the audit's
      cited baseline, since main kept moving after several of these audits
      were written) via `git merge-base --is-ancestor` + real `git diff
      origin/main pr-N -- <files>`:
      - #423, #417, #405: audits said "byte-identical to an already-merged
        commit" -- true at audit time, but main has SINCE gained a newer,
        different fix in the same function (verified real diff, not just
        blob-hash match) -- reclassified from the audit's literal "FAIL:
        duplicate" wording to the more precise SPEC bucket **Superseded**,
        citing the actual newer main commit each time (89b30ab, 58c23d7,
        1b71062 respectively).
      - #273, #276: audits said "real defect, needs tests / has a security
        gap" against their own diff in isolation -- but both PRs' branches
        are so stale (bases from 2026-08-07, before main's 2026-08-08
        commits 86a2a81 + 7f70543 landed a hardened, tested
        stop-work-order gate AND master_issue_tracker CRUD with real test
        coverage) that merging either now would delete thousands of lines
        of newer main content (confirmed via `git diff --stat`: -5967 and
        -3921 lines respectively). Reclassified from the audits' "FAIL"
        wording to **Superseded**, citing 86a2a81/7f70543.
- [x] Spot-checked the remaining "real defect (stale/unfinished)" candidates
      (#8, #72, #204, #213/#435, #190) to rule out the same
      already-superseded trap: confirmed via `ls`/`grep` that NONE of their
      added files/functions (unified_orchestrator.py,
      _bounded_for_storage/_TRUNCATION_MARKER_SENTINEL,
      test_owner_status.py etc., session_metadata_sync.py/
      sweep_awaiting_approval.py) exist on current main -- these really are
      still-unshipped work, not superseded.
- [x] #355: genuine, single, uncontradicted AUDIT: PASS on record (tier1,
      2026-08-16T09:44:27Z, against its own head 9f0080a). `gh`/`git
      merge-tree` confirmed a REAL conflict against current main in
      PROGRESS.md + test_pm_sentinel_tick.py (main gained 2 unrelated new
      GTM-completion-certificate test classes in the same file region since
      #355's branch point) -- not mergeable as-is despite the genuine PASS.
      Resolved for real (not self-certified): `git merge --no-ff` PR #355's
      branch onto current main on this task's own worker branch;
      PROGRESS.md resolved by keeping the accumulating branch's disposable
      stub (confirmed main's copy is the same kind of stub, not real
      content); test_pm_sentinel_tick.py resolved by keeping BOTH sides'
      new test classes in full (verified via `git cat-file -p :2:`/`:3:`
      stage extraction + python merge, not a blind conflict-marker edit).
      pm-sentinel-tick.sh auto-merged clean. Verified: `py_compile` clean,
      `bash -n` clean, full `test_pm_sentinel_tick.py` suite 19/19 passing.
      Pushed, opened **PR #442** as the real superseding PR (original
      commits preserved via --no-ff, not squashed).
- [x] Dispatched a real independent audit of PR #442 via the box-native
      mechanism (`dispatch-owner-task.sh` directly, tier1/judgment,
      `--no-relay`, prompt built with `pm_lifecycle.build_tightened_prompt`
      matching the established `dispatch_independent_audit()` shape) --
      confirmed genuinely queued: UMR-20260816-172756-80cc,
      task_identity=owner-task-20260816-172754-803243,
      source_trigger=owner_dispatch_gateway, tier=1, status=queued as of
      dispatch time. Awaiting real dispatch + AUDIT:PASS/FAIL comment on
      #442 before merging.

## Remaining
- [ ] Poll UMR-20260816-172756-80cc / PR #442 for the real AUDIT verdict;
      merge #442 into main on PASS, then close #355 pointing at #442's
      merge commit. If FAIL, report #355/#442's real defect instead of
      merging.
- [ ] Close #276 (superseded by 86a2a81) and #273 (superseded by
      86a2a81+7f70543) with real citing comments.
- [ ] Close #423 (superseded by 89b30ab), #417 (superseded by 58c23d7),
      #405 (superseded by 1b71062) with real citing comments.
- [ ] Post real-defect summary comments (or confirm existing audit comments
      already say enough) on the real-defect bucket: #8, #72, #190, #204,
      #213, #357, #401, #416, #422, #435 -- leave open, no merge.
- [ ] Write final report table (all 16) and record completion via
      agent_work_briefing.py.

## Disposition table (real reasons, pending final #442 outcome)
| # | Outcome | Real reason | Docs-only |
|---|---|---|---|
| 8 | Real defect (open) | Reaudit: diff fails `git apply --check` against current dispatch-owner-task.sh (Tier3/4 headless routing moved the target region) + superboss-register.py base is 3.2x smaller than current live file; needs a fresh rebase, not a mechanical reapply. Minor: unescaped SQL LIKE wildcards in check-conflict. | No |
| 72 | Real defect (open) | OCID_068_PHASE_2 addendum's "Real live-apply results" section is still literal TBD; PROGRESS.md's own Remaining list still has the live --apply/verification/PR steps unchecked -- self-documented WIP. | No |
| 190 | Real defect (open) | sweep_awaiting_approval.py never checks a review's risk tier before `gh pr merge`, so it would auto-merge tier2-held PRs, violating the standing tier2 hard-hold rule; its own justification cites an unverified "Owner directive". | No |
| 204 | Real defect (open) | test_owner_status.py::test_hours_filter_excludes_old_rows hardcodes an absolute timestamp that has since aged out of its own `--hours 48` filter -- the suite fails today, contradicting the diff's embedded "588 passed" self-report. | No |
| 213 | Real defect (open) | Branch is 231 commits / ~8 days behind main; real unresolved conflicts in superboss-register.py + PROGRESS.md confirmed via `git merge-tree`. Same head SHA as #435 (see #435). | No |
| 276 | **Superseded** (close) | main commit 86a2a81 (2026-08-08T11:46Z, "harden stop-work-order gate...") already lands a hardened gate addressing the exact ambient-HEAD-trust gap this PR's own audit flagged, 14 min after that audit posted. | No |
| 273 | **Superseded** (close) | main commits 86a2a81 + 7f70543 already land master_issue_tracker CRUD (add/close/update/list-issues) with real test coverage (tests/test_shed_load_master_issue_tracker.py); merging #273 now would delete ~5967 lines of newer main content. | No |
| 355 | **Merge** (pending #442 audit) | Genuine PASS on record; not cleanly mergeable as-is (real PROGRESS.md/test conflict) -- rebased for real onto PR #442, fresh audit dispatched, will merge #442 on PASS and close #355 pointing at it. | No |
| 357 | Real defect (open, tier2) | Reaudit (same head) live-reproduced real production-DB pollution of /opt/veridian/ai-os/memory/superboss-register.sqlite during a routine pytest run, caused by the new lazy DB-path-resolution pattern breaking ~20 files' existing test fixtures; also drops several graceful-degradation guards. | No |
| 401 | Real defect (open) | New `_CLI_INVOCATION_RE` lacks a word boundary, matches "sh" mid-word (smash/polish/finish/...) followed by a `.py`/`.sh` path, silently over-widening the completion-gate's "no code needed" exclusion; no test probes the gap. | No |
| 405 | **Superseded** (close) | main commit 1b71062 (2026-08-15T04:31Z) already lands a newer, more complete fail-closed fix (adds `force_new_umr_id` terminal-resubmission safety this PR's version lacks) 8 min after this PR's own head commit; merging would revert it. | No |
| 416 | Real defect (open) | next_queued_task()'s new stale-skip gate permanently starves any row once it crosses MAX_QUEUED_AGE_SECONDS (age never resets); its own docstring's claimed PM-decision recovery path isn't implemented; no dedicated test of the new gate. | No |
| 417 | **Superseded** (close) | main commit 58c23d7 (2026-08-15T15:58Z) already fixes the identical resume-starvation problem via a different (reserved-slot) mechanism, landed ~2h after this PR's branch point; merging would revert it. | No |
| 422 | Real defect (open) | Wiring `validate_tight_task()` into `build_tightened_prompt()` breaks the diff's own `dispatch_audit_fix()`/`dispatch_independent_audit()` callers (their success_criteria text isn't a recognized command line) -- uncaught ValueError aborts the whole cycle; no test covers it. | No |
| 423 | **Superseded** (close) | main commit 89b30ab (2026-08-16T09:32Z) already replaces the same default-complexity_tier bug with a different, more carefully reasoned fix (`None`-default "safe absent" pattern) ~10h after this PR's commit; merging would revert it. | No |
| 435 | Real defect (open) | Same commit/defect as #213 (identical head SHA 645a807; #435 is the adopt-sweep mechanism's own audit-vehicle PR for #213's content) -- 231 commits / ~8 days stale, real conflicts confirmed via `git merge-tree`. | No |
