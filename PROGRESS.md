# PROGRESS -- task-20260806-041307-pm-report-v3-five-deterministic-sections

SPEC: UMR-20260806-041307-0bfd (parent UMR-20260805-181636-32f2, grandparent
UMR-20260802-165606-4413/OCID-020). Standing principle: PM tokens go to
judgment/dispatch/audit, never manual searching -- extend
`generate_pm_report_v3.py` (already merged, real, live) with five new fixed,
deterministic, zero-AI-calls sections so the PM never needs a separate manual
check for: OCID compliance audit results, multi-report trends, stuck-task
detail, cross-repo/cross-worker collision risk, or instruction quality on
recent dispatches.

## Zero-duplication collision check (done before writing any code)

Ran `systemctl --user list-units 'veridian-worker@*' --state=running` per
this project's standing duplicate-dispatch discipline. Found two real running
units:
- `task-20260806-035541-owner-directive--build-a-real-pm-cycle-s` -- read its
  real `prompt.txt`. Scope: extend/backfill the `capability_registry` script
  registry (`superboss-register.py`) and build a *separate new* PM-cycle
  data-gathering script. Confirmed via its live workspace: modified files are
  `PROGRESS.md`, `generate_software_catalog.py`, `generate_wiring_registry.py`,
  `superboss-register.py` -- `generate_pm_report_v3.py` itself is only
  mentioned as a registry-tag target and as prior art to reference, never
  edited. Diffed the workspace's copy of `generate_pm_report_v3.py` against
  live `main`: workspace copy is 3 commits *behind*, confirming this task has
  not touched that file. **Not a collision with this task's exact scope**
  (extending `generate_pm_report_v3.py` itself with the 5 SPEC'd sections).
- `task-20260806-041150-corruption-recovery-unblocked--resume-th` -- read its
  real `prompt.txt`. Scope: sqlite3 `.recover` database-recovery sequence,
  wholly unrelated to the PM report script. **Not a collision.**

Both share this task's general parent-UMR lineage (the "PM tokens shouldn't
go to manual work" principle / report-generator chain) but neither touches
the same file or the same specific 5-item deliverable. Proceeded per the
task's own instruction ("only proceed if no live collision is found").

## Completed
- [x] Read `generate_pm_report_v3.py` in full (927 lines) before writing
      anything, to match its exact section-numbering/style/docstring
      conventions (Sections 1-8 already existed; new work is Sections 9-13).
- [x] Added `SCRIPT_VERSION = "3.1.0"` (script had no version constant before
      this task; `REPORT_FORMAT_VERSION` existed but is a distinct concept --
      the report-JSON schema tag, not the script's own version). Printed in
      both the report dict (`script_version`) and the rendered text header.
- [x] Section 9 -- Database validation fold-in: shells out to the real
      `audit_ocid_compliance.py --report` (subprocess, never the underlying
      audit logic directly), parses its real JSON, folds in
      passed/failed counts + a `newly_failing_ocids` diff against the prior
      `pm_report_snapshots.report_json` row's own stored Section-9 result
      (this script's existing prior-snapshot mechanism, reused not
      reinvented). Honest `prior_baseline_available=False` when no usable
      prior row exists yet.
- [x] Section 10 -- 10-report trend analysis: pure first-half-average vs
      second-half-average arithmetic over the real last (up to) 10
      `pm_report_snapshots` rows for `swap_free_pct`, `load_1min`,
      `gtm_pass_count`. Fixed `TREND_STABLE_TOLERANCE_PCT = 5.0`
      (documented in the module docstring, same spirit as the existing
      `SWAP_FREE_PCT_WARN_THRESHOLD`/`LOAD_1MIN_WARN_THRESHOLD`). Honestly
      reports real row count when fewer than 10 rows exist; never fabricates
      a trend from `<2` data points (`insufficient_data`).
- [x] Section 11 -- Deterministic stall detection: refactored
      `get_stuck_tasks()`'s file read into a shared
      `_load_stuck_tasks_heartbeat_doc()` helper (zero duplicate parsing),
      added `get_stuck_tasks_detail()` which folds one real line per real
      stuck task (`task_id`, `blocked_minutes`, `last_note` -- confirmed
      exact field names by inspecting the live
      `STUCK_TASKS_HEARTBEAT.json` before writing this, not guessed).
- [x] Section 12 -- Deterministic collision detection: (a) real `gh pr list`
      + pairwise `gh pr diff --name-only` set-intersection per tracked repo;
      (b) real running `veridian-worker@*`/`veridian-supervisor@*` units'
      `prompt.txt` UMR-citation sets, pairwise intersected. Emits
      `COLLISION_DETECTED=YES/NO` with named pairs. `COLLISION_TRACKED_REPOS`
      documented in the module docstring: no single pre-existing canonical
      tracked-repo list was found in this codebase (resource_governor.py's
      duplicate-PR guard and status-remediation-tick.py's `TRACKED_REPOS`
      are two different, narrower, real subsets for two different
      purposes) -- used `("compliance-tracker", "veridian-scripts")`, the
      two repos this task's own directive named, env-overridable like every
      other constant in this file.
- [x] Section 13 -- Deterministic instruction quality check: last 20
      `umr_tasks` rows (`ORDER BY ts_submitted DESC`), fixed 3-rule check
      against `inputs_json.prompt` (UMR citation present / vague-verb list
      absent / structural concrete-completion signal -- file-path-looking
      token or `PR #<N>` reference). `DETERMINISTIC_INSTRUCTION_COUNT=<N>/<real
      denominator>` (honest when fewer than 20 real rows exist).
- [x] Tests: 38 new unit tests added to `test_generate_pm_report_v3.py`
      covering the deterministic logic in all 5 new sections against
      real/realistic fixture data (trend arithmetic, stall-detection
      parsing reuse, collision pairwise-overlap logic for both PR-file and
      worker-UMR sources, instruction-quality rule checks) -- subprocess/gh/
      systemctl mocked, rule logic itself exercised for real.
      `test_generate_pm_report_v3.py`: 54/54 passing. Full repo suite:
      **228/228 passing**, zero regressions. `python3 -m py_compile` clean.
- [x] Ran the updated script for real against the live server once
      (`--no-db-write`) -- full sample output with all 5 new sections
      genuinely populated captured in the final report to the Owner/PM.

## Remaining
- [ ] Open PR, get independent review via `veridian-task.py adopt` +
      supervisor review, confirm real merge lands.
