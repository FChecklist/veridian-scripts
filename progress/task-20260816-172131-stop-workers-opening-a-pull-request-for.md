# Stop workers opening a PR for tasks that produced no shippable change

## Completed
- [x] Hypothesis CONFIRMED against real evidence (not assumed): sampled FChecklist/compliance-tracker
  open PRs (422 open, 414 by the fleet bot, 189 "docs" prefix, 115/500-sample progress/docs-only).
  Traced 3 real docs-only PRs (#1277, #1290, #1291) end-to-end via their own task dirs.
- [x] REAL MECHANISM found, two-part (broader than the SPEC's own hypothesis text — followed the
  evidence): (1) `supervisor-entrypoint.sh:274` `gh pr create` fired unconditionally for any
  branch with AHEAD_COUNT>0, no diff-content check. (2) The worker's own Claude session directly
  ran `gh pr create` itself mid-task (confirmed live: each sampled task's own `result.json`
  contains a literal `gh pr create --title ...` call), before the supervisor ever ran — its own
  later `gh pr create` then failed "already exists" (see each task's `supervisor.log`) and fell
  through to review/audit the same progress-only PR.
- [x] `docs_only_diff_guard.py` (new): deterministic classifier, reuses (does not duplicate)
  quality-gate.sh's own already-audit-hardened DOCS_ONLY allowlist.
- [x] `supervisor-entrypoint.sh`: new DOCS-ONLY-PR-GUARD-BLOCK, switch `VERIDIAN_GATE_PR_ON_CODE_CHANGE`
  (default `1`). Docs-only diff -> no `gh pr create`, closes any pre-existing PR the worker already
  opened itself, preserves the note via a new `docs_only_completion.json` + `completed_docs_only`
  checkpoint (never discards work). Code-relevant diff -> unchanged path.
- [x] `worker-exit-status-bridge.py`: bridges `completed_docs_only` to the real, honest
  `completed_unmerged` umr_tasks status (real commit, genuinely not merged) — kept structurally
  distinct from `completed_no_change` (that one requires AHEAD_COUNT==0; this one is AHEAD_COUNT>0
  but non-code).
- [x] `worker-entrypoint.sh` + `AGENTS.md`: soft instruction telling the agent not to self-run
  `gh pr create` for docs-only work (the real gate is the deterministic guard above).
- [x] Tests: `tests/test_supervisor_docs_only_pr_guard.py` (5 real E2E subprocess tests: docs-only
  no-PR, closes pre-existing PR, code-touching still opens PR, mixed diff still opens PR, switch=0
  reverts). `tests/test_worker_exit_status_bridge.py` (+3 tests for the new bridge path). Fixed one
  pre-existing test (`test_supervisor_no_op_branch_guard.py`'s `REAL_WORK.md`->`.py`, since a `.md`
  "real work" placeholder is now — correctly — caught by the new classifier). Full suite: 76 passed.
- [x] Did NOT touch: `progress_completion_gate.py`, `quality-gate.sh`'s own gate logic, the
  Superboss AI review, the audit-comment logic, or the merge-detection block — only whether/when
  `gh pr create` fires.
- [x] Deployed live: fast-forwarded `/opt/veridian/scripts` (was at `2a077da`, 5 commits behind
  `origin/main`'s real tip) to the merged commit; real local modifications, if any, preserved not
  discarded (checked via `git status`/`stash` before pulling). Proof: see PROGRESS.md / final report.

## Remaining
- [ ] Not this task's scope (flagged, not fixed): `dispatch-owner-task.sh:761` has its own,
  separate, still-unconditional `gh pr create` on the CLI execution path — none of the 3 sampled
  docs-only PRs went through it, so it's not implicated in the measured volume, but it has the
  same latent defect and would benefit from the same `docs_only_diff_guard.py` gate in a future
  task.
- [ ] Clearing the existing 422-PR backlog is explicitly out of scope (owned by sibling dispatch
  UMR-20260816-171145-08ac) — not attempted here.
