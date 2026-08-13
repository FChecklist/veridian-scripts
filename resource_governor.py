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
import time
import yaml
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
PROC_VMSTAT_PATH = os.environ.get("VERIDIAN_GOVERNOR_PROC_VMSTAT", "/proc/vmstat")

# Real, additive stale-swap-ratchet override (UMR-20260813-155201-da76,
# addendum to P1 UMR-20260806-171945-5767 / UMR-20260813-163237 spec "unwedge
# dispatch -- stale swap ratchet blocked"). Real evidence this closes, live
# 2026-08-13: dispatch_core.py's swap_backoff gate is a STATIC occupancy
# ratio (1 - SwapFree/SwapTotal from /proc/meminfo) -- Linux never
# proactively reclaims swap pages once written, so a single past spike (this
# box's own known ~2GB-per-register-CLI-call working set) can leave that
# ratio permanently >= BACKOFF_UTILIZATION_PCT (0.80) even with abundant real
# MemAvailable and ZERO ongoing swap I/O. 5 real /proc/meminfo samples over
# 15s that tick showed SwapFree byte-frozen at exactly 775980 kB every
# sample (swap_used_pct=0.8149) while MemAvailable held ~11.3GB of 15.6GB
# genuinely free, and real `vmstat 2 5` showed so=1079,0,0,0,0 / si tapering
# to near-zero -- no steady-state swap activity, i.e. the gate was blocking
# on a stale ratchet, not real pressure. See
# swap_activity_quiet_detail()/_override_stale_swap_backoff() below for the
# real mechanism -- this stays in resource_governor.py (exempt from the
# narrow 2026-08-08 stop-work order) and wraps dispatch_core.py's own
# has_free_slot_detail() result; dispatch_core.py itself is left unmodified.
SWAP_ACTIVITY_STATE_PATH = os.environ.get(
    "VERIDIAN_GOVERNOR_SWAP_ACTIVITY_STATE", f"{LOCKS_DIR}/resource-governor-swap-activity-state.json")
# A real elapsed window is required before a "quiet" verdict can be trusted
# -- two samples taken within the same fraction of a second would look
# "quiet" from pure sampling luck, not because swap I/O is actually idle.
SWAP_ACTIVITY_MIN_INTERVAL_SECONDS = float(
    os.environ.get("VERIDIAN_GOVERNOR_SWAP_ACTIVITY_MIN_INTERVAL_S", "5"))
# Small, real allowance for isolated single-page noise (e.g. one cold page
# swapped in by an unrelated process) -- NOT a real sustained swap-out.
# Default 0: only a byte-for-byte-zero pswpin/pswpout delta counts as quiet.
SWAP_ACTIVITY_NOISE_PAGES = int(os.environ.get("VERIDIAN_GOVERNOR_SWAP_ACTIVITY_NOISE_PAGES", "0"))

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

# Point 14/16 of task-gateway.py audit-24-points (UMR-20260808-145030-f3d1,
# governing chain UMR-20260806-171945-5767): the exact two-condition
# staleness definition this session's own PM review cycles have been
# checking manually via ad-hoc sqlite queries against umr_tasks throughout
# 2026-08 -- deliberately distinct from MAX_QUEUED_AGE_SECONDS (4h,
# flag_stale_queued_tasks()'s own real-remediation threshold above) and
# HEARTBEAT_STALE_TTL_SECONDS (15min, reconcile_stale_heartbeats()'s own
# real-remediation threshold) -- see detect_stale_umr_rows()'s own
# docstring for why a third, read-only detection-only pair of thresholds is
# correct here rather than reusing either existing one.
UMR_STALE_QUEUED_DISPATCH_NULL_SECONDS = int(os.environ.get(
    "VERIDIAN_GOVERNOR_STALE_QUEUED_DISPATCH_NULL_S", str(90 * 60)))
UMR_STALE_RUNNING_HEARTBEAT_SECONDS = int(os.environ.get(
    "VERIDIAN_GOVERNOR_STALE_RUNNING_HEARTBEAT_S", str(45 * 60)))

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

# Real issue #980 (UMR_5767_ISSUE_RESOLUTION_MATRIX.json, governed by
# UMR-20260806-171945-5767 / UMR-20260807-161418-a63f): a standing Owner
# stop-work order is only a real, deterministic gate if it lives in code, not
# in individual dispatched-worker judgment. Confirmed live, same real day:
# UMR-20260807-110133-205d's real worker (task-20260807-150203) never checked
# for the standing order at all -- zero mentions anywhere in its own real
# PROGRESS.md -- and merged real PRs (#269, #250, #251) straight through the
# gap, while nine separate other dispatches (b4e9, a7e5, 7433, 35bc, a683,
# f9f4, ee23, a4b5, 162a) each independently happened to check and correctly
# declined. STOP_WORK_ORDER_TASK_IDS is the real, well-known, reviewable
# marker this gate checks -- a tuple of task ids, not a free-text
# PROGRESS.md/prose search. Append to it (never silently remove an id) the
# moment a new standing stop-work order is declared, so every future order
# is enforced by this one real gate instead of depending on which worker
# instance happens to think to check -- the exact inconsistency real issue
# #980 exists to close. Per AGENTS.md Rule 9, narrowing what this gate
# enforces (removing an id) is a guardrail change and needs the same
# explicit Owner sign-off + manifest-style review as any other guardrail
# weakening, quoted in the PR that does it.
#
# Env-overridable, same convention every other real constant in this module
# already follows (EMERGENCY_STOP_PATH, METRIC_THRESHOLD_PERCENT, ...) -- the
# real production default is the one standing order below; tests unrelated to
# this specific gate set VERIDIAN_GOVERNOR_STOP_WORK_ORDER_TASK_IDS="" to
# disable it cleanly (including across a real subprocess boundary, e.g.
# dispatch-owner-task.sh's own tests, which cannot reach into this module's
# Python attributes directly the way an in-process test can).
STOP_WORK_ORDER_TASK_IDS = tuple(
    t.strip() for t in os.environ.get(
        "VERIDIAN_GOVERNOR_STOP_WORK_ORDER_TASK_IDS",
        "task-20260806-165921-owner-absolute-stop-work-order--complete",
    ).split(",") if t.strip()
)
# The one real, independently-verifiable channel a stop-work-order exemption
# (or an order being genuinely lifted) can be recorded through. Deliberately
# the SAME file real Owner-approved operational decisions already use for
# other low-stakes items (e.g. crontab-snapshot approvals) -- not a new
# mechanism, reusing an existing, already-established real record.
OWNER_DECISIONS_PATH = os.environ.get(
    "VERIDIAN_OWNER_DECISIONS_PATH", f"{AI_OS}/OWNER_DECISIONS_NEEDED_2026-07-23.yaml")
STOP_WORK_ORDER_GIT_TIMEOUT_SECONDS = int(
    os.environ.get("VERIDIAN_GOVERNOR_STOP_WORK_GIT_TIMEOUT_S", "5"))
# Real hardening, 2026-08-08 (independent tier1 review of the first version
# of _git_committed_file_text() below, filed under real issue #980): the
# original implementation read `git show HEAD:<path>` in whatever branch
# happened to be checked out in the shared, live AI_OS working directory at
# call time -- it never verified HEAD was actually on trunk or had been
# pushed/merged anywhere. Confirmed live, same real day: that directory was
# found checked out on an unrelated, pre-existing local branch whose HEAD was
# a real, unpushed local commit matching the exact "lift the stop-work
# order" pattern this gate exists to police -- i.e. the gap was not
# hypothetical, it was the live, present state of the very file this gate
# reads. STOP_WORK_ORDER_TRUNK_REF is the one real ref this gate will ever
# trust; a bare ref with no "/" (e.g. a local branch name, used by tests that
# don't want a real network fetch) skips the fetch step entirely and reads
# that ref directly instead.
STOP_WORK_ORDER_TRUNK_REF = os.environ.get(
    "VERIDIAN_GOVERNOR_STOP_WORK_TRUNK_REF", "origin/main")
# Real, bounded retry for the real `git fetch` the trunk-ref pinning above
# requires -- rides out a genuinely transient network/auth blip without
# weakening the fail-closed security property (a real, sustained failure
# still blocks; see _git_committed_file_text()'s own docstring for why
# that's correct, not a bug).
STOP_WORK_ORDER_GIT_FETCH_RETRIES = int(
    os.environ.get("VERIDIAN_GOVERNOR_STOP_WORK_GIT_FETCH_RETRIES", "2"))
STOP_WORK_ORDER_GIT_FETCH_RETRY_DELAY_SECONDS = float(
    os.environ.get("VERIDIAN_GOVERNOR_STOP_WORK_GIT_FETCH_RETRY_DELAY_S", "0.5"))


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
# Stale-swap-ratchet override (UMR-20260813-155201-da76) -- see the
# SWAP_ACTIVITY_* constants' own comment above for the full real incident.
# Two real, independent pieces: (1) read real MemAvailable headroom directly
# (this module's own PROC_MEMINFO_PATH, same convention as read_mem_percent()
# above -- never dispatch_core.py's private helper, so this stays fully
# testable via the existing env-override convention), and (2) a real,
# delta-based swap-activity check against /proc/vmstat's cumulative
# pswpin/pswpout counters, persisted across calls the same way
# sample_metrics() above persists cpu/disk/net state -- never a blocking
# `vmstat N M` subprocess call inside this 30s-cadence dispatch hot path.
# ---------------------------------------------------------------------------

def read_swap_page_counters(path=None):
    """Real, cumulative since-boot pswpin/pswpout page counts from
    /proc/vmstat -- the same real kernel counters `vmstat`'s own si/so
    columns are derived from (vmstat itself just reports the per-interval
    DELTA of these two counters). Reading the raw cumulative values lets a
    delta be taken between two real, timestamped governor samples instead
    of shelling out to `vmstat N M`, which blocks for N*M wall-clock
    seconds -- unacceptable inside dispatch_one()'s real per-tick path."""
    path = path or PROC_VMSTAT_PATH
    pswpin = pswpout = 0
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) != 2:
                continue
            if parts[0] == "pswpin":
                pswpin = int(parts[1])
            elif parts[0] == "pswpout":
                pswpout = int(parts[1])
    return pswpin, pswpout


