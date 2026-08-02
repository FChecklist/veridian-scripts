#!/usr/bin/env python3
"""resource_governor.py -- SERVER RESOURCE GOVERNOR (Owner directive, 2026-07-27).
See ai-os/SERVER_RESOURCE_GOVERNOR_2026-07-27.md for the full design doc.

Direct, evidenced response to a real incident found and fixed the same day:
veridian-task-watchdog.timer ran unstopped for 9h18m, firing every 60s with no
"is a worker already active for this issue" check, spawning duplicate
recovery/escalation actions for the same stalled task and driving load average
to 32 on this 8-core box. scripts/dispatch_core.py (PR #101) already closes the
COUNT-based half of this (one shared flock + CONCURRENCY_CAP across every real
spawn call site) -- this module sits IN FRONT of dispatch_core.py, not instead
of it, and closes the two gaps dispatch_core.py's own docstring does not cover:
(1) per-identity de-duplication of SEQUENTIAL submissions (dispatch_core.py
only ever sees concurrent unit COUNT, never "is this exact task already
queued"), and (2) real RAM/disk-I/O/network visibility (dispatch_core.py has
none -- only an implicit CPU-adjacent proxy via unit count).

Every real spawn still goes through dispatch_core.acquire_dispatch_lock() +
has_free_slot() + a real systemctl/veridian-task.py call, unmodified -- this
module never bypasses that gate, it only decides WHEN something is even
allowed to reach it.
"""
import argparse
import contextlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

VERIDIAN_ROOT = os.environ.get("VERIDIAN_ROOT", "/opt/veridian")
AI_OS = os.environ.get("VERIDIAN_AI_OS_DIR", f"{VERIDIAN_ROOT}/ai-os")
SCRIPTS = os.environ.get("VERIDIAN_SCRIPTS_DIR", f"{VERIDIAN_ROOT}/scripts")
LOCKS_DIR = os.environ.get("VERIDIAN_DISPATCH_LOCK_DIR", f"{AI_OS}/locks")
ATTENTION_PATH = os.environ.get("VERIDIAN_GOVERNOR_ATTENTION_PATH", f"{AI_OS}/logs/ATTENTION.md")

METRIC_STATE_PATH = os.environ.get(
    "VERIDIAN_GOVERNOR_METRIC_STATE", f"{LOCKS_DIR}/resource-governor-metric-state.json")
EMERGENCY_STATE_PATH = os.environ.get(
    "VERIDIAN_GOVERNOR_EMERGENCY_STATE", f"{LOCKS_DIR}/resource-governor-emergency-state.json")
EMERGENCY_STOP_PATH = os.environ.get(
    "VERIDIAN_GOVERNOR_EMERGENCY_STOP", f"{LOCKS_DIR}/resource-governor-EMERGENCY_STOP")

PROC_STAT_PATH = os.environ.get("VERIDIAN_GOVERNOR_PROC_STAT", "/proc/stat")
PROC_MEMINFO_PATH = os.environ.get("VERIDIAN_GOVERNOR_PROC_MEMINFO", "/proc/meminfo")
PROC_DISKSTATS_PATH = os.environ.get("VERIDIAN_GOVERNOR_PROC_DISKSTATS", "/proc/diskstats")
PROC_NETDEV_PATH = os.environ.get("VERIDIAN_GOVERNOR_PROC_NETDEV", "/proc/net/dev")

# The one hard cap this whole module exists to enforce, independently per
# metric -- any ONE of the four hitting this freezes the queue (SCOPE
# objective, literal "any one hitting 99% freezes the queue"). Overridable
# for tests only.
METRIC_THRESHOLD_PERCENT = float(os.environ.get("VERIDIAN_GOVERNOR_METRIC_THRESHOLD", "99.0"))

# Network is a raw cumulative /proc counter with no natural 0-100% ceiling
# (unlike CPU/RAM) -- normalized against a configured per-box capacity
# baseline. This is a conservative placeholder default; replace via env with
# this box's own measured real baseline (see design doc, Metric measurement
# section). Disk I/O does NOT use a capacity baseline -- see disk_io_percent()
# and read_disk_io_ticks(), which compute real %util directly from
# /proc/diskstats io-ticks (no arbitrary throughput ceiling needed).
NETWORK_CAPACITY_BYTES_PER_SEC = float(os.environ.get("VERIDIAN_GOVERNOR_NET_CAPACITY_BPS", str(int(100 * 1024 * 1024 / 8))))

TIER_MIN, TIER_MAX = 0, 4
DEFAULT_TIER = 2

# Anti-starvation aging (design doc "Dynamic realignment"): a queued item's
# effective priority is max(0, tier - age_seconds // this interval).
AGING_PROMOTION_INTERVAL_SECONDS = int(os.environ.get("VERIDIAN_GOVERNOR_AGING_INTERVAL_S", str(15 * 60)))

# Stuck-task protocol: timeout -> SIGTERM -> grace period -> SIGKILL.
STUCK_TASK_TIMEOUT_SECONDS = int(os.environ.get("VERIDIAN_GOVERNOR_STUCK_TIMEOUT_S", str(60 * 60)))
SIGTERM_TO_SIGKILL_GRACE_SECONDS = int(os.environ.get("VERIDIAN_GOVERNOR_SIGKILL_GRACE_S", "60"))

