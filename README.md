# veridian-scripts — historical snapshot (frozen)

> **⚠️ This repo is a frozen historical snapshot, not a live system.**
> It is a version-controlled copy of `/opt/veridian/scripts` as it existed
> on the **VERIDIAN-DEV Hetzner server (167.233.220.35)**, which was
> **permanently deleted on 2026-08-25**. Any script here that references
> `systemctl`, `crontab`, or a live `/opt/veridian/...` path is describing
> a target that **no longer exists**. All real automation now runs
> directly on the developer's laptop; there is no server to deploy these
> scripts to.

Last real commit: 2026-08-17 (before the server deletion). The repo's own
`PROGRESS.md` was left mid-task with an unfinished
"deploy live to /opt/veridian/scripts" step — that step is now void, not
merely incomplete.

## What's actually in here

~575 tracked files: the old server's operational automation tree (dispatch
scripts, consolidation notes, cron/systemd-oriented tooling). Kept as an
audit trail of what ran on that server — not as deployable automation.

## Where the real thing lives now

Current VERIDIAN AI OS development happens in
[FChecklist/compliance-tracker](https://github.com/FChecklist/compliance-tracker),
worked on directly (no server, no proxy layer, no crontab).

---
*Added 2026-09-01 as part of a code-quality inspection pass (see
`public.code_quality_inspection_findings` in the `verdian-ai` Supabase
project) that flagged this repo's docs as actively misleading about
current infrastructure.*