def swap_activity_quiet_detail(now=None):
    """(quiet, detail): real, delta-based check of whether swap is ACTIVELY
    being written/read right now, independent of the static SwapFree/
    SwapTotal occupancy ratio dispatch_core.py's swap_backoff check uses.
    Same persisted-state-file delta pattern sample_metrics() above already
    uses for cpu/disk/net (a separate state file -- SWAP_ACTIVITY_STATE_PATH
    -- so this never contends with or corrupts that one).

    quiet is True only when ALL of the following real conditions hold:
      - a PRIOR sample exists (not this process's first-ever call/cold
        start against SWAP_ACTIVITY_STATE_PATH),
      - at least SWAP_ACTIVITY_MIN_INTERVAL_SECONDS of real wall-clock time
        has elapsed since it (guards against a too-close-together pair of
        calls looking "quiet" purely from too short a window to measure
        across), and
      - both the real pswpin and pswpout deltas over that window are at/
        under SWAP_ACTIVITY_NOISE_PAGES.
    Every other case (cold start, too-short interval, or a real nonzero-
    beyond-noise delta) returns quiet=False -- fails open to the ORIGINAL
    swap_backoff block; this function only ever narrows when dispatch backs
    off, it never widens uncertainty into an override."""
    now = now or _utcnow()
    curr_in, curr_out = read_swap_page_counters()
    curr_ts = now.timestamp()

    with _state_file_lock(SWAP_ACTIVITY_STATE_PATH):
        prev = _load_json(SWAP_ACTIVITY_STATE_PATH)
        _save_json(SWAP_ACTIVITY_STATE_PATH, {"ts": curr_ts, "pswpin": curr_in, "pswpout": curr_out})

    if prev is None:
        return False, {"check": "swap_activity_cold_start"}

    dt = curr_ts - prev.get("ts", curr_ts)
    if dt < SWAP_ACTIVITY_MIN_INTERVAL_SECONDS:
        return False, {"check": "swap_activity_interval_too_short", "dt_seconds": dt,
                        "min_interval_seconds": SWAP_ACTIVITY_MIN_INTERVAL_SECONDS}

    in_delta = max(0, curr_in - prev.get("pswpin", curr_in))
    out_delta = max(0, curr_out - prev.get("pswpout", curr_out))
    quiet = in_delta <= SWAP_ACTIVITY_NOISE_PAGES and out_delta <= SWAP_ACTIVITY_NOISE_PAGES
    return quiet, {
        "check": "swap_activity_quiet" if quiet else "swap_activity_sustained",
        "pswpin_delta": in_delta, "pswpout_delta": out_delta, "dt_seconds": dt,
        "noise_allowance_pages": SWAP_ACTIVITY_NOISE_PAGES,
    }


def _real_mem_headroom_bytes(path=None):
    """Real MemAvailable headroom (bytes) below dispatch_core.py's own
    BACKOFF_UTILIZATION_PCT ceiling on memory -- the identical math
    dispatch_core.has_resource_headroom_detail()'s mem_headroom_budget check
    already does, independently re-derived here from this module's own
    PROC_MEMINFO_PATH (never dispatch_core.py's private helper) so this stays
    testable via the existing env-override convention. Returns None if
    MemTotal is unreadable/zero -- callers must treat that as "cannot
    confirm headroom", never as "abundant"."""
    path = path or PROC_MEMINFO_PATH
    vals = {}
    with open(path) as f:
        for line in f:
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable"):
                parts = rest.strip().split()
                if parts:
                    vals[key] = int(parts[0]) * 1024
    mem_total = vals.get("MemTotal", 0)
    if not mem_total:
        return None
    mem_available = vals.get("MemAvailable", mem_total)
    mem_used_bytes = mem_total - mem_available
    dc = _dispatch_core()
    return (mem_total * dc.BACKOFF_UTILIZATION_PCT) - mem_used_bytes


def _override_stale_swap_backoff(slot_ok, slot_detail, now=None):
    """Real, narrow override of dispatch_core.has_free_slot_detail()'s
    "swap_backoff" veto specifically -- see the SWAP_ACTIVITY_* constants'
    own comment above for the real evidence this closes.

    Deliberately narrow: only ever overrides slot_detail["check"] ==
    "swap_backoff" (the SOFT 0.80 BACKOFF_UTILIZATION_PCT threshold
    dispatch_core.py's own module comment documents as "meaningfully below
    the hard ceiling... a build/compile spike... still has real room before
    0.99"). NEVER overrides "swap_hard_ceiling" (the Owner's own 0.99
    number, "never cross" per that same module comment), "mem_backoff",
    "mem_hard_ceiling", "mem_headroom_budget", "load1_backoff",
    "load1_unreadable", or "cap_exhausted" -- none of those are the stale
    ratchet this UMR's real evidence found; overriding any of them would be
    exactly the kind of invented exemption this task's own spec forbids.
    Passing through slot_ok/slot_detail completely unchanged (including
    slot_ok=True, i.e. no block to override) is the correct behavior for
    every one of those other cases.

    Both of the following real, freshly-live-read conditions must hold, or
    the original (slot_ok, slot_detail) is returned unchanged:
      1. _real_mem_headroom_bytes() confirms at least one more worker's own
         PER_WORKER_MEMORY_BUDGET_BYTES of real headroom below the backoff
         ceiling -- memory itself must be genuinely abundant, not just
         "not yet over its own threshold".
      2. swap_activity_quiet_detail() confirms zero-or-noise real
         pswpin/pswpout activity over a real, trustworthy elapsed window.

    Returns (ok, detail) in the exact same shape dispatch_core.py's own
    has_free_slot_detail() uses. When it overrides, detail carries
    check="swap_backoff_override_stale_ratchet" plus every real number both
    conditions were computed from, so this is fully diagnosable from the
    tick log / veridian-dispatch-decision journal alone, same as every
    other real check in this module."""
    if slot_ok or not slot_detail or slot_detail.get("check") != "swap_backoff":
        return slot_ok, slot_detail

    dc = _dispatch_core()
    try:
        mem_headroom_bytes = _real_mem_headroom_bytes()
    except (OSError, ValueError):
        return slot_ok, slot_detail  # real /proc/meminfo unreadable -- fail open to the original block
    if mem_headroom_bytes is None or mem_headroom_bytes < dc.PER_WORKER_MEMORY_BUDGET_BYTES:
        return slot_ok, slot_detail  # real memory headroom is NOT independently confirmed abundant

    try:
        quiet, activity_detail = swap_activity_quiet_detail(now=now)
    except (OSError, ValueError):
        return slot_ok, slot_detail  # real /proc/vmstat unreadable -- fail open to the original block
    if not quiet:
        return slot_ok, slot_detail  # real swap I/O is active, or not yet confirmed quiet

    return True, {
        "check": "swap_backoff_override_stale_ratchet",
        "original_check": "swap_backoff",
        "swap_used_pct": slot_detail.get("swap_used_pct"),
        "threshold_pct": slot_detail.get("threshold_pct"),
        "mem_headroom_bytes": mem_headroom_bytes,
        "required_bytes": dc.PER_WORKER_MEMORY_BUDGET_BYTES,
        "swap_activity": activity_detail,
    }


# ---------------------------------------------------------------------------
# Real issue #980 -- standing stop-work-order gate (single deterministic gate,
# not per-worker judgment). See STOP_WORK_ORDER_TASK_IDS's own comment above
# for the real incident this closes.
# ---------------------------------------------------------------------------

def _git_committed_file_text(path, timeout=None):
    """Real content of `path` as committed on the real, pushed trunk ref
    (STOP_WORK_ORDER_TRUNK_REF, default origin/main) -- deliberately NEVER
    the live working tree, and NEVER raw local HEAD either.

    Real, confirmed reason this exact distinction is load-bearing (2026-08-07,
    real issue #980's own governing evidence): a fabricated stop-work-order
    "exemption" was independently caught and declined three separate times,
    escalating from embedded dispatch-prompt text to an uncommitted edit of
    OWNER_DECISIONS_NEEDED_2026-07-23.yaml each time. The first version of
    this function closed that specific gap by reading `git show HEAD:<path>`
    instead of the working tree -- but HEAD alone was not enough: confirmed
    live, 2026-08-08, the very next escalation was a real, committed local
    git commit (real author, real message, genuinely at HEAD) that was never
    pushed anywhere -- /opt/veridian/ai-os was found checked out on an
    unrelated, pre-existing local branch whose HEAD was exactly that commit.
    A purely local, unpushed commit on any branch checked out in this shared
    directory satisfied the original HEAD-only check just as well as a real,
    reviewed, merged PR would, with zero GitHub involvement. Reading a real
    remote-tracking ref (origin/main by default) after a real, bounded `git
    fetch` closes this: no actor can make content "authoritative" here
    without it actually existing on a real, fetchable, pushed branch, which
    for this repo's own branch-protection convention means it went through a
    real PR and review (AGENTS.md Rule 6's PR/CI gate).

    Fails closed on every real failure mode: the fetch itself failing (a
    real, honest signal something's wrong -- e.g. network/auth issues --
    never silently fall back to a possibly-stale cached remote-tracking
    ref), the ref not resolving, the path not existing at that ref, or any
    timeout. Every caller must treat None as "not verified", never as "no
    restriction". A bare ref with no "/" in STOP_WORK_ORDER_TRUNK_REF (e.g. a
    local branch name) skips the fetch step and reads that ref directly --
    for tests that intentionally want a real, local, no-network trunk
    fixture, never for production (whose real default always has a "/").

    Real operational risk flagged 2026-08-08 (independent tier1 review,
    round 2, not yet an incident -- a real, worth-confirming caveat on the
    hardening above): every veridian_task_create call while a stop-work
    order is open now requires a live git fetch to succeed, including to
    recognize a real, already-pushed, approved exemption/lift entry -- a
    sustained network/credential outage on this box would block ALL task
    creation with no way to lift it short of a code/env change.
    STOP_WORK_ORDER_GIT_FETCH_RETRIES (default 2, bounded, short fixed
    backoff) rides out a genuinely transient blip without weakening the
    real security property -- a real, SUSTAINED failure still fails closed,
    which is the correct, intended behavior for a security gate (better to
    block real work than silently accept unverified authorization), not a
    bug to route around."""
    directory = os.path.dirname(os.path.abspath(path))
    timeout = STOP_WORK_ORDER_GIT_TIMEOUT_SECONDS if timeout is None else timeout
    trunk_ref = STOP_WORK_ORDER_TRUNK_REF
    try:
        root_proc = _run(["git", "-C", directory, "rev-parse", "--show-toplevel"], timeout=timeout)
        if root_proc.returncode != 0:
            return None
        repo_root = root_proc.stdout.strip()
        relpath = os.path.relpath(os.path.abspath(path), repo_root)
        if relpath.startswith(".."):
            return None
        if "/" in trunk_ref:
            remote, _, branch = trunk_ref.partition("/")
            fetch_ok = False
            for attempt in range(STOP_WORK_ORDER_GIT_FETCH_RETRIES + 1):
                if attempt > 0:
                    time.sleep(STOP_WORK_ORDER_GIT_FETCH_RETRY_DELAY_SECONDS)
                fetch_proc = _run(
                    ["git", "-C", repo_root, "fetch", "--quiet", remote, branch], timeout=timeout)
                if fetch_proc.returncode == 0:
                    fetch_ok = True
                    break
            if not fetch_ok:
                return None  # fail closed -- a real, sustained fetch failure, never a stale cached ref
        show_proc = _run(["git", "-C", repo_root, "show", f"{trunk_ref}:{relpath}"], timeout=timeout)
        if show_proc.returncode != 0:
            return None
        return show_proc.stdout
    except Exception:
        return None


