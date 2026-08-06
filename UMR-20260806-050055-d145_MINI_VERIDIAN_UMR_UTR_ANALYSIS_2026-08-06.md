# UMR-20260806-050055-d145 — Analysis-Only Findings

**Owner standing directive**: register the permanent UMR for the nine-part
directive (UMR global metadata registry / UTR global task registry
consolidation + Mini VERIDIAN browser-first local execution architecture).
Explicit constraint: **analysis only** — investigation and written notes
only, nothing built, nothing implemented, until the PM reviews these
findings and gives explicit build authorization.

- UMR row: `UMR-20260806-050055-d145` (`task_identity =
  owner-task-20260806-050053-1500765`, tier 1, `source_trigger =
  owner_dispatch_gateway`), confirmed live in `umr_tasks` via direct
  read-only query — this is the actual row the Owner's prompt refers to as
  "this same UMR row", not an assumption.
- Scope covered here: **parts 1, 2, 3, 4, 7, 8** of the directive only.
  Parts 5, 6, 9 are explicitly PM-level conceptual framing and are **not**
  addressed in this document — the PM is handling those directly, per the
  directive's own instruction.
- **Nothing was built or changed by this task.** All findings below come
  from read-only investigation: sqlite opened `-readonly` throughout, no
  `INSERT`/`UPDATE`/`DELETE` issued against the live DB, no source files in
  `/opt/veridian/scripts` or either frontend repo were modified, no
  install/build/deploy commands were run. Only artifact produced is this
  markdown findings file plus `PROGRESS.md` checkpoints on this task's own
  branch.

---

## Part 1/2 — Does UMR-20260805-093630-29d1 already cover the new ask?

**Verdict: PARTIAL.** The UTR/UMR *naming and definition* is real and
already recorded at the DB source. The *consolidation* work the new
directive asks for, and the *Mini VERIDIAN browser-first architecture*, are
**not** covered by it — treating UMR-20260805-093630-29d1 as already
satisfying either of those two asks would be incorrect.

**`registry_taxonomy_notes` table — checked directly, not assumed.**
Exists, 1 row (`note_key = 'utr_umr_single_source_of_truth_taxonomy'`).
Verbatim content: **UTR (Universal Task Registry) = the real `umr_tasks`
table**; **UMR (Universal Metadata Registry) = the real, broader
knowledge/metadata layer**; the `UMR-YYYYMMDD-HHMMSS-hash` identifiers used
throughout the system are entries *within* that UMR layer, not a second,
separate identifier scheme. Names `superboss-register.sqlite` as "the one
real place of truth" housing UTR + UMR + the OCID family of tables. Cites
`(UMR-20260805-093630-29d1, citing UMR-20260804-170055-a069,
UMR-20260805-090549-9710, UMR-20260805-093138-2bd0.)`

Source of truth for this note is code, not just data:
`/opt/veridian/scripts/superboss-register.py:5023-5045` (constant
`REGISTRY_TAXONOMY_UTR_UMR_NOTE`), re-seeded idempotently on every DB
connection by `_seed_registry_taxonomy_notes()` (`superboss-register.py:5080`,
called from `_migrate_schema()`) — which is why the row's `recorded_at`
reads as near-now rather than 2026-08-05; this is a re-stamp of unchanged
content on every process touch, not evidence of fresh tampering or of a new
decision.

