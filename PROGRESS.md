# PROGRESS -- task-20260813-223359-phase-2-sub-phase-1-remainder--wire-git

## Verification (before any code change)

Independently re-verified the SPEC's claims against live state (per the
established false-premise pattern for these dispatches) before writing code:

- ✅ Matrix issue #921 text: verified verbatim against
  `/opt/veridian/ai-os/UMR_5767_ISSUE_RESOLUTION_MATRIX.json`, real, matches
  the SPEC's quote exactly.
- ✅ Zero `hash_object`/`hash-object` references anywhere in
  `/opt/veridian/scripts/*.py` at task start (live grep) -- confirmed.
- ✅ `full_server_file_registration.py`'s `content_hash_of()` used plain
  `hashlib.sha256(bytes)` (via `generate_wiring_registry.py`'s
  `_hash_file_bytes()`) -- confirmed, not git's blob model.
- ✅ `document_engine.py`'s `detect_duplicate_documents_by_hash()` takes a
  pre-supplied `contentHash`, does not compute one -- confirmed.
- ⚠️ **Discrepancy found and worth flagging**: the SPEC cited stop-work-order
  entry id `stop-work-order-lifted-2026-08-08-v2` at `2026-08-08T11:01:00Z`.
  The real entry in `ai-os/OWNER_DECISIONS_NEEDED_2026-07-23.yaml` is id
  `stop-work-order-lifted-2026-08-08` (no `-v2`), decided at
  `2026-08-08T09:55:38.639558Z` -- both the id and timestamp the SPEC cited
  are wrong. Substance also doesn't fully support the SPEC's "no stop-work
  blocker remains" framing: that real entry's scope is limited to exactly 4
  files (`resource_governor.py`, `superboss-register.py`, `task-gateway.py`,
  `resource_governor_tick_loop.sh`) -- **not** `document_engine.py` or
  `full_server_file_registration.py`. However, an earlier, separately real
  and approved entry (`phase2-subphase1-stop-work-order-exemption`, approved
  2026-08-07T14:55:00Z) broadly exempts "Phase 2 sub-phase-1 build/PR work
  (UMR-20260807-110133-205d and its real amendments)" with no per-file
  restriction, and this task's governing UMR (5767) is independently
  corroborated (via capability_registry metadata for
  `single_deterministic_orchestrator_pipeline`, unrelated to this SPEC) to
  chain into 205d. Proceeded on that independently-verified basis, not on
  the SPEC's own (partly inaccurate) citation. This is a real, bounded,
  reversible PR (not a merge, not a DB write/restore/kill), consistent with
  the low-risk end of the SPEC's own ask.

## Completed
- [x] Verified all technical + authorization claims independently (see above)
- [x] Verified the local git-blob-hash algorithm (`sha1('blob '+len+'\0'+content)`)
      is byte-identical to real `git hash-object` output (both ad hoc and in
      new pytest tests)
- [x] `full_server_file_registration.py`: added `git_hash_object_of()`
      (streaming, in-process git blob-hash), swapped `content_hash_of()` to
      use it instead of `generate_wiring_registry.py`'s plain-sha256
      `_hash_file_bytes()`; removed the now-unused `gwr()` loader; updated
      module docstring's reuse list
- [x] `document_engine.py`: added `git_hash_object_of()` (same algorithm) as
      the real "thin lookup"; added `--files` mode to `detect-duplicates`
      (computes real contentHash via git's blob model instead of requiring a
      pre-supplied one) and a standalone `hash-object` subcommand;
      `detect_duplicate_documents_by_hash()` itself left unchanged (its
      pre-supplied-contentHash contract is the real field-for-field TS port
      fidelity these tests + `resource_governor.py`'s Step 10 direct
      in-process call already depend on)
- [x] Updated `test_full_server_file_registration.py` and
      `test_document_engine.py` for the new algorithm; added real-boolean-test
      coverage (identical content, different filenames -> identical hash) and
      real `git hash-object` subprocess cross-checks
- [x] Full local test run: `test_full_server_file_registration.py` (21/21) +
      `test_document_engine.py` (18/18, incl. 7 new) all pass
- [x] Confirmed `resource_governor.py`'s only real caller of
      `document_engine.py` (Step 10, `_document_engine()` ->
      `detect_duplicate_documents_by_hash()` direct in-process call) is
      unaffected -- that function's signature/behavior is unchanged

## Remaining
- [ ] Commit + push branch, open PR against veridian-scripts
- [ ] Record completion via agent_work_briefing.py
