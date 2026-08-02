#!/usr/bin/env python3
"""dispatch-tick.py -- task-20260726-210339 consolidation of supervisor-sweep.sh +
queue-dispatcher.py + module-queue-dispatcher.py into one script sharing
dispatch_core.py's concurrency gate. Run via cron (see this task's PR body for the
proposed schedule -- not added to the live crontab by this task).

Same real sub-behaviors as the 3 scripts it replaces, unchanged:
  1. supervisor-sweep discovery: any task with status=pending_review and no
     review.json (a missed supervisor trigger, OR a `veridian-task.py adopt`ed
     task -- adopt leaves exactly this shape on purpose) gets its supervisor
     started.
  2. gap_queue.yaml dispatch: syncs dispatched-item status from real task.yaml
     state, honors dispatch_paused/held_task_ids exactly as queue-dispatcher.py
     did (see gap_queue_tick()'s docstring -- this task changes NONE of that
     gate's values or semantics), runs the same existing_scope_conflict()
     duplication guard, dispatches via veridian-task.py create.
  3. module queue dispatch (ai-os/queues/*.yaml): same dependency_met() graph,
     same veridian-task.py create call, same module_scope.yaml sidecar --
     against the SAME shared concurrency pool as (2), never a separate one
     (this is what module-queue-dispatcher.py's own docstring already said its
     CONCURRENCY_CAP=3 was *meant* to be, but the old code never actually
     enforced it -- separate lock file, separate cap check).

The one real behavior change: every actual spawn call site (systemctl start /
veridian-task.py create) now acquires dispatch_core.acquire_dispatch_lock() and
checks dispatch_core.has_free_slot() first -- across all 3 sub-behaviors AND
across whatever else on the box also imports dispatch_core (phase-continuation-
tick.py). This is the fix: previously each of these 3 mechanisms could each
independently decide "I have room" using its own private accounting (or, for
supervisor-sweep.sh, no accounting at all) and spawn anyway, which is exactly
how 3 veridian-supervisor@ units started in one tick 1 second before the real
2026-07-26 19:00:38 UTC OOM-kill.

Not preserved from supervisor-sweep.sh: its own per-run timestamped log file
and 14-day log rotation (`supervisor-sweep-<ts>.log`, `find ... -mtime +14
-delete`). That existed only because supervisor-sweep.sh's crontab entry had no
external `>> log 2>&1` redirect of its own (unlike queue-dispatcher.py's entry,
which already did) -- every other one of the 6 scripts this task consolidates
already relies on that external redirect + run-logged.sh instead of managing
its own log file. This script follows that same, already-majority convention;
its proposed cron entry (see PR body) redirects to logs/dispatch-tick.log like
the others.

4. resume_interrupted_workers_tick (added 2026-08-01, RCA fix for the 24-unit
   OOM-kill incident): veridian-worker@ units are no longer `systemctl --user
   enable`d (see veridian-task.py's cmd_create and veridian-worker@.service's
   own comments for the full root cause), so a task that was `in_progress` at
   the moment of a reboot/crash no longer auto-restarts on its own -- nothing
   in systemd's boot sequence knows to start it. This tick notices exactly
   that: any task.yaml with status in {"pending", "in_progress"} whose
   veridian-worker@ unit is NOT currently active, and re-submits it through
   resource_governor.py's submit()/umr_tasks queue (task_kind=
   "systemctl_action", action=start or reset_failed_and_start) -- never a
   direct `systemctl start` here. Going through the queue means N interrupted
   tasks after a reboot are subject to the exact same dispatch_core cap/lock
   and resource_governor 4-metric gate as any brand-new task, so they trickle
   back in at the existing concurrency cap instead of all firing at once
   (the same shape of bug this whole RCA fix closes, just for "many tasks
   resume at once" instead of "many tasks boot at once").
"""
import argparse
import contextlib
import datetime
import fcntl
import glob as globmod
import json
import os
import re
import subprocess
import sys

import yaml

import dispatch_core
import resource_governor

VERIDIAN_ROOT = dispatch_core.VERIDIAN_ROOT
AI_OS = dispatch_core.AI_OS
SCRIPTS = dispatch_core.SCRIPTS
TASKS_DIR = dispatch_core.TASKS_DIR