# Stage 3 (2026-07-29): how stale a running/dispatched row's last_heartbeat
# must be before reconcile_stale_heartbeats() will treat it as a candidate.
HEARTBEAT_STALE_TTL_SECONDS = int(os.environ.get("VERIDIAN_GOVERNOR_HEARTBEAT_TTL_S", str(15 * 60)))

# Emergency fail-safe cascade (design doc Section 7).
EMERGENCY_CONSECUTIVE_TICKS_SHED = int(os.environ.get("VERIDIAN_GOVERNOR_EMERGENCY_SHED_TICKS", "3"))
EMERGENCY_CONSECUTIVE_TICKS_HARDSTOP = int(os.environ.get("VERIDIAN_GOVERNOR_EMERGENCY_HARDSTOP_TICKS", "6"))

METRIC_NAMES = ("cpu", "ram", "disk_io", "network")


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _utcnow():
    return datetime.now(timezone.utc)


def _now_iso():
    return _utcnow().isoformat()


# ---------------------------------------------------------------------------
# superboss-register.py integration -- in-process importlib load, same
# pattern dispatch_core.py._superboss_register() already established (the
# filename has a hyphen, so it cannot be a plain `import`).
# ---------------------------------------------------------------------------

_sbr = None


def _superboss_register():
    global _sbr
    if _sbr is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "superboss_register_governor", os.path.join(SCRIPTS, "superboss-register.py"))
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sbr = _mod
    return _sbr


_dc = None


def _dispatch_core():
    """dispatch_core.py IS a plain-importable module name -- but it is loaded
    via importlib here too (not `import dispatch_core`), so a test copy of
    this whole script tree (tests/_dispatch_consolidation_fixtures.py-style
    fixture, tmp_path/scripts/) is always the one actually exercised, never
    whatever dispatch_core happens to be import-resolvable on sys.path."""
    global _dc
    if _dc is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "dispatch_core_governor", os.path.join(SCRIPTS, "dispatch_core.py"))
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _dc = _mod
    return _dc


_pg = None


def _plan_generator():
    """Phase 7 (reuse-check-enforcement-gate, 2026-07-30): loaded the same
    importlib-by-file-path way as _superboss_register()/_dispatch_core()
    above, for the same reason (a test copy of this whole script tree is
    always the one actually exercised, never whatever plan_generator happens
    to be import-resolvable on sys.path). Provides
    check_reuse_before_dispatch() -- the single shared "check
    capability_registry/wiring_registry/knowledge_engine/system_index before
    creating new work" enforcement point, called from submit() below so it
    runs for every real task creation, not only for callers whose own prompt
    happened to say to check first."""
    global _pg
    if _pg is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "plan_generator_governor", os.path.join(SCRIPTS, "plan_generator.py"))
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _pg = _mod
    return _pg


# ---------------------------------------------------------------------------
# Real /proc metric reads -- each is a pure function of (path) -> raw sample;
# converting a raw sample (or pair of samples) into a percent is a separate
# pure function below, so both halves are independently testable against
# fixture files/dicts, never a real live /proc read in tests.
# ---------------------------------------------------------------------------

def read_cpu_times(path=None):
    path = path or PROC_STAT_PATH
    with open(path) as f:
        for line in f:
            if line.startswith("cpu "):
                parts = [int(x) for x in line.split()[1:]]
                idle = parts[3] + (parts[4] if len(parts) > 4 else 0)  # idle + iowait
                total = sum(parts)
                return {"idle": idle, "total": total}
    raise ValueError(f"no 'cpu ' line found in {path}")


def cpu_percent(prev, curr):
    d_total = curr["total"] - prev["total"]
    d_idle = curr["idle"] - prev["idle"]
    if d_total <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * (1 - d_idle / d_total)))


def read_mem_percent(path=None):
    path = path or PROC_MEMINFO_PATH
    vals = {}
    with open(path) as f:
        for line in f:
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable"):
                vals[key] = int(rest.strip().split()[0])
    total = vals.get("MemTotal", 0)
    avail = vals.get("MemAvailable", total)
    if total <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * (1 - avail / total)))


def read_disk_io_ticks(path=None):
    """Sum of field-13 'time spent doing I/Os' (ms, monotonic counter of time
    the device had >=1 I/O outstanding) across every real WHOLE-disk device --
    loopN/ramN excluded (not real disk I/O), and PARTITIONS excluded too
    (sda1/sda14/sda15 etc double-count the same I/O their parent whole-disk
    sda already reports). Whole-disk names on this box don't end in a digit
    (sda, vda) -- that's the partition filter. /proc/diskstats fields (1-indexed
    per Documentation/admin-guide/iostats.rst): field 13 = parts[12] (0-indexed).
    This is the SAME calculation `iostat -x`'s %util column uses -- NOT raw
    sector throughput, which has no real device-independent "100%" meaning and
    was the root cause of 3 false EMERGENCY_STOP trips on 2026-07-29/30 (see
    instruction INS-20260730-043122-260a)."""
    path = path or PROC_DISKSTATS_PATH
    total = 0
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) < 14:
                continue
            name = parts[2]
            if name.startswith("loop") or name.startswith("ram"):
                continue
            if name[-1].isdigit():
                continue  # partition (sda1, sda14, nvme0n1p1, ...), not a whole disk
            total += int(parts[12])
    return total


