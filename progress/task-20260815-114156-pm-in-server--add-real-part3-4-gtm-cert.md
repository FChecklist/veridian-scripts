# PROGRESS -- task-20260815-114156-pm-in-server--add-real-part3-4-gtm-cert

Owner directive 2026-08-15: add real Part3+4 GTM-certification completion
tracking to pm-sentinel-tick.sh (the ~10min server-native PM tick), min
tokens, real audit, real completion certificate. pm.md / desktop-sentinel
SKILL.md were already updated separately (prose, no PR); this is the
code-level equivalent for this repo.

## Verified premise before building (per repeated false-premise pattern on
this task queue -- see memory)
- Live-queried `gtm_certification_categories` (OCID-020) directly against
  `/opt/veridian/ai-os/memory/superboss-register.sqlite`: 25 total rows,
  **7 real gap rows** (passed=0 or passed IS NULL) as of 2026-08-15, not the
  SPEC's own illustrative "9" -- confirms the SPEC's own warning that this
  count "will change as real work lands" was already true at dispatch time.
  Gap rows: security audit(3, FAIL), load testing(10, NULL), stress
  testing(11, NULL), AI testing(13, NULL), browser compatibility(17, FAIL),
  UX audit(23, FAIL), production readiness audit(25, FAIL). multi tenant
  testing(15)/role permission testing(16), which the SPEC listed as
  never-validated, are actually already passed=1 live -- real work landed
  between SPEC authoring and this dispatch.
- Live-queried both seed UMRs (UMR-20260815-033344-4799,
  UMR-20260815-042226-f271) via `resource_governor.py --query-umr --umr-id`:
  both status=**failed**, not in flight.
- Live `resource_governor.py --query-umr --search "gtm_certification_categories
  OCID-020 UMR-20260815-033344-4799 UMR-20260815-042226-f271" --limit 20`:
  20 real matches, all status in {completed, rejected_duplicate} -- nothing
  currently queued/dispatched/running. Confirms Check 4 would genuinely
  dispatch on a real live tick right now (not tested against the live DB by
  this task -- only against isolated schema-only copies, per this repo's own
  test convention).
- Confirmed OCID-020's real governing UMR via `ocid_canonical_registry`:
  UMR-20260802-165606-4413 (used for the completion-certificate's `umr_id`
  citation).
- Deliberately did NOT touch `dispatch_core.py` (frozen, per a real 2026-08-08
  stop-work order) or the unrelated `owner_priority_sequence` "Phase 3/4"
  concept in superboss-register.py (a different code concept that happens to
  share the word "Phase 3/4" -- confirmed via read, not the SPEC's actual
  Part3+4 GTM-cert scope).

## Completed
- [x] Read prompt.txt (task dir, not cwd -- cwd never had one this
      invocation) and mapped SPEC's 5 numbered requirements to this file's
      existing Check 0-3 pattern (dispatch_gap()/DISPATCH_OWNER_TASK_SH,
      is_in_flight, record_finding/DECIDE-AND-FIX, emit_report_row).
- [x] Added `gtm_part34_certification_check.py` (new file): one real
      round-trip that queries live gtm_certification_categories (OCID-020,
      never hardcoded), and -- only when genuinely 0 gap rows AND every
      passed=1 row has real non-placeholder evidence_summary, AND no prior
      certificate already exists (idempotent) -- writes ONE real,
      timestamped, evidence-citing completion record via the EXISTING
      `ocid_master_standard_audit_log` table / `record_ocid_master_standard_
      audit_event()` (superboss-register.py, importlib-loaded, same
      convention as `gtm_write_category_result.py`'s own `load_sbr()`) --
      never a new table.
- [x] Added Check 4 to `pm-sentinel-tick.sh` (between existing Check 3 and
      the DECIDE-AND-FIX reconciliation block): calls the above script once,
      then on a real gap>0 does a real content-based in-flight search
      (`resource_governor.py --query-umr --search`, covers task_identity/
      source_trigger/logs_ref) before dispatching exactly one real
      `pm_lifecycle.py`-driven fix via the existing `dispatch_gap()`
      (tier 1, repo=compliance-tracker) -- so real gap rows > 0 and an
      already-in-flight dispatch (even one this script never itself
      recorded) both correctly no-op. Also updated the file's own top
      header comment to enumerate Check 4.
- [x] Added 3 real pytest fixtures to `test_pm_sentinel_tick.py`, run for
      real (not fabricated):
      - `PmSentinelTickGtmCertDispatchTest` -- 9-gap-row fixture (SPEC's own
        named composition) -> real dispatch through dispatch-owner-task.sh,
        prompt cites OCID-020/pm_lifecycle.py/gtm_write_category_result.py.
      - `PmSentinelTickGtmCertCertifyTest` -- 0-gap, all-evidenced fixture ->
        real certificate row written to ocid_master_standard_audit_log; a
        second real tick does NOT write a second row (idempotency).
      - `PmSentinelTickGtmCertInFlightTest` -- a real already-queued row
        whose task_identity cites OCID-020 (not recorded in this script's
        own STATE_FILE) -> real content search finds it, no duplicate
        dispatch.
      Real `pytest test_pm_sentinel_tick.py -v` output: **14 passed** (11
      pre-existing + 3 new), 51.37s, zero failures.
- [x] `bash -n pm-sentinel-tick.sh` -- syntax OK. `python3 -m py_compile` +
      `python3 -m pyflakes gtm_part34_certification_check.py` -- clean.
- [x] Manually ran `gtm_part34_certification_check.py` against the real live
      DB (read-only outcome, since 7 real gap rows exist -- no write path
      exercised live): confirmed output matches the 7-gap-row state above.

## Remaining
- [ ] Commit + push this branch, open PR.
- [ ] Get a real independent AUDIT:PASS before merge (per Rule 10 /
      mandatory-audit-check convention) -- never self-certify.
- [ ] (Deliberately NOT in this task's scope: pm.md / SKILL.md text was
      already updated separately per the SPEC's own governing-chain note.)
