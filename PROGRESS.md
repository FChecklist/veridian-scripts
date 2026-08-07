# PROGRESS -- task-20260807-094754-stop-work-order--batch-1--write-real-tes

## Verification of SPEC claims (done before any writes, per standing false-premise-check policy)
- [x] Confirmed live `PLATFORM_COMPLETION_CHECKLIST.md`/`.json` reads Scripts 47/148 (file mtime 2026-08-07 09:47:55Z)
- [x] Confirmed all 101 incomplete rows have evidence reason `no test file references this script by name` (101/101, no other reason present)
- [x] Confirmed alphabetical-sort of incomplete scripts matches SPEC's stated first-6 and derived the real first-15 list
- [x] Read `generate_platform_completion_checklist.py` to understand exact pass condition: a `test_*.py` file (top level or `tests/`) whose content contains the script's basename/stem (underscore/hyphen variants), AND that test file passes when pytest is run on it (via a `git archive HEAD` snapshot -- i.e. must be committed to count)

## Batch 1 target list (first 15 alphabetically, complete_and_tested=false)
1. automation_rule_engine.py
2. backfill_phase_self_report.py
3. batch-import-conversation-log.py
4. chatgpt_audit_guard.py
5. chatgpt_audit_versioning.py
6. chatgpt_promptlib_guard.py
7. check_latest_task.py
8. check_single_protocol_file.py
9. chrome_start.sh
10. chrome_stop.sh
11. claude-tmux-usage-limit-check.sh
12. claude-usage-limit-retry.sh
13. context_engine.py
14. cost-reconciliation.py
15. cost-usage-60min.py

## In progress
- [ ] 5 parallel subagents dispatched to write real pytest tests (temp-DB/temp-file/stubbed-boundary discipline, house style from test_apply_owner_dispatch_status_corrections.py) for the 15 scripts above, 2-3 scripts each. Each agent runs its own tests locally before returning.
  - [ ] Group 1: automation_rule_engine.py, backfill_phase_self_report.py
  - [ ] Group 2: batch-import-conversation-log.py, chatgpt_audit_guard.py, chatgpt_audit_versioning.py
  - [ ] Group 3: chatgpt_promptlib_guard.py, check_latest_task.py, check_single_protocol_file.py
  - [ ] Group 4: chrome_start.sh, chrome_stop.sh, claude-tmux-usage-limit-check.sh, claude-usage-limit-retry.sh
  - [ ] Group 5: context_engine.py, cost-reconciliation.py, cost-usage-60min.py

## Remaining (after agents return)
- [ ] Review each generated test file myself for the fake-test anti-patterns (bare imports, assert True, existence-only checks) before trusting group reports
- [ ] Run full pytest suite on all 15 new test files together, fix any real failures
- [ ] Regenerate PLATFORM_COMPLETION_CHECKLIST via `generate_platform_completion_checklist.py`, capture real before/after N/148
- [ ] Commit new test files + regenerated checklist, open PR
- [ ] Record real completion via `agent_work_briefing.py record-completion --umr-id UMR-20260807-060727-c3ae`
