# Real PM Cycle Scope: Independent Verification (2026-08-06)

**Real dispatch instruction:** task-20260806-035541 (Owner directive), citing `UMR-20260805-181636-32f2`
(the report-generator chain) and `UMR-20260805-185000-e94f` (the deterministic-script-consolidation chain).

Per this repo's own standing lesson (urgent SPECs have repeatedly cited claims that didn't match live
state -- see PRs #83/#89/#93/#94/#98/#100), every concrete claim in this task's own SPEC was
independently re-checked against live state before any code was written. Two did not match; the rest
did. Documented here plainly, not silently corrected.

## 1. "check whether capability_registry ... already serve this purpose" -- partially false premise

**Checked:** `capability_registry`'s schema (`superboss-register.py`) and its `register_capability`/
`lookup_capability`/`list_capabilities` functions, live.

**Found:**
- `capability_registry` already has a `version` field (`version TEXT NOT NULL DEFAULT 'unversioned'`,
  populated by every `register_capability()` call). **No version field needed to be added there** --
  the SPEC's premise that one might be missing was itself already false.
- `capability_registry` is schema-shaped for *business capabilities* (`business_rules`, `apis`,
  `permissions`, `ai_required`, `confidence`, `workflow`) -- there is no `path` column, and forcing a
  generic utility script (e.g. `resource_governor.py`) through those required fields would be a
  category error, not a natural fit.
- The genuinely correct, already-existing mechanism for **script bookkeeping specifically** is
  `wiring_registry`'s `entity_type='script'` rows (`register-entity`/`lookup-entity`/`list-entities` CLI,
  `register_entity_row()`), already fed in bulk from `ai-os/SOFTWARE_CATALOG.yaml` by
  `generate_wiring_registry.py`'s `build_scripts_and_cron()`. It has a literal `path` column, a
  `metadata_json` field already carrying `purpose`/`cron_scheduled`, and upserts by `entity_id`
  (`ON CONFLICT DO UPDATE`) -- i.e. it is already zero-duplication-safe by construction.

**Action taken:** extended `wiring_registry` (not `capability_registry`, and not a new parallel table)
with two new nullable columns -- `originating_umr` and `script_version` -- via the same additive
`ALTER TABLE ADD COLUMN` idempotent-migration convention already used for `content_hash`
(`_migrate_wiring_registry_umr_and_version()`). `register_entity_row()` now accepts both as optional
fields, same convention as `content_hash`.

## 2. "every real gtm_check_*.py script" -- false, none exist on this server

**Checked:** `ls /opt/veridian/scripts/gtm_check_*.py` (live server) and `git log --all --diff-filter=A
-- '*gtm_check*'` (full history, this repo).

**Found:** Zero `gtm_check_*.py` files exist on the live server. Nine such files (
`gtm_check_production_readiness_audit.py`, `gtm_check_multi_tenant_testing.py`,
`gtm_check_role_permission_testing.py`, `gtm_check_lighthouse_audit.py`,
`gtm_check_performance_testing.py`, `gtm_check_regression_testing.py`, `gtm_check_e2e_testing.py`,
`gtm_check_ui_testing.py`, `gtm_check_accessibility_testing.py`, `gtm_check_architecture_audit.py`, and
others) **were built and committed on feature branches** (e.g. `feat/gtm-checks-production-readiness-synthesis`)
but **were never merged to `main`** -- `git merge-base --is-ancestor <their-commit> HEAD` returns false
for all of them. `generate_pm_report_v3.py`'s own `classify_passed()` docstring cites
`gtm_check_production_readiness_audit.py` as "real, already-merged," which is the likely source of this
SPEC's false premise -- that docstring's own claim does not match `main`.

**Action taken:** none invented. The real backfill (item 2) tags every real script that actually exists
on disk; it does not fabricate registry rows for `gtm_check_*.py` files that were never merged/deployed.
`gtm_write_category_result.py` (the one real, currently-existing `gtm_*` script) is included in the
backfill normally.

## 3. "add every existing script ... with its real originating UMR" -- real gap found and fixed at the root cause

