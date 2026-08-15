#!/usr/bin/env python3
"""gtm_part34_certification_check.py -- real, live, single-round-trip
gap-and-certify check for OCID-020's gtm_certification_categories table (the
"Part3+4 GTM certification" scope named by the 2026-08-15 Owner directive,
see pm-sentinel-tick.sh's own new Check 4 for the full governing chain).

Deliberately reuses existing infrastructure instead of inventing a new
table/writer, same convention gtm_write_category_result.py already
established for this table (importlib-load superboss-register.py rather than
duplicating its DB-path resolution / write-lock / connection discipline):
  - superboss-register.py's own _connect()/resolve_superboss_db_path() for
    the real gtm_certification_categories read (SUPERBOSS_REGISTER_DB env
    override honored automatically, same testability seam every other real
    query in this codebase already uses).
  - the EXISTING ocid_master_standard_audit_log table (already real, live,
    append-only, already used for real status_reconciliation/
    certification_refused events elsewhere in superboss-register.py) as the
    completion-certificate's one fixed real path -- via
    record_ocid_master_standard_audit_event(), never a new table.

One call does both the read AND the conditional write in the SAME
connection/process, specifically so a completion certificate can never be
written against a stale prior read (no separate query-then-certify
round-trip for the caller to race against real work landing in between):
  1. real live query: every gtm_certification_categories row where
     ocid_number='OCID-020' and (passed=0 OR passed IS NULL) is a real gap
     row. Never hardcodes an expected count or category list -- always
     re-queried live (see prompt.txt SPEC point 1: the 2026-08-15 count of 9
     will change as real work lands, and did -- 7 real gap rows as of this
     integration's own verification, see PROGRESS.md).
  2. if any real gap row exists: prints the real gap count + real gap rows,
     writes nothing. The caller (pm-sentinel-tick.sh Check 4) is the one
     that decides whether to dispatch a real fix, through the file's own
     existing dispatch_gap()/DISPATCH_OWNER_TASK_SH gateway -- this script
     never dispatches anything itself.
  3. if zero real gap rows: independently re-verifies every passed=1 row's
     own evidence_summary is real (non-empty, non-placeholder) -- SPEC point
     4's hard requirement, "never accept passed=1 with empty evidence as
     real". Only when that also holds, AND no prior real certificate for
     this exact scope already exists in ocid_master_standard_audit_log
     (idempotent -- a genuinely all-clear state re-queried every ~10min
     tick must not spam a fresh audit-log row every single tick), writes
     ONE real, timestamped, evidence-citing completion record and reports
     certified=true. Every other real outcome (gap rows remain, evidence
     missing, already certified) reports certified=false with a real,
     specific `reason` -- never silently ambiguous.

Usage:
    python3 gtm_part34_certification_check.py [--sbr-path PATH]

Exit code is always 0 for a real, completed check (including a real
certified=false outcome -- that is a real, honest, non-error disposition,
not a tooling failure). Exit code 1 only for a genuine tooling failure
(DB unreadable, table missing, etc.) -- the caller must fail closed on that,
never treat it as "zero gaps found"."""
import argparse
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone

DEFAULT_SBR_PATH = "/opt/veridian/scripts/superboss-register.py"
OCID_NUMBER = "OCID-020"
CERT_EVENT_TYPE = "gtm_part3_4_certification_complete"
EVIDENCE_EXCERPT_LEN = 200

# Same shape as ddl_authorization_check.py's own PLACEHOLDER_REFERENCE_RE
# (that module's domain is unrelated -- DDL-authorization citations, not GTM
# evidence -- so this is a small, self-contained equivalent rather than a
# cross-module import into an unrelated check script) -- a bare token like
# "tbd"/"n/a"/"pending" is exactly the "empty or placeholder citation" shape
# SPEC point 4 says must never be accepted as real evidence.
PLACEHOLDER_EVIDENCE_RE = re.compile(
    r"^(tbd|todo|n/?a|none|null|undefined|pending|xxx+|\.\.\.|fill.?in|<.*>)$",
    re.IGNORECASE,
)


def load_sbr(sbr_path):
    spec = importlib.util.spec_from_file_location("superboss_register", sbr_path)
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def has_real_evidence(evidence_summary):
    if not evidence_summary or not evidence_summary.strip():
        return False
    return not PLACEHOLDER_EVIDENCE_RE.match(evidence_summary.strip())


