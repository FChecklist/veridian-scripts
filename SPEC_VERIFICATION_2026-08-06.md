# SPEC verification -- task-20260806-192048 (UMR-20260806-130416-3d77)

Governing chain per SPEC: UMR-20260806-124055-bc80 (stop-work order). Manual-registration
reason cited in SPEC: UMR-20260806-130110-c620.

## Part 1-3: re-run scanner, confirm the two named files, register if missing

**Independently verified before acting** (per this repo's false-premise-pattern history --
see prior PRs #94/#118/#129/#186/#202 etc.): the two functions the SPEC claims exist do
genuinely exist:
- `upsert_live_wiring_registry()` -- `scripts/generate_wiring_registry.py:969`
- `register_entity_row()` / `register_entity()` -- `scripts/superboss-register.py:2794`/`2832`

Ran the real existing scanner fresh (`python3 scripts/generate_wiring_registry.py`, live
upsert, not `--report-only`):
```
entity_count: 7838, live_wiring_registry_row_count: 8575 (post-run)
```

Confirmed by name, **both before and after** the fresh run, that both named files are
present in `wiring_registry` with `verification_status='VERIFIED_MATCH'`:

| path | entity_id | type | verification_status | last_verified_ts (post-run) |
|---|---|---|---|---|
| `/opt/veridian/repos/compliance-tracker/src/lib/prompt-os-resolver.ts` | `file-0e4aafa79c52` | file | VERIFIED_MATCH | 2026-08-06T19:29:07Z |
| `repos/compliance-tracker/src/lib/prompt-os-resolver.ts` | `function-c3d3e06cf577`, `function-51df4c7c99ba` | function (x2) | VERIFIED_MATCH | 2026-08-06T19:29:07Z |
| `/opt/veridian/repos/compliance-tracker/src/lib/orchestra-execution-logger.ts` | `file-e9e1eca5b0a1` | file | VERIFIED_MATCH | 2026-08-06T19:29:07Z |
| `repos/compliance-tracker/src/lib/orchestra-execution-logger.ts` | `function-d3d147f2589f`, `function-f176d9dbbb37`, `function-0d739c92bda9`, `function-b2cc25f20539` | function (x4) | VERIFIED_MATCH | 2026-08-06T19:29:07Z |

**Conclusion: the automated scanner already catches both files.** No manual
`register_entity` call was made -- the SPEC's own fallback condition ("if the real
automated scanner does not catch them, register both manually") is false; doing the manual
registration anyway would have created duplicate entity_ids for already-VERIFIED_MATCH rows.

## Part 4: comprehensive audit (scripts / repo files / engines vs registry)

Current live counts (`wiring_registry`, 8575 rows total):
```
ai_role 195 | browser_component 4 | cron_job 72 | dispatch_event 644 | engine 20 |
file 1981 | function 5028 | gateway 10 | github_repo 7 | governance_doc 10 | route 6 |
script 151 | supabase_table 444 | vercel_project 3
```
`capability_registry`: 16 rows (unchanged this run).

**Engines**: 20/20 -- confirmed already fully reconciled against its own source
(`20_ENGINES_10_GATEWAYS_PHASE_PLAN_2026-07-24.yaml`) via `coverage_delta()`, per prior
audit (case 20 in project memory, unchanged this run).

**`/opt/veridian/scripts` specifically** (the literal directory named in the SPEC):
- Real files directly in that directory (`find -maxdepth 1 -type f`): **381**
- `wiring_registry` rows: `entity_type='script'` = 151 (catalog-driven, from
  `SOFTWARE_CATALOG.yaml`, not a raw directory scan) + `entity_type='file'` with path
  containing `/scripts/` = 59. Combined coverage ≤ 210 rows against 381 real files --
  **an honest, real gap of up to ~171 files** (includes `.bak`/`.pre-deploy`/`.superseded`
  backup copies, `.txt` crontab backups, etc. -- not all of which necessarily warrant a
  registry row; that judgment call is explicitly out of scope here, see below).

**Full server-wide file-vs-row reconciliation (every repo, every file) was deliberately
NOT attempted in this task.** A same-batch sibling task dispatched 4 seconds after this one
(`task-20260806-192052-deterministic-full-server-file-registrat`, governing chain citing
*this task's own UMR* `UMR-20260806-130416-3d77` as its "wiring re-run mandate"
prerequisite) is explicitly chartered to build exactly this: a content-hash-deduped,
`.git`/`node_modules`/`.next`/ephemeral-task-workspace-excluded canonical scan across every
repo, wrapping `file_inventory.py`, with an idempotency proof. That is the correct owner of
the final authoritative disk-count-vs-registered-count number and missing-file list --
duplicating that build here (without its careful exclusion/dedup design) would produce a
second, inconsistent "audit" number, which is the opposite of the SPEC's own goal of "one
real place of truth." This task supplies its prerequisite (a fresh scanner run + the two
named files confirmed) and reports the honest partial numbers above; the full reconciliation
should be read from task `-192052`'s output once it completes.

**capability_registry vs `/opt/veridian/scripts`**: only 16 rows total (dedicated,
single-purpose capabilities) against 381 real script-dir files -- by design, per this
registry's own documented purpose (capability_registry is a curated capability catalog, not
a 1:1 file mirror; wiring_registry's `script`/`file` types are the file-coverage layer).

## Sibling-task cross-check (same UMR-20260806-124055-bc80 batch, dispatched within
seconds of this task)
- `-192038` (engine/superboss/capability study, gates orchestrator acceptance) -- no overlap.
- `-192043` (correction citing the *same two files* as design-pattern references for a
  `prompt_templates` table + structured execution log inside the orchestrator itself) --
  different ask (mirror the pattern in new code) from this task's (confirm registry
  presence); no conflict, no duplicated write.
- `-192052` (full-server file registration) -- see above, correct owner of Part 4's full
  reconciliation; this task does not duplicate it.
- `-192056` (Vercel/GitHub/Supabase registration) -- no overlap.

## Net result
- Scanner re-run: done, live, fresh (`last_verified_ts` = 2026-08-06T19:29:07Z).
- Both named files: confirmed present, `VERIFIED_MATCH`, both pre- and post-run.
- Manual registration: not needed (scanner already covers them) -- not performed.
- Comprehensive audit: honest partial numbers reported above (engines 20/20 clean; scripts
  dir has a real, disclosed ~171-file gap against `script`/`file`-typed rows); full
  server-wide reconciliation intentionally left to sibling task `-192052`, its designated
  owner, to avoid producing a second, conflicting "total" number.
