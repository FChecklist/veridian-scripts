# Duplicate-Worker Verification — 2026-08-05T16:52-17:10Z

**Relates to:** `UMR-20260804-170055-a069`, `UMR-20260805-025349-a6b8`
**SPEC claim:** three named task directories "actively running and consuming real CPU and
RAM right now," redoing already-merged work, contributing to a load-average spike of 18-29.

## Verdict: the three named tasks are NOT currently running and are NOT genuine duplicates in progress. Nothing was stopped, because there was nothing live to stop.

## Per-task verification (live process table + `.task.lock` + `task.yaml` `status:` + PROGRESS.md, checked directly, not inferred)

1. **`task-20260805-114126-pm-decision--reconcile-ocid-068-umr-book`** (SPEC's own spelling
   omits the `--` after `decision`; the real directory has it — verified by `rg` search across
   `ai-os/tasks/*/prompt.txt` for the cited UMR IDs, which is how the real directory was found).
   `status: completed`. No process holds `.task.lock` (`fuser` — empty). Its own PROGRESS.md
   shows it independently re-ran `superboss-register.py reconcile-umr-status --umr-id
   UMR-20260804-170055-a069`, got `is_stale: false` (the row was already `status=completed`,
   `ts_completed=2026-08-05T02:45:07Z`), and performed **zero** DB writes and zero redeploys —
   the module's own `--apply` path only writes when stale. Finished by `11:50Z`.
2. **`task-20260805-114207-build-real-deterministic-pre-merge-gate`**. `status: blocked`. No
   process holds `.task.lock`. Its PROGRESS.md shows it found the SEC-07 gate already merged
   (PR #933, commit `119577a0`), independently re-ran the test suite to confirm it still works
   (12/12), did **not** rebuild it, and instead made one small non-duplicate governance-doc
   accuracy fix (four files that still described SEC-07 as unenforced) — already committed and
   pushed for independent review. Last activity `12:20Z`, i.e. **4.5+ hours** before this SPEC's
   "last several minutes" / "right now" claim.
3. **`task-20260805-114214-fix-metadata-index-coverage-check-failur`**. `status: blocked`. No
   process holds `.task.lock`. Its PROGRESS.md shows it found PR #932 and PR #933 both already
   merged (`0c2ab78c` at `03:20:24Z`, `88bd2e76` at `03:24:31Z`) and the actual Metadata Index
   Coverage gap already closed by a third PR, #934 (`854a29c0`), confirmed via
   `git merge-base --is-ancestor` against `origin/main`. It made **zero commits** — a correct
   no-op, self-reported in its own PROGRESS.md as "Duplicate dispatch, same class as
   `[[veridian-task-prompt-false-premise-pattern]]`." Last activity `12:13Z`.

Cross-check against the live process table (`ps -eo pid,etimes,cmd`) at `16:52Z`, `17:02Z`, and
again after killing my own stray background greps: no process, worker, or systemd unit
referencing `114126`, `114207`, or `114214` anywhere. `systemctl --user list-units --all` has no
unit for any of the three. Load average sampled twice: `2.22/2.43/3.88` at `16:52Z`, then
`14.42/12.19/8.21` at `17:02Z` — a real spike did occur in that window, but the live process
table at spike time still shows no process tied to any of the three named tasks; the load is
attributable to the platform's much larger concurrent task volume (`PM_TRIAGE_ALERTS.md`'s
`16:33Z` entry independently reports 604 tasks stuck >30min and 62 with fresh audit-reject
verdicts — a real, pre-existing, much larger backlog outside this task's scope), not to these
three.

**Conclusion: none of the three needed stopping.** All three had already reached a terminal
state (`completed`/`blocked`) 4.5-5+ hours before this SPEC was dispatched, and — this is the
important part — each one's own worker independently re-verified live state before acting and
correctly declined to redo already-merged work. The zero-duplication rule held; the actual cost
was bounded to each worker's own dispatch-to-verification run (single-digit minutes to ~40min
each per their `task.yaml` timestamps), not sustained duplicate concurrency.

## Root-cause investigation: why did already-completed work get re-dispatched at all?

Two candidate root causes were checked:

