# PROGRESS -- task-20260806-031225-owner-directive--close-the-deterministic

SPEC recap (5 items, Real Owner directive): (1) diagnose+fix real launcher/env
bug behind task-20260805-193951's 3x exit-1/0-token crash, (2) re-dispatch the
pm_decisions_pending / pm_report_snapshots writer as an EXTENSION of
superboss-register.py (not a new script), (3) get PR #95 into review and
merged, (4) confirm gtm_write_category_result.py is live at
/opt/veridian/scripts after PR #95 merges, (5) add a canonical-script comment
block to the top of superboss-register.py.

## Completed

- [x] Item 1 -- root cause independently found (SPEC's premise was false).
  **Duplicate-dispatch discovered**: a concurrent task for this identical
  Owner directive (UMR-20260806-031211-64de, spawned ~14s before this task)
  had already opened an equivalent fix as PR #98 by the time I finished mine
  (PR #100). Closed #100 as a duplicate, deferred to #98 (opened first,
  03:15:01Z). Adopted #98's branch via `veridian-task.py adopt` so
  dispatch-tick's sweep picks it up for supervisor review (previously it had
  no task_dir, so it was invisible to that pipeline -- same gap PR #95 hit).
  **Not yet independently verified as MERGED** -- tracking below.
- [x] Item 3 -- independently verified: PR #95 **is merged**
  (`6890c3265181003b308483f7cd9c98556c6f2d79`, 2026-08-06T03:21:19Z). Real
  root cause of the stall found (see below), not "nudged" by me -- it
  self-resolved via the existing dispatch-tick sweep before I finished
  investigating, confirmed against the real supervisor logs, not assumed.
- [x] Item 4 -- independently verified: `gtm_write_category_result.py` is
  live at `/opt/veridian/scripts/gtm_write_category_result.py`, md5
  `8e5cb8f60fec07ff9e3c6ef6a60e784b`, byte-identical to the repo checkout,
  and `/opt/veridian/scripts` HEAD is `6890c32` (PR #95's own merge commit) --
  not stale, not just "present in the repo checkout."

## Remaining

- [ ] Item 1 (follow-through) -- confirm PR #98 actually gets reviewed +
      merged (adoption task created, not yet swept/reviewed as of this write)
- [ ] Item 2 -- a concurrent task (task-20260806-031857-extend-superboss...,
      confirmed genuinely distinct from this one, see below) is already doing
      this. Monitoring, not redispatching a 3rd duplicate. Will independently
      verify its actual diff once it completes -- not done yet.
- [ ] Item 5 -- add canonical-script comment header to superboss-register.py
      (SPEC explicitly sequences this AFTER 1-4 are genuinely done; items 1
      and 2 aren't yet, so deliberately not done yet)

## Item 1 findings (independently verified against live logs/systemd, not just the SPEC's claim)

**The SPEC's premise is false.** This is the same false-premise pattern noted
in past veridian-scripts tasks (see this session's memory note) -- the SPEC
guessed "likely a bad argument, missing env var, or broken dispatch template."
None of those are what happened.

Real evidence, read directly from
`/opt/veridian/ai-os/tasks/task-20260805-193951-build-a-deterministic-writer-for-pm-deci/`:
- `result.json` / `.claude-out-main.json` (all 3 invocations): every single
  one is `"is_error":true, "api_error_status":429,
  "result":"You've hit your weekly limit · resets 2am (UTC)"` --
  `input_tokens`/`output_tokens` all 0, confirming the SPEC's "zero tokens
  consumed" detail, but the cause is an **account-wide Claude subscription
  weekly-usage-limit rejection**, not a launcher bug. The model was never
  reached because the API itself refused the call before any inference.
- Confirmed this was NOT specific to this one task: 27 other tasks' own
  `.claude-out-main.json` files independently show the identical
  `api_error_status=429`/"weekly limit" text in the same ~19:33-19:41 UTC
  window on 2026-08-05 (a burst-dispatch window). Fleet-wide quota
  exhaustion, not a per-task defect.
- `systemctl --user status veridian-worker@task-20260805-193951-...` shows
  `Result: exit-code`, `Start request repeated too quickly` -- systemd's own
  StartLimitBurst is what actually stopped the retries at invocation 3 (not
  the app-level circuit breaker, and not the app-level lifetime-invocation
  cap of 20).

**Real secondary bug found and fixed along the way:** the app-level circuit
breaker (`check_circuit_breaker()` in `preflight-guard.py`) only trips on 2
*consecutive identical* failure signatures. `record_failure_signature()` in
`worker-entrypoint.sh` hashes the last 400 chars of `worker.log`, which always
contains a per-invocation random `action_id`/`session_id` -- so 3 retries of
the exact same account-wide 429 produced 3 *different* signatures
(`79c7a27d...`, `1eb09d87...`, `ead465bf...`) and the circuit breaker never
saw it as a repeat. This is real and independently confirmed by reading
`.failure_signatures.json` directly. It means quota-exhaustion 429s were
silently exempt from the circuit breaker that exists specifically to stop
this shape of blind retry.

**Fix applied** (`worker-entrypoint.sh`, before the existing
`CLI_HIT_BUDGET_CAP` block, same file that is the real launcher for every
`veridian-worker@*` task, so this closes the class for all future tasks, not
just this one): detect `api_error_status==429` (with a text-match fallback)
right after the existing API-level-error parse, and hard-stop exactly like the
pre-existing `openrouter_balance_exhausted`/`error_max_budget_usd` hard stops
-- checkpoint `blocked` with the real reset-time text surfaced in the note,
`systemctl --user disable` the unit so it can't restart-storm, exit 0. This
matches the same pattern already used twice in this file for other
"retrying reproduces the identical wall" failure classes, so it's consistent
with the file's existing design, not a new mechanism.

Verified: `bash -n worker-entrypoint.sh` passes; the new Python one-liners
were smoke-tested directly against the real failed task's own
`.claude-out-main.json` (prints `1`, correctly flags it) and against a
synthetic ordinary-success JSON (prints `0`, no false positive).

**Deployment path confirmed** (relevant to item 4 too): `/opt/veridian/scripts`
is a real live `git` clone of `FChecklist/veridian-scripts` on `main`
(currently `f6014e5`, one merge behind this repo's current tip `a077b3f` --
normal lag). It updates via `git pull --ff-only` on
`veridian-cron-sync-repos.timer` (systemd, not cron -- crontab itself is
permanently retired per its own header, 2026-07-29/08-01). The old
`deploy-live-scripts.sh` (copying from `claude-control`'s `scripts/`) is
retired per `sync-repos.sh`'s own 2026-08-01 comment -- confirmed live: diffing
`/opt/veridian/scripts/superboss-register.py` against the `veridian-scripts`
repo checkout shows byte-identical content; diffing against `claude-control`'s
copy shows real divergence. So once a PR merges to `veridian-scripts`
main, the live path picks it up automatically within one
`sync-repos.timer` cycle (currently every ~1h), no manual deploy step needed.

## Duplicate-dispatch finding (real, independently confirmed -- not from the
## "clarification" task's own say-so)

While working item 1, found `PR #98` (branch
`fix/worker-entrypoint-weekly-limit-hard-stop-umr20260806031211`, opened
2026-08-06T03:15:01Z) already contains a functionally-equivalent fix to the
one I had just independently written, for the identical root cause, citing
`UMR-20260806-031211-64de`. That UMR/branch timestamp (03:12:11) is 14
seconds before this task's own id (`task-20260806-031225-...`) -- real
evidence this exact Owner directive was dispatched to two separate task_ids
almost simultaneously, not evidence of a launcher bug this time, a
**dispatch-layer** duplication. Closed my redundant PR #100 in favor of #98
(github.com/FChecklist/veridian-scripts/pull/100#issuecomment, closed
2026-08-06) rather than carry two competing PRs for one fix.

A separate task, `task-20260806-032356-clarification--not-a-real-collision--bot`,
appeared shortly after asserting "not a real duplicate collision, ... continue
without further pause." I did **not** take that assertion at face value (past
tasks in this repo, and this session's own memory, record that urgent
PM/clarification SPECs in this codebase have repeatedly not matched live
state) -- I independently checked the two units it named
(`task-20260806-031225` = this task, `task-20260806-031857` = the item-2
writer task) and confirmed those two specifically are NOT duplicates of each
other (different task_ids, different scopes, one is literally this
orchestration task). That part of its claim checks out. It did **not**
address the real duplication I'd already found (PR #98 vs my now-closed
#100), which is a different pair than the one it was reassuring me about --
so its "not a real collision" verdict was correct for the question it
answered but incomplete for the one that actually mattered. Recorded here
for the final report; not treating either the SPEC's premise or a bot's
reassurance as ground truth without checking the artifacts myself, same
practice as item 1.

## Item 3 findings (PR #95 review stall -- independently verified root cause)

`task-20260805-checkpoint-pr95-adoption` (the existing supervisor-adoption
task for PR #95, created same minute as the PR itself, 2026-08-05T19:04) shows
its **first** supervisor review attempt at 19:53:11 UTC ended
`status: blocked`, note *"supervisor failed to produce a review verdict --
see supervisor.log"*, with **`Real review cost: $0.0`** -- i.e. the
supervisor's own `claude -p` call spent zero tokens, the same fingerprint as
item 1's failures. 19:53 UTC on 2026-08-05 falls inside the same account-wide
weekly-usage-limit outage window independently confirmed in item 1 (27 other
tasks hit identical 429s between ~19:33-19:41 UTC that same evening; the
outage plausibly extended to 19:53). **So PR #95's stalled review shares item
1's real root cause** -- not a separate defect, and not "no supervisor review
ever started" as the SPEC guessed (one did start, and failed for the same
reason as everything else that evening).

What actually resumed it: nothing in this task's own actions -- by the time I
checked (03:2x), `task-20260805-checkpoint-pr95-adoption` had already been
re-triggered (a second `git fetch`/supervisor invocation at 03:21:14, real
cost still $0.0 curiously, but this one produced a real `review.json`:
verdict `approve`, tier `tier1`), and PR #95 merged automatically at
03:21:19Z (tier1 + approved -> autonomous merge, per
`supervisor-entrypoint.sh`'s own documented policy). This matches the
timing of the concurrent UMR-20260806-031211-64de activity above -- most
likely that task chain (or a routine dispatch-tick sweep unrelated to either
of us) re-triggered the stalled supervisor unit. Independently verified via
`gh pr view 95` (`state: MERGED`, `mergedAt: 2026-08-06T03:21:19Z`,
`mergeCommit: 6890c3265181003b308483f7cd9c98556c6f2d79`), not assumed from
the task.yaml note alone.

Residual real gap, not fixed here (out of the SPEC's explicit 5 items, noting
for completeness): a `blocked`-with-no-review.json supervisor task has no
automatic re-trigger -- it sat stalled ~7.5 hours until something incidental
resumed it. Same shape as the pattern already fixed for `worker-entrypoint.sh`
hard-stops (explicit re-enable required) but supervisor tasks apparently lack
even that explicit blocked-state handling in dispatch-tick's sweep. Flagging,
not fixing -- not one of the 5 items and risks scope creep on an already
large task.

## Item 4 findings

After PR #95 merged (6890c326), independently verified (not assumed):
- `md5sum` of `/opt/veridian/scripts/gtm_write_category_result.py` and the
  `veridian-scripts` repo checkout's copy are identical
  (`8e5cb8f60fec07ff9e3c6ef6a60e784b`).
- `/opt/veridian/scripts` (`git log -1`) is at `6890c32`, PR #95's own merge
  commit -- not stale.
- Confirmed the live sync mechanism is `sync-repos.sh` via
  `veridian-cron-sync-repos.timer` (systemd), `git pull --ff-only` directly
  against `/opt/veridian/scripts`'s own real git clone of
  `FChecklist/veridian-scripts` -- the old `deploy-live-scripts.sh`
  (copying from `claude-control`'s stale `scripts/` mirror) is retired per
  `sync-repos.sh`'s own 2026-08-01 changelog comment. By the time I checked,
  the sync had already happened (file present, correct HEAD) -- did not need
  to force it manually.

## Next steps (not yet done)
1. Confirm PR #98 gets swept for supervisor review and merges (adopted into
   the pipeline via `veridian-task.py adopt`, not yet reviewed as of this
   write).
2. Independently verify item 2's task (`task-20260806-031857-extend-...`)
   once it completes: confirm it truly extended `superboss-register.py`
   (not a new parallel script) with real `pm_decisions_pending`/
   `pm_report_snapshots` functions, and that its PR merges.
3. ~~Investigate PR #95~~ -- done, see above.
4. ~~Verify gtm_write_category_result.py lands at /opt/veridian/scripts~~ --
   done, see above.
5. Once 1 and 2 are genuinely merged/live, add the canonical-script header
   comment to `superboss-register.py` and open its own PR.