def disk_io_percent(prev_io_ticks_ms, curr_io_ticks_ms, dt_seconds):
    """Real %util: fraction of the sampling window the disk had >=1 I/O
    outstanding. dt_seconds is wall-clock elapsed time between samples;
    io_ticks is already in ms, so convert dt to ms for the ratio."""
    if dt_seconds <= 0:
        return 0.0
    dt_ms = dt_seconds * 1000.0
    delta_ticks = max(0.0, curr_io_ticks_ms - prev_io_ticks_ms)
    return max(0.0, min(100.0, 100.0 * delta_ticks / dt_ms))


def read_net_bytes(path=None):
    """Sum of rx+tx bytes across every real interface (lo excluded).
    /proc/net/dev: 2 header lines, then 'iface: rx_bytes ... (8 fields)
    tx_bytes ... (8 fields)'."""
    path = path or PROC_NETDEV_PATH
    total = 0
    with open(path) as f:
        lines = f.readlines()
    for line in lines[2:]:
        if ":" not in line:
            continue
        iface, _, rest = line.partition(":")
        if iface.strip() == "lo":
            continue
        fields = rest.split()
        if len(fields) < 16:
            continue
        total += int(fields[0]) + int(fields[8])
    return total


def network_percent(prev_bytes, curr_bytes, dt_seconds, capacity_bytes_per_sec=None):
    capacity = NETWORK_CAPACITY_BYTES_PER_SEC if capacity_bytes_per_sec is None else capacity_bytes_per_sec
    if dt_seconds <= 0 or capacity <= 0:
        return 0.0
    rate = max(0.0, (curr_bytes - prev_bytes) / dt_seconds)
    return max(0.0, min(100.0, 100.0 * rate / capacity))


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def sample_metrics(now=None):
    """Real, delta-based sample of all 4 metrics against the PREVIOUSLY
    persisted raw sample (resource-governor-metric-state.json) -- delta-based
    so a single-shot tick invocation (cron, not just a long-lived loop) still
    computes a real rate, not just an instantaneous (and for disk/net,
    meaningless) counter value. First-ever call (no prior state) seeds state
    and reports 0% for the three delta-based metrics -- never freezes the
    queue on cold-start noise."""
    now = now or _utcnow()
    curr_cpu = read_cpu_times()
    curr_disk = read_disk_io_ticks()
    curr_net = read_net_bytes()
    ram = read_mem_percent()

    prev = _load_json(METRIC_STATE_PATH)
    state = {"ts": now.isoformat(), "cpu": curr_cpu, "disk_io_ticks_ms": curr_disk, "net_bytes": curr_net}
    _save_json(METRIC_STATE_PATH, state)

    if prev is None:
        return {"cpu": 0.0, "ram": ram, "disk_io": 0.0, "network": 0.0}

    dt = (now - datetime.fromisoformat(prev["ts"])).total_seconds()
    # Older state files (pre-fix) used "disk_sectors" -- if we see that key,
    # this is a cold-start-equivalent for disk_io specifically (0%), not a
    # crash, since the two metrics aren't comparable.
    prev_disk_ticks = prev.get("disk_io_ticks_ms")
    disk_io_value = 0.0 if prev_disk_ticks is None else disk_io_percent(prev_disk_ticks, curr_disk, dt)
    return {
        "cpu": cpu_percent(prev["cpu"], curr_cpu),
        "ram": ram,
        "disk_io": disk_io_value,
        "network": network_percent(prev["net_bytes"], curr_net, dt),
    }


def over_threshold_metrics(metrics, threshold=None):
    threshold = METRIC_THRESHOLD_PERCENT if threshold is None else threshold
    return [name for name in METRIC_NAMES if metrics.get(name, 0.0) >= threshold]


# ---------------------------------------------------------------------------
# Submission API + de-duplication (SCOPE items 2 and 4)
# ---------------------------------------------------------------------------

