# PROGRESS -- task-20260807-085309-land-the-proven-single-gateway-for-super

## Completed
- [x] Verified premises independently (not just trusting SPEC text): draft
      file exists at /opt/veridian/ai-os/superboss_gateway_DRAFT_2026-08-07.py,
      syntax-valid (py_compile), and re-ran the end-to-end test myself:
      GET /health -> {"ok": true, "journal_mode": "wal"}; POST /read on
      umr_tasks returned a row matching a direct sqlite3 SELECT on the real
      DB exactly; POST /read on sqlite_master correctly rejected
      ("table not allowlisted"). Confirmed live DB path
      /opt/veridian/ai-os/memory/superboss-register.sqlite is real (8023
      rows in umr_tasks, journal_mode=wal).
- [x] Checked the deterministic briefing's 2 pointers -- both non-blocking:
      wiring_registry match is just this task's own self-referential
      dispatch_event row (noise from scope-term matching, not a prior
      gateway); capability_registry "task_oa" is an unrelated Next.js
      AI-orchestration API route capability, not a DB gateway -- no real
      duplicate exists, safe to proceed.
- [x] Checked ~/.config/systemd/user/README.md STANDING RULE: the
      closed-set-of-19 applies to periodic/cron units only (explicit text:
      "closed set of periodic jobs"); this is a persistent always-on
      singleton daemon, same category as the already-live
      veridian-glm-proxy.service / veridian-governor-tick.service, not
      subject to that rule.
- [x] Copied (not rewritten) the reviewed draft into scripts/superboss_gateway.py.
- [x] Added systemd/veridian-superboss-gateway.service (Type=simple,
      Restart=on-failure, [Install] WantedBy=default.target -- singleton,
      not templated, so no repeat of the 2026-08-01 boot-storm OOM incident).

## Remaining
- [ ] Commit + push scripts/superboss_gateway.py + systemd unit.
- [ ] Open PR citing this UMR/task, get PR number.
- [ ] After merge: sync /opt/veridian/scripts, install + daemon-reload +
      enable --now the systemd --user unit, confirm `systemctl --user
      status` active and real `curl 127.0.0.1:8790/health` returns ok:true.
- [ ] Register capability_registry row `superboss_gateway` describing the
      read/write endpoints and allowlisted tables.
- [ ] Record completion via agent_work_briefing.py record-completion.
