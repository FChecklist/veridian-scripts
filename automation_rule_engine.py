#!/usr/bin/env python3
"""
automation_rule_engine.py -- real trigger->condition->action automation
rule builder for the AI-OS *operational* layer (closes engine 9,
Automation Engine, of ai-os/20_ENGINES_10_GATEWAYS_PHASE_PLAN_2026-07-24.yaml's
phase_3_workflow_automation_integration).

Prior-art note (do not duplicate, per the Owner's standing no-duplication
rule): repos/compliance-tracker/src/lib/services/automation-rule-service.ts
(Wave 30) already IS a real, live trigger->condition->action automation
rule builder -- n8n-inspired, single-condition rules, no node-graph -- and
it already indexes every rule into the Capability Registry's automation_rule
entity type (indexCapability("automation_rule", rule.id, ...)). It is
scoped entirely to in-app/product events (2 call sites: notice-service.ts,
updateIssue()) with 2 action types (notify_user, create_task), and it is
out of this task's repo boundary (compliance-tracker is a separate repo;
this task's own EXPECTED_OUTPUT is a claude-control PR only).

This module is NOT a second copy of that service -- it is the AI-OS
operational-layer sibling automation-rule-service.ts has no path to: rules
whose trigger is a server-side event (an inbound webhook, a task-gateway.py
close, a cron finding) and whose action dispatches into this repo's own
scripts (task-gateway.py submit, notify-owner.py, superboss-register.py
log-action). The TriggerCondition shape below is deliberately identical,
field-for-field, to automation-rule-service.ts's own TriggerCondition type
(field/operator/value, operator constrained to "equals", no-condition means
"fires on every event of this trigger_type") -- reused convention, not a
competing rule language. Rules are keyed to an existing
capability_registry.capability_name row (Phase 1's live table) wherever one
applies, honoring this schema's own `automation` column description
("an automation_rule row keyed by this capability_name").

Subcommands: register-rule, list-rules, evaluate-rules
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import uuid

VERIDIAN_ROOT = "/opt/veridian"
SCRIPTS = f"{VERIDIAN_ROOT}/scripts"
AI_OS = f"{VERIDIAN_ROOT}/ai-os"
SUPERBOSS = f"{SCRIPTS}/superboss-register.py"
TASK_GATEWAY = f"{SCRIPTS}/task-gateway.py"
NOTIFY_OWNER = f"{SCRIPTS}/notify-owner.py"
SUPERBOSS_REGISTER = SUPERBOSS

# Real, canonical DB-path resolution -- same lazy-import-cached convention
# reconcile_stale_running_workers.py and worker-exit-status-bridge.py already
# use: never a hardcoded path, always the one real SUPERBOSS_REGISTER_DB
# override every other real caller already honors.
_sbr = None


def _superboss_register():
    global _sbr
    if _sbr is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "superboss_register_automation_rule_engine", SUPERBOSS_REGISTER)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sbr = _mod
    return _sbr


def _resolve_db_path():
    return _superboss_register().resolve_superboss_db_path()

# action_type is a closed set, deliberately smaller than
# automation-rule-service.ts's own 2-value set widened for this layer's
# real needs -- no AI-in-the-loop action type, no code-execution action
# type, same "much smaller than n8n itself" discipline that file's own
# header documents.
VALID_ACTION_TYPES = ["task_gateway_submit", "notify_owner", "log_action"]


def _connect():
    conn = sqlite3.connect(_resolve_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables(conn):
    """Standalone idempotent create, same defensiveness convention as
    superboss-register.py's own _ensure_capability_registry_table -- works
    even if init_db() was never run against this DB."""
    conn.execute("""CREATE TABLE IF NOT EXISTS automation_rules (
        rule_id TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        rule_name TEXT NOT NULL,
        capability_name TEXT,
        trigger_type TEXT NOT NULL,
        trigger_conditions TEXT NOT NULL DEFAULT '{}',
        action_type TEXT NOT NULL,
        action_config TEXT NOT NULL DEFAULT '{}',
        is_active INTEGER NOT NULL DEFAULT 1
    )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_automation_rules_name ON automation_rules(rule_name)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_automation_rules_trigger_type ON automation_rules(trigger_type)")
    conn.execute("""CREATE TABLE IF NOT EXISTS automation_rule_runs (
        run_id TEXT PRIMARY KEY,
        rule_id TEXT NOT NULL,
        ts TEXT NOT NULL,
        trigger_payload TEXT NOT NULL,
        status TEXT NOT NULL,
        result_summary TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_automation_rule_runs_rule_id ON automation_rule_runs(rule_id)")
    conn.commit()


def _now_iso():
    # Deliberately reuses sqlite's own clock (not Python's datetime.now())
    # so run timestamps are ordered consistently with every other table's
    # `ts` column, which superboss-register.py also derives from SQL.
    conn = sqlite3.connect(_resolve_db_path())
    val = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0]
    conn.close()
    return val


def _run_json(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return proc.returncode, json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        return proc.returncode, {"raw_stdout": proc.stdout, "raw_stderr": proc.stderr}


def cmd_register_rule(args):
    try:
        trigger_conditions = json.loads(args.trigger_conditions) if args.trigger_conditions else {}
    except json.JSONDecodeError:
        print(json.dumps({"error": "--trigger-conditions must be valid JSON"}, indent=2))
        sys.exit(1)
    try:
        action_config = json.loads(args.action_config) if args.action_config else {}
    except json.JSONDecodeError:
        print(json.dumps({"error": "--action-config must be valid JSON"}, indent=2))
        sys.exit(1)
    if args.action_type not in VALID_ACTION_TYPES:
        print(json.dumps({"error": f"--action-type must be one of {VALID_ACTION_TYPES}"}, indent=2))
        sys.exit(1)

    conn = _connect()
    _ensure_tables(conn)

    if args.capability_name:
        row = conn.execute(
            "SELECT capability_name FROM capability_registry WHERE capability_name = ?",
            (args.capability_name,),
        ).fetchone()
        if not row:
            conn.close()
            print(json.dumps({
                "error": f"--capability-name '{args.capability_name}' has no matching row in "
                         "capability_registry -- register the capability first (Phase 1's "
                         "register-capability), or omit --capability-name for a rule not tied to a "
                         "named capability."
            }, indent=2))
            sys.exit(1)

    rule_id = f"AR-{uuid.uuid4().hex[:12]}"
    ts = _now_iso()
    conn.execute(
        "INSERT INTO automation_rules (rule_id, ts, rule_name, capability_name, trigger_type, "
        "trigger_conditions, action_type, action_config, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1) "
        "ON CONFLICT(rule_name) DO UPDATE SET capability_name=excluded.capability_name, "
        "trigger_type=excluded.trigger_type, trigger_conditions=excluded.trigger_conditions, "
        "action_type=excluded.action_type, action_config=excluded.action_config, is_active=1",
        (rule_id, ts, args.rule_name, args.capability_name, args.trigger_type,
         json.dumps(trigger_conditions), args.action_type, json.dumps(action_config)),
    )
    conn.commit()
    final_id = conn.execute(
        "SELECT rule_id FROM automation_rules WHERE rule_name = ?", (args.rule_name,)
    ).fetchone()["rule_id"]
    conn.close()

    print(json.dumps({"rule_id": final_id, "rule_name": args.rule_name, "registered": True}, indent=2))


def cmd_list_rules(args):
    conn = _connect()
    _ensure_tables(conn)
    query = "SELECT * FROM automation_rules"
    params = []
    if args.trigger_type:
        query += " WHERE trigger_type = ?"
        params.append(args.trigger_type)
    query += " ORDER BY rule_name"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    rules = []
    for r in rows:
        d = dict(r)
        d["trigger_conditions"] = json.loads(d["trigger_conditions"])
        d["action_config"] = json.loads(d["action_config"])
        d["is_active"] = bool(d["is_active"])
        rules.append(d)
    print(json.dumps({"count": len(rules), "rules": rules}, indent=2))


def _condition_matches(trigger_conditions, payload):
    """Field-for-field port of automation-rule-service.ts's conditionsMatch():
    empty/missing conditions fire on every event of this trigger_type; a
    dotted field path is resolved against payload; operator is always
    "equals" (the only operator that file's own TriggerCondition type
    supports today)."""
    if not trigger_conditions:
        return True
    field = trigger_conditions.get("field")
    if not field:
        return True
    value = trigger_conditions.get("value")
    current = payload
    for key in str(field).split("."):
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            current = None
            break
    return current == value


def _dispatch_action(rule, payload):
    """Real dispatch, not a dry-run -- mirrors evaluateAndRunRules()'s own
    real-side-effect behavior (insert notification / insert task) with this
    layer's own 3 real action types."""
    config = json.loads(rule["action_config"])
    action_type = rule["action_type"]

    if action_type == "task_gateway_submit":
        text = config.get("text") or f"automation_rule {rule['rule_name']} fired for {rule['trigger_type']}"
        session_id = f"automation-rule-{rule['rule_id']}"
        rc, result = _run_json([
            "python3", TASK_GATEWAY, "submit",
            "--text", text, "--source", "ai_agent", "--session-id", session_id,
        ])
        return (rc == 0), result

    if action_type == "notify_owner":
        subject = config.get("subject") or f"Automation rule fired: {rule['rule_name']}"
        body = config.get("body") or f"Rule '{rule['rule_name']}' fired for trigger_type={rule['trigger_type']} with payload={json.dumps(payload)}"
        if not os.path.isfile(NOTIFY_OWNER):
            return False, {"error": f"notify-owner.py not found at {NOTIFY_OWNER}"}
        proc = subprocess.run(
            ["python3", NOTIFY_OWNER, "--subject", subject, "--body", body],
            capture_output=True, text=True,
        )
        return proc.returncode == 0, {"stdout": proc.stdout[-1000:], "stderr": proc.stderr[-1000:]}

    if action_type == "log_action":
        content = config.get("content") or f"automation_rule:{rule['rule_name']}:{rule['trigger_type']}"
        term = config.get("term") or "automation_rule_engine"
        rc, result = _run_json([
            "python3", SUPERBOSS, "log-action",
            "--source", "ai_agent", "--medium", "automation_rule_engine",
            "--content", content, "--term", term,
        ])
        return (rc == 0), result

    return False, {"error": f"unknown action_type {action_type}"}


def cmd_evaluate_rules(args):
    try:
        payload = json.loads(args.payload) if args.payload else {}
    except json.JSONDecodeError:
        print(json.dumps({"error": "--payload must be valid JSON"}, indent=2))
        sys.exit(1)

    conn = _connect()
    _ensure_tables(conn)
    rows = conn.execute(
        "SELECT * FROM automation_rules WHERE trigger_type = ? AND is_active = 1",
        (args.trigger_type,),
    ).fetchall()

    fired = []
    for r in rows:
        rule = dict(r)
        conditions = json.loads(rule["trigger_conditions"])
        if not _condition_matches(conditions, payload):
            continue

        if args.dry_run:
            fired.append({"rule_id": rule["rule_id"], "rule_name": rule["rule_name"],
                          "action_type": rule["action_type"], "would_fire": True, "dry_run": True})
            continue

        success, result = _dispatch_action(rule, payload)
        run_id = f"ARR-{uuid.uuid4().hex[:12]}"
        conn.execute(
            "INSERT INTO automation_rule_runs (run_id, rule_id, ts, trigger_payload, status, result_summary) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, rule["rule_id"], _now_iso(), json.dumps(payload),
             "success" if success else "failed", json.dumps(result)[:2000]),
        )
        conn.commit()
        fired.append({
            "rule_id": rule["rule_id"], "rule_name": rule["rule_name"],
            "action_type": rule["action_type"], "run_id": run_id,
            "status": "success" if success else "failed", "result": result,
        })

    conn.close()
    print(json.dumps({
        "trigger_type": args.trigger_type,
        "rules_evaluated": len(rows),
        "rules_fired": len(fired),
        "fired": fired,
    }, indent=2))


def build_parser():
    p = argparse.ArgumentParser(prog="automation_rule_engine.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("register-rule")
    r.add_argument("--rule-name", dest="rule_name", required=True)
    r.add_argument("--capability-name", dest="capability_name", default=None)
    r.add_argument("--trigger-type", dest="trigger_type", required=True)
    r.add_argument("--trigger-conditions", dest="trigger_conditions", default="{}",
                    help='JSON, e.g. {"field": "event", "operator": "equals", "value": "invoice.created"}')
    r.add_argument("--action-type", dest="action_type", required=True, choices=VALID_ACTION_TYPES)
    r.add_argument("--action-config", dest="action_config", default="{}")
    r.set_defaults(func=cmd_register_rule)

    l = sub.add_parser("list-rules")
    l.add_argument("--trigger-type", dest="trigger_type", default=None)
    l.set_defaults(func=cmd_list_rules)

    e = sub.add_parser("evaluate-rules")
    e.add_argument("--trigger-type", dest="trigger_type", required=True)
    e.add_argument("--payload", default="{}", help="JSON event payload")
    e.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="report which rules would fire without dispatching any action")
    e.set_defaults(func=cmd_evaluate_rules)

    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
