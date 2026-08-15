# Progress: task-20260815-225847-real-code-fix--not-docs---tier-aware-hai

## Completed
- [x] Confirmed `worker-entrypoint.sh` is the correct target file (systemd `veridian-worker@.service` `ExecStart`), with the two quoted `claude -p ... --model sonnet ...` lines at lines 378 and 838.
- [x] Confirmed PR #415 really has zero code diff (4 non-code files) and no Haiku commit exists in git history -- SPEC's factual claims check out.
- [x] Independently verified the SPEC's *actionable* premise ("tier 0-1 mechanical / tier 2-4 judgment, already available as a variable"): **false**. No tier variable exists anywhere in `worker-entrypoint.sh` or `task.yaml`. The only reachable numeric tier (`umr_tasks.tier`, 0-4) is dispatch queue *priority*, not complexity -- confirmed via `resource_governor.py` (`DEFAULT_TIER=2`, priority-queue semantics only) and via direct counter-evidence: this task's own `umr_tasks` row is `tier=0`, i.e. under the SPEC's own proposed branch this judgment-heavy task would itself route to Haiku.
- [x] Verified the real `mechanical`/`integrative`/`judgment` complexity concept (Rule 10, compliance-tracker `AGENTS.md`) is scoped to a different repo's AI Dev Team roster, unrelated to `veridian-scripts`' numeric `tier`.
- [x] Verified `supervisor-entrypoint.sh`'s `HOLD_FOR_OWNER_SIGNOFF`/tier2 gate uses `risk-tier.py`'s post-diff `tier1`/`tier2` classifier, not `umr_tasks.tier` -- refuting the governing UMR's own claim these are "the exact same signal" (and confirming it's structurally unavailable before a worker's own `claude -p` call runs).
- [x] Verified compliance-tracker `AGENTS.md` Rule 8 (90-day quality mandate, active through ~2026-10-08) currently says the opposite of the requested change, and found no verifiable Owner decision authorizing a `veridian-scripts`-scoped exception.
- [x] Discovered governing UMR-20260815-054533-148d's own stored `outputs_json` shows the worker that ran under it actually delivered an unrelated zero-gap/zero-duplication audit, not the Haiku-routing work its `inputs_json` requested -- this is the 3rd dispatch of the same objective (1st killed pre-dispatch, 2nd mismatched output, this one).
- [x] Wrote up full verification in `FINDING_haiku_tier_routing_premise_false_2026-08-15.md`.
- [x] No code change made to `worker-entrypoint.sh` or `compliance-tracker/AGENTS.md` -- implementing the requested branch on the real numeric `tier` field would risk silently routing judgment-tier work to Haiku, which the concrete tier=0 counter-evidence above shows would happen to real judgment-heavy dispatches.

- [x] Recorded evidence via `superboss-register.py log-action` against UMR-20260815-135358-cbb7 (ACT-20260815-230813-1b16).
- [x] Called `agent_work_briefing.py record-completion --umr-id UMR-20260815-135358-cbb7` per protocol (AGENT-20260815-135358-cbb7).

## Remaining
- [ ] Commit + push this finding.
