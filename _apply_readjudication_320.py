#!/usr/bin/env python3
"""One-off apply script for UMR-20260807-051828-6715 (governance-integrity
re-adjudicate-320): writes the real, evidence-based dispositions computed in
/tmp/adjudication_results.json back onto umr_tasks via superboss-register.py's
own real update_umr_task()/reset_umr_task_to_queued()/
validate_umr_terminal_completion_evidence() functions -- imported and called
directly, same convention as apply_owner_dispatch_status_corrections.py and
reconcile_owner_dispatch_status.py -- never a raw SQL UPDATE.

Read-merge-write on outputs_json: the pre-existing outputs_json (which
carries the real new_task_id/returncode/stderr from the ORIGINAL dispatch,
the very evidence this whole re-adjudication is built on) is read fresh from
the row immediately before writing and preserved untouched except for the
'completed' disposition, which additively merges in pr_number/commit_sha/repo
-- never a blind replace (the CLI's mark-umr-terminal path does replace
outputs_json wholesale, which is why this script calls the underlying
functions directly instead of shelling out to the CLI).
"""
import importlib.util as _ilu
import json
import os
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_sbr_spec = _ilu.spec_from_file_location("superboss_register", os.path.join(SCRIPT_DIR, "superboss-register.py"))
_sbr = _ilu.module_from_spec(_sbr_spec)
_sbr_spec.loader.exec_module(_sbr)

REPO_LOCAL = {
    "compliance-tracker": "/opt/veridian/repos/compliance-tracker",
    "veridian-scripts": "/opt/veridian/repos/veridian-scripts",
}

UMR_SELF = "UMR-20260807-051828-6715"

# Manual restore for the one row this script's own live-test accidentally
# blind-overwrote (mark-umr-terminal CLI path replaces outputs_json wholesale
# -- confirmed live, is the real reason this module-level script exists
# instead of shelling out to that CLI for all 320). Original outputs_json
# before that test call: {"new_task_id": "...", ...}. Only new_task_id is
# restored (the field this whole re-adjudication depends on); returncode/
# stderr from the original dispatch are low-value and were not captured
# before the test overwrite.
MANUAL_OUTPUTS_RESTORE = {
    "UMR-20260802-032127-2d8d": {"new_task_id": "task-20260802-032153-merge-pr--9-on-veridian-scripts--fixed-c"},
}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def main():
    results = json.load(open("/tmp/adjudication_results.json"))
    print(f"loaded {len(results)} rows to apply", file=sys.stderr)

    conn = _sbr._connect()
    _sbr._ensure_umr_table(conn)

    applied = {"completed": 0, "failed": 0, "requeued": 0}
    skipped = []

    for i, r in enumerate(results):
        umr_id = r["umr_id"]
        disposition = r["disposition"]

        current = conn.execute(
            "SELECT status, outputs_json, task_identity FROM umr_tasks WHERE umr_id=?", (umr_id,)
        ).fetchone()
        if current is None:
            skipped.append((umr_id, "row no longer exists"))
            continue
        if current["status"] != "failed":
            skipped.append((umr_id, f"status is now {current['status']!r}, not 'failed' -- someone/something else already touched it, skipping to avoid clobbering"))
            continue

        # Duplicate-safety re-check immediately before write (defense in
        # depth alongside the global pre-check already run interactively):
        # refuse to requeue if -- right now -- any OTHER row shares this
        # task_identity and is currently active.
        if disposition == "requeued":
            siblings = conn.execute(
                "SELECT umr_id, status FROM umr_tasks WHERE task_identity=? AND umr_id!=?",
                (current["task_identity"], umr_id),
            ).fetchall()
            active = [s for s in siblings if s["status"] in ("queued", "dispatched", "running")]
            if active:
                skipped.append((umr_id, f"duplicate-safety: active sibling row(s) for same task_identity: {[dict(a) for a in active]}"))
                continue

        try:
            existing_outputs = json.loads(current["outputs_json"]) if current["outputs_json"] else {}
        except (TypeError, ValueError):
            existing_outputs = {}
        if umr_id in MANUAL_OUTPUTS_RESTORE and "new_task_id" not in existing_outputs:
            existing_outputs.update(MANUAL_OUTPUTS_RESTORE[umr_id])

        reason = (
            f"{UMR_SELF} re-adjudication of erroneous one-time-backfill 'failed' write "
            f"(Stage 1 sweep, resource_governor.py backfill_null_heartbeats()/_task_yaml_for_umr_row(): "
            f"that lookup only checks task_docs[task_identity] and a 'reconcile-umr-<id>' directory-name "
            f"fallback, never outputs_json.new_task_id, so it never found this row's real task_dir at "
            f"{r['task_dir']} and defaulted to failed with zero artifact evidence). Real re-check of "
            f"task_dir={r['new_task_id']!r} (repo={r['repo']}, branch={r['branch']}, task.yaml "
            f"status={r['yaml_status']!r}): {r['verdict_reason']}."
        )

        ts = _now_iso()

        if disposition == "completed":
            repo_root = REPO_LOCAL.get(r["repo"])
            allowed, refusal = _sbr.validate_umr_terminal_completion_evidence(
                status="completed", file_path=None, commit_sha=r["merge_commit_sha"], repo_root=repo_root,
            )
            if not allowed:
                skipped.append((umr_id, f"evidence gate refused: {refusal}"))
                continue
            outputs = dict(existing_outputs)
            outputs.update({
                "pr_number": r["merged_pr_number"],
                "commit_sha": r["merge_commit_sha"],
                "repo": r["repo"],
            })
            with _sbr._write_lock():
                _sbr.update_umr_task(conn, umr_id, status="completed", ts_completed=ts, reason=reason, outputs=outputs)
                conn.commit()
            applied["completed"] += 1

        elif disposition == "failed":
            with _sbr._write_lock():
                _sbr.update_umr_task(conn, umr_id, status="failed", ts_completed=ts, reason=reason)
                conn.commit()
            applied["failed"] += 1

        elif disposition == "requeued":
            with _sbr._write_lock():
                _sbr.reset_umr_task_to_queued(conn, umr_id, reason=reason)
                conn.commit()
            applied["requeued"] += 1

        else:
            skipped.append((umr_id, f"unknown disposition {disposition!r}"))
            continue

        if (i + 1) % 40 == 0:
            print(f"...{i + 1}/{len(results)} processed", file=sys.stderr)

    conn.close()
    print("APPLIED:", applied, file=sys.stderr)
    print("SKIPPED:", len(skipped), file=sys.stderr)
    for s in skipped:
        print("  SKIP:", s, file=sys.stderr)

    with open("/tmp/apply_result.json", "w") as f:
        json.dump({"applied": applied, "skipped": skipped}, f, indent=2, default=str)


if __name__ == "__main__":
    main()
