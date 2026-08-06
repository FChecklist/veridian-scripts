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
import fcntl
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
# 2026-07-29 adversarial-test fix: unbounded task_identity length was a real
# storage/FTS5-index-bloat vector (500KB string accepted with no cap, live-
# confirmed). branch names, filenames, and DB rows all use this value, so a
# generous but real cap.
MAX_TASK_IDENTITY_LEN = 500

# Anti-starvation aging (design doc "Dynamic realignment"): a queued item's
# effective priority is max(0, tier - age_seconds // this interval).
AGING_PROMOTION_INTERVAL_SECONDS = int(os.environ.get("VERIDIAN_GOVERNOR_AGING_INTERVAL_S", str(15 * 60)))

# Stuck-task protocol: timeout -> SIGTERM -> grace period -> SIGKILL.
STUCK_TASK_TIMEOUT_SECONDS = int(os.environ.get("VERIDIAN_GOVERNOR_STUCK_TIMEOUT_S", str(60 * 60)))

# Real max-queued-age safeguard (dispatch-queue-starvation investigation,
# UMR-20260806-090229-f2a7, parent UMR-20260806-071025-1d28): confirmed live
# that a real queued row can silently starve far longer than any legitimate
# transient backpressure delay -- 30 real tier-1 umr_tasks rows sat
# status='queued' for ~2 real days because a single, unrelated, chronically-
# resubmitted task_identity permanently occupied next_queued_task()'s #1 rank
# (real root cause fixed separately in directive_engine.py's process_one(),
# see that function's own comment) and dispatch_one()/run_tick() only ever
# evaluate that one top-ranked row per tick. Real legitimate concurrency/
# resource-headroom backoff, by contrast, was directly observed clearing
# within tens of minutes in every real log sample checked during this
# investigation (dispatch_one()'s own OCID comment documents 27-74 real
# minutes as an expected, working-as-designed delay). 4 hours is chosen to
# sit comfortably above every legitimate delay actually observed on this box,
# while still surfacing a real stall the same real day it starts rather than
# after multiple days of silence.
MAX_QUEUED_AGE_SECONDS = int(os.environ.get("VERIDIAN_GOVERNOR_MAX_QUEUED_AGE_S", str(4 * 60 * 60)))
SIGTERM_TO_SIGKILL_GRACE_SECONDS = int(os.environ.get("VERIDIAN_GOVERNOR_SIGKILL_GRACE_S", "60"))

# Stage 3 reconciliation sweep (2026-07-29, "task exits cleanly but umr_tasks
# status never reconciles" fix): worker-entrypoint.sh/doc-worker-entrypoint.sh
# touch last_heartbeat every 300s (their periodic checkpoint loop) plus at
# each credit-accountant.py report call site -- 900s (3x the 300s interval)
# gives real headroom for a slow tick/lock contention before a live task is
# ever considered stale. Overridable for tests only.
HEARTBEAT_STALE_TTL_SECONDS = int(os.environ.get("VERIDIAN_GOVERNOR_HEARTBEAT_TTL_S", str(15 * 60)))

# Emergency fail-safe cascade (design doc Section 7).
EMERGENCY_CONSECUTIVE_TICKS_SHED = int(os.environ.get("VERIDIAN_GOVERNOR_EMERGENCY_SHED_TICKS", "3"))
EMERGENCY_CONSECUTIVE_TICKS_HARDSTOP = int(os.environ.get("VERIDIAN_GOVERNOR_EMERGENCY_HARDSTOP_TICKS", "6"))

METRIC_NAMES = ("cpu", "ram", "disk_io", "network")

# Stage 4 (2026-07-29): duplicate-PR guard. Real incidents this closes: PR
# #617 redispatched 6x, PR #58 redispatched into two separate PRs (#64, #65)
# -- same task_identity submitted again after its prior UMR row already went
# terminal (killed/failed/completed), with a PR from the earlier run still
# open. find_active_umr_by_identity() in submit() only rejects a SECOND
# submission while the FIRST is still active (queued/dispatched/running); it
# cannot see a prior run that already finished and already has a PR.
GH_ORG = os.environ.get("VERIDIAN_GH_ORG", "FChecklist")
GH_PR_CHECK_REPOS = tuple(
    r.strip() for r in os.environ.get("VERIDIAN_GOVERNOR_GH_PR_CHECK_REPOS",
                                       "compliance-tracker,projexa").split(",") if r.strip()
)
GH_PR_CHECK_TIMEOUT_SECONDS = int(os.environ.get("VERIDIAN_GOVERNOR_GH_PR_CHECK_TIMEOUT_S", "8"))


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


def _safe_superboss_register(context):
    """Real fix (independent review round 2, PR #20): centralizes the
    fail-open wrapper around _superboss_register() in ONE place, so every
    real call site gets the same protection automatically -- the first fix
    round only wrapped submit()'s own call site individually and the
    reviewer correctly found four more unguarded ones (dispatch_one() /
    run_tick()'s core dispatch path, scan_stuck_tasks()'s SIGTERM/SIGKILL
    safety net, _shed_load()'s emergency load-shedding cascade, and main()'s
    --query-umr handler), each a real gap the same class of bug could
    recur in if patched individually again. superboss-register.py's own
    resolve_superboss_db_path() (OCID-068, UMR-20260804-180210-9e2c) raises
    SuperbossDbPathError unconditionally at module-import time on any
    verification failure -- by design, never a silent fallback -- and since
    _superboss_register() is not cached across process runs (this whole
    script runs as a brand-new process every real tick), a transient
    verification hiccup must never crash the caller uncaught.

    Returns (sbr_module_or_None, error_message_or_None). On failure, also
    appends a real CRITICAL entry to ATTENTION.md via _append_attention()
    (defined below) so a genuine infrastructure problem is never silently
    swallowed -- every caller still gets real, honest visibility, just
    without an uncaught crash. `context` is a short, real label (e.g.
    "dispatch_one", "scan_stuck_tasks") identifying which real call site hit
    the failure, since ATTENTION.md and any caller-logged reason should say
    which real operation was blocked, not just that "something" failed."""
    try:
        return _superboss_register(), None
    except Exception as e:
        error = f"superboss_register_unavailable ({context}): {e}"
        try:
            _append_attention(f"CRITICAL: {error}")
        except Exception:
            pass  # _append_attention itself must never mask the real original error
        return None, error


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


@contextlib.contextmanager
def _state_file_lock(path):
    """Serializes the read-modify-write cycle of a governor state JSON file
    (resource-governor-metric-state.json / resource-governor-emergency-state.json)
    across processes -- same proven pattern as superboss-register.py's own
    _write_lock() (built for the 2026-07-18 CONTROLLER.yaml corruption; see
    that function's docstring for the full incident history). Stage 0a
    (2026-07-29): neither state file had any locking at all -- two concurrent
    callers (e.g. a manual --tick plus the live veridian-governor-tick.service
    loop, or two overlapping loop iterations) could each read the same prior
    state, then both write back, silently dropping whichever write lost the
    race. For the emergency-state file specifically, a dropped increment (or a
    dropped reset back to 0) can desync the real consecutive-over-threshold
    count from what actually happened metric-by-metric -- a plausible
    contributor to the repeated-EMERGENCY_STOP-trip symptom this fix targets.
    Acquiring this OS file lock around the whole load-then-save cycle (not
    just the save) closes that race. flock is only held for the duration of
    the `with` block and is auto-released if the holder is killed, so this
    cannot deadlock the tick loop; it also does not change any behavior when
    there is no contention."""
    lock_path = path + ".lock"
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "w") as lockfile:
        fcntl.flock(lockfile, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lockfile, fcntl.LOCK_UN)