**Checked:** live `ai-os/SOFTWARE_CATALOG.yaml` (94 scripts) against a live directory listing of
`/opt/veridian/scripts` (113 real script files: `.py`/`.sh`/`.mjs`).

**Found:** 28 real files -- mostly `.sh` (`dispatch-owner-task.sh`, `worker-entrypoint.sh`,
`sync-repos.sh`, `supervisor-entrypoint.sh`, and 24 others) plus one `.mjs` -- were silently absent from
every prior run of the catalog. Root cause: `generate_software_catalog.py`'s `list_scripts()` only ever
matched `name.endswith(".py")`.

**Action taken:** fixed at the source, not papered over downstream -- `list_scripts()` now matches
`.py`/`.sh`/`.mjs`; `script_docstring()` gained a shell/`.mjs`-header-comment path
(`_shell_header_comment()`) alongside the existing `ast.get_docstring()` path for `.py`. Also added
`script_originating_umr()` (real regex recovery of a `UMR-YYYYMMDD-HHMMSS-hash` or, failing that, an
older pre-UMR-convention `task-YYYYMMDD-HHMMSS` id, from the script's own real file content -- NULL,
never invented, when neither is found) and `script_version_from_filename()` (real `_v\d+` filename
suffix parse, e.g. `generate_pm_report_v3.py` -> `v3`; NULL when absent).

## 4. "already specified in SKILL.md" (OCID-068 regression checks) -- false, no SKILL.md exists in this repo

**Checked:** `git ls-files | grep -i skill` and `git log --all --oneline -- '**/SKILL.md'` (this repo,
full history).

**Found:** no `SKILL.md` file has ever existed in `veridian-scripts`. The three concrete OCID-068
regression checks the SPEC describes ("resolver present, canonical registry row count, seven guardrail
PRs still ancestors") were instead independently recovered from this repo's own real, merged records:
`OCID_068_GUARDRAIL_RULES_PERMANENT_CLOSURE_2026-08-05.md` (the seven guardrail PRs' merge commits) and
`OCID_068_PHASE_2_REGISTRY_SCHEMA_AND_LINKAGE_EXTENSION_2026-08-05.md` section 6 (the 69-row
`OCID-001..OCID-069` full roster baseline).

**Independently re-verified live** (this task, against the real production DB / real `origin/main`,
before encoding into `pm_cycle_precheck.py`):
- `resolve_ocid_canonical` is present in `superboss-register.py`: **True**.
- `ocid_canonical_registry` real row count: **69** (matches the documented full roster).
- All seven guardrail PR merge commits (`#26 29a153b`, `#29 50c272d`, `#30 fe3ec0d`, `#32 64e16d0`,
  `#33 9b716b9`, `#34 8235a87`, `#35 638fd38`) are **still real ancestors of `origin/main`**
  (`git merge-base --is-ancestor`, run against the live `/opt/veridian/repos/veridian-scripts` mirror).

## What was confirmed true, unchanged

- `capability_registry`, `register_capability`/`lookup_capability`/`list_capabilities` all exist exactly
  as described, from PR #8 (per that table's own docstring history).
- `superboss-register.py` is real, live, and is the one DB write path (per PR #106's prior canonical
  statement, unaffected by this task).
- `resource_governor.py`, `dispatch-owner-task.sh`, `audit_ocid_canonical_registry.py`,
  `audit_ocid_compliance.py`, `generate_pm_report_v3.py` all genuinely exist on the live server as named.

## Real live DB_PATH note

`superboss-register.py`'s `DB_PATH` resolves to the one real, live, shared production database
(`/opt/veridian/ai-os/memory/superboss-register.sqlite`) regardless of which git checkout it is run
from. Every schema/migration change in this task was first proven against a real, full byte-for-byte
copy of that live database (`cp` to a scratch path, `SUPERBOSS_REGISTER_DB=<scratch>`) before the one
narrow, additive, already-idempotent backfill described in `PROGRESS.md` was run for real against
production -- see that file for the real invocation and real row-count evidence.