**UMR-20260805-093630-29d1 record — read verbatim from `umr_tasks`.**
`status='completed'`, `tier=2`. Its `inputs_json` is itself a prior Owner
reinforcement instruction defining the same UTR/UMR taxonomy and asking for
"a real, permanent explanatory record of this exact taxonomy... also add
this same real taxonomy to the OCID-068 real addendum document." That work
did land: the taxonomy note in the DB, plus §7 of
`OCID_068_PHASE_2_REGISTRY_SCHEMA_AND_LINKAGE_EXTENSION_2026-08-05.md`
(merged to `main` via PR #57, `mergedAt 2026-08-05T09:53:07Z`).

Two loose threads found, flagged for completeness rather than covered up:
- The specific "independent re-verification" commit that names
  UMR-20260805-093630-29d1 by name (`47a6780`,
  `OCID_068_UTR_UMR_TAXONOMY_INDEPENDENT_VERIFICATION_2026-08-05.md`) sits
  on an **unmerged branch with no PR ever opened** — even though the
  substantive content it was verifying did land earlier via PR #57.
- The row's `ts_completed` (09:53:07Z) **predates** its own `ts_dispatched`
  (16:12:44Z) — inherited from PR #57's merge timestamp rather than
  reflecting this row's own (later, unmerged) follow-up. A bookkeeping
  inconsistency, not a functional problem.

**Does it cover "UMR global metadata registry + UTR global task registry
consolidation"? NO evidence of any consolidation effort.** Grep of all
`.md` docs and FTS search of `umr_tasks_fts` for `consolidat*` near
UMR/UTR: zero hits. What exists is a naming clarification ("these are
already one system, here are the two names for its two halves") — not a
plan, design, or execution of structurally consolidating anything.

**Does it say anything about "Mini VERIDIAN browser-first local execution
architecture"? NO.** Zero hits anywhere (all `.md`/`.py` in
`/opt/veridian/scripts` and `/opt/veridian/repos/veridian-scripts`, plus
FTS search of the DB) for "Mini VERIDIAN", `mini_veridian`, `MiniVeridian`,
"browser-first"/"browser first". One adjacent but unrelated and dead thread
surfaced: three `umr_tasks` rows (`UMR-20260802-051754-fdef/85d0/44f2`,
`task_identity` containing `phase5-browser-execution-lite-llm-npu`), all
`status='killed'`, from an automated backfill process, with **no
corresponding document found anywhere**. This is not evidence the Mini
VERIDIAN architecture exists or is planned — it is an orphaned, killed,
unrelated automation artifact.

**Bottom line:** don't silently assume UMR-20260805-093630-29d1 covers this
directive. It covers the taxonomy-naming piece only.

---

## Part 3 — Is `superboss-register.py` genuinely the one script agents use? Real search time/memory.

**Verdict: YES, confirmed by independent wiring grep (not just its own
docstring claim), with real measured numbers below.**

Live call sites confirmed by grep of `/opt/veridian/scripts` and
`/opt/veridian/ai-os` (not the script's self-description):
- `worker-entrypoint.sh:217` and `doc-worker-entrypoint.sh:159` — every
  worker/doc-worker logs its AI response via `superboss-register.py
  log-action --source ai_response`.
- `task-gateway.py:37,47` — `SUPERBOSS`/`DB_PATH` constants point directly
  at this script/DB; `cmd_submit` routes through `superboss-register.py
  lookup-capability`.
- `prompt_gateway/gateway_persistence.py:29,163` /
  `prompt_gateway/gateway.py:502` — `gateway.py`'s status-search shells out
  to `superboss-register.py search <query> --limit 5` (this is the
  realistic query pattern measured below).
- `generate_task_checklist.py:106-118`, `index-logs.py:6`,
  `knowledge_registry_multisource.py:77,79`,
  `gtm_write_category_result.py:35` — all point at the same
  script/DB rather than reimplementing search/write logic.

**Duplication risk assessment: low.** `wiring_query.py` exists but
`importlib`-loads `superboss-register.py` and reuses its `_fts_query()`
rather than reimplementing it — and nothing else in the live tree calls
`wiring_query.py`, so it's an unused, disclosed wrapper, not a rival path.
`gateway_persistence.py` has one narrow, docstring-disclosed exception (a
direct read-only `SELECT` against `umr_tasks_fts`, which the `search`
subcommand doesn't cover) — additive, not a hidden duplicate.

**Real representative query measured:** `superboss-register.py search
"UMR-20260805-165906-0923" --limit 5` (mirrors the real
`gateway_persistence.py:163` call site; a real, currently in-flight UMR ID
referenced across many live task directories — the kind of lookup an agent
resuming/checking a UMR would actually run).

Because this script's `search` path opens the DB read-write with no
`--dry-run`/read-only flag, the measurement loaded the unmodified live
`search()` function via `importlib` and pointed it at a `mode=ro` SQLite
URI (same technique `wiring_query.py` itself uses) so the exact real query
ran with any accidental write raising an error instead of silently
succeeding. Confirmed no write occurred (live `.sqlite` mtime unchanged
before/after). All temp files deleted after.

| Run | in-process `search()` time | wall clock (`/usr/bin/time -v`) | peak RSS |
|---|---|---|---|
| 1 (cold) | 0.003629 s | 0:00.04 | 20,176 KB ≈ **19.7 MB** |
| 2 (warm) | 0.004547 s | 0:00.05 | 20,180 KB ≈ **19.7 MB** |

Both runs returned identical real results (7,397-byte JSON payload; hits in
`instructions`/`work_items`, none in `system_index`/`log_index`). The
~40-50ms wall time is dominated by Python interpreter + module-load
overhead for the 329KB script, not the FTS query itself (<5ms in-process).
Peak RSS (~20MB) is baseline CPython+sqlite3, not proportional to DB size —
expected, since FTS5 is index-driven.

**Context the numbers need:** live DB is **1.6 GB on disk**
(1,700,814,848 bytes), actively written concurrently by other live
processes during this measurement (`.writelock` present, `-wal`/`-shm`
advancing). Row counts of tables `search` covers: `instructions` 3,364;
`work_items` 1,777; `actions` 25,733; `system_index` 135; `log_index`
10,938 (`umr_tasks`, 7,121 rows, is *not* covered by `search` — only by the
disclosed `gateway_persistence.py` exception above). One caveat for
context: this DB directory carries visible history of prior corruption
incidents (`*.CORRUPTED-2026-07-2*`, `*.rebuild-*`, `*fts-damage*`
backups) — today's fast, clean, low-memory read is a snapshot of current
health, not a guarantee against past failure modes.

---

## Part 4 — Full inventory (tables + live scripts), duplication risk

Full inventory below is intentionally exhaustive per the directive's own
"visible at a glance" requirement.

### Database tables (`/opt/veridian/ai-os/memory/superboss-register.sqlite`)

**Core 3-tree register:** `instructions` (3,364 rows) — one row per Owner
instruction logged; `work_items` (1,777) — one row per unit of work
registered against an instruction; `actions` (25,733) — finest-grained
per-actor audit trail linked to work_item/instruction.

**System/software inventory:** `system_index` (135) — registry of every
script/component (path/category/layer/status/purpose/calls); `execution_log`
(16) — per-task field-completion log; `file_inventory` (27,249) —
path/size/mtime/hash inventory for drift/security detection;
`file_inventory_corrupted_orig_20260806T044301Z` — **malformed, unqueryable
forensic-preserved copy, not live**; `wiring_registry` (8,430) — master
entity graph (engines/gateways/tables/functions/routes/files/cron/roles);
`knowledge_engine` (378) — canonical/derived knowledge-artifact registry;
`capability_registry` (11) — business-capability registry.

**Audit pipeline:** `audit_findings` (16,672), `audit_runs` (164),
`audit_events` (150), `audit_master_reports` (2),
`audit_orchestration_runs` (2), `directive_compliance_runs` (8,720).

**Task/dispatch tracking:** `umr_tasks` (7,121) — the core UMR ledger;
`task_claims` (43) — dedup lease table; `task_audits` (16); `rca_open` (2);
`known_fixes` (5); `unregistered_mentions` (8).

**OCID family:** `ocid_canonical_registry` (69), `ocid_artifact_links`
(215), `ocid_master_standard_audit_log` (52), `ocid_compliance_state`
(113, trigger-derived), `ocid_compliance_audit_log` (2,938),
`registry_taxonomy_notes` (1 — see Part 1/2), `gtm_certification_categories`
(25).

**PM reporting/decisions:** `pm_report_snapshots` (23), `pm_decisions_pending`
(4).

**Other engines:** `automation_rules` (3) / `automation_rule_runs` (13),
`intent_unmatched_log` (4), `conversation_memory` (1), `plans` (1) /
`plan_steps` (3), `learning_reflections` (5), `log_index` (10,938),
`route_replay` (8).

FTS5 shadow tables (`*_fts*` for actions/instructions/work_items/
system_index/log_index/knowledge_engine/capability_registry/umr_tasks/
wiring_registry/route_replay) are SQLite's own full-text-index internals,
not independent data — not counted separately.

### Live scripts (`/opt/veridian/scripts`, 124 live top-level entries; 170 backup/dead files excluded)

Grouped by function (every live file covered):

- **Dispatch/task-lifecycle core:** `dispatch-tick.py`, `dispatch_core.py`,
  `dispatch-owner-task.sh`, `dispatch-docworker-task.sh`,
  `directive_engine.py`/`.sh`, `phase-continuation-tick.py`,
  `status-remediation-tick.py`, `master-decompose.py`,
  `resource_governor.py`/`_tick_loop.sh`, `veridian-task.py`,
  `task-gateway.py`, `task-template.py`, `veridian-task-watchdog.py`,
  `recover-failed-workers.py`, `sweep_awaiting_approval.py`.
- **Guards/gates:** `preflight-guard.py`, `scope-check.py`, `risk-tier.py`,
  `decision-service.py`, `policy_decision.py`, `ddl_authorization_check.py`,
  `tight_task_validation.py`, `check_single_protocol_file.py`,
  `chatgpt_audit_guard.py`, `chatgpt_promptlib_guard.py`.
- **Register/DB core:** `superboss-register.py` (the register itself, see
  Part 3), one-off backfills (`migrate_2026-07-31_dedup_constraints.py`,
  `backfill_evidence_json_schema.py`,
  `backfill_ocid_registry_phase2_columns.py`,
  `backfill_phase_self_report.py`), `gtm_write_category_result.py`,
  `session_metadata_sync.py`.
- **OCID audit/compliance:** `audit_ocid_canonical_registry.py`,
  `audit_ocid_compliance.py`.
- **Catalog/registry generators:** `generate_software_catalog.py`,
  `generate_wiring_registry.py`, `generate_quick_reference.py`,
  `generate_task_checklist.py`, `generate-system-diagram.py`,
  `regenerate_master_index.py`, `generate_module_gap_audit_module_list.py`,
  `generate_chatgpt_audit_index.py`, `generate_chatgpt_audit_request.py`,
  `generate_chatgpt_promptbatch_request.py`,
  `generate_prompt_coverage_report.py`, `ingest_chatgpt_audit_response.py`,
  `ingest_chatgpt_promptbatch_response.py`, `detect_prompt_duplicates.py`,
  `module_gap_audit_lib.py`, `knowledge_registry_multisource.py`,
  `metadata_reconciliation.py`, `generate_pm_report_v3.py`,
  `extract-db-schema-catalog.mjs`.
- **Engines:** `automation_rule_engine.py`, `context_engine.py`,
  `intent_engine.py`, `learning_engine.py`, `plan_generator.py`,
  `document_engine.py`, `notification_engine.py`.
- **Ops/infra:** `chrome_start.sh`/`chrome_stop.sh`,
  `claude-tmux-usage-limit-check.sh`/`claude-usage-limit-retry.sh`,
  `cost-reconciliation.py`, `cost-usage-60min.py`, `credit-accountant.py`,
  `deploy-live-scripts.sh`, `sync-repos.sh`, `sync-vercel-env.sh`,
  `sync-verdian-ai-data.sh`, `sync-controller-back.py`, `system-sync.py`,
  `health-check-15min.py`, `security-check.py`, `notify-owner.py`,
  `owner_status.py`, `check_latest_task.py`, `gap-status.py`,
  `index-logs.py`, `import-memory-history.py`,
  `batch-import-conversation-log.py`, `pallavi_stop.sh`,
  `launch-interactive-claude.sh`, `run-logged.sh`, `quality-gate.sh`,
  `supervisor-entrypoint.sh`, `supervisor-sweep.sh`,
  `doc-worker-entrypoint.sh`, `repair_controller.py`,
  `sap_mapping_store.py`, `external_ai_state_machine.py`,
  `anthropic_openrouter_proxy.py`/`_v2.py`, `auto_phase_continuation.py`,
  `veridian_remediation_dispatcher.py`.
- **Tests (11):** `test_check_crontab_unauthorized_change.py`,
  `test_dedup_constraints_2026-07-31.py`, `test_dispatch_tick_heartbeat.py`,
  `test_generate_pm_report_v3.py`, `test_ocid063_handoff_envelope.py`,
  `test_pm_triage.py`, `test_stuck_task_heartbeat.py`,
  `test_tight_task_validation.py`, `test_worker_boot_activation_and_resume.py`.
- **Docs (14 .md):** `README-dispatch-consolidation.md` plus 12
  OCID/UMR/duplicate-verification finding records dated 2026-08-05/06.
- **Subdirectories:** `archive/` (1 archival file, not live);
  `browser/` (`smoketest.js`, `persistent-profile.js` — Playwright/Chrome
  automation helpers); `owner_engine_convergence/` (`build_gold_items.py`,
  `measure_accuracy.py`); `prompt_gateway/` (package: `gateway.py`,
  `config.py`, `query.py`, `gateway_persistence.py`, plus `engine/`
  subpackage); `systemd/` (unit/timer configs); `tests/` (~15 pytest
  files).

### Possible duplication — callout list

1. **Confirmed dead-but-still-live scripts contradicting their own
   consolidation record.** `auto_phase_continuation.py`,
   `veridian_remediation_dispatcher.py`, and `supervisor-sweep.sh` each
   still exist as plain, unsuffixed live files, byte-identical in size to
   their own `.superseded-by-consolidation-2026-07-27` copies — despite
   `README-dispatch-consolidation.md`'s own mapping table declaring all
   three retired in favor of `phase-continuation-tick.py`,
   `status-remediation-tick.py`, and `dispatch-tick.py` respectively. A
   stray cron entry or manual invocation could still run the "retired"
   copy alongside its replacement.
2. **`anthropic_openrouter_proxy.py` vs `_v2.py`** both live at top level;
   v2's docstring implies v1 is functionally superseded but v1 was never
   removed. Worth confirming only v2 is wired to a running proxy port.
3. **Nine separate dispatch-adjacent scripts** — distinct documented
   triggers each, but the count itself is the class of complexity
   `dispatch_core.py`'s own docstring names as the root cause of the
   2026-07-26 OOM-kill incident.
4. **Five-plus independent "inventory the whole system" mechanisms**
   (`system_index`, `knowledge_engine`, `wiring_registry`,
   `capability_registry` tables, plus `generate_software_catalog.py`'s
   `SOFTWARE_CATALOG.yaml` and `regenerate_master_index.py`'s
   `MASTER_INDEX.yaml`) all overlap in purpose. Highest-density
   duplication-risk surface found.
5. **Same filename, two different live packages**: top-level
   `context_engine.py` vs `prompt_gateway/engine/context_engine.py`; same
   for `document_engine.py`. Genuinely different code, real
   "which one do you mean" grep-confusion risk.
6. **Two task-management CLIs**: `veridian-task.py` and `task-gateway.py`
   (documented wrapper-over-base, not a true duplicate, but a common
   confusion point).
7. **Five separate sync scripts** (`sync-repos.sh`, `sync-vercel-env.sh`,
   `sync-verdian-ai-data.sh`, `sync-controller-back.py`, `system-sync.py`)
   — distinct real scopes, grouped here only because "sync" naming alone
   doesn't disambiguate at a glance.

---

## Part 7 — Server-side list + honest client-side inventory

### List A — global server-side tables, functions, scripts

Tables: all of the Part 4 database table inventory above (36 real tables +
FTS shadows), all living in one SQLite file,
`/opt/veridian/ai-os/memory/superboss-register.sqlite`.

Scripts/functions: all 124 live entries under `/opt/veridian/scripts`
inventoried in Part 4, principally centered on `superboss-register.py`
(the register CLI itself — `log-action`, `search`, `lookup-capability`,
and the taxonomy-note/OCID/UMR read-write surface) plus the
dispatch/guard/catalog/engine clusters listed above.

### List B — honest client-side inventory: PROJEXA + veridian-compliance-ai

Repo naming confirmed first, not assumed: no repo under
`/opt/veridian/repos` has `package.json` name `veridian-compliance-ai` —
that string is the production domain
(`veridian-compliance-ai.vercel.app`); the actual repo is
`/opt/veridian/repos/compliance-tracker` (confirmed via its own docs and
git remote `github.com/FChecklist/compliance-tracker.git`).

**PROJEXA (`/opt/veridian/repos/projexa`):**
- localStorage: 3 real call sites, all trivial UX state (a pending-org-name
  handoff between signup/login pages) — not app data.
- IndexedDB: **real, substantial.** `src/lib/offline/work-progress-queue.ts`
  (206 lines, via `idb-keyval`) implements a per-user-scoped offline queue
  for field-worker "work progress" entries including local Blob photo
  storage, wired into `WorkProgressClient.tsx`, covered by
  `e2e/offline-work-progress-sync.spec.ts`. Photos are stored locally but
  **not** currently uploaded on sync — a disclosed gap in the code
  comments, not a hidden one.
- PWA manifest: **found**, `src/app/manifest.ts` — standalone display,
  installable. The file's own header states PROJEXA had zero PWA infra
  before this was added.
- Service worker: **found and real**, `public/sw.js` (69 lines,
  hand-written) — app-shell caching, network-first navigation with offline
  fallback, cache-first static assets, explicitly bypasses `/api/*`;
  registered by `src/components/ServiceWorkerRegister.tsx` in
  `layout.tsx`.
- **Summary: real, working offline infrastructure already exists**,
  scoped to the field-worker work-progress capture flow specifically —
  not a general offline app.

**compliance-tracker / veridian-compliance-ai
(`/opt/veridian/repos/compliance-tracker`):**
- localStorage/sessionStorage: real but minor — visitor ID, an offer code,
  onboarding-checklist state, chat-dock draft text, a UI library's
  resizable-panel state, plus an unrelated Office Add-in's own API-key
  storage. All small UI-preference/tracking values, not app data
  persistence.
- IndexedDB: **real, substantial, and directly relevant to "Mini
  VERIDIAN".** Three modules: `src/lib/browser-intent-cache.ts` (client-only
  cache of past chat submissions, feeds VeriComposer/IntentCommandPalette;
  its own docs note "encrypted" from an earlier spec was deliberately
  *not* implemented — an honest disclosed limitation, not a claim of
  security it doesn't have); `src/lib/browser-execution/model-cache.ts`
  (`IndexedDbModelCache` implementing Transformers.js's `CacheInterface`
  to cache downloaded ML model weights locally, consumed by
  `transformers-engine.ts`/`npu-engine.ts`); `src/lib/browser-execution/
  cross-tier-storage.ts` (OPFS → Cache API → IndexedDB fallback
  abstraction for the same browser-execution ML infra). **This means a
  browser-side local-model-execution tier already exists in code today**
  under `src/lib/browser-execution/` — directly relevant context for the
  Mini VERIDIAN part of the directive that Part 1/2 found zero doc/UMR
  evidence for. No hand-rolled `idb`/`dexie`/`localforage` dep — raw
  IndexedDB throughout.
- PWA manifest: **found**, `src/app/manifest.ts` — standalone display,
  `start_url: "/home"`, plus a working OS-level `share_target` entry.
- Service worker: **not found.** No `sw.js`, no
  `navigator.serviceWorker.register` call, no workbox/PWA-tooling deps in
  `package.json`. **No offline support exists** — the installable manifest
  currently has no offline capability behind it; every page load still
  needs live network.
- **Summary: real but partial** — installable PWA manifest + substantial
  IndexedDB (chat-intent cache + ML model-weight cache) exist; **zero
  offline support** (no service worker) — that would be new build work,
  not something to discover as already present.

**Bottom line for the PM:** neither frontend is starting from zero, and
PROJEXA is the more offline-capable of the two today. compliance-tracker
already has a nontrivial browser-execution ML tier
(`src/lib/browser-execution/`) that is directly relevant scope for any
"Mini VERIDIAN" plan — worth reading before scoping new build work, since
some of it may already exist.

---

## Part 8 — Does a real server↔browser sync mechanism exist today?

**Verdict: PARTIAL — leaning NO for anything resembling real sync.** Only
plain on-demand HTTP request/response (polling) exists between a browser
and its *own* app's server. Nothing resembling server-push, background
sync, or conflict resolution is wired into production anywhere.

**Client→server evidence (real, all polling/on-demand):**
- `compliance-tracker/.../chat/page.tsx:18,84` — 8s poll interval.
- `compliance-tracker/.../veri-ai/page.tsx:23,64` — 6s poll interval.
- Similar fetch/react-query usage across several other compliance-tracker
  pages/components — ordinary request/response against
  compliance-tracker's own Next.js API routes.
- `projexa/src/lib/veridian-client.ts:97` — but this file's own header
  states it "never runs in the browser (server components / route
  handlers only)" — server-to-server, not browser→server.
- `projexa/src/lib/offline/work-progress-queue.ts:167` — a genuine
  browser→PROJEXA-server call on the browser's `online` event
  (`MAX_SYNC_ATTEMPTS = 5`). Real and working, but syncs to PROJEXA's own
  backend, not to `/opt/veridian/scripts`.

**Server→client push evidence: none found.** Zero repo-wide hits for
WebSocket/`EventSource`/`socket.io`/Supabase realtime channels in either
frontend. `/opt/veridian/scripts/webhook_receiver.py` is confirmed
**inbound** (external event → `automation_rule_engine.py`), and confirmed
**not currently running** (no listening port, no matching systemd unit, no
crontab entry). No API server process anywhere in `/opt/veridian/scripts`
that a browser could reach — live listening ports on the host are only
sshd, local DNS, and a local Supabase CLI dev-stack block.

**Do the frontend calls and a real server correspond?** compliance-tracker
genuinely calls its own live deployed server (a real, connected loop);
PROJEXA's server-side calls genuinely reach that same deployment. But **no
frontend code anywhere calls `/opt/veridian/scripts` or the ai-os dispatch
machinery** — `superboss-register.sqlite` is touched only by local Python
processes on the host, with no exposed endpoint any browser could reach.
So the "server" in the Owner-directive sense (scripts/ai-os) has zero
connection, live or aspirational, to either browser client today.

**Offline-sync/background-sync/conflict-resolution: exists in code but is
inert/unwired**, with one narrow real exception:
- `compliance-tracker/src/lib/browser-execution/sync-engine.ts` (245
  lines) implements a real `OfflineQueue`, `coalesceQueuedChanges()`,
  `syncQueue()`, `pullDeltaSync()`, `SyncMutex` — genuine delta-sync logic
  — but its **only importer in the entire codebase is its own test file**.
  Unit-tested, never wired to a real transport or production caller.
- No service worker in compliance-tracker at all, so no Background Sync
  API usage is even possible there.
- compliance-tracker's live task-update path has no version/ETag/If-Match
  field — confirmed unconditional last-write-wins.
- The one real, live, wired offline queue is PROJEXA's
  `work-progress-queue.ts` (Part 7) — scoped to construction work-progress
  entries, syncing to PROJEXA's own backend only, not to scripts/ai-os.

**Corroboration note:** this matches an existing internal analysis already
in the repo, `compliance-tracker/ai-os/
VERIDIAN_UNIFIED_SYNCHRONIZATION_RUNTIME_2026-08-03.md`. That document was
not simply trusted — the key claims (poll intervals, zero
WebSocket/EventSource hits, sync-engine.ts's test-only importer,
webhook_receiver.py's inbound/non-running status, absence of any
scripts-side listening server) were independently re-derived via direct
grep/read before being treated as corroboration. Worth flagging to the PM
that this prior analysis already exists and reached the same conclusion.

---

## Explicit statement

Nothing was built, implemented, or changed on any live system by this
task. All five investigation threads above (parts 1/2, 3, 4, 7, 8) were
conducted read-only: the live sqlite DB was opened `-readonly` throughout
with confirmed-unchanged mtime, no source files were modified in
`/opt/veridian/scripts`, `/opt/veridian/repos/projexa`, or
`/opt/veridian/repos/compliance-tracker`, and no install/build/deploy
commands were run. The only artifacts produced are this findings document
and this task's own `PROGRESS.md` checkpoints, on this task's own branch.
Awaiting PM review and explicit build authorization before any of parts 5,
6, 9, or any implementation work proceeds.