def sample_metrics(now=None):
    """Real, delta-based sample of all 4 metrics against the PREVIOUSLY
    persisted raw sample (resource-governor-metric-state.json) -- delta-based
    so a single-shot tick invocation (cron, not just a long-lived loop) still
    computes a real rate, not just an instantaneous (and for disk/net,
    meaningless) counter value. First-ever call (no prior state) seeds state
    and reports 0% for the three delta-based metrics -- never freezes the
    queue on cold-start noise.

    Stage 0a (2026-07-29): also returns a "raw" sub-dict alongside the
    existing derived percentages -- the real io-ticks delta and the real
    elapsed dt_seconds between samples that disk_io_percent() computes its
    percentage from. Added for forensic diagnosis of EMERGENCY_STOP trips
    after the fact (the derived percentage alone doesn't tell an operator
    whether a trip was a real sustained I/O spike or a dt/counter artifact).
    Purely additive -- does not change any existing key's value.

    2026-07-30 real fix: disk I/O now uses read_disk_io_ticks() (real
    iostat-style %util, see that function's docstring) instead of raw sector
    throughput, which was the root cause of 3 false EMERGENCY_STOP trips.
    The "raw" sub-dict below reports the io-ticks-based values accordingly.
    Older state files (pre-fix) used "disk_sectors" -- if we see that key,
    this is a cold-start-equivalent for disk_io specifically (0%), not a
    crash, since the two metrics aren't comparable."""
    now = now or _utcnow()
    curr_cpu = read_cpu_times()
    curr_disk = read_disk_io_ticks()
    curr_net = read_net_bytes()
    ram = read_mem_percent()

    with _state_file_lock(METRIC_STATE_PATH):
        prev = _load_json(METRIC_STATE_PATH)
        state = {"ts": now.isoformat(), "cpu": curr_cpu, "disk_io_ticks_ms": curr_disk, "net_bytes": curr_net}
        _save_json(METRIC_STATE_PATH, state)

    if prev is None:
        return {
            "cpu": 0.0, "ram": ram, "disk_io": 0.0, "network": 0.0,
            "raw": {
                "disk_io_prev_ticks_ms": None,
                "disk_io_curr_ticks_ms": curr_disk,
                "disk_io_ticks_delta_ms": None,
                "disk_io_dt_seconds": None,
            },
        }

    dt = (now - datetime.fromisoformat(prev["ts"])).total_seconds()
    prev_disk_ticks = prev.get("disk_io_ticks_ms")
    if prev_disk_ticks is None:
        disk_io_value = 0.0
        raw_disk = {
            "disk_io_prev_ticks_ms": None,
            "disk_io_curr_ticks_ms": curr_disk,
            "disk_io_ticks_delta_ms": None,
            "disk_io_dt_seconds": dt,
        }
    else:
        disk_io_value = disk_io_percent(prev_disk_ticks, curr_disk, dt)
        raw_disk = {
            "disk_io_prev_ticks_ms": prev_disk_ticks,
            "disk_io_curr_ticks_ms": curr_disk,
            "disk_io_ticks_delta_ms": curr_disk - prev_disk_ticks,
            "disk_io_dt_seconds": dt,
        }
    return {
        "cpu": cpu_percent(prev["cpu"], curr_cpu),
        "ram": ram,
        "disk_io": disk_io_value,
        "network": network_percent(prev["net_bytes"], curr_net, dt),
        "raw": raw_disk,
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
      "tenant_id": str,        OPTIONAL (Stage 10 END_USER_ENGINE foundation,
                               2026-07-29) -- the real tenant/customer this
                               task is scoped to, once a future end-user-
                               facing caller exists. Omit entirely (or pass
                               None) for Owner-side work, which is every real
                               caller today -- defaults to None, persisted
                               verbatim as umr_tasks.tenant_id (nullable), so
                               no existing caller needs to change anything.
      "correlation_id": str,   OPTIONAL (Phase 6, task-umr-tasks-utm-
                               correlation-phase6-2026-07-29, 3-monitoring-
                               stream correlation goal) -- when this task
                               originates from or relates to a real end-
                               user-facing action, the SAME identifier that
                               appears in compliance-tracker's own
                               orchestraExecutions.taskId / activityLog rows
                               (a completely separate Postgres stack -- see
                               this field's own note in metadata_json below
                               for what is and is not actually wired
                               end-to-end today). Omit entirely (or pass
                               None) for every real caller today -- persisted
                               as umr_tasks.metadata_json.correlation_id
                               (never a placeholder value; a genuinely absent
                               correlation stays absent, not "none"/"n/a").
    }
    Returns {"accepted": bool, "umr_id": str, "reason": str}. Never raises for
    a normal duplicate rejection -- that is a real, logged outcome, not an
    error.
    """
    if not (TIER_MIN <= tier <= TIER_MAX):
        raise ValueError(f"tier must be an int {TIER_MIN}..{TIER_MAX}, got {tier!r}")

    # 2026-07-29 adversarial-test fix (real, live-reproduced crash-loop bug):
    # this used to do zero shape/type validation on task_spec, so a malformed
    # spec (non-dict inputs, missing/non-string/empty task_identity, unknown
    # task_kind) would be accepted and written straight to umr_tasks as
    # status=queued -- then crash _perform_spawn() on the NEXT tick with an
    # uncaught exception BEFORE the row's status was ever updated away from
    # "queued", so next_queued_task() would re-select and re-crash on the
    # same row forever (a permanent poison-pill blocking that priority slot,
    # confirmed live via UMR-20260728-224429-01b0). Reject clearly here
    # instead, at the one place that can give the caller an actionable error;
    # _perform_spawn() below is ALSO hardened as defense-in-depth for rows
    # that predate this fix or reach the queue by any other path.
    task_identity = task_spec.get("task_identity")
    if not isinstance(task_identity, str) or not task_identity.strip():
        raise ValueError(f"task_identity must be a non-empty string, got {task_identity!r}")
    if len(task_identity) > MAX_TASK_IDENTITY_LEN:
        raise ValueError(
            f"task_identity too long ({len(task_identity)} chars, max {MAX_TASK_IDENTITY_LEN}) -- "
            f"refusing to avoid unbounded umr_tasks/FTS5 index bloat"
        )
    inputs = task_spec.get("inputs", {})
    if not isinstance(inputs, dict):
        raise ValueError(f"inputs must be a JSON object, got {type(inputs).__name__}")

    # OCID-068 seven-rule guardrails addendum, Rule 3 (UMR-20260804-180711-7f96,
    # UMR-20260804-203846-e722): "validate the input, the OCID, the task
    # identity, the database, and zero duplication in that order, and only
    # mint a UMR and write the database and create the task after every
    # validation passes." Real, previously-unvalidated gap this closes:
    # inputs.ocid_number (the optional OCID-linkage field wired into
    # insert_ocid_artifact_link() below, per UMR-20260804-170055-a069) was
    # only ever read AFTER upsert_umr_task() had already minted the row --
    # a malformed value would silently fail to link (insert_ocid_artifact_link()
    # never raises, by design) rather than being rejected up front. Validated
    # here, before any database write, same fail-fast style as
    # tenant_id/correlation_id above. Optional: every caller that omits
    # ocid_number (the overwhelming majority) is completely unaffected.
    ocid_number = inputs.get("ocid_number")
    if ocid_number is not None and (not isinstance(ocid_number, str) or not re.match(r"^OCID-\d+$", ocid_number)):
        raise ValueError(f"inputs.ocid_number must be a string matching 'OCID-<digits>', got {ocid_number!r}")

    task_kind = task_spec.get("task_kind", "systemctl_action")
    if task_kind not in ("systemctl_action", "veridian_task_create"):
        raise ValueError(
            f"task_kind must be 'systemctl_action' or 'veridian_task_create', got {task_kind!r}"
        )
    # Stage 10 (END_USER_ENGINE foundation, 2026-07-29): optional, additive --
    # every real caller today omits this key, so .get() returns None here and
    # behavior is unchanged from before this field existed.
    tenant_id = task_spec.get("tenant_id")
    if tenant_id is not None and not isinstance(tenant_id, str):
        raise ValueError(f"tenant_id must be a string or None, got {type(tenant_id).__name__}")

    # Phase 6 (task-umr-tasks-utm-correlation-phase6-2026-07-29, 3-monitoring-
    # stream correlation goal): optional, additive, same shape as tenant_id
    # above -- every real caller today omits this key too, so behavior is
    # unchanged. NOT currently populated by any real end-to-end code path --
    # see this function's docstring and metadata_json.correlation_id's own
    # note for the honest wiring status (structurally ready, not connected).
    correlation_id = task_spec.get("correlation_id")
    if correlation_id is not None and not isinstance(correlation_id, str):
        raise ValueError(f"correlation_id must be a string or None, got {type(correlation_id).__name__}")

    # Phase 7 (reuse-check-enforcement-gate, 2026-07-30): runs
    # plan_generator.check_reuse_before_dispatch() against
    # capability_registry/wiring_registry/knowledge_engine/system_index
    # BEFORE the task row is written, and records the full structured result
    # on the row itself (metadata_json.reuse_check_result), for both the
    # accepted and rejected_duplicate outcomes. This is the one, real,
    # software-enforced reuse check for this entrypoint specifically because
    # it is the lowest-level real task-creation path everything else
    # (task-gateway.py cmd_submit, directive_engine.py submit_task) funnels
    # into -- unlike those two callers, which already run their own
    # check-duplicate/search/query-knowledge/lookup-capability battery before
    # ever reaching here, a caller that constructs a task_spec and calls this
    # function directly (or via `resource_governor.py --submit`) previously
    # had no such check unless its own prompt/author happened to remember to
    # run one by hand. Advisory only, same fail-open philosophy as
    # directive_engine.py's find_in_flight_duplicate()/
    # run_check_duplicate_battery(): a low-confidence or no-match result (or
    # a broken check) never blocks the submission, it is only recorded for
    # accountability.
    intent_text = (
        inputs.get("prompt")
        or inputs.get("title")
        or inputs.get("action")
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
    metadata = {"reuse_check_result": reuse_check_result}
    if correlation_id:
        metadata["correlation_id"] = correlation_id

    # Real fix (independent review, PR #20): uses the centralized
    # _safe_superboss_register() helper (see its own docstring) rather than
    # an inline try/except, so this call site can never silently drift out
    # of sync with the other real callers again. Fail-open here, same
    # philosophy the reuse-check three lines above already applies: a
    # broken/unavailable Superboss Register must be a real, clearly-labeled
    # rejection (never a crash), never silently treated as an accepted
    # submission either.
    sbr, error = _safe_superboss_register("submit")
    if error:
        return {"accepted": False, "umr_id": None, "reason": error,
                "reuse_check_result": reuse_check_result}

    with sbr._write_lock():
        conn = sbr._connect()
        sbr._ensure_umr_table(conn)
        # Real fix (independent review, PR #20): grouped with the
        # _ensure_umr_table() call above, both at the very start of this
        # transaction, rather than later inline (its old position). Both
        # _ensure_*_table() functions call conn.commit() internally
        # (matching their own pre-existing DDL convention) -- doing that
        # once, up front, before any real row-level work in this
        # transaction, avoids an extra mid-transaction commit point that
        # the write-lock block's own single-commit-per-branch design
        # otherwise implies.
        sbr._ensure_ocid_artifact_links_table(conn)
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
                "tenant_id": tenant_id,
                "inputs": task_spec.get("inputs", {}),
                "reason": reason,
                "metadata": metadata,
            })
            conn.commit()
            conn.close()
            return {"accepted": False, "umr_id": umr_id, "reason": reason,
                     "reuse_check_result": reuse_check_result}

        # OCID-068 seven-rule guardrails addendum, Rule 6 (UMR-20260804-180711-7f96,
        # UMR-20260804-205741-cf3f): "zero duplication, before creating any
        # new UMR verify the OCID... and if a match is found return the
        # existing UMR instead of creating a duplicate." The complement of
        # the task_identity check just above: same real ACTIVE-status-only
        # scope (a genuinely CONCURRENT second UMR for an OCID that already
        # has one in flight -- see find_active_umr_by_ocid()'s own docstring
        # for why "one OCID = one UMR forever" would be a real, wrong
        # over-application, given this session's own history of many
        # legitimate sequential UMRs per OCID). Runs inside the same
        # write-lock as the task_identity check, closing the same TOCTOU
        # window. Only fires when the caller's task_spec carries an
        # ocid_number (opt-in, same as the OCID-linkage wiring below) --
        # every caller that omits it is unaffected.
        ocid_active = sbr.find_active_umr_by_ocid(conn, ocid_number) if ocid_number else None
        if ocid_active:
            reason = (
                f"duplicate submission rejected: ocid_number={ocid_number!r} already has an "
                f"active umr_id={ocid_active['umr_id']} ({ocid_active['status']}, "
                f"source_trigger={ocid_active['source_trigger']!r}, tier={ocid_active['tier']})"
            )
            umr_id = sbr.upsert_umr_task(conn, {
                "task_identity": task_identity,
                "tier": tier,
                "status": "rejected_duplicate",
                "source_trigger": source_trigger,
                "task_kind": task_spec.get("task_kind", "systemctl_action"),
                "unit_name": task_spec.get("unit_name"),
                "tenant_id": tenant_id,
                "inputs": task_spec.get("inputs", {}),
                "reason": reason,
                "metadata": metadata,
            })
            conn.commit()
            conn.close()
            return {"accepted": False, "umr_id": umr_id, "reason": reason,
                     "reuse_check_result": reuse_check_result}

        # OCID-068 seven-rule guardrails addendum, Rule 1 (real, previously
        # documented gap -- see the module comment above
        # find_active_umr_by_identity()'s own definition, and
        # UMR-20260804-180711-7f96 / UMR-20260804-194355-be9c): a retry/
        # resume/redispatch of the SAME task_identity, after its prior
        # umr_tasks row already went terminal (completed/failed/killed/
        # rejected_duplicate), used to always mint a brand-new umr_id here --
        # the real caller this fixes is dispatch-tick.py's
        # resume_interrupted_workers_tick(), which reuses task_identity=task_id
        # stably across every resume attempt for a given worker task. Reusing
        # the prior row's own umr_id (via upsert_umr_task's existing
        # ON CONFLICT(umr_id) DO UPDATE path) means a retried task keeps one
        # real, continuous UMR history rather than a fresh, disconnected one
        # per resume. Owner-dispatch callers (dispatch-owner-task.sh) mint a
        # fresh timestamp+pid task_identity per call, so this never fires for
        # them -- only a genuine identity collision (by construction, a
        # retry/resume of the same real task) reuses a umr_id.
        prior = sbr.find_most_recent_umr_by_identity(conn, task_identity)
        reused_umr_id = prior["umr_id"] if prior else None

        # Real fix (independent review, PR #26 round 1): upsert_umr_task()'s
        # existing ON CONFLICT(umr_id) DO UPDATE path unconditionally
        # overwrites outputs_json/logs_ref/metric_snapshot_json/
        # ts_dispatched/ts_sigterm/ts_completed with whatever this record
        # supplies -- a fresh submit() record supplies none of them, which
        # would silently wipe the prior terminal run's real execution
        # evidence the moment its umr_id is reused, defeating the whole
        # point of "one real, continuous UMR history." Carry the prior row's
        # own values forward here so a reuse preserves them; the resumed
        # run's own worker/supervisor overwrites them for real via its own
        # later update_umr_task() checkpoint calls as it actually progresses
        # -- this only prevents the reuse INSERT itself from clobbering them
        # with nulls before that real progress happens.
        prior_outputs = json.loads(prior["outputs_json"]) if prior and prior.get("outputs_json") else {}
        prior_metric_snapshot = json.loads(prior["metric_snapshot_json"]) if prior and prior.get("metric_snapshot_json") else None

        umr_id = sbr.upsert_umr_task(conn, {
            "umr_id": reused_umr_id,
            "task_identity": task_identity,
            "tier": tier,
            "status": "queued",
            "source_trigger": source_trigger,
            "task_kind": task_spec.get("task_kind", "systemctl_action"),
            "unit_name": task_spec.get("unit_name"),
            "tenant_id": tenant_id,
            "inputs": task_spec.get("inputs", {}),
            "reason": "queued" if not reused_umr_id else f"resubmitted (reused umr_id, prior status was {prior['status']!r})",
            "metadata": metadata,
            "outputs": prior_outputs if reused_umr_id else {},
            "logs_ref": prior["logs_ref"] if reused_umr_id else None,
            "metric_snapshot": prior_metric_snapshot if reused_umr_id else None,
            "ts_dispatched": prior["ts_dispatched"] if reused_umr_id else None,
            "ts_sigterm": prior["ts_sigterm"] if reused_umr_id else None,
            "ts_completed": prior["ts_completed"] if reused_umr_id else None,
        })
        # OCID-068 real requirement addendum (UMR-20260804-170055-a069, Owner
        # real-time implementation override on the standing hard-rule-7 lock):
        # structured OCID -> UMR linkage, recorded at this real, canonical
        # UMR-creation chokepoint -- the one place submit() actually mints a
        # genuinely new umr_id for an accepted submission. Opt-in only: fires
        # only when the caller's task_spec carries an "ocid_number" input
        # (a new, optional field -- no existing caller changes behavior by
        # omitting it). Never lets a traceability-write failure break this
        # function's own real, load-bearing UMR-creation return value --
        # insert_ocid_artifact_link() itself never raises (see its own
        # docstring), and this call site additionally never touches
        # `accepted`/`umr_id` in the return value below.
        inputs = task_spec.get("inputs", {}) or {}
        ocid_number = inputs.get("ocid_number")
        if ocid_number:
            sbr.insert_ocid_artifact_link(
                conn, ocid_number=ocid_number, umr_id=umr_id,
                repo=inputs.get("repo") or "unknown", link_kind="registration",
            )
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
    already does. Returns {"status", "unit_name", "outputs"}.

    2026-07-29 adversarial-test fix (real, live-reproduced crash-loop bug):
    the whole body used to run with no exception handling. dispatch_one()
    calls this and only writes the row's terminal status AFTER it returns --
    so an uncaught exception here (e.g. AttributeError from inputs.get() on
    a non-dict `inputs`, confirmed live via a submitted spec with
    inputs="not-a-dict") left the row's status stuck at "queued" forever,
    and next_queued_task() would re-select and re-crash the identical row on
    every subsequent tick (every 30s, permanently, once it became the
    highest-priority queued item) -- a real poison-pill. submit() now
    validates shape at the door, but this function must ALSO never let an
    exception escape, as defense-in-depth for any row that predates that
    fix or reaches the queue by another path: any failure here must resolve
    to a clean status="failed" row, never a crash that leaves "queued"
    unwritten."""
    try:
        task_kind = row["task_kind"]
        inputs = row.get("inputs_json")
        inputs = json.loads(inputs) if isinstance(inputs, str) else (inputs or {})
        if not isinstance(inputs, dict):
            return {"status": "failed", "unit_name": row.get("unit_name"),
                    "outputs": {"error": f"malformed inputs: expected object, got {type(inputs).__name__}"}}

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
            if "title" not in inputs or "prompt" not in inputs:
                return {"status": "failed", "unit_name": row.get("unit_name"),
                        "outputs": {"error": "veridian_task_create requires inputs.title and inputs.prompt"}}
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
    except Exception as e:
        return {"status": "failed", "unit_name": row.get("unit_name"),
                "outputs": {"error": f"_perform_spawn crashed: {type(e).__name__}: {e}"}}


