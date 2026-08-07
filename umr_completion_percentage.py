#!/usr/bin/env python3
"""
umr_completion_percentage.py -- real, deterministic per-UMR percent-completion
calculator (Owner standing order 2026-08-07, governing chain
UMR-20260806-124055-bc80): "no manual AI-judged percentages anywhere, ever --
every completion percent reported by the PM or the cron must come from a real
deterministic script, not AI estimation."

Precedent check done before writing this (per this task's own SPEC): the 17
existing capability_registry rows include nothing that computes a per-UMR
percent (generate_platform_completion_checklist.py is scoped to whole-platform
scripts/tables, not a single umr_id). This is new, not a duplicate.

Five real, closed-ended rules, evaluated in this fixed order for one umr_id.
The first rule whose precondition holds wins -- never falls through to a later
rule once one has matched:

  1. status == 'completed' AND ts_completed is a real non-null timestamp AND
     outputs_json parses to a real non-empty dict with at least one real
     (non-null/non-empty) value -> 100.
  2. A real task.yaml exists on disk for this row's new_task_id (from
     outputs_json.new_task_id, the real fresh dispatched task id -- see
     resource_governor.py's own _recorded_new_task_ids_for_identity()) or for
     task_identity itself, AND its completed_steps/remaining_steps are both
     real YAML lists with at least one entry between them -> round(100 *
     len(completed_steps) / (len(completed_steps) + len(remaining_steps))).
     Applies regardless of status (this is rule 5's "running" case too --
     running is just the common status this actually fires for).
  3. status == 'failed' and no real task.yaml (rule 2) showed genuine
     progress -> 0.
  4. status == 'queued' and ts_dispatched is NULL -> 0.
  5. Anything else (no umr_tasks row at all; malformed outputs_json; a
     terminal status.yaml never wrote real evidence; a non-terminal status
     with no task.yaml on disk; a task.yaml with empty completed_steps AND
     empty remaining_steps, i.e. an unresolvable 0/0) -> percent: null, with
     a real, specific reason string. Never a guess.

task.yaml discovery deliberately checks BOTH the row's own new_task_id and its
task_identity, because in practice (verified live against real rows, see the
5 test UMR ids below) task_identity is the pre-dispatch owner-task identity
string and is almost never itself a real TASKS_DIR entry -- the real
dispatched task directory is named after new_task_id, minted at spawn time by
veridian-task.py create and recorded into outputs_json by
resource_governor.py's _perform_spawn(). Both are tried (new_task_id first)
so a row whose task_identity IS itself a real task dir (an older/different
dispatch shape) still resolves.

Reuses the existing DB_PATH resolution + connection + row helpers from
superboss-register.py via the same in-process importlib load convention
agent_work_briefing.py / resource_governor.py already use for this
hyphenated-filename module -- never re-derives DB_PATH or reimplements a
second sqlite connector.

Usage:
    python3 umr_completion_percentage.py UMR-xxxx [UMR-yyyy ...]
    python3 umr_completion_percentage.py --umr-id UMR-xxxx --umr-id UMR-yyyy

Prints one real JSON array to stdout, one object per input umr_id (input
order preserved, duplicates preserved as given), each object shaped:
    {"umr_id": ..., "percent": <int or null>, "rule": "<rule tag>",
     "reason": "<real, specific string>"}
"""
import argparse
import importlib.util as _ilu
import json
import os
import sys

import yaml

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
VERIDIAN_ROOT = os.environ.get("VERIDIAN_ROOT", "/opt/veridian")
AI_OS = os.environ.get("VERIDIAN_AI_OS_DIR", f"{VERIDIAN_ROOT}/ai-os")
TASKS_DIR = os.environ.get("VERIDIAN_TASKS_DIR", f"{AI_OS}/tasks")

_sbr = None