1. **The already-known-and-fixed DB-path bookkeeping bug**
   (`resolve_superboss_db_path()` in `superboss-register.py`, canonical chokepoint added in
   commit `5130153` at `2026-08-04T18:12:32Z`, deliberately fails closed against the confusable
   zero-byte decoy `ai-os/superboss-register.sqlite`). **Ruled out**: that fix landed ~17 hours
   before these three tasks were dispatched (`11:41-11:42Z` on `08-05`), and task 114126's own
   live re-run of `reconcile-umr-status` confirms the DB row was already correctly
   `status=completed` at dispatch time — the DB itself was not lagging.
2. **Stale premise baked into the dispatch prompt itself.** This is the real cause found here.
   Task 114214's `prompt.txt` (dispatched `11:42Z`) states PR #932 and PR #933 as *currently*
   blocked by a failing Metadata Index Coverage Check — a state that was already ~8 hours stale
   at dispatch time (both merged `03:20-03:25Z` that same morning). The dispatch pipeline
   (`dispatch-tick.py`'s PM-triage-alert path, `PM_TRIAGE_ALERTS.md` → PM-decision Claude
   invocation → new task prompt, `PM_TRIAGE_COOLDOWN_MINUTES=60` default) mints a fresh task
   prompt from whatever GH/UMR snapshot was true when the *alert* was written, not from a live
   re-check taken immediately before the task is actually created. `gap_queue_tick`'s own
   generic gap-prompt template (`build_gap_prompt()`) already carries an explicit warning for
   this ("the codebase has moved since this evaluation was written... say so in PROGRESS.md
   rather than making an unnecessary change") — the PM-decision-style prompts these three tasks
   used do not consistently carry the same warning, though in practice each worker did the right
   thing anyway.

   This is the same *class* of bug as the DB-path issue (trust a cached snapshot instead of
   re-verifying live state at the point of action) but a **different instance** — it lives in
   the alert→dispatch step, not the DB-read step, and is not fixed by `resolve_superboss_db_path`.
   It is also not new to this investigation: task 114214's own PROGRESS.md already names it
   `[[veridian-task-prompt-false-premise-pattern]]`, and a concurrent, independently-dispatched
   task (`task-20260805-122939-investigate-why-pr-932-and-pr-933-merged`) found the adjacent
   upstream cause of *why* PR #932/#933 merged with a failing required check in the first place:
   `supervisor-entrypoint.sh` merges via plain `gh pr merge --merge` (no `--admin`), and GitHub
   reports `mergeStateStatus: UNSTABLE` (not `BLOCKED`) for an admin able to bypass a failing
   required check when `enforce_admins` is off — so the bypass merge succeeds silently, with no
   distinct signal to whatever alerting/dispatch snapshot is watching, which is exactly the kind
   of event that makes a cached "still failing" snapshot go stale without anything re-checking it.

## Why no code fix was made here

A concrete fix exists (re-verify live GH/UMR state — e.g. `gh pr view --json mergedAt,state` /
`git merge-base --is-ancestor` — immediately before `dispatch_gap_item`/the PM-triage task-create
call mints a new task, not only when the alert was first written), but it was not implemented in
this task, for three honest reasons: (1) `dispatch-tick.py` is live, shared infrastructure
gating task creation for the entire fleet — an unreviewed change to it is a high-blast-radius,
hard-to-reverse action outside what this SPEC asked for; (2) at least four other concurrently
dispatched tasks were found already actively working this exact area (`task-20260805-122939`
merge-bypass investigation, `task-20260805-122949` compliance-tracker hardening,
`task-20260804-193850` / `task-20260805-151455` DB-path determinism) — writing a competing fix
here risks being the exact kind of duplicate/conflicting concurrent work this task exists to
prevent; (3) this SPEC's explicit, verified ask (stop live duplicate workers) required no code
change since nothing was live. Recommendation for the Owner: route the specific fix above
(pre-dispatch live re-check, PM-triage prompt template gets the same staleness warning
`build_gap_prompt()` already has) to whichever of the four in-flight sessions above ends up
owning the dispatch pipeline, rather than dispatching a fifth parallel attempt at it.

## Explicitly not stopped by mistake

No task was stopped. All three were independently confirmed non-live before this report was
written; stopping a `status: completed`/`blocked` task with no held lock and no process would
have been a no-op at best.