# Non-terminal task.yaml statuses: work that was still live (or about to
# start) and is therefore a candidate for resume-after-interruption if its
# unit isn't currently running. Deliberately excludes "blocked"/"failed"
# (worker-entrypoint.sh already `systemctl --user disable`s the unit itself
# on those -- a human decision is needed, not an automatic resume) and
# "pending_review"/"awaiting_human_approval" (the work is already done;
# supervisor_sweep_tick above is what re-triggers those, not this).
RESUMABLE_STATUSES = {"pending", "in_progress"}

GAP_QUEUE_PATH = os.environ.get("VERIDIAN_GAP_QUEUE_PATH", f"{AI_OS}/gap_queue.yaml")
GAP_QUEUE_LOCK = os.environ.get("VERIDIAN_GAP_QUEUE_LOCK", f"{AI_OS}/.gap_queue.lock")
MODULE_QUEUES_DIR = os.environ.get("VERIDIAN_MODULE_QUEUES_DIR", f"{AI_OS}/queues")
MODULE_QUEUES_LOCK = os.environ.get("VERIDIAN_MODULE_QUEUES_LOCK", f"{AI_OS}/.module_queues.lock")
TASK_MANAGER = os.environ.get("VERIDIAN_TASK_MANAGER", f"{SCRIPTS}/veridian-task.py")
REPO = "compliance-tracker"
REPO_PATH = f"{VERIDIAN_ROOT}/repos/{REPO}"
MAX_RETRIES = 3

TERMINAL_GOOD = {"completed"}
TERMINAL_BAD = {"blocked", "failed"}
TERMINAL_HOLD = {"awaiting_human_approval"}

# Stuck-task / heartbeat surface (added 2026-08-02, PM directive: this tick is
# the one mechanism on the box that is genuinely laptop-independent -- the
# interactive tmux session and any PM laptop session both depend on either
# being alive to catch stuck work, this tick does not). Read-only w.r.t. task
# state: this only ever reports, it never flips a status or dispatches
# anything -- a blocked task needs a real PM decision, not an auto-resolve.
# Checked MASTER_INDEX.yaml's registries/quick_reference first: no existing
# stuck-task or heartbeat file convention to extend, so this is a new single
# canonical file, same non-git ai-os/ live-runtime-state directory as
# CONTROLLER.yaml/ATTENTION.md.
STUCK_TASK_THRESHOLD_MINUTES = float(
    os.environ.get("VERIDIAN_STUCK_TASK_THRESHOLD_MINUTES", "30"))
