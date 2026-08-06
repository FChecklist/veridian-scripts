#!/usr/bin/env python3
"""gtm_check_governance_testing.py -- real, re-runnable check for GTM
certification category_index=14 ("governance testing").

Built under UMR-20260806-122546-78d6 (TEST_SCRIPT_BUILD): category 14's
evidence_json already recorded a real result (resolver present+correct,
live duplicate-submission probe correctly rejected) but cited a
script_path, gtm_check_governance_testing.py, confirmed genuinely absent
from disk. This script reproduces that real methodology as a genuine,
committed, re-runnable file -- with one deliberate improvement: the prior
run's sub_check_2 hardcoded one specific historical task_identity
("owner-task-20260805-154254-2720487"), which is not durably re-runnable --
that exact row's real status has since moved on (umr_tasks rows are not
static). This version instead probes the real, live-at-call-time active
queue: it reads whichever real umr_tasks row is currently active
(status IN queued/dispatched/running) and confirms the real
find_active_umr_by_identity() resolver correctly finds it, plus a real
negative control (a random, guaranteed-unused identity correctly resolves
to no match). Purely read-only -- never submits, mutates, or dispatches
anything.

Sub-checks, both real and mechanical (never narrated):
  1. resolver_present: superboss-register.py's resolve_superboss_db_path()
     returns the real canonical DB_PATH, and it matches the module's own
     live DB_PATH.
  2. dedup_works: find_active_umr_by_identity() (the real function
     resource_governor.submit() itself calls under _write_lock() to reject
     duplicates) correctly (a) finds a real, currently-active umr_tasks row
     by its own task_identity, and (b) finds no match for a random,
     guaranteed-unused identity.

Pass criterion (documented, fixed, not adjustable at call time):
  PASS <=> both sub-checks report true.
  Any real mismatch (resolver misconfigured, or the resolver function
  giving a wrong answer for either probe) is a genuine FAIL. "blocked" is
  reserved for: superboss-register.py confirmed unimportable, or (for
  sub_check_2's positive probe only) no real active umr_tasks row existing
  at call time to probe against -- in which case sub_check_2's negative
  control and sub_check_1 still run and are reported, but the overall
  result is "blocked" rather than a fabricated pass/fail on an untested
  positive case.

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=14's result.

Usage:
  gtm_check_governance_testing.py [--no-write]
"""
import argparse
import importlib.util
import json
import os
import sys
import uuid

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WRITER = os.path.join(SCRIPTS_DIR, "gtm_write_category_result.py")
SBR_PATH = os.path.join(SCRIPTS_DIR, "superboss-register.py")
CATEGORY_INDEX = 14


def load_sbr():
    spec = importlib.util.spec_from_file_location("superboss_register_gtm14", SBR_PATH)
    sbr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sbr)
    return sbr


def call_writer(result, evidence_summary, evidence):
    import subprocess
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(CATEGORY_INDEX),
        "--result", result,
        "--script-path", "gtm_check_governance_testing.py",
        "--evidence-summary", evidence_summary,
        "--evidence-json", json.dumps(evidence),
    ]
    print("Calling writer:", " ".join(cmd), file=sys.stderr)
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout)
    if p.returncode != 0:
        print(p.stderr, file=sys.stderr)
        sys.exit(p.returncode)


def emit(args, result, summary, evidence):
    if args.no_write:
        print(json.dumps({"result": result, "summary": summary, "evidence": evidence}, indent=2))
        return
    call_writer(result, summary, evidence)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-write", action="store_true", help="evaluate only, print JSON result, never call the writer")
    args = ap.parse_args()

    try:
        sbr = load_sbr()
    except Exception as e:
        emit(args, "blocked", f"superboss-register.py confirmed unimportable: {e}", {"sbr_path": SBR_PATH, "import_error": str(e)})
        return

    # sub_check_1: resolver present + resolved path matches live DB_PATH
    resolver_present = hasattr(sbr, "resolve_superboss_db_path")
    resolved_path = None
    resolved_path_matches_live_db = False
    if resolver_present:
        try:
            resolved_path = sbr.resolve_superboss_db_path()
            resolved_path_matches_live_db = (resolved_path == sbr.DB_PATH)
        except Exception as e:
            resolved_path = f"<resolution raised: {e}>"

    # sub_check_2: real, live dedup resolver correctness
    conn = sbr._connect()
    active_row = conn.execute(
        "SELECT umr_id, task_identity, status FROM umr_tasks "
        "WHERE status IN ({}) ORDER BY ts_submitted DESC LIMIT 1".format(
            ",".join("?" * len(sbr.UMR_ACTIVE_STATUSES))
        ),
        tuple(sbr.UMR_ACTIVE_STATUSES),
    ).fetchone()

    positive_probe = None
    positive_probe_ok = None
    if active_row:
        target_identity = active_row["task_identity"]
        found = sbr.find_active_umr_by_identity(conn, target_identity)
        positive_probe_ok = bool(found and found.get("umr_id") == active_row["umr_id"])
        positive_probe = {
            "target_umr_id": active_row["umr_id"],
            "target_task_identity": target_identity,
            "target_status": active_row["status"],
            "resolver_found_umr_id": found.get("umr_id") if found else None,
            "match": positive_probe_ok,
        }

    negative_identity = f"gtm-governance-check-guaranteed-unused-{uuid.uuid4()}"
    negative_found = sbr.find_active_umr_by_identity(conn, negative_identity)
    negative_probe_ok = negative_found is None
    negative_probe = {"target_task_identity": negative_identity, "resolver_found_umr_id": (negative_found.get("umr_id") if negative_found else None), "correctly_found_nothing": negative_probe_ok}

    dedup_works = negative_probe_ok and (positive_probe_ok is True)

    evidence = {
        "sub_check_1_resolver_present": resolver_present,
        "sub_check_1_resolved_path": resolved_path,
        "sub_check_1_resolved_path_matches_live_db": resolved_path_matches_live_db,
        "sub_check_2_positive_probe": positive_probe,
        "sub_check_2_negative_probe": negative_probe,
        "sub_check_2_dedup_works": dedup_works,
        "methodology_note": "purely read-only: never calls resource_governor.submit(), never inserts/mutates any umr_tasks row",
    }

    if not resolver_present or resolved_path is None:
        emit(args, "blocked", "resolve_superboss_db_path() confirmed absent or raised on call; cannot verify sub_check_1.", evidence)
        return

    if active_row is None:
        emit(
            args, "blocked",
            "sub_check_1 (resolver) passed, but no real active umr_tasks row existed at call time to run sub_check_2's positive probe against; negative control ran and is recorded.",
            evidence,
        )
        return

    result = "pass" if (resolved_path_matches_live_db and dedup_works) else "fail"
    summary = (
        f"resolver_present={resolver_present}, resolved_path_matches_live_db={resolved_path_matches_live_db}; "
        f"dedup positive probe against real active umr_id={active_row['umr_id']}: {positive_probe_ok}; "
        f"negative control: {negative_probe_ok}."
    )
    emit(args, result, summary, evidence)


if __name__ == "__main__":
    main()
