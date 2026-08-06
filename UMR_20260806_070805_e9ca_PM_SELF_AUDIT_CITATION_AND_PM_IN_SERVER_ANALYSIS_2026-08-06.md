# UMR-20260806-070805-e9ca: PM Self-Audit Citation (record only) + PROJECT MANAGER IN SERVER Analysis/Design (no build)

**Real dispatch instruction:** `task-20260806-070026-register-real-umr-for-pm-self-audit-and` (Owner directive, "Real Owner directive, this dispatch mints the real permanent UMR for two real standing items...")
**Real citation UMR minted by this record:** `UMR-20260806-070805-e9ca` (`resource_governor.submit()`, tier=2, `source_trigger=owner_dispatch_gateway`, `task_kind=veridian_task_create`, `task_identity=umr-citation-pm-self-audit-and-pm-in-server-directive-task-20260806-070026`)

## Real concurrent-dispatch collision, disclosed up front

The exact same Owner directive (title `"Register real UMR for PM self audit and PROJECT MANAGER IN SERVER orchestration directive"`, and a second phrasing `"...and orchestration directive"`) was independently found, live, dispatched to **four** separate worker sessions within the same minute, all under the umbrella of the same `owner-task-20260806-065103-*` submission:

| umr_id | worker unit (`unit_name` at last write) | new task id |
|---|---|---|
| `UMR-20260806-065104-c69a` | `veridian-worker@task-20260806-070019-...` | `task-20260806-070019-register-real-umr-for-pm-self-audit-and` |
| `UMR-20260806-065104-844e` | `veridian-worker@task-20260806-070026-...` | **this task** |
| `UMR-20260806-065104-4432` | `veridian-worker@task-20260806-070143-...` | `task-20260806-070143-register-real-umr-for-pm-self-audit-and` |
| `UMR-20260806-065104-598e` | `veridian-worker@task-20260806-070148-...` | `task-20260806-070148-register-real-umr-for-pm-self-audit-and` |

Checked directly against the live `umr_tasks` table and the open-PR list (`gh pr list --repo FChecklist/veridian-scripts --state open`) both before starting this record and again immediately before writing it: none of the three sibling tasks had opened a PR yet. Per this repo's own established convention for this exact situation (e.g. `87aeb74`, `22a21a9`, `c9a3028`, `9730b1e` — "already fixed/merged/resolved by concurrent dispatch"), whichever of the four PRs merges first should stand as the real citation UMR; the other three should self-detect the merge and close as docs-only "already resolved by concurrent dispatch," not re-mint a second citation UMR for the same two standing items. This record notes that convention explicitly so any of the three sibling sessions reading this after the fact has a direct pointer to it.

## Premise check against live state (per this repo's own no-false-premise-write convention)

The dispatch's framing — "both queued since before the real database lock incident that just resolved" — was independently verified against the live `/opt/veridian/ai-os/memory/superboss-register.sqlite`, not accepted narrated:

- `PRAGMA integrity_check` on the live file currently reports real corruption, but it is **fully confined** to one table: `file_inventory_corrupted_orig_20260806T044301Z`, and that table's own name says what it is — the deliberately-retained *original, pre-repair* copy of `file_inventory`, kept as a forensic artifact by `/tmp/repair_file_inventory.py`'s rename-swap repair (real script found on disk, real docstring: *"One-off, explicit, checkpointed repair of the corrupted `file_inventory` table... per the rehearsed rename-swap plan in PROGRESS.md Step 5"*), timestamped `2026-08-06T04:43:01Z` — well before this dispatch (`07:00:26`).
- The **live**, in-use `file_inventory` table (30,965 rows), `umr_tasks` (7,176+ rows, actively receiving new inserts throughout this investigation), `actions`, `instructions`, and every other real operational table read back cleanly with no errors.
- Conclusion: the "database lock incident" is real, matches a real, already-executed, checkpointed repair from earlier the same morning, and is genuinely resolved for every live/operational table. The residual `integrity_check` failure is expected and inert (a quarantined forensic copy, not a live table). This premise is **true**, not a repeat of the false-premise pattern seen in prior dispatches this session.

## Part 1 — PM self-audit: permanent citation (record only, no further action)

This UMR is the requested permanent citation. It does not re-derive or re-answer the self-audit (already answered in chat, per the dispatch). The real, already-merged, already-live artifacts that back that chat answer — deterministic work assignment, close-ended/boolean rules, real file paths, hallucination/staleness/duplication prevention — are:

