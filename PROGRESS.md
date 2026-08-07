# PROGRESS -- task-20260807-053237-owner-mandate--persistently-disable-all

Governing chain: UMR-20260806-124055-bc80

## Verification done before acting
- Checked `journalctl --user -u '*.timer'` and confirmed the PM's claimed `systemctl --user stop`
  actions on the 19 timers really happened (~04:23-04:26 UTC 2026-08-07) -- premise held up,
  no fabrication found (see [[veridian-task-prompt-false-premise-pattern]] pattern in memory --
  checked for it, didn't apply here).
- Confirmed `veridian-cron-dispatch-tick.timer` and `veridian-pm-report-tick.timer` were
  active+enabled before touching anything, and untouched by this task.

## Completed
- [x] Ran `systemctl --user disable` on all 19 named timers.
- [x] Captured real `systemctl --user is-enabled` evidence for all 21 timer names (19 target +
      2 kept). See below.
- [x] Confirmed `veridian-cron-dispatch-tick.timer` and `veridian-pm-report-tick.timer` remain
      `active`/`enabled` (untouched, as instructed).

## Remaining
- [ ] **launchpadlib-cache-clean.timer still reports `enabled`**, not disabled. Root cause:
      it has a **global-scope** enablement (`systemctl --global is-enabled` -> `enabled`,
      distro preset in vendor/global systemd-user config), which `systemctl --user disable`
      cannot override -- user-scope disable only removes the per-user symlink, and the
      global-scope one still wins for effective state / next-boot behavior. Fully disabling
      it would require `systemctl --global disable launchpadlib-cache-clean.timer`, a
      **system-wide change affecting every user on the machine**, not scoped to this task's
      veridian-automation mandate. Did not run that unilaterally -- flagging for owner/PM
      decision rather than expanding scope without confirmation. All 18 other (veridian-owned)
      timers disabled cleanly.

## Evidence: `systemctl --user is-enabled` for all 21 real timer names (post-disable)

| Timer | is-enabled |
|---|---|
| veridian-build-lock-liveness-guard.timer | disabled |
| veridian-cron-veridian-self-check.timer | disabled |
| veridian-cron-prune-memory-backups.timer | disabled |
| veridian-cron-cost-usage-60min.timer | disabled |
| veridian-cron-security-check.timer | disabled |
| veridian-cron-sync-controller-back.timer | disabled |
| veridian-cron-audit-pipeline-security.timer | disabled |
| veridian-cron-sync-repos.timer | disabled |
| veridian-cron-system-sync.timer | disabled |
| veridian-cron-sync-vercel-env.timer | disabled |
| veridian-cron-knowledge-registry-multisource.timer | disabled |
| launchpadlib-cache-clean.timer | **enabled** (global-scope override, see Remaining) |
| veridian-cron-sync-verdian-ai-data.timer | disabled |
| veridian-cron-credit-ledger-prune.timer | disabled |
| veridian-cron-software-catalog-gen.timer | disabled |
| veridian-cron-file-inventory.timer | disabled |
| veridian-cron-health-check-15min.timer | disabled |
| veridian-cron-phase-continuation-tick.timer | disabled |
| veridian-cron-status-remediation-tick.timer | disabled |
| veridian-cron-dispatch-tick.timer | enabled (kept, per instruction) |
| veridian-pm-report-tick.timer | enabled (kept, per instruction) |

`systemctl --user is-active` for the 2 kept timers: both `active`.
