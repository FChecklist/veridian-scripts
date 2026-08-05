#!/usr/bin/env python3
"""gtm_check_production_readiness_audit.py -- real, re-runnable check for
GTM certification category_index=25 ("production readiness audit").

Per the dispatching task's own explicit instruction, this is built and run
LAST, after every other one of the 24 categories: it is NOT an independent
check of its own (no new tool, no new probe) -- it is a real synthesis
that reads the other 24 categories' own already-computed rows straight out
of `gtm_certification_categories` and aggregates them into an overall
readiness picture (pass/fail/blocked counts, and a P0-P3-style severity
breakdown).

Severity tiers (this script's own documented, fixed rubric -- not an
external Owner-provided standard, and not adjustable at call time; exists
so the aggregate below means something more than a flat pass-rate):
  P0 (mission-critical -- a real fail or block here means the product is
      not fit to ship): security audit(3), API testing(4), database
      testing(12), governance testing(14), backup and recovery testing(19),
      deployment testing(21).
  P1 (release-quality gates): architecture audit(1), static code
      analysis(2), UI testing(5), end to end testing(6), regression
      testing(7), performance testing(9), multi tenant testing(15), role
      permission testing(16), monitoring testing(20).
  P2 (quality/polish, not release-blocking on their own): accessibility
      testing(8), browser compatibility(17), responsive testing(18),
      documentation audit(22), lighthouse audit(24).
  P3 (deferred/exempted by standing PM instruction, not part of this
      task's scope, not counted against readiness): load testing(10),
      stress testing(11), AI testing(13), UX audit(23).

Pass bar (documented, fixed, not adjustable at call time):
  PASS <=> every real P0 category has passed=1 (zero P0 fails, zero P0
           blocked/pending) AND zero real P1 fails (P1 blocked/pending is
           tolerated -- it means "not yet checked", not "checked and
           broken" -- but a genuine P1 FAIL is not).
  Any real P0 category that is fail or blocked, or any real P1 fail, is a
  genuine FAIL for category 25 -- an honest "not production-ready yet",
  which is the real, correct synthesis given this session's own actual
  findings (see evidence_json). "blocked" is reserved for: the
  `gtm_certification_categories` table itself being unreadable/absent, or
  fewer than 24 of the other categories having ever been run at all (too
  little real data to synthesize anything from).

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=25's result -- this script's own
DB access for reading the other 24 rows is read-only (plain SELECT via
superboss-register.py's own `_connect()`, no write, no lock needed for a
read).

Usage:
  gtm_check_production_readiness_audit.py
"""
import importlib.util
import json
import os
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WRITER = os.path.join(SCRIPTS_DIR, "gtm_write_category_result.py")
CATEGORY_INDEX = 25
SBR_PATH = "/opt/veridian/scripts/superboss-register.py"

SEVERITY = {
    3: "P0", 4: "P0", 12: "P0", 14: "P0", 19: "P0", 21: "P0",
    1: "P1", 2: "P1", 5: "P1", 6: "P1", 7: "P1", 9: "P1", 15: "P1", 16: "P1", 20: "P1",
    8: "P2", 17: "P2", 18: "P2", 22: "P2", 24: "P2",
    10: "P3", 11: "P3", 13: "P3", 23: "P3",
}


def load_sbr():
    spec = importlib.util.spec_from_file_location("superboss_register", SBR_PATH)
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def call_writer(result, evidence_summary, evidence):
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(CATEGORY_INDEX),
        "--result", result,
        "--script-path", "gtm_check_production_readiness_audit.py",
        "--evidence-summary", evidence_summary,
        "--evidence-json", json.dumps(evidence),
    ]
    print("Calling writer:", " ".join(cmd), file=sys.stderr)
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout)
    if p.returncode != 0:
        print(p.stderr, file=sys.stderr)
        sys.exit(p.returncode)


def classify(passed):
    if passed == 1:
        return "pass"
    if passed == 0:
        return "fail"
    return "blocked_or_pending"


def main():
    try:
        sbr = load_sbr()
        conn = sbr._connect()
    except Exception as e:
        call_writer("blocked", f"could not open superboss-register.sqlite / load superboss-register.py: {e}", {"error": str(e)})
        return

    try:
        rows = conn.execute(
            "SELECT category_index, category_name, passed, evidence_summary, validated_at "
            "FROM gtm_certification_categories WHERE category_index != ? ORDER BY category_index",
            (CATEGORY_INDEX,),
        ).fetchall()
    except Exception as e:
        call_writer("blocked", f"could not read gtm_certification_categories: {e}", {"error": str(e)})
        return

    if len(rows) < 24:
        call_writer(
            "blocked",
            f"only {len(rows)}/24 other categories exist in gtm_certification_categories -- too little real data to synthesize a production readiness picture.",
            {"other_category_rows_found": len(rows)},
        )
        return

    per_category = []
    by_severity = {"P0": [], "P1": [], "P2": [], "P3": []}
    counts = {"pass": 0, "fail": 0, "blocked_or_pending": 0}

    for r in rows:
        idx = r["category_index"]
        state = classify(r["passed"])
        sev = SEVERITY.get(idx, "unclassified")
        entry = {
            "category_index": idx,
            "category_name": r["category_name"],
            "severity": sev,
            "state": state,
            "evidence_summary": r["evidence_summary"],
        }
        per_category.append(entry)
        counts[state] += 1
        by_severity.setdefault(sev, []).append(entry)

    p0_fail_or_blocked = [e for e in by_severity["P0"] if e["state"] != "pass"]
    p1_fail = [e for e in by_severity["P1"] if e["state"] == "fail"]

    result = "fail" if (p0_fail_or_blocked or p1_fail) else "pass"

    evidence = {
        "total_other_categories": len(rows),
        "pass_count": counts["pass"],
        "fail_count": counts["fail"],
        "blocked_or_pending_count": counts["blocked_or_pending"],
        "severity_rubric": SEVERITY,
        "per_category": per_category,
        "p0_not_passing": p0_fail_or_blocked,
        "p1_failing": p1_fail,
        "pass_criterion": "every real P0 category has passed=1 (zero P0 fail/blocked) AND zero real P1 fail; P1/P2/P3 blocked-or-pending is tolerated on its own",
    }
    p0_names = ", ".join(f"{e['category_name']}({e['state']})" for e in p0_fail_or_blocked)
    p1_names = ", ".join(f"{e['category_name']}({e['state']})" for e in p1_fail)
    summary = (
        f"Production readiness synthesis over {len(rows)}/24 other categories: "
        f"{counts['pass']} pass, {counts['fail']} fail, {counts['blocked_or_pending']} blocked/pending. "
        f"P0 not-passing: {p0_names or 'none'}. P1 failing: {p1_names or 'none'}."
    )
    call_writer(result, summary, evidence)


if __name__ == "__main__":
    main()