def _owner_decisions_committed_entries():
    """Real list of entries from OWNER_DECISIONS_PATH as committed on the
    real trunk ref (see _git_committed_file_text()'s own docstring for why
    this must never be the live working tree or raw local HEAD). Returns []
    -- fail closed -- on any missing file, git failure, or malformed YAML;
    callers must treat an empty result as "no real verified exemption/lift
    found", never as permission to proceed. The real file's own top-level
    shape is a dict with a `decisions` key (confirmed via direct read,
    2026-08-08), not a bare list -- unwrapped here the same way every other
    real reader of this file already does."""
    text = _git_committed_file_text(OWNER_DECISIONS_PATH)
    if text is None:
        return []
    try:
        data = yaml.safe_load(text)
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("decisions", [])
    if not isinstance(data, list):
        return []
    return [entry for entry in data if isinstance(entry, dict)]


def _stop_work_order_exemption_covers(entry, task_identity, title, umr_id):
    """Real, deterministic scope match for a real, committed, status:approved
    exemption entry. Matches only on real identifiers this dispatch actually
    carries (task_identity / umr_id) or an explicit, unambiguous
    all-work-covered phrase in the entry's own text -- deliberately never
    matches on `title` text alone, since title is requester-controlled prose,
    not a real identifier, and matching on it would let a fabricated dispatch
    simply reuse wording from a real, narrowly-scoped exemption to claim
    broader coverage than was actually approved."""
    scope_text = " ".join(
        str(entry.get(k) or "") for k in ("what", "needed_action", "title", "scope")
    )
    if task_identity and task_identity in scope_text:
        return True
    if umr_id and umr_id in scope_text:
        return True
    if re.search(r"\ball\b[^.]*(pr|push)|every (pr|push)", scope_text, re.IGNORECASE):
        return True
    return False


def _stop_work_order_lifted_for(order_id, entries):
    """Real, deterministic per-order lift check. 2026-08-08 hardening (real
    issue #980 follow-up, independent tier1 review): the original
    implementation let ANY approved 'stop-work-order-lifted' entry lift
    EVERY order in STOP_WORK_ORDER_TASK_IDS at once, with no check on which
    specific order its own scope text actually named -- fine while only one
    order was ever open, but the tuple is explicitly documented to grow, and
    an entry meant to lift one order would silently over-lift all of them.
    Matches only on the real order_id string appearing in the entry's own
    scope text (what/needed_action/title/scope) -- same convention and same
    reasoning as _stop_work_order_exemption_covers()'s task_identity/umr_id
    matching: never match on unstructured prose alone."""
    for entry in entries:
        entry_id = str(entry.get("id") or "")
        if "stop-work-order-lifted" not in entry_id:
            continue
        if str(entry.get("status") or "").strip().lower() != "approved":
            continue
        scope_text = " ".join(
            str(entry.get(k) or "") for k in ("what", "needed_action", "title", "scope")
        )
        if order_id in scope_text:
            return True
    return False


def resource_threshold_block_reason(now=None):
    """Real, shared resource-protection gate (UMR-20260808-121334-e122,
    Owner-decided Option B, PM decision cycle UMR-20260808-141807-7f38,
    2026-08-08): the EMERGENCY_STOP sentinel-file check and the live
    metric-threshold ("frozen") check _dispatch_one_inner() already ran,
    unconditionally, before selecting or spawning any real work, are
    extracted here -- pure extraction, same two checks, same order, same
    real return values -- so task-gateway.py's cmd_start (a different,
    synchronous, direct-spawn calling convention Option B deliberately
    leaves unchanged, rather than restructuring cmd_start into
    dispatch_one()'s async submit-and-queue shape) gets the identical real
    protection before IT spawns a real systemd unit too, instead of a
    parallel, divergent reimplementation of these same two checks.

    Returns (blocked: bool, detail: str|None, metrics: dict|None). metrics
    is None only for the emergency-stop case (sample_metrics() never runs
    there -- unnecessary once already blocked, matching the original
    inline code's own short-circuit).

    Deliberately does NOT call _record_emergency_tick() -- that is
    dispatch_one()'s own tick-cadence-specific escalation bookkeeping (a
    consecutive-TICKS-over-threshold counter that can itself write the
    EMERGENCY_STOP sentinel or shed load). Calling it from here would let
    cmd_start's on-demand, non-periodic calls corrupt that real "consecutive
    ticks" semantics. _record_emergency_tick() stays dispatch_one()-only,
    called by it after this function returns, exactly as before this
    extraction."""
    if os.path.exists(EMERGENCY_STOP_PATH):
        return True, "EMERGENCY_STOP sentinel present -- clear via --clear-emergency-stop", None
    metrics = sample_metrics(now=now)
    over = over_threshold_metrics(metrics)
    if over:
        return True, f"metric(s) at/over {METRIC_THRESHOLD_PERCENT}%: {over}", metrics
    return False, None, metrics


def _stop_work_order_block_reason(task_kind, task_identity=None, title=None, umr_id=None):
    """Real, deterministic single-gate check for the standing stop-work
    order(s) named in STOP_WORK_ORDER_TASK_IDS (real issue #980). Returns a
    real, human-readable block reason string if this dispatch must be
    blocked, or None if it may proceed.

    In scope: only task_kind == 'veridian_task_create' -- the order's own
    text explicitly covers "any PR review or push work"; task_kind ==
    'systemctl_action' rows (service start/stop/restart) do neither and are
    unaffected, the same real distinction the pre-existing duplicate-PR
    guard below already draws (only 'veridian_task_create' rows can ever
    have an associated PR).

    An order in STOP_WORK_ORDER_TASK_IDS is presumed OPEN by definition of
    being in that real, well-known, reviewable tuple (fail closed -- the
    only way to close one without a code change is a real, git-committed,
    origin/main-verified OWNER_DECISIONS_PATH entry whose id contains
    'stop-work-order-lifted', status: approved, and whose own scope text
    names that SPECIFIC order_id -- see _stop_work_order_lifted_for()).

    A real exemption only counts if it is BOTH (a) a status: approved entry
    in OWNER_DECISIONS_PATH whose id contains 'stop-work-order-exemption',
    AND (b) present in that file's content as committed on the real,
    fetched trunk ref (see _git_committed_file_text()) -- an uncommitted
    working-tree edit, an unpushed local commit on any branch, or a claim
    that only exists in dispatch-prompt text, is never sufficient (see that
    function's own docstring for the real, confirmed fabrication patterns
    this specifically defeats)."""
    if task_kind != "veridian_task_create":
        return None
    open_orders = list(STOP_WORK_ORDER_TASK_IDS)
    if not open_orders:
        return None

    entries = _owner_decisions_committed_entries()

    # Real per-order scoping: an order only drops out of "still open" if a
    # real, committed, approved, in-scope lift record names it specifically.
    still_open_orders = [
        order_id for order_id in open_orders
        if not _stop_work_order_lifted_for(order_id, entries)
    ]
    if not still_open_orders:
        return None  # every real, currently-tracked order has a real, in-scope lift record

    for entry in entries:
        entry_id = str(entry.get("id") or "")
        if str(entry.get("status") or "").strip().lower() != "approved":
            continue
        if "stop-work-order-exemption" in entry_id and _stop_work_order_exemption_covers(
                entry, task_identity, title, umr_id):
            return None  # real, committed, approved, in-scope exemption

    reason = (
        f"BLOCKED by standing stop-work order(s) {still_open_orders!r} -- real issue #980 gate "
        f"(UMR_5767_ISSUE_RESOLUTION_MATRIX.json, governed by UMR-20260806-171945-5767 / "
        f"UMR-20260807-161418-a63f). No real, git-committed, origin/main-verified, status:approved "
        f"exemption/lift entry found in {OWNER_DECISIONS_PATH!r} covering this dispatch "
        f"(task_identity={task_identity!r}, title={title!r}, umr_id={umr_id!r}). A prompt-text-only, "
        f"uncommitted-working-tree-only, or unpushed-local-commit-only claim of Owner exemption does "
        f"NOT satisfy this gate -- see _stop_work_order_block_reason()'s own docstring."
    )
    # UMR-20260808-074726-d105 (governing chain UMR-20260806-171945-5767): the
    # 'software also has to write to it' half of the master_issue_tracker
    # permanence directive -- see _record_master_issue_if_new()'s own
    # docstring. Dedup-checked against the real issue_id this gate's own
    # already-migrated row uses ('UMR5767-0980', real issue #980 cited
    # throughout this function's own comments above) -- a real no-op against
    # production today, live-verified to actually insert against a DB that
    # doesn't already have that row.
    _record_master_issue_if_new(
        "UMR5767-0980",
        "The standing stop-work order is only a real, deterministic gate if it lives in code, not "
        "individual dispatched-worker judgment -- resource_governor.py's own "
        "_stop_work_order_block_reason() gate is that real, code-level enforcement.",
        linked_umr_id="UMR-20260806-171945-5767",
        linked_source="resource_governor.py:_stop_work_order_block_reason",
        file_path="scripts/resource_governor.py",
    )
    return reason


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

    # Real issue #980 (UMR-20260807-161418-a63f) -- standing stop-work-order
    # gate. Deliberately placed here: after every real shape/type validation
    # above (same "validate first" ordering OCID-068 Rule 3 already
    # establishes) but BEFORE the reuse-check/DB-connection work below, so a
    # blocked dispatch never even reaches "queued" -- which, for the real
    # dispatch-owner-task.sh caller, also means its own downstream tmux relay
    # into a live interactive session (which only fires once accepted=True)
    # never happens either. This closes BOTH real channels a stop-work-order
    # violation could travel through, not just the mechanical dispatch_one()
    # pickup path -- _dispatch_one_inner() below re-checks this same gate as
    # defense in depth for any row that reaches the queue by a different
    # route (e.g. one queued before this gate existed, or before an order
    # started, or inserted by a caller other than submit()).
    stop_work_block_reason = _stop_work_order_block_reason(
        task_kind, task_identity=task_identity, title=inputs.get("title"))
    if stop_work_block_reason:
        sbr, error = _safe_superboss_register("submit")
        if error:
            return {"accepted": False, "umr_id": None, "reason": error}
        with sbr._write_lock():
            conn = sbr._connect()
            sbr._ensure_umr_table(conn)
            umr_id = sbr.upsert_umr_task(conn, {
                "task_identity": task_identity,
                "tier": tier,
                # Reuses the existing 'rejected_duplicate' status value
                # (same convention _dispatch_one_inner()'s own
                # superseded_by_ocid_evidence/rejected_duplicate_pr guards
                # already use below -- see their comments) rather than
                # widening umr_tasks.status's CHECK constraint for a single
                # new value; the real, grep-able signal for WHY is this
                # row's own `reason` text, not its status.
                "status": "rejected_duplicate",
                "source_trigger": source_trigger,
                "task_kind": task_kind,
                "unit_name": task_spec.get("unit_name"),
                "tenant_id": tenant_id,
                "inputs": task_spec.get("inputs", {}),
                "reason": stop_work_block_reason,
                "metadata": {"stop_work_order_block": True},
            })
            conn.commit()
            conn.close()
        return {"accepted": False, "umr_id": umr_id, "reason": stop_work_block_reason}

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
# UMR-20260807-110133-205d -- single deterministic orchestrator pipeline.
# Governing chain: UMR-20260806-171945-5767 (original spec,
# task-20260806-201941), first amendment (UMR-20260807-035145-aa45, real:
# task-20260807-053227-...), second amendment (task-20260807-053232-...).
#
# Twelve real, ordered integration steps into run_tick()'s own real pass,
# each one a real import + real function call into the named existing file,
# never reimplemented here. Every new call site below is wrapped fail-open
# (same convention as _safe_superboss_register() above): a problem in any
# ONE of these twelve additions must never block or crash the real
# stuck-task/stale-queue/dispatch pass this whole module exists to run.
# ---------------------------------------------------------------------------

