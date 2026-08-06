# PROGRESS -- task-20260806-064619-real-test-after-owner-confirmed-patch-ap

SPEC: "Real recheck, does the lock fix patch now work."

## Identified target
The "lock fix patch" is commit `6844c75` (fix: nested `_write_lock()` deadlock
in evidence_json schema refusal path, 2026-08-05). `upsert_ocid_canonical_registry()`'s
refusal path used to acquire a second, nested `_write_lock()` around its audit-log
insert; `flock()` is per-open-file-description/not re-entrant, so calling it from
inside a caller that already holds the lock (the real production call pattern used
by every current call site) hung forever instead of raising `OcidEvidenceSchemaRefused`.
Confirmed `6844c75` is already merged and present on this branch's HEAD
(`git merge-base --is-ancestor 6844c75 HEAD` succeeds).

## Completed
- [x] Confirmed `6844c75` is on current HEAD (already merged, not pending).
- [x] Ran the existing regression test
      (`tests/test_evidence_json_schema_gate.py::test_refused_upsert_from_inside_an_outer_write_lock_does_not_deadlock`)
      -- PASSED, plus full file: 12 passed.
- [x] Independently reproduced (own script, outside the repo's test file) calling
      `upsert_ocid_canonical_registry()` with an incomplete-evidence completed-status
      row from inside an already-held `_write_lock()`, against current HEAD's
      `superboss-register.py`: raised `OcidEvidenceSchemaRefused` correctly in ~9ms,
      no hang.
- [x] Independently confirmed the test has teeth: ran the identical repro script
      against the **pre-fix** version of `superboss-register.py`
      (`git cat-file -p 6844c75^:superboss-register.py`) under a hard 8s `timeout`
      -- it hung and was killed (exit 124), proving the pre-fix bug was real and
      the fix actually resolves it (not a tautological/mocked test).
- [x] Verdict: **the lock fix patch works.** Real, independently confirmed, not
      just trusting the commit message or existing test.

## Remaining
- [ ] Full repo test suite (`python3 -m pytest -q`) running in background (>120s,
      exceeded foreground timeout) -- awaiting result before final commit, to catch
      any unrelated regression.
- [ ] Commit + push this PROGRESS.md update.
