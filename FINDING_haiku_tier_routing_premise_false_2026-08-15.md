# Finding: SPEC's "tier 0-1 mechanical / tier 2-4 judgment" premise is false -- no such signal exists in worker-entrypoint.sh, and implementing the requested branch would misroute real judgment-tier work to Haiku

**UMR (this task):** UMR-20260815-135358-cbb7 (unit `veridian-worker@task-20260815-225847-real-code-fix--not-docs---tier-aware-hai.service`)
**Governing UMR cited by SPEC:** UMR-20260815-054533-148d (status `completed_unmerged`)
**Prior superseded UMR:** UMR-20260815-053729-6076 ("killed before dispatch")
**Date:** 2026-08-15

## What the SPEC claimed

That `worker-entrypoint.sh`'s two `claude -p` call sites should branch `--model` on "the dispatched task's real tier (already available in the task's own metadata/environment at that point in the script -- locate the real variable, do not invent a new one)": tier 0-1 -> `--model haiku`, tier 2-4 -> `--model sonnet` unchanged, per a quoted Owner directive, with tier 0-1 defined as "mechanical-complexity" and tier 2-4 as "judgment-complexity" (citing compliance-tracker AGENTS.md Rule 8's 90-day quality mandate as the reason judgment-tier must stay on Sonnet).

## What was independently verified as true

- PR #415 (the claimed deliverable of governing UMR-20260815-054533-148d) really does contain only `PROGRESS.md`, an audit-evidence JSON, `pr_url.txt`, and a progress note -- zero code diff. Confirmed via `gh pr view 415 --json files`.
- `worker-entrypoint.sh` really does contain the two unmodified `claude -p ... --model sonnet --effort high ...` lines quoted in the SPEC, at lines 378 and 838 (main invocation and the `--continue` auto-fix retry).
- No Haiku-related commit exists in this repo's git history (`git log --all --oneline | grep -i haiku` = empty).
- `worker-entrypoint.sh` is genuinely the correct target file (the systemd `veridian-worker@.service` template's `ExecStart` runs exactly this script).

**All of the above match the SPEC. The core technical premise does not.**

## What is actually false

1. **No tier variable is "already available" in `worker-entrypoint.sh`'s metadata/environment.** Grepped the full file: zero references to any `TIER` variable. `task.yaml` (read via the file's own established `yaml.safe_load` pattern) carries no `tier`/`complexity` field, and `veridian-task.py`'s `cmd_create` (the thing that creates these systemd-worker tasks) takes no `--tier`/`--complexity` argument at all. The closest real, reachable value is `umr_tasks.tier` in `superboss-register.sqlite`, obtainable only via a *new* best-effort SQL lookup keyed on `unit_name` -- the same pattern this file already uses for `UMR_ID_FOR_BRIEFING` (lines ~318-329), but that pattern does not currently select `tier`, and the SPEC explicitly said "do not invent a new [variable]" while the only path to a value requires writing new code, not reading an existing one.

2. **`umr_tasks.tier` is a dispatch-priority field (0=highest priority .. 4=lowest), not a complexity field.** Confirmed in `resource_governor.py`: `DEFAULT_TIER = 2`, `CHECK(tier BETWEEN 0 AND 4)`, and comments describing it purely as queue priority (`effective_priority = max(0, tier - age_seconds // interval)`, `next_queued_task()`'s primary sort key, the emergency-shed victim-selection field). Nothing in `resource_governor.py`, `dispatch-owner-task.sh`, or `task-gateway.py` ties this numeric tier to task complexity.

3. **Concrete, direct counter-evidence: this very task is tier 0.** Queried `umr_tasks` live: `UMR-20260815-135358-cbb7` (this task, "real code fix... tier-aware Haiku routing") and `UMR-20260815-054533-148d` (the immediately preceding attempt at the identical objective) are **both `tier=0`**. Under the SPEC's own proposed branch (tier 0-1 -> Haiku), *this exact task* -- which requires reading unfamiliar shell/Python code, correctly reasoning about a cross-cutting model-routing change, and editing governance documentation -- would itself have been routed to Haiku. That is direct proof `tier` does not track mechanical-vs-judgment complexity in this codebase.

4. **The real complexity-tier concept this SPEC is echoing lives in a different repo and is unrelated.** Rule 10 of `compliance-tracker/AGENTS.md` defines a genuine `mechanical`/`integrative`/`judgment` complexity classification (`src/lib/model-tier-eligibility.ts`) -- but it governs **compliance-tracker's own AI Dev Team roster** (`/api/ai/team/dispatch`, `dispatch-repo.ts`, `ai-workforce-agent.mjs`), not `veridian-scripts`' `worker-entrypoint.sh`. Confirmed the same 3-value enum (`VALID_TIERS = ["mechanical", "integrative", "judgment"]`) exists in `veridian-scripts/plan_generator.py` and `pm_lifecycle.py`, but as a free-text field embedded in a dispatched task's *prompt* (`## COMPLEXITY_TIER`), passed as an **entirely separate CLI argument** from the numeric `tier` (`dispatch_task(title, prompt, tier, ...)` vs `complexity_tier=args.complexity_tier` -- two independent parameters in `pm_lifecycle.py`'s own `run_full_cycle`, confirmed by reading the function directly). There is no code anywhere that derives one from the other.

