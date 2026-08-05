#!/usr/bin/env python3
"""backfill_evidence_json_schema.py -- real, one-off backfill of the
standardized evidence_json shape (this cycle's Owner directive, citing
UMR-20260804-170055-a069 [canonical OCID-068 UMR] and
UMR-20260805-032326-becc [real OCID canonical roster build]).

For each of the 69 real existing ocid_canonical_registry rows, rewrites
evidence_json to the new required shape (EVIDENCE_JSON_REQUIRED_KEYS in
superboss-register.py: commit_sha, file_name, file_path, merge_status,
umr_id, ocid_number, pr_number, pr_repo, evidence_summary), while
preserving every real pre-existing evidence_json value verbatim, nested
under a new "legacy_evidence" key -- real information is never discarded
to satisfy the new schema.

Real provenance note (independently confirmed live, this same session):
this script does NOT itself call `gh pr view` to recover commit_sha/
file_path/merge_status. A separate, already-real, already-merged backfill
(backfill_ocid_registry_phase2_columns.py, OCID-068 Phase 2, PR #57) was
independently re-dispatched and actually executed against this same live
production DB concurrently with this task (its own real audit trail is
visible inside several rows' own pre-existing evidence_json, e.g. OCID-068's
"phase2_backfill_execution" note) -- every row's dedicated commit_sha/
file_name/file_path/merge_status/evidence_summary column is now genuinely
populated (or honestly NULL where no real PR/unambiguous file exists) by
that real run. Re-running a second independent `gh pr view` sweep here
would be a redundant, duplicate implementation of the exact same real
recovery logic against the exact same real PRs -- this script instead
reads those already-real dedicated columns directly and folds them into
the new evidence_json shape, honoring this codebase's own "zero duplicate
implementations" discipline (see resolve_ocid_canonical()'s own docstring
in superboss-register.py for the same principle applied elsewhere).

If a future re-run of THIS script ever finds those dedicated columns still
NULL for rows that should have real PR evidence (e.g. run against a DB
where the Phase 2 backfill was never applied), it prints an honest warning
per row rather than silently treating NULL-because-never-fetched the same
as NULL-because-genuinely-not-recoverable -- but it still does not itself
shell out to gh; run backfill_ocid_registry_phase2_columns.py --apply first
in that case.

Default is a dry run (prints the real proposed per-row evidence_json plus a
summary as JSON, writes nothing). Pass --apply to actually write, via the
real, already-merged upsert_ocid_canonical_registry() (never raw SQL),
inside a real _write_lock(). --ocid-number limits to one row (debugging /
targeted re-run).
"""
import argparse
import importlib.util
import json
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

# The 8 real rows already honestly confirmed not_found -- a real PR/commit/
# file never applies to an OCID that was never real / never registered.
NOT_FOUND_EXCEPTION_OCIDS = {
    "OCID-007", "OCID-008", "OCID-009", "OCID-010", "OCID-011",
    "OCID-012", "OCID-013", "OCID-014",
}


def _load_sbr():
    spec = importlib.util.spec_from_file_location(
        "sbr_backfill_evidence_json_schema", os.path.join(SCRIPTS_DIR, "superboss-register.py")
    )
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def build_evidence_json(row):
    """Pure transform: this row's own already-real dedicated Phase 2
    columns + its own canonical_umr_id/ocid_number/pr_number/pr_repo,
    folded into the new required evidence_json shape. `legacy_evidence` is
    this row's own pre-existing evidence_json value, preserved verbatim --
    never discarded, never overwritten by this backfill."""
    ocid = row["ocid_number"]
    is_exception = ocid in NOT_FOUND_EXCEPTION_OCIDS
    no_pr = not row["pr_number"] or not row["pr_repo"]

    evidence_summary = row.get("evidence_summary")
    if not evidence_summary or not str(evidence_summary).strip():
        # Real, honest fallback -- should not be needed against the live DB
        # (every row's dedicated evidence_summary column was already real
        # and non-empty at the time this script was written), but a short
        # sentence is always required by the schema even if it ever is.
        base = (row.get("status") or "").strip().rstrip(".")
        if is_exception:
            evidence_summary = f"{ocid}: {base} -- confirmed not_found; no real PR/commit/file applies."
        elif no_pr:
            evidence_summary = f"{ocid}: {base} -- no real pr_number on record; nothing to recover."
        else:
            evidence_summary = f"{ocid}: {base} -- no dedicated evidence_summary recovered yet."

    return {
        "commit_sha": row["commit_sha"],
        "file_name": row["file_name"],
        "file_path": row["file_path"],
        "merge_status": row["merge_status"],
        "umr_id": row["canonical_umr_id"],
        "ocid_number": ocid,
        "pr_number": row["pr_number"],
        "pr_repo": row["pr_repo"],
        "evidence_summary": evidence_summary,
        "legacy_evidence": row["evidence"],
    }