_sbg = None


def _superboss_gateway():
    """Step 1 lazy loader for scripts/superboss_gateway.py, same importlib
    convention as _superboss_register()/_dispatch_core() above (the file
    lives under SCRIPTS/scripts/, the real deployed location -- see
    `ls /opt/veridian/scripts/scripts/superboss_gateway.py`, landed PR
    #257). Calls its real handle_read()/handle_write() in-process (never
    over its HTTP transport) -- those ARE the real allowlisted-table /
    validated-column logic the gateway's docstring describes; calling them
    directly here reuses that real logic without making a governor tick
    depend on a separate long-running server process being up first.
    superboss_gateway.py's own docstring is explicit that migrating the 46
    PRE-EXISTING raw sqlite3.connect() callers (including this file's own
    sbr-based access via _superboss_register()) is separate,
    deliberately-not-done-here follow-up work -- this wiring only covers
    the NEW read this pipeline itself adds (step 1 below), it does not
    touch any pre-existing DB access path in this file."""
    global _sbg
    if _sbg is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "superboss_gateway_governor", os.path.join(SCRIPTS, "scripts", "superboss_gateway.py"))
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sbg = _mod
    return _sbg


_rve = None


def _reuse_verdict_engine():
    """Step 2 lazy loader for reuse_verdict_engine.py (PR #251, real
    deterministic three-tier create/reuse/duplicate_blocked verdict --
    sha256 intent hash + stable term-frequency/IDF vector similarity, zero
    AI model call)."""
    global _rve
    if _rve is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "reuse_verdict_engine_governor", os.path.join(SCRIPTS, "reuse_verdict_engine.py"))
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _rve = _mod
    return _rve


_hc15 = None


def _health_check_15min():
    """Step 4 lazy loader for health-check-15min.py, imported directly (not
    subprocess) so its real is_stale_blocked(task_id) staleness function
    runs in-process."""
    global _hc15
    if _hc15 is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "health_check_15min_governor", os.path.join(SCRIPTS, "health-check-15min.py"))
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _hc15 = _mod
    return _hc15


_aocr = None


def _audit_ocid_canonical_registry():
    """Steps 3/6 lazy loader for audit_ocid_canonical_registry.py's real
    plan_for_ocid()/resolve_ocid_canonical() six-method cross-reference
    (umr_tasks substring + full-dump grep, `gh pr list --search`, `git log
    --all --grep`, PR-body UMR-id extraction, MASTER-TRACKER/ACTIVE-CLAIMS
    grep as last resort)."""
    global _aocr
    if _aocr is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "audit_ocid_canonical_registry_governor",
            os.path.join(SCRIPTS, "audit_ocid_canonical_registry.py"))
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _aocr = _mod
    return _aocr


_aocc = None


def _audit_ocid_compliance():
    """Step 8 lazy loader for audit_ocid_compliance.py's real
    build_compliance_report()/plan_pairs() -- read-only over
    ocid_compliance_state, whose 13 boolean fields are themselves derived
    by real sqlite AFTER INSERT/UPDATE triggers from ocid_compliance_audit_log
    evidence (superboss-register.py's
    _ensure_ocid_compliance_state_derive_triggers()), never caller-set."""
    global _aocc
    if _aocc is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "audit_ocid_compliance_governor", os.path.join(SCRIPTS, "audit_ocid_compliance.py"))
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _aocc = _mod
    return _aocc


_docen = None


def _document_engine():
    """Step 10 lazy loader for document_engine.py's real
    detect_duplicate_documents_by_hash() -- exact contentHash grouping."""
    global _docen
    if _docen is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "document_engine_governor", os.path.join(SCRIPTS, "document_engine.py"))
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _docen = _mod
    return _docen


_intenge = None


def _intent_engine():
    """Step 11 lazy loader for intent_engine.py's real
    cmd_check_intent()/intent_unmatched_log miss-logging pattern."""
    global _intenge
    if _intenge is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "intent_engine_governor", os.path.join(SCRIPTS, "intent_engine.py"))
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _intenge = _mod
    return _intenge


_TERMINAL_UMR_STATUSES = frozenset({
    "completed", "failed", "rejected_duplicate", "rejected_duplicate_pr",
    "killed", "superseded_by_ocid_evidence", "rejected_duplicate_reuse_verdict",
})


def _orchestrator_output_contract(sbr, umr_id, status, reason, outputs):
    """Step 9: every real completion write this file makes for a terminal
    status (see _TERMINAL_UMR_STATUSES above) now ends at
    superboss-register.py's real derive_umr_output_contract() -- the one
    real output shape (UMR-20260806-171945-5767 2nd amendment, PR #250) --
    merged into outputs under 'output_contract' before the existing
    sbr.update_umr_task() call already writes it. Non-terminal writes
    (running/sigterm_sent/etc.) are intentionally left untouched by
    callers of this helper -- derive_umr_output_contract() is a
    completion-time contract, not an intermediate-state one, per its own
    docstring. Never raises: an output-contract derivation failure must
    never block the real status write it's attached to (same fail-open
    convention as _safe_superboss_register())."""
    if status not in _TERMINAL_UMR_STATUSES:
        return outputs
    try:
        contract = sbr.derive_umr_output_contract(umr_id, status, reason or "", outputs or {})
        return {**(outputs or {}), "output_contract": contract}
    except Exception as e:
        try:
            _append_attention(f"WARNING: output_contract derivation failed for {umr_id!r}: {e}")
        except Exception:
            pass
        return outputs


def _orchestrator_reuse_verdict_gate(sbr, conn, row):
    """Step 2: call reuse_verdict_engine.py's real assess() before any new
    dispatch (spawn). Returns (blocked: bool, verdict_result_or_None).
    Fail-open: any exception here (e.g. the vector_similarity candidate
    table not yet populated on a brand-new DB) is treated as
    non-blocking -- this is a NEW, additive gate on top of the two
    existing, independently-proven duplicate guards
    (superseded_by_ocid_evidence / rejected_duplicate_pr) already in
    _dispatch_one_inner, never a replacement for them."""
    try:
        rve = _reuse_verdict_engine()
        raw_inputs = row.get("inputs_json")
        inputs = json.loads(raw_inputs) if isinstance(raw_inputs, str) else (raw_inputs or {})
        intent_text = (inputs.get("title") or row.get("task_identity") or "")
        result = rve.assess(conn, sbr, intent_text, use_cache=True)
        if result.get("verdict") == "duplication_blocked":
            return True, result
        if result.get("verdict") == "create_authorized":
            # Step 11: reuse intent_engine.py's real miss-logging pattern
            # for this real inventory gap (a dispatch about to create new
            # work with no existing candidate match at all).
            _orchestrator_log_intent_miss(intent_text, domain="dispatch")
        return False, result
    except Exception as e:
        try:
            _append_attention(f"WARNING: reuse_verdict_engine.assess() failed, dispatch not blocked: {e}")
        except Exception:
            pass
        return False, None


def _orchestrator_log_intent_miss(intent_text, domain=None):
    """Step 11: reuse intent_engine.py's real cmd_check_intent()
    miss-logging pattern (writes intent_unmatched_log via its own
    _ensure_tables()/INSERT, only for a genuine miss) rather than
    reimplementing that INSERT here. Fail-open, best-effort: a logging
    failure must never affect a real dispatch decision."""
    try:
        ie = _intent_engine()
        args = argparse.Namespace(intent_text=intent_text, domain=domain, session_id=None)
        ie.cmd_check_intent(args)
    except Exception as e:
        try:
            _append_attention(f"WARNING: intent_engine miss-logging failed: {e}")
        except Exception:
            pass


def _orchestrator_ocid_governance_check(sbr, conn, ocid_number):
    """Steps 3/6/8, combined and gated: only runs when a real dispatch row
    actually names an OCID (same `ocid_match = re.search(r"OCID-0*(\\d+)",
    title)` extraction _dispatch_one_inner already uses for its own
    superseded_by_ocid_evidence guard) -- never on every tick/row, since
    audit_ocid_canonical_registry.py's real six-method cross-reference
    shells out to `gh pr list --search` and `git log --all --grep` per
    OCID, and running that unconditionally on every dispatch would itself
    be exactly the kind of resource load this governor exists to prevent.

    Step 3: OCID-068 Rule 1 (superboss-register.py's real
    find_most_recent_umr_by_identity()) for idempotent reuse-not-remint
    ordering -- already the live pattern submit() itself uses; re-checked
    here so the SAME idempotency guarantee also covers this tick's
    governance read, not only submission time.
    Step 6: audit_ocid_canonical_registry.py's real plan_for_ocid() for
    this OCID's cross-referenced canonical-registry integrity claim.
    Step 8: audit_ocid_compliance.py's real trigger-derived compliance
    state (query_ocid_compliance_state(), never re-derived here) for the
    governance decision itself.

    Returns a dict of real evidence (never raises -- fail-open, matching
    every other real call site in this pipeline)."""
    evidence = {"ocid_number": ocid_number}
    try:
        task_identity_hint = f"OCID-driven governance check for {ocid_number}"
        prior = sbr.find_most_recent_umr_by_identity(conn, task_identity_hint)
        evidence["rule1_idempotent_prior"] = prior["umr_id"] if prior else None
    except Exception as e:
        evidence["rule1_error"] = str(e)
    try:
        aocr = _audit_ocid_canonical_registry()
        existing_by_ocid = {
            r["ocid_number"]: r for r in sbr.query_ocid_canonical_registry(conn, ocid_number=ocid_number)
        }
        plan = aocr.plan_for_ocid(sbr, conn, ocid_number, existing_by_ocid)
        evidence["canonical_status"] = plan.get("status")
    except Exception as e:
        evidence["canonical_registry_error"] = str(e)
    try:
        aocc = _audit_ocid_compliance()
        state_rows = sbr.query_ocid_compliance_state(conn, ocid_number=ocid_number)
        evidence["compliance_report"] = aocc.build_compliance_report(
            state_rows, sbr.OCID_COMPLIANCE_STATE_RULE_FIELDS)
    except Exception as e:
        evidence["compliance_error"] = str(e)
    return evidence


