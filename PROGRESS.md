# PROGRESS -- task-20260806-212456-narrow-umr-20260806-092722-e526--pr-153

Governing UMR: UMR-20260806-071025-1d28. Own UMR: UMR-20260806-093654-7566.
Supersedes step 3/4 of UMR-20260806-092722-e526.

## Completed
- [x] Step 5 (dedup/coordination): confirmed PR 153 (`fix/dispatch-queue-starvation-umr20260806090229-f2a7`,
      merged 2026-08-06T09:46:45Z, merge commit `2782998`) is already in this branch's
      history (HEAD descends from it, current tip `1110091`). No rebase needed.
      `directive_engine.py` was **not** modified -- confirmed untouched, per hard limit.
- [x] Step 1 (find the resubmission code): found and read, did NOT modify.
      `resource_governor.py` `submit()`, the "OCID-068 seven-rule guardrails addendum,
      Rule 1" block (~lines 728-772), calls
      `superboss-register.py::find_most_recent_umr_by_identity()` (~line 5433) and then
      `upsert_umr_task()` with `umr_id=reused_umr_id`, `status="queued"`. This reuses a
      prior terminal row's own `umr_id` and writes reason
      `"resubmitted (reused umr_id, prior status was '<status>')"`.
- [x] Step 2 (real count): **2** rows in the live `umr_tasks` table (canonical DB,
      `/opt/veridian/ai-os/memory/superboss-register.sqlite`, verified via
      `resolve_superboss_db_path()`'s own checks) have `reason LIKE '%reused umr_id%'`:
      `UMR-20260730-041943-093a` and `UMR-20260806-114330-7f2f`.
      **Correction to the SPEC**: `UMR-20260729-112414-3269` (the SPEC's second named
      "blocking row") does **not** carry this reason -- its real reason is
      `"reconciled by heartbeat sweep: unit ... inactive, last_heartbeat stale (>900s),
      real exit status=completed"`. The SPEC's claim that "both blocking rows" carry a
      reused-umr_id reason is false for this row.
- [x] Step 3 (investigate root-cause claim) -- **premise verified false, no code
      change made**:
      - The umr_id-reuse-on-resubmit behavior is an intentional, documented,
        already-tested feature (OCID-068 Rule 1, `UMR-20260804-180711-7f96` /
        `UMR-20260804-194355-be9c`): "one logical task shall have exactly one OCID,
        exactly one UMR ... any retry/resume/redispatch shall reuse the existing UMR
        rather than minting a new one" -- so a resumed task keeps one continuous
        history instead of fragmenting across disconnected UMRs.
      - It is correctly scoped, not a wildcard reviver: the only real caller is
        `dispatch-tick.py::resume_interrupted_workers_tick()`, which only resubmits a
        `task_id` when its **on-disk task.yaml status** is still non-terminal
        (`RESUMABLE_STATUSES`) *and* its systemd unit is confirmed **not** active --
        i.e. only genuinely crashed/interrupted mid-work, never a task whose real work
        already finished or was deliberately/permanently killed by policy.
      - Already has dedicated regression coverage:
        `tests/test_umr_reuse_on_resume.py` (7 tests). Re-ran it independently in this
        session: `7 passed`.
      - Both SPEC-named "blocking" rows are independently confirmed to already be in
        terminal status right now (`UMR-20260730-041943-093a` = `killed`,
        `UMR-20260729-112414-3269` = `completed`), with no live process and no
        matching systemd unit (`systemctl list-units 'veridian-worker*'` = no match)
        -- consistent with PR 153's max-queued-age safeguard having let the
        genuinely-interrupted retry-storm casualties resolve normally once the
        underlying poison-pill loop (PR 153) was fixed, not with an ongoing
        "silent revival" defect.
      - Conclusion: there is no data-integrity defect to fix here. Writing a change
        that forbids ever reusing a terminal-status `umr_id` would **break** the
        intentional, tested resume/continuity feature above. No code change was made;
        no new test was needed (existing coverage already proves a killed row's
        `umr_id` reuse works as designed, and there is no bug to add a regression
        test against).
- [x] Step 4 (close the two rows) -- **not performed, and correctly so**: both rows
      are already in terminal status (verified above). `mark-umr-terminal` has no
      "already terminal" guard; calling it again would just overwrite each row's
      accurate, informative `reason` (heartbeat-sweep reconciliation / real
      resubmission history) with a redundant terminal write serving no purpose, since
      there is nothing open to close and no live work behind either row. Per hard
      limits ("do not mark anything completed that did not really complete" /
      accuracy of the registry), left both rows untouched rather than writing a
      no-op citing a disproven defect.
- [x] Step 6: recorded via `agent_work_briefing.py record-completion` (see below).
      No new wiring_registry entity registered (no new code/capability was created).

## Remaining
- [ ] None -- all six steps addressed. Steps 3/4 concluded "premise false, no action
      needed" after independent verification, consistent with this UMR's own real
      finding narrowed to what the live DB and live code actually show.

## Summary for future readers
This is another instance of the recurring veridian-scripts false-premise pattern:
the SPEC's "real new finding" (reused-umr_id = data integrity defect) does not match
the live code (an intentional, tested, correctly-scoped OCID-068 Rule 1 resume
feature) or the live DB (one of the two named rows doesn't even carry the reason
claimed, and both are already terminal with no live work). No destructive or
unnecessary write was made against `umr_tasks`; `directive_engine.py` was not
touched, honoring PR 153's ownership.
