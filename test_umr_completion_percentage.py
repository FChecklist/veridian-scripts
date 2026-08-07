#!/usr/bin/env python3
"""
test_umr_completion_percentage.py -- standalone (no pytest required) proof
that umr_completion_percentage.py's 5 real deterministic rules actually
behave as its own SPEC (UMR-20260806-124055-bc80: "no manual AI-judged
percentages anywhere, ever") requires, including the unresolved/null path
none of the 5 live UMR IDs used as the SPEC's own required real-world test
happened to hit.

Runs entirely against a throwaway temp DB (SUPERBOSS_REGISTER_DB env var
override) and a throwaway temp TASKS_DIR (VERIDIAN_TASKS_DIR env var
override), same convention test_agent_work_briefing.py already established
-- never touches the live /opt/veridian/ai-os/memory/superboss-register.sqlite
or /opt/veridian/ai-os/tasks/.

Run: python3 test_umr_completion_percentage.py
Exits 0 and prints PASS if every check holds; exits 1 and prints the first
failure(s) otherwise.
"""
import json
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
    cols.setdefault("ts_submitted", "2026-08-07T00:00:00+00:00")
    cols.setdefault("tier", 1)
    cols.setdefault("status", "queued")
    cols.setdefault("source_trigger", "test")
    fields = ["umr_id"] + list(cols.keys())
    placeholders = ",".join("?" for _ in fields)
    conn.execute(
        f"INSERT INTO umr_tasks ({','.join(fields)}) VALUES ({placeholders})",
        [umr_id] + list(cols.values()),
    )