def _superboss_register():
    """In-process importlib load of superboss-register.py -- same convention
    agent_work_briefing.py's/resource_governor.py's own _superboss_register()
    already use (the filename has a hyphen, so it cannot be a plain
    `import`). Gives us the one real, canonical DB_PATH resolution + _connect()
    without re-deriving either here."""
    global _sbr
    if _sbr is None:
        _spec = _ilu.spec_from_file_location(
            "superboss_register_umr_percent", os.path.join(SCRIPTS, "superboss-register.py"))
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sbr = _mod
    return _sbr


def _load_task_yaml(task_dir_name):
    """Real read of TASKS_DIR/<task_dir_name>/task.yaml via PyYAML
    safe_load, same parser backfill_phase_self_report.py's own sweep()/
    backfill_one() already use for this exact file. Returns the parsed dict,
    or None if the directory/file genuinely doesn't exist or the YAML is
    unparseable -- never raises, never fabricates a partial doc."""
    if not task_dir_name:
        return None
    path = os.path.join(TASKS_DIR, task_dir_name, "task.yaml")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return None
    return doc if isinstance(doc, dict) else None


def _find_task_yaml(task_identity, new_task_id):
    """Try new_task_id first (the real dispatched-task-dir naming
    convention), then task_identity. Returns (doc, matched_id, matched_via)
    or (None, None, None) if neither resolves to a real task.yaml."""
    if new_task_id:
        doc = _load_task_yaml(new_task_id)
        if doc is not None:
            return doc, new_task_id, "new_task_id"
    if task_identity:
        doc = _load_task_yaml(task_identity)
        if doc is not None:
            return doc, task_identity, "task_identity"
    return None, None, None


def _steps_percent(doc):
    """Rule 2/5's real formula, read directly from the real YAML lists.
    Returns (percent_or_None, detail_reason). percent is None (never
    estimated) when either list isn't a real list, or when both lists are
    empty (0/0 has no real answer)."""
    completed = doc.get("completed_steps")
    remaining = doc.get("remaining_steps")
    if not isinstance(completed, list) or not isinstance(remaining, list):
        return None, (
            f"completed_steps/remaining_steps are not both real YAML lists "
            f"(completed_steps={completed!r}, remaining_steps={remaining!r})")
    total = len(completed) + len(remaining)
    if total == 0:
        return None, "completed_steps and remaining_steps are both empty (0/0 has no real percent)"
    percent = round(100 * len(completed) / total)
    return percent, f"len(completed_steps)={len(completed)}, len(remaining_steps)={len(remaining)}, total={total}"


def _parse_outputs(outputs_json):
    """Real, defensive JSON parse of umr_tasks.outputs_json. Returns
    (dict_or_None, had_real_evidence: bool). had_real_evidence is True only
    for a real, non-empty dict with at least one non-null/non-empty value --
    '{}' , null, a malformed string, or a dict of only empty/null values all
    count as no real evidence."""
    if not outputs_json:
        return None, False
    try:
        outputs = json.loads(outputs_json)
    except (TypeError, ValueError):
        return None, False
    if not isinstance(outputs, dict) or not outputs:
        return outputs if isinstance(outputs, dict) else None, False
    had_evidence = any(v not in (None, "", [], {}) for v in outputs.values())
    return outputs, had_evidence