def _orchestrator_tick_maintenance(sbr, now=None):
    """Real, once-per-tick (never once-per-row) additions -- deliberately
    kept OUTSIDE the dispatch_core lock and outside any per-row hot path,
    since none of these need row-level freshness and running them once per
    real run_tick() pass (not once per dispatched row) keeps their real
    cost bounded, matching this module's own resource-governance purpose.

    Step 1: superboss_gateway.py real read (wiring_registry snapshot) --
    the one NEW read this pipeline itself adds through the gateway.
    Step 4: health-check-15min.py real is_stale_blocked() staleness check,
    applied to any real 'blocked' task.yaml rows under TASKS_DIR (bounded:
    only the current real blocked set, not a fresh directory-wide scan
    beyond what os.listdir already returns).
    Step 10: document_engine.py real detect_duplicate_documents_by_hash()
    exact-hash dedup, applied to the real top-level *.py files in SCRIPTS
    (file/script cruft detection) -- real sha256 content hashes computed
    here (document_engine.py itself never hashes; it only groups
    pre-supplied hashes, per its own real code), the grouping logic reused
    unmodified.
    Step 12: PRAGMA wal_checkpoint(TRUNCATE) + conditional VACUUM, added
    directly here per the governing SPEC's own explicit instruction --
    neither superboss_gateway.py nor superboss-register.py expose a
    maintenance/PRAGMA endpoint, so this is the one genuinely unavoidable
    exception to "never raw sqlite3.connect": it reuses sbr._connect() (the
    real, already-trusted connection helper) rather than opening a second,
    independently-hardcoded raw connection.

    Returns a dict of real evidence for this tick; never raises."""
    now = now or _utcnow()
    report = {}

    try:
        sbg = _superboss_gateway()
        status, payload = sbg.handle_read({"table": "wiring_registry", "limit": 1})
        report["gateway_read"] = {"status": status, "count": payload.get("count")}
    except Exception as e:
        report["gateway_read_error"] = str(e)

    try:
        hc = _health_check_15min()
        stale_blocked = []
        if os.path.isdir(hc.TASKS_DIR):
            for task_id in os.listdir(hc.TASKS_DIR):
                yaml_path = os.path.join(hc.TASKS_DIR, task_id, "task.yaml")
                if not os.path.exists(yaml_path):
                    continue
                try:
                    with open(yaml_path) as f:
                        if "status: blocked" not in f.read():
                            continue
                except Exception:
                    continue
                if hc.is_stale_blocked(task_id):
                    stale_blocked.append(task_id)
        report["stale_blocked_tasks"] = stale_blocked
    except Exception as e:
        report["staleness_check_error"] = str(e)

    try:
        docen = _document_engine()
        import hashlib as _hashlib
        documents = []
        for name in os.listdir(SCRIPTS):
            if not name.endswith(".py"):
                continue
            path = os.path.join(SCRIPTS, name)
            try:
                with open(path, "rb") as f:
                    content_hash = _hashlib.sha256(f.read()).hexdigest()
            except Exception:
                continue
            documents.append({"id": name, "contentHash": content_hash})
        report["duplicate_script_groups"] = docen.detect_duplicate_documents_by_hash(documents)
    except Exception as e:
        report["dedup_check_error"] = str(e)

    try:
        conn = sbr._connect()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            conn.commit()
            report["wal_checkpoint"] = "truncate_attempted"
            page_count = conn.execute("PRAGMA page_count;").fetchone()[0]
            freelist_count = conn.execute("PRAGMA freelist_count;").fetchone()[0]
            report["page_count"] = page_count
            report["freelist_count"] = freelist_count
            # Conditional, not unconditional: VACUUM is real, non-trivial
            # I/O (same real-cost reasoning credit-accountant.py's own
            # existing VACUUM call already documents) -- only run it when
            # free pages are a real, non-trivial fraction of the file,
            # never on every single tick.
            if page_count > 0 and (freelist_count / page_count) >= 0.20:
                conn.execute("VACUUM;")
                report["vacuum"] = "ran"
            else:
                report["vacuum"] = "skipped_below_threshold"
        finally:
            conn.close()
    except Exception as e:
        report["pragma_maintenance_error"] = str(e)

    return report


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
    # UMR-20260807-110133-205d step 2: reuse_verdict_engine.py's real
    # duplication_blocked verdict -- same real "rejected" outcome as the
    # two existing duplicate guards above, a NEW third one.
    "rejected_duplicate_reuse_verdict": "rejected",
    # Real issue #980 (UMR-20260807-161418-a63f): the row this fires on is
    # written terminal (status='rejected_duplicate', ts_completed set) by
    # the same real gate -- "rejected", not "blocked", since it will not be
    # silently retried by this same UMR row; a fresh resubmission re-runs
    # the same real gate and is blocked again for as long as the order
    # remains open.
    "blocked_stop_work_order": "rejected",
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
    # UMR-20260808-121334-e122 (Option B, 2026-08-08): these two checks are
    # now resource_threshold_block_reason() (see its own docstring) --
    # behavior here is UNCHANGED, this is a pure extraction so task-
    # gateway.py's cmd_start can share the identical real checks.
    resource_blocked, resource_detail, metrics = resource_threshold_block_reason(now=now)
    if metrics is None:
        # EMERGENCY_STOP sentinel -- short-circuited before sample_metrics()
        # ever ran, exactly as this function's own pre-extraction code did.
        return {"action": "emergency_stopped", "detail": resource_detail}

    over = over_threshold_metrics(metrics)
    _record_emergency_tick(over, metrics=metrics)
    if resource_blocked:
        return {"action": "frozen", "detail": resource_detail, "metrics": metrics}

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

        # UMR-20260813-155201-da76 (unwedge dispatch -- stale swap ratchet
        # blocked dispatch, addendum to P1 UMR-20260806-171945-5767): a
        # "swap_backoff" slot_detail specifically can be a STALE ratchet
        # (static SwapFree/SwapTotal occupancy that Linux never proactively
        # reclaims) rather than real, current pressure -- see
        # _override_stale_swap_backoff()'s own docstring for the real,
        # narrow conditions (abundant real MemAvailable headroom AND
        # confirmed-quiet real swap I/O) required before this can ever
        # override, and for why every other real gate (including the 0.99
        # swap_hard_ceiling) is left completely untouched. Passes through
        # unchanged in every other case.
        slot_overridden = slot_ok is False and (slot_detail or {}).get("check") == "swap_backoff"
        slot_ok, slot_detail = _override_stale_swap_backoff(slot_ok, slot_detail, now=now)
        if slot_overridden and slot_ok:
            _append_attention(
                f"INFO: dispatch_one() overrode a stale swap_backoff ratchet for "
                f"umr_id={row['umr_id']!r} -- real MemAvailable headroom confirmed abundant and "
                f"real swap I/O confirmed quiet, see slot_detail: {slot_detail}"
            )

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

        # Real issue #980 (UMR-20260807-161418-a63f) -- standing stop-work-
        # order gate, defense in depth. submit() already runs this same real
        # check at admission time (see its own comment for why that also
        # closes dispatch-owner-task.sh's tmux-relay channel) -- this second
        # check here covers any row that reaches 'queued' by a different
        # route: one queued before a stop-work order started, one queued
        # before this gate itself existed, or one inserted by some future
        # caller that bypasses submit(). Runs first, before the OCID/
        # duplicate-PR guards below -- a governance block takes priority
        # over deduplication logic, not the other way around.
        if row["task_kind"] == "veridian_task_create":
            raw_inputs_for_stop_work = row.get("inputs_json")
            row_inputs_for_stop_work = (
                json.loads(raw_inputs_for_stop_work) if isinstance(raw_inputs_for_stop_work, str)
                else (raw_inputs_for_stop_work or {})
            )
            stop_work_block_reason = _stop_work_order_block_reason(
                row["task_kind"], task_identity=row["task_identity"],
                title=row_inputs_for_stop_work.get("title"), umr_id=row["umr_id"],
            )
            if stop_work_block_reason:
                with sbr._write_lock():
                    sbr.update_umr_task(conn, row["umr_id"], status="rejected_duplicate",
                                         ts_completed=_now_iso(), reason=stop_work_block_reason)
                    conn.commit()
                conn.close()
                _append_attention(
                    f"BLOCKED: dispatch_one() refused to spawn a real veridian_task_create row "
                    f"(umr_id={row['umr_id']!r}, task_identity={row['task_identity']!r}) -- "
                    f"{stop_work_block_reason}"
                )
                return {"action": "blocked_stop_work_order", "umr_id": row["umr_id"],
                         "detail": stop_work_block_reason, "metrics": metrics}

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
                        sbr.update_umr_task(
                            conn, row["umr_id"], status="rejected_duplicate",
                            ts_completed=_now_iso(), reason=reason,
                            outputs=_orchestrator_output_contract(
                                sbr, row["umr_id"], "rejected_duplicate", reason, {}),
                        )
                        conn.commit()
                    # Steps 3/6/8: OCID governance cross-reference, since
                    # this row genuinely names a real OCID -- run before
                    # conn.close() below, reusing this same real connection.
                    ocid_evidence = _orchestrator_ocid_governance_check(sbr, conn, ocid_number)
                    conn.close()
                    _append_attention(
                        f"INFO: dispatch_one() skipped a real, redundant veridian_task_create "
                        f"spawn for umr_id={row['umr_id']!r} (task_identity={row['task_identity']!r}): "
                        f"{reason}"
                    )
                    return {"action": "superseded_by_ocid_evidence", "umr_id": row["umr_id"],
                             "detail": reason, "ocid_number": ocid_number, "metrics": metrics,
                             "ocid_governance": ocid_evidence}

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
                    sbr.update_umr_task(
                        conn, row["umr_id"], status="rejected_duplicate",
                        ts_completed=_now_iso(), reason=reason,
                        outputs=_orchestrator_output_contract(
                            sbr, row["umr_id"], "rejected_duplicate", reason, {}),
                    )
                    conn.commit()
                conn.close()
                return {"action": "rejected_duplicate_pr", "umr_id": row["umr_id"], "detail": reason,
                         "pr": {"repo": dup_repo, "number": dup_pr}, "metrics": metrics}

        # Step 2: reuse_verdict_engine.py's real assess() -- a NEW,
        # additive duplication gate on top of the two existing,
        # independently-proven guards just above. Fail-open (see
        # _orchestrator_reuse_verdict_gate()'s own docstring).
        blocked, verdict_result = _orchestrator_reuse_verdict_gate(sbr, conn, row)
        if blocked:
            reason = (
                f"reuse_verdict_engine.assess() real verdict=duplication_blocked "
                f"(best_match={verdict_result.get('best_match')!r}, score={verdict_result.get('score')!r}) "
                f"-- redispatch skipped, not spawned"
            )
            with sbr._write_lock():
                sbr.update_umr_task(
                    conn, row["umr_id"], status="rejected_duplicate",
                    ts_completed=_now_iso(), reason=reason,
                    outputs=_orchestrator_output_contract(
                        sbr, row["umr_id"], "rejected_duplicate", reason, {"reuse_verdict": verdict_result}),
                )
                conn.commit()
            conn.close()
            return {"action": "rejected_duplicate_reuse_verdict", "umr_id": row["umr_id"],
                     "detail": reason, "reuse_verdict": verdict_result, "metrics": metrics}

        result = _perform_spawn(row)
        with sbr._write_lock():
            sbr.update_umr_task(
                conn, row["umr_id"], status=result["status"],
                unit_name=result.get("unit_name") or row["unit_name"],
                ts_dispatched=_now_iso(),
                outputs=_orchestrator_output_contract(
                    sbr, row["umr_id"], result["status"], None, result.get("outputs", {})),
                metric_snapshot=metrics,
            )
            conn.commit()
        if result["status"] == "running":
            dc.record_dispatch_event(
                task_id=row["task_identity"], dispatched_by=f"resource_governor:{row['source_trigger']}",
                source_queue_or_plan="umr_tasks", worker_unit=result.get("unit_name") or row["unit_name"] or "",
            )
        conn.close()
    return {"action": "dispatched", "umr_id": row["umr_id"], "result": result, "metrics": metrics}


