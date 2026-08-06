# PROGRESS -- task-20260806-065104-real-test-after-owner-confirmed-patch-ap

SPEC: "Real recheck, does the lock fix patch now work."

## Note on duplicate dispatch

A near-duplicate task (`task-20260806-064619-real-test-after-owner-confirmed-patch-ap`,
same SPEC text, dispatched ~4.5 min before this one) already did this exact
verification on its own branch (commit `75dfe66`), reaching the same "the fix
works" verdict. Per standing lesson
([[veridian-task-prompt-false-premise-pattern]]) that did not get trusted
blindly -- everything below was independently re-run from scratch on this
branch/workspace, not copied.

## Identified target

The "lock fix patch" is commit `6844c75` (fix: nested `_write_lock()` deadlock
in evidence_json schema refusal path). `upsert_ocid_canonical_registry()`'s
refusal path used to acquire a second, nested `with _write_lock():` around its
audit-log insert. `flock()` is per-open-file-description, not
per-process/re-entrant, so calling it from inside a caller that already holds
the lock (the real production call pattern used by every current call site --
`audit_ocid_canonical_registry.py`, `backfill_ocid_registry_phase2_columns.py`,
`backfill_evidence_json_schema.py`, and this file's own CLI command) hung
forever instead of raising `OcidEvidenceSchemaRefused`.

## Completed

- [x] Confirmed `6844c75` is on this branch's HEAD (`git merge-base
      --is-ancestor 6844c75 HEAD` succeeds) -- already merged, not pending.
- [x] Ran the existing regression test suite for this fix
      (`tests/test_evidence_json_schema_gate.py`) -- 12 passed, including
      `test_refused_upsert_from_inside_an_outer_write_lock_does_not_deadlock`.
- [x] Independent repro #1 (own script, not the repo's test file): loaded
      `superboss-register.py` **as of commit `6844c75^` (pre-fix)** via
      `git show 6844c75^:superboss-register.py`, called
      `upsert_ocid_canonical_registry()` with an incomplete-evidence
      `status='completed'` row from inside an already-held `_write_lock()`,
      under `timeout 8`: **hung, killed at 8s (exit 124)** -- confirms the
      pre-fix bug was real, not a paper bug.
- [x] Independent repro #2: identical call against the current HEAD
      (post-fix) `superboss-register.py`: raised `OcidEvidenceSchemaRefused`
      correctly in **0.0102s**, no hang.
- [x] **Verdict: the lock fix patch works.** Confirmed independently end to
      end (bug reproduced pre-fix, resolved post-fix, existing regression
      test passes) -- not just trusting the commit message or the other
      task's write-up.

## Remaining

- [ ] Full repo suite (`python3 -m pytest -q`) running in background to
      confirm no unrelated regressions; will record result and commit again
      once it finishes (>120s, backgrounded).
