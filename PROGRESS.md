# PROGRESS -- re-dispatch verification, relay dead-zone fix (UMR-20260806-115423-500d)

SPEC (task-20260806-155951, "no-relay path" re-dispatch): re-asserted the fix for
`dispatch-owner-task.sh` never marking a real row dispatched based only on an attempted tmux
relay ("the relay becomes a real best effort notification only and must never remove a row from
the real queued pool, the only real legitimate transition out of queued is dispatch-tick.py real
mechanical pickup"), on the premise that the prior attempt (UMR-20260806-115423-500d) was
"confirmed stranded in the real dead zone, no real task directory was ever created for it," plus a
new ask: "Update SKILL.md to state plainly a printed RELAYED message is never proof of delivery."

Per this repo's own standing lesson (memory cases #10/#15/#16/#17/#19 this same day: urgent-sounding
re-dispatch SPECs have repeatedly cited stale/already-resolved state), every concrete claim was
independently re-checked against live state before touching anything.

## Completed

- [x] Read `dispatch-owner-task.sh` on `origin/main` directly: the real fix is **already merged**
      (PR #166, merge commit `38650b35b24954ca9277029798c579e6baf9c658`, `mergedAt=2026-08-06T13:25:57Z`
      -- source commit `8df34d5`, UMR-20260806-115423-500d). Confirmed the relay block does exactly
      what this SPEC asks: neither branch (`tmux has-session` true or false) writes `status`,
      `ts_dispatched`, or `ts_completed` any more -- both call the new
      `mark-umr-relay-attempted` CLI subcommand, which writes only `ts_relay_attempted` /
      `relay_outcome` / `relay_detail`. A row stays `status='queued'` after either branch,
      unconditionally eligible for `resource_governor.py`'s `next_queued_task()`
      (`SELECT * FROM umr_tasks WHERE status='queued'`, called from `dispatch-tick.py`'s own tick)
      -- the one real, tmux-independent mechanical pickup path. `mark-umr-dispatched` still exists
      as a CLI command for a genuinely different future non-interactive-confirmation channel; it is
      simply no longer called from this script.
- [x] Confirmed the live production script now matches `origin/main` byte-for-byte
      (`/opt/veridian/scripts/dispatch-owner-task.sh`, 237 lines) -- it had briefly lagged behind
      during this task's own investigation window (a concurrent session's
      `reconcile_live_scripts_dir_to_origin_main` action, `UMR-20260806-160208-2482`, closed that
      gap independently of this task; not this task's own doing, noted for the record only).
      `dispatch-tick.py` and `resource_governor.py` on the live server are already byte-identical
      to `origin/main` (diff empty both ways).
- [x] Checked `UMR-20260806-115423-500d`'s own row directly in `umr_tasks` (never grep/find the
      filesystem for a UMR id -- it is a live DB row): `status='completed'`,
      `ts_completed='2026-08-06T14:48:28.927600+00:00'`. It was **not** left stranded -- a prior
      session already reconciled it hours before this task was dispatched, with a reason field
      citing this exact PR #166 merge evidence and explaining the original "dead zone" (relay
      marked it `dispatched` on attempt; `dispatch-tick.py` only ever polls `status='queued'`;
      the real underlying work had actually completed hours earlier via the interactive/tmux
      session that authored PR #166, which itself never called `mark-umr-terminal`). Deliberately
      not reset to `queued` by that prior session, correctly -- doing so now would trigger a
      wasteful duplicate re-dispatch of already-merged, already-deployed work.
- [x] Checked "no real task directory was ever created for it": confirmed true and understood --
      PR #166's actual authoring happened directly inside the live interactive tmux session (the
      relay's own destination), which never spawns a discrete `ai-os/tasks/task-*/` directory the
      way a `veridian-worker@*.service`-backed dispatch does. That is a real, separate, already-
      documented property of the interactive-session path (not a defect this SPEC's fix could or
      should change) -- not the root cause of the original stuck-`dispatched` symptom, which was
      the relay's own status write, already fixed per the point above.
- [x] Checked the new ask, "Update SKILL.md to state plainly a printed RELAYED message is never
      proof of delivery" -- **false premise, no such file exists to update.** `git log --all
      --oneline -- '**/SKILL.md'` in this repo returns nothing; no `SKILL.md` has ever existed in
      `veridian-scripts` (independently re-confirmed here; matches this same repo's own
      `PM_CYCLE_PRECHECK_VERIFICATION_2026-08-06.md` finding #4 for an unrelated OCID-068 SPEC the
      same day). Also checked every other repo checkout under `/opt/veridian/repos/*` (`git ls-files
      | grep -i skill.md`, every `.git` dir) and this live `/opt/veridian/scripts` checkout's own
      `.claude/` tree: zero matches anywhere on this server. The only mention of `SKILL.md` in this
      codebase at all is a comment in `generate_pm_report_v3.py` naming a Windows-laptop-only path
      (`C:\Users\Dell\.claude\scheduled-tasks\veridian-server-sentinel\SKILL.md`) -- explicitly out
      of scope for a server-side session per the Owner's 2026-07-31 directive (AGENTS.md Contact
      section: "server ... work independently ... laptop can be closed"). No file was fabricated to
      satisfy this ask.
- [x] The substantive content this ask wanted written down already exists in the one real,
      canonical place for this fix: `dispatch-owner-task.sh`'s own header comment (the
      `UMR-20260806-115423-500d` block) states plainly that "a successful `tmux send-keys` proves
      only that keystrokes were written into a pane -- NEVER that a live process actually read and
      acted on them," and the script's own runtime output on a successful relay literally prints
      "(best-effort courtesy notification ONLY -- send-keys returning 0 proves the keystrokes were
      written into the pane, NOT that any live process read or acted on them; this is never proof
      of delivery)" -- i.e. this exact sentence, at the two places (source comment + printed
      output) that anyone or anything actually reading a `RELAYED` message would see it. No second,
      currently-nonexistent doc file is needed to carry the same fact.

## Remaining

- [ ] None from this task's own scope. The code fix was already correct and merged before this
      task was dispatched; the previously-stuck UMR row was already reconciled; the "no task
      directory" observation is a real but already-understood property of the interactive-session
      relay path, not a new defect; the `SKILL.md` ask targets a file that has never existed
      anywhere on this server. No merge, no DB write beyond the log-action call below, no fix
      re-implemented (would have been a no-op duplicate of PR #166).

Logged via `superboss-register.py log-action` (never raw SQL): `ACT-20260806-161013-9ee4`.
