# PROGRESS -- task-20260806-065109-test

SPEC: "test after lock fix"

## Identified target
The "lock fix" is commit `6844c75` (fix: nested `_write_lock()` deadlock in
evidence_json schema refusal path). `upsert_ocid_canonical_registry()`'s
refusal path used to acquire a second, nested `_write_lock()` around its
audit-log insert; `flock()` is per-open-file-description / not re-entrant,
so calling it from inside a caller that already holds the lock (the real
production call pattern used by every current call site) hung forever
instead of raising `OcidEvidenceSchemaRefused`.

## Concurrent-dispatch note
Branch `worker/task-20260806-064619-real-test-after-owner-confirmed-patch-ap`
(commit `75dfe66`, pushed ~4.5 min before this task started, same SPEC in
substance: "Real recheck, does the lock fix patch now work") already did this
exact verification: ran the existing regression test, and independently
repro'd pre-fix (hangs, killed by timeout) vs post-fix (raises correctly,
~9ms) behavior. That branch has no open PR and never merged -- only its
PROGRESS.md changed vs `main`, no code. Per memory
(`veridian-task-prompt-false-premise-pattern`), did not just trust that
branch's claims -- independently reran verification below on this task's own
HEAD before concluding.

## Completed
- [x] Confirmed `6844c75` is already merged into `origin/main` and present on
      this branch's HEAD (`git merge-base --is-ancestor 6844c75 origin/main`
      succeeds).
- [x] Ran the existing regression test
      `tests/test_evidence_json_schema_gate.py::test_refused_upsert_from_inside_an_outer_write_lock_does_not_deadlock`
      -- PASSED. Confirmed it has real teeth (not tautological/mocked): it
      spawns a real subprocess against a real sqlite file/flock, wrapped in a
      hard `subprocess.run(..., timeout=15)`; a reintroduced deadlock would
      raise `TimeoutExpired`, not silently pass.
- [x] Ran the full file: `tests/test_evidence_json_schema_gate.py` -- 12
      passed in 0.87s.
- [x] Ran the broader set of tests touching `_write_lock` /
      `upsert_ocid_canonical_registry` (`test_evidence_json_schema_gate.py`,
      `test_audit_ocid_canonical_registry.py`,
      `test_ocid_registry_completion_gate.py`,
      `test_ocid_canonical_registry.py`,
      `test_audit_ocid_compliance_report.py`) -- 31 passed in 4.22s, no
      hangs, no failures.
- [x] Verdict: **the lock fix patch works**, independently reconfirmed on
      this task's own HEAD (not just trusting the concurrent branch's
      PROGRESS.md or the original commit message).

## Remaining
- [ ] None. No code change needed -- the fix is already merged and verified
      by a real, non-tautological regression test.
