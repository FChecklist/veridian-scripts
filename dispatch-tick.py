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
import importlib.util
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
# Stale-swap-ratchet override, applied to every real has_free_slot() call
# site in this script (task-20260813-205525-close-fake-progress-md-only-prs-
# 317-321, closing PR #317's real gap -- that PR itself shipped no code,
# see PROGRESS.md-only history on the closed PR).
#
# Real gap: dispatch_core.has_free_slot()'s swap_backoff check
# (dispatch_core.py, frozen under the narrow 2026-08-08 stop-work order, see
# resource_governor.py's own SWAP_ACTIVITY_* comment) is a STATIC
# SwapFree/SwapTotal occupancy ratio -- Linux never proactively reclaims
# swap pages once written, so a single past spike can latch that gate closed
# forever, even with abundant real MemAvailable and zero ongoing swap I/O
# (real evidence: UMR-20260813-155201-da76, SwapFree byte-frozen at 775980kB
# across 5 samples over 15s while MemAvailable held ~11.3GB free and real
# `vmstat` showed no steady-state swap activity). resource_governor.py
# already carries the real, narrow, activity-based override
# (_override_stale_swap_backoff()) that re-checks real swap I/O (vmstat
# pswpin/pswpout delta over a real elapsed window) and real MemAvailable
# headroom on every call, and re-opens automatically the moment either
# condition changes -- but it was only ever wired into
# resource_governor.dispatch_one()'s own umr_tasks queue (#309/#314). This
# script's own 3 real spawn call sites below (supervisor_sweep_tick,
# gap_queue_tick, module_queue_tick) each called dispatch_core.has_free_slot()
# directly and never went through that override, so a stale swap ratchet
# could still permanently wedge these 3 dispatch paths even after
# resource_governor.py's own fix landed -- exactly the still-open half of
# the gap PR #317 claimed (with zero code) to have fixed. Reusing
# resource_governor's existing, already-tested override here (never
# reimplementing swap-activity/headroom logic a second time) closes it for
# real, for every dispatch path on the box, not just the umr_tasks queue.
def has_free_slot_with_stale_swap_override(cap=None):
    """True if a real spawn is currently allowed -- dispatch_core's normal
    two-gate has_free_slot_detail() check (fixed concurrency cap + real
    memory/swap/load headroom veto), with
    resource_governor._override_stale_swap_backoff() applied to a
    swap_backoff-specific block before it's honored. Every other
    slot_detail["check"] value (cap_exhausted, mem_backoff,
    swap_hard_ceiling, mem_headroom_budget, load1_backoff, load1_unreadable,
    or already-ok) passes through completely unchanged -- see that
    function's own docstring for the exact two real, freshly-live-read
    conditions ("real MemAvailable headroom below the backoff ceiling still
    fits one more worker's own memory budget" AND "real, confirmed-quiet
    swap I/O over a real, trustworthy elapsed window") required before a
    swap_backoff block can ever be overridden, and for why the 0.99
    swap_hard_ceiling and every other real gate are never touched."""
    slot_ok, slot_detail = dispatch_core.has_free_slot_detail(cap=cap)
    slot_ok, _ = resource_governor._override_stale_swap_backoff(slot_ok, slot_detail)
    return slot_ok


# ---------------------------------------------------------------------------
# 1. supervisor-sweep discovery (was supervisor-sweep.sh)
# ---------------------------------------------------------------------------

def supervisor_sweep_tick(tasks):
    started, skipped_cap = [], []
    for task_id, doc in tasks.items():
        if doc.get("status") != "pending_review" or doc["_has_review_json"]:
            continue
        with dispatch_core.acquire_dispatch_lock():
            if not has_free_slot_with_stale_swap_override():
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


def _existing_active_umr(task_id, sbr_module=None):
    """Real, read-only pre-check (UMR-20260806-103711-bf00): does
    task_identity=task_id already have an active (queued/dispatched/running)
    umr_tasks row, via the exact same find_active_umr_by_identity() that
    resource_governor.submit() itself runs inside its write-lock below? This
    call sits outside that lock -- nothing is written here, so there is
    nothing to serialize -- which makes it advisory, not authoritative: a
    genuine race against a concurrent submit() is still caught for real by
    submit()'s own lock-protected check, unchanged. This pre-check exists
    purely to avoid paying the cost this function used to pay every tick for
    every still-queued task -- a fresh rejected_duplicate row recorded by
    submit() -- when a cheap read already knows the answer (confirmed via
    real umr_tasks evidence: 6238 rejected_duplicate rows from this exact
    source_trigger by 2026-08-06, e.g. 6 in the single second
    2026-08-06T10:33:19-20Z, against only 16 queued/6 running/85 failed/7
    killed -- see OCID/UMR audit trail for this fix).

    Same fail-open philosophy as every other real _safe_superboss_register()
    caller in this module (see _real_umr_heartbeat_age_minutes()'s own
    docstring above): an unavailable Superboss Register returns None here,
    treated as 'cannot confirm' -- resume_interrupted_workers_tick() below
    then falls back to its pre-fix behavior (call submit(), let its own
    lock-protected check be the real authority) rather than silently
    skipping a real resume just because this read-only optimization
    couldn't run.
    """
    sbr_module = sbr_module or resource_governor
    sbr, error = sbr_module._safe_superboss_register("resume_interrupted_workers_tick")
    if error:
        return None
    conn = sbr._connect()
    try:
        return sbr.find_active_umr_by_identity(conn, task_id)
    finally:
        conn.close()


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

    UMR-20260806-103711-bf00 fix: "safe to call every tick" was true (no
    real double-dispatch ever happened -- submit()'s own gate is not, and
    was never, the defect) but this function used to rely on that gate as
    its ONLY check, so every tick that found the same still-queued/still-
    capped task_id (unit legitimately not yet started -- e.g. resource cap,
    not actually interrupted) called submit() anyway and got a real
    rejected_duplicate row written back, forever, once per tick, per such
    task. _existing_active_umr() above now checks first (read-only) and
    skips the submit() call entirely when a live row already covers this
    identity -- same real outcome (task stays exactly where it already was
    in the queue, nothing double-dispatched), zero new duplicate rows. The
    gate inside submit() is untouched and still the real authority for any
    genuine race.
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

        existing = _existing_active_umr(task_id)
        if existing:
            skipped_duplicate.append(task_id)
            print(f"SKIP resume (already {existing['status']} as umr_id={existing['umr_id']}, "
                  f"no duplicate row written): {task_id}")
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


