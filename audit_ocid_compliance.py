#!/usr/bin/env python3
"""audit_ocid_compliance.py -- real batch driver for the OCID-068 seven-rule
compliance tracking tables (Owner directive UMR-20260805-093138-2bd0, scope
clarification UMR-20260805-093254-056e, citing the canonical OCID-068 UMR
UMR-20260804-170055-a069). Tables named `ocid_compliance_state` /
`ocid_compliance_audit_log` -- renamed from the originally-requested
`ocid_068_compliance_state`/`ocid_068_compliance_audit_log` per
UMR-20260805-093254-056e's own explicit authorization to rename once real
row coverage became the full OCID-001..069 roster, not OCID-068 alone;
this script's own filename follows the same rename, disclosed here plainly.

For every real OCID number already in ocid_canonical_registry and every
real UMR associated with it (canonical_umr_id plus every id in
all_umr_ids_json), calls the real, already-built run_ocid_compliance_audit()
in superboss-register.py -- zero additional AI judgment in this script
beyond the fixed rule-check functions that function already calls -- and
writes both real tables together, in one real transaction per pair.

Default is a dry run (reports the real planned (ocid, umr) pairs, writes
nothing). Pass --apply to actually write, inside a real _write_lock().
--ocid-number limits to a single real OCID (debugging / targeted re-run).
"""
import argparse
import importlib.util
import json
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_sbr():
    spec = importlib.util.spec_from_file_location(
        "sbr_audit_ocid_compliance", os.path.join(SCRIPTS_DIR, "superboss-register.py")
    )
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def plan_pairs(canonical_rows):
    """Real, deterministic (ocid_number, umr_id) pair enumeration: every
    real id in all_umr_ids_json, plus canonical_umr_id if somehow not
    already among them (defensive; upsert_ocid_canonical_registry always
    includes it in practice). Rows with genuinely zero real UMR ids (the 8
    real not_found rows, plus any not-yet-linked row) are honestly skipped
    -- there is no real UMR to audit rule compliance for."""
    pairs = []
    for row in canonical_rows:
        all_umr_ids = list(row.get("all_umr_ids") or [])
        canonical = row.get("canonical_umr_id")
        if canonical and canonical not in all_umr_ids:
            all_umr_ids.append(canonical)
        if not all_umr_ids:
            continue
        for umr_id in all_umr_ids:
            pairs.append((row["ocid_number"], umr_id, all_umr_ids, row))
    return pairs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                         help="actually write the real compliance-state + audit-log rows; default is a dry run")
    parser.add_argument("--ocid-number", help="limit to a single real OCID number (debugging / targeted re-run)")
    args = parser.parse_args()

    sbr = _load_sbr()
    conn = sbr._connect()
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_canonical_registry_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    sbr._ensure_ocid_compliance_tables(conn)

    canonical_rows = sbr.query_ocid_canonical_registry(conn, ocid_number=args.ocid_number)
    pairs = plan_pairs(canonical_rows)
    print(f"Planning real compliance audit for {len(pairs)} real (ocid,umr) pairs across "
          f"{len(canonical_rows)} real OCID row(s)", file=sys.stderr)

    if not args.apply:
        preview = []
        for ocid_number, umr_id, _all_umr_ids, _row in pairs:
            umr_row = conn.execute("SELECT umr_id FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
            preview.append({"ocid_number": ocid_number, "umr_id": umr_id, "real_umr_tasks_row_exists": umr_row is not None})
        conn.close()
        print(json.dumps(preview, indent=2))
        found = sum(1 for p in preview if p["real_umr_tasks_row_exists"])
        print(f"DRY RUN SUMMARY: {len(preview)} real pairs planned | {found} have a real umr_tasks row | "
              f"{len(preview) - found} do not (rule checks needing that row will honestly record None/indeterminate)",
              file=sys.stderr)
        print("DRY RUN -- pass --apply to actually write", file=sys.stderr)
        return 0

    results = []
    with sbr._write_lock():
        for ocid_number, umr_id, all_umr_ids, canonical_row in pairs:
            r = sbr.run_ocid_compliance_audit(
                conn, ocid_number, umr_id, all_umr_ids=all_umr_ids,
                canonical_row=canonical_row, audited_by="audit_ocid_compliance.py",
            )
            results.append(r)
        conn.commit()
    conn.close()

    audited = len(results)
    passed = sum(1 for r in results if r["audit_passed"])
    rule_true_counts = {rule: sum(1 for r in results if r["results"][rule] is True)
                         for rule in sbr.OCID_COMPLIANCE_STATE_RULE_FIELDS}
    rule_mechanism_not_existed_counts = {
        rule: sum(1 for r in results if r["results"][rule] is False) for rule in sbr.OCID_COMPLIANCE_STATE_RULE_FIELDS
    }

    print(json.dumps(results, indent=2, default=str))
    print(f"APPLIED: {audited} real (ocid,umr) pairs audited | {passed} fully compliant (all 7 rules true) | "
          f"{audited - passed} not fully compliant", file=sys.stderr)
    for rule in sbr.OCID_COMPLIANCE_STATE_RULE_FIELDS:
        print(f"  {rule}: {rule_true_counts[rule]} true / {rule_mechanism_not_existed_counts[rule]} false "
              f"(out of {audited})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
