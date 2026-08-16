# PROGRESS -- task-20260816-092554-make-mechanical-tier-model-selection-act

SPEC: finish + deploy the tier-aware mechanical model-selection feature
(merged origin/main earlier today under UMR-20260816-041030-cdc4, real
AUDIT: PASS, but INERT/NOT DEPLOYED). Fix the merge-audit-flagged
inertness gap in pm_lifecycle.py's `run` subcommand --complexity-tier
argparse default, get a fresh real audit + merge, deploy to both live
checkouts, then prove it live with two real end-to-end dispatches.

## Completed

- [x] Independently re-verified REAL STATE (did not trust the SPEC's
      claims blindly, per prior false-premise incidents):
  - `/opt/veridian/scripts` HEAD confirmed at `10a9af6` (2026-08-15),
    branch main -- matches claim. Only untracked scratch files present
    (`queue-manager.py`, `timer-manager.py`, sqlite backups, a
    `.rollback-*` file) -- no modified tracked files to preserve.
  - `/opt/veridian/repos/veridian-scripts` HEAD also confirmed at
    `10a9af6`, clean -- matches claim.
  - Confirmed via grep: `complexity_tier` field genuinely absent from the
    live checkout's code at that SHA.
  - Confirmed the defect itself by reading the code directly:
    `pm_lifecycle.py`'s `p_run.add_argument("--complexity-tier",
    default="moderate")` -- `"moderate"` is not in `plan_generator.py`'s
    `VALID_TIERS = ["mechanical", "integrative", "judgment"]` but is
    truthy, so `dispatch_task()`'s `if complexity_tier:` always fired and
    wrote an invalid `complexity_tier: moderate` into every real
    `task.yaml` from the one real live caller (this module's own
    docstring usage example, which never passes `--complexity-tier`).
    `worker-entrypoint.sh`'s routing snippet only matches `=='mechanical'`
    so this didn't cause an *unsafe* model pick (falls through to sonnet)
    but it did permanently poison the field and keep mechanical/Haiku
    routing inert on the only real production path that reaches it --
    exactly the "untested real argparse default" gap the merge audit
    flagged. Confirmed via existing `tests/test_complexity_tier_haiku_routing.py`
    that every existing test only exercises Python-level keyword defaults
    (`complexity_tier="mechanical"` or omitted), never a real parsed argv
    through `build_parser()`.
- [x] Fix implemented in `pm_lifecycle.py`:
  - `--complexity-tier` argparse default changed from `"moderate"` to
    `None`.
  - `build_tightened_prompt()` guarded (`if complexity_tier:`) so a real
    `None` from the new default no longer crashes on `.strip()` -- mirrors
    the existing `known_context` guard pattern already in the same
    function.
- [x] Added real regression tests to
      `tests/test_complexity_tier_haiku_routing.py` (section 5) that parse
      an actual argv through the real `build_parser()` -- the real live
      call path, not a Python-level function default:
  - `test_run_subcommand_real_argparse_default_is_none_not_invalid_moderate`
  - `test_run_subcommand_real_argparse_default_never_reaches_dispatch_owner_task_argv`
  - `test_run_subcommand_real_argparse_explicit_mechanical_still_threads_through`
- [x] Ran full relevant test files locally, all green:
  `tests/test_complexity_tier_haiku_routing.py` (14 passed),
  `tests/test_pm_lifecycle.py` (25 passed),
  `test_dispatch_owner_task_complexity_tier.py`,
  `tests/test_target_identifier_invocation_citation_dedup.py`.

- [x] Committed (89b30ab) + pushed
      `worker/task-20260816-092554-make-mechanical-tier-model-selection-act`.
      Opened PR #433 against origin/main:
      https://github.com/FChecklist/veridian-scripts/pull/433
- [x] Confirmed no live automatic supervisor process would pick this PR up
      on its own: `veridian-pm-sentinel-tick.timer` (and every other
      `veridian-cron-*.timer`) is `not-found inactive dead` on this host --
      only `veridian-governor-tick.service` (spawns real workers from the
      queue) is actually running. So dispatched a genuine independent
      audit myself via the real live pipeline (`dispatch-owner-task.sh`,
      same mechanism `pm_lifecycle.py`'s `dispatch_independent_audit()`
      uses) from `/opt/veridian/scripts` -- a fresh, separate worker
      process reviews PR #433 and posts its own real AUDIT: PASS/FAIL
      comment; I never self-certify. Real dispatch:
      `umr_id=UMR-20260816-093429-ffbb`,
      `task_identity=owner-task-20260816-093427-2906627`.

## Remaining

- [ ] Wait for the real independent audit comment on PR #433 (polling via
      Monitor).
- [ ] Deploy: `git status` on both `/opt/veridian/repos/veridian-scripts`
      and `/opt/veridian/scripts`, preserve any real local modification,
      fast-forward both to the new origin/main.
- [ ] Prove it live:
  - One real end-to-end dispatch carrying `complexity_tier=mechanical` --
    show the real resulting `task.yaml` and the real `--model` argument
    the worker actually invoked resolved to a Haiku 4.5 model id.
  - One real non-mechanical dispatch -- show it still resolves to sonnet.
- [ ] Report: new PR number, real mergedAt timestamp, real origin/main SHA
      now checked out at `/opt/veridian/scripts`, and real evidence from
      both live proof dispatches.
- [ ] `agent_work_briefing.py record-completion` with a real summary.