def query_gap_state(conn):
    """Real, live query -- never hardcodes the category list or expected
    count (SPEC point 1)."""
    rows = [dict(r) for r in conn.execute(
        "SELECT category_index, category_name, ocid_number, passed, evidence_summary "
        "FROM gtm_certification_categories WHERE ocid_number = ? ORDER BY category_index",
        (OCID_NUMBER,),
    ).fetchall()]
    gap_rows = [r for r in rows if r["passed"] == 0 or r["passed"] is None]
    passed_rows = [r for r in rows if r["passed"] == 1]
    unevidenced_rows = [r for r in passed_rows if not has_real_evidence(r["evidence_summary"])]
    return {
        "total_rows": len(rows),
        "gap_count": len(gap_rows),
        "gap_rows": [
            {"category_index": r["category_index"], "category_name": r["category_name"], "passed": r["passed"]}
            for r in gap_rows
        ],
        "passed_count": len(passed_rows),
        "unevidenced_count": len(unevidenced_rows),
        "unevidenced_rows": [
            {"category_index": r["category_index"], "category_name": r["category_name"]}
            for r in unevidenced_rows
        ],
        "evidenced_rows": [
            {"category_index": r["category_index"], "category_name": r["category_name"],
             "evidence_summary": (r["evidence_summary"] or "")[:EVIDENCE_EXCERPT_LEN]}
            for r in passed_rows
        ],
    }


def already_certified(sbr, conn):
    """Idempotency guard: a prior real certificate for this exact scope
    already sitting in ocid_master_standard_audit_log means this genuinely
    all-clear state was already certified on an earlier tick -- writing a
    fresh row every ~10min tick forever would be real audit-log spam, not a
    new real fact. Returns the prior row's real recorded_at, or None."""
    sbr._ensure_ocid_master_standard_audit_log_table(conn)
    row = conn.execute(
        "SELECT recorded_at FROM ocid_master_standard_audit_log "
        "WHERE event_type = ? AND ocid_number = ? ORDER BY id DESC LIMIT 1",
        (CERT_EVENT_TYPE, OCID_NUMBER),
    ).fetchone()
    return dict(row)["recorded_at"] if row else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sbr-path", default=DEFAULT_SBR_PATH,
                     help="real superboss-register.py path to importlib-load "
                          "(default: the canonical live path; pm-sentinel-tick.sh "
                          "passes its own already-resolved SUPERBOSS_REGISTER_PY "
                          "here so both stay consistent)")
    args = ap.parse_args()

    try:
        sbr = load_sbr(args.sbr_path)
        conn = sbr._connect()
    except Exception as e:
        print(json.dumps({"error": f"could not load/connect via {args.sbr_path}: {e}"}))
        sys.exit(1)

    try:
        state = query_gap_state(conn)
    except Exception as e:
        conn.close()
        print(json.dumps({"error": f"gtm_certification_categories query failed: {e}"}))
        sys.exit(1)

    if state["gap_count"] != 0:
        conn.close()
        print(json.dumps({"certified": False, "reason": f"{state['gap_count']} real gap row(s) remain", **state}))
        return

    if state["unevidenced_count"] != 0:
        conn.close()
        print(json.dumps({
            "certified": False,
            "reason": f"{state['unevidenced_count']} passed=1 row(s) have empty/placeholder evidence_summary "
                      "-- never accepted as real evidence",
            **state,
        }))
        return

    prior_ts = already_certified(sbr, conn)
    if prior_ts:
        conn.close()
        print(json.dumps({
            "certified": False,
            "reason": f"already certified previously (idempotent -- see prior "
                      f"{CERT_EVENT_TYPE} row recorded_at={prior_ts})",
            "already_certified_at": prior_ts,
            **state,
        }))
        return

    governing_umr = sbr._lookup_ocid_governing_umr(conn, OCID_NUMBER)
    now = datetime.now(timezone.utc).isoformat()
    detail = {
        "ocid_number": OCID_NUMBER,
        "scope": "Part3+4 GTM certification",
        "certified_at": now,
        "certified_by": "pm-sentinel-tick.sh Check 4 -> gtm_part34_certification_check.py",
        "total_rows": state["total_rows"],
        "passed_count": state["passed_count"],
        "gap_count": state["gap_count"],
        # Real evidence citing the real query results that proved zero gaps
        # (SPEC point 4) -- every passed=1 row's own real evidence_summary
        # excerpt, not merely a claimed count.
        "evidenced_rows": state["evidenced_rows"],
    }
    try:
        with sbr._write_lock():
            sbr.record_ocid_master_standard_audit_event(
                conn, CERT_EVENT_TYPE, detail, ocid_number=OCID_NUMBER, umr_id=governing_umr,
            )
            conn.commit()
    except Exception as e:
        conn.close()
        print(json.dumps({"error": f"real completion-certificate write failed: {e}"}))
        sys.exit(1)
    conn.close()
    print(json.dumps({"certified": True, "detail": detail, **state}))


if __name__ == "__main__":
    main()