def compute_completion(umr_id, row):
    """Pure function: real umr_tasks row (a sqlite3.Row / dict-like, or None
    if no such row exists) -> one real result dict. Never touches the
    network, never asks an LLM for a judgment call -- disk + DB reads and
    arithmetic only."""
    if row is None:
        return {"umr_id": umr_id, "percent": None, "rule": "not_found",
                "reason": f"no row in umr_tasks for umr_id={umr_id!r}"}

    status = row["status"]
    ts_dispatched = row["ts_dispatched"]
    ts_completed = row["ts_completed"]
    task_identity = row["task_identity"]
    outputs, had_evidence = _parse_outputs(row["outputs_json"])
    new_task_id = outputs.get("new_task_id") if isinstance(outputs, dict) else None

    # Rule 1: completed + real ts_completed + real non-empty evidence -> 100.
    if status == "completed" and ts_completed and had_evidence:
        return {
            "umr_id": umr_id, "percent": 100, "rule": "rule1_completed_with_evidence",
            "reason": (
                f"status='completed', ts_completed={ts_completed!r} is a real non-null "
                f"timestamp, outputs_json has real non-empty evidence field(s): "
                f"{sorted(outputs.keys())!r}"),
        }

    # Rule 2 / 5: a real task.yaml exists (any status) -> steps formula.
    doc, matched_id, matched_via = _find_task_yaml(task_identity, new_task_id)
    if doc is not None:
        percent, detail = _steps_percent(doc)
        if percent is not None:
            rule = "rule5_running_task_yaml_steps" if status == "running" else "rule2_task_yaml_steps"
            return {
                "umr_id": umr_id, "percent": percent, "rule": rule,
                "reason": (
                    f"real task.yaml found at tasks/{matched_id}/task.yaml (matched via "
                    f"{matched_via}={matched_id!r}, status={status!r}): {detail}"),
            }
        return {
            "umr_id": umr_id, "percent": None, "rule": "unresolved_task_yaml_no_steps",
            "reason": (
                f"real task.yaml found at tasks/{matched_id}/task.yaml (matched via "
                f"{matched_via}={matched_id!r}) but its step lists cannot be resolved to a "
                f"percent: {detail}"),
        }

    # Rule 3: failed, no task.yaml showed genuine progress -> 0.
    if status == "failed":
        return {
            "umr_id": umr_id, "percent": 0, "rule": "rule3_failed_no_task_yaml_progress",
            "reason": (
                f"status='failed' and no real task.yaml exists for task_identity="
                f"{task_identity!r} or new_task_id={new_task_id!r} to show genuine progress"),
        }

    # Rule 4: queued, never dispatched -> 0.
    if status == "queued" and not ts_dispatched:
        return {
            "umr_id": umr_id, "percent": 0, "rule": "rule4_queued_not_dispatched",
            "reason": "status='queued' and ts_dispatched is NULL",
        }

    # Rule 5 (unresolved fallback): document why, never guess.
    if status == "completed":
        reason = (
            f"status='completed' but rule 1's evidence check failed "
            f"(ts_completed={ts_completed!r}, outputs_json real-evidence={had_evidence}) and "
            f"no real task.yaml exists for task_identity={task_identity!r} or "
            f"new_task_id={new_task_id!r} to fall back to the steps formula")
    elif status == "queued":
        reason = f"status='queued' but ts_dispatched={ts_dispatched!r} is non-null (inconsistent row state)"
    else:
        reason = (
            f"status={status!r} has no matching rule and no real task.yaml exists for "
            f"task_identity={task_identity!r} or new_task_id={new_task_id!r}")
    return {"umr_id": umr_id, "percent": None, "rule": "unresolved", "reason": reason}


def compute_many(umr_ids, conn):
    results = []
    for umr_id in umr_ids:
        row = conn.execute("SELECT * FROM umr_tasks WHERE umr_id=?", (umr_id,)).fetchone()
        results.append(compute_completion(umr_id, row))
    return results


def main():
    ap = argparse.ArgumentParser(
        description="Real, deterministic percent-completion for one or more umr_id(s). "
                    "Never AI-judged -- see module docstring for the 5 real rules.")
    ap.add_argument("umr_ids", nargs="*", metavar="UMR-...", help="one or more umr_id positional args")
    ap.add_argument("--umr-id", dest="umr_id_opt", action="append", default=[],
                     metavar="UMR-...", help="repeatable alternative to positional args")
    args = ap.parse_args()

    umr_ids = list(args.umr_ids) + list(args.umr_id_opt)
    if not umr_ids:
        ap.error("at least one umr_id required (positional or --umr-id)")

    sbr = _superboss_register()
    sbr.init_db_silent()
    conn = sbr._connect()
    try:
        results = compute_many(umr_ids, conn)
    finally:
        conn.close()

    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