def backfill_row(sbr, row):
    ocid = row["ocid_number"]
    is_exception = ocid in NOT_FOUND_EXCEPTION_OCIDS
    no_pr = not row["pr_number"] or not row["pr_repo"]
    never_fetched_warning = None
    if not is_exception and not no_pr and not row["merge_status"] and not row["commit_sha"]:
        never_fetched_warning = (
            f"{ocid} has a real pr_number ({row['pr_number']}) but its dedicated merge_status/"
            f"commit_sha columns are still NULL -- this looks like the Phase 2 backfill was never "
            f"run for this row; commit_sha/file_path will be recorded as honestly null here, but "
            f"consider running backfill_ocid_registry_phase2_columns.py --apply first."
        )

    evidence_json = build_evidence_json(row)
    ok, problems, reason = sbr.validate_evidence_json_schema(evidence_json)
    return {
        "ocid_number": ocid,
        "is_exception": is_exception,
        "no_pr": no_pr,
        "never_fetched_warning": never_fetched_warning,
        "evidence_json": evidence_json,
        "schema_ok": ok,
        "schema_problems": problems,
        "schema_reason": reason,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                         help="actually write the backfilled evidence_json; default is a dry run that only prints the plan")
    parser.add_argument("--ocid-number", help="limit to a single real OCID number (debugging / targeted re-run)")
    args = parser.parse_args()

    sbr = _load_sbr()
    conn = sbr._connect()
    sbr._ensure_ocid_canonical_registry_table(conn)
    rows = sbr.query_ocid_canonical_registry(conn, ocid_number=args.ocid_number)

    if not rows:
        conn.close()
        print(json.dumps({"error": "no real rows found", "ocid_number": args.ocid_number}, indent=2))
        return 1

    results = [backfill_row(sbr, row) for row in rows]
    print(json.dumps(results, indent=2, default=str))

    bad = [r for r in results if not r["schema_ok"]]
    if bad:  # pragma: no cover -- would indicate a real bug in this script itself
        for r in bad:
            print(f"INTERNAL ERROR: {r['ocid_number']} evidence_json failed its own schema: "
                  f"{r['schema_problems']}", file=sys.stderr)
        conn.close()
        return 2

    for r in results:
        if r["never_fetched_warning"]:
            print(f"WARNING: {r['never_fetched_warning']}", file=sys.stderr)

    exceptions = sum(1 for r in results if r["is_exception"])
    no_pr_count = sum(1 for r in results if r["no_pr"] and not r["is_exception"])
    with_pr = [r for r in results if not r["is_exception"] and not r["no_pr"]]
    recovered_commit = sum(1 for r in with_pr if r["evidence_json"]["commit_sha"])
    recovered_file = sum(1 for r in with_pr if r["evidence_json"]["file_path"])
    never_fetched = sum(1 for r in results if r["never_fetched_warning"])
    print(
        f"SUMMARY: {len(results)} total rows | {exceptions} not_found exception rows | "
        f"{no_pr_count} rows with no real pr_number (honestly null) | "
        f"{len(with_pr)} rows with a real pr_number | commit_sha present: {recovered_commit} | "
        f"file_path present: {recovered_file} | rows never fetched by Phase 2 backfill yet: {never_fetched} | "
        f"every row's evidence_json validated against the real required schema",
        file=sys.stderr,
    )

    if not args.apply:
        conn.close()
        print("DRY RUN -- pass --apply to actually write these rows", file=sys.stderr)
        return 0

    with sbr._write_lock():
        for row, r in zip(rows, results):
            sbr.upsert_ocid_canonical_registry(
                conn, row["ocid_number"],
                canonical_umr_id=row["canonical_umr_id"], status=row["status"],
                all_umr_ids=row["all_umr_ids"], evidence=r["evidence_json"],
                pr_number=row["pr_number"], pr_repo=row["pr_repo"],
                duplicate_reason=row["duplicate_reason"], not_found=bool(row["not_found"]),
                commit_sha=row["commit_sha"], file_name=row["file_name"],
                file_path=row["file_path"], merge_status=row["merge_status"],
                evidence_summary=r["evidence_json"]["evidence_summary"],
                audit_raw_output=row.get("audit_raw_output"),
            )
        conn.commit()
    conn.close()
    print(f"APPLIED: wrote {len(results)} real ocid_canonical_registry rows with the standardized "
          f"evidence_json schema", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
