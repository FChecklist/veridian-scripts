#!/usr/bin/env python3
"""
VERIDIAN task watchdog -- ai-os/STANDING_DIRECTIVE.yaml v2_watchdog_service.
Built 2026-07-23, task-20260723-142643-build-veridian-task-watchdog-service.

Runs once per invocation (systemd timer, OnUnitActiveSec=60), scans every
active veridian-worker@ unit's task.yaml, flags STALL/LOOP, and on either
runs a bounded auto-recovery attempt before escalating to a fresh RCA task.

REUSED, NOT DUPLICATED (per this task's own KNOWN_CONTEXT constraint):
  - scripts/check_latest_task.py's exact `systemctl --user list-units
    veridian-worker@* --state=active --no-legend` invocation is the same
    query this script uses to find active units. check_latest_task.py only
    ever looks at the single newest task dir and blindly restarts it if no
    unit is active -- it does not read checkpoint history, cannot detect a
    stall (unit still active, just not progressing) or a loop, and has no
    RCA/escalation path. This script scans EVERY active unit's checkpoint
    history instead, which is why it supersedes rather than calls into it.
  - scripts/recover-failed-workers.py's task_id_from_unit() parsing
    convention (`unit.split("@", 1)[1].rsplit(".service", 1)[0]`) and its
    "read real evidence first, only touch units with a confirmed matching
    signature, print skipped ones for manual review" pattern is the model
    this script's step_1/step_2 signature-matched auto-recovery generalizes
    from one hardcoded failure type (402 balance errors) to any recurring
    checkpoint-note signature, via the new known_fixes table.
  - scripts/superboss-register.py's `log-fix` subcommand (added by this same
    task) is called via subprocess for every known_fixes write, rather than
    opening the sqlite file directly here -- keeps all writes going through
    that script's existing _write_lock() flock serialization instead of a
    second, parallel write path into the same canonical DB.
  - scripts/veridian-task.py's `create` subcommand (unmodified) is the
    step_3 escalation entrypoint; it already starts the new unit itself, so
    this script's own explicit `systemctl start` after create is a no-op
    safety net, not a real second start.

JUDGMENT CALLS (per this task's own OBJECTIVE -- these are not mechanical):

  1. STALL threshold: the spec's literal 20-minute checkpoint-staleness rule
     is applied as specified, but only against units systemctl currently
     reports active -- a unit that already exited (active_units==0) is
     either finished or crashed-to-terminal-state, neither of which this
     watchdog's stall/loop machinery is the right tool for (that is what
     recover-failed-workers.py's `failed`-state handling is for).

  2. LOOP false-positive filter: the spec's literal rule ("last 3
     checkpoints[].note are identical strings") is applied AS-IS, with one
     evidence-based exception. Every worker's harness appends a checkpoint
     roughly every 5 minutes regardless of whether anything happened, and
     when nothing new occurred that checkpoint's note is the fixed literal
     string "periodic checkpoint" -- confirmed live on
     task-20260723-141444-gap-closing-phase9-warnings-login-log-in, which
     had 3 consecutive "periodic checkpoint" notes while actively healthy
     (see watchdog.jsonl for this run's real evaluation of that task).
     Flagging that as a repeated-failure loop would fire on nearly every
     quiet-but-working task. LOOP_EXCLUDED_NOTES exists to name that one
     literal harness string as ineligible for loop detection; anything else
     -- including three identical *real* error/status sentences -- still
     triggers LOOP exactly per spec, because that IS the coincidental-vs-
     real distinction the spec's OBJECTIVE asks for, not a blanket exemption.

  3. fix_action is opaque, AI-authored free text (RCA tasks register it via
     `log-fix`). Blindly shell-executing it would be a real command-injection
     vector into an automated, unattended, 60-second-interval process. Step_2
     therefore only executes actions found in the small FIX_ACTIONS registry
     below; any other fix_action string is still recorded as "applied" (per
     spec) in known_fixes.last_applied/success_count via log-fix, but the
     watchdog performs no automated system action for it beyond that record
     -- action_taken in watchdog.jsonl says so explicitly, never fabricates
     a recovery that did not happen.
"""
import argparse
import glob
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

AI_OS = "/opt/veridian/ai-os"
TASKS_DIR = f"{AI_OS}/tasks"
LOGS_DIR = f"{AI_OS}/logs"
WATCHDOG_LOG = f"{LOGS_DIR}/watchdog.jsonl"
ATTENTION_PATH = f"{LOGS_DIR}/ATTENTION.md"
DB_PATH = "/opt/veridian/ai-os/memory/superboss-register.sqlite"
SUPERBOSS_REGISTER = "/opt/veridian/scripts/superboss-register.py"

