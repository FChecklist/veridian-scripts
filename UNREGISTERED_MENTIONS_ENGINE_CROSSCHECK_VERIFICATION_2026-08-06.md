# Unregistered-mentions detector re-run + engine cross-check — verification (2026-08-06)

## SPEC claims checked independently before any write

1. **"unregistered_mentions has 8 rows, all resolved, all dated 2026-07-29, detector not
   re-run since."** — TRUE, confirmed by direct query against the live DB
   (`/opt/veridian/ai-os/memory/superboss-register.sqlite`) before touching anything: all 8
   rows `status LIKE 'RESOLVED_AUTO_REGISTERED:IDX-auto-20260729185058-%'` — same
   `regenerate_master_index.py --apply` run, same second (`18:50:58`), 2026-07-29.
2. **"wiring_registry is the real universal meta registry, ~8,447 entities, 20 engines."**
   — Partially true: 20 `engine` rows confirmed exact. Total row count at task start was
   **8,562**, not 8,447 (registry has grown since the SPEC was minted — expected, not a
   contradiction; reported honestly below as the real BEFORE count instead of the SPEC's
   stale figure).
3. **"A much larger real engine count was referenced elsewhere in this project than the
   twenty currently registered — cross-check compliance-tracker/src/lib/engines against
   wiring_registry entity_type=engine."** — The directory does contain far more than 20
   `*-engine.ts` files (27: 22 "country-agnostic" VCEL business-computation engines +
   5 country-specific tax engines under `ae/`/`in/`). **But** this is a false premise about
   *what wiring_registry's `engine` entity_type means*: it is a closed, hand-curated,
   20-item **AI-OS architectural taxonomy** (Intent Engine, Context Engine, Policy Engine,
   etc.), sourced verbatim from `ai-os/20_ENGINES_10_GATEWAYS_PHASE_PLAN_2026-07-24.yaml`'s
   `engine_inventory` — confirmed via `generate_wiring_registry.py --report-only`
   (`coverage_delta()`), which showed `engines_expected: 20, engines_covered: 20,
   engines_missing: []` — i.e. wiring_registry's engine rows are already in perfect sync
   with their own source of truth. The 22 VCEL business-computation engines are a
   **different, already-documented, intentionally separate category** — confirmed by the
   codebase's own comment in
   `repos/compliance-tracker/src/lib/engines/compliance-engine-registry.ts`: *"The 22
   country-agnostic engines in src/lib/engines/ (accounting, banking, costing, inventory,
   hr, payroll, etc.) have no statute-specific logic and are NOT part of this registry."*
   Registering them as new `entity_type=engine` rows (engine_no 21+) in
   `20_ENGINES_10_GATEWAYS_PHASE_PLAN_2026-07-24.yaml` would have been a category error,
   contradicting the architecture doc's own explicit, standing scope (`meta.*`: "all 20
   engines... Zero engines are NONE" — a closed set by design) and the Owner's own
   standing "no duplication" instruction embedded in that same document. **Did not do this.**

## What the cross-check actually found (the real gap)

Diffing the 24 non-registered-as-`engine` `*-engine.ts` files (the 22 minus the 3 that
happen to double as AI-OS engine `exists_as` evidence — `analytics-engine.ts` (engine-18),
`data-quality-engine.ts` (engine-13), `document-processing-engine.ts` (engine-11) — plus
the 5 country tax engines) against **every** wiring_registry row (all entity_types, not
just `engine`) showed **22 of the 24 are already fully tracked** — each has its own `file`
entity plus multiple `function` entities (sourced from `FUNCTION_CATALOG.json`, generated
2026-07-20). Correctly *not* tagged `entity_type=engine`, for the reason above.

Only **2 files were genuinely absent from wiring_registry under any entity_type**:
- `repos/compliance-tracker/src/lib/engines/ae/corporate-tax-engine.ts`
- `repos/compliance-tracker/src/lib/engines/ae/vat-engine.ts`

