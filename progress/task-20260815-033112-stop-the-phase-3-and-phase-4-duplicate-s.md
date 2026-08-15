# task-20260815-033112-stop-the-phase-3-and-phase-4-duplicate-s

Governing UMR cited by SPEC: UMR-20260806-071025-1d28

## Verdict: SPEC premise is FALSE. No destructive action taken. See evidence below.

This matches the documented recurring pattern in
`veridian-task-prompt-false-premise-pattern` (memory): an urgent SPEC with
confident, specific, "real"/"live right now" claims that do not match live
state. Every one of the SPEC's numeric claims checks out against the
database **verbatim** — but as evidence from **2026-08-06, nine days ago**,
not from today (2026-08-15) as presented. Additionally, the exact fix the
SPEC asks for in Step 3 was already implemented and merged on 2026-08-06,
under a *child UMR of this same governing UMR*.

## Completed

- [x] Step 1 — independently re-verified the zombie claim for both cited UMRs, via `resource_governor.py --query-umr --umr-id` (canonical tool, not raw SQL):
  - `UMR-20260730-041943-093a` (PHASE-3-BUILD-CALC): status = **`killed`**, `ts_completed = 2026-08-06T11:43:41Z`. **NOT** "running since 2026-07-30" as the SPEC claims — it has been terminal for 9 days.
  - `UMR-20260729-112414-3269` (PHASE-4-BUILD-WORKFLOW): status = **`completed`**, `ts_completed = 2026-08-06T11:17:18Z`. **NOT** "queued since 2026-07-29" as the SPEC claims — it has been terminal for 9 days.
  - `ps -eo pid,cmd | grep -i "PHASE-3-BUILD-CALC\|PHASE-4-BUILD-WORKFLOW"` → no match (exit 1). Confirms no live process, consistent with both rows already being terminal.
  - `systemctl list-units --all | grep -i "phase-3\|phase-4"` → no match (exit 1). Confirms no systemd unit, consistent with both rows already being terminal.
  - No `task.yaml` exists for either identity under `/opt/veridian/ai-os/tasks/` (only this task's own `task.yaml` exists).
  - **Conclusion: neither row has genuinely live work behind it, but neither is "already running"/"already queued" either — both are already closed. There is nothing to close.**

- [x] Cross-checked the SPEC's "volume of waste" claims against `/opt/veridian/ai-os/memory/superboss-register.sqlite` (the real live DB, resolved via `resource_governor.resolve_superboss_db_path()` — **not** the empty stub at `scripts/superboss-register.sqlite`):
  - `rejected_duplicate` counts by hour on 2026-08-06: hr00=126, hr01=126, hr02=126, hr03=126, hr04=127, hr07=128, hr08=105, hr09=195 (partial-day query, SPEC's "83 so far in hour 09" is a stale snapshot mid-hour). **Matches the SPEC's cited rates exactly.**
  - Burst window 2026-08-06 09:23:33–09:24:08: 18 rejected_duplicate rows (SPEC says "20 in 35 seconds" — same window, close count, consistent with a stale snapshot taken mid-count).
  - Rows at 2026-08-06T09:26:10.593701Z (`UMR-20260806-092610-5028`, PHASE-3-BUILD-CALC) and 2026-08-06T09:26:11.269111Z (`UMR-20260806-092611-cecb`, PHASE-4-BUILD-WORKFLOW) — **exact match** to the SPEC's "09:26:10 and 09:26:11" rows.
  - **All of these real rows are dated 2026-08-06 — nine days before today (2026-08-15). The SPEC presents them as "live right now" / "while this evidence was being gathered." They are not live right now.**
  - Max `ts_submitted` for PHASE-3-BUILD-CALC across the whole table: `2026-08-06T09:54:10Z`. Max for PHASE-4-BUILD-WORKFLOW: `2026-08-06T10:17:52Z`. **Zero rows for either identity on any date after 2026-08-06.**
  - Overall `rejected_duplicate` counts by day, all identities: 2026-08-06=1197, 2026-08-05=3102, 2026-08-04=2047, 2026-08-03=4, 2026-08-02=21, then **zero every day since 2026-08-06** (checked through 2026-08-15). The loop is dormant, not live.

- [x] Root-cause check (would have been Step 3): the exact fix the SPEC asks for — "make it stop retrying blindly forever, back off and surface a real blocker" — **already exists and is deployed on `main`**:
  - `scripts/directive_engine.py`, `process_one()` (~line 311 onward): a durable retry-once gate. On a task_identity's first terminal outcome (`failed`/`rejected_duplicate`/`killed`) it allows exactly one resubmission and records that fact in `DIRECTIVE_RETRY_STATE_FILE` (`/opt/veridian/ai-os/tasks/DIRECTIVE_RETRY_STATE.json`, owned exclusively by this module). On the *next* terminal outcome it calls `note_needs_review()` instead of resubmitting — surfacing a real blocker in `PENDING_OWNER_REVIEW.md` rather than retrying forever.
  - Commits: `b0a2516` "fix(dispatch-queue): close directive_engine.py retry-storm poison-pill + add max-queued-age safeguard (UMR-20260806-090229-f2a7)" (2026-08-06T09:24:54Z) and `68e0b94` "fix: address real Superboss review finding -- retry-once signal must not share umr_tasks.reason with dispatch_one()" (2026-08-06T09:41:11Z), merged via PR #153. Both confirmed `git merge-base --is-ancestor <sha> HEAD` → **YES**, present on current `main`/HEAD (`12121ee`).
  - `UMR-20260806-090229-f2a7` is itself a **child of UMR-20260806-071025-1d28** — the exact governing UMR this SPEC cites.
  - Live proof the fix worked: `/opt/veridian/ai-os/tasks/DIRECTIVE_RETRY_STATE.json` contains exactly `{"PHASE-3-BUILD-CALC": {"umr_id": "UMR-20260806-095410-713b", "ts": "2026-08-06T10:17:50Z"}, "PHASE-4-BUILD-WORKFLOW": {"umr_id": "UMR-20260806-095411-dab2", "ts": "2026-08-06T10:17:51Z"}}` — both identities were caught by the retry-once gate at that timestamp, which is why no further resubmissions of either happened after 2026-08-06T10:17:52Z (confirmed above).
  - Regression coverage: `tests/test_directive_engine_retry_gate.py` (round 1 + round 2 bug narrative documented in its own module docstring) already covers this exact scenario, using an isolated temp DB/state file, never the live DB.
  - There is also a separate, older, already-dormant symptom: `PENDING_OWNER_REVIEW.md` has 1920 pre-existing PHASE-3-BUILD-CALC/PHASE-4-BUILD-WORKFLOW entries from an earlier, unrelated `run_check_duplicate_battery()` code path, all dated 2026-07-29T11:49Z–2026-07-30T04:52Z. Nothing for either identity has been appended there since 2026-07-30 either — also dormant, not a currently-active problem.

- [x] Step 4 — checked for other task identities stuck in the same pattern: queried for any `task_identity` resubmitted ≥3× in the last 5 days (2026-08-10 through 2026-08-15) and for any `rejected_duplicate` row at all in that window, across **all** identities. **Zero results either way.** No other identity is currently looping.

- [x] Verified this is a recurring meta-pattern specifically for this governing UMR: `git log` shows prior "docs: verified SPEC premise false" commits already made against `UMR-20260806-071025-1d28` (`970b7c5`, `ba7ec64`, `3038642`, `60c7187`) — this SPEC is at least the 5th time a stale/false SPEC has been dispatched citing this same governing UMR.

## Remaining

- [x] SPEC steps 2/3/5/6 (close the rows, patch the code, wait-and-recount, open a fix PR) do not apply: there is nothing genuinely broken right now to close, patch, or re-measure. Per the hard limits ("do not close any row that still has genuinely live work behind it," "do not mark anything completed that did not really complete") and per hard rule 2 (zero duplication), redoing an already-merged fix or force-closing already-terminal rows would itself be the exact waste this SPEC is nominally trying to prevent.
- [x] Recorded completion evidence via `agent_work_briefing.py record-completion --umr-id UMR-20260806-092722-e526` (this doc-only finding, `AGENT-20260806-092722-e526`).
- [x] Committed + pushed this progress file (`c38589f`); opened PR documenting the false-premise finding (no code change — none is warranted): **https://github.com/FChecklist/veridian-scripts/pull/402**

None remaining.

## Hard-limit compliance

- Did not close/mark-terminal either UMR row (both already terminal; `mark-umr-terminal` was never invoked).
- Did not touch `dispatch_core.py` (frozen, per [[veridian-dispatch-core-py-frozen-stop-work-order]] memory) or `directive_engine.py` (already fixed 9 days ago — re-touching it would be pure duplication, the thing hard rule 2 forbids).
- Did not rotate any credential, delete/archive any repository, or run any raw-SQL write against `umr_tasks`.
- All DB reads used the canonical CLI (`resource_governor.py --query-umr`) or a read-only `sqlite3.connect()` against the real resolved path for cross-checks the CLI doesn't directly expose (day/hour aggregation) — no writes were made outside the canonical tools.
