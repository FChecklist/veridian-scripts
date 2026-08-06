# PROGRESS -- feat/pm-child-umr-proposals-umr20260806034750

SPEC: UMR-20260806-034750-05cf (parent chain UMR-20260805-185000-e94f /
UMR-20260802-165606-4413, OCID-020). Build real support in
`superboss-register.py` (+ its database) for a new standing workflow gate:
"thinking is by the Project Manager, execution is by AI agents, AI agents do
not think for themselves." Applies to real novel findings OUTSIDE
already-approved scope; explicitly NOT retroactive to already-authorized
broad-category work already in flight (e.g. the GTM script build) -- that
authorization already covers its own scope. No retroactive enforcement of
that distinction is built here (per the task's own explicit instruction),
only noted, here and in code.

## Real schema decision (read first, decided honestly, not skipped)

Read `pm_decisions_pending`'s real, live schema (`PRAGMA table_info`) and
both of its real functions (`insert_pm_decision_pending()`/
`resolve_pm_decision_pending()`, added under UMR-20260806-031558-4dbd, PR
#103) in full before deciding.

**Decision: a genuinely separate table, `pm_child_umr_proposals`, not an
extension of `pm_decisions_pending`.** Full reasoning is written as a code
comment directly above `_ensure_pm_child_umr_proposals_table()` in
`superboss-register.py` (not just here) -- summary:

1. Different real-world object, different real lifecycle.
   `pm_decisions_pending` models an OPEN QUESTION a PM answers (a
   multi-option menu, one terminal close event: opened -> resolved,
   `closed_ts`/`closed_by`/`closed_note`). This workflow models an
   AI-INITIATED PROPOSAL with a genuine three-stage lifecycle (proposed ->
   approved/redirected/held -> completed) and a real completion event
   carrying structured implementation evidence (commit/file_path/evidence)
   with no analog in `pm_decisions_pending`.
2. The task's own suggested extension columns (`decision_level`,
   `proposed_by`, `verdict`, `completion_commit`/`completion_file_path`/
   `completion_evidence`) would leave every existing `pm_decisions_pending`
   row permanently NULL across all of them (not proposals, never will be),
   and every new proposal row would leave `options_json`/
   `recommended_option` permanently NULL (a proposal is issue +
   proposed_action, not a multi-option menu) -- two different real objects
   sharing one wide table with disjoint, permanently-NULL columns in both
   directions, the schema-design reading of the Owner's zero-duplication
   instruction ("model each real thing once, correctly"), not just
   "never write the same SQL twice."
3. The relationship between the two tables is real and worth surfacing --
   expressed via `generate_pm_report_v3.py` rendering each as its own
   clearly-labeled report section (matching the existing "7. PM DECISION
   REQUIRED" convention), not by forcing one shared table.

## Real design decision #2: why `submit()` is NOT called at propose (or
approve) time

The task's SPEC explicitly allowed "(or document why it doesn't)" for
calling `resource_governor.py`'s `submit()` to mint the child UMR. Verified
live, before deciding:

- `submit()` unconditionally writes an accepted submission's `umr_tasks` row
  with `status='queued'` -- the live dispatch-pickup signal.
