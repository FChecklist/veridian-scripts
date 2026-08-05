# OCID-001..068 Canonical UMR Mapping — Real Methodology Note

**Real dispatch instruction:** `UMR-20260805-032326-becc` (Owner directive)
**Related:** `UMR-20260802-165606-4413` (OCID-020), `UMR-20260802-173631-ca85` (OCID-021)
**Infrastructure PR:** veridian-scripts #53 (`b42a01e7509370aa32565667580054f90277005f`) — `ocid_canonical_registry` table + `upsert_ocid_canonical_registry()`/`query_ocid_canonical_registry()` + `query-ocid-canonical` CLI subcommand, real independent review, merged 2026-08-05.

## What this record is

This documents the real, complete population of the live `ocid_canonical_registry` table in `/opt/veridian/ai-os/memory/superboss-register.sqlite` with all 68 real rows (`OCID-001` through `OCID-068`). The full structured data — canonical UMR, all UMR IDs, status, PR number/repo, duplicate reasoning, not-found flag, evidence — lives in that live table itself (`python3 superboss-register.py query-ocid-canonical` or `query-ocid-canonical --ocid-number OCID-NNN`), not duplicated here. This note records methodology and headline findings only.

## Real search methodology (applied per OCID, in this order)

1. Query the live `umr_tasks` table by `task_identity` substring match, multiple real casings (`ocid-NNN`, `ocid_NNN`, `OCID-NNN`).
2. Full dump + grep of every text column in `umr_tasks` (3,845+ rows) for the OCID string — exact `task_identity` matching alone misses rows where the OCID appears only in `inputs_json` title/prompt text (a real gap independently found this session for OCID-022, OCID-023, OCID-058, OCID-060).
3. `git log --all --oneline -i --grep=OCID-NNN` across fresh clones of `compliance-tracker`, `veridian-scripts`, and `projexa`, used only as a cross-check, never the sole source.
4. `gh pr list --repo <repo> --state all --search "OCID-NNN in:title,body"` across all three repos — commit-message search alone misses real documentation-only PRs.
5. UMR IDs extracted from matched PR body text (regex `UMR-\d{8}-\d{6}-[0-9a-f]{4}`).
6. `ai-os/MASTER-TRACKER.yaml` / `ai-os/boss/ACTIVE-CLAIMS.yaml` (compliance-tracker) grepped as a last resort.
7. Where the live `umr_tasks.status` field contradicted real, independently-confirmed PR-merge evidence for the same UMR, the real merge evidence was treated as authoritative, not the DB status field (this exact bookkeeping-lag pattern was independently found and fixed this same cycle for `UMR-20260805-032731-b412`, OCID-068's own closure-record UMR).

Work was split across 5 parallel research agents (OCID-001–014, 015–028, 029–042, 043–055, 056–068), each running the full protocol above independently, with no cross-agent trust of un-reproduced claims.

## Result

**68 of 68** real rows written, covering the complete OCID-001 through OCID-068 range with no gaps.
- **8 honestly recorded as not_found** after exhausting all real search methods: OCID-007, OCID-008, OCID-009, OCID-010, OCID-011, OCID-012, OCID-013, OCID-014.
- **36 had more than one real UMR ID found**, each recorded in full with an explicit canonical choice and reason (never silently picked).

## Notable real findings surfaced during this research (not resolved here — flagged for Owner awareness)

- **OCID-012**: internally inconsistent in the repo's own history — merged `OS.yaml` text still says "real active work begins at OCID-012", contradicted by a later merged PM-decision commit (`b4a09563`) declaring it never-real. Neither document was reconciled with the other.
- **OCID-013**: the only "evidence" trail (still-open PR #874) contains a real citation error, conflating `OCID-20260802-013` (a date-based Owner-directive ID) with sequential `OCID-013` — independently caught and discounted here; worth correcting in PR #874 before it merges, since it would otherwise seed a false "COMPLETE" entry into the registry.
- **OCID-022 / OCID-023**: each has an unresolved governance-integrity defect — their merged registration PRs (#765, #768) self-minted a fabricated "artifact UMR" that does not exist in `umr_tasks`. Documented in `MASTER-TRACKER.yaml`'s own `GAP-SELF-MINTED-ARTIFACT-UMR-FABRICATION` entry as still open (the analogous OCID-034/PR#779 case was fixed; these two were not).
- **OCID-041 / OCID-042**: the only merged PR touching them (#793) is a placeholder stub (files: `PROGRESS.md` + `IMPLEMENTATION_MATRIX` only); the real discovery documents (PR #799, #800) remain open/unmerged.
- **OCID-050**: a 2026-08-04 document claimed this OCID "never got a UMR" and speculated a collision with OCID-051's UMR. Independently re-checked directly against the live `umr_tasks` table and found **false** — no real collision exists; it was a documentation error, now corrected in this registry.
- **OCID-053 / OCID-054 / OCID-055**: each dispatched for registration 3 times in the same session due to the same `umr_tasks` exact-match false-negative pattern this methodology is designed to guard against. None of the three have ever merged into `main`.
- **Live-deploy gap found and fixed during this task**: `/opt/veridian/scripts/superboss-register.py` (the actual file every cron job and dispatch script runs) was one real merge behind `origin/main` — missing PR #53's `ocid_canonical_registry` table/functions entirely. Fixed via `git pull --ff-only` (clean fast-forward, no local edits at risk) before any data could be written. A stray `OCID-999-TEST`/`UMR-TEST-1` smoke-test row, unrelated to this task, was also found already present in the live table and removed as contaminant cleanup.

## Real citations

- `UMR-20260805-032326-becc` (this task's own dispatch instruction, Owner directive)
- `UMR-20260802-165606-4413` (OCID-020), `UMR-20260802-173631-ca85` (OCID-021) — related master initiatives, discovery/registration only, no new implementation under either lock
- veridian-scripts PR #53 (`b42a01e7`) — the real schema/API/CLI infrastructure this data was written through
