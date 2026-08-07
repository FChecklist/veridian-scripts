# PROGRESS -- task-20260807-053617-register-real-cron-and-systemd-timer-dis

GOVERNING CHAIN: UMR-20260806-124055-bc80 (confirmed live in umr_tasks, status=completed)
Deterministic briefing UMR (this task's own): UMR-20260807-045110-6a56

## Completed
- [x] Re-verified live `systemctl --user is-enabled` / `is-active` for every real timer unit
      on this server (26 unit files total: `systemctl --user list-unit-files --type=timer`).
- [x] Confirmed scope: 24 `veridian-*.timer` units are in scope. `launchpadlib-cache-clean.timer`
      (global system scope, per SPEC "unrelated") and `systemd-tmpfiles-clean.timer` (non-veridian
      system default, never had a wiring_registry row) are out of scope, confirmed by DB query
      showing they have never been registered as `cron_job` rows.
- [x] **Discrepancy found vs. SPEC's narrative** (SPEC's own TASK section explicitly instructed
      "re-verify live, do not assume a prior list is still accurate" -- so this is expected,
      not disqualifying): live state shows **3** timers enabled+active, not 2 --
      `veridian-cron-dispatch-tick.timer`, `veridian-dispatch-tick.timer`, and
      `veridian-pm-report-tick.timer`. Also found `veridian-cron-session-metadata-60min.timer`
      is disabled but currently **active** (a running instance despite being disabled). Both
      recorded as real evidence in the capability_registry row's metadata, not silently
      corrected away.
- [x] Blocking pre-existing bug found and fixed: `superboss-register.py`'s
      `_ensure_wiring_registry_table()` had an unescaped `{normalized_token: count}` inside an
      f-string SQL-comment (added under UMR-20260807-035145-aa45, today), raising
      `NameError: name 'normalized_token' is not defined` on every call -- broke
      `register-entity`/`lookup-entity`/`list-entities` CLI entirely. One-line fix: escaped to
      `{{normalized_token: count}}`. Verified `list-entities`/`list-capabilities` both work
      post-fix.
- [x] Reused existing `wiring_registry` rows (entity_type=`cron_job`, matched by real unit
      name, no new rows created) for all 24 `veridian-*.timer` units -- updated each with real
      `is_enabled`/`is_active` (in `metadata_json`), real `last_verified_ts`, and a
      `relationships` entry pointing to governing UMR-20260806-124055-bc80. Row count
      **before=72, after=72** (zero duplicates, `GROUP BY entity_id HAVING c>1` returns empty).
- [x] Registered new `capability_registry` entry `cron_systemd_state_manager`
      (capability_id=`CAP-20260807-054048-85c2`), citing UMR-20260806-124055-bc80 as the
      canonical script/procedure for checking or changing timer state going forward.
- [x] Real boolean evidence captured: before_count=72, after_count=72, rows_updated=24,
      duplicate_entity_ids=[], new capability_id=CAP-20260807-054048-85c2.

## Remaining
- [x] Commit + push.
- [x] Call `agent_work_briefing.py record-completion` write-back.