def _recorded_new_task_ids_for_identity(task_identity, exclude_umr_id=None, limit=2):
    """Stage 5 (2026-07-29) real fix: veridian-task.py create mints a FRESH
    task_id (and therefore a fresh worker/<task_id> git branch) on every
    dispatch -- that id is generated at spawn time and is NOT derived from
    task_spec['task_identity'], so it can never be reconstructed from
    task_identity alone. Confirmed live via the real PR #58/#64/#65 incident
    (task_identity "PR58-CONFLICT" / "DIRECTIVE-002-PR58-CONFLICT", both
    UMR-20260728-123527-1d4d and UMR-20260728-175827-a017 in umr_tasks):
    the real worker branches were worker/task-20260728-160929-resolve-
    fresh-conflict-on-pr--58 (-> PR #65, still OPEN) and worker/task-
    20260729-001520-resolve-fresh-conflict-on-pr--58 -- neither is
    "worker/PR58-CONFLICT", so a guard that only ever checks
    worker/<task_identity> (the shape find_pr_for_task_identity() had before
    this fix) can never match a real branch for this task_kind, and would
    have silently let PR #65 be opened redundant to PR #64.

    The real, recoverable link is each PRIOR row's own outputs_json.
    new_task_id, written by _perform_spawn() at the moment that dispatch's
    veridian-task.py create call returned. Returns those historical task_id
    values (most recent first) for every OTHER row -- any status, this is a
    historical lookup, not a live-state one -- sharing this exact
    task_identity, so find_pr_for_task_identity() can check the REAL branch
    names a prior attempt actually created, not a name that was never real.
    Never raises -- a failed lookup here returns [] (fail open, same
    philosophy as find_pr_for_task_identity() itself: a broken check must
    never block a real, legitimate dispatch)."""
    if not task_identity:
        return []
    try:
        sbr = _superboss_register()
        conn = sbr._connect()
        rows = conn.execute(
            "SELECT umr_id, outputs_json FROM umr_tasks WHERE task_identity=? "
            "ORDER BY ts_submitted DESC LIMIT ?",
            (task_identity, limit + 1),
        ).fetchall()
        conn.close()
    except Exception:
        return []
    task_ids = []
    for row in rows:
        if exclude_umr_id and row["umr_id"] == exclude_umr_id:
            continue
        try:
            outputs = json.loads(row["outputs_json"]) if row["outputs_json"] else {}
        except (TypeError, ValueError):
            continue
        new_task_id = outputs.get("new_task_id") if isinstance(outputs, dict) else None
        if new_task_id and new_task_id not in task_ids:
            task_ids.append(new_task_id)
        if len(task_ids) >= limit:
            break
    return task_ids


def _referenced_pr_number(text):
    """Stage 6 (2026-07-29) helper: extract the FIRST 'PR #NNN' / 'PR NNN'
    reference from a task's own title (or prompt) text -- e.g. 'Resolve
    fresh conflict on PR #58' -> '58'. Returns None if no such reference is
    present or text is falsy. Never raises (a bad/odd string just yields no
    match, same fail-open philosophy as the rest of this guard)."""
    if not text:
        return None
    m = re.search(r"\bPR\s*#?\s*(\d+)\b", text, re.IGNORECASE)
    return m.group(1) if m else None