def flag_stale_queued_tasks(now=None):
    """Real max-queued-age safeguard -- see the MAX_QUEUED_AGE_SECONDS module
    comment above for the full real incident this closes.

    Deliberately generic and deterministic, zero AI judgment: does not try to
    diagnose WHY a row is stale, only measures real age against a real,
    documented, bounded threshold. Any row that has been status='queued' for
    at least MAX_QUEUED_AGE_SECONDS gets exactly one real, idempotent
    pm_decisions_pending row opened via superboss-register.py's own
    insert_pm_decision_pending() -- never a raw UPDATE/DELETE against
    umr_tasks, and this function never itself resolves/closes a flagged row or
    changes umr_tasks in any way; only a real PM decision
    (resolve_pm_decision_pending()) ever closes what this opens. Idempotent by
    a real pre-check against pm_decisions_pending itself (skips a umr_id that
    already has an open 'STALE-QUEUED:' row) -- safe to call every real tick,
    same as scan_stuck_tasks() below.

    Returns the list of umr_id values newly flagged this call (empty if
    nothing newly stale). Fails open/silent (empty list) if Superboss
    Register is unavailable -- a broken check here must never crash the real
    dispatch tick that calls this, same philosophy as scan_stuck_tasks()."""
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

    flagged = []
    for row, age_seconds in stale:
        umr_id = row["umr_id"]
        already_open = conn.execute(
            "SELECT id FROM pm_decisions_pending WHERE related_umr=? AND status='open' "
            "AND title LIKE 'STALE-QUEUED:%'",
            (umr_id,),
        ).fetchone()
        if already_open:
            continue
        age_hours = age_seconds / 3600.0
        threshold_hours = MAX_QUEUED_AGE_SECONDS / 3600.0
        title = f"STALE-QUEUED: {umr_id} queued {age_hours:.1f}h (exceeds {threshold_hours:.1f}h safeguard)"
        detail = (
            f"task_identity={row['task_identity']!r} tier={row['tier']} "
            f"source_trigger={row['source_trigger']!r} ts_submitted={row['ts_submitted']!r} "
            f"reason={row.get('reason')!r} unit_name={row.get('unit_name')!r} -- real, "
            f"deterministic max-queued-age safeguard (resource_governor.py "
            f"flag_stale_queued_tasks(), UMR-20260806-090229-f2a7): this row has never "
            f"reached a real terminal status (completed/failed/killed/rejected_duplicate) "
            f"within {threshold_hours:.1f}h of its real ts_submitted. Zero AI judgment "
            f"applied here -- a real PM decision is needed on whether to hold, investigate, "
            f"or manually intervene."
        )
        with sbr._write_lock():
            sbr.insert_pm_decision_pending(
                conn, title, detail, related_umr=umr_id,
                recommended_option="investigate real dispatch history for this umr_id",
            )
            conn.commit()
        flagged.append(umr_id)
    conn.close()
    return flagged


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
    # UMR-20260807-110133-205d step 2: same real row-resolved reasoning as
    # the two actions above -- this row is no longer 'queued' either.
    "rejected_duplicate_reuse_verdict",
})


def _advance_owner_priority_phases_safe(now=None):
    """Real, fail-open wrapper (same convention as _safe_superboss_register
    above) around superboss-register.py's own advance_owner_priority_phases
    -- amendment to UMR-20260807-070110-5ea7 (governed by
    UMR-20260806-124055-bc80): called every real tick, BEFORE the first
    next_queued_task() lookup, per that task's own SPEC ("run every tick
    before next_queued_task"). A broken/unavailable Superboss Register or
    any transient failure inside the phase-advance check must never crash
    or block the rest of run_tick()'s own real dispatch loop -- same
    'never raises for a normal outcome' contract scan_stuck_tasks()/
    dispatch_one() already carry. Returns the real result dict from
    advance_owner_priority_phases(), or a real {'error': ...} dict on any
    failure (never raises).

    Deliberately does NOT itself change next_queued_task()'s own row
    selection -- that consumption side of owner_priority_override is
    UMR-20260807-070110-5ea7's own real, separately-dispatched work (see
    its SPEC: "build a real, narrow, bounded priority-override mechanism
    in next_queued_task"). This function only keeps owner_priority_override
    populated with the current real active phase's members every tick, so
    that mechanism (whenever it lands) always reads a real, up-to-date
    table -- extending 5ea7's population lifecycle, not duplicating its
    consumption logic.

    Real review finding (PR #256 review.json, round 2), fixed here:
    this used to wrap the ENTIRE sbr.advance_owner_priority_phases() call
    in sbr._write_lock() -- superboss-register.py's own cross-process
    flock that every other write-path invocation of that script (dispatch,
    submit, mark-terminal, ...) across the whole system must also acquire.
    advance_owner_priority_phases() can, for commit_sha-backed member
    evidence, shell out to real 60s-timeout git fetch/cat-file/merge-base
    subprocess calls; holding the system-wide write lock across that meant
    a slow/degraded network during an active large phase (3/4: 179/70 real
    members per the SPEC's own evidence) could block every other worker's
    write-path invocation of superboss-register.py for the whole window --
    the exact starvation failure mode this feature exists to fix,
    reintroduced at system-wide scope. advance_owner_priority_phases()
    now acquires sbr._write_lock() itself, only around its own real reads/
    writes, deliberately releasing it across its own evidence-check loop --
    so this caller must NOT wrap the call in its own lock (see that
    function's own docstring: doing so would, via _write_lock()'s real
    reentrancy, collapse its two short critical sections back into one
    long one spanning the unlocked loop, silently reintroducing this exact
    bug).

    Also real review finding (PR #256 review.json, round 2, minor): `conn`
    is now opened before, and closed in a `finally` after, the real call --
    previously an exception raised inside the `with sbr._write_lock():`
    block (e.g. _sync_owner_priority_override's own >1-active-phase
    RuntimeError) skipped the conn.close() below it entirely, leaking the
    connection on that error path."""
    sbr, error = _safe_superboss_register("advance_owner_priority_phases")
    if error:
        return {"error": error}
    conn = None
    try:
        conn = sbr._connect()
        sbr._ensure_umr_table(conn)
        sbr._ensure_ocid_canonical_registry_table(conn)
        return sbr.advance_owner_priority_phases(conn, now=now)
    except Exception as e:
        return {"error": f"advance_owner_priority_phases failed: {e}"}
    finally:
        if conn is not None:
            conn.close()


