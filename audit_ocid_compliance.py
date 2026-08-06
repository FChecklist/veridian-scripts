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

--report (Owner directive UMR-20260805-093138-2bd0's real report command,
closing the anti-fabrication gap named by UMR-20260805-092408-4f97): a
completely separate, read-only mode. It runs NO audit, calls NO gh/git
subprocess, and writes NOTHING -- it only calls the real, already-trigger-
computed query_ocid_compliance_state() in superboss-register.py and prints
its own real stored state verbatim. Every boolean it prints was already
computed, deterministically, by the ocid_compliance_state_derive_ai/_au
triggers at the time ocid_compliance_audit_log's raw evidence was written --
never re-derived, interpreted, or summarized by this script or by whoever
runs it. The intended real usage: a PM (human or AI) runs
`audit_ocid_compliance.py --report` and reports its exact stdout, performing
no analysis of its own on this data ever again -- see
tests/test_audit_ocid_compliance_report.py for the automated proof that two
separate real runs of --report against the same real unchanged data produce
byte-identical output.
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


def build_compliance_report(state_rows, rule_fields):
    """Pure function (no conn, no I/O) so real tests can call it directly
    against a fixed, hand-built `state_rows` list with zero DB/subprocess
    dependency -- same testability-seam convention as plan_for_ocid() in
    audit_ocid_canonical_registry.py. Builds the exact real, deterministic
    report structure `--report` prints: every field here is copied straight
    from the already-trigger-computed state_rows (query_ocid_compliance_state()'s
    own output) -- this function computes only plain aggregate counts over
    those already-real booleans, never a new judgment about any individual
    row."""
    rows = []
    for r in state_rows:
        rows.append({
            "ocid_number": r["ocid_number"],
            "umr_id": r["umr_id"],
            **{f: bool(r[f]) for f in rule_fields},
            "audit_done": bool(r["audit_done"]),
            "audit_passed": bool(r["audit_passed"]),
            "last_audit_timestamp": r["last_audit_timestamp"],
            "file_path": r["file_path"],
        })
    rule_true_counts = {f: sum(1 for r in rows if r[f]) for f in rule_fields}
    return {
        "mode": "report",
        "read_only": True,
        "wrote_to_database": False,
        "note": (
            "Every boolean below was already computed, deterministically, by the "
            "ocid_compliance_state_derive_ai/_au SQLite triggers at the time it was "
            "written -- this report re-derives nothing and re-runs no audit."
        ),
        "rows": rows,
        "summary": {
            "total_pairs": len(rows),
            "audited_pairs": sum(1 for r in rows if r["audit_done"]),
            "fully_compliant_pairs": sum(1 for r in rows if r["audit_passed"]),
            "rule_true_counts": rule_true_counts,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                         help="actually write the real compliance-state + audit-log rows; default is a dry run")
    parser.add_argument("--report", action="store_true",
                         help="read-only: print the real current ocid_compliance_state analysis exactly as "
                              "already stored (no audit run, no gh/git calls, no writes) -- the PM runs this "
                              "flag and reports its output verbatim, performing no analysis itself")
    parser.add_argument("--ocid-number", help="limit to a single real OCID number (debugging / targeted re-run)")
    args = parser.parse_args()

    sbr = _load_sbr()
    conn = sbr._connect()
    sbr._ensure_umr_table(conn)
    sbr._ensure_ocid_canonical_registry_table(conn)
    sbr._ensure_ocid_artifact_links_table(conn)
    sbr._ensure_ocid_compliance_tables(conn)

    if args.report:
        state_rows = sbr.query_ocid_compliance_state(conn, ocid_number=args.ocid_number)
        conn.close()
        report = build_compliance_report(state_rows, sbr.OCID_COMPLIANCE_STATE_RULE_FIELDS)
        print(json.dumps(report, indent=2, default=str))
        print(
            f"[REPORT MODE -- READ-ONLY, NOTHING WRITTEN] {report['summary']['total_pairs']} real pairs | "
            f"{report['summary']['fully_compliant_pairs']} fully compliant (all 7 rules true) | "
            f"{report['summary']['total_pairs'] - report['summary']['fully_compliant_pairs']} not fully compliant",
            file=sys.stderr,
        )
        return 0

    mode = "APPLY" if args.apply else "DRY_RUN"
    print(f"[{mode}] this run "
          f"{'WILL WRITE to the real database' if args.apply else 'WILL NOT WRITE anything'}", file=sys.stderr)

    canonical_rows = sbr.query_ocid_canonical_registry(conn, ocid_number=args.ocid_number)
    pairs = plan_pairs(canonical_rows)
    print(f"[{mode}] Planning real compliance audit for {len(pairs)} real (ocid,umr) pairs across "
          f"{len(canonical_rows)} real OCID row(s)", file=sys.stderr)

    if not args.apply:
        preview = []
        for ocid_number, umr_id, _all_umr_ids, _row in pairs:
            umr_row = conn.execute("SELECT umr_id FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
            preview.append({"ocid_number": ocid_number, "umr_id": umr_id, "real_umr_tasks_row_exists": umr_row is not None})
        conn.close()
        print(json.dumps({"mode": "dry_run", "wrote_to_database": False, "pairs": preview}, indent=2))
        found = sum(1 for p in preview if p["real_umr_tasks_row_exists"])
        print(f"[{mode}] DRY RUN SUMMARY: {len(preview)} real pairs planned | {found} have a real umr_tasks row | "
              f"{len(preview) - found} do not (rule checks needing that row will honestly record None/indeterminate)",
              file=sys.stderr)
        print(f"[{mode}] DRY RUN COMPLETE -- NOTHING WAS WRITTEN. Pass --apply to actually write.", file=sys.stderr)
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

    print(json.dumps({"mode": "apply", "wrote_to_database": True, "results": results}, indent=2, default=str))
    print(f"[{mode}] APPLY COMPLETE -- {audited} real (ocid,umr) pairs WERE WRITTEN | "
          f"{passed} fully compliant (all 7 rules true) | "
          f"{audited - passed} not fully compliant", file=sys.stderr)
    for rule in sbr.OCID_COMPLIANCE_STATE_RULE_FIELDS:
        print(f"[{mode}]   {rule}: {rule_true_counts[rule]} true / {rule_mechanism_not_existed_counts[rule]} false "
              f"(out of {audited})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
