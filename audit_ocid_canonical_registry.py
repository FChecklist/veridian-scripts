#!/usr/bin/env python3
"""audit_ocid_canonical_registry.py -- OCID-068 Phase 2 real, standalone,
independently re-runnable, deterministic audit driver (Owner urgent
correction UMR-20260805-092408-4f97, extending UMR-20260805-090549-9710 /
UMR-20260805-091934-86a2, citing the canonical OCID-068 UMR
UMR-20260804-170055-a069).

Zero AI judgment inside this script: its only logic is (1) iterate over a
real list of OCID numbers, (2) call the already-merged, already-locked
resolve_ocid_canonical() (UMR-20260805-042152-e559) for each -- the one
real, existing, fully mechanical, zero-AI-judgment 6-method search this
codebase already runs, in this exact order:
  (a) real umr_tasks.task_identity substring match, multiple casings
  (b) real full dump + grep of every umr_tasks text column
  (c) real `gh pr list --search "<OCID> in:title,body"` across
      compliance-tracker/veridian-scripts/projexa, --state all
  (d) real `git log --all --oneline -i --grep=<OCID>` across the same 3
      repos, cross-check only
  (e) real UMR ID extraction (regex) from matched PR body/title text
  (f) real MASTER-TRACKER.yaml/ACTIVE-CLAIMS.yaml grep, last resort only
(see resolve_ocid_canonical()'s own docstring in superboss-register.py for
the authoritative description -- this script does not reimplement or
duplicate any of it), and (3) decide, by a single fixed, deterministic rule
applied identically to every OCID (never a per-row judgment call), whether
to overwrite the existing row's canonical_umr_id/status/all_umr_ids/
pr_number/pr_repo/duplicate_reason with this run's fresh result, or
preserve the existing values while still recording the fresh raw evidence.

Fixed merge rule (identical for every OCID, no per-row exception):
  - `not_found` and `audit_raw_output` are ALWAYS overwritten with this
    run's fresh result -- per UMR-20260805-092408-4f97, these two fields'
    real provenance must always trace to the latest real mechanical run,
    never to a stale or hand-typed value.
  - canonical_umr_id/status/all_umr_ids/pr_number/pr_repo/duplicate_reason
    are PRESERVED from the existing row when the existing canonical_umr_id
    is still present in this run's fresh all_umr_ids set (fresh evidence
    still corroborates the prior choice). This avoids silently downgrading
    a real, carefully-reasoned canonical choice (e.g. OCID-068's own is
    explicitly NOT "chronologically earliest UMR" -- see
    OCID_068_GUARDRAIL_RULES_PERMANENT_CLOSURE_2026-08-05.md) to
    resolve_ocid_canonical()'s own simpler automatic "earliest UMR wins"
    default, which that function's own docstring explicitly documents as
    an un-reviewed default, not a considered choice.
  - Otherwise (no existing canonical_umr_id, or the fresh run no longer
    corroborates it, or the row was previously not_found and this run
    found something real and new) this run's fresh result is used in full,
    with an honest, non-silent duplicate_reason note naming the change.

Bounded-storage rule (Owner urgent correction UMR-20260805-161157, added
this task; identical for every OCID, never a per-row judgment call): before
`audit_raw_output`/`evidence` are written, every individual string leaf
value in the fresh evidence dict is passed through `_bounded_for_storage()`
-- a fixed 5000-char cap applied identically regardless of OCID, with the
real original length disclosed whenever a value is capped. This is not
interpretation and not a narrative summary -- every real command actually
run and its real result is still recorded, and every genuine gh/git/grep
result (naturally well under the cap) is untouched byte-for-byte. It exists
solely because real production `umr_tasks` rows were found this task to
carry multi-megabyte `metadata_json` values (a real, separate, pre-existing
data-quality issue in this codebase's task-dedup engine, independently
flagged, not fixed here) that resolve_ocid_canonical()'s method (b) (full
umr_tasks grep) legitimately matches for most real OCID numbers -- storing
those verbatim in full would have added an estimated 1-2+ GB to the live
production database in a single --apply run. See _bounded_for_storage()'s
own docstring below for the full real accounting.

Default is a dry run (prints the real proposed per-OCID plan as JSON,
writes nothing). Pass --apply to actually write, via the real, already-
merged upsert_ocid_canonical_registry() (never raw SQL), inside a real
_write_lock(). --ocid-number limits to a single OCID (debugging / targeted
re-run); default is the full real OCID-001..OCID-069 range.
"""
import argparse
import importlib.util
import json
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ALL_OCID_NUMBERS = [f"OCID-{n:03d}" for n in range(1, 70)]  # OCID-001..OCID-069

