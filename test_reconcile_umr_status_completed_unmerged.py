#!/usr/bin/env python3
"""
test_reconcile_umr_status_completed_unmerged.py -- standalone (no pytest
required) real proof for the task-20260814-080739 fix to
reconcile_umr_status_against_pr()'s own stale_statuses set in
superboss-register.py.

Before this fix, a real umr_tasks row correctly written as
status='completed_unmerged' (a real commit that genuinely was not yet an
ancestor of main/master at write time -- see
validate_umr_terminal_completion_evidence()'s own docstring) could NEVER be
promoted to 'completed' by this function even once its PR later merged,
because stale_statuses only ever contained {"queued", "dispatched",
"running"}. This is the exact real-world bug this sweep found live against
UMR-20260814-054218-9475 (commit 5e9f6dea, PR #209 merged after the row was
already written completed_unmerged) and fixed by hand for that one row via a
direct mark-umr-terminal call -- this test proves the underlying function
itself now closes that gap for every future row of the same shape, not just
that one.

Runs entirely against a throwaway temp DB (SUPERBOSS_REGISTER_DB env var
override), same convention test_umr_completion_percentage.py /
test_agent_work_briefing.py already established -- never touches the live
/opt/veridian/ai-os/memory/superboss-register.sqlite. `pr_evidence` is passed
in directly (the function's own documented injection point for deterministic/
offline testing) -- no live network/gh call is made.

Run: python3 test_reconcile_umr_status_completed_unmerged.py
Exits 0 and prints PASS if every check holds; exits 1 and prints the first
failure(s) otherwise.
"""
import importlib.util as ilu
import os
import sqlite3
import sys
import tempfile

SCRIPTS = os.path.dirname(os.path.abspath(__file__))

UMR_TASKS_DDL = """CREATE TABLE umr_tasks (
    umr_id TEXT PRIMARY KEY,
    task_identity TEXT NOT NULL,
    ts_submitted TEXT NOT NULL,
    tier INTEGER NOT NULL CHECK(tier BETWEEN 0 AND 4),
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','dispatched','running','completed','completed_unmerged','failed','rejected_duplicate','sigterm_sent','killed')),
    source_trigger TEXT NOT NULL,
    task_kind TEXT NOT NULL DEFAULT 'systemctl_action',
    unit_name TEXT,
    inputs_json TEXT NOT NULL DEFAULT '{}',
    outputs_json TEXT NOT NULL DEFAULT '{}',
    logs_ref TEXT,
    metric_snapshot_json TEXT,
    ts_dispatched TEXT,
    ts_sigterm TEXT,
    ts_completed TEXT,
    reason TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    last_heartbeat TEXT, tenant_id TEXT, utm_source TEXT, utm_medium TEXT, utm_campaign TEXT,
    utm_content TEXT, utm_term TEXT, external_agent_eligible INTEGER NOT NULL DEFAULT 0,
    external_agent_task_type TEXT, blast_radius TEXT, requires_multi_file_context INTEGER NOT NULL DEFAULT 0,
    files_touched TEXT NOT NULL DEFAULT '[]', external_agent_status TEXT,
    external_agent_reject_count INTEGER NOT NULL DEFAULT 0, external_agent_dispatch_count INTEGER NOT NULL DEFAULT 0,
    ts_relay_attempted TEXT, relay_outcome TEXT, relay_detail TEXT
)"""

def _insert_row(conn, umr_id, **cols):
    cols.setdefault("task_identity", f"identity-for-{umr_id}")
    cols.setdefault("ts_submitted", "2026-08-14T00:00:00+00:00")
    cols.setdefault("tier", 1)
    cols.setdefault("status", "queued")
    cols.setdefault("source_trigger", "test")
    fields = ["umr_id"] + list(cols.keys())
    placeholders = ",".join("?" for _ in fields)
    conn.execute(
        f"INSERT INTO umr_tasks ({','.join(fields)}) VALUES ({placeholders})",
        [umr_id] + list(cols.values()),
    )
    conn.commit()


