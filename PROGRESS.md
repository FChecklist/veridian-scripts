# PROGRESS -- task-20260806-163355-correction--ai-agent-id-scoped-one-per-u

## Completed
- [x] Verified premise independently before writing anything: confirmed UMR-20260806-121252-3207
      is a real row (`status='running'`, `ts_dispatched=2026-08-06T16:33:53Z`, matching the
      "just dispatched" claim), read its full real original prompt from `umr_tasks.inputs_json`,
      and confirmed the sibling worker task (task-20260806-163350-owner-explicit-go-ahead--build-the-real)
      had produced zero commits/work yet -- so this correction lands before any conflicting work exists.
      This task's own UMR is UMR-20260806-121332-6ba4 (confirmed via its own `inputs_json`).
- [x] Zero-duplication check before building (per this SPEC's own closing requirement): `lookup-capability`
      + `list-capabilities` (12 rows) showed no existing agent-id/registry capability; `find_code.sh
      "agent_id"` and `find_code.sh "ai_agent_registry|agent_registry|AgentRegistry"` over
      `/opt/veridian/scripts` both returned zero hits; confirmed compliance-tracker's own `worker_agent`
      capability-registry entity type is a separate embedding-similarity index in a different repo, not
      this mechanism.
- [x] Built `ai_agent_registry.py` -- new `ai_agent_registry` table inside the one existing live
      `superboss-register.sqlite` (zero new files/DBs), `agent_id` derived as a pure, deterministic,
      zero-judgment transform of `umr_id` ("UMR-" -> "AGENT-"), `umr_id` UNIQUE-constrained so one real
      UMR maps to exactly one real agent_id at the DB level (never a fuzzy task-class match, replacing
      UMR-20260806-121252-3207's original scoping per the Owner's clarification). Commands: `ensure-agent`
      (idempotent mint), `record-work` (appends to that UMR's own agent memory file across every
      cycle/retry/follow-up dispatch), `lookup-agent`, `list-agents`, `check-before-dispatch` (the real
      2-step deterministic gate: capability_registry first, then this exact UMR's own agent_id).
- [x] `test_ai_agent_registry.py` -- standalone test against a real, isolated throwaway temp DB, all
      green: idempotent 1:1 scoping, per-agent memory-file isolation (no cross-UMR leakage), deterministic
      id derivation, malformed-umr_id rejection, check-before-dispatch's `new_work_justified` flips
      false once an agent already exists for that exact UMR.
  - Caught and fixed a real near-miss while writing this test: `superboss-register.py`'s
    `resolve_superboss_db_path()` silently falls back to the LIVE production DB when
    `SUPERBOSS_REGISTER_DB` names a not-yet-existing file (never raises) -- the same pattern
    `test_dedup_constraints_2026-07-31.py` also uses, unguarded. First test run under this pattern
    wrote 3 real rows into the live DB; caught via direct sqlite3 verification, deleted immediately,
    confirmed clean, then fixed by pre-bootstrapping a minimal valid `umr_tasks` stub before setting
    the env var, plus a hard `DB_PATH` equality assertion before any write. Final runs confirmed zero
    live-DB writes.
- [x] Registered `ai_agent_registry` itself in the real, live `capability_registry` table (`CAP-20260806-164355-6f47`,
      via `register-capability --record-file ai_agent_registry_capability_record.json`) so a future
      zero-duplication check finds this mechanism there too, per this SPEC's own second requirement.
      Verified live: `lookup-capability --capability-name ai_agent_registry` finds it; a live
      `check-before-dispatch` dry run for a fresh dummy UMR now correctly returns `new_work_justified:
      false` because this capability itself already matches.
- [x] Confirmed the live `ai_agent_registry` table is empty (0 rows) after all setup -- correct, since
      no real work has been dispatched under any UMR against this new mechanism yet; only the
      `capability_registry` row (the mechanism's own registration) is a real, intentional live write.

## Out of scope (explicitly, not overlooked)
- UMR-20260806-121252-3207's OTHER original item (correcting the false `failed` status on
  UMR-20260806-065104-c69a) is not addressed here -- this correction SPEC only replaces the agent-id
  scoping unit and adds the capability_registry registration requirement; it never mentions the status
  item. That remains the sibling task's (task-20260806-163350-owner-explicit-go-ahead--build-the-real)
  responsibility under its own original, uncorrected spec. Noted here so it isn't silently dropped.
- Live wiring of `check-before-dispatch` into the actual dispatch mechanism (task-gateway.py /
  dispatch-tick.py calling this before every AI-judgment task) is not done in this task -- this SPEC
  builds and registers the mechanism itself; wiring it into the dispatch chokepoint is real follow-on
  work for whichever UMR the Owner directs next, not fabricated as already-done here.

## Remaining
- [ ] None for this SPEC's own scope.