def _write_task_yaml(tasks_dir, task_id, completed_steps, remaining_steps):
    task_dir = os.path.join(tasks_dir, task_id)
    os.makedirs(task_dir, exist_ok=True)
    lines = (["completed_steps:"] + [f"- {s!r}" for s in completed_steps]
              if completed_steps else ["completed_steps: []"])
    lines += (["remaining_steps:"] + [f"- {s!r}" for s in remaining_steps]
              if remaining_steps else ["remaining_steps: []"])
    with open(os.path.join(task_dir, "task.yaml"), "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    tmpdir = tempfile.mkdtemp(prefix="umr_completion_percentage_test_")
    db_path = os.path.join(tmpdir, "test.sqlite")
    tasks_dir = os.path.join(tmpdir, "tasks")
    os.makedirs(tasks_dir, exist_ok=True)

    # Same resolve_superboss_db_path() existence trap test_agent_work_briefing.py
    # already discovered: bootstrap the real umr_tasks schema BEFORE the env
    # var is read, so the resolver accepts this temp path instead of falling
    # back to the live production db.
    _bootstrap = sqlite3.connect(db_path)
    _bootstrap.execute(UMR_TASKS_DDL)
    _bootstrap.commit()
    _bootstrap.close()

    os.environ["SUPERBOSS_REGISTER_DB"] = db_path
    os.environ["VERIDIAN_TASKS_DIR"] = tasks_dir

    import importlib.util as ilu
    spec = ilu.spec_from_file_location(
        "umr_completion_percentage_test_mod", os.path.join(SCRIPTS, "umr_completion_percentage.py"))
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)

    sbr = mod._superboss_register()
    if os.path.realpath(sbr.DB_PATH) != os.path.realpath(db_path):
        print(f"FAIL (setup): resolved DB_PATH {sbr.DB_PATH!r} is NOT the temp db "
              f"{db_path!r} -- refusing to proceed.")
        return 1

    conn = sbr._connect()

    failures = []

    def check(label, got, want):
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    # --- Rule 1: completed + real ts_completed + real evidence -> 100.
    _insert_row(conn, "UMR-T-RULE1", status="completed", ts_completed="2026-08-07T01:00:00+00:00",
                outputs_json=json.dumps({"pr_number": 1, "repo": "x"}))
    # --- Rule 1 should NOT fire: completed evidence is empty ("{}") -- falls through.
    _insert_row(conn, "UMR-T-RULE1-NO-EVIDENCE", status="completed", ts_completed="2026-08-07T01:00:00+00:00",
                outputs_json="{}")
    # --- Rule 2: real task.yaml with real steps (any status, here 'dispatched').
    _insert_row(conn, "UMR-T-RULE2", status="dispatched", ts_dispatched="2026-08-07T01:00:00+00:00",
                outputs_json=json.dumps({"new_task_id": "task-rule2"}))
    _write_task_yaml(tasks_dir, "task-rule2", completed_steps=["a", "b", "c"], remaining_steps=["d"])
    # --- Rule 3: failed, no task.yaml -> 0.
    _insert_row(conn, "UMR-T-RULE3", status="failed",
                outputs_json=json.dumps({"new_task_id": "task-rule3-nonexistent"}))
    # --- Rule 3 should NOT fire when a task.yaml DOES show progress: falls to rule 2 instead.
    _insert_row(conn, "UMR-T-RULE3-WITH-PROGRESS", status="failed",
                outputs_json=json.dumps({"new_task_id": "task-rule3b"}))
    _write_task_yaml(tasks_dir, "task-rule3b", completed_steps=["a"], remaining_steps=["b", "c"])
    # --- Rule 4: queued, ts_dispatched NULL -> 0.
    _insert_row(conn, "UMR-T-RULE4", status="queued")
    # --- Rule 5: running + real task.yaml -> steps formula.
    _insert_row(conn, "UMR-T-RULE5", status="running",
                outputs_json=json.dumps({"new_task_id": "task-rule5"}))
    _write_task_yaml(tasks_dir, "task-rule5", completed_steps=[], remaining_steps=["only"])
    # --- Unresolved: completed but ts_completed NULL, no task.yaml -> null.
    _insert_row(conn, "UMR-T-UNRESOLVED-COMPLETED-NO-TS", status="completed",
                outputs_json=json.dumps({"pr_number": 9}))
    # --- Unresolved: task.yaml exists but both step lists are empty (0/0) -> null.
    _insert_row(conn, "UMR-T-UNRESOLVED-ZERO-STEPS", status="running",
                outputs_json=json.dumps({"new_task_id": "task-zero"}))
    _write_task_yaml(tasks_dir, "task-zero", completed_steps=[], remaining_steps=[])
    # --- Unresolved: dispatched status, no task.yaml -> null.
    _insert_row(conn, "UMR-T-UNRESOLVED-DISPATCHED", status="dispatched", ts_dispatched="2026-08-07T01:00:00+00:00")
    # --- task_identity fallback: new_task_id absent, task_identity itself is a real dir.
    _insert_row(conn, "UMR-T-TASK-IDENTITY-FALLBACK", status="running", task_identity="task-identity-dir")
    _write_task_yaml(tasks_dir, "task-identity-dir", completed_steps=["a"], remaining_steps=[])

    conn.commit()

    def get(umr_id):
        row = conn.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
        return mod.compute_completion(umr_id, row)

    r = get("UMR-T-RULE1")
    check("rule1.percent", r["percent"], 100)
    check("rule1.rule", r["rule"], "rule1_completed_with_evidence")

    r = get("UMR-T-RULE1-NO-EVIDENCE")
    check("rule1_no_evidence.percent", r["percent"], None)
    check("rule1_no_evidence.rule", r["rule"], "unresolved")

    r = get("UMR-T-RULE2")
    check("rule2.percent", r["percent"], 75)  # 3/(3+1)*100
    check("rule2.rule", r["rule"], "rule2_task_yaml_steps")

    r = get("UMR-T-RULE3")
    check("rule3.percent", r["percent"], 0)
    check("rule3.rule", r["rule"], "rule3_failed_no_task_yaml_progress")

    r = get("UMR-T-RULE3-WITH-PROGRESS")
    check("rule3_with_progress.percent", r["percent"], 33)  # round(100*1/3)
    check("rule3_with_progress.rule", r["rule"], "rule2_task_yaml_steps")

    r = get("UMR-T-RULE4")
    check("rule4.percent", r["percent"], 0)
    check("rule4.rule", r["rule"], "rule4_queued_not_dispatched")

    r = get("UMR-T-RULE5")
    check("rule5.percent", r["percent"], 0)  # 0/(0+1)*100
    check("rule5.rule", r["rule"], "rule5_running_task_yaml_steps")

    r = get("UMR-T-UNRESOLVED-COMPLETED-NO-TS")
    check("unresolved_completed_no_ts.percent", r["percent"], None)
    check("unresolved_completed_no_ts.rule", r["rule"], "unresolved")

    r = get("UMR-T-UNRESOLVED-ZERO-STEPS")
    check("unresolved_zero_steps.percent", r["percent"], None)
    check("unresolved_zero_steps.rule", r["rule"], "unresolved_task_yaml_no_steps")

    r = get("UMR-T-UNRESOLVED-DISPATCHED")
    check("unresolved_dispatched.percent", r["percent"], None)
    check("unresolved_dispatched.rule", r["rule"], "unresolved")

    r = get("UMR-T-TASK-IDENTITY-FALLBACK")
    check("task_identity_fallback.percent", r["percent"], 100)  # 1/(1+0)*100
    # status='running' here too, so this is rule 5's tag, not rule 2's.
    check("task_identity_fallback.rule", r["rule"], "rule5_running_task_yaml_steps")

    r = mod.compute_completion("UMR-T-NOT-FOUND", None)
    check("not_found.percent", r["percent"], None)
    check("not_found.rule", r["rule"], "not_found")

    # compute_many() preserves input order (including duplicates).
    many = mod.compute_many(["UMR-T-RULE1", "UMR-T-RULE4", "UMR-T-RULE1"], conn)
    check("compute_many.order", [x["umr_id"] for x in many],
          ["UMR-T-RULE1", "UMR-T-RULE4", "UMR-T-RULE1"])
    check("compute_many.len", len(many), 3)

    conn.close()

    if failures:
        print(f"FAIL ({len(failures)} check(s) failed):")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"PASS ({14 + 2} checks across 13 scenarios)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