def run_tick(max_dispatches=None, now=None):
    """One full governor pass: real owner-priority-phase advance check,
    stuck-task scan, stale-queued-age safeguard, the real
    UMR-20260807-110133-205d twelve-step orchestrator maintenance pass
    (step 1/4/10/12, once per tick -- see
    _orchestrator_tick_maintenance()'s own docstring; steps 2/3/6/8/9/11 are
    wired into dispatch_one()'s own real per-row path, since they gate or
    shape an actual per-row dispatch decision; steps 5/7 are pre-existing,
    unmodified real behavior of this module and dispatch_core.py
    respectively), then priority-ordered dispatch until the queue is empty,
    a slot/metric limit stops it, or max_dispatches is reached.

    The loop keeps going past any outcome that already resolved the picked
    row to a real terminal (non-'queued') status -- see
    ROW_RESOLVED_NON_DISPATCH_ACTIONS's docstring above -- and only stops on
    a genuinely row-independent block or an empty queue."""
    results = {
        "owner_priority_phase_advance": _advance_owner_priority_phases_safe(now=now),
        "stuck_task_actions": scan_stuck_tasks(now=now),
        "stale_queued_flagged": flag_stale_queued_tasks(now=now),
        "dispatches": [],
    }
    sbr, error = _safe_superboss_register("run_tick_orchestrator_maintenance")
    if error:
        results["orchestrator_maintenance"] = {"error": error}
    else:
        results["orchestrator_maintenance"] = _orchestrator_tick_maintenance(sbr, now=now)
    while max_dispatches is None or len(results["dispatches"]) < max_dispatches:
        r = dispatch_one(now=now)
        results["dispatches"].append(r)
        # UMR-20260813-120054-4e66: real, per-tick journal instrumentation --
        # see dispatch_core.log_dispatch_decision()'s own docstring for the
        # full real gap this closes (journalctl showed nothing useful about
        # WHY a tick dispatched nothing, even on the real unit that owns
        # this real dispatch loop). Best-effort/fail-open inside that
        # function itself -- never allowed to break a real tick.
        _dispatch_core().log_dispatch_decision(r)
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
    was stuck).

    Real fix (RCA, UMR-20260813-101757-f13c, live-reproduced incident,
    UMR-20260808-150937-43d0): scoped to task_kind='veridian_task_create'
    only. This whole SIGTERM/SIGKILL protocol assumes "running" means "an
    ephemeral task unit that is expected to exit on its own within
    STUCK_TASK_TIMEOUT_SECONDS of ITS OWN start" -- true for
    veridian-worker@<task_id>.service units (Type=oneshot-ish, one task,
    then exit), false for task_kind='systemctl_action' rows, whose
    unit_name is often a persistent, always-on singleton daemon
    (Restart=always/on-failure, WantedBy=default.target -- e.g.
    veridian-superboss-gateway.service, veridian-glm-proxy.service,
    veridian-governor-tick.service) that is SUPPOSED to keep running
    forever. _perform_spawn() marks a systemctl_action row status="running"
    the instant `systemctl start` returns 0 -- including when the unit was
    ALREADY active, in which case _unit_active_enter_timestamp() reports
    the timestamp of whenever it first started, which can trivially be
    older than STUCK_TASK_TIMEOUT_SECONDS. That made this scan wrongly
    conclude the row was "stuck" on its very next tick and SIGTERM/SIGKILL
    (and, at line ~2565 below, disable) the real, healthy, always-on
    gateway daemon -- confirmed live: UMR-20260808-150937-43d0 (a
    registration-only "start veridian-superboss-gateway.service" row) was
    SIGTERM'd 31s after dispatch and SIGKILL'd+disabled 60s after that, and
    the real unit was still disabled/inactive 5 days later when this RCA
    ran. systemctl_action rows have no "must exit" contract to police --
    _perform_spawn() already resolves their real outcome synchronously
    (status="running"/"failed" from the `systemctl start` returncode
    itself) -- so they must never enter this ephemeral-task reaper at all."""
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
        "SELECT * FROM umr_tasks WHERE status='running' AND unit_name IS NOT NULL "
        "AND task_kind='veridian_task_create'"
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
            kill_reason = f"stuck-task SIGKILL: no exit {SIGTERM_TO_SIGKILL_GRACE_SECONDS}s after SIGTERM"
            with sbr._write_lock():
                sbr.update_umr_task(
                    conn, row["umr_id"], status="killed", ts_completed=_now_iso(), reason=kill_reason,
                    outputs=_orchestrator_output_contract(sbr, row["umr_id"], "killed", kill_reason, {}),
                )
                conn.commit()
            actions.append({"umr_id": row["umr_id"], "unit_name": row["unit_name"], "action": "SIGKILL",
                             "since_sigterm_s": since_sigterm})

    conn.close()
    return actions


def detect_stale_umr_rows(now=None):
    """Real, READ-ONLY staleness scan across live umr_tasks: a row matches if
    EITHER (a) status='queued' AND ts_dispatched IS NULL for at least
    UMR_STALE_QUEUED_DISPATCH_NULL_SECONDS (default 90 minutes), age measured
    from ts_submitted since a never-dispatched row has no other real
    timestamp to measure from; OR (b) status='running' AND its heartbeat is
    stale -- last_heartbeat older than UMR_STALE_RUNNING_HEARTBEAT_SECONDS
    (default 45 minutes) if it has ever heartbeated, else its unit's real
    ActiveEnterTimestamp (same anchor scan_stuck_tasks() above already uses)
    if it has never heartbeated, so a genuinely freshly-started running row
    with no heartbeat yet is not falsely flagged.

    This is the exact two-condition definition this session's own PM review
    cycles have been checking manually via ad-hoc sqlite queries against
    umr_tasks throughout 2026-08 -- extracted here as one real, callable,
    deterministic function so task-gateway.py audit-24-points (Point 14) and
    the existing 30-second resource_governor_tick_loop.sh (Point 16) share
    the identical definition instead of each carrying its own, potentially-
    divergent one (UMR-20260808-145030-f3d1).

    Deliberately detection-only, no remediation: flag_stale_queued_tasks()
    and scan_stuck_tasks() already own real remediation on their own,
    different thresholds/conditions above -- this function only answers
    'do any real rows match', matching Point 14's own framing. Returns the
    list of matching rows (each a dict: umr_id, status, condition,
    age_seconds, task_identity), empty if none. Fails open (empty list) if
    Superboss Register is unavailable, same convention as every other real
    scan in this module."""
    now = now or _utcnow()
    sbr, error = _safe_superboss_register("detect_stale_umr_rows")
    if error:
        return []
    conn = sbr._connect()
    sbr._ensure_umr_table(conn)
    matches = []

    for row in conn.execute(
        "SELECT * FROM umr_tasks WHERE status='queued' AND ts_dispatched IS NULL"
    ).fetchall():
        row = dict(row)
        ts_submitted = row.get("ts_submitted")
        if not ts_submitted:
            continue
        if isinstance(ts_submitted, str):
            ts_submitted = datetime.fromisoformat(ts_submitted)
        age_seconds = max(0.0, (now - ts_submitted).total_seconds())
        if age_seconds >= UMR_STALE_QUEUED_DISPATCH_NULL_SECONDS:
            matches.append({
                "umr_id": row["umr_id"], "status": "queued", "condition": "queued_ts_dispatched_null",
                "age_seconds": age_seconds, "task_identity": row.get("task_identity"),
            })

    for row in conn.execute("SELECT * FROM umr_tasks WHERE status='running'").fetchall():
        row = dict(row)
        last_heartbeat = row.get("last_heartbeat")
        if last_heartbeat:
            anchor = last_heartbeat
            if isinstance(anchor, str):
                anchor = datetime.fromisoformat(anchor)
            age_seconds = max(0.0, (now - anchor).total_seconds())
        else:
            unit_name = row.get("unit_name")
            started = _unit_active_enter_timestamp(unit_name) if unit_name else None
            if started is None:
                continue  # no real anchor to measure staleness from -- never falsely flag
            age_seconds = max(0.0, (now - started).total_seconds())
        if age_seconds >= UMR_STALE_RUNNING_HEARTBEAT_SECONDS:
            matches.append({
                "umr_id": row["umr_id"], "status": "running", "condition": "running_no_heartbeat",
                "age_seconds": age_seconds, "task_identity": row.get("task_identity"),
            })

    conn.close()
    return matches


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
        # Real, documented answer to UMR171945-0002 (single output gate audit,
        # 2026-08-08): this writes status=terminal via update_umr_task()
        # DIRECTLY, not through superboss-register.py's cmd_mark_umr_terminal()
        # -- and so does NOT go through that CLI's own
        # validate_umr_terminal_completion_evidence() gate (the real
        # commit-sha/file-path requirement UMR-20260806-130914-e7f1 built for
        # a *claimed* completion). That is deliberate, not a gap: this sweep's
        # own real evidence basis is different in kind, not absent -- it is
        # live, directly-observed systemd unit state (confirmed is_active=
        # False above, a real, present-tense fact this function checked
        # itself), not a claim about a commit/PR that could be fabricated or
        # stale. Forcing this through the PR/commit-evidence gate would be
        # wrong: there is no commit or PR to cite for "a systemd unit exited
        # and its status column never got reconciled," and requiring one
        # would just make this sweep unable to do the one real thing it
        # exists for. Every real terminal-status write in this file (this
        # one, backfill_null_heartbeats() below, and dispatch_one()'s own
        # rejected_duplicate/sigterm_sent/killed/failed writes) still goes
        # through the SAME single underlying writer -- update_umr_task(),
        # inside the same real sbr._write_lock() -- so "single output gate"
        # is true at the write-FUNCTION level; it is the evidence-gate
        # specifically that is (correctly) scoped to cmd_mark_umr_terminal()'s
        # own AI/PM-claimed-completion use case, not universal.
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
    """Real task.yaml lookup for one umr_tasks row -- three real, ordered
    paths, not two, per a real gap found live re-verifying this against 5
    real owner_dispatch_gateway rows (UMR-20260807-112306-4e60, reconciling
    UMR-20260807-061238-ae93/070110-5ea7/070904-736a/035145-aa45/040704-992a):
    a plain source_trigger='owner_dispatch_gateway' row's own task_identity is
    a synthetic 'owner-task-<ts>-<pid>' string that was NEVER itself a
    TASKS_DIR directory name (confirmed live: 261 of 277 real such rows
    checked in an earlier cycle have no task.yaml under path 1 or 2 below --
    a real, honest 'no evidence' outcome, not a bug, for THAT set). But the
    real remediation/progress record for a row dispatched straight through
    resource_governor.submit() -> a real systemd unit does not require a
    separately-created 'adopted-reconcile-umr-...' task at all: the row's own
    real `unit_name` column (e.g. 'veridian-worker@task-20260807-081903-
    mandatory-execute-the-rebuild--do-not-in.service') already IS, minus the
    'veridian-worker@' prefix and '.service' suffix, the exact real TASKS_DIR
    directory name -- confirmed live for all 5 real rows this fix was built
    against (task_status_sync() had a correctly-keyed entry for every one,
    under exactly this derived id; path 1 above missed all 5 because each
    row's own task_identity is the synthetic parent id, not this child id;
    path 2 missed all 5 too because none of their real directory names
    contain 'reconcile-umr-'). Without this third path,
    backfill_null_heartbeats() silently defaulted every one of these 5 real,
    NULL-heartbeat, systemctl-confirmed-inactive rows to 'failed', even
    though 4 had a real OPEN PR (genuine forward progress) and 1 had a real
    MERGED PR (genuine completion) -- the exact false-negative class this
    module's own docstring already describes fixing for paths 1/2, just not
    yet for this third, more common systemd-dispatch shape.

      1. Direct: task_docs.get(row['task_identity']) -- the common case for
         a directly-dispatched worker task whose own task_identity IS its
         real TASKS_DIR directory name.
      2. unit_name-derived: for a systemd-dispatched row (unit_name matching
         'veridian-worker@<child_task_id>.service'), task_docs.get(<child_task_id>)
         -- the child task_identity resource_governor.submit() actually
         dispatched under, which a synthetic 'owner-task-...' parent
         task_identity (path 1) can never match directly.
      3. Fallback: any task.yaml whose own real directory name contains
         'reconcile-umr-<row's own umr_id, lowercased>' -- if more than one
         (a real re-attempt history), the most recently created_at wins.

    Returns the doc, or None if no path finds one (falls through to the
    original unconditional 'failed' behavior, unchanged)."""
    doc = task_docs.get(row["task_identity"])
    if doc is not None:
        return doc
    unit = row.get("unit_name")
    if unit and unit.startswith("veridian-worker@") and unit.endswith(".service"):
        derived_id = unit[len("veridian-worker@"):-len(".service")]
        doc = task_docs.get(derived_id)
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
            # UMR171945-0002 (single output gate audit): same real, direct
            # update_umr_task() write path as reconcile_stale_heartbeats()
            # above -- see that function's own real-evidence-basis comment
            # for why bypassing cmd_mark_umr_terminal()'s PR/commit-evidence
            # gate is correct here, not a gap (this "completed" verdict is
            # grounded in real, directly-observed systemd + task.yaml
            # cross-check state, a different but equally real evidence kind).
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
            # UMR171945-0002: same real, documented direct-write path as the
            # other two "completed" writers above -- evidence basis here is
            # external_ai_state_machine.py's own real, independently-checked
            # session status, not a PR/commit claim.
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


def _record_master_issue_if_new(issue_id, issue_identified, linked_umr_id=None, linked_ocid=None,
                                 linked_source=None, file_path=None):
    """UMR-20260808-074726-d105 (governing chain UMR-20260806-171945-5767):
    the 'software also has to write to it' half of the master_issue_tracker
    permanence directive -- a real deterministic gate/pipeline block records
    itself into the one real, permanent issue tracker, not just
    ATTENTION.md/umr_tasks' own per-task reason field (which every real
    caller here already writes separately -- this is additive, not a
    replacement).

    Deliberately dedup-checked by issue_id, never a bare unconditional
    add-issue call: a recurring trip of an already-known, already-tracked
    issue CLASS must never spam a fresh row per occurrence. Every real
    caller below passes a fixed, deterministic issue_id per issue class
    (never a timestamp/run-specific one) -- a genuinely new class inserts
    exactly once; every later recurrence of that same class is a real,
    verified no-op here, which is what keeps this scoped and minimal per
    the governing directive ('do not duplicate umr_tasks' own existing
    failure/reason recording, only add a row for genuinely new, distinct
    issue classes not already captured').

    Best-effort by design, same fail-open convention as
    _safe_superboss_register()/_append_attention() -- a problem recording
    this secondary bookkeeping entry must never crash or block the real
    gate/cascade logic calling it. Returns True if a new row was actually
    inserted, False otherwise (already existed, or recording itself
    failed).

    Ported forward 2026-08-08 (UMR-20260808-145030-f3d1, Point 19 of the
    task-gateway.py audit-24-points scope) from the unmerged
    feat/master-issue-tracker-add-issue-cli branch (originally
    task-20260807-074739/UMR-20260807-074739-dde3) -- that branch had gone
    stale behind main's later STOP_WORK_ORDER_TRUNK_REF hardening and could
    not be merged as-is, so this function and its two real call sites were
    carried forward by hand onto current main instead of a raw branch
    merge, per that task's own instruction to finish landing this real work
    rather than build a second, competing implementation.

    Real, confirmed bug fixed 2026-08-08 (independent tier1 review, PR #280
    round 4): the existence check used to run BEFORE acquiring
    sbr._write_lock(), only the insert itself was inside it -- a genuine
    check-then-act race window where two concurrent callers could both
    observe "not found" before either held the lock. The real, live schema
    already has a UNIQUE constraint on issue_id (master_issue_tracker's own
    CREATE TABLE), so this could never actually produce a silent duplicate
    row -- the second racing caller's insert would raise a real
    IntegrityError, caught by this function's own outer try/except below
    and returned as a normal False -- but the check-then-act pattern still
    didn't match this function's own documented "genuinely new class
    inserts exactly once" dedup contract. The existence check now runs
    INSIDE the same _write_lock() as the insert, making the whole
    check-and-insert genuinely atomic, not just eventually-safe-by-
    accident of the schema."""
    try:
        sbr, error = _safe_superboss_register("_record_master_issue_if_new")
        if error:
            return False
        conn = sbr._connect()
        sbr._ensure_master_issue_tracker_table(conn)
        with sbr._write_lock():
            existing = conn.execute(
                "SELECT tracker_id FROM master_issue_tracker WHERE issue_id=?", (issue_id,)
            ).fetchone()
            if existing:
                conn.close()
                return False
            sbr.add_master_issue(
                conn, issue_id, issue_identified, linked_umr_id=linked_umr_id,
                linked_ocid=linked_ocid, linked_source=linked_source or "resource_governor.py",
                file_path=file_path,
            )
            conn.commit()
        conn.close()
        return True
    except Exception as e:
        try:
            _append_attention(f"WARNING: _record_master_issue_if_new({issue_id!r}) failed: {e}")
        except Exception:
            pass
        return False


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
    # UMR-20260808-074726-d105 / UMR171945-0018 (real, currently-uncovered
    # event class found 2026-08-08): Stage 3's hard-stop cascade
    # (_write_emergency_stop() above) already writes a real
    # master_issue_tracker row for its own trip; Stage 2's own real
    # load-shedding cascade (this function) never did -- confirmed live
    # before this fix: grep of every _record_master_issue_if_new( call site
    # found none inside _shed_load(). A real, sustained metric overload
    # severe enough to SIGTERM a live running task is exactly the same
    # class of "software also has to write it down" event Stage 3's own
    # comment already argues for -- not a per-task condition (real per-shed
    # evidence lives in ATTENTION.md, same convention as Stage 3), so this
    # uses the same fixed, deduplicated issue_id/dedup contract, not a
    # fresh row per shed.
    _record_master_issue_if_new(
        "RG-EMERGENCY-STOP-SHEDLOAD",
        "resource_governor.py's real Stage 2 load-shedding cascade (_shed_load()) tripped: at least "
        f"one real metric stayed at/over {METRIC_THRESHOLD_PERCENT}% for "
        f"{EMERGENCY_CONSECUTIVE_TICKS_SHED} consecutive governor ticks, SIGTERMing the lowest-tier "
        "real running unit to free real resources. Real per-shed evidence (which unit, which tier, "
        "consecutive-tick counts, metrics) lives in ATTENTION.md -- not duplicated onto this row.",
        linked_umr_id="UMR-20260808-074726-d105",
        linked_source="resource_governor.py:_shed_load",
        file_path="scripts/resource_governor.py",
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
    # UMR-20260808-074726-d105: genuinely new, distinct issue class -- this
    # system-wide hard-stop cascade was previously recorded only in
    # ATTENTION.md/EMERGENCY_STOP_PATH, never in master_issue_tracker (checked
    # live before adding this), and it is not a per-task condition so it does
    # not duplicate any umr_tasks row's own reason field. See
    # _record_master_issue_if_new()'s own docstring for the dedup contract.
    _record_master_issue_if_new(
        "RG-EMERGENCY-STOP-HARDSTOP",
        "resource_governor.py's real emergency fail-safe cascade (Stage 3 hard-stop, "
        "_write_emergency_stop()) tripped: at least one real metric stayed at/over "
        f"{METRIC_THRESHOLD_PERCENT}% for {EMERGENCY_CONSECUTIVE_TICKS_HARDSTOP} consecutive "
        "governor ticks, halting all new dispatch until an operator runs "
        "`python3 scripts/resource_governor.py --clear-emergency-stop`. Real per-trip evidence "
        "(consecutive-tick counts, metrics) lives in ATTENTION.md -- not duplicated onto this row.",
        linked_umr_id="UMR-20260808-074726-d105",
        linked_source="resource_governor.py:_write_emergency_stop",
        file_path="scripts/resource_governor.py",
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
    ap.add_argument("--umr-staleness-scan", dest="umr_staleness_scan", action="store_true",
                     help="Point 14/16 (task-gateway.py audit-24-points): read-only scan for "
                          "queued+ts_dispatched-NULL rows older than 90min or running rows with a "
                          "heartbeat/ActiveEnterTimestamp older than 45min -- see "
                          "detect_stale_umr_rows()'s own docstring. Takes no remediation action.")
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
    ap.add_argument("--check-task-start-gate", action="store_true",
                     help="UMR-20260808-121334-e122 (Option B): real, shared stop-work-order + "
                          "resource-threshold check for a caller OUTSIDE dispatch_one()'s own queue "
                          "(currently: task-gateway.py's cmd_start, before it spawns a real systemd "
                          "unit) -- same real gate dispatch_one() applies to every queued row, exposed "
                          "as its own callable check rather than a parallel/divergent reimplementation. "
                          "Prints {\"blocked\": bool, \"check\": str|None, \"detail\": str|None} and "
                          "exits 0 regardless of blocked (the caller decides what a block means; this "
                          "command's own exit code is not the gate signal, exactly like --query-umr "
                          "above).")
    ap.add_argument("--task-kind", dest="task_kind", default="veridian_task_create",
                     help="only 'veridian_task_create' triggers the stop-work-order check (matches "
                          "dispatch_one()'s own real scoping) -- default matches cmd_start's only "
                          "real use, since task-gateway.py has no other task_kind")
    ap.add_argument("--title", default=None)
    ap.add_argument("--umr-id", dest="umr_id", default=None)
    args = ap.parse_args()

    if args.clear_emergency_stop:
        clear_emergency_stop()
        print(json.dumps({"ok": True, "cleared": True}))
        return

    if args.check_task_start_gate:
        resource_blocked, resource_detail, _metrics = resource_threshold_block_reason()
        if resource_blocked:
            print(json.dumps({"blocked": True, "check": "resource_threshold", "detail": resource_detail}))
            return
        if args.task_kind == "veridian_task_create":
            stop_work_detail = _stop_work_order_block_reason(
                args.task_kind, task_identity=args.task_identity, title=args.title, umr_id=args.umr_id,
            )
            if stop_work_detail:
                print(json.dumps({"blocked": True, "check": "stop_work_order", "detail": stop_work_detail}))
                return
        print(json.dumps({"blocked": False, "check": None, "detail": None}))
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
                                    task_identity=args.task_identity, query_text=args.search,
                                    umr_id=args.umr_id)
        # Point 2 (task-gateway.py audit-24-points, UMR-20260808-145030-f3d1):
        # this IS the other canonical query path (alongside task-gateway.py
        # status) -- log it. Best-effort: a broken log write must never break
        # this real --query-umr response, same fail-open convention as
        # _safe_superboss_register() itself.
        try:
            sbr._ensure_governance_cycle_log_table(conn)
            with sbr._write_lock():
                sbr.log_governance_cycle_event(
                    conn, "query", caller="resource_governor.py:--query-umr",
                    detail=f"limit={args.limit} status={args.status} search={args.search!r}",
                )
                conn.commit()
        except Exception:
            pass
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

    if args.umr_staleness_scan:
        stale = detect_stale_umr_rows()
        if stale:
            _append_attention(
                f"STALE-UMR-SCAN: {len(stale)} real umr_tasks row(s) matched Point 14/16 staleness "
                f"thresholds (queued+ts_dispatched NULL >{UMR_STALE_QUEUED_DISPATCH_NULL_SECONDS//60}min "
                f"or running+no-heartbeat >{UMR_STALE_RUNNING_HEARTBEAT_SECONDS//60}min): {stale}"
            )
        print(json.dumps({"stale_rows": stale}, default=str))
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