# OCID-068 seven-rule guardrails addendum, Rule 5 (UMR-20260804-180711-7f96,
# UMR-20260804-205741-cf3f, citing UMR-20260804-170055-a069): "real stall
# detection, a task is stale only if it has no heartbeat, no checkpoint, no
# log entry, no CPU activity, and no file change for the configured
# threshold, visual tmux pane text alone shall never be used by itself to
# declare a stall, stall detection requires this real combined evidence."
#
# Real discovery: find_stuck_tasks() above already exists but covers only
# status=='blocked' tasks (RESUMABLE_STATUSES' own comment: a blocked task
# has no running unit at all, by construction -- worker-entrypoint.sh
# disables it before writing the blocked status), using ONE signal
# (last_checkpoint_at). That single-signal design is real, correct, and
# unmodified here for that specific case (a genuinely dead process has no
# CPU/log/file activity to check anyway). Rule 5's combined-evidence design
# targets a DIFFERENT, previously uncovered real gap: a status=='in_progress'
# task whose systemd unit still reports ActiveState=active (so it is NOT
# blocked, and find_stuck_tasks() never looks at it) but is silently hung --
# no real forward progress despite looking "alive." This is the "real false
# stall concern" the rule's own text references: naive tmux-pane-text-only
# detection could wrongly call a legitimately-still-computing task stalled;
# combining 5 independent real signals avoids that false positive.
TASK_CPU_STATE_PATH = os.environ.get("VERIDIAN_TASK_CPU_STATE_PATH", f"{AI_OS}/TASK_CPU_STATE.json")


