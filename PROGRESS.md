# PROGRESS -- task-20260807-142918-stop-work-order--batch-2--real-tests-for

## Completed

- [x] Read batch-1's work first (task dir task-20260807-094754-stop-work-order--batch-1--write-real-tes,
  PR #261, commits bc2236c/b13e204/30d4bff/5c832f7) to confirm its pattern before writing anything:
  15 real pytest files (5 parallel agents, ~3 scripts each), real temp SQLite/temp files/temp git repos,
  network/systemd/tmux/docker stubbed only at the boundary, 3 genuine bugs documented as regression tests
  not fixed, checklist regenerated after, PR opened.
- [x] Verified SPEC's metric claim independently (false-premise-check policy, see
  `[[veridian-task-prompt-false-premise-pattern]]`): canonical `/opt/veridian/scripts/PLATFORM_COMPLETION_CHECKLIST.json`
  currently reads **40/160 complete** -- matches what SPEC said, no disagreement to flag.
  Batch-1's 47/148->60/158 numbers live only in their own workspace copy / merged PR #261 diff, not in the
  live canonical file (expected -- canonical is a shared, actively-moving directory; see below).
- [x] Confirmed important environment fact: `/opt/veridian/scripts` (canonical, live) and this task's git
  workspace are two different checkouts of the same `FChecklist/veridian-scripts` repo, at different,
  diverging commits, and the canonical directory currently has ~13 untracked/uncommitted files from other
  concurrent agent sessions (e.g. `_apply_readjudication_320.py`, `reuse_verdict_engine.py`,
  `session_metadata_sync.py`, `sweep_awaiting_approval.py`, `vector_similarity.py` -- none on `origin/main`).
  Target selection is filtered to scripts that are real, git-tracked files in this workspace's HEAD (so
  the tests + PR we produce are meaningful) -- the 5 untracked in-flight one-offs above are excluded from
  candidacy for that reason, not skipped arbitrarily.
- [x] Selection method (mirrors batch-1 exactly): from canonical checklist, filtered to
  `complete_and_tested == false` AND evidence == "no test file references this script by name" (i.e. the
  same `no_referencing_tests` class batch-1 targeted -- scripts with pre-existing *failing* shared test
  suites, e.g. `credit-accountant.py`, `resource_governor.py`, `superboss-register.py`,
  `triage_owner_umr_24h.py`, are a different, much more entangled problem -- fixing 2 shared failing tests
  in `test_triage_owner_umr_24h.py` -- and out of scope per "this is batch 2, do not attempt the whole
  remaining set"). Sorted alphabetically, skipped batch-1's 15, took the next 15 that are git-tracked.

## Batch-2 target list (exact, auditable)

1. `ddl_authorization_check.py`
2. `decision-service.py`
3. `deploy-live-scripts.sh`
4. `detect_prompt_duplicates.py`
5. `directive-engine-stop-audit-monitor.sh`
6. `directive_engine_stop_audit_monitor.py`
7. `dispatch-docworker-task.sh`
8. `doc-worker-entrypoint.sh`
9. `document_engine.py`
10. `full_server_file_registration.py`
11. `gap-status.py`
12. `generate-system-diagram.py`
13. `generate_chatgpt_audit_index.py`
14. `generate_chatgpt_audit_request.py`
15. `generate_chatgpt_promptbatch_request.py`

(Excluded from candidacy as untracked/in-flight-only in the live canonical dir, not part of this batch:
`_apply_readjudication_320.py`, `reuse_verdict_engine.py`, `session_metadata_sync.py`,
`sweep_awaiting_approval.py`, `vector_similarity.py`.)

## Remaining
- [ ] Dispatch parallel agents (grouped ~3 scripts each) to write real pytest coverage for the 15 targets.
- [ ] Run full suite, confirm live DB/systemd/tmux untouched.
- [ ] Commit test files, push.
- [ ] Regenerate canonical checklist, record real before/after N of M.
- [ ] Report any genuine bugs found.
- [ ] Open PR.
- [ ] Record completion via agent_work_briefing.py.
