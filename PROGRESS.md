# PROGRESS -- task-20260806-151752-complete-the-unfinished-disk-retention-s

## Verdict: SPEC premise is false. No further work performed. Stopped after independent verification (step 0), per the recurring veridian task-dispatch false-premise pattern.

This SPEC claimed steps 6-7 of UMR-20260806-084306-f599 were "genuinely not done":
script missing, no timer, no PR, 20 stray backup copies. Every one of those
claims is contradicted by the live system, checked directly before any
write/delete:

- `/opt/veridian/scripts/prune_memory_backups.py` **exists** (10512 bytes,
  mtime 2026-08-06 09:48), with a hard integrity-check gate, `--dry-run`
  support, and a `--keep 3` retention policy -- exactly what step 2 asked
  for. It was already built and merged in PR #151
  ("fix(memory): add real backup-retention policy, fix ad-hoc-copy root
  cause (UMR-20260806-084306-f599)").
- A real systemd user timer, `veridian-cron-prune-memory-backups.timer`,
  **exists, is enabled, and is actively firing** (`systemctl --user
  list-timers` shows it triggering every couple of minutes; journalctl
  shows real successful runs, exit 0/SUCCESS). Follow-on PR #175 raised its
  cadence and added an event-based `.path` trigger
  (UMR-20260806-134738-eec3). PRs #179/#180 added a further shared
  snapshot-cap helper on top of this (UMR-20260806-140500-bkup). None of
  this is "no timer, no PR" -- it's four merged PRs of real, already-shipped
  work.
- Running the script's real `--dry-run` right now (live DB verified `ok`
  first, per its own safety gate) finds only **3 backup groups total**
  across both `/opt/veridian/ai-os/memory` and `.../memory/backups`, all 3
  within the keep=3 policy, **0 deletion candidates** -- not "20 files still
  match the database copy pattern". Output captured below.
- The register itself already documents why this SPEC is stale: querying
  `umr_tasks` for the parent UMR shows `UMR-20260806-084306-f599` was marked
  **`killed`** at `2026-08-06T15:18:30Z` (minutes before this task started)
  with `reason`: *"Terminated on a false premise per
  UMR-20260806-151638-48cc: ... blindly re-dispatched 7 hours later ... by
  dispatch-tick.py:228 resume_interrupted_workers_tick(), which resubmits
  any non-terminal task.yaml whose systemd unit is inactive with zero check
  on whether the task's real-world premise still holds ... the disk
  emergency this row describes ended hours ago (see
  UMR-20260806-134738-eec3/UMR-20260806-135538-e7e1, completed 14:17Z,
  finding only 32KB genuinely prunable). ... Did not mark completed -- no
  real work was completed under this stale resumption."

This task (`task-20260806-151752-...`) is a second instance of the exact
same stale-resumption bug re-issuing the already-closed parent SPEC's text
as if it were a fresh "real follow on task". It is the pattern recorded in
persistent memory as `veridian-task-prompt-false-premise-pattern` (11+
prior cases).

## Completed
- [x] Independently re-verified every factual claim in the SPEC before
      touching anything (per hard-limits + circuit-breaker protocol: verify
      before write/restore/kill).
- [x] Confirmed live DB integrity_check == ok (both directly and via the
      script's own gate).
- [x] Ran the script's real `--dry-run` (evidence below) -- confirms
      nothing is actually prunable and the retention policy is already
      governing the directory.
- [x] Confirmed script, timer, structural fix, and PR all already exist and
      are merged (PRs #151, #175, #179, #180).
- [x] Logged this finding to the register via `superboss-register.py
      log-action` (canonical script, no raw SQL writes).

## Remaining
- [ ] None. No code change, no new PR, no deletions performed -- there is
      no real remaining gap to close. If a future cycle finds a genuinely
      new gap (e.g. an ad-hoc script actually caught writing outside
      `backups/`), that would be new, independently-verified work, not a
      continuation of this SPEC.

## Evidence: real dry-run output (2026-08-06T15:18:52Z)

```json
{
  "mode": "dry-run",
  "live_db": "/opt/veridian/ai-os/memory/superboss-register.sqlite",
  "live_db_verified": true,
  "scan_dirs": [
    "/opt/veridian/ai-os/memory",
    "/opt/veridian/ai-os/memory/backups"
  ],
  "keep_count": 3,
  "kept": [
    {"main_path": ".../superboss-register.sqlite.pre-systemd-backup-20260806T133738Z", "verified": true, "bytes": 2122162176},
    {"main_path": ".../backups/superboss-register.sqlite.20260806T084420Z-verified.bak", "verified": true, "bytes": 1624858624},
    {"main_path": ".../superboss-register.sqlite.pre-dedup-constraint-backup-20260731-093417Z", "verified": true, "bytes": 64610304}
  ],
  "deleted": [],
  "reclaimed_bytes": 0
}
```

## Evidence: real timer state

```
$ systemctl --user is-enabled veridian-cron-prune-memory-backups.timer
enabled

$ systemctl --user list-timers | grep prune-memory-backups
Thu 2026-08-06 15:20:10 UTC 1min 45s Thu 2026-08-06 15:15:10 UTC 3min 14s ago veridian-cron-prune-memory-backups.timer veridian-cron-prune-memory-backups.service
```

## Evidence: real merged PRs already covering this SPEC's steps 2-6

- PR #151 -- fix(memory): add real backup-retention policy, fix ad-hoc-copy root cause (UMR-20260806-084306-f599)
- PR #175 -- fix(memory): raise prune-memory-backups cadence, add event-based trigger (UMR-20260806-134738-eec3)
- PR #179 -- feat(memory-backup): real shared ad-hoc snapshot helper with a hard daily cap (UMR-20260806-140500-bkup)
- PR #180 -- fix(memory-backup): never silently no-op when the daily snapshot cap is hit (UMR-20260806-140500-bkup)

## Evidence: register already shows parent UMR killed as false premise

```
umr_id:      UMR-20260806-084306-f599
status:      killed
ts_completed: 2026-08-06T15:18:30.763098+00:00
reason: Terminated on a false premise per UMR-20260806-151638-48cc: ...
  the disk emergency this row describes ended hours ago (see
  UMR-20260806-134738-eec3/UMR-20260806-135538-e7e1, completed 14:17Z,
  finding only 32KB genuinely prunable). ... Did not mark completed --
  no real work was completed under this stale resumption.
```
