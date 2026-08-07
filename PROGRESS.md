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
      duplicate exists.
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
- [x] PR #257 opened + merged: scripts/superboss_gateway.py + systemd unit.
- [x] PR #258 opened + merged: fixed ExecStart path bug found before
      install (repo root == /opt/veridian/scripts, and the file landed at
      scripts/superboss_gateway.py relative to repo root, so the real live
      path is /opt/veridian/scripts/scripts/superboss_gateway.py).
- [x] Synced /opt/veridian/scripts (git checkout of just the 2 new paths,
      no disturbance to other agents' in-flight local changes there),
      confirmed py_compile clean and byte-identical to the tested draft.
- [x] Installed unit into ~/.config/systemd/user/, daemon-reload, enable
      --now. Killed a leftover manual-test process that was squatting on
      port 8790 from my own earlier verification run. Confirmed real:
      `systemctl --user is-active` -> active, `is-enabled` -> enabled,
      `curl 127.0.0.1:8790/health` -> {"ok": true, "journal_mode": "wal"}.
- [x] Registered capability_registry row via the canonical
      `superboss-register.py register-capability --record-file` CLI (not
      raw SQL): capability_id CAP-20260807-085901-a234, capability_name
      superboss_gateway, describing the health/read/write endpoints and
      the 7-table allowlist. PR #259 opened + merged with the
      version-controlled copy of that record (same convention as the
      other *_capability_record.json files in this repo).
- [x] Explicitly did NOT migrate any of the 46 existing scripts to use the
      gateway -- out of scope per SPEC, tracked as separate future work.

## Remaining
- [ ] None. Record completion via agent_work_briefing.py record-completion.

## Real evidence
- PR #257: https://github.com/FChecklist/veridian-scripts/pull/257 (merged)
- PR #258: https://github.com/FChecklist/veridian-scripts/pull/258 (merged)
- PR #259: https://github.com/FChecklist/veridian-scripts/pull/259 (merged)
- systemd: `veridian-superboss-gateway.service` active (running), enabled
- curl: `curl 127.0.0.1:8790/health` -> `{"ok": true, "db": "/opt/veridian/ai-os/memory/superboss-register.sqlite", "journal_mode": "wal"}`
- capability_registry: CAP-20260807-085901-a234 / superboss_gateway
