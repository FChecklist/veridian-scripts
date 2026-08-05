# OCID Canonical Registry "Data Corruption" — False-Alarm Verification — 2026-08-05T17:27–17:35Z

**Relates to:** `UMR-20260804-170055-a069` (OCID-068), `UMR-20260805-090549-9710`,
and the PM's own retraction `UMR-20260805-121654-4b77` / `UMR-20260805-122042-8dbc`.

## Verdict: no data corruption occurred. Nothing was restored, because nothing was wrong.

The SPEC's specific claims — OCID-001 showing OCID-004's UMR; OCID-003/004/005 sharing
`UMR-20260804-162430-d156`; OCID-007/011 sharing `UMR-20260805-091934-86a2`; OCID-012/014
sharing a UMR contradicting their not-real status; OCID-015 showing OCID-003's UMR — do not
match the live `ocid_canonical_registry` table on any of those rows.

## Independent verification performed

1. **Full-table diff, live DB vs. the cited "known-correct" snapshot** (`/tmp/full_roster.json`,
   pulled 2026-08-05T12:14Z, 69 rows): queried the live table directly via
   `superboss-register.py`'s own `query_ocid_canonical_registry()` (never raw SQL), compared
   `canonical_umr_id` and `not_found` for all 69 OCID rows against the snapshot.
   **Result: 0 diffs.** Every row the SPEC named as corrupted — OCID-001, 003, 004, 005, 007,
   011, 012, 014, 015 — already matches the snapshot exactly; none show the claimed
   cross-contaminated values.
2. **Search for the claimed corrupted values anywhere on disk**: `UMR-20260804-162430-d156`
   and `UMR-20260805-091934-86a2` do not appear in the live DB rows for OCID-003/004/005 or
   OCID-007/011 respectively (OCID-060's real, correct, unrelated UMR is
   `UMR-20260804-161339-d586`, and OCID-068's Phase-2 UMR chain includes
   `UMR-20260805-091934-86a2` legitimately in this script's own docstring — plausible sources
   for a garbled recollection, not evidence of a live write).
3. **Code-path review of `audit_ocid_canonical_registry.py`**: `plan_for_ocid()` processes each
   OCID number independently against its own `existing_by_ocid.get(ocid_number)` lookup and its
   own fresh `resolve_ocid_canonical()` call — there is no shared mutable state between OCIDs in
   the loop, so a code path that writes one OCID's fresh result into a *different* OCID's row
   (the "cross-contamination" the SPEC describes) does not exist in this script as written.
   Existing tests (`tests/test_audit_ocid_canonical_registry.py`, 4 tests, all passing before
   this change) already cover the one fixed merge rule (preserve-if-corroborated /
   fresh-if-not / always-refresh-`not_found`/`audit_raw_output`) with no contamination.
4. **A companion task directory found on this same host**,
   `task-20260805-172727-correction--no-real-data-corruption-exis` (created 9 seconds after this
   task, `prompt.txt` dated the same run), is the PM's own retraction: *"the real data corruption
   I reported did not exist, I misread the `audit_ocid_canonical_registry.py` dry-run terminal
   output, the `changed=True` fields in its printed log described a proposed or in-memory
   comparison, not a confirmed live write... Your own independent verification, that the real
   live database matches the real known-correct roster file on every row I named, is the real,
   trusted finding, stand down... no real restore or lock investigation is needed."* This
   independently confirms finding (1) above from the other side of the incident.

## Root cause (the one real, legitimate finding here)

`audit_ocid_canonical_registry.py`'s default dry-run mode prints per-OCID
`changed=True`/`CHANGED: ... -> canonical_umr_id=...` lines to stderr **unprefixed**, identical
in wording to what a real `--apply` write would report, with the actual "DRY RUN — pass --apply
to actually write these rows" disclosure appearing only once, at the very end. A reader who sees
a `CHANGED:` line for an OCID they know about — without reading to the final line, or after the
output scrolled/was piped/grepped — has no way to tell a proposed in-memory comparison from a
confirmed live write. That ambiguity is exactly what produced this SPEC's false alarm.

## Fix applied (`audit_ocid_canonical_registry.py`)

Every stderr line main() prints is now unambiguously tagged `[DRY RUN]` or `[APPLY]` per
invocation — including the per-OCID and `CHANGED:` lines, not just the summary — and the
dry-run summary/CHANGED lines now say "PROPOSED ONLY, NOT YET WRITTEN" / "(proposed only — not
written; pass --apply to write)" inline, so no single line can be misread in isolation. No
behavioral/write-path change: default is still dry-run-only, `--apply` is still required to
write, `_write_lock()` still serializes writes — this is an output-clarity fix only.

## Test added (`tests/test_audit_ocid_canonical_registry.py`)

- `test_dry_run_makes_zero_writes_and_every_output_line_is_unambiguously_labeled`: seeds a row
  whose existing canonical choice is deliberately no longer corroborated (so the run produces a
  real `changed=True`/`CHANGED:` line — the exact case that was misread), runs the real CLI via
  subprocess against an isolated temp DB, and asserts (a) the full row-data snapshot is
  byte-for-byte identical before/after — a real, empirical zero-writes proof, not an assumption —
  and (b) every `changed`/`CHANGED` stderr line is prefixed `[DRY RUN]`.
- `test_apply_actually_writes_and_output_is_labeled_apply_not_dry_run`: confirms the other half —
  `--apply` does write, and its output is labeled `[APPLY]`, never `[DRY RUN]`.

All 6 tests in the file pass (`python3 -m pytest tests/test_audit_ocid_canonical_registry.py -q`
→ `6 passed`), as do the related `tests/test_ocid_canonical_registry.py` (11 passed together).

## Rows the SPEC claimed were corrupted — confirmed never corrupted

| OCID | SPEC's claim | Live value (unchanged throughout) |
|------|-----|-----|
| OCID-001 | shows OCID-004's UMR | `UMR-20260802-034545-3388` (its own, correct) |
| OCID-003 | shares `UMR-20260804-162430-d156` with 004/005 | `UMR-20260802-054239-4251` (its own, correct) |
| OCID-004 | shares `UMR-20260804-162430-d156` with 003/005 | `UMR-20260802-104058-25ba` (its own, correct) |
| OCID-005 | shares `UMR-20260804-162430-d156` with 003/004 | `UMR-20260802-105532-775a` (its own, correct) |
| OCID-007 | shares `UMR-20260805-091934-86a2` with 011 | `not_found=1`, no UMR (its own, correct) |
| OCID-011 | shares `UMR-20260805-091934-86a2` with 007 | `not_found=1`, no UMR (its own, correct) |
| OCID-012 | shares a UMR with 014, contradicting not-real status | `not_found=1`, confirmed not real (correct) |
| OCID-014 | shares a UMR with 012 | `not_found=1`, confirmed not real (correct) |
| OCID-015 | shows OCID-003's UMR | `UMR-20260802-164801-2ab9` (its own, correct) |

No restore action was performed — there was nothing to restore. `audit_ocid_canonical_registry.py`
remains safe to run (default dry-run unchanged); the output-clarity fix above is the only change.
