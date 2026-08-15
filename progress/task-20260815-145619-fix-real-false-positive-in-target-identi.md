# PROGRESS -- task-20260815-145619-fix-real-false-positive-in-target-identi

UMR: UMR-20260815-052932-e80b

## Completed
- [x] Read `extract_target_identifiers()`/`find_target_identifier_duplicate()`
      in full, including all prior real-incident docstrings/module comments
      (UMR-20260813-220216-2e2b UMR-id extraction, UMR-20260814-010802-b566
      boilerplate-script exclusion, UMR-20260814-034424-ded4 scope-aware
      TARGET:/SCOPE: restriction) before designing the fix.
- [x] Reproduced the real 2026-08-15 incident against the live
      `superboss-register.sqlite`: confirmed `UMR-20260815-135327-d6ad`
      ("Reject invalid complexity_tier constant in pm_lifecycle...") and
      `UMR-20260815-044235-a5e1` ("PM-in-Server: add real Part3+4 GTM-cert
      completion tracking to pm-sentinel-tick.sh") produce a real
      `script:pm_lifecycle.py` false-positive intersection under the
      pre-fix code -- a5e1 only cites `pm_lifecycle.py` twice, both times
      as an "orchestrator run"/"run" it dispatches, never as an edit
      target, and a5e1's prompt has no TARGET:/SCOPE: framing so the
      existing scope-aware restriction never engaged on its side.
- [x] Root-caused the false positive as a one-directional gap: the
      existing scope-aware restriction (UMR-20260814-034424-ded4) only ever
      narrows a text's own declared TARGET:/SCOPE: section; historical
      stored rows written before that convention existed get no equivalent
      narrowing in fallback (no-section) mode.
- [x] Implemented a real, deterministic, narrow fix in
      `superboss-register.py`: `_TARGET_ID_INVOCATION_CITATION_TRAILING_RE`
      excludes a `script:`/`path:` identifier occurrence immediately
      followed by "run", "orchestrator run", or bare "orchestrator" --
      applied per-regex-match (works in both fallback and
      TARGET:/SCOPE:-restricted mode, on either side of a
      `find_target_identifier_duplicate()` comparison). Does NOT touch
      `umr:`/`pr:` extraction. Documented with the real incident in both
      the module comment and the function docstring, matching this file's
      existing convention.
- [x] Verified against the real live DB rows: post-fix, `d6ad`'s ids and
      `a5e1`'s ids no longer intersect (`script:pm_lifecycle.py` is gone
      from a5e1's set; `script:pm-sentinel-tick.sh` correctly remains).
- [x] Added `tests/test_target_identifier_invocation_citation_dedup.py`:
      real-incident fixtures (verbatim d6ad/a5e1 title/prompt text),
      the general TESTING-spec regression shape (TARGET: section + EVIDENCE:
      invocation-citation aside on the new dispatch vs. unstructured
      genuinely-editing stored row citing the other file via the same
      invocation-citation shape), true-duplicate-still-refused checks in
      both unstructured and TARGET:/SCOPE:-section shapes, a same-text
      genuine-mention-plus-citation check, a "run precedes the name"
      non-regression check, and umr:/pr: extraction-unaffected checks.
- [x] Ran the real test suite: `tests/test_target_identifier_dedup.py`,
      `tests/test_target_identifier_scope_aware_dedup.py`,
      `tests/test_target_identifier_invocation_citation_dedup.py` (34 tests,
      all pass), plus `test_dispatch_owner_task_attach.py` (3 tests, all
      pass, the only other test file referencing this mechanism). Real
      pytest output, not fabricated -- see commit message / PR body for the
      exact run.
- [x] Committed real code change in `superboss-register.py` (not a
      docs-only diff) plus the new real test file.

- [x] Pushed branch, opened PR #420
      (FChecklist/veridian-scripts).
- [x] Requested independent audit. First audit returned **AUDIT:FAIL**:
      the initial `_TARGET_ID_INVOCATION_CITATION_TRAILING_RE` also matched
      a bare trailing "run" with no "orchestrator"/"via" qualifier (e.g.
      "worker.py run() function", "pm_lifecycle.py run out of memory"),
      which is ordinary bug-report phrasing, not an invocation citation --
      a real, demonstrated false-negative risk (could silently empty
      `my_ids` and fully bypass the duplicate guard), and it fired even
      inside a text's own declared TARGET: section, overriding what should
      be an authoritative declaration. Confirmed both failures reproduce
      against the real module before fixing.
- [x] Fixed: narrowed the regex to require "orchestrator" (bare or
      "orchestrator run") or "run via/using/through/by" -- both phrasings
      verbatim in the real a5e1 incident text -- and restricted the
      exclusion to fallback (no declared TARGET:/SCOPE: section) scanning
      only, never overriding a declared section. Re-verified: the
      auditor's false-negative repros now return non-empty ids, the real
      a5e1 incident citations are still excluded, and the real d6ad/a5e1
      live-DB intersection is still empty.
- [x] Added 3 more regression tests
      (`test_bare_trailing_run_as_ordinary_prose_is_not_a_citation`,
      `test_invocation_citation_exclusion_never_overrides_a_declared_target_section`,
      `test_orchestrator_run_still_excluded_even_though_bare_run_is_not`).
      Full suite: 40 passed (was 37).
- [x] Committed and pushed the narrowing fix to PR #420.

## Remaining
- [ ] Get a second independent AUDIT:PASS on the narrowed fix before merge.
- [ ] After merge: `agent_work_briefing.py record-completion --umr-id
      UMR-20260815-052932-e80b`.
