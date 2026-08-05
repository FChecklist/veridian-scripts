#!/usr/bin/env python3
"""
Shared, canonical writer for gtm_certification_categories results. Every
per-category check script (gtm_check_*.py) MUST call this instead of writing
SQL directly -- single point of truth, reuses superboss-register.py's own
_connect()/_write_lock() safety discipline (same pattern as
migrate_2026-08-05_gtm_certification_categories.py).

Usage:
  python3 gtm_write_category_result.py \\
    --category-index 2 \\
    --result pass|fail|blocked \\
    --script-path scripts/gtm_check_static_code_analysis.py \\
    --evidence-summary "eslint exit 0, tsc exit 0 against origin/main HEAD abc1234" \\
    --evidence-json '{"eslint_exit_code": 0, "tsc_exit_code": 0, "commit_sha": "abc1234"}'

--result pass  -> passed=1
--result fail  -> passed=0   (a real script ran, and the real check failed --
                              this is a genuine, evidenced fail, not "blocked")
--result blocked -> passed=NULL, evidence_summary must explain what's missing
                     (tool absent, credentials absent, etc.) -- never fabricate
                     a pass/fail when the check couldn't genuinely run.

evidence_json MUST include enough for the result to be independently
re-verified (real exit codes, real counts, real commit SHA, real command run)
-- never a narrative description standing in place of that.
"""
import argparse
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone

SBR_PATH = "/opt/veridian/scripts/superboss-register.py"


def load_sbr():
    spec = importlib.util.spec_from_file_location("superboss_register", SBR_PATH)
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--category-index", type=int, required=True)
    ap.add_argument("--result", choices=["pass", "fail", "blocked"], required=True)
    ap.add_argument("--script-path", required=True, help="repo-relative or absolute path to the real check script that produced this result")
    ap.add_argument("--evidence-summary", required=True)
    ap.add_argument("--evidence-json", required=True, help="JSON string -- real exit codes/counts/commit SHAs, never narration")
    ap.add_argument("--fix-commit", default=None)
    ap.add_argument("--fix-file-path", default=None)
    ap.add_argument("--fix-pr-number", type=int, default=None)
    args = ap.parse_args()

    try:
        evidence = json.loads(args.evidence_json)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"--evidence-json is not valid JSON: {e}"}))
        sys.exit(1)

    # evidence_json must always cite the script path itself so the result is
    # genuinely re-runnable, not just described.
    evidence.setdefault("script_path", args.script_path)

    passed = {"pass": 1, "fail": 0, "blocked": None}[args.result]
    now = datetime.now(timezone.utc).isoformat()

    sbr = load_sbr()
    conn = sbr._connect()

    existing = conn.execute(
        "SELECT category_index, category_name FROM gtm_certification_categories WHERE category_index=?",
        (args.category_index,),
    ).fetchone()
    if existing is None:
        print(json.dumps({"error": f"no gtm_certification_categories row for category_index={args.category_index}"}))
        sys.exit(1)

    with sbr._write_lock():
        conn.execute(
            """
            UPDATE gtm_certification_categories
            SET passed=?, evidence_summary=?, evidence_json=?,
                fix_commit=?, fix_file_path=?, fix_pr_number=?,
                validated_at=?, last_updated_at=?
            WHERE category_index=?
            """,
            (
                passed, args.evidence_summary, json.dumps(evidence),
                args.fix_commit, args.fix_file_path, args.fix_pr_number,
                now if passed is not None else None, now,
                args.category_index,
            ),
        )
        sbr.record_ocid_master_standard_audit_event(
            conn, "gtm_certification_category_result",
            {
                "category_index": args.category_index,
                "category_name": existing["category_name"],
                "result": args.result,
                "script_path": args.script_path,
                "evidence_summary": args.evidence_summary,
                "evidence": evidence,
            },
            ocid_number="OCID-020", umr_id="UMR-20260802-165606-4413",
        )
        conn.commit()

    print(json.dumps({
        "category_index": args.category_index,
        "category_name": existing["category_name"],
        "result": args.result,
        "passed": passed,
    }))


if __name__ == "__main__":
    main()
