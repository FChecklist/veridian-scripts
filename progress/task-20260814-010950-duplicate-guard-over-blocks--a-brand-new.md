# task-20260814-010950-duplicate-guard-over-blocks--a-brand-new

Governing chain: P1 UMR-20260806-171945-5767. UMR for this task: UMR-20260814-010944-2e16.

## What this task fixed

Two independent real over-broad duplicate guards in `resource_governor.py` that were
silently killing brand-new, legitimate dispatches (never spawned, `ts_dispatched=NULL`)
by comparing them against unrelated records.

### 1. `find_pr_for_task_identity()` Stage 6 -- cross-repo / non-target PR-number over-match

Real incident, reproduced live 2026-08-14T01:01:52Z: `UMR-20260814-010152-7981`
(`task_identity='owner-task-20260814-010149-432146'`, a **brand-new** dispatch, zero
prior branches -- `prior real branch(es) []`) titled *"...every audited merged fix
(incl PR 322) is NOT running"* was rejected `rejected_duplicate` citing
`FChecklist/claude-control#185`, whose real title is *"Resolve conflict, audit and land
veridian-scripts PR 322..."*. Both titles say "PR 322" -- one means
`veridian-scripts#322` (a real, unrelated, already-merged PR in a **different repo**),
the other is a `claude-control` PR that merely talks *about* it. A PR number with no
repo attached, and mentioned only as a parenthetical citation, is not evidence of
duplicate work.

Fix: Stage 6 now resolves the title's PR-number reference the same repo-scoped way
`target_pr_already_resolved()` (UMR-20260813-165620-aac7) already does -- prefer a
repo-qualified reference (`_repo_qualified_pr_ref()`) and search **only** that repo;
otherwise a bare number is resolved **only** against `hint_repo`, never scanned across
the rest of `GH_PR_CHECK_REPOS`. New helper `_title_pr_reference_is_citation_only()`
additionally drops the reference entirely (no gh call at all) when the number is only a
parenthetical aside in this task's own title, mirroring the existing
`_DISCLOSURE_CITATION_RE` fix (UMR-20260813-172606-101a) applied to the query side
instead of the candidate side.

### 2. `_orchestrator_reuse_verdict_gate()` (Step 2) -- task/resume intent vs `wiring_registry` file cross-type match

Real, quantified damage over the last 7 days (see below): **474 of 489**
`status=rejected_duplicate` `umr_tasks` rows, **all** `systemctl_action` task-resume
rows, **all** `ts_dispatched IS NULL` (never spawned), rejected with
`best_match={'source': 'wiring_registry', 'kind': 'file'}`. Confirmed live:
`UMR-20260814-004301-2d07` (`task_identity='task-20260807-071557-retry-ai-cost-
governance-finops-cost-vis'`, a `systemctl_action` `start` resume) scored 0.953 against
wiring_registry row `file-10d3faee408e`, which is **that same task's own** prior
`task.yaml`, auto-registered as a generic `entity_type='file'` row by
`full_server_file_registration.py`. Resuming an already-existing unit can never
"duplicate" a wiring_registry file by construction -- this is a category error, not a
scoring-threshold problem.

Fix: `_orchestrator_reuse_verdict_gate()` (`reuse_verdict_engine.assess()`) is now only
invoked for `task_kind == 'veridian_task_create'` rows, mirroring the exact scoping
Stage 4/5/6 already applies just above it in `_dispatch_one_inner()`. `systemctl_action`
rows (start/stop/restart of an existing unit, including every
`dispatch-tick:resume_interrupted_workers` task-resume) never reach the reuse-verdict
check at all.

## Real damage, quantified (live `umr_tasks`, last 7 days, `>= 2026-08-07T01:20:06Z`)

- Total `status=rejected_duplicate` rows: **489**
- Never spawned (`ts_dispatched IS NULL`): **485**
- Rejected via `duplicate-PR guard (Stage 4/5/6)`: **6** -- all 6 had `prior real
  branch(es) []` (brand-new task_identity, never ran before) -- i.e. **all 6** were only
  ever caught by the flawed Stage 6 title-number match, never a real branch/PR.
- Rejected via `reuse_verdict_engine.assess()` `duplication_blocked`: **474** -- **all
  474** had `best_match.kind == 'file'` (wiring_registry cross-type match), **all
  never spawned**.

## Tests

New file `tests/test_duplicate_guard_over_broad_false_positives.py`, 8 tests, all real
(mocked `_run()` gh subprocess boundary / isolated temp-file sqlite DB, never network,
never the live production DB):

1. `test_brand_new_task_identity_with_pr_number_in_title_and_no_prior_branch_not_blocked`
   -- the exact real UMR-20260814-010152-7981 shape, verbatim title text -> not blocked.
2. `test_cross_repo_same_number_pr_does_not_block` -- repo-qualified reference to a
   different repo than hint_repo -> searches only the qualified repo, never the other.
3. `test_citation_only_pr_reference_helper_distinguishes_parenthetical_from_target`
4. `test_genuine_same_repo_same_target_duplicate_still_blocked` -- real #58/#64/#65/#66
   shape still caught.
5. `test_repo_qualified_same_repo_duplicate_still_blocked`
6. `test_orchestrator_reuse_verdict_gate_never_invoked_for_systemctl_action_row`
7. `test_dispatch_one_end_to_end_resumed_systemctl_action_not_blocked_by_wiring_registry_file`
   -- full `dispatch_one()` E2E, real scratch DB, planted fake 0.953 file-kind verdict
   that must never even be consulted.
8. `test_dispatch_one_end_to_end_veridian_task_create_still_blocked_by_reuse_verdict` --
   control case proving the gate still runs/blocks for genuine new-work rows.

Real output (`python3 -m pytest tests/test_duplicate_guard_over_broad_false_positives.py -v`):

```
8 passed in 0.39s
```

Full `tests/` regression suite after the fix: **707 passed**, 1 pre-existing failure
(`test_timer_is_really_enabled_and_active`, an environment-dependent systemd-timer
check unrelated to this change, same failure present on `origin/main` before this
branch).
