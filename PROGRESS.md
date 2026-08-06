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

- [x] Built `verify_registry_file_paths.py` -- the real deterministic
      checker the SPEC requires, all 3 tables in one run, right-now
      `os.path.exists()`, never the DB's own cached `verification_status`.
      Reuses `generate_wiring_registry.py`'s own `normalize_path`/
      `path_exists` for `wiring_registry` (never reimplemented), a small
      new resolver for `capability_registry`'s free-text `apis`/`workflow`/
      `business_rules[].mechanism_path` fields (absolute or
      `VERIDIAN_ROOT`-relative, matching that module's own convention), and
      a direct `memory_file_path` check for `ai_agent_registry`. Correctly
      excludes entity_types whose `wiring_registry.path` is by-design not a
      single disk path (`engine`/`gateway` multi-file summaries, `route`
      `src -> dst` pairs, `cron_job` command lines, `ai_role` slugs,
      `supabase_table` `schema.table` identifiers, `vercel_project`/
      `dispatch_event` NULLs) -- checking those literally would have been
      the same category error this session's own memory already flagged
      once. See `REGISTRY_FILE_PATH_VERIFICATION_2026-08-06.md`/`.json` for
      the full real report.
- [x] **Ran it for real. Real counts:**
      - `capability_registry`: **14/16 PASS, 2 FAIL**
        (`CAP-20260806-182313-9028`/`CAP-20260806-182326-0e3e`, both
        `apis` fields carrying a bare CLI subcommand name instead of a real
        script path -- root cause: a still-**live** sibling task
        (`task-20260806-181146-...`, this SPEC's own governing-chain
        sibling) registered them minutes before this check, real code on
        open PR #205. Deliberately **not** edited this session to avoid
        racing a live sibling's own row -- documented with the exact
        correct fix for whoever closes PR #205).
      - `wiring_registry`: **7117/7928 PASS, 64 FAIL**, 747 correctly
        excluded as not-a-single-disk-path by entity_type design. Root
        causes (all confirmed, not fabricated): 4 rows for 2 genuinely
        obsolete scripts (`module-queue-dispatcher.py`/
        `queue-dispatcher.py`, consolidated into `dispatch-tick.py`
        2026-07-26 per `SOFTWARE_CATALOG.yaml`'s own text -- orphaned
        because `upsert_live_wiring_registry()` only ever upserts, never
        prunes rows a fresh run stops producing); ~60 stale/never-written
        `knowledge_engine`-sourced planning-doc references from
        2026-07-23/24/25 (`ai-os-scripts/*.py`, dated `*.yaml` audits); 1
        genuine category mismatch (`https://claude.ai/...` URL modeled as
        a `file` artifact).
      - `ai_agent_registry`: 0/0 -- real, live (merged this session), 0
        rows until the first `ensure-agent`/`record-work` call.
- [x] **Fixed what was safely fixable this session**, all via existing
      canonical mechanisms, never raw SQL: re-ran
      `verify-knowledge --path ...` (existing CLI) against 11 stale
      `knowledge_engine` rows the live DB had cached as `PATH_MISSING` --
      6 correctly moved to honest `HASH_DRIFTED` (file exists again,
      content changed since the stale scan), 5 confirmed still genuinely
      missing (real, not stale-cache artifacts). Then re-ran
      `generate_wiring_registry.py` for real (the same script this box's
      own cron already runs periodically) so `wiring_registry` picked up
      the correction. Did **not** attempt a blind bulk fix of the
      remaining ~55 stale `knowledge_engine` rows or delete the 4 orphaned
      script rows -- no canonical single-row deletion mechanism exists yet
      for `wiring_registry` (checked: no `deregister-entity` CLI), and
      building one safely (scoped to this generator's own entity_id
      namespace only, never touching `agent_work_briefing.py`'s ad-hoc
      rows -- confirmed this is a real risk, not a hypothetical one) is a
      distinct, separately-scoped unit of work, documented as the real
      concrete next step in the verification report rather than
      guessed/rushed.
- [x] Confirmed orchestrator path usage already satisfies the SPEC:
      `worker-entrypoint.sh` (PR #199, merged this session) resolves
      `ai_agent_registry.py`/`agent_work_briefing.py` structurally
      (`SCRIPTS`-relative `os.path.join`, matching `ai_agent_registry.py`'s
      own convention) when it calls `assemble-briefing`/
      `record-completion` -- never a hardcoded absolute guess. Nothing
      further built here; the existing pattern already prevents drift by
      construction rather than needing a runtime lookup against this
      verification script's output.

## Remaining
- [ ] Wire `verify_registry_file_paths.py` into
      `generate_platform_completion_checklist.py`/
      `generate_pm_report_v3.py` Section 16 (currently `wiring_registry`
      only) so all 3 tables' real pass/fail counts surface in the
      already-scheduled PM report cadence, not just this standalone
      script's own stdout/report file. Not done this session (budget) --
      the script itself is complete, tested against live data, and its
      JSON output (`--json`) is already report-generator-ready.
- [ ] Add a narrow, single-row `deregister-entity --entity-id <id>
      --reason <text>` CLI to `superboss-register.py` (matching this
      codebase's existing single-row-only convention, e.g.
      `mark-umr-terminal`) and use it on the 4 confirmed-obsolete
      `module-queue-dispatcher.py`/`queue-dispatcher.py` wiring_registry
      rows. Real, scoped, evidence already gathered -- not built this
      session.
- [ ] Once sibling PR #205 (`task_precedent_search`/
      `capability_graduation_recording`) merges, correct its 2
      `capability_registry` `apis` fields to the real host script path
      (`/opt/veridian/scripts/superboss-register.py <subcommand>`) via
      `register-capability`'s idempotent upsert.
- [ ] The ~55-row `knowledge_engine` stale-planning-doc backlog (see
      verification report #3) needs a real decision per row (doc actually
      never written -> leave `PATH_MISSING` honestly; doc moved -> correct
      `artifact_path`) -- out of this session's remaining budget, real
      identities already listed in
      `REGISTRY_FILE_PATH_VERIFICATION_2026-08-06.json`.
- [ ] "The one unified orchestrator" itself -- deliberately not built here.
      Per sibling task `-181141`'s own finding (merged PR #202, this
      task's own PROGRESS.md history above), 5 concurrent amendment SPECs
      are redefining that same deliverable's requirements in real time;
      building it unilaterally in this task would create exactly the
      fragmented-duplicate-version outcome the governing chain warns
      against. Needs Owner/dispatcher reconciliation across all 5 first.
