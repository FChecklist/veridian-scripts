# PROGRESS -- task-20260806-165903-correction--wire-the-new-ai-agent-id-tab

SPEC (`prompt.txt`): direct correction/extension to UMR-20260806-121332-6ba4
(`ai_agent_registry.py`, 1 UMR = 1 agent_id). Build the deterministic pre-work
briefing + post-work write-back layer it describes, reusing existing
infrastructure only, never recreating it.

## Completed
- [x] Verified the SPEC's premises independently before writing anything (per this
      session's own standing false-premise-verification practice). Real, confirmed:
      - `wiring_registry` is real and live: 8562 rows (SPEC said ~8447; grown since --
        the system is live, expected drift), across the exact entity types named
        (function 5028, file 1978, dispatch_event 634, supabase_table 444, ai_role 195,
        script 151, cron_job 72, engine 20, gateway 10, governance_doc 10, github_repo 7,
        route 6, browser_component 4, vercel_project 3).
      - `capability_registry` is real and live: 13 rows (SPEC said 12; the 13th,
        `ai_agent_registry`, is UMR-20260806-121332-6ba4's own registration).
      - The "new UMR scoped agent_id table" is `ai_agent_registry`, already live in the
        DB (0 rows -- its own test run cleaned up after itself) but **not yet on
        `main`**: it exists only on branch `worker/task-20260806-163355-...`
        (PR #194, OPEN/unmerged, `mergeable=CONFLICTING`). Cherry-picked commit
        `5f36209` onto this branch (conflict only in `PROGRESS.md`, resolved keeping
        this branch's own content; `ai_agent_registry.py` / `test_ai_agent_registry.py`
        / `ai_agent_registry_capability_record.json` applied cleanly, byte-identical to
        that branch) so this task can actually import and reuse it directly, per the
        SPEC's own "never recreate any of it, cite it and reuse it directly."
      - Confirmed via this branch's own already-merged history (PR #195, a sibling task
        `task-20260806-163350-owner-explicit-go-ahead--build-the-real`) that it
        independently investigated this exact same directive tree and found PR #194's
        work already covers UMR-20260806-121332-6ba4 itself, explicitly deferring
        "live wiring of check-before-dispatch into the actual dispatch chokepoint" as
        future work -- confirming this task (title: "wire the new ai agent id tab") is
        that real, non-duplicate next step, not a re-do.
- [x] Built `agent_work_briefing.py` -- the real deterministic briefing + write-back
      layer, composing three existing pieces only (never reimplementing their query/
      write logic):
      - `assemble-briefing`: queries `wiring_registry` (via `superboss-register.py`'s
        own `lookup_entity()` for path/entity_type/source_ref, PLUS a direct
        `relationships LIKE` scan added here since `wiring_registry_fts` does not
        index that column -- the SPEC explicitly names both fields), `capability_registry`
        (via `lookup_capability()`, same reuse pattern `ai_agent_registry.py`'s own
        `check-before-dispatch` already established), and `ai_agent_registry` (via
        `cmd_lookup_agent`, this exact UMR's own prior history). Returns one JSON
        object with explicit booleans (`has_matches`/`has_match`/`has_prior_history`)
        and a `close_ended_facts` array of concrete, non-vague statements.
      - `record-completion`: writes back through `ai_agent_registry.py`'s own
        `cmd_record_work` (agent's own memory row), `superboss-register.py`'s own
        `cmd_mark_umr_terminal` (the real system of record, `umr_tasks`),
        `gtm_write_category_result.py` (the one canonical writer every `gtm_check_*.py`
        script already calls, invoked the same subprocess way, only when a real
        `--gtm-category-index` is given -- "where relevant", never unconditional), and
        `superboss-register.py`'s own `register_entity_row()` for a genuinely new
        `wiring_registry` entity -- gated by an exact `entity_id` search-first check,
        confirmed a real no-op on a repeat call (never a duplicate row).
- [x] `test_agent_work_briefing.py` -- standalone test against an isolated temp DB
      (same convention as `test_ai_agent_registry.py`), all green: honest all-empty
      briefing for an unknown UMR/scope, real `path`-matched AND real
      `relationships`-only-matched `wiring_registry` rows both surfaced, real
      `capability_registry` match surfaced, prior `ai_agent_registry` history
      surfaced only once it exists, `umr_tasks`/`gtm_certification_categories` rows
      genuinely updated in the DB (verified by direct query, not just the CLI's own
      stdout), and the search-first `wiring_registry` dedup verified both by the
      command's own reported `written: false` and by a direct `COUNT(*)=1` check.
      Also re-ran `test_ai_agent_registry.py` (still green) and confirmed via direct
      query that none of this left any stray rows in the LIVE db.
      - Bootstrap note for future test authors: `_ensure_umr_table`'s fast path
        skips all migration once umr_id + 5 specific columns are present --
        `test_ai_agent_registry.py`'s own minimal stub relies on that skip, but a test
        that asserts against `umr_tasks.status` (this one does) must bootstrap the
        REAL, full column set instead, or the fast path leaves `status` missing.
- [x] Registered `agent_work_briefing.py` itself in the live `capability_registry`
      (`CAP-20260806-170938-a2c0`) via the existing `register-capability` CLI --
      zero-duplication check first (`list-capabilities`: 13 existing rows, none
      matching; `lookup-capability --intent-text "..."`: only an unrelated
      `capability_registry_dedup` FTS near-hit, not a real match).

- [x] Live-wired `assemble-briefing` into the actual dispatch chokepoint
      (`worker-entrypoint.sh`, the script that starts every real `claude -p` AI agent
      invocation): on a genuine first start (never a resume -- the agent already has
      this from its own PROGRESS.md/checkpoint history by then), resolves this exact
      worker unit's CURRENT real `umr_id` fresh via `umr_tasks.unit_name` (discovered
      live: the SAME systemd unit can be reused across a resumed/corrected
      re-dispatch with a genuinely DIFFERENT `umr_id` each time -- confirmed against
      this task's own row, `unit_name='veridian-worker@task-...-165903....service'`
      resolves to `UMR-20260806-121829-509e`, a prior UMR this same unit ran under
      before being reused for this task -- so it is never cached/assumed), then calls
      `agent_work_briefing.py assemble-briefing` and injects only its compact
      `close_ended_facts` (not the full JSON) into the agent's own prompt. Added a
      matching `record-completion` instruction, mirroring how the existing
      `PROGRESS_INSTRUCTION` already tells the agent to maintain `PROGRESS.md`.
      Additive/best-effort throughout (no `set -e` in this file; every DB/subprocess
      call degrades to an empty `BRIEFING_INSTRUCTION` on any failure, never blocks
      real dispatch) -- same posture `insert_ocid_artifact_link`'s own docstring
      documents for every other purely-additive traceability write in this codebase.
      - Real finding while wiring this: `lookup_capability()`'s own OR-of-terms FTS
        stage, given a full multi-sentence `intent_text`, honestly returned 12 of 13
        live `capability_registry` rows as "matches" on incidental vocabulary alone --
        not a bug in that existing function, but passing it verbatim would have made
        `agent_work_briefing.py`'s own output the vague, overclaiming guidance the
        SPEC explicitly rules out. Fixed at the call site (worker-entrypoint.sh passes
        the task's own concise `task.yaml` title, not the full `prompt.txt`) AND in
        `assemble_briefing()` itself (>5 keyword-only matches are now reported as
        "broad OR-keyword overlap, not a confirmed single-purpose fit" rather than
        implied as N confirmed reuse targets) -- verified live: dropped from 12/30KB
        to 6-7/compact with an honest qualifier.
      - Verified end-to-end against this task's own real live `umr_id` (dry run,
        `set -uo pipefail`, matching the real file's own shell options): resolves,
        calls the real live DB, produces a compact multi-line `BRIEFING_INSTRUCTION`;
        also verified the graceful all-empty degradation path for a `TASK_ID` with no
        matching `umr_tasks` row. `bash -n worker-entrypoint.sh` syntax-checks clean;
        existing `PREFLIGHT-GUARD-BLOCK-*`/`NOOP-COMPLETION-BLOCK-*` extraction
        markers (used by tests not present in this checkout) left untouched.

## Remaining
- [ ] None known. Both SPEC halves (pre-work briefing, post-work write-back) are
      built, tested, and live-wired into the real dispatch chokepoint. Out of this
      task's own scope, left for whoever picks it up: PR #194
      (`worker/task-20260806-163355-...`, the `ai_agent_registry.py` this task
      cherry-picked from) is still open/`CONFLICTING` against `main` -- once it
      merges, this branch's own cherry-picked copy should merge as a clean no-op
      (byte-identical commit), but worth a real check at that time rather than
      assuming it.
