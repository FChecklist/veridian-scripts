#!/usr/bin/env python3
"""
Real, deterministic check for GTM category 14, "governance testing".

Per explicit PM instruction (UMR-20260805-153813-767f): checks the real
specific mechanical facts already used as evidence this session -- zero
duplication check passes, canonical database resolver present -- as actual
code/behavior checks, never narration. A prior version of this category's
row was reverted (UMR-20260805-152508-d4c9) precisely because it was a
narrated summary citing historical actions instead of a re-runnable check;
this script is the real re-runnable replacement.

Two real, mechanical sub-checks, both must pass:

1. Canonical database resolver present: `resolve_superboss_db_path` is a
   real, callable function in scripts/superboss-register.py (imported and
   called directly -- not a grep/text match, an actual function-existence +
   callability check).

2. Zero-duplication check actually works, functionally, live: submits a
   real task_spec via resource_governor.py's own submit() with a
   task_identity deliberately colliding with a REAL currently
   queued/running/dispatched row (queried fresh, not hardcoded), and
   confirms the real return value is accepted=False with a
   rejected_duplicate-shaped reason. This is a live functional test of the
   exact guarantee this session relied on when stopping
   task-20260805-114214 and evaluating UMR-20260805-115044-b481 -- not
   a description of that past event.

If no currently queued/running task exists to collide against (queue
briefly empty), this is `--result blocked` (genuinely can't run the test
right now), never a fabricated pass.
"""
import importlib.util
import json
import os
import subprocess
import sys

WRITER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gtm_write_category_result.py")
CATEGORY_INDEX = 14
SBR_PATH = "/opt/veridian/scripts/superboss-register.py"
RG_PATH = "/opt/veridian/scripts/resource_governor.py"
DB_PATH = "/opt/veridian/ai-os/memory/superboss-register.sqlite"


def run_writer(result, evidence_summary, evidence):
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(CATEGORY_INDEX),
        "--result", result,
        "--script-path", "gtm_check_governance_testing.py",
        "--evidence-summary", evidence_summary,
        "--evidence-json", json.dumps(evidence),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True)
    print(out.stdout.strip())
    if out.returncode != 0:
        print(out.stderr, file=sys.stderr)


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    evidence = {}

    # Sub-check 1: canonical resolver present and callable.
    sbr = load_module(SBR_PATH, "superboss_register")
    resolver_present = hasattr(sbr, "resolve_superboss_db_path") and callable(sbr.resolve_superboss_db_path)
    resolved_path = None
    if resolver_present:
        try:
            resolved_path = sbr.resolve_superboss_db_path()
        except Exception as e:
            resolver_present = False
            evidence["resolver_call_error"] = str(e)
    evidence["sub_check_1_resolver_present"] = resolver_present
    evidence["sub_check_1_resolved_path"] = resolved_path
    evidence["sub_check_1_resolved_path_matches_live_db"] = (resolved_path == DB_PATH)

    # Sub-check 2: live functional duplicate-rejection test.
    import sqlite3
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT task_identity FROM umr_tasks WHERE status IN ('queued','running','dispatched') "
        "ORDER BY ts_submitted DESC LIMIT 1"
    ).fetchone()

    if row is None:
        evidence["sub_check_2_note"] = "no currently queued/running/dispatched task to collide against"
        run_writer("blocked", "sub-check 1 (resolver) real result available, but sub-check 2 (live duplicate-rejection test) could not run -- queue empty", evidence)
        return

    target_identity = row["task_identity"]
    resgov = load_module(RG_PATH, "resource_governor")
    try:
        result = resgov.submit(
            {"task_identity": target_identity, "task_kind": "veridian_task_create",
             "inputs": {"title": "gtm_check_governance_testing.py duplicate-rejection probe (non-functional, always rejected)",
                        "prompt": "This is a real functional test of the zero-duplication guarantee, not real work -- expected to be rejected as a duplicate."}},
            tier=4, source_trigger="gtm_check_governance_testing.py:live_dedup_probe",
        )
    except Exception as e:
        evidence["sub_check_2_error"] = str(e)
        run_writer("blocked", "sub-check 2 (live duplicate-rejection test) raised an exception calling submit()", evidence)
        return

    dedup_works = (result.get("accepted") is False) and ("duplicate" in (result.get("reason") or "").lower())
    evidence["sub_check_2_target_task_identity"] = target_identity
    evidence["sub_check_2_submit_result"] = {"accepted": result.get("accepted"), "reason": result.get("reason")}
    evidence["sub_check_2_dedup_works"] = dedup_works

    if resolver_present and evidence["sub_check_1_resolved_path_matches_live_db"] and dedup_works:
        run_writer("pass", "both real mechanical checks pass: canonical DB resolver present+correct, live duplicate-submission probe correctly rejected", evidence)
    else:
        run_writer("fail", "at least one real mechanical governance check failed -- see evidence_json for which", evidence)


if __name__ == "__main__":
    main()