def main():
    tmpdir = tempfile.mkdtemp(prefix="reconcile_umr_status_completed_unmerged_test_")
    db_path = os.path.join(tmpdir, "test.sqlite")

    # Same resolve_superboss_db_path() existence trap test_umr_completion_percentage.py
    # already documented: bootstrap the real schema BEFORE the env var is
    # read, so the resolver accepts this temp path instead of falling back
    # to the live production db.
    _bootstrap = sqlite3.connect(db_path)
    _bootstrap.execute(UMR_TASKS_DDL)
    _bootstrap.commit()
    _bootstrap.close()

    os.environ["SUPERBOSS_REGISTER_DB"] = db_path

    spec = ilu.spec_from_file_location(
        "sbr_test_mod", os.path.join(SCRIPTS, "superboss-register.py"))
    sbr = ilu.module_from_spec(spec)
    spec.loader.exec_module(sbr)

    if os.path.realpath(sbr.DB_PATH) != os.path.realpath(db_path):
        print(f"FAIL (setup): resolved DB_PATH {sbr.DB_PATH!r} is NOT the temp db "
              f"{db_path!r} -- refusing to proceed.")
        return 1

    conn = sbr._connect()
    sbr._ensure_ocid_master_standard_audit_log_table(conn)
    failures = []

    def check(label, got, want):
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    merged_evidence = [{"state": "MERGED", "mergedAt": "2026-08-14T07:16:24Z", "number": 209}]
    open_evidence = [{"state": "OPEN", "mergedAt": None, "number": 360}]

    # --- Case 1 (the real bug this fix closes): completed_unmerged + a real
    # merged-PR evidence match -> now correctly proposed stale/completed.
    _insert_row(conn, "UMR-T-CU-MERGED", status="completed_unmerged",
                ts_completed="2026-08-14T06:02:44+00:00",
                outputs_json='{"commit_sha": "5e9f6de", "repo": "claude-control"}')
    result = sbr.reconcile_umr_status_against_pr(conn, "UMR-T-CU-MERGED", pr_evidence=merged_evidence)
    check("completed_unmerged+merged PR: is_stale", result["is_stale"], True)
    check("completed_unmerged+merged PR: proposed_status", result["proposed_status"], "completed")
    check("completed_unmerged+merged PR: proposed_ts_completed", result["proposed_ts_completed"],
          "2026-08-14T07:16:24Z")

    # --- Case 2: completed_unmerged with NO merged-PR evidence (still
    # genuinely open, e.g. real PR #360/#214 blocked on a fresh AUDIT:PASS)
    # -> must NOT be touched; this fix must not make every completed_unmerged
    # row look stale, only ones with real found merge evidence.
    _insert_row(conn, "UMR-T-CU-STILL-OPEN", status="completed_unmerged",
                ts_completed="2026-08-14T07:00:59+00:00",
                outputs_json='{"commit_sha": "05c33ed", "repo": "veridian-scripts"}')
    result2 = sbr.reconcile_umr_status_against_pr(conn, "UMR-T-CU-STILL-OPEN", pr_evidence=open_evidence)
    check("completed_unmerged+open PR: is_stale", result2["is_stale"], False)
    check("completed_unmerged+open PR: proposed_status", result2["proposed_status"], None)

    # --- Case 3: pre-existing behavior (running + merged PR) must still work
    # unchanged -- this fix is additive to stale_statuses, not a rewrite.
    _insert_row(conn, "UMR-T-RUNNING-MERGED", status="running")
    result3 = sbr.reconcile_umr_status_against_pr(conn, "UMR-T-RUNNING-MERGED", pr_evidence=merged_evidence)
    check("running+merged PR: is_stale", result3["is_stale"], True)
    check("running+merged PR: proposed_status", result3["proposed_status"], "completed")

    # --- Case 4: a genuinely terminal, already-correct status (failed) with
    # merge evidence present must still be left alone -- stale_statuses is a
    # real allowlist, not "any status with merge evidence".
    _insert_row(conn, "UMR-T-FAILED-MERGED", status="failed")
    result4 = sbr.reconcile_umr_status_against_pr(conn, "UMR-T-FAILED-MERGED", pr_evidence=merged_evidence)
    check("failed+merged PR: is_stale", result4["is_stale"], False)

    # --- Case 5: real end-to-end apply path -- --apply's own real write
    # (update_umr_task under _write_lock, exactly what cmd_reconcile_umr_status
    # does) actually lands status='completed' in the real DB row, matching
    # what the CLI's --apply flag does.
    with sbr._write_lock():
        sbr.update_umr_task(conn, "UMR-T-CU-MERGED", status=result["proposed_status"],
                             ts_completed=result["proposed_ts_completed"])
        conn.commit()
    row = conn.execute("SELECT status, ts_completed FROM umr_tasks WHERE umr_id=?",
                        ("UMR-T-CU-MERGED",)).fetchone()
    check("real --apply write: status", row["status"], "completed")
    check("real --apply write: ts_completed", row["ts_completed"], "2026-08-14T07:16:24Z")

    conn.close()

    if failures:
        print(f"FAIL ({len(failures)} check(s) failed):")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("PASS: all real checks held -- completed_unmerged rows with real "
          "merged-PR evidence are now correctly proposed/applied as "
          "completed, and every pre-existing case (open PR, running+merged, "
          "already-terminal) is unchanged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