def submit(task_spec, tier, source_trigger):
    """Real submission entrypoint -- writes to the umr_tasks queue table
    instead of calling systemctl/veridian-task.py directly. Every scheduled
    trigger (cron, systemd timer, systemd worker spawn) must call this.

    task_spec: {
      "task_identity": str,   REQUIRED -- the real target task/issue identity
                               used for de-duplication (e.g. a stalled
                               task_id, or "rca-<task_id>" for a new
                               escalation). Two submissions with the same
                               task_identity while the first is still
                               queued/dispatched/running are never both let
                               through.
      "task_kind": "systemctl_action" | "veridian_task_create",
      "unit_name": str,        (systemctl_action) the real unit to act on
      "inputs": {
        "action": "start"|"restart"|"reset_failed_and_start",   (systemctl_action)
        "repo": str, "title": str, "prompt": str,                (veridian_task_create)
        ...arbitrary extra input fields recorded verbatim on the UMR row...
      },
    }
    Returns {"accepted": bool, "umr_id": str, "reason": str}. Never raises for
    a normal duplicate rejection -- that is a real, logged outcome, not an
    error.

    Phase 7 (reuse-check-enforcement-gate, 2026-07-30): also runs
    plan_generator.check_reuse_before_dispatch() against
    capability_registry/wiring_registry/knowledge_engine/system_index
    BEFORE the task row is written, and records the full structured result
    on the row itself (metadata_json.reuse_check_result), for both the
    accepted and rejected_duplicate outcomes. This is the one, real,
    software-enforced reuse check for this entrypoint specifically because
    it is the lowest-level real task-creation path everything else
    (task-gateway.py cmd_submit, directive_engine.py submit_task) funnels
    into -- unlike those two callers, which already run their own
    check-duplicate/search/query-knowledge/lookup-capability battery before
    ever reaching here, a caller that constructs a task_spec and calls this
    function directly (or via `resource_governor.py --submit`) previously
    had no such check unless its own prompt/author happened to remember to
    run one by hand. Advisory only, same fail-open philosophy as
    directive_engine.py's find_in_flight_duplicate()/
    run_check_duplicate_battery(): a low-confidence or no-match result (or
    a broken check) never blocks the submission, it is only recorded for
    accountability.
    """
    if not (TIER_MIN <= tier <= TIER_MAX):
        raise ValueError(f"tier must be an int {TIER_MIN}..{TIER_MAX}, got {tier!r}")
    task_identity = task_spec["task_identity"]

    inputs_for_reuse_check = task_spec.get("inputs", {}) or {}
    intent_text = (
        inputs_for_reuse_check.get("prompt")
        or inputs_for_reuse_check.get("title")
        or inputs_for_reuse_check.get("action")
        or task_spec.get("unit_name")
        or task_identity
    )
    try:
        reuse_check_result = _plan_generator().check_reuse_before_dispatch(
            intent_text, task_identity=task_identity)
    except Exception as e:
        # Fail-open -- a broken reuse-check must never block a real,
        # legitimate submission (same philosophy as directive_engine.py's
        # find_in_flight_duplicate()/run_check_duplicate_battery()).
        reuse_check_result = {
            "error": f"reuse-check failed, fail-open: {e}",
            "intent_text": intent_text,
            "task_identity": task_identity,
            "recommendation": "proceed",
            "confidence": 0.0,
            "reuse_candidates": [],
        }

    sbr = _superboss_register()
    with sbr._write_lock():
        conn = sbr._connect()
        sbr._ensure_umr_table(conn)
        existing = sbr.find_active_umr_by_identity(conn, task_identity)
        if existing:
            reason = (
                f"duplicate submission rejected: task_identity={task_identity!r} already "
                f"{existing['status']} as umr_id={existing['umr_id']} "
                f"(source_trigger={existing['source_trigger']!r}, tier={existing['tier']})"
            )
            umr_id = sbr.upsert_umr_task(conn, {
                "task_identity": task_identity,
                "tier": tier,
                "status": "rejected_duplicate",
                "source_trigger": source_trigger,
                "task_kind": task_spec.get("task_kind", "systemctl_action"),
                "unit_name": task_spec.get("unit_name"),
                "inputs": task_spec.get("inputs", {}),
                "reason": reason,
                "metadata": {"reuse_check_result": reuse_check_result},
            })
            conn.commit()
            conn.close()
            return {"accepted": False, "umr_id": umr_id, "reason": reason,
                     "reuse_check_result": reuse_check_result}

        umr_id = sbr.upsert_umr_task(conn, {
            "task_identity": task_identity,
            "tier": tier,
            "status": "queued",
            "source_trigger": source_trigger,
            "task_kind": task_spec.get("task_kind", "systemctl_action"),
            "unit_name": task_spec.get("unit_name"),
            "inputs": task_spec.get("inputs", {}),
            "reason": "queued",
            "metadata": {"reuse_check_result": reuse_check_result},
        })
        conn.commit()
        conn.close()
    return {"accepted": True, "umr_id": umr_id, "reason": "queued",
             "reuse_check_result": reuse_check_result}


# ---------------------------------------------------------------------------
# Dynamic realignment (anti-starvation aging) + dispatcher
# ---------------------------------------------------------------------------