# Real operational-safety cap, discovered this task (Owner urgent correction
# UMR-20260805-161157, extending UMR-20260805-092408-4f97 / -091934-86a2 /
# -090549-9710): running this script for real against the live production
# `umr_tasks` table (never exercised against real production data before --
# every prior run of this exact script was either --dry-run or against a
# scratch test DB) surfaced that a handful of real umr_tasks rows (this same
# session's own OCID-068 Phase-2 dispatch/reuse-check rows among them) carry
# a `metadata_json.reuse_check_result` field 6+ MB in size, because the
# real, separate, already-existing task-dedup engine embeds full candidate
# intent text, not just match scores, in that field (a genuine, real,
# pre-existing data-quality issue in that OTHER subsystem, independently
# flagged for Owner awareness below -- out of scope to fix here). Method (b)
# of resolve_ocid_canonical() (real full-table grep, UMR-20260805-... gap
# fix) legitimately matches those rows for a majority of real OCID numbers
# (many of those dispatch texts literally enumerate ranges like "OCID-001
# through OCID-069"), so storing every matched row's FULL text verbatim in
# `audit_raw_output` would have added an estimated 1-2+ GB to the live
# 1.4 GB production superboss-register.sqlite in a single --apply run --
# a real, hard-to-reverse, outward-facing operational risk, not merely a
# cosmetic one.
#
# The fix applied here is a fixed, deterministic, identically-applied-to-
# every-OCID byte cap on individual string leaf values inside the evidence
# dict before it is stored -- NOT interpretation of what the evidence means,
# NOT a narrative summary, NOT a per-OCID judgment call. Every real command
# actually run and its real result are still recorded; only pathologically
# oversized individual matched-row-text values (never the genuine gh/git/
# grep command outputs themselves, which are naturally small) are capped,
# with the real original length disclosed alongside the cap, and the exact
# same real search fully re-runnable on demand (via this same script, or
# directly via resolve_ocid_canonical()) to recover the untruncated value.
_AUDIT_RAW_OUTPUT_LEAF_CHAR_CAP = 5000
_TRUNCATION_MARKER_SENTINEL = "REAL VERBATIM VALUE TRUNCATED FOR STORAGE"


def _bounded_for_storage(value, max_chars=_AUDIT_RAW_OUTPUT_LEAF_CHAR_CAP):
    """Pure, deterministic, zero-AI-judgment recursive cap on string leaf
    values -- same fixed limit applied identically to every OCID, every
    field, every run. Dicts/lists are walked and rebuilt with every string
    value over `max_chars` replaced by a real, disclosed-truncation marker
    naming the real original length; everything else (including every
    string at or under the cap, i.e. every genuine real gh/git/grep command
    result in ordinary practice) passes through byte-for-byte unchanged.
    Deterministic and idempotent: calling this twice on the same input (or
    on its own output) yields the same result, which is what
    tests/test_audit_ocid_canonical_registry.py's determinism proof
    requires."""
    if isinstance(value, str):
        if len(value) <= max_chars or _TRUNCATION_MARKER_SENTINEL in value:
            # Already at/under the cap, OR already carries this function's
            # own truncation marker (re-applying the cap to its own prior
            # output is a real, deliberate no-op -- required for
            # idempotence/determinism across repeated real runs; without
            # this check, the marker text itself would push a
            # freshly-truncated value back over max_chars and get truncated
            # again, differently, each time it is re-bounded).
            return value
        return (
            value[:max_chars]
            + f"...[REAL VERBATIM VALUE TRUNCATED FOR STORAGE: {len(value)} total real chars, "
              f"fixed {max_chars}-char cap applied identically to every OCID by "
              f"audit_ocid_canonical_registry.py's own _bounded_for_storage(); re-run this same "
              f"script, or call resolve_ocid_canonical() directly, for the full untruncated real value]"
        )
    if isinstance(value, dict):
        return {k: _bounded_for_storage(v, max_chars) for k, v in value.items()}
    if isinstance(value, list):
        return [_bounded_for_storage(v, max_chars) for v in value]
    return value