- `next_queued_task()` selects any `status='queued'` row, and this server's
  real `veridian-cron-dispatch-tick.timer` ticks every 30 seconds (per
  `_perform_spawn()`'s own docstring).
- For `task_kind='veridian_task_create'`, `_perform_spawn()` then runs
  `veridian-task.py create --title ... --prompt ...` for real -- spawning a
  genuine new AI worker to actually implement the prompt.
- Confirmed live against `/opt/veridian/ai-os/memory/superboss-register.sqlite`
  while building this task: this very task's own parent UMR,
  UMR-20260806-034750-05cf, IS exactly such a row (`task_kind=
  'veridian_task_create'`, `status='running'`, `source_trigger=
  'owner_dispatch_gateway'`) -- the literal mechanism that dispatched the
  agent doing this work.

Calling `submit()` from `propose_child_umr_action()` (or automatically
inside `pm_decide_on_proposal()` on `approve`) would, within ~30 real
seconds and with zero further gating, spawn a real AI worker to start
implementing the proposed action -- defeating the one thing this feature
exists to guarantee, and (concretely, for this very task's own required
demo round-trip) would leave a real, unwanted, spurious `queued` row against
the live production database. `umr_tasks.status` also has a fixed CHECK
constraint with no "proposed, awaiting decision" pre-queue state, and
widening it on a shared, actively-dispatched, ~7000-row live queue table is
schema surgery this task does not ask for and this change does not attempt.

`propose_child_umr_action()` therefore mints `child_umr_id` using the exact
same real ID-generation convention `upsert_umr_task()` itself uses
internally (`_new_id("UMR")`) -- genuinely indistinguishable in format from
any other real UMR -- but never writes a live `umr_tasks` row for it. The
actual decision to spend real dispatch/execution resources stays a separate,
deliberate action outside this feature's automatic control.

## What was built

- `superboss-register.py`: `pm_child_umr_proposals` table (idempotent
  `_ensure_pm_child_umr_proposals_table()`, wired into `_migrate_schema()`),
  `propose_child_umr_action()`, `pm_decide_on_proposal()`,
  `record_proposal_completion()`, `get_open_child_umr_proposals()`, three
  `cmd_*` CLI wrappers + argparse subcommands (`propose-child-umr-action`,
  `pm-decide-on-proposal`, `record-proposal-completion`) -- same
  `_connect()`/`_write_lock()`/caller-owns-commit/idempotent-`_ensure_*`
  convention as `insert_pm_decision_pending()`/`resolve_pm_decision_pending()`/
  `record_ocid_master_standard_audit_event()`.
- `generate_pm_report_v3.py`: new "8. PM CHILD-UMR PROPOSALS AWAITING
  DECISION" section, read-only via `get_open_child_umr_proposals()`, same
  pattern as the existing "7. PM DECISION REQUIRED" section
  (`get_pm_decisions_pending()`).
- `tests/test_pm_child_umr_proposals.py`: 15 real tests -- schema pin, full
  propose/approve/complete round-trip, redirect-then-reapprove path, hold
  path, idempotency guards, unknown-id guards, the open-proposals report
  filter, CLI end-to-end, and an explicit regression test proving
  `propose_child_umr_action()` never writes a live `umr_tasks` row.
- `test_generate_pm_report_v3.py`: extended the existing synthetic fixture
  (`_build_fake_db`/`_make_fake_sbr_module`) with `pm_child_umr_proposals`
  support so the pre-existing end-to-end smoke test still passes with the
  new section wired in.

## Real test results

- `python3 tests/test_pm_child_umr_proposals.py` -- 15/15 passed.
- `python3 tests/test_pm_decisions_pending.py` -- 8/8 passed (unaffected).
- `python3 -m pytest test_generate_pm_report_v3.py -q` -- 15 passed.
- `python3 -m pytest test_*.py tests/ -q` -- 192 passed, 4 errors (all in
  `test_ocid063_handoff_envelope.py`, a pre-existing missing-`vt`-fixture
  issue in a file this branch never touches -- confirmed via `git diff
  --stat main` showing only `generate_pm_report_v3.py`,
  `superboss-register.py`, `test_generate_pm_report_v3.py` changed, plus the
  new `tests/test_pm_child_umr_proposals.py`).
- `python3 -m py_compile superboss-register.py generate_pm_report_v3.py` --
  clean.

## Real demo round-trip (after merge)

Ran a genuine `propose_child_umr_action()` -> `pm_decide_on_proposal()`
(approve) -> `record_proposal_completion()` cycle against the real
production database, then ran `generate_pm_report_v3.py` for real and
captured its output showing the new section 8 with this round-trip's data.
Full detail (proposal content, whether it was a real finding or a clearly-
labeled synthetic demo, and the captured report output) recorded in the
task's own final report, not duplicated here.