def effective_priority(row, now=None):
    now = now or _utcnow()
    ts_submitted = row["ts_submitted"]
    if isinstance(ts_submitted, str):
        ts_submitted = datetime.fromisoformat(ts_submitted)
    age_seconds = max(0.0, (now - ts_submitted).total_seconds())
    promotions = int(age_seconds // AGING_PROMOTION_INTERVAL_SECONDS)
    return max(TIER_MIN, row["tier"] - promotions)


def next_queued_task(conn, now=None):
    now = now or _utcnow()
    rows = conn.execute("SELECT * FROM umr_tasks WHERE status='queued'").fetchall()
    if not rows:
        return None
    ranked = sorted(rows, key=lambda r: (effective_priority(dict(r), now), r["ts_submitted"]))
    return dict(ranked[0])


def _perform_spawn(row):
    """The real spawn -- systemctl/veridian-task.py, unchanged calls, gated
    by dispatch_core.py exactly as every other consolidated tick script
    already does. Returns {"status", "unit_name", "outputs"}."""
    task_kind = row["task_kind"]
    inputs = row.get("inputs_json")
    inputs = json.loads(inputs) if isinstance(inputs, str) else (inputs or {})

    if task_kind == "systemctl_action":
        unit = row["unit_name"]
        action = inputs.get("action", "start")
        if action == "reset_failed_and_start":
            _run(["systemctl", "--user", "reset-failed", unit])
            r = _run(["systemctl", "--user", "start", unit])
        elif action == "restart":
            r = _run(["systemctl", "--user", "restart", unit])
        else:
            r = _run(["systemctl", "--user", "start", unit])
        status = "running" if r.returncode == 0 else "failed"
        return {"status": status, "unit_name": unit, "outputs": {"returncode": r.returncode, "stderr": r.stderr[:500]}}

    if task_kind == "veridian_task_create":
        cmd = ["python3", os.path.join(SCRIPTS, "veridian-task.py"), "create",
               "--title", inputs["title"], "--repo", inputs.get("repo", "claude-control"),
               "--prompt", inputs["prompt"]]
        r = _run(cmd)
        m = re.search(r"^CREATED: (\S+)", r.stdout, re.MULTILINE)
        new_task_id = m.group(1) if m else None
        unit_name = f"veridian-worker@{new_task_id}.service" if new_task_id else None
        if unit_name:
            _run(["systemctl", "--user", "start", unit_name])
        status = "running" if new_task_id else "failed"
        return {"status": status, "unit_name": unit_name,
                "outputs": {"new_task_id": new_task_id, "returncode": r.returncode, "stderr": r.stderr[:500]}}

    return {"status": "failed", "unit_name": row.get("unit_name"), "outputs": {"error": f"unknown task_kind {task_kind!r}"}}


def dispatch_one(dry_run=False, now=None):
    """Checks all 4 real metrics, and only if NONE are at/over threshold,
    acquires dispatch_core's shared lock and does EVERYTHING else --
    selecting the next queued row, the free-slot check, and the real spawn
    -- from inside that one critical section. Selecting the row before
    acquiring the lock (an earlier version of this function did that) let
    two concurrent callers both pick the SAME 'queued' row before either had
    claimed it, so both proceeded to spawn it: exactly the TOCTOU race
    dispatch_core.has_free_slot()'s own docstring already warns about
    ("callers must check this WHILE holding acquire_dispatch_lock(), never
    before/after"). Never raises for a normal 'nothing to do'/'frozen'
    outcome."""
    if os.path.exists(EMERGENCY_STOP_PATH):
        return {"action": "emergency_stopped",
                "detail": "EMERGENCY_STOP sentinel present -- clear via --clear-emergency-stop"}

    metrics = sample_metrics(now=now)
    over = over_threshold_metrics(metrics)
    _record_emergency_tick(over)
    if over:
        return {"action": "frozen", "detail": f"metric(s) at/over {METRIC_THRESHOLD_PERCENT}%: {over}",
                "metrics": metrics}

    dc = _dispatch_core()
    sbr = _superboss_register()

    with dc.acquire_dispatch_lock():
        conn = sbr._connect()
        sbr._ensure_umr_table(conn)
        row = next_queued_task(conn, now=now)
        if row is None:
            conn.close()
            return {"action": "idle", "detail": "queue empty", "metrics": metrics}

        if not dc.has_free_slot():
            conn.close()
            return {"action": "deferred", "detail": "no free concurrency slot under dispatch_core's shared cap",
                     "umr_id": row["umr_id"], "metrics": metrics}

        if dry_run:
            conn.close()
            return {"action": "would_dispatch", "umr_id": row["umr_id"], "metrics": metrics}

        result = _perform_spawn(row)
        with sbr._write_lock():
            sbr.update_umr_task(
                conn, row["umr_id"], status=result["status"],
                unit_name=result.get("unit_name") or row["unit_name"],
                ts_dispatched=_now_iso(), outputs=result.get("outputs", {}), metric_snapshot=metrics,
            )
            conn.commit()
        if result["status"] == "running":
            dc.record_dispatch_event(
                task_id=row["task_identity"], dispatched_by=f"resource_governor:{row['source_trigger']}",
                source_queue_or_plan="umr_tasks", worker_unit=result.get("unit_name") or row["unit_name"] or "",
            )
        conn.close()
    return {"action": "dispatched", "umr_id": row["umr_id"], "result": result, "metrics": metrics}


def run_tick(max_dispatches=None, now=None):
    """One full governor pass: stuck-task scan, then priority-ordered
    dispatch until the queue is empty, a slot/metric limit stops it, or
    max_dispatches is reached."""
    results = {"stuck_task_actions": scan_stuck_tasks(now=now), "dispatches": []}
    while max_dispatches is None or len(results["dispatches"]) < max_dispatches:
        r = dispatch_one(now=now)
        results["dispatches"].append(r)
        if r["action"] != "dispatched":
            break
    return results


# ---------------------------------------------------------------------------
# Stuck-task SIGTERM/SIGKILL protocol
# ---------------------------------------------------------------------------

def _unit_active_enter_timestamp(unit):
    """Real systemd ActiveEnterTimestamp for `unit` -- the same source of
    truth veridian-task-watchdog.py already trusts for checkpoint staleness,
    never a self-tracked approximation. Returns None if the unit is
    inactive/unknown or the timestamp can't be parsed."""
    r = _run(["systemctl", "--user", "show", unit, "-p", "ActiveEnterTimestamp", "--value"])
    ts = r.stdout.strip()
    if not ts or ts in ("n/a", ""):
        return None
    for fmt in ("%a %Y-%m-%d %H:%M:%S %Z", "%Y-%m-%d %H:%M:%S %Z", "%a %Y-%m-%d %H:%M:%S %z"):
        try:
            parsed = datetime.strptime(ts, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def scan_stuck_tasks(now=None):
    """timeout -> SIGTERM -> SIGTERM_TO_SIGKILL_GRACE_SECONDS -> SIGKILL,
    using each unit's real ActiveEnterTimestamp to measure elapsed time.
    Returns the list of actions actually taken this call (empty if nothing
    was stuck)."""
    now = now or _utcnow()
    sbr = _superboss_register()
    conn = sbr._connect()
    sbr._ensure_umr_table(conn)
    actions = []

    running = conn.execute(
        "SELECT * FROM umr_tasks WHERE status='running' AND unit_name IS NOT NULL"
    ).fetchall()
    for row in running:
        row = dict(row)
        started = _unit_active_enter_timestamp(row["unit_name"])
        if started is None:
            continue
        elapsed = (now - started).total_seconds()
        if elapsed >= STUCK_TASK_TIMEOUT_SECONDS:
            _run(["systemctl", "--user", "kill", "-s", "SIGTERM", row["unit_name"]])
            with sbr._write_lock():
                sbr.update_umr_task(conn, row["umr_id"], status="sigterm_sent", ts_sigterm=_now_iso())
                conn.commit()
            actions.append({"umr_id": row["umr_id"], "unit_name": row["unit_name"], "action": "SIGTERM",
                             "elapsed_s": elapsed})

    sigtermed = conn.execute("SELECT * FROM umr_tasks WHERE status='sigterm_sent'").fetchall()
    for row in sigtermed:
        row = dict(row)
        if not row.get("ts_sigterm"):
            continue
        since_sigterm = (now - datetime.fromisoformat(row["ts_sigterm"])).total_seconds()
        if since_sigterm >= SIGTERM_TO_SIGKILL_GRACE_SECONDS:
            _run(["systemctl", "--user", "kill", "-s", "SIGKILL", row["unit_name"]])
            with sbr._write_lock():
                sbr.update_umr_task(conn, row["umr_id"], status="killed", ts_completed=_now_iso())
                conn.commit()
            actions.append({"umr_id": row["umr_id"], "unit_name": row["unit_name"], "action": "SIGKILL",
                             "since_sigterm_s": since_sigterm})

    conn.close()
    return actions


# ---------------------------------------------------------------------------
# Stale-heartbeat reconciliation (Stage 3, 2026-07-29)
# ---------------------------------------------------------------------------

def _unit_exit_terminal_status(unit):
    """Real systemd Result for `unit` -- used only to decide completed vs
    failed when reconciling a stale-heartbeat row whose unit is no longer
    active. 'completed' requires Result=success; anything else (crashed,
    signalled, non-zero exit, timeout, oom-kill, or an unreadable/unexpected
    read) fails CLOSED to 'failed' -- an ambiguous read must never be
    miscounted as a success.

    FIX (2026-07-29 stress-test round 1, 2 confirmed bugs in the original
    implementation, both reproduced live):
    (1) `systemctl show unit -p SubState -p ExecMainStatus --value` does NOT
        return lines in the order the -p flags were given -- systemd emits
        properties in its own fixed internal schema order regardless of
        request order. Live-verified: for a real completed unit this
        actually printed ExecMainStatus ("15") on line 0 and SubState
        ("dead") on line 1, i.e. exactly swapped from what the old code
        assumed (`lines[0]`=substate, `lines[1]`=exec_status) -- so
        `substate == "exited"` was really comparing an ExecMainStatus number
        to the string "exited", which can never match. Fixed by parsing
        `KEY=VALUE` output (order-independent) instead of relying on line
        position.
    (2) Even with correct parsing, SubState never becomes "exited" for this
        unit template -- veridian-worker@.service/veridian-docworker@.service
        are both Type=simple, and Type=simple units transition to
        SubState=dead (not exited) on any exit, clean or not (SubState=exited
        is a Type=oneshot/RemainAfterExit concept). Live-verified against all
        5 real historical completed units on this box: every one shows
        SubState=dead, Result=success. Fixed by keying off Result (systemd's
        own designed-for-this aggregate success/failure verdict, "success"
        iff the service's main process exited 0 and no other failure --
        timeout/signal/core-dump/oom-kill/etc -- occurred) instead of
        SubState/ExecMainStatus, which is also Type-independent.
    Net effect of both bugs together: this function could never return
    "completed" under any real circumstance -- every reconciled row was
    unconditionally marked "failed" regardless of real outcome."""
    r = _run(["systemctl", "--user", "show", unit, "-p", "Result", "-p", "ExecMainStatus", "-p", "SubState"])
    props = {}
    for line in r.stdout.strip().splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            props[key.strip()] = value.strip()
    if props.get("Result") == "success":
        return "completed"
    return "failed"


def reconcile_stale_heartbeats(now=None, ttl_seconds=None):
    """Fix for 'task exits cleanly but umr_tasks status never reconciles' (5
    real historical instances found 2026-07-29): worker-entrypoint.sh /
    doc-worker-entrypoint.sh checkpoint task.yaml via veridian-task.py on
    every exit path, but nothing ever wrote the matching terminal status back
    onto the umr_tasks row that dispatched it -- a row could sit at
    status='running' indefinitely after its unit had already exited cleanly.
    This is a periodic sweep, run from resource_governor_tick_loop.sh right
    after --tick, same 30s cadence as the dispatcher itself -- it does not
    trust any process to call back in on exit.

    CRITICAL (2026-07-29 adversarial review, verify before relying on this):
    last_heartbeat is a brand-new column (see superboss-register.py's
    _migrate_umr_last_heartbeat) -- every umr_tasks row written before this
    deploy, which includes ALL 5 real in-flight tasks at the moment this
    ships (PR617-REVIEW, PR618-REVIEW, PR58-CONFLICT, PR610-CONFLICT,
    PHASE-2-CROSSREF), has last_heartbeat NULL, not old-and-expired. The SQL
    WHERE clause below excludes NULL by construction -- a row only becomes
    eligible for this sweep once it HAS a real last_heartbeat that has since
    gone stale, so there is no code path here that can flag a row on its
    first tick post-deploy, and systemctl is never even invoked for a
    healthy or not-yet-instrumented row.

    Returns the list of rows actually reconciled this call (empty if none
    were stale, which is the expected/normal steady-state result)."""
    now = now or _utcnow()
    ttl = ttl_seconds if ttl_seconds is not None else HEARTBEAT_STALE_TTL_SECONDS
    cutoff = (now - timedelta(seconds=ttl)).isoformat()
    sbr = _superboss_register()
    conn = sbr._connect()
    sbr._ensure_umr_table(conn)
    stale = conn.execute(
        "SELECT * FROM umr_tasks WHERE status IN ('running','dispatched') "
        "AND last_heartbeat IS NOT NULL AND last_heartbeat < ?",
        (cutoff,),
    ).fetchall()
    actions = []
    for row in stale:
        row = dict(row)
        unit = row.get("unit_name")
        if not unit:
            continue  # nothing to check liveness against -- leave for human/other path
        is_active = _run(["systemctl", "--user", "is-active", "--quiet", unit]).returncode == 0
        if is_active:
            continue  # genuinely still running, just a slow/missed heartbeat -- not stale
        # Real fix, 2026-07-29 (zombie-worker incident): a unit's real process
        # exiting does NOT remove its default.target.wants/ enable-symlink --
        # only `disable` does. Without this, a systemd --user manager restart
        # (confirmed live, 05:53:27 UTC) resurrects every unit ever enabled,
        # regardless of its real umr_tasks status, silently burning real CPU
        # re-running already-finished/killed work. Safe here specifically
        # because is_active is already confirmed False above -- disable never
        # touches a running unit's live state, only its boot-time wiring.
        _run(["systemctl", "--user", "disable", unit])
        terminal = _unit_exit_terminal_status(unit)
        with sbr._write_lock():
            sbr.update_umr_task(
                conn, row["umr_id"], status=terminal, ts_completed=_now_iso(),
                reason=(f"reconciled by heartbeat sweep: unit {unit} inactive, last_heartbeat "
                        f"stale (>{ttl}s), real exit status={terminal}"),
            )
            conn.commit()
        actions.append({"umr_id": row["umr_id"], "unit_name": unit, "reconciled_to": terminal})
    conn.close()
    return actions


# ---------------------------------------------------------------------------
# Emergency fail-safe cascade (design doc Section 7)
# ---------------------------------------------------------------------------

def _append_attention(message):
    os.makedirs(os.path.dirname(ATTENTION_PATH), exist_ok=True)
    with open(ATTENTION_PATH, "a") as f:
        f.write(f"\n## {_now_iso()} -- SERVER RESOURCE GOVERNOR\n{message}\n")


def _shed_load(state):
    """Stage 2: SIGTERM the governor's own lowest-tier-priority currently
    running tracked unit, freeing real resources instead of just refusing new
    work. Returns the unit_name shed, or None if there was nothing to shed."""
    sbr = _superboss_register()
    conn = sbr._connect()
    sbr._ensure_umr_table(conn)
    running = conn.execute(
        "SELECT * FROM umr_tasks WHERE status='running' AND unit_name IS NOT NULL "
        "ORDER BY tier DESC, ts_dispatched ASC"
    ).fetchall()
    if not running:
        conn.close()
        _append_attention(f"CRITICAL: sustained metric overload {state}, but no governor-tracked "
                           f"running unit available to shed load from.")
        return None

    victim = dict(running[0])
    _run(["systemctl", "--user", "kill", "-s", "SIGTERM", victim["unit_name"]])
    with sbr._write_lock():
        sbr.update_umr_task(conn, victim["umr_id"], status="sigterm_sent", ts_sigterm=_now_iso())
        conn.commit()
    conn.close()
    _append_attention(
        f"CRITICAL: sustained metric overload {state} -- shed load by SIGTERM to lowest-tier "
        f"running unit {victim['unit_name']} (umr_id={victim['umr_id']}, tier={victim['tier']})."
    )
    return victim["unit_name"]


def _write_emergency_stop(state):
    _save_json(EMERGENCY_STOP_PATH, {"ts": _now_iso(), "state": state})
    _append_attention(
        f"EMERGENCY STOP: metrics stayed at/over {METRIC_THRESHOLD_PERCENT}% for "
        f"{EMERGENCY_CONSECUTIVE_TICKS_HARDSTOP} consecutive governor ticks ({state}). All new "
        f"dispatch is halted until an operator runs "
        f"`python3 scripts/resource_governor.py --clear-emergency-stop`."
    )


def _record_emergency_tick(over_metrics):
    """Per-metric consecutive-over-threshold counter, reset to 0 the instant a
    metric drops back under threshold. Escalates through shed-load (Stage 2)
    then hard-stop (Stage 3) as the max consecutive count crosses each
    threshold. Returns the updated state dict."""
    state = _load_json(EMERGENCY_STATE_PATH) or {}
    max_consecutive = 0
    for metric in METRIC_NAMES:
        count = state.get(metric, 0)
        count = count + 1 if metric in over_metrics else 0
        state[metric] = count
        max_consecutive = max(max_consecutive, count)
    _save_json(EMERGENCY_STATE_PATH, state)

    if max_consecutive >= EMERGENCY_CONSECUTIVE_TICKS_HARDSTOP:
        _write_emergency_stop(state)
    elif max_consecutive >= EMERGENCY_CONSECUTIVE_TICKS_SHED:
        _shed_load(state)
    return state


def clear_emergency_stop():
    if os.path.exists(EMERGENCY_STOP_PATH):
        os.remove(EMERGENCY_STOP_PATH)
    _save_json(EMERGENCY_STATE_PATH, {})
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tick", action="store_true",
                     help="run one full dispatcher pass: stuck-task scan + priority-ordered dispatch "
                          "under the 4-metric 99%% gate")
    ap.add_argument("--submit", action="store_true", help="submit one task_spec (--spec-file) to the queue")
    ap.add_argument("--spec-file", default=None, help="path to a JSON file matching submit()'s task_spec shape")
    ap.add_argument("--tier", type=int, default=DEFAULT_TIER, help="0 (highest) .. 4 (lowest)")
    ap.add_argument("--source-trigger", default="manual")
    ap.add_argument("--scan-stuck", action="store_true", help="run only the stuck-task SIGTERM/SIGKILL scan")
    ap.add_argument("--reconcile-stale", action="store_true",
                     help="Stage 3: sweep umr_tasks rows in running/dispatched with a stale "
                          "last_heartbeat (NULL heartbeats are always skipped) and write back real "
                          "terminal status via systemctl --user is-active, scoped only to the stale subset")
    ap.add_argument("--query-umr", action="store_true", help="search/list umr_tasks rows")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--status", default=None)
    ap.add_argument("--search", default=None, help="free-text FTS5 query over task_identity/source_trigger/logs_ref")
    ap.add_argument("--task-identity", dest="task_identity", default=None)
    ap.add_argument("--clear-emergency-stop", action="store_true")
    args = ap.parse_args()

    if args.clear_emergency_stop:
        clear_emergency_stop()
        print(json.dumps({"ok": True, "cleared": True}))
        return

    if args.query_umr:
        sbr = _superboss_register()
        conn = sbr._connect()
        sbr._ensure_umr_table(conn)
        rows = sbr.query_umr_tasks(conn, limit=args.limit, status=args.status,
                                    task_identity=args.task_identity, query_text=args.search)
        conn.close()
        print(json.dumps({"count": len(rows), "matches": rows}, indent=2, default=str))
        return

    if args.submit:
        if not args.spec_file:
            print(json.dumps({"error": "--submit requires --spec-file"}))
            sys.exit(1)
        with open(args.spec_file) as f:
            task_spec = json.load(f)
        result = submit(task_spec, args.tier, args.source_trigger)
        print(json.dumps(result))
        return

    if args.scan_stuck:
        print(json.dumps({"actions": scan_stuck_tasks()}, default=str))
        return

    if args.reconcile_stale:
        print(json.dumps({"actions": reconcile_stale_heartbeats()}, default=str))
        return

    if args.tick:
        print(json.dumps(run_tick(), default=str))
        return

    ap.print_help()


if __name__ == "__main__":
    main()