def find_pr_for_task_identity(task_identity, hint_repo=None, extra_task_ids=None, title=None):
    """Stage 4 (2026-07-29) duplicate-PR guard -- real, exact --head branch
    match against GitHub, ported from owner_backlog_orchestrator.py's
    find_pr_for_task() (2026-07-27/28 bug-fix history preserved verbatim:
    GitHub's --search is fuzzy/lagged and misses real existing PRs; a worker
    can also legitimately open its PR in the OTHER repo than its nominal
    dispatch repo -- check both, hinted repo first).

    Stage 5 (2026-07-29) real fix: this used to check ONLY worker/<task_identity>,
    which is never the real branch name veridian_task_create rows actually
    produce (see _recorded_new_task_ids_for_identity()'s docstring for the
    live-confirmed PR #58/#64/#65 evidence -- this exact guard, as originally
    written, could not have caught that real incident). extra_task_ids (from
    _recorded_new_task_ids_for_identity(), called by dispatch_one() below)
    lets the caller also check the REAL prior branch(es) this task_identity
    already produced, in addition to the original worker/<task_identity>
    check (kept for back-compat / any caller whose branch naming really does
    match task_identity directly).

    LOCK-SCOPE DECISION (documented per this stage's own spec, not left
    implicit): dispatch_one() below calls this WHILE still holding
    dispatch_core.acquire_dispatch_lock() -- i.e. inside the same critical
    section that already atomically selects the queued row and checks
    has_free_slot(). This is deliberate:

      - Releasing the lock before this network call (to let `gh` run
        unlocked) and re-acquiring it afterward to spawn would reopen the
        exact TOCTOU race dispatch_one()'s own docstring already closed once
        (two concurrent callers picking the SAME queued row before either
        claims it) -- between release and re-acquire, a second caller could
        pick up and dispatch the very same row this call is still deciding
        on. A guard against duplicate PRs must not itself reintroduce a
        duplicate-dispatch race to do its job.
      - The blast radius of holding the lock across a network call is bounded
        three separate ways so this does not become a new stall for the 5
        real in-flight tasks (PR617-REVIEW, PR618-REVIEW, PR58-CONFLICT,
        PR610-CONFLICT, PHASE-2-CROSSREF) or anything else sharing this
        server-wide lock via the other consolidated tick scripts:
          1. Callers only invoke this for task_kind=='veridian_task_create'
             rows -- systemctl_action rows (restarts, watchdog recoveries --
             the majority of real governor dispatches per this module's own
             design doc) never open PRs and skip the check entirely.
          2. Each `gh pr list` call carries a hard subprocess timeout
             (GH_PR_CHECK_TIMEOUT_SECONDS, default 8s) instead of an
             unbounded one. Stage 5 (2026-07-29) added up to
             _recorded_new_task_ids_for_identity()'s `limit` (default 2)
             extra real prior-branch candidates per task_identity, checked
             alongside the original worker/<task_identity> guess -- worst
             case is now (1 + limit) candidate branches x 2 repos x
             GH_PR_CHECK_TIMEOUT_SECONDS, i.e. ~48s at defaults, not the
             original ~16s. Deliberately kept small (limit=2, not the whole
             history) precisely to keep this bounded: the real PR #58/#64/#65
             incident only ever needed the single most recent prior branch to
             catch it, and each check still returns immediately on the first
             match/success in the common case -- this is a worst-case ceiling
             on repeated gh timeouts, not the normal-path cost.
          3. On timeout or any gh/network error this fails OPEN -- returns
             "no duplicate found" and logs to ATTENTION.md -- rather than
             fail-closed. A GitHub outage or rate-limit must degrade to
             "old behavior, no guard, dispatch proceeds" for a few
             dispatches; it must never degrade to "queue permanently
             wedged", since correctness here (closing the duplicate-PR
             redispatch bug) is not worth trading for a new, different
             class of stall.

    Stage 6 (2026-07-29) real fix: closes a SECOND, separate real gap the same
    PR #58/#64/#65/#66 incident exposed -- the same underlying PR-58
    conflict-resolution work was submitted under two DIFFERENT task_identity
    strings entirely ("DIRECTIVE-002-PR58-CONFLICT" for PR #64's row, plain
    "PR58-CONFLICT" for PR #65/#66's rows -- confirmed live in umr_tasks:
    UMR-20260728-122213-ff96 vs UMR-20260728-123527-1d4d /
    UMR-20260728-175827-a017). Stage 5's extra_task_ids only ever correlates
    rows sharing the EXACT SAME task_identity, so it could never have caught
    PR #64 as a duplicate of #65/#66 -- a Stage 4/5-only guard still misses
    this half of the real incident. The one thing both rows' dispatch inputs
    DID share is plain title text naming the same real PR being fixed
    ("...PR #58..."). If a PR number can be extracted from this task's own
    title (see _referenced_pr_number()), do one further bounded gh call per
    repo (same GH_PR_CHECK_TIMEOUT_SECONDS, same fail-open-on-error/timeout
    semantics as the branch-based checks above) and check whether any
    existing OPEN/MERGED PR's title already references that same PR number --
    a low-false-positive signal in practice: unrelated PRs essentially never
    reference the exact same "PR #NNN" substring by coincidence.

    Returns (pr_number, repo) if an OPEN or MERGED PR already exists for
    worker/<task_identity>, worker/<any of extra_task_ids>, OR (when `title`
    is given) any existing PR whose own title references the same PR number
    as this task's title; (None, None) if none found (including on any
    failure -- fail open, see above)."""
    if not task_identity:
        return None, None
    candidate_idents = [task_identity] + [t for t in (extra_task_ids or []) if t and t != task_identity]
    if hint_repo and hint_repo in GH_PR_CHECK_REPOS:
        repos = [hint_repo] + [r for r in GH_PR_CHECK_REPOS if r != hint_repo]
    elif hint_repo:
        repos = [hint_repo] + list(GH_PR_CHECK_REPOS)
    else:
        repos = list(GH_PR_CHECK_REPOS)
    for repo in repos:
        for ident in candidate_idents:
            branch = f"worker/{ident}"
            try:
                r = _run(
                    ["gh", "pr", "list", "--repo", f"{GH_ORG}/{repo}", "--state", "all",
                     "--head", branch, "--json", "number,state", "--limit", "3"],
                    timeout=GH_PR_CHECK_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                _append_attention(
                    f"WARNING: Stage 4/5 duplicate-PR guard timed out checking {GH_ORG}/{repo} for branch "
                    f"{branch} (>{GH_PR_CHECK_TIMEOUT_SECONDS}s) -- failing open, dispatch proceeding "
                    f"WITHOUT the duplicate-PR check against this repo/branch for this row."
                )
                continue
            if r.returncode != 0:
                continue  # fail open on any gh error (auth hiccup, rate limit, transient API failure, ...)
            try:
                prs = json.loads(r.stdout)
            except (json.JSONDecodeError, ValueError):
                prs = []
            if prs:
                return prs[0]["number"], repo

    # Stage 6 (2026-07-29): see this function's docstring for the real
    # PR #64/#65/#66 evidence this closes -- a task_identity-fragmented
    # duplicate (different task_identity string, same real PR referenced in
    # the title) that no branch-name-based check above can ever catch.
    pr_num = _referenced_pr_number(title)
    if pr_num:
        for repo in repos:
            try:
                r = _run(
                    ["gh", "pr", "list", "--repo", f"{GH_ORG}/{repo}", "--state", "all",
                     "--json", "number,title", "--limit", "50"],
                    timeout=GH_PR_CHECK_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                _append_attention(
                    f"WARNING: Stage 6 title-reference duplicate-PR guard timed out checking "
                    f"{GH_ORG}/{repo} (>{GH_PR_CHECK_TIMEOUT_SECONDS}s) -- failing open, dispatch "
                    f"proceeding WITHOUT this extra check against this repo for this row."
                )
                continue
            if r.returncode != 0:
                continue  # fail open on any gh error, same as the branch-based checks above
            try:
                prs = json.loads(r.stdout)
            except (json.JSONDecodeError, ValueError):
                prs = []
            for pr in prs:
                if _referenced_pr_number(pr.get("title") or "") == pr_num:
                    return pr["number"], repo
    return None, None


# OCID-068 seven-rule guardrails addendum, Rule 2 (UMR-20260804-180711-7f96,
# UMR-20260804-203846-e722, UMR-20260804-170055-a069): "every dispatch shall
# return exactly one of five allowed results, success, failed, blocked,
# rejected, or cancelled... on failure the dispatch must return a real error
# id, a real root cause, real evidence, and a real next action." dispatch_one()
# already has a real, richer internal "action" vocabulary (idle, deferred,
# frozen, dispatched, rejected_duplicate_pr, ...) that other real code
# (dispatch-tick.py, run_tick()'s own loop-stop condition, existing tests)
# depends on -- this constant maps that existing vocabulary onto Rule 2's
# canonical 5-value outcome, additively, without changing dispatch_one()'s
# existing "action"/"result" shape for any current caller.
RULE2_OUTCOME_MAP = {
    "emergency_stopped": "blocked",
    "frozen": "blocked",
    "superboss_unavailable": "blocked",
    "deferred": "blocked",
    "rejected_duplicate_pr": "rejected",
    # UMR-20260804-213847-4b56: the real OCID-evidence supersession check,
    # same real "rejected" outcome as rejected_duplicate_pr above -- a
    # deliberate, evidence-based skip, not a failure.
    "superseded_by_ocid_evidence": "rejected",
}


def classify_dispatch_outcome(dispatch_result):
    """Rule 2's real classification, applied to dispatch_one()'s own return
    dict. Two of dispatch_one()'s real actions -- "idle" (queue empty, no
    task was ever selected) and "would_dispatch" (an explicit --dry-run
    preview) -- are not a real dispatch ATTEMPT at all, so Rule 2's "every
    dispatch shall return..." does not apply to them by construction; this
    function returns outcome=None for both rather than forcing a fabricated
    value into the 5-enum. Every other action maps to exactly one of
    success/failed/blocked/rejected. "cancelled" is real, but is emitted by
    scan_stuck_tasks()'s own real SIGKILL path (status="killed"), a distinct
    real lifecycle event from dispatch_one()'s own dispatch attempts -- out
    of this function's scope, not fabricated here.

    Returns {"outcome": str|None, "error_id": str|None, "root_cause": str|None,
    "evidence": str|None, "next_action": str|None} -- the four extra fields
    are populated (never left silently empty) whenever outcome is anything
    other than "success"/None, per Rule 2's own explicit requirement."""
    action = dispatch_result.get("action")
    umr_id = dispatch_result.get("umr_id")

    if action in ("idle", "would_dispatch"):
        return {"outcome": None, "error_id": None, "root_cause": None, "evidence": None, "next_action": None}

    if action == "dispatched":
        spawn_result = dispatch_result.get("result") or {}
        if spawn_result.get("status") == "running":
            return {"outcome": "success", "error_id": None, "root_cause": None, "evidence": None, "next_action": None}
        # _perform_spawn() always resolves failures to status="failed" with a
        # real outputs.error/outputs.stderr -- never an empty/silent failure
        # (see its own docstring's "no exception ever escapes" contract).
        outputs = spawn_result.get("outputs") or {}
        root_cause = outputs.get("error") or outputs.get("stderr") or "unknown spawn failure (no error detail captured)"
        return {
            "outcome": "failed",
            "error_id": f"DISPATCH-FAILED-{umr_id}" if umr_id else "DISPATCH-FAILED-UNKNOWN-UMR",
            "root_cause": root_cause,
            "evidence": json.dumps(outputs),
            "next_action": "inspect outputs_json on this umr_id's umr_tasks row; retry via resume_interrupted_workers_tick() once the underlying cause is fixed",
        }

    if action in RULE2_OUTCOME_MAP:
        outcome = RULE2_OUTCOME_MAP[action]
        detail = dispatch_result.get("detail") or "no detail captured"
        next_actions = {
            "blocked": "re-run dispatch_one() on the next tick; this is a real, expected transient block (metric threshold, concurrency cap, EMERGENCY_STOP, or Superboss Register unavailability), not a task-specific failure",
            "rejected": "no action -- real evidence (an existing PR for this task_identity, or newer ocid_artifact_links evidence for this task's own OCID) shows this row's work is already done; intentionally terminal",
        }
        return {
            "outcome": outcome,
            "error_id": f"DISPATCH-{outcome.upper()}-{action.upper()}",
            "root_cause": detail,
            "evidence": json.dumps({k: v for k, v in dispatch_result.items() if k != "metrics"}),
            "next_action": next_actions.get(outcome, "no defined next action for this outcome"),
        }

    # Defensive: an action this function doesn't recognize (e.g. a future
    # addition) must never silently produce an empty/missing classification --
    # Rule 2 explicitly forbids that. Surfaced as "failed" with a real,
    # honest root_cause naming the gap, rather than guessed at.
    return {
        "outcome": "failed",
        "error_id": f"DISPATCH-UNCLASSIFIED-ACTION-{action}",
        "root_cause": f"classify_dispatch_outcome() has no mapping for dispatch_one() action={action!r} -- "
                       f"this is a real gap in this function's own coverage, not a task failure",
        "evidence": json.dumps({k: v for k, v in dispatch_result.items() if k != "metrics"}),
        "next_action": "add this action to RULE2_OUTCOME_MAP (or the idle/would_dispatch exclusion) in resource_governor.py",
    }


def dispatch_one(dry_run=False, now=None):
    """Real, thin Rule 2 wrapper (OCID-068 seven-rule guardrails addendum,
    UMR-20260804-180711-7f96, UMR-20260804-203846-e722): calls
    _dispatch_one_inner() (the actual, unchanged dispatch logic -- see its
    own docstring) and merges classify_dispatch_outcome()'s real
    outcome/error_id/root_cause/evidence/next_action fields into the
    returned dict before returning, so every real dispatch_one() call now
    genuinely carries Rule 2's required classification alongside the
    existing "action"/"result" shape every current caller (dispatch-tick.py,
    run_tick(), existing tests) already depends on -- purely additive, no
    existing key removed or renamed."""
    result = _dispatch_one_inner(dry_run=dry_run, now=now)
    result.update(classify_dispatch_outcome(result))
    return result


def _dispatch_one_inner(dry_run=False, now=None):
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
    _record_emergency_tick(over, metrics=metrics)
    if over:
        return {"action": "frozen", "detail": f"metric(s) at/over {METRIC_THRESHOLD_PERCENT}%: {over}",
                "metrics": metrics}

    dc = _dispatch_core()
    # Real fix (independent review round 2, PR #20): see
    # _safe_superboss_register()'s own docstring. Matches this function's
    # own documented contract ("Never raises for a normal 'nothing to
    # do'/'frozen' outcome") -- a broken/unavailable Superboss Register is
    # now the same kind of real, non-raising outcome as "frozen"/"idle",
    # not an uncaught crash of the whole dispatch loop.
    sbr, error = _safe_superboss_register("dispatch_one")
    if error:
        return {"action": "superboss_unavailable", "detail": error, "metrics": metrics}

    with dc.acquire_dispatch_lock():
        conn = sbr._connect()
        sbr._ensure_umr_table(conn)
        sbr._ensure_ocid_artifact_links_table(conn)
        row = next_queued_task(conn, now=now)
        if row is None:
            conn.close()
            return {"action": "idle", "detail": "queue empty", "metrics": metrics}

        slot_ok, slot_detail = dc.has_free_slot_detail()
        if not slot_ok:
            conn.close()
            # Real fix (UMR-20260806-101839-688e): the old fixed detail
            # string ("no free concurrency slot under dispatch_core's shared
            # cap") was printed for EVERY has_free_slot() failure, including
            # a resource-headroom veto (e.g. real load average over
            # threshold) with running_worker_count() at 0/5 -- live-
            # reproduced on this box: real tick log showed exactly that
            # misleading string every single tick while 5 real slots sat
            # idle. slot_detail now names the real check that actually
            # failed (cap_exhausted / mem_backoff / swap_backoff /
            # load1_backoff / mem_hard_ceiling / swap_hard_ceiling /
            # mem_headroom_budget / load1_unreadable) plus the real numbers,
            # so this is diagnosable from the tick log alone going forward.
            return {"action": "deferred",
                     "detail": f"no free dispatch slot -- real gate: {slot_detail}",
                     "slot_detail": slot_detail,
                     "umr_id": row["umr_id"], "metrics": metrics}

        if dry_run:
            conn.close()
            return {"action": "would_dispatch", "umr_id": row["umr_id"], "metrics": metrics}

        # Real root-cause fix (UMR-20260804-213847-4b56, citing
        # UMR-20260804-180711-7f96): dispatch-owner-task.sh's own real,
        # by-design dual dispatch (relay the same instruction into the live
        # interactive tmux session AND submit a real veridian_task_create
        # task into this same governed queue, in the SAME call -- see that
        # script's own header comment) means every real Owner/PM
        # instruction ends up on two real, independent channels at once.
        # This is deliberate (laptop-independence: the queued twin still
        # runs even if the interactive session goes away) -- the real,
        # previously-missing piece is a way for THIS channel to notice the
        # OTHER channel already finished the same real work while this
        # task sat queued (confirmed live, 2026-08-04: real
        # anti-starvation aging plus real concurrency backpressure from
        # this session's own heavy interactive slot usage left several
        # owner-dispatch tasks queued 27-74 real minutes before reaching
        # this point, by which time the same UMR's work had already been
        # completed and merged via the interactive channel -- not a retry
        # loop, not a duplicate cron/notification, the same original
        # queued row simply reaching the front of a real, working-as-
        # designed priority queue late). Deterministic, evidence-based,
        # never heuristic/semantic: if this task's own title names a real
        # OCID, and ocid_artifact_links (the real OCID<->UMR<->PR/commit
        # registry OCID-068 itself built) already has a real link for that
        # OCID created AFTER this row's own ts_submitted, that is real,
        # direct evidence the same OCID's work was independently completed
        # while this task waited -- skip the redundant spawn rather than
        # duplicate real, already-finished work.
        if row["task_kind"] == "veridian_task_create":
            raw_inputs_for_ocid = row.get("inputs_json")
            row_inputs_for_ocid = (
                json.loads(raw_inputs_for_ocid) if isinstance(raw_inputs_for_ocid, str)
                else (raw_inputs_for_ocid or {})
            )
            title = row_inputs_for_ocid.get("title") or ""
            ocid_match = re.search(r"OCID-0*(\d+)", title, re.IGNORECASE)
            if ocid_match:
                ocid_number = f"OCID-{int(ocid_match.group(1)):03d}"
                newer_links = [
                    link for link in sbr.query_ocid_artifact_links(conn, ocid_number=ocid_number)
                    if (link.get("created_at") or "") > (row["ts_submitted"] or "")
                ]
                if newer_links:
                    newest = newer_links[0]
                    reason = (
                        f"superseded: {ocid_number} (extracted from this task's own title {title!r}) "
                        f"already has real, newer evidence in ocid_artifact_links -- umr_id="
                        f"{newest['umr_id']!r}, repo={newest['repo']!r}, pr_number={newest.get('pr_number')!r}, "
                        f"commit_sha={newest.get('commit_sha')!r}, link_kind={newest['link_kind']!r}, "
                        f"created_at={newest['created_at']!r} (after this task's own ts_submitted="
                        f"{row['ts_submitted']!r}) -- the same OCID's real work was independently "
                        f"completed while this task sat queued; redispatch skipped, not spawned"
                    )
                    with sbr._write_lock():
                        sbr.update_umr_task(conn, row["umr_id"], status="rejected_duplicate",
                                             ts_completed=_now_iso(), reason=reason)
                        conn.commit()
                    conn.close()
                    _append_attention(
                        f"INFO: dispatch_one() skipped a real, redundant veridian_task_create "
                        f"spawn for umr_id={row['umr_id']!r} (task_identity={row['task_identity']!r}): "
                        f"{reason}"
                    )
                    return {"action": "superseded_by_ocid_evidence", "umr_id": row["umr_id"],
                             "detail": reason, "ocid_number": ocid_number, "metrics": metrics}

        # Stage 4 (2026-07-29): duplicate-PR guard. See find_pr_for_task_identity()'s
        # docstring for the lock-scope reasoning. Only veridian_task_create rows can
        # ever have an associated PR -- systemctl_action rows skip this entirely.
        # Stage 5 (2026-07-29) real fix: the original Stage 4 call below only ever
        # checked worker/<task_identity>, which is NEVER the real branch name
        # veridian-task.py actually creates (it mints a fresh task_id per dispatch --
        # see _recorded_new_task_ids_for_identity()'s docstring). Confirmed live: this
        # exact gap is why the real PR #58/#64/#65 incident happened even though this
        # guard already existed -- worker/PR58-CONFLICT was never a real branch, so the
        # check always found nothing and let the redundant dispatch through. Passing
        # the real prior branch(es) recorded on this task_identity's own past rows
        # closes that gap without touching the check's fail-open semantics.
        if row["task_kind"] == "veridian_task_create":
            raw_inputs = row.get("inputs_json")
            row_inputs = json.loads(raw_inputs) if isinstance(raw_inputs, str) else (raw_inputs or {})
            prior_task_ids = _recorded_new_task_ids_for_identity(
                row["task_identity"], exclude_umr_id=row["umr_id"])
            dup_pr, dup_repo = find_pr_for_task_identity(
                row["task_identity"], row_inputs.get("repo"), extra_task_ids=prior_task_ids,
                title=row_inputs.get("title"))
            if dup_pr is not None:
                reason = (
                    f"duplicate-PR guard (Stage 4/5/6): existing PR {GH_ORG}/{dup_repo}#{dup_pr} already "
                    f"open/merged for task_identity={row['task_identity']!r} "
                    f"(checked worker/{row['task_identity']}, prior real branch(es) "
                    f"{[f'worker/{t}' for t in prior_task_ids]}, and any existing PR title referencing "
                    f"the same PR number as this task's own title) -- redispatch skipped, not spawned"
                )
                with sbr._write_lock():
                    sbr.update_umr_task(conn, row["umr_id"], status="rejected_duplicate",
                                         ts_completed=_now_iso(), reason=reason)
                    conn.commit()
                conn.close()
                return {"action": "rejected_duplicate_pr", "umr_id": row["umr_id"], "detail": reason,
                         "pr": {"repo": dup_repo, "number": dup_pr}, "metrics": metrics}

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


STALE_QUEUED_AGGREGATE_TITLE_PREFIX = "STALE-QUEUED-AGGREGATE:"


def flag_stale_queued_tasks(now=None):
    """Real max-queued-age safeguard -- see the MAX_QUEUED_AGE_SECONDS module
    comment above for the full real incident this closes.

    Deliberately generic and deterministic, zero AI judgment: does not try to
    diagnose WHY a row is stale, only measures real age against a real,
    documented, bounded threshold (unchanged by the aggregation fix below --
    the 4h MAX_QUEUED_AGE_SECONDS default and the underlying detection are
    real and valuable, and are not being weakened here).

    Real emission-shape fix (UMR-20260806-163738-4323, governing
    UMR-20260806-071025-1d28, superseding the one-row-per-umr_id shape this
    function originally shipped with under UMR-20260806-090229-f2a7): that
    original shape opened one new real pm_decisions_pending row per stale
    umr_id, which meant 48 of 118 real open decision rows (~41%) measured at
    investigation time were the identical STALE-QUEUED condition repeated --
    Section 7 of the standing 10-minute PM report (generate_pm_report_v3.py)
    lists every open decision, and at that ratio the section stopped
    supporting a real decision at all, just something to skim past (same
    "always-on signal carries no information" failure class independently
    found in COLLISION_DETECTED -- investigated separately; that one's real
    root cause turned out to be a genuinely different mechanism, a real
    pairwise citation/file-overlap count across many concurrently open PRs,
    not a duplicate-row-per-instance emission bug, so it is deliberately left
    unchanged here). This function now keeps exactly ONE real open
    'STALE-QUEUED-AGGREGATE:' pm_decisions_pending row representing the
    condition as a whole, carrying the real current count and the real full
    list of currently-affected umr_id values in its detail, updated IN PLACE
    (superboss-register.py's own new update_pm_decision_pending()) as the
    real count changes on each call -- never a raw UPDATE/DELETE against
    umr_tasks or against pm_decisions_pending outside superboss-register.py,
    and this function still never itself resolves/closes the row for a real
    PM decision to hold/investigate/intervene; only a real PM decision
    (resolve_pm_decision_pending()) closes that. The one exception is the
    condition genuinely clearing (zero real stale rows left): this function
    then resolves its own aggregate row itself with an honest closed_note,
    the same way a monitoring check clearing its own alert would, rather
    than leaving a "48 stale" row open forever once nothing is actually
    stale -- this is not a suppression of detection, the row still reopens
    the moment a umr_task next crosses the real threshold.

    The 48 real pre-existing per-umr_id rows this shape superseded were
    resolved once, out of band from this function, as status='superseded'
    (never deleted -- see PROGRESS.md / the governing UMR's evidence for that
    one-time migration), each citing this aggregate row's real id.

    Idempotent by construction: at most one real open
    'STALE-QUEUED-AGGREGATE:' row can ever exist (this function is the only
    writer of that title prefix), so calling this every real tick only ever
    updates that same row's real title/detail, or inserts it once if
    genuinely absent, or resolves it once the real condition clears -- same
    "safe to call every real tick" property scan_stuck_tasks() below has.

    Returns the real, current, full list of umr_id values that are stale as
    of THIS call (not just newly-stale ones -- the aggregate shape has no
    "newly" concept the way one-row-per-umr_id did; run_tick()'s
    stale_queued_flagged key is documentation/observability only, nothing
    downstream branches on new-vs-still-stale). Fails open/silent (empty
    list) if Superboss Register is unavailable -- a broken check here must
    never crash the real dispatch tick that calls this, same philosophy as
    scan_stuck_tasks() below."""
    now = now or _utcnow()
    sbr, error = _safe_superboss_register("flag_stale_queued_tasks")
    if error:
        return []
    conn = sbr._connect()
    sbr._ensure_umr_table(conn)
    sbr._ensure_pm_decisions_pending_table(conn)

    stale = []
    for row in conn.execute("SELECT * FROM umr_tasks WHERE status='queued'").fetchall():
        row = dict(row)
        ts_submitted = row["ts_submitted"]
        if isinstance(ts_submitted, str):
            ts_submitted = datetime.fromisoformat(ts_submitted)
        age_seconds = max(0.0, (now - ts_submitted).total_seconds())
        if age_seconds >= MAX_QUEUED_AGE_SECONDS:
            stale.append((row, age_seconds))
    # Oldest/most-stale first -- a real, deterministic, documented ordering
    # rule for the detail list below, not an AI judgment call.
    stale.sort(key=lambda pair: pair[1], reverse=True)

    threshold_hours = MAX_QUEUED_AGE_SECONDS / 3600.0
    open_aggregates = conn.execute(
        "SELECT id FROM pm_decisions_pending WHERE status='open' "
        "AND title LIKE ? ORDER BY id",
        (STALE_QUEUED_AGGREGATE_TITLE_PREFIX + "%",),
    ).fetchall()

    if not stale:
        # Real condition cleared -- resolve any open aggregate row honestly
        # rather than leaving a stale count open forever. Not a weakening of
        # detection: the moment a real umr_task next crosses the threshold,
        # a fresh aggregate row opens again exactly as below.
        if open_aggregates:
            with sbr._write_lock():
                for row in open_aggregates:
                    sbr.resolve_pm_decision_pending(
                        conn, row["id"], closed_by="resource_governor:flag_stale_queued_tasks",
                        closed_note="real stale-queued count returned to 0 -- condition cleared",
                        status="resolved",
                    )
                conn.commit()
        conn.close()
        return []

    umr_ids = [row["umr_id"] for row, _ in stale]
    affected_lines = [
        f"- {row['umr_id']}: queued {age_seconds / 3600.0:.1f}h "
        f"(task_identity={row['task_identity']!r} tier={row['tier']} "
        f"source_trigger={row['source_trigger']!r} ts_submitted={row['ts_submitted']!r} "
        f"reason={row.get('reason')!r} unit_name={row.get('unit_name')!r})"
        for row, age_seconds in stale
    ]
    title = (
        f"{STALE_QUEUED_AGGREGATE_TITLE_PREFIX} {len(stale)} real umr_tasks rows queued "
        f"past {threshold_hours:.1f}h safeguard"
    )
    detail = (
        f"Real, deterministic max-queued-age safeguard, aggregated (resource_governor.py "
        f"flag_stale_queued_tasks(), UMR-20260806-163738-4323, superseding the prior "
        f"one-row-per-umr_id shape opened under UMR-20260806-090229-f2a7): {len(stale)} real "
        f"umr_tasks rows have been status='queued' for at least {threshold_hours:.1f}h as of "
        f"{now.isoformat()}. None of these rows have reached a real terminal status "
        f"(completed/failed/killed/rejected_duplicate) within {threshold_hours:.1f}h of their "
        f"real ts_submitted. Zero AI judgment applied here -- a real PM decision is needed on "
        f"whether to hold, investigate, or manually intervene on the dispatcher's queued-work "
        f"drain rate. This single row is kept updated in place as the real count changes -- it "
        f"is not re-opened per occurrence.\n\n"
        f"Real affected umr_id list ({len(stale)}):\n" + "\n".join(affected_lines)
    )

    with sbr._write_lock():
        if open_aggregates:
            keep_id = open_aggregates[0]["id"]
            sbr.update_pm_decision_pending(conn, keep_id, title=title, detail=detail)
            # Defensive only -- this function is the sole writer of this
            # title prefix, so more than one open aggregate row should never
            # happen, but real defensive coding costs nothing: resolve any
            # extra as superseded by the one real row being kept.
            for extra in open_aggregates[1:]:
                sbr.resolve_pm_decision_pending(
                    conn, extra["id"], closed_by="resource_governor:flag_stale_queued_tasks",
                    closed_note=f"real duplicate open aggregate row -- superseded by id={keep_id}",
                    status="superseded",
                )
        else:
            sbr.insert_pm_decision_pending(
                conn, title, detail, related_umr=None,
                recommended_option="investigate real dispatcher queued-work drain rate",
            )
        conn.commit()
    conn.close()
    return umr_ids


# Real fix (UMR-20260806-101839-688e, dispatch-throughput-stall follow-up):
# run_tick()'s dispatch loop used to stop the ENTIRE tick after the first
# dispatch_one() call whose action wasn't literally "dispatched" -- but two
# of those non-"dispatched" actions (REJECTED_DUPLICATE_PR_ACTIONS,
# ROW_RESOLVED_NON_DISPATCH_ACTIONS below) already write a real TERMINAL
# status on the row that produced them (see rejected_duplicate_pr /
# superseded_by_ocid_evidence handling in _dispatch_one_inner) -- that row
# is no longer 'queued', so next_queued_task() would pick a genuinely
# DIFFERENT row on the next call. Stopping the tick there wasted real,
# available dispatch capacity every time the top-ranked row happened to be
# a duplicate/superseded one: the other real queued rows never even got a
# next_queued_task() lookup that tick. Only a real row-INDEPENDENT block
# (frozen/deferred/emergency_stopped/superboss_unavailable) or an empty
# queue (idle) should stop the loop -- retrying those mid-tick cannot
# succeed since nothing about which row was picked changes the outcome.
ROW_RESOLVED_NON_DISPATCH_ACTIONS = frozenset({
    "dispatched", "rejected_duplicate_pr", "superseded_by_ocid_evidence",
})


def run_tick(max_dispatches=None, now=None):
    """One full governor pass: stuck-task scan, stale-queued-age safeguard,
    then priority-ordered dispatch until the queue is empty, a slot/metric
    limit stops it, or max_dispatches is reached.

    The loop keeps going past any outcome that already resolved the picked
    row to a real terminal (non-'queued') status -- see
    ROW_RESOLVED_NON_DISPATCH_ACTIONS's docstring above -- and only stops on
    a genuinely row-independent block or an empty queue."""
    results = {
        "stuck_task_actions": scan_stuck_tasks(now=now),
        "stale_queued_flagged": flag_stale_queued_tasks(now=now),
        "dispatches": [],
    }
    while max_dispatches is None or len(results["dispatches"]) < max_dispatches:
        r = dispatch_one(now=now)
        results["dispatches"].append(r)
        if r["action"] not in ROW_RESOLVED_NON_DISPATCH_ACTIONS:
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
    # Real fix (independent review round 2, PR #20): see
    # _safe_superboss_register()'s own docstring. A broken/unavailable
    # Superboss Register must never crash this function's own real
    # SIGTERM/SIGKILL stuck-task safety net -- it returns the same real,
    # empty "nothing done this call" shape this function's own docstring
    # already documents for the ordinary no-stuck-tasks case, while still
    # surfacing the real failure via ATTENTION.md (never silently
    # indistinguishable from "genuinely nothing was stuck").
    sbr, error = _safe_superboss_register("scan_stuck_tasks")
    if error:
        return []
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
            # Real fix, 2026-07-29 (zombie-worker incident, same root cause as
            # reconcile_stale_heartbeats' equivalent fix above): kill alone
            # leaves the enable-symlink behind, letting a systemd --user
            # manager restart resurrect this exact unit later regardless of
            # its now-terminal umr_tasks status.
            _run(["systemctl", "--user", "disable", row["unit_name"]])
            with sbr._write_lock():
                sbr.update_umr_task(conn, row["umr_id"], status="killed", ts_completed=_now_iso())
                conn.commit()
            actions.append({"umr_id": row["umr_id"], "unit_name": row["unit_name"], "action": "SIGKILL",
                             "since_sigterm_s": since_sigterm})

    conn.close()
    return actions


# ---------------------------------------------------------------------------
# Heartbeat reconciliation sweep (Stage 3, 2026-07-29)
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


def reconcile_stale_heartbeats(now=None, ttl_seconds=None, execute=False):
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

    EXECUTE GATE (real fix, UMR-20260806-141429-f447 / proposal 88 priority
    one): unlike --backfill-null-heartbeats (backfill_null_heartbeats(),
    below), this function used to have NO execute gate at all -- it wrote/
    committed the real terminal status the instant it found any stale
    non-NULL last_heartbeat row, with no dry-run path and no caller-visible
    way to preview first. That every umr_tasks row happened to have a NULL
    last_heartbeat (so nothing was ever actually written) was an empirical
    accident of this box's current data, never a structural safety property
    of this function -- the very first row with a real, aged last_heartbeat
    would have been written to unconditionally. `execute` now mirrors
    backfill_null_heartbeats()'s own convention exactly: False (the default)
    is a real read-only dry run -- every stale-and-inactive row is still
    found and reported (decision 'would_reconcile'), but `systemctl --user
    disable` and the umr_tasks write are both skipped -- and only
    execute=True performs the real disable + write. A dry run's report is
    therefore always a true preview of what execute=True would do, never a
    different code path. Callers (this module's own CLI --reconcile-stale,
    resource_governor_tick_loop.sh) must pass execute=True/--execute
    explicitly to keep applying real writes; until they do, this sweep is
    report-only.

    Returns the list of rows examined this call, each tagged with a real
    'decision' of 'would_reconcile' (dry run) or 'reconciled' (execute=True)
    -- empty if none were stale-and-inactive, which is the expected/normal
    steady-state result."""
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
        terminal = _unit_exit_terminal_status(unit)
        if not execute:
            actions.append({"umr_id": row["umr_id"], "unit_name": unit,
                             "reconciled_to": terminal, "decision": "would_reconcile"})
            continue
        # Real fix, 2026-07-29 (zombie-worker incident): a unit's real process
        # exiting does NOT remove its default.target.wants/ enable-symlink --
        # only `disable` does. Without this, a systemd --user manager restart
        # (confirmed live, 05:53:27 UTC) resurrects every unit ever enabled,
        # regardless of its real umr_tasks status, silently burning real CPU
        # re-running already-finished/killed work. Safe here specifically
        # because is_active is already confirmed False above -- disable never
        # touches a running unit's live state, only its boot-time wiring.
        _run(["systemctl", "--user", "disable", unit])
        with sbr._write_lock():
            sbr.update_umr_task(
                conn, row["umr_id"], status=terminal, ts_completed=_now_iso(),
                reason=(f"reconciled by heartbeat sweep: unit {unit} inactive, last_heartbeat "
                        f"stale (>{ttl}s), real exit status={terminal}"),
            )
            conn.commit()
        actions.append({"umr_id": row["umr_id"], "unit_name": unit,
                         "reconciled_to": terminal, "decision": "reconciled"})
    conn.close()
    return actions


# ---------------------------------------------------------------------------
# One-time NULL-heartbeat backfill (Stage 1, 2026-07-29)
# ---------------------------------------------------------------------------
# reconcile_stale_heartbeats() above is structurally blind to any row whose
# last_heartbeat has ALWAYS been NULL -- its own WHERE clause requires
# last_heartbeat < cutoff, which by construction excludes NULL. Two real
# classes of row can never age out of NULL on their own: rows written before
# the last_heartbeat column/instrumentation existed, and rows for task types
# that never heartbeat at all (external_ai_state_machine.py-backed sessions,
# which checkpoint via their own SQLite table, not umr_tasks.last_heartbeat).
# This is a separate, one-time, explicitly-invoked backfill -- NOT wired into
# run_tick() or the periodic tick loop, and it does not alter
# reconcile_stale_heartbeats() itself in any way.

EXTERNAL_AI_STATE_MACHINE_SCRIPT = os.path.join(SCRIPTS, "external_ai_state_machine.py")
# Real Owner email this one-time backfill's external-session ground-truth
# lookup is scoped to (external_ai_state_machine.py sessions are stored
# per-user by email hash) -- overridable for tests only.
BACKFILL_OWNER_EMAIL = os.environ.get("VERIDIAN_GOVERNOR_BACKFILL_EMAIL", "raajat.agarwal@gmail.com")


def _external_ai_list_sessions(email):
    """Real, read-only call into external_ai_state_machine.py's own
    `list-sessions` command -- the ground truth for external_ai_state_machine.py
    -backed rows (unit_name IS NULL, so systemctl has nothing to check against).
    Returns the parsed `sessions` list on success, or None on any failure (a
    missing/non-zero-exit call, or unparseable output) -- callers must treat
    None as 'cannot verify, leave every unmatched row alone', never as 'zero
    sessions exist'."""
    r = _run(["python3", EXTERNAL_AI_STATE_MACHINE_SCRIPT, "list-sessions", "--email", email])
    if r.returncode != 0:
        return None
    try:
        sessions = json.loads(r.stdout).get("sessions")
    except (json.JSONDecodeError, ValueError):
        return None
    return sessions if isinstance(sessions, list) else None


def _external_ai_mark_complete(session_id):
    """Real call into external_ai_state_machine.py's own `mark-complete`
    command -- reconciliation of an external-engine-owned row always goes
    through that engine's own write path, never a raw UPDATE against its
    external_ai_sessions table from this script. Returns True iff the engine
    itself reports marked_complete=True."""
    r = _run(["python3", EXTERNAL_AI_STATE_MACHINE_SCRIPT, "mark-complete", "--session-id", session_id])
    if r.returncode != 0:
        return False
    try:
        return bool(json.loads(r.stdout).get("marked_complete"))
    except (json.JSONDecodeError, ValueError):
        return False


_TASK_YAML_PR_URL_RE = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")

# Root-cause fix, UMR-20260806-082352-7b1b (child of Owner directive
# UMR-20260806-081403-ebd3): backfill_null_heartbeats()'s systemd-dispatched
# branch used to mark EVERY NULL-heartbeat, systemctl-confirmed-inactive row
# 'failed' unconditionally, even when that task's own real task.yaml (under
# TASKS_DIR/<task_identity>/) already recorded real forward progress (a
# referenced PR/commit) or genuine completion (a merged PR) -- live-confirmed
# this cycle by manual inspection of 9 real rows: 6 were genuinely
# 'blocked with real forward progress' (mislabeled 'failed'), 1 was genuinely
# 'completed' via a merged PR (mislabeled 'failed'), and 2 were correctly
# 'failed' -- their own task.yaml's claimed progress did NOT hold up under an
# independent cross-check (e.g. a referenced branch's real tip commit
# predated that task's own real start time). The three helpers below
# implement that same real, evidence-based cross-check -- a task.yaml's own
# claims are NEVER trusted on their own; a referenced PR is only believed
# after a real `gh pr view`, and a referenced-but-PR-less branch is only
# believed after its own real tip-commit date is confirmed to postdate the
# task's own real created_at.


def _pr_number_from_task_yaml(doc):
    """Real PR number referenced by this task.yaml doc, if any -- checked
    only in the two real, structured places a task.yaml actually records a
    PR it opened/adopted (the explicit adopted_pr_url field, and any real
    github.com/<owner>/<repo>/pull/NNN URL inside a real checkpoint's own
    note text), never a loose title-text guess like _referenced_pr_number()
    above uses for the (much lower-stakes) duplicate-dispatch guard.

    Independent review finding (real): the URL's own owner/repo segments
    are extracted and cross-checked against this same doc's own top-level
    `repo` field -- a referenced PR whose URL names a DIFFERENT repo than
    this task's own `repo` is real evidence of a mismatch (a copy/paste
    error, or a genuinely unrelated PR), not something to silently trust
    and look up under the WRONG repo; such a mismatch is treated as no real
    reference at all (same fail-toward-'no evidence' philosophy as every
    other branch here).

    Returns the PR number (str), or None if no real same-repo reference
    exists. Never raises -- a malformed checkpoints shape just yields no
    match."""
    own_repo = (doc.get("repo") or "").strip().lower()

    def _same_repo_pr_number(url):
        m = _TASK_YAML_PR_URL_RE.search(url or "")
        if not m:
            return None
        url_repo = m.group(2).strip().lower()
        if own_repo and url_repo != own_repo:
            return None
        return m.group(3)

    pr_number = _same_repo_pr_number(doc.get("adopted_pr_url"))
    if pr_number:
        return pr_number
    for cp in (doc.get("checkpoints") or []):
        note = cp.get("note") if isinstance(cp, dict) else None
        pr_number = _same_repo_pr_number(note)
        if pr_number:
            return pr_number
    return None


def _real_pr_state_for_backfill(pr_number, repo):
    """Real `gh pr view --json state,mergedAt,mergeCommit,url` call, same
    shape pm_cycle_precheck.py's gather_pr_states() already uses -- ground
    truth for whether a task.yaml-referenced PR is genuinely MERGED, still
    OPEN, or genuinely CLOSED-unmerged. Returns {"ok": False, ...} (never
    raises) on any timeout/non-zero-exit/unparseable output -- the caller
    treats an unverifiable PR the same as no real evidence at all."""
    try:
        r = _run(
            ["gh", "pr", "view", str(pr_number), "--repo", f"{GH_ORG}/{repo}",
             "--json", "state,mergedAt,mergeCommit,url"],
            timeout=GH_PR_CHECK_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"gh pr view #{pr_number} timed out (>{GH_PR_CHECK_TIMEOUT_SECONDS}s)"}
    if r.returncode != 0:
        return {"ok": False, "error": (r.stderr or f"gh pr view #{pr_number} failed, exit {r.returncode}").strip()}
    try:
        data = json.loads(r.stdout)
    except (json.JSONDecodeError, ValueError):
        return {"ok": False, "error": f"unparseable `gh pr view #{pr_number}` output"}
    return {
        "ok": True, "state": data.get("state"), "merged_at": data.get("mergedAt"),
        "merge_commit": (data.get("mergeCommit") or {}).get("oid"), "url": data.get("url"),
    }


def _real_branch_tip_commit_date(branch, repo):
    """Real `gh api repos/<org>/<repo>/commits/<branch>` call -- the branch's
    own real current tip commit's real committer date (an aware datetime),
    ground truth for the "does this task.yaml's claimed branch progress
    actually postdate the task's own start" cross-check. Returns None on any
    error/timeout/missing-branch/unparseable-date (never raises, never
    fabricates a date -- an unverifiable branch is treated as no real
    evidence)."""
    if not branch:
        return None
    try:
        r = _run(
            ["gh", "api", f"repos/{GH_ORG}/{repo}/commits/{branch}", "--jq", ".commit.committer.date"],
            timeout=GH_PR_CHECK_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        return None
    raw = (r.stdout or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_parse_iso(value):
    """Best-effort ISO-8601 parse (task.yaml's own created_at, always written
    by veridian-task.py as a real timezone-aware isoformat() string) -- None
    on anything missing/malformed, never raises."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _task_yaml_for_umr_row(task_docs, row):
    """Real task.yaml lookup for one umr_tasks row -- two real, ordered paths,
    not one, per a real gap found while live-re-verifying this fix against
    the current 24h owner_dispatch_gateway set: a plain source_trigger=
    'owner_dispatch_gateway' row's own task_identity is a synthetic
    'owner-task-<ts>-<pid>' string that was NEVER itself a TASKS_DIR
    directory name (confirmed live: 261 of 277 real such rows checked this
    cycle have no task.yaml under either path at all -- a real, honest
    'no evidence' outcome, not a bug). The real remediation/progress record
    for such a row, when one exists, lives in a SEPARATE, later-created
    'adopted-reconcile-umr-<umr_id>-...' task (confirmed live for 16 of
    those 277 real rows) -- e.g. the real task.yaml this exact fix's own
    live re-verification found: task-20260806-072312-adopted-reconcile-umr-
    20260806-042531-be9c--pr11, which explicitly reconciles umr_id
    UMR-20260806-042531-be9c in its own directory name and title.

      1. Direct: task_docs.get(row['task_identity']) -- the common case for
         a directly-dispatched worker task whose own task_identity IS its
         real TASKS_DIR directory name.
      2. Fallback: any task.yaml whose own real directory name contains
         'reconcile-umr-<row's own umr_id, lowercased>' -- if more than one
         (a real re-attempt history), the most recently created_at wins.

    Returns the doc, or None if neither path finds one (falls through to
    the original unconditional 'failed' behavior, unchanged)."""
    doc = task_docs.get(row["task_identity"])
    if doc is not None:
        return doc
    umr_suffix = row["umr_id"][4:] if row["umr_id"].upper().startswith("UMR-") else row["umr_id"]
    needle = f"reconcile-umr-{umr_suffix}".lower()
    candidates = [
        (tid, d) for tid, d in task_docs.items()
        if needle in tid.lower() or needle in (d.get("id") or "").lower()
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[1].get("created_at") or "", reverse=True)
    return candidates[0][1]


def _forward_progress_decision(doc):
    """Real, evidence-based decision for a NULL-heartbeat, systemctl-confirmed
    -inactive row that HAS a real task.yaml (the caller already handles the
    no-task.yaml case as the original unconditional 'failed'). Returns
    (status, detail) where status is one of 'failed' / 'running' /
    'completed' -- 'failed' is the outcome of every branch below unless real,
    independently cross-checked evidence positively justifies 'running' or
    'completed'; this task.yaml's own claims are never trusted on their own
    (see the module-level comment above this function for the real 2-of-9
    live incident that made that cross-check a hard requirement, not an
    optimization). `detail` is a dict of the real evidence gathered, folded
    into the row's own `reason` by the caller."""
    yaml_status = doc.get("status")
    detail = {"task_yaml_status": yaml_status}

    # A task.yaml that already recorded its own genuinely-terminal-negative
    # outcome agrees with the original default -- nothing to override.
    if yaml_status in ("failed", "cancelled", "rejected_duplicate", "superseded", "not_needed"):
        detail["cross_check"] = f"task.yaml itself already status={yaml_status!r} -- no override, default failed retained"
        return "failed", detail

    repo = doc.get("repo")
    pr_number = _pr_number_from_task_yaml(doc)
    if pr_number and repo:
        pr_state = _real_pr_state_for_backfill(pr_number, repo)
        detail["pr_number"] = pr_number
        detail["repo"] = repo
        detail["pr_check"] = pr_state
        if not pr_state.get("ok"):
            detail["cross_check"] = f"gh pr view #{pr_number} unverifiable ({pr_state.get('error')}) -- default failed retained"
            return "failed", detail
        if pr_state["state"] == "MERGED":
            detail["cross_check"] = f"gh pr view #{pr_number} confirms MERGED -- genuine completion"
            return "completed", detail
        if pr_state["state"] == "OPEN":
            detail["cross_check"] = f"gh pr view #{pr_number} confirms OPEN -- real forward progress (blocked, not dead)"
            return "running", detail
        detail["cross_check"] = f"gh pr view #{pr_number} confirms {pr_state['state']} (not merged) -- genuinely rejected/stale"
        return "failed", detail

    # No real PR referenced -- only a 'blocked' task.yaml status plus a real
    # referenced branch, itself cross-checked against this task's own real
    # created_at, can justify 'running'. This is the exact real check that
    # caught both of the 9 real rows whose claimed progress did NOT hold up.
    branch = doc.get("branch")
    if yaml_status == "blocked" and branch and repo:
        tip_date = _real_branch_tip_commit_date(branch, repo)
        created_at = _safe_parse_iso(doc.get("created_at"))
        detail["branch"] = branch
        detail["branch_tip_commit_date"] = tip_date.isoformat() if tip_date else None
        detail["task_created_at"] = created_at.isoformat() if created_at else None
        if tip_date is None:
            detail["cross_check"] = f"branch {branch!r} tip commit date unverifiable via `gh api` -- default failed retained"
            return "failed", detail
        # Independent review finding (blocking, real): created_at missing or
        # unparseable must fail toward 'failed' the exact same way tip_date
        # being unverifiable does above -- `if created_at and ...` alone
        # short-circuits past the postdate check on a falsy created_at and
        # silently falls through to 'running' without ever having verified
        # anything, exactly the "ambiguous claim believed anyway" failure
        # mode this whole function exists to prevent.
        if created_at is None:
            detail["cross_check"] = (
                f"task.yaml created_at missing/unparseable ({doc.get('created_at')!r}) -- cannot verify branch "
                f"{branch!r}'s real tip commit postdates task creation -- default failed retained"
            )
            return "failed", detail
        if tip_date < created_at:
            detail["cross_check"] = (
                f"branch {branch!r} real tip commit ({tip_date.isoformat()}) predates this task's own real "
                f"created_at ({created_at.isoformat()}) -- claimed task.yaml progress does not hold up, genuinely failed"
            )
            return "failed", detail
        detail["cross_check"] = f"branch {branch!r} real tip commit postdates task creation -- real forward progress (blocked, not dead)"
        return "running", detail

    detail["cross_check"] = "no real PR/commit evidence of forward progress in task.yaml -- default failed retained"
    return "failed", detail


def backfill_null_heartbeats(now=None, execute=False, email=None):
    """ONE-TIME operational backfill for the real gap reconcile_stale_heartbeats()
    cannot structurally close (see module docstring above it). Dry-run
    (read-only, execute=False) by default -- computes and reports every
    decision without writing anything; execute=True applies the identical
    decisions for real. A dry run's report is therefore always a true preview
    of what --execute would do, never a different code path.

    NARROW FILTER, DELIBERATE (real incident on this exact server earlier
    today, 2026-07-29): both queries below are scoped to status IN
    ('running','dispatched') ONLY -- 'queued' is deliberately excluded even
    though a queued row can also have last_heartbeat NULL. A queued row has no
    unit_name yet (never dispatched) and may legitimately succeed on its very
    next tick; a broader filter that folded 'queued' in here nearly
    misclassified exactly such a row as dead earlier today. This function must
    never repeat that mistake.

    Two independent ground-truth paths, never a guess, never a raw DB write
    without one:

      (a) unit_name IS NOT NULL (systemd-dispatched): `systemctl --user
          is-active <unit_name>` is ground truth. Active -> genuine live work,
          left alone untouched. Not active -> the unit itself is confirmed
          dead, but that alone no longer means the WORK is dead: this task's
          own real task.yaml (TASKS_DIR/<task_identity>/task.yaml, read via
          dispatch_core.task_status_sync(), never a second parallel reader)
          is real-checked for real evidence of forward progress before
          defaulting to 'failed' -- see _forward_progress_decision()'s own
          docstring for the full real algorithm and the real 2-of-9 live
          incident (a claimed branch whose real tip commit predated its own
          task's real start time) that makes an independent cross-check of
          that evidence a hard requirement, not an optional nicety. A
          referenced PR is only ever believed after a real `gh pr view`
          (MERGED -> 'completed', OPEN -> 'running', CLOSED-unmerged ->
          'failed'); a PR-less 'blocked' status with a referenced branch is
          only believed after that branch's own real tip-commit date is
          confirmed (via `gh api`) to postdate the task's own real
          created_at. No task.yaml, no evidence, or any unverifiable check
          all fail toward the original default: marked 'failed', with
          ts_completed=now and a `reason` recording the real evidence
          gathered (unit_name checked found inactive, plus whatever
          task.yaml/PR/branch cross-check was or wasn't possible).

      (b) unit_name IS NULL (external_ai_state_machine.py-backed, e.g.
          sessions from that state machine which never write to
          umr_tasks.last_heartbeat at all): that engine's own real
          `list-sessions --email <email>` output is ground truth. A row is
          only reconciled if list-sessions returns a session whose own
          task_id exactly matches this row's task_identity AND that session's
          own status is already 'COMPLETE' -- reconciled via that engine's own
          `mark-complete` (never a raw DB edit), then this umr_tasks row is
          synced to status='completed' to match. No matching session, a
          session still 'ACTIVE', the ambiguous 'ABANDONED' state, or a failed/
          unparseable list-sessions call are all left completely untouched --
          this function never guesses at another engine's own session state.

    Returns a report dict: every row examined (with its category, real
    evidence, and decision -- 'left_alone' / 'marked_failed' /
    'reconciled_completed', or the 'would_*' dry-run equivalents), plus
    aggregate counts.
    """
    now = now or _utcnow()
    email = email or BACKFILL_OWNER_EMAIL
    sbr = _superboss_register()
    conn = sbr._connect()
    sbr._ensure_umr_table(conn)

    systemd_rows = conn.execute(
        "SELECT * FROM umr_tasks WHERE status IN ('running','dispatched') "
        "AND last_heartbeat IS NULL AND unit_name IS NOT NULL"
    ).fetchall()
    external_rows = conn.execute(
        "SELECT * FROM umr_tasks WHERE status IN ('running','dispatched') "
        "AND last_heartbeat IS NULL AND unit_name IS NULL"
    ).fetchall()

    report = {
        "execute": execute,
        "ts": now.isoformat(),
        "examined": [],
        "counts": {
            "systemd_examined": len(systemd_rows),
            "systemd_marked_failed": 0,
            "systemd_marked_running": 0,
            "systemd_marked_completed": 0,
            "systemd_left_active": 0,
            "external_examined": len(external_rows),
            "external_reconciled_completed": 0,
            "external_left_untouched": 0,
        },
    }

    # Real task.yaml ground truth for the cross-check below -- ONE real read
    # of TASKS_DIR via dispatch_core's own canonical task_status_sync()
    # (never a second, parallel glob/parse of TASKS_DIR), reused for every
    # row in this loop rather than re-globbed per row.
    try:
        task_docs = _dispatch_core().task_status_sync()
    except Exception as e:
        task_docs = {}
        _append_attention(
            f"WARNING: backfill_null_heartbeats() could not read TASKS_DIR via dispatch_core.task_status_sync() "
            f"({type(e).__name__}: {e}) -- proceeding with NO task.yaml cross-check this run, every systemd-"
            f"dispatched inactive row falls back to the original unconditional 'failed' behavior."
        )

    # --- (a) systemd-dispatched rows: ground-truth via systemctl -----------
    for row in systemd_rows:
        row = dict(row)
        unit = row["unit_name"]
        is_active = _run(["systemctl", "--user", "is-active", "--quiet", unit]).returncode == 0

        if is_active:
            report["counts"]["systemd_left_active"] += 1
            report["examined"].append({
                "umr_id": row["umr_id"], "task_identity": row["task_identity"], "category": "systemd",
                "unit_name": unit, "status_before": row["status"], "decision": "left_alone",
                "detail": f"systemctl --user is-active {unit} -> active; real live work, not touched",
            })
            continue

        doc = _task_yaml_for_umr_row(task_docs, row)
        if doc is not None:
            decided_status, evidence = _forward_progress_decision(doc)
        else:
            decided_status = "failed"
            evidence = {
                "cross_check": (
                    "no task.yaml found under TASKS_DIR for this task_identity, nor any "
                    "'adopted-reconcile-umr-<id>' task referencing this umr_id -- default failed retained"
                )
            }

        base_note = (
            f"one-time backfill reconciliation (Stage 1, {now.isoformat()}): unit_name={unit!r} "
            f"checked via `systemctl --user is-active`, found inactive -- row had last_heartbeat=NULL "
            f"and could never be reached by reconcile_stale_heartbeats()'s stale-heartbeat sweep."
        )
        reason = f"{base_note} Real task.yaml cross-check: {evidence['cross_check']} (full evidence: {json.dumps(evidence)})"

        entry = {
            "umr_id": row["umr_id"], "task_identity": row["task_identity"], "category": "systemd",
            "unit_name": unit, "status_before": row["status"], "evidence": evidence,
            "detail": f"systemctl --user is-active {unit} -> inactive; real task.yaml cross-check -> {decided_status}",
            "reason": reason,
        }

        if decided_status == "completed":
            entry["decision"] = "marked_completed" if execute else "would_mark_completed"
            if execute:
                with sbr._write_lock():
                    sbr.update_umr_task(conn, row["umr_id"], status="completed", ts_completed=_now_iso(), reason=reason)
                    conn.commit()
                report["counts"]["systemd_marked_completed"] += 1
        elif decided_status == "running":
            entry["decision"] = "marked_running" if execute else "would_mark_running"
            if execute:
                # Real evidence-based reconciliation of the null heartbeat --
                # refreshed to `now` so this row leaves this backfill's
                # candidate set (last_heartbeat IS NULL) and returns to being
                # tracked normally by reconcile_stale_heartbeats() going
                # forward, exactly like any other genuinely-alive row.
                with sbr._write_lock():
                    sbr.update_umr_task(conn, row["umr_id"], status="running", last_heartbeat=_now_iso(), reason=reason)
                    conn.commit()
                report["counts"]["systemd_marked_running"] += 1
        else:
            entry["decision"] = "marked_failed" if execute else "would_mark_failed"
            if execute:
                with sbr._write_lock():
                    sbr.update_umr_task(conn, row["umr_id"], status="failed", ts_completed=_now_iso(), reason=reason)
                    conn.commit()
                report["counts"]["systemd_marked_failed"] += 1
        report["examined"].append(entry)

    # --- (b) external_ai_state_machine.py-backed rows: ground-truth via ----
    #         that engine's own list-sessions, never a raw DB read/write.
    sessions = _external_ai_list_sessions(email)
    for row in external_rows:
        row = dict(row)
        match = None
        if sessions:
            for s in sessions:
                if s.get("task_id") == row["task_identity"]:
                    match = s
                    break

        if match is None:
            report["counts"]["external_left_untouched"] += 1
            report["examined"].append({
                "umr_id": row["umr_id"], "task_identity": row["task_identity"], "category": "external",
                "unit_name": None, "status_before": row["status"], "decision": "left_alone",
                "detail": (
                    f"no external_ai_state_machine.py session with task_id== "
                    f"{row['task_identity']!r} found via `list-sessions --email {email}`"
                    if sessions is not None else
                    "list-sessions call failed or returned unparseable output -- cannot verify, "
                    "leaving row untouched"
                ),
            })
            continue

        if match.get("status") != "COMPLETE":
            report["counts"]["external_left_untouched"] += 1
            report["examined"].append({
                "umr_id": row["umr_id"], "task_identity": row["task_identity"], "category": "external",
                "unit_name": None, "status_before": row["status"],
                "session_id": match.get("id"), "session_status": match.get("status"),
                "decision": "left_alone",
                "detail": (
                    f"matching external session {match.get('id')} is real and still "
                    f"{match.get('status')!r} -- genuinely active/incomplete or ambiguous, not touched"
                ),
            })
            continue

        reason = (
            f"one-time backfill reconciliation (Stage 1, {now.isoformat()}): matching "
            f"external_ai_state_machine.py session {match.get('id')!r} for task_identity="
            f"{row['task_identity']!r} is real and already status='COMPLETE' per `list-sessions`; "
            f"reconciled via that engine's own `mark-complete` (not a raw DB edit), then this "
            f"umr_tasks row synced to 'completed' to match."
        )
        entry = {
            "umr_id": row["umr_id"], "task_identity": row["task_identity"], "category": "external",
            "unit_name": None, "status_before": row["status"],
            "session_id": match.get("id"), "session_status": match.get("status"),
            "decision": "reconciled_completed" if execute else "would_reconcile_completed",
            "reason": reason,
        }
        if execute:
            entry["mark_complete_call_result"] = _external_ai_mark_complete(match["id"])
            with sbr._write_lock():
                sbr.update_umr_task(conn, row["umr_id"], status="completed", ts_completed=_now_iso(), reason=reason)
                conn.commit()
            report["counts"]["external_reconciled_completed"] += 1
        report["examined"].append(entry)

    conn.close()
    return report


# ---------------------------------------------------------------------------
# Emergency fail-safe cascade (design doc Section 7)
# ---------------------------------------------------------------------------

def _append_attention(message):
    os.makedirs(os.path.dirname(ATTENTION_PATH), exist_ok=True)
    with open(ATTENTION_PATH, "a") as f:
        f.write(f"\n## {_now_iso()} -- SERVER RESOURCE GOVERNOR\n{message}\n")


def _shed_load(state, metrics=None):
    """Stage 2: SIGTERM the governor's own lowest-tier-priority currently
    running tracked unit, freeing real resources instead of just refusing new
    work. Returns the unit_name shed, or None if there was nothing to shed."""
    # FIX (2026-07-29 gap-fix pass, real bug, live-reproduced): every message
    # below used to interpolate `state` directly and call it "metric
    # overload" -- but `state` is _record_emergency_tick's per-metric
    # CONSECUTIVE-TICK COUNTER (0..EMERGENCY_CONSECUTIVE_TICKS_HARDSTOP), not
    # the actual metric percentages. ATTENTION.md has been logging entries
    # like "sustained metric overload {'disk_io': 6, ...}" that look like
    # "disk_io is at 6%" but actually mean "disk_io has been over-threshold
    # for 6 consecutive ticks" -- this real ambiguity caused multiple
    # independent review passes this session to (reasonably) suspect a false
    # trip. `metrics` (the real sample_metrics() percentages, when the caller
    # has them) is now logged alongside `state` so operators can see the
    # actual numbers, not just the counter.
    metrics_note = f", real metrics at trip time: {metrics}" if metrics is not None else ""
    # Real conflict resolution (merge of the recovered pre-PR20 local hotfix
    # above and PR #20's own independent review round 2 fix -- see PR #21's
    # own description for the full real conflict record): both real fixes
    # kept together, not one discarded in favor of the other. PR #20's real
    # fix: see _safe_superboss_register()'s own docstring -- a
    # broken/unavailable Superboss Register must never crash this function's
    # own real emergency load-shedding cascade -- treated the same as
    # "nothing to shed" for this function's own return contract, but still
    # surfaced as its own real CRITICAL ATTENTION.md entry (in addition to
    # the one _safe_superboss_register() itself already appends), since an
    # inability to even shed load during a sustained metric overload is a
    # more urgent real signal than the ordinary "no running unit" case. Uses
    # the same real metrics_note (recovered fix, above) in its own message
    # too, for the same real reason: `state` alone is a consecutive-tick
    # counter, not a percentage.
    sbr, error = _safe_superboss_register("_shed_load")
    if error:
        _append_attention(f"CRITICAL: sustained over-threshold ticks {state}{metrics_note}, and "
                           f"Superboss Register itself is unavailable -- cannot even shed load. {error}")
        return None
    conn = sbr._connect()
    sbr._ensure_umr_table(conn)
    running = conn.execute(
        "SELECT * FROM umr_tasks WHERE status='running' AND unit_name IS NOT NULL "
        "ORDER BY tier DESC, ts_dispatched ASC"
    ).fetchall()
    if not running:
        conn.close()
        _append_attention(f"CRITICAL: sustained over-threshold ticks {state}{metrics_note}, but no "
                           f"governor-tracked running unit available to shed load from.")
        return None

    victim = dict(running[0])
    _run(["systemctl", "--user", "kill", "-s", "SIGTERM", victim["unit_name"]])
    with sbr._write_lock():
        sbr.update_umr_task(conn, victim["umr_id"], status="sigterm_sent", ts_sigterm=_now_iso())
        conn.commit()
    conn.close()
    _append_attention(
        f"CRITICAL: sustained over-threshold ticks {state}{metrics_note} -- shed load by SIGTERM to "
        f"lowest-tier running unit {victim['unit_name']} (umr_id={victim['umr_id']}, tier={victim['tier']})."
    )
    return victim["unit_name"]


def _write_emergency_stop(state, metrics=None):
    metrics_note = f", real metrics at trip time: {metrics}" if metrics is not None else ""
    _save_json(EMERGENCY_STOP_PATH, {"ts": _now_iso(), "state": state, "metrics": metrics})
    _append_attention(
        f"EMERGENCY STOP: at least one metric stayed at/over {METRIC_THRESHOLD_PERCENT}% for "
        f"{EMERGENCY_CONSECUTIVE_TICKS_HARDSTOP} consecutive governor ticks (consecutive-tick "
        f"counts: {state}{metrics_note}). All new dispatch is halted until an operator runs "
        f"`python3 scripts/resource_governor.py --clear-emergency-stop`."
    )


def _record_emergency_tick(over_metrics, metrics=None):
    """Per-metric consecutive-over-threshold counter, reset to 0 the instant a
    metric drops back under threshold. Escalates through shed-load (Stage 2)
    then hard-stop (Stage 3) as the max consecutive count crosses each
    threshold. Returns the updated state dict.

    Stage 0a (2026-07-29): the whole load-modify-save cycle below is now
    inside _state_file_lock(EMERGENCY_STATE_PATH) -- see that function's
    docstring for why (this was previously a plain unlocked read-modify-write,
    same real gap as sample_metrics()'s metric-state file)."""
    with _state_file_lock(EMERGENCY_STATE_PATH):
        state = _load_json(EMERGENCY_STATE_PATH) or {}
        max_consecutive = 0
        for metric in METRIC_NAMES:
            count = state.get(metric, 0)
            count = count + 1 if metric in over_metrics else 0
            state[metric] = count
            max_consecutive = max(max_consecutive, count)
        _save_json(EMERGENCY_STATE_PATH, state)

    if max_consecutive >= EMERGENCY_CONSECUTIVE_TICKS_HARDSTOP:
        _write_emergency_stop(state, metrics=metrics)
    elif max_consecutive >= EMERGENCY_CONSECUTIVE_TICKS_SHED:
        _shed_load(state, metrics=metrics)
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
                          "last_heartbeat (NULL heartbeats are always skipped) and report/write back "
                          "real terminal status via systemctl --user is-active, scoped only to the "
                          "stale subset. Read-only dry run by default (UMR-20260806-141429-f447: "
                          "reconcile_stale_heartbeats() has its own real execute gate now, matching "
                          "--backfill-null-heartbeats); pass --execute to apply the real writes it "
                          "reports.")
    ap.add_argument("--backfill-null-heartbeats", action="store_true",
                     help="ONE-TIME backfill (Stage 1, 2026-07-29): reconcile running/dispatched "
                          "umr_tasks rows with last_heartbeat IS NULL that reconcile_stale_heartbeats() "
                          "can never reach -- ground-truths unit_name rows via `systemctl is-active` "
                          "and unit_name-IS-NULL rows via external_ai_state_machine.py's own "
                          "list-sessions. Deliberately excludes 'queued' rows. Read-only dry run by "
                          "default; pass --execute to apply the real writes it reports.")
    ap.add_argument("--execute", action="store_true",
                     help="apply real writes for --reconcile-stale / --backfill-null-heartbeats "
                          "(default: read-only dry run that only reports what it WOULD do)")
    ap.add_argument("--backfill-email", dest="backfill_email", default=None,
                     help="override the Owner email used for --backfill-null-heartbeats' "
                          "external_ai_state_machine.py list-sessions lookup "
                          f"(default: {BACKFILL_OWNER_EMAIL!r})")
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
        # Real fix (independent review round 2, PR #20): see
        # _safe_superboss_register()'s own docstring. A broken/unavailable
        # Superboss Register must produce a real, informative CLI error
        # (and non-zero exit) here, never an uncaught Python traceback.
        sbr, error = _safe_superboss_register("--query-umr")
        if error:
            print(json.dumps({"error": error}))
            sys.exit(1)
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
        # 2026-07-29 adversarial-test fix: this used to have no exception
        # handling at all, so any malformed --spec-file (missing file,
        # invalid JSON, JSON that isn't an object, a valid object missing
        # task_identity, an out-of-range --tier, ...) produced a raw Python
        # traceback on stderr instead of the JSON this CLI's every other
        # branch returns -- inconsistent with this file's own
        # json.dumps({...})-everywhere convention, and unsafe for any caller
        # (e.g. gateway.py) that expects to json.loads() the output. Still
        # exits non-zero on failure (unchanged real behavior), just with a
        # clean, parseable error body now.
        try:
            with open(args.spec_file) as f:
                task_spec = json.load(f)
            if not isinstance(task_spec, dict):
                raise ValueError(f"--spec-file must contain a JSON object, got {type(task_spec).__name__}")
            result = submit(task_spec, args.tier, args.source_trigger)
        except Exception as e:
            print(json.dumps({"accepted": False, "umr_id": None,
                               "error": f"{type(e).__name__}: {e}"}))
            sys.exit(1)
        print(json.dumps(result))
        return

    if args.scan_stuck:
        print(json.dumps({"actions": scan_stuck_tasks()}, default=str))
        return

    if args.reconcile_stale:
        print(json.dumps({"actions": reconcile_stale_heartbeats(execute=args.execute)}, default=str))
        return

    if args.backfill_null_heartbeats:
        print(json.dumps(
            backfill_null_heartbeats(execute=args.execute, email=args.backfill_email),
            indent=2, default=str,
        ))
        return

    if args.tick:
        print(json.dumps(run_tick(), default=str))
        return

    ap.print_help()


if __name__ == "__main__":
    main()