- **`UMR-20260804-170055-a069`** — canonical OCID-068 UMR, `status=completed`. Its seven-rule guardrail addendum (`OCID_068_GUARDRAIL_RULES_PERMANENT_CLOSURE_2026-08-05.md`, this repo) is the core deterministic-assignment rule set, each rule its own merged PR with dedicated tests (`tests/test_rule1..7_*.py`):
  - Rule 1 — UMR reuse on resume (PR #26)
  - Rule 2 — dispatch outcome classification (PR #29)
  - Rule 3 — no premature UMR minting; validate input → OCID → task identity → DB → zero-duplication, in that order, *before* any mint/write (PR #30, `resource_governor.py:submit()` lines ~511-560 in this checkout)
  - Rule 4 — PM-visible real counts (PR #32)
  - Rule 5 — real stall detection, via `umr_tasks.last_heartbeat` (PR #33)
  - Rule 6 — zero duplication by OCID, `find_active_umr_by_ocid()` (PR #34)
  - Rule 7 — completion evidence (PR #35)
- **`UMR-20260805-042152-e559`** — OCID Master Standard v6 Phase 1 (`OCID_MASTER_STANDARD_V6_PHASE1_2026-08-05.md`): `resolve_ocid_canonical()` (locks down the multi-source OCID→UMR search method so exact-substring false negatives, independently found for OCID-022/023/058/060, can't recur), `reconcile_umr_status_against_pr()` (staleness detection + proposed correction, never silent auto-apply), `refuse_certification_if_merged_without_required_checks()` (redundant anti-bypass refusal logic).
- **`plan_generator.check_reuse_before_dispatch()`** (Phase 7, `reuse-check-enforcement-gate-phase7-2026-07-30`) — wired directly into `resource_governor.submit()` (the one low-level entry point every real task-creation path funnels into): a deterministic, no-LLM-call reuse check against `capability_registry` + `wiring_registry` + `knowledge_engine` + `system_index` before every mint, recorded on the row itself (`metadata_json.reuse_check_result`), fail-open/advisory by design.
- **`pm_decisions_pending`** table + lifecycle (PR #110/#108, commit `daf9d3e`) — structured Owner/AI decision proposal/approval/resolution records, real columns (`decision_type`, `artifact_path`, `commit_sha`, `evidence`), replacing ad hoc prose decisions with a real, queryable table.
- **`OCID_001_069_CANONICAL_UMR_MAPPING_METHODOLOGY_2026-08-05.md`** — the anti-hallucination methodology itself: multi-source cross-checked search, explicit `not_found` honesty (8 of 69 honestly reported not-found rather than guessed), never a silent single-source pick when multiple UMRs exist for one OCID.

No further action is required on Part 1. This section is a citation record only.

## Part 2 — PROJECT MANAGER IN SERVER: findings + proposed design (analysis only — NO BUILD STARTED)

**Confirmation: no code was written, modified, or deployed for Part 2 as part of this task.** Everything below is investigation of existing architecture plus a proposed design for PM review. This section itself, plus the UMR row it is filed under, is the only "artifact" produced for Part 2.

### What already exists (so a build would not duplicate it)

| Concern | Real existing piece | Real gap vs. the directive |
|---|---|---|
| Always-on server-side loop | `resource_governor_tick_loop.sh` + `dispatch-tick.py` (cron/timer-driven), `systemd/veridian-worker@.service` (per-task worker unit template, real live instances confirmed this session: `veridian-worker@task-20260806-070026-....service` etc.), `systemd/veridian-pm-report-tick.service`+`.timer` | These already run continuously and spawn workers deterministically from the `umr_tasks` queue. They are the real orchestration substrate — a new layer should extend/read them, not replace them. |
| "Does a script/capability already exist" decision | `plan_generator.check_reuse_before_dispatch()`, wired into `resource_governor.submit()` (see Part 1) — queries `capability_registry` (11 live rows), `wiring_registry` (8,438 live rows, `entity_type` includes `script`, `engine`, `ai_role`, `cron_job`), `knowledge_engine` (378 rows), `system_index` FTS | This already answers "reuse a script" deterministically, no LLM call. It does **not** answer "reuse a running/idle **agent instance**" — a different axis the directive also asks for (script vs. **reused agent** vs. new). |
| Agent identity / persistent memory linked by file path | `conversation_memory` table exists in the live schema (`session_id, org_id, actor_ref, created_ts, last_active_ts, turn_count, entity_relationships, summary`) | **Real gap, not duplication risk**: only **1 row** exists in the live table, and no writer call site was found wired into the real dispatch path (`dispatch-tick.py`, `dispatch-owner-task.sh`, `worker-entrypoint.sh`, `veridian-task.py`). It is a dormant schema, not a working memory system — a new design can extend it rather than invent a parallel table, but should not assume it is already populated/authoritative. |
| Standing-instruction routing (server tmux + laptop) | `dispatch-owner-task.sh` is already the one real shared entry point for the **server tmux side**: it runs `check-content-duplicate` (content-hash, 6h window) before minting, then relays via `tmux send-keys -t claude` into the live interactive session named `claude` | The relay step itself is raw prose (`tmux send-keys -l "[$UMR_ID] $PROMPT"`) — software decides *whether to dispatch*, but the actual instruction delivered to the tmux Claude session is free text, not a structured, machine-actionable decision object. No equivalent single entry point was found for a **laptop-side** PM session (this conversation's own dispatch channel) — it appears to reach the same `umr_tasks` table via a separate path, not confirmed to run through `dispatch-owner-task.sh`'s same content-duplicate/reuse-check gate. |
| Stall / heartbeat detection for a live agent | `umr_tasks.last_heartbeat` column + Rule 5 (real stall detection, OCID-068) | Detects a *stalled* worker; does not currently answer "is this worker's session still warm/idle and safely reusable for a *new*, unrelated task" — a distinct question a reuse-vs-new-agent decision would need. |

### Proposed design (recommendation only — not authorized to build)

1. **Extend, don't replace, `check_reuse_before_dispatch()`.** Add a second, explicit axis alongside its existing script/capability check: an **agent-reuse check** that queries `umr_tasks` for a `running` row with a recent `last_heartbeat` and a `unit_name`/workspace path matching a reusable scope (e.g. same repo, same task family), returning a third possible recommendation value (e.g. `"resume_agent"`) with the candidate's real file path (`/opt/veridian/ai-os/tasks/<task-id>/workspace`) — never invented, always a real, queried path.
2. **Give agent identity a real row, written on every real dispatch, not just schema-defined.** Either populate `conversation_memory` for real (one row per live worker session, `actor_ref` = the real systemd unit name, keyed to the real task workspace path) or, if its `org_id`/`entity_relationships` shape turns out to be product-scoped rather than AI-OS-scoped (needs one more real read of its actual writers/readers, not assumed here), add a narrowly-scoped sibling table following this repo's own established pattern (small, single-purpose, own migration, own tests — as `pm_decisions_pending` and `ocid_master_standard_audit_log` both did). **This choice is flagged as an open question for PM/Owner decision, not decided unilaterally in this analysis.**
3. **Route both the laptop PM session and the server tmux session through the same real entry point.** Concretely: extend `dispatch-owner-task.sh` (already the single real shared gate for the tmux path) so it is the *only* path either session uses — the laptop session should shell out to the exact same script (or an equivalent Python entry point calling the exact same `check-content-duplicate` → `resource_governor.submit()` → reuse-check chain) instead of writing to `umr_tasks` by any separate route. This closes the "prose each cycle" gap the directive names, using software that already exists rather than new invented logic.
4. **Replace the raw `tmux send-keys` prose relay with a structured delivery envelope** once (1)-(3) exist: still delivered into the interactive tmux session (humans/agents there still read natural language), but generated *from* the structured decision object (`reuse_candidates`, `recommendation`, target file path) rather than being the sole record of the decision — the structured object is what a future "PM in server" orchestration layer reads, not a re-parse of the prose.
5. **Phase this the same way OCID-068's seven rules and the OCID Master Standard v6 were phased** (see `OCID_MASTER_STANDARD_V6_PHASE1_2026-08-05.md`'s own "deliberately deferred" section for the precedent): one small, independently-reviewed PR per numbered item above, never one large unreviewable build.

### Open questions for PM review before any build authorization

- Is `conversation_memory` the intended home for agent identity/memory, or does its existing (single, unclear-origin) row indicate it belongs to a different subsystem entirely? Needs a direct read of whoever wrote that one row before extending it.
- What heartbeat/idle threshold makes a running worker safely "resumable" for a new, unrelated task without risking cross-task context bleed?
- Does the laptop PM session currently write to `umr_tasks` through any existing shared gate at all, or does it need one built from scratch (as opposed to extended)? Not confirmed in this analysis — flagged rather than assumed.

## Disposition of this UMR

`UMR-20260806-070805-e9ca` is marked `completed` immediately (registration/analysis record, not a dispatched build), following the same precedent as `UMR-20260805-051109-77a9` (OCID-069's own registration) and `UMR-20260805-112247-3ad0` (a record-only citation, no new work dispatched).

## Real citations

- `task-20260806-070026-register-real-umr-for-pm-self-audit-and` (this record's own originating Owner directive)
- `UMR-20260806-070805-e9ca` (this record's own real citation UMR)
- `UMR-20260804-170055-a069` (canonical OCID-068 UMR, seven-rule guardrail addendum)
- `UMR-20260805-042152-e559` (OCID Master Standard v6 Phase 1)
- `UMR-20260806-065104-c69a` / `-844e` / `-4432` / `-598e` (the four concurrent sibling dispatches of this exact directive)
- `OCID_068_GUARDRAIL_RULES_PERMANENT_CLOSURE_2026-08-05.md`, `OCID_MASTER_STANDARD_V6_PHASE1_2026-08-05.md`, `OCID_001_069_CANONICAL_UMR_MAPPING_METHODOLOGY_2026-08-05.md` (Part 1's cited artifacts)