def _load_sbr():
    spec = importlib.util.spec_from_file_location(
        "sbr_audit_ocid", os.path.join(SCRIPTS_DIR, "superboss-register.py")
    )
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def plan_for_ocid(sbr, conn, ocid_number, existing_by_ocid, **resolve_kwargs):
    """Pure (given a real resolve_ocid_canonical() call and the real
    existing row snapshot) decision function -- the one fixed merge rule
    documented above, applied identically to every OCID. Kept separate from
    main() so real tests can call it directly with an injected fake
    `_runner`/`conn`, no live network or live DB required."""
    fresh = sbr.resolve_ocid_canonical(ocid_number, conn, **resolve_kwargs)
    existing = existing_by_ocid.get(ocid_number)

    preserve_canonical = bool(
        existing is not None
        and existing.get("canonical_umr_id")
        and existing["canonical_umr_id"] in fresh["all_umr_ids"]
    )

    if preserve_canonical:
        plan = {
            "ocid_number": ocid_number,
            "canonical_umr_id": existing["canonical_umr_id"],
            "status": existing["status"],
            "all_umr_ids": existing["all_umr_ids"],
            "pr_number": existing["pr_number"],
            "pr_repo": existing["pr_repo"],
            "duplicate_reason": existing["duplicate_reason"],
        }
    else:
        note = fresh.get("duplicate_reason")
        if existing is not None and existing.get("canonical_umr_id"):
            note = (
                f"Real re-audit (UMR-20260805-092408-4f97) no longer corroborates the prior "
                f"canonical_umr_id={existing['canonical_umr_id']!r} in this run's fresh "
                f"all_umr_ids={fresh['all_umr_ids']!r}; using this run's fresh result in full. "
                + (note or "")
            )
        plan = {
            "ocid_number": ocid_number,
            "canonical_umr_id": fresh["canonical_umr_id"],
            "status": fresh["status"],
            "all_umr_ids": fresh["all_umr_ids"],
            "pr_number": fresh["pr_number"],
            "pr_repo": fresh["pr_repo"],
            "duplicate_reason": note,
        }

    bounded_fresh_evidence = _bounded_for_storage(fresh["evidence"])

    plan["not_found"] = fresh["not_found"]
    plan["audit_raw_output"] = bounded_fresh_evidence
    plan["evidence"] = existing["evidence"] if (preserve_canonical and existing is not None) else bounded_fresh_evidence
    plan["preserved_existing_canonical_choice"] = preserve_canonical
    plan["changed_from_existing"] = (
        existing is None
        or bool(existing.get("not_found")) != bool(plan["not_found"])
        or existing.get("canonical_umr_id") != plan["canonical_umr_id"]
    )
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                         help="actually write the re-audited rows; default is a dry run that only prints the plan")
    parser.add_argument("--ocid-number", help="limit to a single real OCID number (debugging / targeted re-run)")
    args = parser.parse_args()

    sbr = _load_sbr()
    conn = sbr._connect()
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_canonical_registry_table(conn)

    ocid_numbers = [args.ocid_number] if args.ocid_number else ALL_OCID_NUMBERS
    existing_rows = sbr.query_ocid_canonical_registry(conn)
    existing_by_ocid = {r["ocid_number"]: r for r in existing_rows}

    plans = []
    for ocid_number in ocid_numbers:
        plan = plan_for_ocid(sbr, conn, ocid_number, existing_by_ocid)
        plans.append(plan)
        print(f"  {ocid_number}: not_found={plan['not_found']} canonical_umr_id={plan['canonical_umr_id']} "
              f"preserved_existing={plan['preserved_existing_canonical_choice']} "
              f"changed={plan['changed_from_existing']}", file=sys.stderr)

    changed = [p for p in plans if p["changed_from_existing"]]
    print(f"SUMMARY: {len(plans)} real OCIDs re-audited | {len(changed)} changed vs existing row "
          f"(not_found flip or canonical_umr_id no longer corroborated by fresh evidence)", file=sys.stderr)
    for p in changed:
        print(f"  CHANGED: {p['ocid_number']} -> canonical_umr_id={p['canonical_umr_id']} not_found={p['not_found']}",
              file=sys.stderr)

    if not args.apply:
        conn.close()
        print(json.dumps(plans, indent=2, default=str))
        print("DRY RUN -- pass --apply to actually write these rows", file=sys.stderr)
        return 0

    with sbr._write_lock():
        for p in plans:
            sbr.upsert_ocid_canonical_registry(
                conn, p["ocid_number"],
                canonical_umr_id=p["canonical_umr_id"], status=p["status"],
                all_umr_ids=p["all_umr_ids"], evidence=p["evidence"],
                pr_number=p["pr_number"], pr_repo=p["pr_repo"],
                duplicate_reason=p["duplicate_reason"], not_found=p["not_found"],
                audit_raw_output=p["audit_raw_output"],
            )
        conn.commit()
    conn.close()
    print(f"APPLIED: wrote {len(plans)} real re-audited rows to ocid_canonical_registry", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