STUCK_TASKS_HEARTBEAT_PATH = os.environ.get(
    "VERIDIAN_STUCK_TASKS_HEARTBEAT_PATH", f"{AI_OS}/STUCK_TASKS_HEARTBEAT.json")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _queue_lock(lock_path):
    @contextlib.contextmanager
    def _cm():
        os.makedirs(os.path.dirname(lock_path), exist_ok=True)
        with open(lock_path, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    return _cm()


def _atomic_save_yaml(path, doc):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
    os.replace(tmp, path)


def _atomic_save_json(path, doc):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=2, default=str)
        f.write("\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 1. supervisor-sweep discovery (was supervisor-sweep.sh)
# ---------------------------------------------------------------------------

def supervisor_sweep_tick(tasks):
    started, skipped_cap = [], []
    for task_id, doc in tasks.items():
        if doc.get("status") != "pending_review" or doc["_has_review_json"]:
            continue
        with dispatch_core.acquire_dispatch_lock():
            if not dispatch_core.has_free_slot():
                print(f"SKIP supervisor start (cap reached): {task_id}")
                skipped_cap.append(task_id)
                continue
            print(f"Missed trigger found: {task_id} -- starting supervisor")
            run(["systemctl", "--user", "daemon-reload"])
            run(["systemctl", "--user", "start", f"veridian-supervisor@{task_id}.service"])
            started.append(task_id)
        dispatch_core.record_dispatch_event(
            task_id=task_id, dispatched_by="dispatch-tick.py:supervisor_sweep",
            source_queue_or_plan="supervisor_sweep_discovery",
            worker_unit=f"veridian-supervisor@{task_id}.service")
    return {"started": started, "skipped_cap": skipped_cap}


# ---------------------------------------------------------------------------
# 1b. resume-after-interruption (added 2026-08-01, 24-unit OOM-kill RCA fix)
# ---------------------------------------------------------------------------

def _unit_active_state(unit):
    """Real, current systemd --user ActiveState for `unit` ('active',
    'inactive', 'failed', 'activating', ...), never a self-tracked guess.
    Returns 'unknown' if systemctl itself can't answer (unit truly never
    existed this boot) -- treated the same as 'inactive' by the caller."""
    r = run(["systemctl", "--user", "show", unit, "-p", "ActiveState", "--value"])
    state = r.stdout.strip()
    return state or "unknown"


def resume_interrupted_workers_tick(tasks):
    """Finds every task.yaml left in a non-terminal status (RESUMABLE_STATUSES)
    whose veridian-worker@ unit is NOT currently active -- the real signature
    of "was mid-work when the box rebooted/crashed, and nothing auto-started
    it" now that these units are never boot-enabled (see module docstring).
    Re-submits each one through resource_governor.submit() -- the queue, not
    a direct systemctl call -- so resource_governor's own dispatch_one()
    (dispatch_core-gated, plus the 4-metric resource gate) is what actually
    restarts it, at the existing cap, whenever it next runs a tick.

    task_identity=task_id gives resource_governor.submit() its existing
    de-dup for free: a task already queued/dispatched/running in umr_tasks
    (e.g. a previous tick already resubmitted it, or it's mid-run right now)
    is rejected as a duplicate rather than double-queued -- this function is
    safe to call every tick, not just once after a reboot.
    """
    resumed, skipped_running, skipped_duplicate = [], [], []
    for task_id, doc in tasks.items():
        service = doc.get("service")
        if not service or not service.startswith("veridian-worker@"):
            continue
        if doc.get("status") not in RESUMABLE_STATUSES:
            continue

        state = _unit_active_state(service)
        if state in ("active", "activating", "reloading"):
            skipped_running.append(task_id)
            continue

        action = "reset_failed_and_start" if state == "failed" else "start"
        result = resource_governor.submit(
            task_spec={
                "task_identity": task_id,
                "task_kind": "systemctl_action",
                "unit_name": service,
                "inputs": {"action": action, "resumed_after_state": state},
            },
            tier=1,  # already-started work outranks brand-new dispatch in priority
            source_trigger="dispatch-tick:resume_interrupted_workers",
        )
        if result["accepted"]:
            resumed.append(task_id)
            print(f"RESUME QUEUED: {task_id} (unit was {state!r}, action={action}, umr_id={result['umr_id']})")
        else:
            skipped_duplicate.append(task_id)
            print(f"SKIP resume (already in queue): {task_id} -- {result['reason']}")

    return {"resumed": resumed, "skipped_running": skipped_running, "skipped_duplicate": skipped_duplicate}


# ---------------------------------------------------------------------------
# 1c. stuck-task detection + real heartbeat (added 2026-08-02, PM directive)
# ---------------------------------------------------------------------------

def _parse_iso_ts(value):
    """Best-effort ISO-8601 -> aware datetime. Returns None on anything
    missing/unparseable rather than raising -- a task.yaml with a malformed
    or absent timestamp must never crash the tick, just be skipped from
    stuck-task detection (it'll show up again next tick with better data,
    or not at all)."""
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _last_checkpoint_note(doc):
    """The note on the most recent checkpoints[] entry, if any -- the real
    last thing the worker (or supervisor) said before the task went quiet.
    Falls back to None, never a fabricated string, so a genuinely-missing
    note is visibly absent in the heartbeat file rather than papered over."""
    checkpoints = doc.get("checkpoints") or []
    if not checkpoints:
        return None
    return checkpoints[-1].get("note")


def find_stuck_tasks(tasks, now, threshold_minutes=None):
    """Any task.yaml with status=='blocked' whose last_checkpoint_at is older
    than threshold_minutes (default STUCK_TASK_THRESHOLD_MINUTES). Blocked is
    a terminal-for-automation status (worker-entrypoint.sh already disables
    the unit on it, see RESUMABLE_STATUSES' own comment above) -- nothing
    else on the box will touch it again without a real PM decision, so
    last_checkpoint_at not advancing IS "no new checkpoint note in that
    window" by construction; there is no separate note-freshness check to
    make. Purely a read: never mutates a task.yaml or dispatches anything."""
    threshold_minutes = STUCK_TASK_THRESHOLD_MINUTES if threshold_minutes is None else threshold_minutes
    stuck = []
    for task_id, doc in tasks.items():
        if doc.get("status") != "blocked":
            continue
        last_at = _parse_iso_ts(doc.get("last_checkpoint_at"))
        if last_at is None:
            continue
        blocked_minutes = (now - last_at).total_seconds() / 60.0
        if blocked_minutes < threshold_minutes:
            continue
        stuck.append({
            "task_id": task_id,
            "blocked_since": doc.get("last_checkpoint_at"),
            "blocked_minutes": round(blocked_minutes, 1),
            "last_note": _last_checkpoint_note(doc),
        })
    stuck.sort(key=lambda item: -item["blocked_minutes"])
    return stuck


def _real_load_average():
    try:
        load1, load5, load15 = os.getloadavg()
    except OSError:
        return None
    return {"1m": load1, "5m": load5, "15m": load15}


def write_stuck_tasks_heartbeat(tasks, stuck_tasks, now):
    """Writes ONE canonical, real-state file every tick: current timestamp,
    real load average, whether resource_governor's EMERGENCY_STOP sentinel is
    set, current blocked/in_progress task counts, and the stuck-task list
    computed above. Lets any future check (this laptop, Cowork, anywhere
    else) read one file for real current state instead of running several
    separate SSH commands by hand. Read-only w.r.t. task state -- this
    function never mutates a task.yaml or triggers dispatch; it only reports.
    Atomic write (tmp + os.replace), same pattern as _atomic_save_yaml, so a
    concurrent reader never sees a half-written file."""
    doc = {
        "generated_at": now.isoformat(),
        "load_average": _real_load_average(),
        "emergency_stop": os.path.exists(resource_governor.EMERGENCY_STOP_PATH),
        "blocked_task_count": sum(1 for d in tasks.values() if d.get("status") == "blocked"),
        "running_task_count": sum(1 for d in tasks.values() if d.get("status") == "in_progress"),
        "stuck_task_threshold_minutes": STUCK_TASK_THRESHOLD_MINUTES,
        "stuck_tasks": stuck_tasks,
    }
    _atomic_save_json(STUCK_TASKS_HEARTBEAT_PATH, doc)
    return doc


# ---------------------------------------------------------------------------
# 2. gap_queue.yaml dispatch (was queue-dispatcher.py)
# ---------------------------------------------------------------------------

def sync_gap_queue_statuses(doc, tasks):
    changed = False
    for item in doc["queue"]:
        if item["status"] == "dispatched" and item.get("task_id"):
            s = (tasks.get(item["task_id"]) or {}).get("status")
            if s in TERMINAL_GOOD:
                item["status"] = "completed"
                changed = True
            elif s in TERMINAL_BAD:
                item["retry_count"] = item.get("retry_count", 0) + 1
                if item["retry_count"] >= MAX_RETRIES:
                    item["status"] = "stuck_needs_human"
                else:
                    item["status"] = "needs_retry"
                changed = True
            elif s in TERMINAL_HOLD:
                item["status"] = "awaiting_human_approval"
                changed = True
    return changed


def existing_scope_conflict(category, sub_category):
    """Best-effort duplication guard: check open PR titles and branch names
    for the same category/sub_category wording before dispatching. Unchanged
    from queue-dispatcher.py."""
    needle = sub_category.lower()[:20]
    r = run(["gh", "pr", "list", "--repo", f"FChecklist/{REPO}", "--state", "open",
             "--json", "title", "-q", ".[].title"])
    if needle in r.stdout.lower():
        return True
    r = run(["git", "-C", REPO_PATH, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"])
    slug = re.sub(r"[^a-z0-9]+", "-", sub_category.lower()).strip("-")[:20]
    if slug and slug in r.stdout.lower():
        return True
    return False


def build_gap_prompt(item):
    if item.get("full_prompt"):
        return item["full_prompt"]
    lines = [
        f"VERIDIAN Review Framework gap-closure: {item['category']} / {item['sub_category']}.",
        f"This covers {item['row_count']} related finding(s) from the framework evaluation. "
        "Close all of them in one coherent PR if they share the same module/area -- do not "
        "create a separate PR per finding if they're naturally one piece of work.",
        "",
        "Findings to address:",
    ]
    for f in item["findings"]:
        lines.append(f"- [{f['severity']}] {f['parameter']}")
        if f["gap_identified"]:
            lines.append(f"  Gap: {f['gap_identified']}")
        if f["recommended_approach"]:
            lines.append(f"  Recommended approach: {f['recommended_approach']}")
    lines += [
        "",
        "Before writing any code: read the actual current implementation of the "
        "relevant module(s) first -- do not assume the gap description is still "
        "accurate, the codebase has moved since this evaluation was written. If a "
        "finding turns out to already be resolved, or the described gap doesn't "
        "match what you find in the code, say so in PROGRESS.md rather than making "
        "an unnecessary change.",
        "Do not touch src/lib/services/permission-service.ts's shared "
        "ERP_ACTION_ROLES table structure or any other in-flight worker's declared "
        "scope -- if your area genuinely needs a new permission-service entry, add "
        "it additively (new keys only).",
        "Maintain PROGRESS.md with '## Completed' / '## Remaining' checklists as usual.",
    ]
    return "\n".join(lines)


def dispatch_gap_item(item):
    was_retry = item["status"] == "needs_retry"
    title = f"{item['category']}: {item['sub_category']}"[:80]
    if was_retry:
        title = f"[retry {item.get('retry_count', 0)}] {title}"[:80]
    prompt = build_gap_prompt(item)
    r = run([sys.executable, TASK_MANAGER, "create", "--repo", REPO, "--title", title, "--prompt", prompt])
    print(r.stdout)
    print(r.stderr, file=sys.stderr)
    m = re.search(r"^CREATED: (\S+)", r.stdout, re.MULTILINE)
    if m:
        item["task_id"] = m.group(1)
        item["status"] = "dispatched"
        return True
    item["status"] = "dispatch_failed"
    return False


def gap_queue_tick(tasks):
    """Owner directive 2026-07-20 (gap_queue.yaml's own pause_reason): while
    dispatch_paused is true, dispatch nothing for any non-completed item,
    held_task_ids included. This function preserves that gate EXACTLY as
    queue-dispatcher.py enforced it -- same single dispatch_paused check, same
    early return, no new per-item held_task_ids filtering added (the original
    never had one; consolidation is not the moment to add new gate logic to
    Owner-set pause state)."""
    if not os.path.isfile(GAP_QUEUE_PATH):
        print(f"No gap_queue.yaml at {GAP_QUEUE_PATH} -- skipping.")
        return {"dispatched": [], "paused": None}

    with _queue_lock(GAP_QUEUE_LOCK):
        with open(GAP_QUEUE_PATH) as f:
            doc = yaml.safe_load(f)

        if doc.get("dispatch_paused"):
            print(f"PAUSED: {doc.get('pause_reason', 'no reason recorded')}")
            print(f"Held task_ids: {len(doc.get('held_task_ids', []))} -- dispatching nothing this run.")
            return {"dispatched": [], "paused": True}

        changed = sync_gap_queue_statuses(doc, tasks)
        dispatched_ids = []

        candidates = [it for it in doc["queue"] if it["status"] in ("queued", "needs_retry")]
        for item in candidates:
            if existing_scope_conflict(item["category"], item["sub_category"]):
                print(f"SKIP (possible duplicate scope): {item['id']}")
                item["status"] = "skipped_possible_duplicate"
                changed = True
                continue
            with dispatch_core.acquire_dispatch_lock():
                if not dispatch_core.has_free_slot():
                    print(f"SKIP (cap reached): {item['id']}")
                    break
                print(f"Dispatching: {item['id']}")
                ok = dispatch_gap_item(item)
                changed = True
            if ok and item.get("task_id"):
                dispatched_ids.append(item["task_id"])
                dispatch_core.record_dispatch_event(
                    task_id=item["task_id"], dispatched_by="dispatch-tick.py:gap_queue",
                    source_queue_or_plan="gap_queue.yaml",
                    worker_unit=f"veridian-worker@{item['task_id']}.service")

        if changed:
            _atomic_save_yaml(GAP_QUEUE_PATH, doc)

        completed = sum(1 for it in doc["queue"] if it["status"] == "completed")
        total = len(doc["queue"])
        print(f"PROGRESS: {completed}/{total} groups completed")
        return {"dispatched": dispatched_ids, "paused": False}


# ---------------------------------------------------------------------------
# 3. module queue dispatch (was module-queue-dispatcher.py) -- SAME shared
#    concurrency pool as gap_queue_tick() above, not a separate cap.
# ---------------------------------------------------------------------------

def _load_render_task_prompt():
    from importlib.util import spec_from_file_location, module_from_spec
    _spec = spec_from_file_location("task_template", os.path.join(SCRIPTS, "task-template.py"))
    _mod = module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod.render_task_prompt


def load_all_module_queues():
    paths = sorted(globmod.glob(f"{MODULE_QUEUES_DIR}/*.yaml"))
    docs = {}
    for p in paths:
        with open(p) as f:
            docs[p] = yaml.safe_load(f) or {"module": os.path.basename(p).replace(".yaml", ""), "queue": []}
    return docs


def sync_module_statuses(docs, tasks):
    changed_paths = set()
    for path, doc in docs.items():
        for item in doc.get("queue", []):
            if item["status"] == "RUNNING" and item.get("task_id"):
                s = (tasks.get(item["task_id"]) or {}).get("status")
                if s in TERMINAL_GOOD:
                    item["status"] = "MERGED"
                    changed_paths.add(path)
                elif s in TERMINAL_BAD:
                    item["status"] = "REWORK"
                    changed_paths.add(path)
                elif s in TERMINAL_HOLD:
                    item["status"] = "REVIEW"
                    changed_paths.add(path)
    return changed_paths


def dependency_met(item, all_items_by_id):
    for dep_id in item.get("dependencies", []):
        dep = all_items_by_id.get(dep_id)
        if not dep or dep["status"] != "MERGED":
            return False
    return True


def dispatch_module_item(item, doc, render_task_prompt):
    module = doc["module"]
    title = f"[{module}] {item['id']}: {item['objective']}"[:80]
    prompt = render_task_prompt(item)
    r = run([sys.executable, TASK_MANAGER, "create", "--repo", REPO, "--title", title, "--prompt", prompt])
    print(r.stdout)
    print(r.stderr, file=sys.stderr)
    m = re.search(r"^CREATED: (\S+)", r.stdout, re.MULTILINE)
    if not m:
        item["status"] = "REWORK"
        item["dispatch_error"] = "veridian-task.py create failed -- see dispatcher log"
        return False
    task_id = m.group(1)
    item["task_id"] = task_id
    item["status"] = "RUNNING"
    task_dir = f"{TASKS_DIR}/{task_id}"
    os.makedirs(task_dir, exist_ok=True)
    with open(f"{task_dir}/module_scope.yaml", "w") as f:
        yaml.safe_dump({"module": module, "files_allowed": item.get("files_allowed", [])}, f)
    return True


def module_queue_tick(tasks):
    if not os.path.isdir(MODULE_QUEUES_DIR):
        print(f"No module queues dir at {MODULE_QUEUES_DIR} -- skipping.")
        return {"dispatched": [], "no_queues": True}

    with _queue_lock(MODULE_QUEUES_LOCK):
        docs = load_all_module_queues()
        if not docs:
            print("No module queue files found in", MODULE_QUEUES_DIR)
            return {"dispatched": [], "no_queues": True}

        render_task_prompt = _load_render_task_prompt()
        changed = sync_module_statuses(docs, tasks)

        all_items_by_id = {}
        for doc in docs.values():
            for item in doc.get("queue", []):
                all_items_by_id[item["id"]] = item

        # Round-robin across module queues so one module's queue can't starve
        # another's within a single tick: one eligible item per module per
        # round, cycling through modules, instead of draining one module's
        # whole queue before moving to the next. (module-queue-dispatcher.py,
        # this function's predecessor, had this exact same comment over
        # module-by-module-not-interleaved code -- a pre-existing bug, not
        # something this consolidation introduced. Fixed here.)
        per_module_candidates = {}
        for path, doc in docs.items():
            eligible = [item for item in doc.get("queue", [])
                        if item["status"] == "NEW" and dependency_met(item, all_items_by_id)]
            if eligible:
                per_module_candidates[path] = eligible

        candidates = []
        while per_module_candidates:
            for path in list(per_module_candidates.keys()):
                item = per_module_candidates[path].pop(0)
                candidates.append((path, docs[path], item))
                if not per_module_candidates[path]:
                    del per_module_candidates[path]

        dispatched_ids = []
        for path, doc, item in candidates:
            with dispatch_core.acquire_dispatch_lock():
                if not dispatch_core.has_free_slot():
                    print(f"SKIP (cap reached): {item['id']}")
                    break
                print(f"Dispatching: {item['id']} (module: {doc['module']})")
                ok = dispatch_module_item(item, doc, render_task_prompt)
                changed.add(path)
            if ok and item.get("task_id"):
                dispatched_ids.append(item["task_id"])
                dispatch_core.record_dispatch_event(
                    task_id=item["task_id"], dispatched_by="dispatch-tick.py:module_queue",
                    source_queue_or_plan=f"module_queue:{doc['module']}",
                    worker_unit=f"veridian-worker@{item['task_id']}.service")

        for path in changed:
            _atomic_save_yaml(path, docs[path])

        for path, doc in docs.items():
            counts = {}
            for item in doc.get("queue", []):
                counts[item["status"]] = counts.get(item["status"], 0) + 1
            print(f"{doc['module']}: {counts}")

        return {"dispatched": dispatched_ids}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.parse_args()

    tasks = dispatch_core.task_status_sync()
    now = datetime.datetime.now(datetime.timezone.utc)

    sweep_result = supervisor_sweep_tick(tasks)
    resume_result = resume_interrupted_workers_tick(tasks)
    gap_result = gap_queue_tick(tasks)
    module_result = module_queue_tick(tasks)

    stuck_tasks = find_stuck_tasks(tasks, now)
    heartbeat = write_stuck_tasks_heartbeat(tasks, stuck_tasks, now)
    if stuck_tasks:
        print(f"STUCK TASKS ({len(stuck_tasks)}, threshold={STUCK_TASK_THRESHOLD_MINUTES}min): "
              f"see {STUCK_TASKS_HEARTBEAT_PATH}")
        for item in stuck_tasks:
            print(f"  - {item['task_id']}: blocked {item['blocked_minutes']}min "
                  f"(last note: {item['last_note']!r})")

    dispatched_this_tick = (
        len(sweep_result.get("started", []))
        + len(resume_result.get("resumed", []))
        + len(gap_result.get("dispatched", []))
        + len(module_result.get("dispatched", []))
    )
    dispatch_core.record_tick(
        "dispatch-tick", status="ok", dispatched_this_tick=dispatched_this_tick,
        extra={
            "supervisor_sweep_started": sweep_result.get("started", []),
            "resumed_interrupted_workers": resume_result.get("resumed", []),
            "gap_queue_dispatched": gap_result.get("dispatched", []),
            "module_queue_dispatched": module_result.get("dispatched", []),
            "stuck_tasks_found": len(stuck_tasks),
        },
    )

    print(json.dumps({
        "supervisor_sweep": sweep_result,
        "resume_interrupted_workers": resume_result,
        "gap_queue": gap_result,
        "module_queue": module_result,
        "stuck_tasks_heartbeat": heartbeat,
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