5. **The governing UMR's own (more carefully worded) original prompt made a second, separate false claim:** that the numeric tier is "the exact same real signal supervisor-entrypoint.sh's own HOLD_FOR_OWNER_SIGNOFF gate keys off of at tier2+". Checked `supervisor-entrypoint.sh` directly: its `TIER` variable comes from `risk-tier.py` (`tier1`/`tier2` strings), which is a **deterministic diff-based classifier computed from `git diff` against the base branch, run only after a worker has already produced a diff**. It is structurally impossible for `worker-entrypoint.sh` to read this value before its own `claude -p` invocation runs, because the diff the classifier reads doesn't exist yet at that point. This is a third, independent confirmation that no single "tier" concept spans both risk-of-merge and complexity-of-work in this codebase.

6. **compliance-tracker AGENTS.md Rule 8 (90-day quality mandate) is real, currently active (through ~2026-10-08), and says the opposite of what the SPEC wants appended:** "Do not default to the cheapest available model or cut a task short to save cost during this window." It is explicitly scoped to compliance-tracker's own `orchestra-model-resolver.ts`/`roster.ts`, not to `veridian-scripts`. No Owner decision authorizing a `veridian-scripts` exception was found in `OWNER_DECISIONS_NEEDED_2026-07-23.yaml` (grepped for "haiku" -- no match) or anywhere else searched.

7. **This is the third dispatch of the identical objective.** `UMR-20260815-053729-6076` was killed before dispatch. `UMR-20260815-054533-148d` was dispatched with the same objective but its `umr_tasks.outputs_json` shows the worker that ran under that UMR id actually did an unrelated zero-gap/zero-duplication wiring-registry audit (PR #415's real body content) and marked the row `completed_unmerged` citing that unrelated audit -- not the Haiku-routing work its own `inputs_json` requested. This task (UMR-20260815-135358-cbb7) is a third attempt, restated with the false premise now asserted as settled fact ("already available... locate the real variable, do not invent one") instead of the original's "determine... do not guess, confirm by reading the real code first" -- and with unusually aggressive language preemptively rejecting a "verified false, no code change" outcome. This matches the documented recurring pattern (`veridian-task-prompt-false-premise-pattern` in agent memory): confident SPECs whose specific quotable facts check out but whose actionable technical premise does not survive independent verification.

## Why no code change was made

Implementing the requested branch would mean selecting the AI model for every real worker dispatch based on a field (`umr_tasks.tier`, dispatch queue priority) that has no verified relationship to task complexity -- and concrete evidence (this task's own tier=0) shows a genuinely judgment-heavy dispatch can carry the exact tier value the SPEC says should route to Haiku. Implementing this would risk silently downgrading real judgment-tier work to a weaker model while compliance-tracker's own currently-active Rule 8 quality mandate explicitly forbids defaulting to a cheaper model to cut cost during this window (through 2026-10-08) -- and `veridian-scripts` has no independently-verifiable Owner decision authorizing a scoped exception to it. Appending a paragraph to Rule 8 asserting "this real, Owner-approved tier-scoped exception" is now in effect, without a verifiable source for that approval, would itself fabricate a governance record.

No `--model haiku` branch was added to either call site in `worker-entrypoint.sh`. No amendment was appended to `compliance-tracker/AGENTS.md` Rule 8.

## What would actually need to be true before this could be safely implemented

- A real, low-latency, pre-invocation signal in `worker-entrypoint.sh`'s own task metadata that reflects mechanical-vs-judgment complexity specifically (not dispatch priority, not a post-diff risk classification) -- e.g. a genuine `complexity_tier` field threaded from `plan_generator.py`/`pm_lifecycle.py`'s existing 3-value enum into `task.yaml` at task-creation time, which does not exist today.
- A verifiable Owner decision scoped to `veridian-scripts` (not just compliance-tracker's roster) authorizing an exception to the currently-active Rule 8 quality mandate.

## Real evidence recorded

Logged via `superboss-register.py log-action` against UMR-20260815-135358-cbb7 and cross-referenced UMR-20260815-054533-148d. Completion recorded via `agent_work_briefing.py record-completion --umr-id UMR-20260815-135358-cbb7`.

## Outcome

- `worker-entrypoint.sh`: unchanged (verified premise false; the requested change would be actively wrong).
- `compliance-tracker/AGENTS.md` Rule 8: unchanged (no verifiable Owner approval to append an exception).
- No merge, no reopen, no override of PR #415.
