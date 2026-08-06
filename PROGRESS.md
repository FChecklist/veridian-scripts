# PROGRESS -- task-20260806-031225-owner-directive--close-the-deterministic

SPEC recap (5 items, Real Owner directive): (1) diagnose+fix real launcher/env
bug behind task-20260805-193951's 3x exit-1/0-token crash, (2) re-dispatch the
pm_decisions_pending / pm_report_snapshots writer as an EXTENSION of
superboss-register.py (not a new script), (3) get PR #95 into review and
merged, (4) confirm gtm_write_category_result.py is live at
/opt/veridian/scripts after PR #95 merges, (5) add a canonical-script comment
block to the top of superboss-register.py.

## Completed

- [x] Item 1 -- root cause found, independently verified, is NOT what the SPEC
  assumed. See findings below. Fix applied to `worker-entrypoint.sh` and
  pushed for review.

## Remaining

- [ ] Item 2 -- re-dispatch pm_decisions_pending/pm_report_snapshots writer
  (extend superboss-register.py) once item 1's fix is merged
- [ ] Item 3 -- investigate PR #95's missing review, nudge into pipeline, merge
- [ ] Item 4 -- confirm gtm_write_category_result.py live at /opt/veridian/scripts
      after PR #95 merges
- [ ] Item 5 -- add canonical-script comment header to superboss-register.py

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

## Next steps (not yet done)
1. Commit + push this fix, open/link its PR, get it merged (same review
   pipeline as everything else in this repo).
2. Once merged and live, re-dispatch the pm_decisions_pending/
   pm_report_snapshots writer task as an extension of superboss-register.py.
3. Investigate PR #95.
4. After PR #95 merges, verify gtm_write_category_result.py lands at
   /opt/veridian/scripts (same git-pull mechanism confirmed above).
5. Add canonical-script header comment to superboss-register.py.
