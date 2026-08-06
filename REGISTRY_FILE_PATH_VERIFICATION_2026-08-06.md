# Registry File Path Verification -- 2026-08-06

UMR-20260806-124936-13b1, amendment to UMR-20260806-124055-bc80 /
-124327-6ffb / -124654-a8d6. Real, deterministic, right-now disk check
(`os.path.exists`, never the DB's own cached `verification_status` column)
against every real row in `capability_registry`, `wiring_registry`, and the
new UMR-scoped `ai_agent_registry` table. Reproducible: `python3
verify_registry_file_paths.py [--json]` (this repo root).

## Real counts (this run, live DB)

| table | checked | PASS | FAIL | not-a-disk-path (excluded by design) |
|---|---|---|---|---|
| `capability_registry` | 16 | 14 | **2** | 0 |
| `wiring_registry` | 7928 (of 8570 rows; rest have `path IS NULL`, e.g. `ai_role`/`vercel_project`/`dispatch_event`) | 7117 | **64** | 747 |
| `ai_agent_registry` | 0 | 0 | 0 | 0 |

"not-a-disk-path" = `entity_type` in `{engine, gateway, supabase_table,
ai_role, route, cron_job, vercel_project, dispatch_event}` -- these
entity_types' `path` column is, by the schema's own design, a multi-file
summary string (`"a.ts; b.ts"`), a `src -> dst` pair, a crontab command
line, a bare role slug, or a `schema.table` DB identifier -- never a single
disk path. The real underlying files they summarize are already tracked
individually as their own `file`/`function`/`browser_component` rows and
ARE included in the FAIL count above when broken. Treating these as broken
paths would be a category error (same class this session's own memory
already flagged once for `engine`-type rows).

`ai_agent_registry` is real (merged this session, PR #199) but has 0 rows
until the first `ensure-agent`/`record-work` call actually happens.

## capability_registry: 2 real failures

| capability_id | name | apis (broken) | root cause |
|---|---|---|---|
| `CAP-20260806-182313-9028` | `task_precedent_search` | `["search-task-precedent"]` (bare CLI subcommand name, not a file path) | Registered ~18:23 today by concurrently-**running** sibling task `task-20260806-181146-critical-amendment...` (UMR-20260806-124654-a8d6 -- one of this SPEC's own governing chain), real code on open PR #205 (unmerged at time of this check). |
| `CAP-20260806-182326-0e3e` | `capability_graduation_recording` | `["record-graduation"]` (same pattern) | Same task/PR. |

**Not fixed this session, deliberately.** Both rows were written by a
sibling task that is still live (`umr_tasks.status='running'`) as of this
check, and their real underlying code is real but still on an unmerged PR
(#205) it owns. Editing its `capability_registry` rows out from under it
risks the exact "duplicate/competing edit into a live task's own row" this
session's own memory warns about (see `veridian-task-prompt-false-premise-pattern`
case #19's "recycled row" lesson). Recommended real fix, once #205 is
observed merged: update `apis` on both rows to
`["/opt/veridian/scripts/superboss-register.py search-task-precedent ..."]`
/ `[".../superboss-register.py record-graduation ..."]` (the actual host
script, matching the already-correct convention `document_duplicate_detection`
uses: `"scripts/document_engine.py detect-duplicates"`), via
`register-capability` (idempotent upsert on `capability_name`), never raw SQL.

## wiring_registry: 64 real failures (of 7928 checked)

Full identity list: `REGISTRY_FILE_PATH_VERIFICATION_2026-08-06.json`
(`wiring_registry.failing_rows`). Three distinct root causes, confirmed:

1. **2 genuinely-obsolete `script` rows** (+2 duplicate `file`-typed rows
   from `knowledge_engine`, 4 total): `module-queue-dispatcher.py`,
   `queue-dispatcher.py`. Confirmed via `SOFTWARE_CATALOG.yaml`'s own text
   on `dispatch-tick.py` (task-20260726-210339): "consolidation of
   supervisor-sweep.sh + queue-dispatcher.py + module-queue-dispatcher.py
   into one script." Real defect: `SOFTWARE_CATALOG.yaml` no longer lists
   either as a script path (confirmed, zero `grep` hits), so a fresh
   `generate_wiring_registry.py` run correctly stops *producing* these two
   rows -- but `upsert_live_wiring_registry()` only ever upserts the fresh
   entity list, it never deletes a row the fresh run no longer produces, so
   both persist forever as orphans. **Not deleted this session**: no
   canonical single-row deletion CLI exists in `superboss-register.py` for
   `wiring_registry` (checked: no `deregister`/`remove-entity`/
   `delete-entity` subcommand). Recommended real fix (two independent
   options, either is correct): (a) add a narrow, single-row
   `deregister-entity --entity-id <id> --reason <text>` CLI (matching this
   codebase's existing single-row-only, no-bulk-op convention) and use it
   on exactly these 4 confirmed-obsolete rows; or (b) make
   `upsert_live_wiring_registry()` a true prune-to-fresh-snapshot for the
   entity_id namespaces this generator itself owns only (never touching
   `agent_work_briefing.py`'s or `register-entity`'s own ad-hoc rows,
   which live outside this generator's 8 source-of-truth inputs and would
   otherwise be wrongly deleted by a blind full-table prune -- confirmed
   this is a real risk before ruling out a naive prune-everything
   implementation).
2. **5 genuinely-missing `knowledge_engine`-sourced `file`/`governance_doc`
   rows**, real and current (`EXECUTION_RULES_AUDIT_2026-07-23.yaml`,
   `MASTER_GAP_AUDIT_2026-07-23.yaml`, `VARIABLE_DICTIONARY_2026-07-24.yaml`,
   `TERMINOLOGY_GUARDRAIL_2026-07-24.py`,
   `OWNER_ENGINE_MANDATORY_GATE_IMPLEMENTATION_2026-07-25.yaml`) -- these
   planning/audit docs from 2026-07-23/24/25 were never actually written to
   disk (or were written then removed); real, no fabrication, still broken
   after a live `verify-knowledge` re-check.
3. **~55 further `knowledge_engine`-sourced rows, mostly under a
   never-existing `/opt/veridian/ai-os-scripts/` (note: **not**
   `/opt/veridian/scripts/`) directory or dated `*_2026-07-24*`/
   `*_2026-07-25*` planning docs** -- same root cause as #2, a large
   pre-existing backlog this session's live `verify-knowledge` run did not
   reach (only the 11 rows already flagged `PATH_MISSING`/`HASH_DRIFTED`
   in the DB *before* this session were re-verified; this direct
   `os.path.exists()` sweep is what surfaced the other ~55 that the DB's
   own cached `verification_status` column had wrongly marked
   `VERIFIED_MATCH` -- itself the clearest evidence for why this SPEC's
   "never trust a cached status, check live" requirement matters).
4. **1 non-file `knowledge_engine` artifact wrongly modeled as a `file`**
   (`KE-20260729-181834-91c6`): `artifact_path` is a `https://claude.ai/...`
   URL, `artifact_type='derived'`, own `purpose` field states verbatim
   "external Claude Artifacts link (claude.ai), not server-hosted." This
   is a genuine category mismatch (same class as this session's memory
   case #20: a same-shaped field used for two different concepts), not a
   broken path to "correct" -- fabricating a fake disk path for it would
   be worse than the current honest `PATH_MISSING`. Recommended real fix:
   `knowledge_engine` needs an `artifact_type='external_link'` (or similar)
   path that legitimately skips disk-existence checking, rather than every
   row being checked against `os.path.isfile` regardless of type.

**Real fixes already applied this session** (via the existing canonical
`verify-knowledge` CLI, never raw SQL): 6 stale `knowledge_engine` rows
correctly moved from a 1-13-day-stale `PATH_MISSING` to the honest current
`HASH_DRIFTED` (file exists again, content changed since the original scan)
-- `AUDITOR_ENGINE_PHASE_PLAN_2026-07-24.yaml`,
`TERMINOLOGY_STANDARDIZATION_PHASE_PLAN_2026-07-24.yaml`,
`METADATA_ENGINE_RECONCILIATION_2026-07-24.yaml`, `pipeline.ts`,
`VERIDIAN_V2_DEFENSE_IN_DEPTH_TOOL_EVALUATION_2026-07-26.yaml`,
`VERIDIAN_V2_DSPY_TECH_DECISION_2026-07-27.md`. Then re-ran
`generate_wiring_registry.py` for real (live DB write, idempotent,
same cron job that already runs this periodically) so `wiring_registry`
picked up the correction.

## Orchestrator path usage

`worker-entrypoint.sh` (merged this session, PR #199) already resolves
`ai_agent_registry.py`/`agent_work_briefing.py` structurally
(`SCRIPTS`-relative `os.path.join`, matching `ai_agent_registry.py`'s own
`SCRIPTS = os.path.dirname(os.path.abspath(__file__))` convention) when it
calls `assemble-briefing`/`record-completion` -- never a separate hardcoded
absolute guess that could drift from the real deployed location. This
already satisfies "the orchestrator must use these real verified paths
directly," structurally rather than by consulting this verification
script's output at invoke time.
