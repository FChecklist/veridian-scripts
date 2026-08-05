# OCID Master Standard v6 — Phase 2: Lifecycle State Machine + Registry Integrity Checks

**Real dispatch:** Owner directive (this task, `task-20260805-131351-merge-veridian-deterministic-ocid-master`)
**Parent references:** `UMR-20260804-170055-a069` (canonical OCID-068 UMR, real status `completed`), `UMR-20260805-032731-b412` (OCID-068 permanent closure record, real status `completed`, PR #52 merge commit `c46da9b777e2a8a60e15230dacd72f2329e885af`)

## What this PR delivers, and why

This is **Phase 2** of the same phasing plan `OCID_MASTER_STANDARD_V6_PHASE1_2026-08-05.md` proposed to Owner/PM after Phase 1 merged (PR #54): *"Phase 2: lifecycle state machine + registry integrity checks."* Same narrow-PR discipline as Phase 1 and as OCID-068's seven guardrail rules (each its own PR: #26, #29, #30, #32, #33, #34, #35) — real, reviewable, independently-tested increments, not one unreviewable mega-PR trying to build the entire directive at once.

### 1. `validate_lifecycle_transition()` + `transition_ocid_lifecycle_state()` — the 11-state lifecycle machine

Locks down the Owner directive's exact 11 states — `created, registered, dispatched, running, testing, pull_request_created, merged, verified, closed, failed, rolled_back` — as `OCID_LIFECYCLE_STATES`, and one real, locked transition table (`OCID_LIFECYCLE_TRANSITIONS`): strict sequential main path, `failed` reachable from any non-terminal state (a real failure can happen at any phase), `rolled_back` reachable only from `failed`, `closed`/`rolled_back` terminal (no outgoing transitions — consistent with "rollback must never delete the UMR or OCID": `rolled_back` is a real terminal state here, never a delete).

This is deliberately a **separate concept** from `umr_tasks.status` (the existing `queued/dispatched/running/...` single-task-dispatch status) — a new `ocid_lifecycle_state` table tracks one real row per OCID, independent of how many `umr_tasks` rows that OCID's retries/redispatches touch.

`transition_ocid_lifecycle_state()` refuses (no DB write, but a durable `lifecycle_transition_refused` audit event) both illegal transitions and — a real, explicit corollary of the Owner directive's "never mint a second UMR for the same unit of work" — an attempt to transition an OCID's lifecycle under a *different* `umr_id` than the one already on record for it.

### 2. `resume_ocid_lifecycle()` — real resume from checkpoint, same OCID+UMR

Pure read of the current `ocid_lifecycle_state` row. A dedicated test (`test_resume_ocid_lifecycle_continues_from_last_checkpoint_same_ocid_and_umr`) drives an OCID through `created → registered → dispatched → running`, "interrupts" (stops calling `transition_ocid_lifecycle_state`), then calls `resume_ocid_lifecycle()` and asserts the real checkpoint (`running`, same `ocid_number`, same `umr_id`) is returned — and that the next transition from there is legal — proving a resume genuinely continues from the last checkpoint rather than restarting from zero or minting a second UMR.

### 3. `check_registry_integrity()` — checksum, foreign keys, orphan rows, duplicate indexes, schema version

Five real, independently-computed booleans, scoped to the OCID Master Standard v6 registry's own tables (`OCID_REGISTRY_INTEGRITY_TABLES`), not this file's much larger general schema:

- `schema_version_ok` — SQLite's own `PRAGMA user_version` equals the locked `OCID_REGISTRY_SCHEMA_VERSION`.
- `checksum_ok` — a real sha256 over the live `sqlite_master` DDL text for the tracked tables/indexes, compared against an explicitly-established baseline (`establish_ocid_registry_schema_baseline()` — never auto-established inside the read-only check itself, same never-auto-apply discipline as `resolve_ocid_canonical()`/`reconcile_umr_status_against_pr()`). No baseline yet is reported as `checksum_ok=False` with an honest explanation, never silently treated as passing.
- `foreign_keys_ok` — `PRAGMA foreign_key_check` (works regardless of whether the connection has `PRAGMA foreign_keys = ON`).
- `orphan_rows_ok` — a real, OCID-specific business-rule check narrower than the generic FK check: compliance/linkage/lifecycle rows for an `ocid_number` with no real `ocid_canonical_registry` row at all.
- `duplicate_index_ok` — two indexes covering the exact same table+column-set among the tracked tables.

### 4. `build_step_result_contract()` — scoped primitive for the mandatory output-contract test requirement

The Owner directive explicitly requires a test that "the JSON output contract forces every step after a failure point to false." This function is that real, minimal, testable primitive: given an ordered list of step names and a dict of real per-step booleans (plus an optional explicit `failed_at`), it forces every step at or after the first real failure to `False` — never silently omitted, never left stale-`True` from an earlier partial run. It is **not** the full standard's strict-JSON-only automated output contract (that needs the full phase sequence, ownership chain, and artifact graph this phase does not implement — building only its JSON shape without the real checks behind it would itself be exactly the kind of unearned, narrated certification this standard exists to prevent).

## Deliberately still deferred — recommendation to Owner/PM, not a unilateral decision

- Ownership-chain resolution to real identities (Owner/PM/dispatcher/supervisor/worker/executor/reviewer/verifier)
- A universal artifact graph with acyclic dependency validation
- Bootstrap phase sequencing with interrupt/checkpoint recovery (this PR's `resume_ocid_lifecycle()` covers the lifecycle-checkpoint piece specifically; the broader bootstrap sequencing does not exist yet)
- Canonical-component discovery/locking as a generic mechanism (registrar discovery, call-chain discovery)
- The full strict-JSON-only output contract for automated runs (this PR delivers the scoped `build_step_result_contract()` primitive only)

**Proposed next phase** (recommendation, not final): Phase 3 — ownership chain + artifact graph + broader checkpoint/resume bootstrap sequencing.

## Real tests

`tests/test_ocid_master_standard_phase2.py` — 20 real tests, all passing, same `importlib.util.spec_from_file_location` + temp-file-SQLite convention as `tests/test_ocid_master_standard_phase1.py`: full legal sequential path, illegal-skip rejection, failure-from-any-active-state, rollback-only-from-failed, terminal-state rejection, initial-transition-must-be-created, real DB write + audit event on legal transitions, real refusal + audit event (no write) on illegal transitions, real refusal on a second-UMR-for-the-same-OCID attempt, a real resume test that reuses the same OCID and UMR and continues from the last checkpoint, all five integrity dimensions (including real drift/orphan/duplicate-index detection cases), and the JSON-output-contract failure-forces-later-steps-false requirement.

Full repo suite: 122 passed (102 pre-existing + 20 new), zero regressions.
