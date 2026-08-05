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

    plan["not_found"] = fresh["not_found"]
    plan["audit_raw_output"] = fresh["evidence"]
    plan["evidence"] = existing["evidence"] if (preserve_canonical and existing is not None) else fresh["evidence"]
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