Both are the UAE country-pack tax engines (per their own header: "V2-1 UAE country pack,
2026-07-20"), modified after `FUNCTION_CATALOG.json`'s last generation (2026-07-20 09:19) —
their sibling India-pack files (`in/gst-engine.ts`, `in/income-tax-engine.ts`,
`in/tds-engine.ts`) *are* present, confirming the catalog simply predates these two files'
most recent real edits. `extract-function-catalog.mjs` (the catalog's own generator) was
not locatable in this environment (`find_code.sh` returned no hits), so it could not be
re-run directly; the equivalent canonical path (`superboss-register.py register-knowledge`,
which `generate_wiring_registry.py`'s `build_from_knowledge_engine()` picks up as new
`file` entities) was used instead — both are `scripts/superboss-register.py` CLI
subcommands, never raw SQL.

## Actions taken (canonical scripts only, no raw SQL)

1. `python3 superboss-register.py register-knowledge --path .../ae/corporate-tax-engine.ts ...`
   → `KE-20260806-170554-1960`
2. `python3 superboss-register.py register-knowledge --path .../ae/vat-engine.ts ...`
   → `KE-20260806-170556-130c`
3. `python3 generate_wiring_registry.py` (no `--report-only`, i.e. the real live-wiring
   apply run) — picked both new knowledge_engine rows up as new `wiring_registry` `file`
   entities (`file-ke-KE-20260806-170554-1960`, `file-ke-KE-20260806-170556-130c`,
   `verification_status=VERIFIED_MATCH`).
4. `python3 regenerate_master_index.py --apply` (the actual "detector," unscoped —
   `--apply` with no `--only` applies zero initiative-status corrections by the script's
   own conservative default, but always runs the unregistered_mentions backlog closure
   unscoped) — re-verified live, first real re-run since 2026-07-29. Result:
   `unregistered_mentions_resolved: 0`, `unregistered_mentions_still_flagged: 0` — nothing
   new had been flagged since 7-29 (the flagging side, `postflight_audit_gate.py`, runs
   elsewhere in the task-dispatch pipeline, not invoked by this script), so re-running the
   resolver correctly found zero pending rows, not a bug. `MASTER_INDEX.yaml` backed up to
   `MASTER_INDEX.yaml.pre-regen-backup-20260806-170644` before the wholesale rewrite, per
   the script's own safety convention.

## Before / after row counts by entity_type (proof)

| entity_type        | before | after |
|---------------------|-------:|------:|
| function             | 5028   | 5028  |
| file                 | 1978   | **1980** |
| dispatch_event       | 634    | 634   |
| supabase_table       | 444    | 444   |
| ai_role              | 195    | 195   |
| script               | 151    | 151   |
| cron_job             | 72     | 72    |
| engine               | 20     | 20    |
| gateway              | 10     | 10    |
| governance_doc       | 10     | 10    |
| github_repo          | 7      | 7     |
| route                | 6      | 6     |
| browser_component    | 4      | 4     |
| vercel_project       | 3      | 3     |
| **total**            | **8562** | **8564** |

`unregistered_mentions`: 8 rows before, 8 rows after (all `RESOLVED_AUTO_REGISTERED`,
same 8 as 2026-07-29 — no new rows were pending; the detector re-run is real and
verifiable via `run_id: REGEN-20260806-170644-bf61e8` in the regenerated
`MASTER_INDEX.yaml`, not just re-asserted).

## Bottom line

- Detector re-run: done for real, live, first time since 2026-07-29. Found 0 new pending
  `unregistered_mentions` rows (legitimately — nothing new had been flagged).
- Engine cross-check: the SPEC's "24-engine gap" framing was a category error (conflating
  the AI-OS's fixed 20-engine architecture with VCEL's separate 22+5 business-computation
  engines, which the codebase's own code comments explicitly exclude from the former). The
  real, narrower gap (2 completely unregistered files, correctly typed `file` not `engine`)
  was found and closed through the canonical scripts only (`register-knowledge` +
  `generate_wiring_registry.py`), +2 `file` rows, +2 `wiring_registry` total.
- No `entity_type=engine` rows added. No hand-edits to `20_ENGINES_10_GATEWAYS_PHASE_PLAN_2026-07-24.yaml`'s `engine_inventory` (its own header says "DO NOT HAND-EDIT this
  block").
