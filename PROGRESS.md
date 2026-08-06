# PROGRESS -- task-20260806-035541-owner-directive--build-a-real-pm-cycle-s

SPEC: extend the zero-manual-searching principle from the 10-minute PM report to the rest of the real
PM cycle. See `PM_CYCLE_PRECHECK_VERIFICATION_2026-08-06.md` for the independent verification of every
concrete claim in the SPEC (two did not match live state, documented there plainly).

## Completed

- [x] Item 1 (script registry check): independently verified `capability_registry` already has a
      `version` field (nothing to add there) but is the wrong table for generic script bookkeeping
      (business-capability schema, no `path` column). The genuinely correct existing mechanism is
      `wiring_registry`'s `entity_type='script'` rows (`register-entity`/`lookup-entity`/`list-entities`).
      Extended it (not `capability_registry`, not a new table) with two new nullable columns,
      `originating_umr` and `script_version`, via an additive idempotent migration
      (`_migrate_wiring_registry_umr_and_version()` in `superboss-register.py`), wired into
      `_migrate_schema()` and into `_migrate_wiring_registry_entity_types()`'s own rebuild path (so a
      future entity-type-widening rebuild doesn't silently drop the new columns).
      `register_entity_row()` now accepts both as optional fields.
- [x] Item 2 (backfill): found and fixed the real root cause of an existing catalog gap --
      `generate_software_catalog.py`'s `list_scripts()` only ever matched `*.py`, silently excluding 28
      real `*.sh`/`*.mjs` scripts. Fixed scope to `.py`/`.sh`/`.mjs`, added a shell-header-comment
      purpose extractor, and added real, mechanically-recovered (never invented) `originating_umr` /
      `script_version` fields per script. `generate_wiring_registry.py`'s `build_scripts_and_cron()`
      passes both through into `wiring_registry`. Verified zero `gtm_check_*.py` files exist on this
      server (SPEC's premise was false -- they exist only on unmerged feature branches); backfill covers
      every real script that actually exists, invents nothing for ones that don't.
      **Real backfill executed against the live production DB**: see "Real live evidence" below.
- [x] Item 3 (new script): `pm_cycle_precheck.py` -- one read-only invocation covering server health
      (reuses `generate_pm_report_v3.py`'s own functions directly, zero duplication), dispatch-tick
      results since the last real PM cycle (`pm_report_snapshots`), a zero-duplication precheck over
      queued/dispatched/running `umr_tasks` plus the existing `check_duplicate()` capability search,
      tracked PR state checks (`gh pr view`), and the three OCID-068 regression checks (resolver
      present, `ocid_canonical_registry` row count vs. the real 69-row baseline, seven guardrail PRs
      still ancestors of `origin/main`). Only write is its own bookkeeping log append
      (`--no-bookkeeping-write` to skip).
- [x] Item 4 (self-registration): every touched/new script (`superboss-register.py`,
      `generate_software_catalog.py`, `generate_wiring_registry.py`, `pm_cycle_precheck.py`)
      self-registered into `wiring_registry` via `register-entity` -- see real invocation output below.
- [x] Test coverage: `tests/test_wiring_registry_umr_and_version.py` (5 tests), `test_generate_software_catalog.py`
      (9 tests), `test_pm_cycle_precheck.py` (7 tests) -- all against real isolated temp-file DBs, never
      the live production DB. Full repo suite: 198 passed (4 pre-existing, unrelated `vt`-fixture errors
      in `test_ocid063_handoff_envelope.py`, already tracked on branch
      `fix/ocid063-handoff-envelope-pytest-vt-fixture`, not introduced by this task).
- [x] Independent verification doc: `PM_CYCLE_PRECHECK_VERIFICATION_2026-08-06.md`.

## Real live evidence (PR https://github.com/FChecklist/veridian-scripts/pull/114)

- **Bonus real bug found and fixed**: `generate_wiring_registry.py`'s `SOFTWARE_CATALOG` constant was
  wrongly using `resolve_doc_path()` (meant for hand-maintained planning docs, prefers the git mirror)
  instead of the hardcoded-real-path convention its 3 sibling machine-generated catalogs use right
  above it. Since `generate_software_catalog.py` always writes straight to the real
  `/opt/veridian/ai-os/SOFTWARE_CATALOG.yaml` and never to the mirror, the mirror copy
  (`/opt/veridian/repos/claude-control/ai-os/SOFTWARE_CATALOG.yaml`) was silently stuck at **40
  scripts, dated 2026-07-24** -- over 2 weeks stale -- and every prior real
  `generate_wiring_registry.py` run on this server (which never runs from inside a `veridian-scripts`
  git checkout) silently backfilled `wiring_registry` from that stale copy instead of live reality.
  Fixed by hardcoding the real path, matching its siblings.
- Ran `generate_software_catalog.py` for real against the live server: **101 -> 122 real scripts**
  cataloged (confirms the `.sh`/`.mjs` fix), **56/122 got a real `originating_umr` tag**, 3/122 got a
  real `script_version` tag, all mechanically recovered, none invented.
- Ran the narrow, scoped backfill (`build_scripts_and_cron()` + `upsert_live_wiring_registry()` only --
  not the full `generate()` pipeline, to avoid touching the other ~8,300 unrelated live rows) for real
  against the live production DB: **124 real `entity_type='script'` rows**, e.g.
  `script-superboss_register_py` -> `originating_umr='UMR-20260806-031211-64de'`,
  `script-generate_wiring_registry_py` -> `originating_umr='task-20260725-032718'`,
  `script-worker_entrypoint_sh` -> `originating_umr='UMR-20260801-153900-9100'` (real per-file regex
  recovery, live-verified).
- Self-registered `pm_cycle_precheck.py` (not yet deployed to `/opt/veridian/scripts`, so the catalog
  scan above correctly couldn't see it) via `register-entity` directly:
  `verification_status='PATH_MISSING'`, `originating_umr='task-20260806-035541'`,
  `metadata.not_yet_deployed_pending_pr=114` -- honest, not fabricated as already-deployed.
- **Real sample invocation** of `pm_cycle_precheck.py` against the live production DB:
  `python3 pm_cycle_precheck.py --search-term "pm_cycle_precheck" --pr-numbers "114,109"` --
  correctly found PR #114 `[OPEN]` / PR #109 `[MERGED]` via live `gh pr view`, correctly flagged the
  zero-dup precheck (`found=1 verdict=STOP`, matching its own just-created registry row), and passed
  all 3 real OCID-068 regression checks (resolver present, 69/69 canonical rows, all 7 guardrail PRs
  still ancestors of `origin/main`) in one invocation, one SSH round trip. Full output in this task's
  own transcript.

## Remaining

- [ ] Get PR #114 merged.
- [ ] Full-catalog `generate_wiring_registry.py` rebuild (all entity types, not just scripts) is
      deliberately OUT of scope for this task -- it touches ~8,300 unrelated rows and risks racing
      concurrent sibling tasks writing to the same shared production DB. Left for its own routine cron
      run; this task only ran the narrow, scoped script-only backfill described above.
