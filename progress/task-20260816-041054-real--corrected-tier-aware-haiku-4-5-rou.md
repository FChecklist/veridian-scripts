# PROGRESS -- task-20260816-041054-real--corrected-tier-aware-haiku-4-5-rou

Governing UMR: UMR-20260816-041030-cdc4. Supersedes the false-premise
finding in UMR-20260815-135358-cbb7 (`FINDING_haiku_tier_routing_premise_false_2026-08-15.md`,
PR #426), which correctly declined the old `umr_tasks.tier`-based design
and named the two real prerequisites needed before this could be safely
implemented.

## Completed
- [x] Independently re-verified cbb7's finding is real (read PR #426's
      FINDING file directly via `gh api`): `umr_tasks.tier` is dispatch
      priority, not complexity; a real 3-value `mechanical`/`integrative`/
      `judgment` enum already exists as `--complexity-tier` on
      `plan_generator.py`/`pm_lifecycle.py` but was embedded only as prompt
      text (`## COMPLEXITY_TIER`), never threaded as a real field.
- [x] Traced the REAL call chain from `pm_lifecycle.py`'s `run` command to
      the actual task.yaml-creating call: `dispatch_task()` ->
      `dispatch-owner-task.sh` -> `resource_governor.py --submit` (queues a
      `veridian_task_create` row) -> `resource_governor.py`'s
      `_perform_spawn()` (task_kind=='veridian_task_create' branch) ->
      `veridian-task.py create` (writes task.yaml via `save_task()`).
      Confirmed `plan_generator.py` itself has NO dispatch call at all (its
      complexity_tier only tags its own `plans`/`plan_steps` tables) --
      nothing to thread on that side; `pm_lifecycle.py` is the one real,
      live pipe.
- [x] Threaded `complexity_tier` end to end as a real, optional field
      (absent by default -- never a null placeholder) through all 5 real
      hops: `pm_lifecycle.py` (`dispatch_task()`), `dispatch-owner-task.sh`
      (new `--complexity-tier` flag, folded into the `SPEC_FILE` inputs),
      `resource_governor.py` (`_perform_spawn()`'s `veridian_task_create`
      branch), `veridian-task.py` (`cmd_create`'s new `--complexity-tier`
      arg), and `worker-entrypoint.sh` (reads `task.yaml`'s
      `complexity_tier` via the file's own established `yaml.safe_load`
      pattern).
- [x] `worker-entrypoint.sh`: both real `claude -p` call sites (main
      invocation, line ~397; `--continue` auto-fix retry, line ~857) now
      use `--model "$CLAUDE_MODEL"`, computed once from `task.yaml`:
      `complexity_tier == 'mechanical'` -> `haiku`; everything else
      (absent, `integrative`, `judgment`, or any other value) -> `sonnet`,
      unchanged.
- [x] Real Owner-decision record committed:
      `OWNER_DECISION_2026-08-16_HAIKU_TIER_ROUTING_MECHANICAL_EXCEPTION.md`
      (veridian-scripts-scoped, quotes the Owner authorization verbatim,
      does NOT touch `compliance-tracker/AGENTS.md` Rule 8).
- [x] Real end-to-end verification, all real code exercised (only the
      subprocess/systemd/network boundary mocked, same convention this
      repo's other tests use) -- `tests/test_complexity_tier_haiku_routing.py`
      (11 tests) + `test_dispatch_owner_task_complexity_tier.py` (2 tests,
      real bash + real tmux):
      - `pm_lifecycle.dispatch_task()` threads `--complexity-tier` into the
        real `dispatch-owner-task.sh` argv (and omits it when not given).
      - `dispatch-owner-task.sh` threads it into the real `--submit
        --spec-file`'s inputs JSON (and omits the key when not given) --
        proven against the real spec file this script writes for real.
      - `resource_governor.py`'s `_perform_spawn()` threads it into the
        real `veridian-task.py create` argv (and omits it when absent from
        inputs).
      - `veridian-task.py`'s real `cmd_create()` (real git worktree, real
        `task.yaml` write) genuinely writes `complexity_tier: mechanical`
        when given, and writes NO `complexity_tier` key at all when not.
      - `worker-entrypoint.sh`'s real, unmodified model-routing snippet
        (extracted verbatim via regex from the live script, not
        re-implemented) resolves `mechanical` -> `haiku` and
        `integrative`/`judgment`/absent/empty -> `sonnet`.
      All 13 new tests pass; confirmed pre-existing repo test suites
      (`tests/test_veridian_task_create_stderr_and_reason.py`,
      `test_dispatch_owner_task_attach.py`, `test_resource_governor_*`,
      `tests/test_pm_lifecycle.py`) are unaffected (one pre-existing,
      unrelated failure in `test_worker_boot_activation_and_resume.py`
      confirmed via `git stash` to fail identically on the unmodified
      checkout -- a live-systemd-unit-count environmental flake, not
      caused by this diff).

## Remaining
- [ ] None -- objective complete. (Optional future follow-up, out of
      scope here: thread `complexity_tier` through `dispatch_audit_fix()`/
      `dispatch_independent_audit()`'s fix/audit re-dispatch helpers too --
      they currently hardcode `complexity_tier="moderate"` as prompt text
      only, unrelated to this task's real field-threading; omitting it
      there is safe, since absent/unset already defaults to `sonnet`.)
