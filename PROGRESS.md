# PROGRESS -- task-20260806-181150-amendment--every-real-file-path-in-every

SPEC: amendment to UMR-20260806-124055-bc80 / -124327-6ffb / -124654-a8d6
(own UMR: UMR-20260806-124936-13b1). Every real row in `capability_registry`,
`wiring_registry`, and the new UMR-scoped `ai_agent_registry` table must
carry a real absolute file path, verified right now with a real deterministic
disk check (never assumed). Report real pass/fail counts + identities of any
failing row. Fix failures (correct path or remove row). The orchestrator
must use verified paths, never a hardcoded/guessed one.

## Completed
- [x] Verified the SPEC's own premises before writing anything (per this
      session's standing false-premise-verification practice). All three
      cited governing UMRs (`-bc80`/`-6ffb`/`-a8d6`) are real, live, `status=
      running` rows in `umr_tasks` (the CLI's own `--query-umr --search` FTS
      path returned false zeros for all of them, including a UMR known-real
      from this session's own memory -- the FTS index over
      `task_identity`/`source_trigger`/`logs_ref` doesn't index `umr_id`
      itself; a direct `SELECT ... WHERE umr_id=?` found all three
      correctly. Noted so a future task doesn't misdiagnose real UMRs as
      fabricated from `--search` alone).
      This task is 1 of 5 concurrently-dispatched amendment SPECs (siblings:
      `-181141`/`UMR-...6ffb`, `-181146`/`UMR-...a8d6`, `-181155`/
      `UMR-...720c`, `-181159`/`UMR-...37a5`) to the same not-yet-built
      "one unified orchestrator". Sibling `-181141`'s own PROGRESS.md
      (merged to `main` as PR #202) explicitly scopes this exact task
      (`-181150`) as "adds a mandatory absolute-path-exists check for every
      capability_registry/wiring_registry/agent_id row" and recommends
      **not** building the orchestrator itself in isolation pending 5-way
      reconciliation. Followed that: this task stays scoped to the
      path-verification+fix deliverable, does not attempt to build "the one
      orchestrator".
- [x] Found the real, concrete defect this SPEC describes, independently:
      `capability_registry` rows `CAP-20260806-164355-6f47`
      (`ai_agent_registry`) and `CAP-20260806-170938-a2c0`
      (`agent_work_briefing`) both pointed at
      `/opt/veridian/scripts/ai_agent_registry.py` /
      `/opt/veridian/scripts/agent_work_briefing.py` -- neither file existed
      anywhere on `main` or the live deploy checkout. Root cause: the real
      code existed, tested, on two open PRs (#194, #199) blocked on a
      trivial `PROGRESS.md` merge conflict against `main`, never a
      fictional/fabricated claim.
- [x] Resolved and merged the blocking PRs rather than leaving the rows
      broken or deleting real work:
      - Diffed PR #194 vs PR #199's copies of `ai_agent_registry.py` /
        `test_ai_agent_registry.py` / its capability record: byte-identical.
        PR #199 is a strict superset (also ships `agent_work_briefing.py` +
        live `worker-entrypoint.sh` wiring). Resolved #199's `PROGRESS.md`
        conflict against `main` (twice -- `main` advanced mid-session via
        PR #201 then #202), re-ran both scripts' own standalone test
        harnesses after each rebase (`test_ai_agent_registry.py`,
        `test_agent_work_briefing.py` -- PASS both times), pushed, merged
        via `gh pr merge 199 --merge` (commit `7f44e24`). Closed #194 as
        superseded (comment cites the byte-identical diff).
      - Confirmed live: `/opt/veridian/scripts/ai_agent_registry.py` (14915
        bytes) and `agent_work_briefing.py` (17980 bytes) now real on disk
        after pulling `main`; both standalone tests PASS against the live
        deploy checkout.
      - This closes the exact gap PR #201's own PROGRESS.md flagged as
        "Remaining": `ai_agent_registry` table/script not yet on `main`.

## Remaining
- [ ] Build `verify_registry_file_paths.py` -- the actual deterministic
      checker this SPEC requires, covering all three tables in one run:
      - `wiring_registry` already has a real per-row `path` +
        `verification_status` column, computed by
        `generate_wiring_registry.py` (reuse its `normalize_path`/
        `path_exists`, don't reimplement). Live snapshot before re-run:
        8570 rows, 22 `PATH_MISSING`, 12 `HASH_DRIFTED` -- needs a fresh
        `generate_wiring_registry.py` run (not a trust of the cached
        status) plus a real look at whether each surviving PATH_MISSING
        row is a genuine stale reference (fix path / remove row) or a
        legitimately-pathless entity type.
      - `capability_registry` has NO dedicated path/verification column --
        path candidates live in free-text `apis` (list) and
        `business_rules[].mechanism_path`, some absolute
        (`/opt/veridian/scripts/x.py`), some root-relative
        (`repos/compliance-tracker/src/...`, `scripts/x.py`). Needs a
        resolver against the known repo roots before `os.path.exists()`.
        Only 14 rows -- tractable to check by hand as a cross-check on the
        script's own output.
      - `ai_agent_registry` (now real, 0 rows until first `ensure-agent`
        call) -- check `memory_file_path` per row once populated.
      Report format: total checked / pass / fail per table + exact
      identity (capability_id / entity_id / agent_id) and broken path for
      every failure, matching the SPEC's "real count ... and real identity
      of any row whose real path check fails" requirement.
- [ ] Fix any real failures the script surfaces (correct path or remove
      row via the canonical CLI mechanism only, never raw SQL) -- not yet
      run for real on this session's live data beyond the 22/12
      wiring_registry snapshot already surfaced by PR #201's Section 16.
- [ ] Wire the verification into "the same real final checklist already
      required" -- `generate_platform_completion_checklist.py`
      (PR #201) is that checklist. Add a section (or extend
      `generate_pm_report_v3.py` Section 16, which currently only covers
      `wiring_registry`) so all three tables' pass/fail counts appear
      there, not just in a standalone script's stdout.
- [ ] Confirm/extend orchestrator path usage: `worker-entrypoint.sh`
      (PR #199) already resolves `ai_agent_registry.py`/
      `agent_work_briefing.py` via `SCRIPTS`-relative `os.path.join`
      (never a hardcoded absolute guess) when it calls
      `assemble-briefing`/`record-completion` -- confirm this still holds
      post-merge and decide whether the SPEC's "orchestrator must use
      these real verified paths directly" needs anything beyond that
      (e.g. consulting `verify_registry_file_paths.py`'s own output before
      invoking, vs. the existing pattern of resolving paths structurally
      so they can't drift in the first place).
