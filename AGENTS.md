# AGENTS.md — Authorized AI Agents (veridian-scripts)

> Owner: Rajat Agarwal (raajat.agarwal@gmail.com)

This document is the veridian-scripts-specific counterpart to `FChecklist/compliance-tracker`'s
`AGENTS.md`. Written from scratch for this repository — veridian-scripts's own history and
infrastructure are different from `compliance-tracker`'s, and this file says so honestly
rather than asserting a governance setup that doesn't exist here yet.

## What this repo actually is

veridian-scripts holds the AI-work dispatch, orchestration, audit, and memory tooling that
runs VERIDIAN's multi-agent task pipeline — `dispatch_core.py`, `dispatch-tick.py`,
`superboss-register.py` (the one real canonical script for every real read/write against
`superboss-register.sqlite`, per that file's own docstring — a same-named but stale duplicate
also exists in `claude-control`; that one is not canonical), `wiring_query.py`, RCAs, and the
supervisor/quality-gate scripts that decide whether a worker task's PR merges. It is deployed
live at `/opt/veridian/scripts` on this box (checked out at `main`).

## Evidence of how this repo has been built so far

**[FACT, verified via `git log` and `gh api`]** — `main` is the default branch, actively
maintained (commits daily as of 2026-08-14). Work follows the pattern visible in `git log`: a
worker branch per task (`worker/task-<id>`), a PR against `main`, review/audit, then a "Merge
pull request" commit — treat this as the required review surface.

**[NOT APPLICABLE YET]** — a named, per-repo "Authorized Agents" roster (the kind
`compliance-tracker/AGENTS.md` has, with named triggers, API keys, and permissions) does not
exist for veridian-scripts as a distinct thing from the dispatch pipeline this repo itself
implements — the roster here effectively *is* whatever `dispatch_core.py`/`superboss-register.py`
dispatch, which is documented in this repo's own scripts and RCAs rather than restated here.
This document establishes the governance discipline below rather than fabricating a roster
that would just duplicate what the dispatch code already enforces mechanically.

## Operating Rules

1. **Owner sign-off required to weaken any rule below.** Any change that removes, disables,
   or routes around a rule in this file requires Rajat Agarwal's explicit written
   instruction, quoted in the PR description — the same anti-bypass principle as
   `compliance-tracker/AGENTS.md` Operating Rule 9. Extending or tightening a rule never
   requires this.

2. **PR-against-`main` is the required review surface, for genuine code/test/config/schema
   changes.** Work on a branch, open a PR against `main`. **[Added 2026-08-16, Owner
   directive UMR-20260816-171513-5901]** Do NOT run `gh pr create` yourself for a diff that
   is progress/documentation only (e.g. only your own `progress/<task_id>.md`) — the
   automated worker/supervisor pipeline (`supervisor-entrypoint.sh`'s DOCS-ONLY-PR-GUARD-
   BLOCK, switch `VERIDIAN_GATE_PR_ON_CODE_CHANGE`, default on) already preserves that note
   via the task's own checkpoint record and will not open (or will close, if you already
   opened one) a PR for it — real, measured evidence: 422 open PRs on
   `FChecklist/compliance-tracker` as of 2026-08-16, 189 with a "docs" title prefix, against
   a near-zero real landing rate, largely from exactly this pattern.

3. **No fabricated governance.** Do not add "Authorized Agents" entries, CI job names, or
   enforcement claims to this file that don't correspond to something real in this
   repository. If a rule is aspirational, mark it `[POLICY ONLY]` or `[NOT APPLICABLE YET]`.

4. **`superboss-register.py` is the one real canonical script for every real read and every
   real write against `superboss-register.sqlite`.** Real raw SQL against that database from
   outside this script is not the standard procedure — extend the function library in
   `superboss-register.py` instead (see that file's own "CANONICAL SCRIPT" docstring note for
   the established convention) rather than writing a second parallel script.

5. **Search-Reuse Discipline — Added 2026-08-14 (Owner-approved, addendum to P1
   UMR-20260806-171945-5767; citation: `OWNER_DECISIONS_NEEDED_2026-07-23.yaml` entry
   `id=crontab-drift-approved-2026-08-14`, `status=approved`).** Real indexes already exist
   and are already used by the deterministic dedup reviewer for dispatch-level decisions —
   `system_index`, `capability_registry`, `wiring_registry` (all three:
   `/opt/veridian/ai-os/memory/superboss-register.sqlite`), `CLAUDE_MEMORY_INDEX.md`,
   `dead_ends.json`, `open_questions.json` (all three: `/opt/veridian/ai-os/memory/`). A
   cross-repo audit on 2026-08-14 found zero instances of any "check the index first"
   instruction in any real `AGENTS.md`, so different worker tasks were repeatedly
   re-discovering the same real facts via fresh exploratory search, wasting real tokens.
   Every worker must: (a) before broad exploratory search, check whether the fact needed is
   already answered by one of the six indexes above (`superboss-register.py
   lookup-capability`/`list-capabilities`/`--query-umr --search` are the real CLI entry
   points into three of them — see this repo's own `find_code.sh` and the dispatch guard
   hooks for the established convention), and cite what was checked in the PR description or
   progress log, even if the check came up empty; (b) only do fresh search for what those
   indexes don't already answer — this is not a reason to skip real verification of current
   state, only a reason not to duplicate a search someone already did; (c) if a fresh search
   turns up a genuinely new fact worth reuse, write it back to the appropriate index
   (`capability_registry`/`wiring_registry` via `superboss-register.py`,
   `CLAUDE_MEMORY_INDEX.md`, `dead_ends.json`, `open_questions.json`) so the next worker
   doesn't have to rediscover it; (d) this does not relax any rule above — a cited index
   lookup is never a substitute for the audit, test, or completion requirements this file or
   any per-task protocol otherwise imposes. Does not assume zoekt or any other code-search
   service is running — no zoekt systemd unit exists as of this writing; verify what's
   actually available before relying on it.

## Contact

Repository owner: raajat.agarwal@gmail.com