STALL_MINUTES = 20
LOOP_COUNT = 3
RECHECK_DELAY_SECONDS = 60
SIGNATURE_LEN = 60

# 2026-07-27, SERVER RESOURCE GOVERNOR (ai-os/SERVER_RESOURCE_GOVERNOR_2026-07-27.md):
# every real spawn call site below now submits through resource_governor.submit()
# instead of calling systemctl/veridian-task.py directly -- the specific,
# concrete fix for the 2026-07-27 watchdog-timer incident (9h18m of unstopped
# 60s-interval firing with no "already in flight for this task_id" check).
# Tiers per the design doc's tier-mapping table: FIX_ACTIONS (get an
# already-stalled worker moving again) outrank a brand-new RCA escalation
# (investigative work spawned in response to a stall that, by definition, is
# already stuck and not made worse by a short queue delay).
GOVERNOR_TIER_FIX_ACTION = 2
GOVERNOR_TIER_ESCALATION = 4


def _governor():
    """Plain import (both files share scripts/, no hyphen in the module
    name) -- lazy, so a dry-run/import of this module never requires
    resource_governor.py's own dependencies (dispatch_core.py,
    superboss-register.py) to be importable unless a real fix/escalation
    path actually runs."""
    import resource_governor
    return resource_governor

LOOP_EXCLUDED_NOTES = {
    "periodic checkpoint",
}


def _now():
    return datetime.now(timezone.utc)


def _now_iso():
    return _now().isoformat()


def _parse_ts(ts):
    return datetime.fromisoformat(ts)


def list_active_task_ids():
    """Same query check_latest_task.py already uses, generalized from
    'count active units' to 'which task_ids are active' -- one real
    systemctl invocation, no duplicate polling mechanism."""
    r = subprocess.run(
        ["systemctl", "--user", "list-units", "veridian-worker@*", "--state=active", "--no-legend"],
        capture_output=True, text=True,
    )
    task_ids = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if not parts:
            continue
        unit = parts[0]
        if not unit.startswith("veridian-worker@"):
            continue
        # same convention as recover-failed-workers.py's task_id_from_unit()
        task_id = unit.split("@", 1)[1].rsplit(".service", 1)[0]
        task_ids.append(task_id)
    return task_ids


def load_task_yaml(task_id):
    path = f"{TASKS_DIR}/{task_id}/task.yaml"
    try:
        import yaml
        with open(path) as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        return None


def evaluate(task, task_id):
    """Pure function of (task.yaml dict) -> (stalled, loop_detected, note).
    No systemctl/subprocess/DB access here -- kept separable so it can be
    exercised directly against real historical task.yaml data for testing
    without side effects (see --dry-run-task)."""
    checkpoints = (task or {}).get("checkpoints") or []
    if not checkpoints:
        return False, False, ""

    last = checkpoints[-1]
    last_note = last.get("note") or ""
    last_at = last.get("at")

    stalled = False
    if last_at:
        try:
            age = (_now() - _parse_ts(last_at)).total_seconds() / 60.0
            stalled = age >= STALL_MINUTES
        except ValueError:
            stalled = False

    loop_detected = False
    if len(checkpoints) >= LOOP_COUNT:
        last_notes = [c.get("note") for c in checkpoints[-LOOP_COUNT:]]
        if len(set(last_notes)) == 1 and last_notes[0] and last_notes[0] not in LOOP_EXCLUDED_NOTES:
            loop_detected = True

    return stalled, loop_detected, last_note


def signature_of(note):
    return (note or "")[:SIGNATURE_LEN]


def search_prior_occurrence(signature):
    """step_1: does this exact error-signature substring appear in a PRIOR
    ATTENTION.md entry or task_audits row? Returns (found: bool, source: str)."""
    if not signature:
        return False, ""

    try:
        with open(ATTENTION_PATH) as f:
            content = f.read()
        if signature in content:
            return True, "ATTENTION.md"
    except FileNotFoundError:
        pass

    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS task_audits (audit_id TEXT PRIMARY KEY, ts TEXT, work_item_id TEXT, "
            "software_task_id TEXT, audit_cmd TEXT, exit_code INTEGER, stdout_tail TEXT, stderr_tail TEXT, verdict TEXT)"
        )
        like = f"%{signature}%"
        row = conn.execute(
            "SELECT audit_id FROM task_audits WHERE stdout_tail LIKE ? OR stderr_tail LIKE ? LIMIT 1",
            (like, like),
        ).fetchone()
        conn.close()
        if row:
            return True, "task_audits"
    except sqlite3.Error:
        pass

    return False, ""


