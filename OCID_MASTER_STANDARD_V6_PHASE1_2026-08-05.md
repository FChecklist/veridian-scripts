# OCID Master Standard v6 — Phase 1: Three Real Corrections + Minimal Audit Log

**Real dispatch instruction:** `UMR-20260805-042152-e559` (Owner directive)
**Parent references:** `UMR-20260804-170055-a069` (canonical OCID-068 UMR, real status `completed`), `UMR-20260805-032731-b412` (OCID-068 permanent closure record, real status `completed`, PR #52 merge commit `c46da9b777e2a8a60e15230dacd72f2329e885af`)

## What this PR delivers, and why

The Owner directive UMR-20260805-042152-e559 describes an enormous "VERIDIAN Deterministic OCID Master Standard version six" — an 11-state lifecycle machine, ownership-chain resolution, a universal artifact graph, bootstrap/checkpoint recovery, registry integrity checks, and a strict-JSON-only automated output contract, among other things. Building all of that in one PR would violate this codebase's own established, validated engineering discipline: OCID-068's seven guardrail rules (PR #26, #29, #30, #32, #33, #34, #35) were each their own separate, narrowly-scoped, independently-reviewed PR — small, real, fully-tested increments, never one giant unreviewable PR.

This PR is **Phase 1 only**: three concrete, real corrections, each named in the directive as a real problem hit this session, plus one minimal real append-only audit log.

### 1. `resolve_ocid_canonical()` — locks down the OCID→UMR resolution methodology

Real gap: the ad-hoc OCID-verification methodology used this session missed real matches for **OCID-022, OCID-023, OCID-058, and OCID-060** because a `task_identity`-substring-only search is not sufficient — the OCID string can appear in `outputs_json`/`metadata_json`/`reason`/`logs_ref` instead. This function locks down one canonical implementation, run in strict order: (a) `umr_tasks.task_identity` substring match across casings, (b) a full dump + grep of every `umr_tasks` text column (never (a) alone), (c) `gh pr list --search "<OCID> in:title,body" --state all` across all three repos (catches documentation-only PRs commit-log search misses), (d) `git log --all --grep` as a cross-check only, (e) UMR ID extraction from matched PR bodies, (f) `MASTER-TRACKER.yaml`/`ACTIVE-CLAIMS.yaml` grep as a real last resort. When multiple distinct UMR IDs are found for one OCID, **all** are returned, plus an explicit `canonical_umr_id` choice and `duplicate_reason` — never a silent pick. When nothing is found, `not_found=True` is returned with per-method evidence of the real empty search.

### 2. `reconcile_umr_status_against_pr()` — cross-checks DB status against real PR-merge evidence

Real bug: `UMR-20260805-032731-b412` was found this session stuck at `status='running'`/`ts_completed=null` in the live `umr_tasks` table despite the underlying PR (#52) being genuinely merged on `origin/main` — a real bookkeeping-lag bug, not a narrated one. This function detects exactly that class of staleness and returns a proposed correction (`{is_stale, current_status, proposed_status, proposed_ts_completed, evidence}`) without silently auto-applying it — the caller applies it via the existing `update_umr_task()`, the same pattern `UMR-20260805-024319-b1e6`'s earlier real correction used. A real `status_reconciliation` audit event is recorded whenever staleness is found.

### 3. `refuse_certification_if_merged_without_required_checks()` — independent, redundant refusal logic

Real incident: compliance-tracker PR #932 and PR #933 were both merged while a required status check (`Metadata Index Coverage Check`) was failing, with zero approving reviews — a real branch-protection bypass. `UMR-20260805-034917-33a9` already fixed this going forward at the GitHub-settings level. This function is a second, independent, redundant layer: given an explicit, structured `pr_merge_record` (no live GitHub calls inside the pure function itself), it returns `(False, reason)` for PR #932's and PR #933's real historical facts, and `(True, reason)` when required checks pass and the required review count is met — proven by dedicated test cases for both outcomes, so it is not merely always-refuse.

### Real append-only audit log

`ocid_master_standard_audit_log` (new table, `_ensure_ocid_master_standard_audit_log_table()`, wired into `_migrate_schema()`) — `id, ocid_number, umr_id, event_type, detail_json, recorded_at`. `record_ocid_master_standard_audit_event()` only ever `INSERT`s. Wired from `reconcile_umr_status_against_pr()` (`event_type="status_reconciliation"`) on a real stale-status finding, and from a new `apply_certification_verdict()` caller-side wrapper (`event_type="certification_refused"`) around the certification function.

## No prior "version 5.1" found

A real search (`git log --grep`, `grep -ril`) across fresh clones of compliance-tracker, veridian-scripts, and projexa found no prior "OCID Master Standard version 5.1" document anywhere. This PR does not claim one exists.

## Deliberately deferred — recommendation to Owner/PM, not a unilateral decision

The following real, large, separate systems described in the Owner directive are **explicitly out of scope** for this PR and were not stubbed or silently skipped:

- The full 11-state lifecycle machine (`created → registered → dispatched → running → testing → pull_request_created → merged → verified → closed → failed → rolled_back`) with illegal-transition rejection
- Ownership-chain resolution to real identities (Owner/PM/dispatcher/supervisor/worker/executor/reviewer/verifier)
- A universal artifact graph with acyclic dependency validation
- Bootstrap phase sequencing with interrupt/checkpoint recovery
- Registry integrity checks (checksums, FK, orphans, duplicate indexes, schema version)
- Canonical-component discovery/locking as a generic mechanism
- The mandatory strict-JSON-only output contract for automated runs

**Proposed phasing** (recommendation, not final):

- **Phase 2**: lifecycle state machine + registry integrity checks
- **Phase 3**: ownership chain + artifact graph + checkpoint/resume bootstrap
- **Phase 4**: strict JSON output contract for automated runs

## Real tests

`tests/test_ocid_master_standard_phase1.py` — 11 real tests, all passing, using the same `importlib.util.spec_from_file_location` + temp-file-SQLite convention as `tests/test_ocid_canonical_registry.py`: multi-UMR-found (report all + canonical choice), not-found honest reporting, last-resort-method-skipped-when-unneeded, stale-status detection + proposed correction + real audit event, non-stale no-op, no-merged-PR-evidence no-op, certification refusal for PR #932 and PR #933's real historical facts, certification allowed when checks pass, review-count-only refusal, and audit-event wiring for `apply_certification_verdict()`.