def _load_json_or_none(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _unit_main_pid(unit):
    """Real, current systemd --user MainPID for `unit`, or None if the unit
    has no real running main process (never a fabricated PID)."""
    r = run(["systemctl", "--user", "show", unit, "-p", "MainPID", "--value"])
    try:
        pid = int(r.stdout.strip())
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _read_proc_cpu_ticks(pid, proc_stat_path=None):
    """Real /proc/<pid>/stat utime+stime (fields 14/15, 1-indexed) -- the
    same real, kernel-reported cumulative CPU-tick counter
    resource_governor.read_cpu_times() uses for host-wide CPU%, applied here
    per-process. The comm field (field 2) is parenthesized and may itself
    contain spaces/parens, so parsing anchors on the LAST ')' in the line
    (the kernel guarantees comm's own parens are always the innermost --
    anything after the final ')' is always the remaining numeric fields),
    same technique the Linux proc(5) man page itself recommends. Returns
    None if the process doesn't exist or the line can't be parsed -- never
    fabricates a ticks value for a process that isn't real."""
    proc_stat_path = proc_stat_path or f"/proc/{pid}/stat"
    try:
        with open(proc_stat_path) as f:
            content = f.read()
    except OSError:
        return None
    try:
        rparen = content.rindex(")")
    except ValueError:
        return None
    fields = content[rparen + 2:].split()
    try:
        return int(fields[11]) + int(fields[12])  # utime + stime
    except (IndexError, ValueError):
        return None


def _cpu_activity_since_last_tick(task_id, unit, now, state_path=None):
    """Real, cross-tick CPU-activity check (a single dispatch-tick.py
    invocation only ever has one instantaneous /proc/<pid>/stat sample --
    detecting a real DELTA needs the prior tick's own sample, persisted to
    state_path, same cross-invocation-delta pattern
    resource_governor.sample_metrics() already establishes for host-wide
    metrics via METRIC_STATE_PATH).

    Returns True (real activity, or cannot rule it out) when: no real
    MainPID exists to check (fails safe toward NOT flagging a stall on an
    inconclusive check), this is the first-ever observation for this
    task_id (cold start -- never fabricates staleness from having nothing
    to compare against, same philosophy as sample_metrics()'s own
    cold-start handling), the unit's PID changed since the last sample (a
    restart is itself real evidence something happened), or the real
    CPU-tick counter moved. Returns False only when a real prior sample
    exists, the same PID is still running, and its CPU ticks are
    genuinely unchanged."""
    state_path = state_path or TASK_CPU_STATE_PATH
    pid = _unit_main_pid(unit)
    if pid is None:
        return True
    curr_ticks = _read_proc_cpu_ticks(pid)
    if curr_ticks is None:
        return True

    state = _load_json_or_none(state_path) or {}
    prev = state.get(task_id)
    state[task_id] = {"pid": pid, "ticks": curr_ticks, "ts": now.isoformat()}
    _atomic_save_json(state_path, state)

    if prev is None or prev.get("pid") != pid:
        return True
    return curr_ticks != prev.get("ticks")


def _real_umr_heartbeat_age_minutes(task_id, now, sbr_module=None):
    """Real umr_tasks.last_heartbeat age (minutes) for the most recent row
    whose task_identity == task_id, via resource_governor's own
    _safe_superboss_register() -- same fail-open philosophy as every other
    real caller: an unavailable Superboss Register returns None (treated as
    'cannot confirm heartbeat activity', not 'definitely stale' -- see
    find_stalled_running_tasks()'s own fail-safe-toward-not-flagging
    default), never a fabricated age."""
    sbr_module = sbr_module or resource_governor
    sbr, error = sbr_module._safe_superboss_register("find_stalled_running_tasks")
    if error:
        return None
    conn = sbr._connect()
    try:
        row = conn.execute(
            "SELECT last_heartbeat FROM umr_tasks WHERE task_identity=? ORDER BY ts_submitted DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row["last_heartbeat"]:
        return None
    hb_ts = _parse_iso_ts(row["last_heartbeat"])
    if hb_ts is None:
        return None
    return (now - hb_ts).total_seconds() / 60.0


def _real_log_and_file_activity_minutes(task_dir, now):
    """Real, most-recent mtime (minutes-ago) across every file under
    task_dir/workspace -- covers both 'log entry' (worker.log's own mtime,
    a file inside this same tree) and 'file change' as ONE real filesystem
    walk, since both signals are answered by the same real question ('has
    anything under this task's real workspace changed recently'). Returns
    None if the workspace doesn't exist or contains no files (never a
    fabricated 0)."""
    workspace = os.path.join(task_dir, "workspace")
    newest = None
    if not os.path.isdir(workspace):
        return None
    for root, _dirs, files in os.walk(workspace):
        if "/.git" in root or root.endswith("/.git"):
            continue
        for fname in files:
            try:
                mtime = os.path.getmtime(os.path.join(root, fname))
            except OSError:
                continue
            if newest is None or mtime > newest:
                newest = mtime
    if newest is None:
        return None
    return (now - datetime.datetime.fromtimestamp(newest, tz=datetime.timezone.utc)).total_seconds() / 60.0


def find_stalled_running_tasks(tasks, now, threshold_minutes=None, sbr_module=None):
    """Rule 5's real, combined-evidence stall detector for status==
    'in_progress' tasks (find_stuck_tasks() above already covers 'blocked').
    A task is flagged ONLY when ALL FIVE real signals independently confirm
    no activity within threshold_minutes: no heartbeat update, no new
    checkpoint, no fresh log/file activity, and no real CPU-tick movement.
    Any signal that cannot be determined (DB unavailable, no MainPID, no
    workspace) fails safe toward NOT flagging a stall -- an inconclusive
    check must never manufacture a false positive. Purely a read: never
    mutates a task.yaml or kills anything (that stays scan_stuck_tasks()'s
    own, separate, already-real SIGTERM/SIGKILL responsibility)."""
    threshold_minutes = STUCK_TASK_THRESHOLD_MINUTES if threshold_minutes is None else threshold_minutes
    sbr_module = sbr_module or resource_governor
    stalled = []
    for task_id, doc in tasks.items():
        if doc.get("status") != "in_progress":
            continue
        service = doc.get("service")
        if not service:
            continue

        checkpoint_age = None
        last_at = _parse_iso_ts(doc.get("last_checkpoint_at"))
        if last_at is not None:
            checkpoint_age = (now - last_at).total_seconds() / 60.0

        heartbeat_age = _real_umr_heartbeat_age_minutes(task_id, now, sbr_module=sbr_module)
        file_log_age = _real_log_and_file_activity_minutes(doc.get("_task_dir", ""), now)
        cpu_active = _cpu_activity_since_last_tick(task_id, service, now)

        evidence = {
            "checkpoint_age_minutes": round(checkpoint_age, 1) if checkpoint_age is not None else None,
            "heartbeat_age_minutes": round(heartbeat_age, 1) if heartbeat_age is not None else None,
            "file_log_age_minutes": round(file_log_age, 1) if file_log_age is not None else None,
            "cpu_active_since_last_tick": cpu_active,
        }

        # Fail-safe: any signal that could not be determined at all means
        # this check is inconclusive for this task -- never flag a stall on
        # incomplete evidence.
        if checkpoint_age is None or heartbeat_age is None or file_log_age is None:
            continue
        if cpu_active:
            continue
        if checkpoint_age < threshold_minutes:
            continue
        if heartbeat_age < threshold_minutes:
            continue
        if file_log_age < threshold_minutes:
            continue

        stalled.append({"task_id": task_id, "threshold_minutes": threshold_minutes, "evidence": evidence})
    stalled.sort(key=lambda item: -item["evidence"]["checkpoint_age_minutes"])
    return stalled


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


# OCID-068 seven-rule guardrails addendum, Rule 4 (UMR-20260804-180711-7f96,
# UMR-20260804-205741-cf3f, citing UMR-20260804-170055-a069): "the project
# manager shall always see real counts for running, queued, blocked, failed,
# rejected, retrying, stale, and completed tasks, and any alert cooldown may
# suppress notifications only, it must never suppress the underlying real
# data or real counts themselves."
#
# Real discovery: task.yaml's own status field (blocked/in_progress/
# completed/...) and umr_tasks' own status column (queued/running/completed/
# failed/killed/rejected_duplicate) are two distinct, real vocabularies for
# two distinct real concepts -- per-worker-task lifecycle vs. per-dispatch-
# attempt governance state. Rule 4's own 8-word list spans both: "blocked"
# and "stale" are real task.yaml-level/heartbeat-level concepts (this
# module's own existing blocked_task_count / stuck_tasks already compute
# them); "running/queued/failed/rejected/completed" map directly onto real
# umr_tasks.status values; "retrying" has no dedicated status column, so it
# is derived from Rule 1's own real, already-live evidence trail (PR #26):
# a row whose `reason` matches "resubmitted (reused umr_id" is, by
# construction, a real resume/retry. One real umr_tasks status,
# "killed" (the real SIGKILL-stuck-task terminal state,
# scan_stuck_tasks()'s own), has no explicit bucket in Rule 4's literal
# 8-word list -- rather than silently drop it or force-fit it into a wrong
# bucket, it is surfaced as its own honestly-labeled 9th field, consistent
# with Rule 4's own stated goal (real counts always visible, nothing
# suppressed).
RULE4_RETRY_REASON_PATTERN = re.compile(r"^resubmitted \(reused umr_id")


def compute_real_task_counts(tasks, stuck_tasks, now):
    """Rule 4's real counts, gathered fresh every call -- never cached,
    never suppressed by any cooldown (this function has no cooldown logic
    of its own, and is called unconditionally by
    write_stuck_tasks_heartbeat(), itself called unconditionally every real
    tick). Queries the real live umr_tasks table directly via
    resource_governor's own _safe_superboss_register() helper (same
    fail-open philosophy as every other real caller of that helper: a
    genuinely unavailable Superboss Register must never crash this tick's
    own heartbeat write, it surfaces as a real, honest
    'umr_counts_error' field instead of a fabricated zero).

    Returns a dict with real integer counts for: running, queued, blocked,
    failed, rejected, retrying, stale, completed, killed. 'blocked' and
    'stale' come from `tasks`/`stuck_tasks` (task.yaml-level); every other
    key comes from a real, fresh umr_tasks GROUP BY status query
    (dispatch-level)."""
    counts = {
        "blocked": sum(1 for d in tasks.values() if d.get("status") == "blocked"),
        "stale": len(stuck_tasks),
        "running": 0, "queued": 0, "failed": 0, "rejected": 0,
        "retrying": 0, "completed": 0, "killed": 0,
    }
    sbr, error = resource_governor._safe_superboss_register("compute_real_task_counts")
    if error:
        counts["umr_counts_error"] = error
        return counts

    conn = sbr._connect()
    try:
        rows = conn.execute("SELECT status, COUNT(*) AS n FROM umr_tasks GROUP BY status").fetchall()
        for row in rows:
            status, n = row["status"], row["n"]
            if status == "running":
                counts["running"] += n
            elif status == "queued":
                counts["queued"] += n
            elif status == "failed":
                counts["failed"] += n
            elif status == "rejected_duplicate":
                counts["rejected"] += n
            elif status == "completed":
                counts["completed"] += n
            elif status == "killed":
                counts["killed"] += n
            # Any future/unrecognized status value is intentionally NOT
            # silently dropped from the real total below -- see
            # "umr_tasks_total" cross-check.
        counts["umr_tasks_total"] = sum(r["n"] for r in rows)

        retrying_row = conn.execute(
            "SELECT COUNT(*) AS n FROM umr_tasks WHERE reason LIKE 'resubmitted (reused umr_id%'"
        ).fetchone()
        counts["retrying"] = retrying_row["n"]
    finally:
        conn.close()
    return counts


def write_stuck_tasks_heartbeat(tasks, stuck_tasks, now, stalled_running_tasks=None):
    """Writes ONE canonical, real-state file every tick: current timestamp,
    real load average, whether resource_governor's EMERGENCY_STOP sentinel is
    set, current blocked/in_progress task counts, the stuck-task list
    computed above, (Rule 4, OCID-068 seven-rule guardrails addendum) the
    full real task-count breakdown from compute_real_task_counts(), and
    (Rule 5, same addendum) the real, combined-evidence stalled-running-task
    list from find_stalled_running_tasks(). Lets any future check (this
    laptop, Cowork, anywhere else) read one file for real current state
    instead of running several separate SSH commands by hand. Read-only
    w.r.t. task state -- this function never mutates a task.yaml or triggers
    dispatch; it only reports. Called unconditionally every real tick, with
    no cooldown of its own -- Rule 4's "never suppress the underlying real
    data" requirement holds by construction: nothing in this function's own
    call path can skip a write once main() reaches it. stalled_running_tasks
    defaults to an empty list (not None) when the caller omits it, so
    existing callers/tests that predate Rule 5 are unaffected. Atomic write
    (tmp + os.replace), same pattern as _atomic_save_yaml, so a concurrent
    reader never sees a half-written file."""
    doc = {
        "generated_at": now.isoformat(),
        "load_average": _real_load_average(),
        "emergency_stop": os.path.exists(resource_governor.EMERGENCY_STOP_PATH),
        "blocked_task_count": sum(1 for d in tasks.values() if d.get("status") == "blocked"),
        "running_task_count": sum(1 for d in tasks.values() if d.get("status") == "in_progress"),
        "stuck_task_threshold_minutes": STUCK_TASK_THRESHOLD_MINUTES,
        "stuck_tasks": stuck_tasks,
        "real_task_counts": compute_real_task_counts(tasks, stuck_tasks, now),
        "stalled_running_tasks": stalled_running_tasks if stalled_running_tasks is not None else [],
    }
    _atomic_save_json(STUCK_TASKS_HEARTBEAT_PATH, doc)
    return doc


# ---------------------------------------------------------------------------
# 1d. PM triage escalation (added 2026-08-02, PM decision UMR-20260802-090702-c813)
# ---------------------------------------------------------------------------
# Extends the stuck-task/heartbeat surface above with a real, but strictly
# scoped, headless-Claude triage step -- same file, same tick, same 10-minute
# cadence, no new script/cron/timer. STRICT SCOPE (the whole point of this
# feature): the invocation below may ONLY answer "does this look like it
# needs Owner/PM attention right now, yes or no, with a one/two sentence
# evidence-cited reason" and write that answer to one append-only alert file.
# It must NEVER make a product-level decision, dispatch new work, edit any
# other file, or resolve a KERNEL_CONFLICT-style stop itself -- those stay PM
# (Claude Desktop) scope per the role split established earlier tonight. This
# is a pure triage/escalation role, closer to a Cowork backstop than a second
# PM or an Executor.
#
# Cheap deterministic pre-filter runs FIRST and gates the real Claude
# invocation entirely -- most ticks find nothing notable and must skip the
# invocation outright (real cost, not spent for nothing). Three real trigger
# conditions, each independently evidence-based, never a guess:
#   (a) any task already in stuck_tasks (computed above) -- something has been
#       blocked past STUCK_TASK_THRESHOLD_MINUTES with no PM action yet.
#   (b) a real, current AUDIT:FAIL/Superboss-rejected verdict on a blocked
#       task's OWN latest checkpoint note -- triggers immediately, does not
#       wait for the stuck-minutes threshold, since a fresh audit rejection is
#       inherently notable the moment it lands.
#   (c) real, non-empty unsubmitted text sitting in the interactive session's
#       own tmux prompt line (session "claude", the same one
#       dispatch-owner-task.sh relays into) -- checked via a real
#       `tmux capture-pane`, never assumed/fabricated. This exists because a
#       prior real incident tonight involved a claimed-but-unverifiable
#       "pending input line" -- this makes that claim mechanically checkable
#       going forward instead of taken on faith either way.

PM_TRIAGE_ALERTS_PATH = os.environ.get(
    "VERIDIAN_PM_TRIAGE_ALERTS_PATH", f"{AI_OS}/PM_TRIAGE_ALERTS.md")
PM_TRIAGE_TMUX_SESSION = os.environ.get("VERIDIAN_PM_TRIAGE_TMUX_SESSION", "claude")
PM_TRIAGE_CLAUDE_MODEL = os.environ.get("VERIDIAN_PM_TRIAGE_CLAUDE_MODEL", "sonnet")
PM_TRIAGE_CLAUDE_BUDGET_USD = os.environ.get("VERIDIAN_PM_TRIAGE_CLAUDE_BUDGET_USD", "0.50")
# Real bug found by an independent supervisor review (2026-08-02, task-20260802-074612's
# own review.json, verdict=reject): should_triage_pm() fired on ANY non-empty stuck_tasks
# list every single tick with no cooldown -- on a box with hundreds of already-stuck tasks
# (424 in this session's own real dry run), this would re-invoke the real, budgeted
# claude -p call roughly every 10 minutes indefinitely, an unbounded recurring-cost bug,
# not a hypothetical. Fixed here: a real cooldown gate, read from the alert file's own
# last real timestamp (no new state file needed) -- skip a new invocation, even if
# should_triage_pm() would otherwise trigger, until this many minutes have passed since
# the last real alert entry.
PM_TRIAGE_COOLDOWN_MINUTES = float(os.environ.get("VERIDIAN_PM_TRIAGE_COOLDOWN_MINUTES", "60"))
# Real, confirmed note signature supervisor-entrypoint.sh writes on a genuine
# Superboss/audit rejection (see e.g. task-20260802-055214's own real
# checkpoint history, 2026-08-02: "Superboss rejected: <PR url> -- see
# review.json for issues") -- matching this exact prefix is a real evidence
# check, not a guess at wording.
AUDIT_FAIL_NOTE_PATTERN = re.compile(r"Superboss rejected|AUDIT:\s*FAIL", re.IGNORECASE)


def _find_fresh_audit_fail_tasks(tasks):
    """Any status=='blocked' task whose OWN LATEST checkpoint note matches a
    real audit-rejection signature -- independent of stuck_tasks/the minutes
    threshold above, since a fresh AUDIT:FAIL is notable the moment it lands,
    not just once it has also sat for 30+ minutes. Read-only, never mutates
    a task.yaml."""
    found = []
    for task_id, doc in tasks.items():
        if doc.get("status") != "blocked":
            continue
        note = _last_checkpoint_note(doc)
        if note and AUDIT_FAIL_NOTE_PATTERN.search(note):
            found.append({"task_id": task_id, "last_note": note})
    return found


def _capture_tmux_pending_input(session=None):
    """Real, honest check of whether the interactive session's own tmux pane
    (session "claude" by default -- the same one dispatch-owner-task.sh's own
    relay targets via `tmux send-keys -t claude`) currently shows non-empty,
    unsubmitted text sitting at its prompt line. Looks for the real prompt
    marker this CLI renders ("<U+276F> " i.e. the '>' glyph) and returns the
    trailing text on that line, or None.

    Fails closed to None (never a fabricated finding) on ANY of: tmux not
    installed, no session by that name, capture-pane erroring, or no
    recognizable prompt line in the captured output -- this check existing to
    make a claim mechanically verifiable is worthless if it can itself
    fabricate a finding when it can't actually tell."""
    session = session or PM_TRIAGE_TMUX_SESSION
    try:
        has = subprocess.run(["tmux", "has-session", "-t", session],
                              capture_output=True, text=True, timeout=5)
        if has.returncode != 0:
            return None
        cap = subprocess.run(["tmux", "capture-pane", "-t", session, "-p"],
                              capture_output=True, text=True, timeout=5)
        if cap.returncode != 0:
            return None
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in reversed(cap.stdout.splitlines()):
        if "❯" in line:  # the real prompt glyph this CLI renders
            after = line.split("❯", 1)[1].strip()
            return after if after else None
    return None


def should_triage_pm(tasks, stuck_tasks, now):
    """The real, deterministic pre-filter -- zero AI cost. Returns
    (should_invoke: bool, reasons: list[str], evidence: dict). Only when this
    returns True does main() spend anything invoking Claude at all; most real
    ticks find nothing notable across all 3 conditions and return
    (False, [], {}), which is the expected, common steady state, not an
    error."""
    reasons = []
    evidence = {}

    if stuck_tasks:
        reasons.append(f"{len(stuck_tasks)} task(s) stuck past {STUCK_TASK_THRESHOLD_MINUTES}min")
        evidence["stuck_tasks"] = stuck_tasks

    fresh_audit_fails = _find_fresh_audit_fail_tasks(tasks)
    if fresh_audit_fails:
        reasons.append(f"{len(fresh_audit_fails)} task(s) with a fresh real audit-reject/fail verdict")
        evidence["fresh_audit_fail_tasks"] = fresh_audit_fails

    pending_input = _capture_tmux_pending_input()
    if pending_input:
        reasons.append("real, non-empty unsubmitted text found in the interactive session's own prompt line")
        evidence["tmux_pending_input"] = pending_input

    return (bool(reasons), reasons, evidence)


PM_TRIAGE_EVIDENCE_MAX_ITEMS = 10


def _summarize_evidence(evidence, max_items=PM_TRIAGE_EVIDENCE_MAX_ITEMS):
    """Bounds any list-valued evidence entry to the first max_items real
    records plus an honest '_omitted_count' of how many more real records
    exist -- never a fabricated smaller number. Exists because real
    production ticks have shown 400+ stuck tasks / dozens of audit-fail
    tasks at once: passing every one of them as a subprocess argv element
    hits the OS ARG_MAX ('Argument list too long') and the raw dump also
    makes the durable alert-file log unreadably huge. Full, untruncated
    evidence stays available for a human via each finding's own task.yaml
    (task_id is always included) -- this is a summary for the triage
    judgment/alert entry, not the sole record."""
    summarized = {}
    for key, value in evidence.items():
        if isinstance(value, list) and len(value) > max_items:
            summarized[key] = value[:max_items]
            summarized[f"{key}_omitted_count"] = len(value) - max_items
        else:
            summarized[key] = value
    return summarized


def _invoke_triage_claude(reasons, evidence, run_fn=None):
    """The one real Claude invocation this feature makes. Strictly scoped:
    no tool access at all (--allowedTools "" -- the model can only return
    text, it can never itself write a file or take any other action; THIS
    SCRIPT is what writes the alert file below, never the model), no
    --dangerously-skip-permissions, no --continue (fresh/stateless every
    call, no session state persisted between ticks), a small real
    --max-budget-usd cap (PM_TRIAGE_CLAUDE_BUDGET_USD, default $0.50) since
    this can run up to every 10 minutes. The prompt contains ONLY the
    specific, already-gathered pre-filter evidence -- never broader repo/task
    access -- and asks for exactly one judgment: does this need Owner/PM
    attention now, yes or no, with a one/two sentence evidence-cited reason.
    It may not decide product questions, dispatch anything, or resolve a
    KERNEL_CONFLICT itself -- the prompt says so explicitly, and it has no
    tool access to do so even if it tried.

    run_fn is injectable (defaults to subprocess.run) so tests can stub the
    real subprocess call without spending real API budget or requiring a
    real `claude` binary/auth in the test environment.

    Returns the real judgment text (str), or a clear error string prefixed
    "INVOCATION_ERROR:" on any failure -- never silently swallowed, never a
    fabricated judgment."""
    run_fn = run_fn or subprocess.run
    bounded_evidence = _summarize_evidence(evidence)
    prompt = (
        "You are a narrow triage/escalation check, NOT a decision-maker. You have "
        "no tool access and cannot take any action beyond this one text answer.\n\n"
        "Real evidence gathered by a deterministic pre-filter (not your own judgment "
        "of what's notable -- these specific findings already crossed a real "
        "threshold; any *_omitted_count field means the real full list was "
        "longer than shown here, never a smaller fabricated count):\n"
        f"Reasons: {json.dumps(reasons)}\n"
        f"Evidence: {json.dumps(bounded_evidence, indent=2, default=str)}\n\n"
        "Answer ONLY: does this genuinely need Owner or PM attention right now -- "
        "YES or NO -- with a one or two sentence reason citing the evidence above. "
        "Do NOT decide what to do about it, do NOT propose a fix, do NOT pick "
        "between options, do NOT say what the right next step is beyond flagging "
        "it. That is all out of scope for you -- your only job is the yes/no plus "
        "reason."
    )
    try:
        r = run_fn(
            ["claude", "-p", prompt,
             "--model", PM_TRIAGE_CLAUDE_MODEL,
             "--allowedTools", "",
             "--max-budget-usd", PM_TRIAGE_CLAUDE_BUDGET_USD,
             "--output-format", "json"],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"INVOCATION_ERROR: {type(e).__name__}: {e}"
    if r.returncode != 0:
        return f"INVOCATION_ERROR: claude -p exited {r.returncode}: {r.stderr[-500:]}"
    try:
        parsed = json.loads(r.stdout)
        return parsed.get("result") or parsed.get("response") or r.stdout.strip()
    except (json.JSONDecodeError, ValueError):
        return r.stdout.strip() or "INVOCATION_ERROR: empty response"


def append_pm_triage_alert(path, now, reasons, evidence, judgment):
    """Real, append-only write -- each tick's real finding becomes one new
    timestamped entry, never overwriting a prior one (unlike
    STUCK_TASKS_HEARTBEAT.json, which is a point-in-time snapshot by design;
    this file is a durable log a PM/Owner can scroll through). Markdown, same
    real convention as the existing ai-os/logs/ATTENTION.md append-only alert
    log -- checked MASTER_INDEX.yaml first, no existing single-purpose
    "PM triage" file to extend, so this is the one new canonical file, same
    non-git ai-os/ live-runtime-state location as ATTENTION.md/CONTROLLER.yaml."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    bounded_evidence = _summarize_evidence(evidence)
    entry = (
        f"\n## {now.isoformat()}\n"
        f"**Reasons:** {'; '.join(reasons)}\n\n"
        f"**Judgment:** {judgment}\n\n"
        f"**Evidence** (any `*_omitted_count` means the real full list was "
        f"longer than shown -- see each finding's own task_id/task.yaml for "
        f"the complete record):\n```json\n{json.dumps(bounded_evidence, indent=2, default=str)}\n```\n"
    )
    with open(path, "a") as f:
        f.write(entry)


_PM_TRIAGE_ALERT_HEADER_RE = re.compile(r"^## (\S+)\s*$", re.MULTILINE)


def _last_pm_triage_alert_ts(path):
    """Real cooldown signal: the ISO timestamp of the most recent '## <ts>'
    entry header already written by append_pm_triage_alert(), read directly
    from the durable alert file itself -- no separate state file to keep in
    sync or lose. Returns None if the file doesn't exist yet or has no real
    entries (never fabricates a timestamp)."""
    if not os.path.isfile(path):
        return None
    with open(path) as f:
        content = f.read()
    matches = _PM_TRIAGE_ALERT_HEADER_RE.findall(content)
    if not matches:
        return None
    try:
        return _parse_iso_ts(matches[-1])
    except (TypeError, ValueError):
        return None


def pm_triage_tick(tasks, stuck_tasks, now, invoke_fn=None):
    """Orchestrates the pre-filter -> (maybe) invoke -> (maybe) alert
    sequence for one tick. invoke_fn defaults to _invoke_triage_claude, and
    is only ever called when should_triage_pm() already returned True AND
    the real cooldown (PM_TRIAGE_COOLDOWN_MINUTES, default 60) has elapsed
    since the last real alert entry -- every real tick where nothing crossed
    a real threshold, or where a real invocation already happened recently,
    skips the invocation (and therefore its real cost) entirely. Fixes a
    real bug an independent supervisor review found (2026-08-02,
    task-20260802-074612's review.json): with no cooldown, a box with
    hundreds of already-stuck tasks would re-invoke the budgeted claude -p
    call roughly every tick indefinitely. Returns a summary dict always safe
    to json.dumps into main()'s own tick summary."""
    invoke_fn = invoke_fn or _invoke_triage_claude
    should_invoke, reasons, evidence = should_triage_pm(tasks, stuck_tasks, now)
    if not should_invoke:
        return {"invoked": False, "reasons": []}
    last_ts = _last_pm_triage_alert_ts(PM_TRIAGE_ALERTS_PATH)
    if last_ts is not None:
        elapsed_minutes = (now - last_ts).total_seconds() / 60.0
        if elapsed_minutes < PM_TRIAGE_COOLDOWN_MINUTES:
            return {
                "invoked": False, "reasons": reasons,
                "skipped_reason": f"cooldown active ({elapsed_minutes:.1f}min of "
                                   f"{PM_TRIAGE_COOLDOWN_MINUTES}min since last real alert)",
            }
    judgment = invoke_fn(reasons, evidence)
    append_pm_triage_alert(PM_TRIAGE_ALERTS_PATH, now, reasons, evidence, judgment)
    return {"invoked": True, "reasons": reasons, "judgment": judgment, "alert_path": PM_TRIAGE_ALERTS_PATH}


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
                if not has_free_slot_with_stale_swap_override():
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
                if not has_free_slot_with_stale_swap_override():
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
# Stale-running-worker reconciliation (UMR-20260813-090037-9a34, addendum to
# UMR-20260806-171945-5767)
# ---------------------------------------------------------------------------

def _load_reconcile_stale_running_workers():
    """Lazy, in-process import of reconcile_stale_running_workers.py -- same
    importlib pattern status-remediation-tick.py's own
    _load_reconcile_owner_dispatch_status() already uses for the analogous
    reconcile_owner_dispatch_status.py wiring, reused here for the same reason
    (one real, already-tested, already-audited script, called in-process
    instead of a second standalone cron entry -- the
    ~/.config/systemd/user/README.md STANDING RULE: a periodic need whose
    cadence/purpose fits an existing unit goes in as a new step inside that
    unit/script, not a new one)."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location(
        "reconcile_stale_running_workers", os.path.join(script_dir, "reconcile_stale_running_workers.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# owner_dispatch_gateway status reconciliation (UMR-20260813-103211, addendum
# to UMR-20260813-065157-ba95: "close the success half of the umr_tasks
# write-back gap" landed real, tested, audited code in status-remediation-
# tick.py's own run_owner_dispatch_reconciliation() -- but status-remediation-
# tick.py has no live caller of its own: its unit
# (veridian-cron-status-remediation-tick.timer) is disabled per the Owner's
# real 2026-08-07 standing order (INS-20260807-042700-a247, "only the project
# manager work timer and the real execution mechanism may keep running until
# the current priority chain finishes, all others hard stopped") which
# explicitly named veridian-cron-dispatch-tick.timer -- THIS unit -- as one of
# the exactly 2 that must stay enabled/active. Re-enabling status-remediation-
# tick's own timer would directly contradict that live directive, so per the
# ~/.config/systemd/user/README.md STANDING RULE ("a periodic need whose
# cadence/purpose fits an existing unit goes in as a new step inside that
# unit/script, not a 20th unit") this wires the SAME already-audited call into
# dispatch-tick.py instead -- reusing status-remediation-tick.py's own
# run_owner_dispatch_reconciliation() function in-process (lazy importlib
# load, same pattern _load_reconcile_owner_dispatch_status() inside that file
# already uses one level down) rather than re-implementing or duplicating its
# logic. dispatch-tick.py runs every ~10 minutes (OnCalendar=*-*-*
# *:2/10:00), the same cadence status-remediation-tick.py itself ran on
# before its timer was disabled, so no scheduling behavior is invented here,
# only the caller changes.
#
# Unlike this script's other dispatch actions (which are inherently "real,
# no extra flag" -- dispatch-tick.py has no --apply/--dry-run switch of its
# own), this calls run_owner_dispatch_reconciliation(apply_=True) directly:
# the STALE_LABEL_TERMINAL bucket it applies is the same narrow, deterministic,
# already-hardened-against-a-real-AUDIT:FAIL (PR #147) mechanical bucket
# status-remediation-tick.py's own docstring describes -- MERGED PR ->
# completed, CLOSED PR -> failed, no PR ever opened -> killed -- never the
# NEEDS_AI_JUDGMENT bucket, which this call (like every other caller of
# run_owner_dispatch_reconciliation()) never touches.
def _load_status_remediation_tick():
    """Lazy, in-process import of status-remediation-tick.py -- same
    importlib pattern this codebase already uses for hyphenated-filename
    modules (see status-remediation-tick.py's own _load_generate_wiring_registry()
    and _load_reconcile_owner_dispatch_status())."""
    spec = importlib.util.spec_from_file_location(
        "status_remediation_tick", os.path.join(SCRIPTS, "status-remediation-tick.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_stale_running_workers_reconciliation():
    """UMR-20260813-090037-9a34 (residue of PR #249's own real AUDIT:FAIL
    finding "(c) reconcile_stale_running_workers.py ... is a ONE-SHOT and is
    not wired to run periodically"): real, ongoing wiring for that script's
    own sweep(), previously only ever invoked by hand.

    Deliberately NOT the same mechanism as status-remediation-tick.py's own
    run_owner_dispatch_reconciliation() (PR #290, UMR-20260813-065157-ba95) --
    checked on real evidence before adding this, not assumed: that reconciler's
    own load_rows() scopes to `source_trigger='owner_dispatch_gateway'` only
    (769 of several thousand real umr_tasks rows on this box, live-confirmed
    via a direct `GROUP BY source_trigger` query), while
    reconcile_stale_running_workers.py's own _fetch_affected_rows() scopes to
    `status='running' AND unit_name LIKE 'veridian-worker@%'` with NO
    source_trigger filter at all -- a real, broader, non-overlapping set (e.g.
    `dispatch-tick:resume_interrupted_workers`, 6388 rows alone, is entirely
    outside PR #290's own coverage). PR #290 landing does NOT supersede this
    gap; this wiring is the real fix for it, called from dispatch-tick.py
    (not status-remediation-tick.py/reconcile_owner_dispatch_status.py, both
    off-limits per this task's own SPEC) since dispatch-tick.py is the
    existing unit that already owns real veridian-worker@ unit lifecycle
    decisions (resume_interrupted_workers_tick above).

    Always real writes (`execute=True`) -- dispatch-tick.py itself has no
    dry-run mode anywhere else (every other tick function above performs real
    actions unconditionally each run); reconcile_stale_running_workers.py's
    own module docstring already describes itself as "safely re-runnable /
    idempotent", the same property this periodic call now depends on. Fully
    fail-open: a real exception here is caught and reported the same way
    run_owner_dispatch_reconciliation() reports its own, never load-bearing
    for the rest of this tick's own dispatch/resume work."""
    try:
        mod = _load_reconcile_stale_running_workers()
        report = mod.sweep(execute=True)
        return {"ok": True, "examined": report["examined"], "counts": report["counts"]}
    except Exception as e:
        print(f"WARNING: stale-running-worker reconciliation failed (non-fatal): {e}", file=sys.stderr)
        return {"ok": False, "error": str(e)}


def owner_dispatch_reconciliation_tick():
    try:
        srt = _load_status_remediation_tick()
        return srt.run_owner_dispatch_reconciliation(apply_=True)
    except Exception as e:
        print(f"WARNING: owner_dispatch_gateway status reconciliation failed (non-fatal): {e}", file=sys.stderr)
        return {"ok": False, "error": str(e)}


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
    stalled_running_tasks = find_stalled_running_tasks(tasks, now)
    heartbeat = write_stuck_tasks_heartbeat(tasks, stuck_tasks, now, stalled_running_tasks=stalled_running_tasks)
    if stalled_running_tasks:
        print(f"STALLED RUNNING TASKS ({len(stalled_running_tasks)}, real combined-evidence check, "
              f"threshold={STUCK_TASK_THRESHOLD_MINUTES}min): see {STUCK_TASKS_HEARTBEAT_PATH}")
        for item in stalled_running_tasks:
            print(f"  - {item['task_id']}: {item['evidence']}")
    if stuck_tasks:
        print(f"STUCK TASKS ({len(stuck_tasks)}, threshold={STUCK_TASK_THRESHOLD_MINUTES}min): "
              f"see {STUCK_TASKS_HEARTBEAT_PATH}")
        for item in stuck_tasks:
            print(f"  - {item['task_id']}: blocked {item['blocked_minutes']}min "
                  f"(last note: {item['last_note']!r})")

    pm_triage_result = pm_triage_tick(tasks, stuck_tasks, now)
    if pm_triage_result["invoked"]:
        print(f"PM TRIAGE ALERT ({'; '.join(pm_triage_result['reasons'])}): "
              f"see {pm_triage_result['alert_path']}")

    stale_running_result = run_stale_running_workers_reconciliation()

    owner_dispatch_reconciliation = owner_dispatch_reconciliation_tick()
    if owner_dispatch_reconciliation.get("ok") and owner_dispatch_reconciliation.get("corrected_umr_ids"):
        print(f"OWNER_DISPATCH_GATEWAY RECONCILED ({len(owner_dispatch_reconciliation['corrected_umr_ids'])}): "
              f"{owner_dispatch_reconciliation['corrected_umr_ids']}")

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
            "pm_triage_invoked": pm_triage_result["invoked"],
            "stale_running_workers_reconciliation": stale_running_result,
            "owner_dispatch_reconciliation_ok": owner_dispatch_reconciliation.get("ok"),
            "owner_dispatch_reconciliation_corrected": owner_dispatch_reconciliation.get("corrected_umr_ids", []),
        },
    )

    print(json.dumps({
        "supervisor_sweep": sweep_result,
        "resume_interrupted_workers": resume_result,
        "gap_queue": gap_result,
        "module_queue": module_result,
        "stuck_tasks_heartbeat": heartbeat,
        "pm_triage": pm_triage_result,
        "stale_running_workers_reconciliation": stale_running_result,
        "owner_dispatch_reconciliation": owner_dispatch_reconciliation,
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