def lookup_known_fix(signature):
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS known_fixes (signature TEXT PRIMARY KEY, fix_action TEXT NOT NULL, "
        "last_applied TEXT, success_count INTEGER NOT NULL DEFAULT 0)"
    )
    row = conn.execute("SELECT * FROM known_fixes WHERE signature=?", (signature,)).fetchone()
    conn.close()
    return dict(row) if row else None


def record_fix_applied(signature, fix_action):
    r = subprocess.run(
        ["python3", SUPERBOSS_REGISTER, "log-fix", "--signature", signature, "--fix-action", fix_action],
        capture_output=True, text=True,
    )
    return r.returncode == 0


def _fix_restart_unit(task_id):
    unit = f"veridian-worker@{task_id}.service"
    result = _governor().submit(
        {"task_identity": task_id, "task_kind": "systemctl_action", "unit_name": unit,
         "inputs": {"action": "restart"}},
        GOVERNOR_TIER_FIX_ACTION, source_trigger="veridian-task-watchdog:restart_unit",
    )
    return f"submitted restart of {unit} to resource governor (umr_id={result['umr_id']}): {result['reason']}"


def _fix_reset_failed_and_start(task_id):
    unit = f"veridian-worker@{task_id}.service"
    result = _governor().submit(
        {"task_identity": task_id, "task_kind": "systemctl_action", "unit_name": unit,
         "inputs": {"action": "reset_failed_and_start"}},
        GOVERNOR_TIER_FIX_ACTION, source_trigger="veridian-task-watchdog:reset_failed_and_start",
    )
    return f"submitted reset-failed+start of {unit} to resource governor (umr_id={result['umr_id']}): {result['reason']}"


FIX_ACTIONS = {
    "restart_unit": _fix_restart_unit,
    "reset_failed_and_start": _fix_reset_failed_and_start,
}


def apply_known_fix(task_id, fix_action):
    fn = FIX_ACTIONS.get(fix_action)
    if fn is None:
        return f"recorded known fix '{fix_action}' (unrecognized action name -- not in FIX_ACTIONS whitelist, no automated system action taken, logged only)"
    return fn(task_id)


RCA_PROMPT_TEMPLATE = """## OBJECTIVE
TASK REQUIRING AI JUDGMENT: {original_task_id} is stalled or looping (watchdog signature: "{signature}"). Read its task.yaml/worker.log/systemd.log, determine the real root cause (not a guess), and apply a real, reusable fix -- not a manual one-off patch.

## SCOPE
INPUT: /opt/veridian/ai-os/tasks/{original_task_id}/task.yaml, worker.log, systemd.log.
OUTPUT: a real fix applied to whatever file/service/config caused the stall or loop, PLUS one new row in the known_fixes table (superboss-register.sqlite) via `python3 scripts/superboss-register.py log-fix --signature "{signature}" --fix-action <name>` -- so this exact signature auto-resolves via step_2 next time, without a second RCA task.

## KNOWN_CONTEXT
zero_duplication_check_performed: true. This RCA task was auto-dispatched by scripts/veridian-task-watchdog.py's step_3 escalation (task-20260723-142643) after step_1 (search ai-os/logs/ATTENTION.md + task_audits for this signature) and step_2 (known_fixes lookup) found no existing recorded fix for this signature.

## SUCCESS_CRITERIA
`python3 scripts/superboss-register.py log-fix --signature "{signature}" --fix-action <name>` succeeds and returns success_count>=1 for this signature. Cite the real known_fixes row.

## EXPECTED_OUTPUT
COMMIT: whatever file(s) the real root cause requires. CHECKPOINT: status=pending_review, note=root cause + fix + known_fixes row as evidence.

## CONSTRAINTS
State the root cause honestly even if it is outside {original_task_id}'s own code (e.g. a shared script or DB). Register the fix in known_fixes even if the fix itself lives elsewhere.

## COMPLEXITY_TIER
judgment
"""


