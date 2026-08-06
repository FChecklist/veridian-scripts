# PROGRESS -- task-20260806-165917-extend-deterministic-report-to-cover-pr

## Completed
- [x] Verified the SPEC's two premises independently before writing code (per this repo's
      own false-premise-verification convention):
      - "PM keeps re-deriving swap/EMERGENCY_STOP/worker-count values that already exist in
        script output" -- **true**. Section 1 of `generate_pm_report_v3.py` already reports
        `swap_free_pct`, `emergency_stop_present` and `parallel_worker_count` on every run.
        This is not a code gap; the fix is documentation (see below), not new logic.
      - "real gaps genuinely do not exist in the script yet: PR merge state for a tracked-PR
        list, and UMR status tracking for recent owner-dispatched rows" -- **true**, confirmed
        via `grep` for `gh pr view` (zero hits) and inspection of Section 14 (aggregate counts
        only, no per-row listing, no 2h-window scoping).
      - Also independently re-confirmed the standing finding from `UMR-20260806-091407-5767`'s
        own docstring block: the real "Reporting Contract V3" SKILL.md lives only at
        `C:\Users\Dell\.claude\scheduled-tasks\veridian-server-sentinel\SKILL.md` on the Owner's
        local Windows machine and is **not accessible from this Linux server** -- re-checked via
        a bounded search across `/opt/veridian` (no SKILL.md, no synced copy, anywhere). This
        task therefore could not literally "update SKILL.md" as the SPEC's last sentence asked;
        see disposition below.
- [x] `generate_pm_report_v3.py` SCRIPT_VERSION 3.4.0 -> 3.5.0: added two new deterministic,
      zero-AI-call sections (no new DB columns; both fold into the existing `report_json` blob):
      - **Section 16 -- TRACKED PR MERGE STATE**: real `gh pr view <N> --repo <org>/<repo>
        --json number,title,state,mergedAt,mergeCommit,headRefName,url` for every entry in a
        real, configurable, file-backed tracked-PR list (`load_tracked_pr_list()`,
        `get_pr_view()`, `get_tracked_pr_merge_state_section()`). Path is
        `TRACKED_PR_LIST_PATH` (env-overridable via
        `VERIDIAN_PM_REPORT_TRACKED_PR_LIST_PATH`, default `{SCRIPTS}/TRACKED_PRS.json`) --
        **never a hardcoded PR number in the script**. New file `TRACKED_PRS.json` added,
        shipped as an empty list `[]`: this task's own directive named no specific PR numbers
        to track, so no starter list was invented -- PM/Owner populates it going forward. A
        missing config file is an honest "nothing configured yet" state, not an error; a
        present-but-malformed file IS a real, surfaced error.
      - **Section 17 -- RECENT OWNER-DISPATCHED UMR STATUS**: real per-row SQL listing
        (`get_recent_owner_umr_status_section()`) of every `umr_tasks` row with
        `source_trigger='owner_dispatch_gateway'` and `ts_submitted` within the trailing
        `OWNER_UMR_RECENT_WINDOW_HOURS` (default 2h, env-overridable via
        `VERIDIAN_PM_REPORT_OWNER_UMR_RECENT_WINDOW_HOURS`), each row's `umr_id`/`status`/
        `ts_submitted`/`age_hours`. Complements Section 14's aggregate-only counts.
      - Both sections wired into `build_report()`/`render_report_text()`, module docstring
        updated with a full "Sections 16-17" block (definitions, real file format, honest
        SKILL.md-inaccessible note).
- [x] 13 new unit tests added to `test_generate_pm_report_v3.py` (all real config-file/
      `gh`-mocked/DB-fixture cases: missing config, malformed config, wrong shape, missing
      required key, real `gh pr view` success/failure, end-to-end tracked-PR section, window
      correctness, empty-window honesty, missing-column graceful degrade). Existing
      `test_end_to_end_smoke_run` and `test_render_report_text_survives_per_metric_
      insufficient_data` updated to include the two new report keys/section headers.
      **Full suite: 115 passed, 1 pre-existing failure** (`test_end_to_end_smoke_run` --
      confirmed via `git stash` to fail identically on the base branch before any change in
      this task; a `load_module_from_path` monkeypatch gap for `gtm_test_script_build_check.py`
      unrelated to Sections 16/17 and out of this task's scope). A standalone manual
      end-to-end run of `build_report()`/`render_report_text()` (full fake DB + fully-wired
      `load_module_from_path` mocks) confirmed Sections 16-17 integrate correctly into the real
      pipeline.
- [x] SKILL.md disposition (honest, not fabricated): the real Windows-side SKILL.md was **not
      edited** -- this server has no access to it (independently re-confirmed, see above). The
      SPEC's "state plainly the PM must read this report every cycle instead of re-deriving
      values manually" instruction was instead recorded in the one place on this server that is
      both real and PM-facing: `generate_pm_report_v3.py`'s own module docstring (the
      established practical substitute for SKILL.md on this server, same precedent as the
      Section 4 placeholder-until-real-source situation). If/when the Owner syncs a real copy
      of SKILL.md to this server, the equivalent instruction belongs there too -- flagged
      explicitly in the docstring for whoever does that sync.

- [x] Real branch pushed, real PR opened: `worker/task-20260806-165917-extend-deterministic-report-to-cover-pr`
      -> **PR #198** (`https://github.com/FChecklist/veridian-scripts/pull/198`), awaiting
      independent review before merge.

## Remaining
- [ ] PR #198 review + merge (not this task's own action -- independent review required per
      this repo's own discipline).
- [ ] None else for this task's own scope. Open follow-ups for a *future* task (not started here):
      - Fix the pre-existing, unrelated `test_end_to_end_smoke_run` failure (see above).
      - If the Owner ever makes the real Windows SKILL.md reachable from this server, mirror
        this task's "read the report, don't re-derive" instruction into it directly.
