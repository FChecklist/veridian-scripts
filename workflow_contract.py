#!/usr/bin/env python3
"""
workflow_contract.py -- shared reference implementation of
ai-os/WORKFLOW_CONTRACT_SCHEMA_2026-07-24.yaml's workflow_contract_schema
(phase_3_workflow_automation_integration, closes engine 8 -- Workflow
Engine). scripts/task-gateway.py imports WORKFLOW_PHASES from this module
rather than hand-typing phase strings, so both files stay in sync with the
one named contract. Same shared-reference-implementation pattern as Phase
2's scripts/policy_decision.py.

Subcommands: describe, validate-sequence
"""
import argparse
import json
import os
import sqlite3
import sys

VERIDIAN_ROOT = "/opt/veridian"
AI_OS = f"{VERIDIAN_ROOT}/ai-os"
SCRIPTS = f"{VERIDIAN_ROOT}/scripts"
SUPERBOSS_REGISTER = os.path.join(SCRIPTS, "superboss-register.py")
CONTRACT_SCHEMA_PATH = f"{AI_OS}/WORKFLOW_CONTRACT_SCHEMA_2026-07-24.yaml"

SCHEMA_VERSION = "1.0"

# Real, canonical DB-path resolution -- same lazy-import-cached convention
# reconcile_stale_running_workers.py / worker-exit-status-bridge.py already use:
# never a hardcoded path, always the one real SUPERBOSS_REGISTER_DB override every
# other real caller already honors.
_sbr = None


def _superboss_register():
    global _sbr
    if _sbr is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "superboss_register_workflow_contract", SUPERBOSS_REGISTER)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sbr = _mod
    return _sbr


def _resolve_db_path():
    return _superboss_register().resolve_superboss_db_path()

# workflow_contract_schema.phases_enum, verbatim from the schema file.
WORKFLOW_PHASES = ["submitted", "planned", "dispatched", "logged", "closed"]

# Single source of truth for the literal_template's required ## SECTION headers.
# Previously hardcoded independently in task-gateway.py's REQUIRED_SECTIONS list
# AND tight_task_validation.py's FIELD_HEADER_RE alternation (task-20260726-092433
# dedup audit, SCOPE item 3) -- both enforced the exact same 7-name set, just in
# two different shapes (presence-check list vs regex alternation), and would have
# silently drifted the moment one was updated without the other. Every caller that
# needs to know "is this text a fully-labeled task spec" now imports from here.
REQUIRED_TASK_SECTIONS = [
    "OBJECTIVE", "SCOPE", "KNOWN_CONTEXT", "SUCCESS_CRITERIA",
    "EXPECTED_OUTPUT", "CONSTRAINTS", "COMPLEXITY_TIER",
]


def has_all_required_sections(text):
    """True iff every REQUIRED_TASK_SECTIONS name appears as a literal
    '## NAME' header in text -- the same check task-gateway.py's cmd_start
    used to do inline, now shared so gateway.py's lifecycle router (which
    needs the identical "is this a complete task spec" answer to decide
    whether a raw Owner message is start-ready) can't drift from it."""
    return all(f"## {s}" in text for s in REQUIRED_TASK_SECTIONS)

# task-gateway.py subcommand -> the phase it represents, per
# WORKFLOW_CONTRACT_SCHEMA_2026-07-24.yaml's phase_mapping.task_gateway_py.
TASK_GATEWAY_SUBCOMMAND_PHASE = {
    "submit": "submitted",
    "start": "dispatched",
    "log": "logged",
    "close": "closed",
}

# The order a real task's work_items rows are expected to progress through.
# "planned" has no distinct work_items row in task-gateway.py's own real
# shape (tight_task_validation.py runs inline inside cmd_start, before the
# work_item is created) -- so a valid observed sequence never contains it;
# this is documented, not treated as a bug, in validate-sequence's output.
EXPECTED_OBSERVABLE_ORDER = ["submitted", "dispatched", "logged", "closed"]


def phase_for_task_gateway_subcommand(subcommand):
    return TASK_GATEWAY_SUBCOMMAND_PHASE.get(subcommand)


def cmd_describe(args):
    print(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "schema_path": CONTRACT_SCHEMA_PATH,
        "phases_enum": WORKFLOW_PHASES,
        "task_gateway_py_subcommand_phase_mapping": TASK_GATEWAY_SUBCOMMAND_PHASE,
    }, indent=2))


def cmd_validate_sequence(args):
    """Best-effort, read-only: reads every actions/work_items row this
    session has real data for (keyed by task_id via work_items.ai_task_id)
    and reports whether the observed subcommand-derived phases are
    non-decreasing against EXPECTED_OBSERVABLE_ORDER. This does not invent
    a new status column -- it re-derives phase purely from which
    task-gateway.py subcommands already ran, per log-action content strings
    (task_start:/task_gateway,log/task_close: -- the same content
    conventions cmd_start/cmd_log/cmd_close already write)."""
    conn = sqlite3.connect(_resolve_db_path())
    conn.row_factory = sqlite3.Row
    wi = conn.execute(
        "SELECT work_item_id, instruction_id, status FROM work_items "
        "WHERE ai_task_id = ? OR software_task_id = ? ORDER BY ts DESC LIMIT 1",
        (args.task_id, args.task_id),
    ).fetchone()
    if not wi:
        conn.close()
        print(json.dumps({"error": f"no work_items row found for task_id {args.task_id}"}, indent=2))
        sys.exit(1)

    actions = conn.execute(
        "SELECT content, ts FROM actions WHERE work_item_id = ? ORDER BY ts ASC",
        (wi["work_item_id"],),
    ).fetchall()
    conn.close()

    observed_phases = ["dispatched"]  # a work_items row's existence at all implies cmd_start already ran
    for row in actions:
        content = row["content"] or ""
        if content.startswith("task_close:"):
            observed_phases.append("closed")
        else:
            # cmd_close's own close_cmd is a log-work call, not log-action,
            # so it never reaches this actions table -- every actions row
            # against this work_item other than an explicit task_close:
            # marker is a real cmd_log invocation, i.e. a "logged" phase
            # observation.
            observed_phases.append("logged")

    is_non_decreasing = True
    last_idx = -1
    for phase in observed_phases:
        idx = EXPECTED_OBSERVABLE_ORDER.index(phase) if phase in EXPECTED_OBSERVABLE_ORDER else -1
        if idx < last_idx:
            is_non_decreasing = False
        last_idx = max(last_idx, idx)

    print(json.dumps({
        "task_id": args.task_id,
        "work_item_id": wi["work_item_id"],
        "work_item_status": wi["status"],
        "observed_phases": observed_phases,
        "expected_observable_order": EXPECTED_OBSERVABLE_ORDER,
        "sequence_valid": is_non_decreasing,
    }, indent=2))


def build_parser():
    p = argparse.ArgumentParser(prog="workflow_contract.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("describe")
    d.set_defaults(func=cmd_describe)

    v = sub.add_parser("validate-sequence")
    v.add_argument("--task-id", dest="task_id", required=True)
    v.set_defaults(func=cmd_validate_sequence)

    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