def escalate(task_id, task, signature, dry_run=False):
    title = f"rca-{task_id}"
    prompt = RCA_PROMPT_TEMPLATE.format(original_task_id=task_id, signature=signature)
    # Real fix (2026-07-27): this used to hardcode --repo claude-control for
    # every escalated rca- task regardless of the stalled task's own real
    # repo -- confirmed live against 2 historical instances where the
    # stalled task's real repo was compliance-tracker, not claude-control.
    # task.yaml's own 'repo' field (already loaded via load_task_yaml() by
    # the caller, passed in here) is the real, known-at-escalation-time repo
    # the RCA worker actually needs to investigate/fix in -- fall back to
    # claude-control only if the stalled task's own task.yaml is somehow
    # unreadable/missing that field.
    repo = (task or {}).get("repo") or "claude-control"
    if dry_run:
        return f"DRY_RUN would escalate via resource governor: title={title} repo={repo}"

    # 2026-07-27: routes through resource_governor.submit() instead of calling
    # veridian-task.py create + systemctl start directly (see GOVERNOR_TIER_*
    # docstring above) -- task_identity=title (the "rca-<task_id>" string)
    # means a second escalation attempt for the SAME stalled task_id, fired by
    # ANY trigger while the first is still queued/dispatched/running, is
    # rejected as a duplicate rather than creating a second RCA task. This is
    # the concrete fix for the 2026-07-27 watchdog-timer incident.
    result = _governor().submit(
        {"task_identity": title, "task_kind": "veridian_task_create",
         "inputs": {"title": title, "repo": repo, "prompt": prompt}},
        GOVERNOR_TIER_ESCALATION, source_trigger="veridian-task-watchdog:escalate",
    )
    if not result["accepted"]:
        return f"escalation NOT queued (resource governor): {result['reason']}"
    return f"escalation queued via resource governor: umr_id={result['umr_id']} title={title}"


def process_task(task_id, task, dry_run_escalation=False):
    stalled, loop_detected, last_note = evaluate(task, task_id)
    status = (task or {}).get("status", "unknown")
    entry = {
        "ts": _now_iso(),
        "task_id": task_id,
        "status": status,
        "stalled": stalled,
        "loop_detected": loop_detected,
        "action_taken": "none",
    }

    if not (stalled or loop_detected):
        return entry

    signature = signature_of(last_note)
    found, source = search_prior_occurrence(signature)
    known_fix = lookup_known_fix(signature) if found else None

    if found and known_fix:
        applied_desc = apply_known_fix(task_id, known_fix["fix_action"])
        record_fix_applied(signature, known_fix["fix_action"])
        time.sleep(RECHECK_DELAY_SECONDS)
        recheck_task = load_task_yaml(task_id)
        still_bad, still_loop, _ = evaluate(recheck_task, task_id)
        if not (still_bad or still_loop):
            entry["action_taken"] = f"step_2: {applied_desc} (signature seen before via {source}); recheck after {RECHECK_DELAY_SECONDS}s: recovered"
            return entry
        entry["action_taken"] = f"step_2: {applied_desc} (signature seen before via {source}); recheck after {RECHECK_DELAY_SECONDS}s: still stalled/looping -> "
        esc = escalate(task_id, task, signature, dry_run=dry_run_escalation)
        entry["action_taken"] += f"step_3: {esc}"
        return entry

    reason = "no prior occurrence found (step_1)" if not found else "prior occurrence found but no known_fixes entry (step_2 not applicable)"
    esc = escalate(task_id, task, signature, dry_run=dry_run_escalation)
    entry["action_taken"] = f"step_1: {reason} -> step_3: {esc}"
    return entry


def append_jsonl(entry):
    os.makedirs(os.path.dirname(WATCHDOG_LOG), exist_ok=True)
    with open(WATCHDOG_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run-task", default=None,
                     help="TEST ONLY: evaluate exactly this task_id's real task.yaml as if its unit were active, "
                          "without querying systemctl. Not used by the production systemd timer invocation (no args).")
    ap.add_argument("--dry-run-escalation", action="store_true",
                     help="TEST ONLY: log what step_3 would run instead of actually dispatching a new billed task.")
    args = ap.parse_args()

    if args.dry_run_task:
        task_ids = [args.dry_run_task]
    else:
        task_ids = list_active_task_ids()

    results = []
    for task_id in task_ids:
        task = load_task_yaml(task_id)
        entry = process_task(task_id, task, dry_run_escalation=args.dry_run_escalation)
        append_jsonl(entry)
        results.append(entry)
        print(json.dumps(entry))

    return results


if __name__ == "__main__":
    main()
