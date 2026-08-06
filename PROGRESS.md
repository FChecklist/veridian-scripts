# PROGRESS -- task-20260806-151757-deduplicate-the-disk-retention-work--one

## Completed
- [x] Investigated the SPEC's factual claims independently (per the recurring
      veridian-scripts false-premise dispatch pattern) before taking any action.
- [x] Found the SPEC's core evidence claims to be **false**:
  - Claim: "`/opt/veridian/scripts/prune_memory_backups.py` still does not exist."
    Reality: it exists (264 lines), added in commit `038ab70`, merged via **PR #151**
    (`MERGED` at `2026-08-06T09:15:18Z`, confirmed via `gh pr view 151`).
  - Claim: "twenty redundant database copies still remain in
    `/opt/veridian/ai-os/memory`." Reality: only 2 legacy pre-migration snapshot
    files plus the live DB/its WAL/SHM companions -- nowhere near 20.
  - Claim: dispatch is "as of 2026-08-06 08:55 UTC" with an agent "running for
    roughly nine minutes." Reality: current time at investigation was
    2026-08-06 15:18 UTC (~6.5h after the claimed dispatch time); no matching
    background process or task workspace was found anywhere on the box.
- [x] Verified the real, already-merged work satisfies all seven requirements
      from UMR-20260806-085335-707c:
  1. Retention script: `prune_memory_backups.py` (PR #151).
  2. `--dry-run` mode: present (`ap.add_argument("--dry-run", ...)`).
  3. Daily systemd timer, enabled: `veridian-cron-prune-memory-backups.timer`
     -- real `systemctl --user status` output captured, `Loaded: ... enabled`,
     `Active: active (waiting)`, later hardened (commit `91cec1a`,
     UMR-20260806-134738-eec3) with a 5-min backstop cadence + inotify `.path`
     trigger; service was observed actually firing live during investigation.
  4. Ad-hoc-copy root cause fixed: `migrate_2026-07-31_dedup_constraints.py`
     now writes its pre-migration snapshot into the DB's `backups/`
     subdirectory instead of directly into `ai-os/memory/`.
  5. Honest real search-command statement: commit `038ab70`'s message documents
     the exact grep used (`shutil.copy/.backup(/open(...,"wb")/subprocess cp`
     near the DB-path variables across every `.py`/`.sh`) and its real result
     (exactly one other offending script, fixed; two other scripts already
     wrote to a different, already-correct location per PR #144).
  6. Real PR number: **#151**, plus follow-up hardening PR for
     UMR-20260806-134738-eec3.
  7. `dispatch-owner-task.sh` left untouched (excluded per hand-off note in
     commit `038ab70`, owned by UMR-20260806-085144-9c63).
- [x] Did **not** start a second/duplicate agent for this scope (zero-duplication
      hard rule honored -- there was nothing left to duplicate).
- [x] Did **not** modify `/opt/veridian/scripts/dispatch-owner-task.sh`.
- [x] Recorded real completion evidence for `UMR-20260806-085335-707c` via the
      canonical `superboss-register.py mark-umr-terminal --status completed`
      (never raw SQL), citing the real PR/commit/systemctl evidence above, so
      no future agent re-does this already-merged work.

## Remaining
- [ ] None. This task's scope (verify + record) is complete. The underlying
      retention-policy engineering work was already done under
      UMR-20260806-084306-f599 Step 6 (PR #151) before this UMR/task existed;
      nothing further to build.
