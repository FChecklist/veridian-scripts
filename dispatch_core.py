#!/usr/bin/env python3
"""dispatch_core.py -- shared concurrency-gating library for the 3 consolidated
dispatch/status scripts (dispatch-tick.py, phase-continuation-tick.py,
status-remediation-tick.py). Closes the real root cause of the 2026-07-26
OOM-kill incident: 3 independent worker-spawn code paths (supervisor-sweep.sh's
`systemctl start veridian-supervisor@`, queue-dispatcher.py/module-queue-dispatcher.py's
`veridian-task.py create`, auto_phase_continuation.py's `task-gateway.py submit/start`)
each enforced their own concurrency accounting -- or none at all -- with no shared
lock or shared cap between them, so nothing on the box ever saw the REAL total number
of workers about to run before spawning another one.

Everything here is a primitive, not a policy: this module never itself decides
WHAT to dispatch (that stays in each script, unchanged) -- it only gates HOW MANY
things may be spawning at once, server-wide, across every script that imports it.

Usage at every real spawn call site (systemctl start / veridian-task.py create /
task-gateway.py submit+start), in all 3 consolidated scripts:

    with dispatch_core.acquire_dispatch_lock():
        if not dispatch_core.has_free_slot():
            print(f"SKIP (cap reached): {what}")
            continue
        <the real systemctl/veridian-task.py/task-gateway.py call>

Holding the lock across BOTH the free-slot check AND the actual spawn call is
what closes the TOCTOU race queue-dispatcher.py and module-queue-dispatcher.py
each had independently (separate lock files, separate caps) -- the spawned
unit is guaranteed to be reflected in the NEXT running_worker_count() call
(systemd's own unit list) before this lock is ever released to a second caller.
"""
import contextlib
import datetime
import fcntl
import os
import subprocess
import sys

import yaml

VERIDIAN_ROOT = os.environ.get("VERIDIAN_ROOT", "/opt/veridian")
AI_OS = os.environ.get("VERIDIAN_AI_OS_DIR", f"{VERIDIAN_ROOT}/ai-os")
SCRIPTS = os.environ.get("VERIDIAN_SCRIPTS_DIR", f"{VERIDIAN_ROOT}/scripts")
TASKS_DIR = os.environ.get("VERIDIAN_TASKS_DIR", f"{AI_OS}/tasks")

LOCK_DIR = os.environ.get("VERIDIAN_DISPATCH_LOCK_DIR", f"{AI_OS}/locks")
LOCK_PATH = os.environ.get("VERIDIAN_DISPATCH_LOCK_PATH", f"{LOCK_DIR}/worker-spawn.lock")

# DYNAMIC CONCURRENCY CAP (2026-08-01, Owner directive UMR-20260801-172407-ae58).
# Was a single fixed number before this (5, Owner-set 2026-07-20 as "~80%
# capacity on this 8-core box" -- itself replacing 3 independent numbers: see
# git history on this constant for that consolidation). A fixed number can't
# account for what's actually free on the box right now -- this session
# directly observed real headroom swing from load 3.18/1.6G swap free to load
# 15.84/283M swap free within the same hour, entirely independent of how many
# workers were dispatched. compute_dynamic_concurrency_cap() below replaces
# the constant with a live computation from real CPU/memory/swap/load,
# re-evaluated fresh on every has_free_slot() call (every real spawn site
# calls this immediately before spawning -- see this module's own docstring
# -- so this is continuous relative to the dispatch flow, not a one-time
# check, without needing a new standing daemon; a separate always-running
# monitor process would itself be exactly the kind of always-on 6th-role
# process this framework's own architecture deliberately avoids, see
# master-decompose.py's docstring for that precedent).
#
# Two thresholds, per the Owner's own explicit numbers:
#   HARD_CEILING_UTILIZATION_PCT (0.99) -- never cross, this is the point the
#     Owner says the server stops/freezes. At or above this on ANY tracked
#     resource, the cap is 0 -- no new dispatch at all, regardless of what
#     the rest of the math says.
#   TARGET_WORKING_UTILIZATION_PCT (0.80) -- meaningfully below the hard
#     ceiling. All headroom/cap math below aims to keep steady-state usage
#     under THIS number, not 0.99, because a single worker's own memory use
#     has been directly observed to ramp from near-zero to a 2GB peak +
#     1GB swap peak DURING its own run (2026-07-26 OOM RCA) -- a cap that
#     only left room down to 0.99 would have zero margin left to absorb that
#     kind of build/compile spike happening after a worker already started.
#
# PER_WORKER_MEMORY_BUDGET_BYTES (2GB) is not a new number invented for this --
# it's the exact same MemoryHigh=2G already enforced per-unit in the
# veridian-worker@.service template (2026-07-26 RCA fix, preserved unchanged
# by this change, see that file). Using the same figure here means the
# memory-based slot count and the per-unit cgroup ceiling agree with each
# other by construction, instead of this module guessing a different number.
#
# Explicit env override is still honored (VERIDIAN_DISPATCH_CONCURRENCY_CAP) --
# unchanged behavior for tests/manual overrides; when unset, production now
# always uses the live dynamic computation instead of a fixed default.
HARD_CEILING_UTILIZATION_PCT = 0.99
TARGET_WORKING_UTILIZATION_PCT = 0.80
PER_WORKER_MEMORY_BUDGET_BYTES = 2 * 1024 ** 3  # matches MemoryHigh=2G per-unit


def _read_meminfo_bytes():
    """Real /proc/meminfo values in bytes. MemAvailable (not MemFree) is the
    kernel's own estimate of memory available for new allocations without
    swapping -- already accounts for reclaimable cache/buffers, same figure
    `free -h`'s "available" column shows."""
    info = {}
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if ":" not in line:
                    continue
                key, rest = line.split(":", 1)
                parts = rest.split()
                if not parts:
                    continue
                info[key.strip()] = int(parts[0]) * 1024
    except (OSError, ValueError):
        pass
    return info


def compute_dynamic_concurrency_cap():
    """Live, real-host-capacity-based concurrency cap -- replaces the old
    fixed CONCURRENCY_CAP constant. Returns an int >= 0. Called fresh on
    every has_free_slot() invocation (never cached), so every real dispatch
    decision reflects current reality, not a snapshot from whenever the
    process started."""
    override = os.environ.get("VERIDIAN_DISPATCH_CONCURRENCY_CAP")
    if override is not None:
        return int(override)

    meminfo = _read_meminfo_bytes()
    mem_total = meminfo.get("MemTotal", 0)
    mem_available = meminfo.get("MemAvailable", 0)
    swap_total = meminfo.get("SwapTotal", 0)
    swap_free = meminfo.get("SwapFree", 0)

    mem_used_pct = (1 - (mem_available / mem_total)) if mem_total else 1.0
    swap_used_pct = (1 - (swap_free / swap_total)) if swap_total else 0.0

    # HARD CEILING -- Owner's own number, never cross. At/above 99% used on
    # either real resource, refuse every new dispatch outright.
    if mem_used_pct >= HARD_CEILING_UTILIZATION_PCT or swap_used_pct >= HARD_CEILING_UTILIZATION_PCT:
        return 0

    cpu_count = os.cpu_count() or 1
    try:
        load1, _, _ = os.getloadavg()
    except OSError:
        load1 = float(cpu_count)  # fail safe: assume fully loaded if unreadable

    # Real headroom to the TARGET working threshold (not the hard ceiling) --
    # this is the safety margin a build/compile spike needs room to be
    # absorbed in without ever touching the 99% hard ceiling.
    mem_used_bytes = mem_total - mem_available
    mem_headroom_bytes = max(0, (mem_total * TARGET_WORKING_UTILIZATION_PCT) - mem_used_bytes)
    memory_based_cap = int(mem_headroom_bytes // PER_WORKER_MEMORY_BUDGET_BYTES)

    cpu_headroom = max(0.0, (cpu_count * TARGET_WORKING_UTILIZATION_PCT) - load1)
    cpu_based_cap = int(cpu_headroom)

    return max(0, min(memory_based_cap, cpu_based_cap))

# Both unit templates this box actually spawns from a dispatch path (see KNOWN_CONTEXT
# item 2 in this task's spec): veridian-worker@ (queue-dispatcher.py/module-queue-
# dispatcher.py, via veridian-task.py create) and veridian-supervisor@ (supervisor-
# sweep.sh, direct systemctl start). Both consume the same real CPU/RAM budget on the
# same box, so both must count against the one shared cap -- counting only
# veridian-worker@ (the old, separate behavior) is exactly how supervisor-sweep.sh's
# own unconditional loop could -- and on 2026-07-26, did -- push the box over budget
# while every worker-side accounting looked fine.
_UNIT_GLOBS = ("veridian-worker@*", "veridian-supervisor@*")


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


@contextlib.contextmanager
def acquire_dispatch_lock():
    """One real flock, shared by every script that imports this module. Blocks
    (does not fail fast) until acquired -- callers hold it only for the short
    check-then-spawn critical section, so contention is brief. flock is
    auto-released if the holder is killed (matches the existing
    superboss-register.py._write_lock() convention this mirrors), so a killed
    caller can never leave dispatch permanently wedged."""
    os.makedirs(LOCK_DIR, exist_ok=True)
    with open(LOCK_PATH, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def running_worker_count():
    """Real, current count of BOTH veridian-worker@* and veridian-supervisor@*
    systemd --user units in state=running, counted together -- the single
    number every spawn call site in every consolidated script checks against
    the live dynamic cap (see compute_dynamic_concurrency_cap()) before
    spawning anything else."""
    total = 0
    for unit_glob in _UNIT_GLOBS:
        r = _run(["systemctl", "--user", "list-units", unit_glob, "--state=running", "--no-legend"])
        total += len([line for line in r.stdout.splitlines() if line.strip()])
    return total


def has_free_slot(cap=None):
    """True if a real spawn is currently allowed under the shared cap. Callers
    must check this WHILE holding acquire_dispatch_lock(), never before/after --
    checking outside the lock reintroduces the exact TOCTOU race this module
    exists to close. cap defaults to the live compute_dynamic_concurrency_cap()
    result (real host CPU/memory/swap/load, re-read fresh on every call) --
    pass an explicit cap only to override for tests."""
    cap = compute_dynamic_concurrency_cap() if cap is None else cap
    return running_worker_count() < cap


def task_status_sync():
    """Single walk of TASKS_DIR/*/task.yaml -- returns {task_id: task_doc}, where
    task_doc is the parsed task.yaml plus '_task_dir' (its directory) and
    '_has_review_json' (bool). Every consolidated script that needs any task.yaml
    field (dispatched-item status sync, pending_review/no-review.json discovery,
    etc.) reads this ONE dict instead of re-globbing/re-parsing TASKS_DIR itself --
    queue-dispatcher.py and module-queue-dispatcher.py each used to open one
    task.yaml file per queue item, individually, every run."""
    tasks = {}
    if not os.path.isdir(TASKS_DIR):
        return tasks
    for entry in sorted(os.listdir(TASKS_DIR)):
        task_dir = os.path.join(TASKS_DIR, entry)
        task_yaml_path = os.path.join(task_dir, "task.yaml")
        if not os.path.isfile(task_yaml_path):
            continue
        try:
            with open(task_yaml_path) as f:
                doc = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError):
            continue
        doc["_task_dir"] = task_dir
        doc["_has_review_json"] = os.path.isfile(os.path.join(task_dir, "review.json"))
        tasks[doc.get("id") or entry] = doc
    return tasks


# ---------------------------------------------------------------------------
# wiring_registry / knowledge_engine integration -- in-process, same importlib
# pattern generate_wiring_registry.py already uses to load superboss-register.py
# (its own docstring: "reuse scripts/superboss-register.py's own wiring_registry
# table DDL + upsert logic as the single source of truth, never redefine the
# schema here"). Never a subprocess call per event -- one process-local
# connection, opened lazily and reused for the whole tick.
# ---------------------------------------------------------------------------

_sbr = None


def _superboss_register():
    global _sbr
    if _sbr is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "superboss_register", os.path.join(SCRIPTS, "superboss-register.py"))
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sbr = _mod
    return _sbr


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _upsert_wiring_row(entity):
    sbr = _superboss_register()
    with sbr._write_lock():
        conn = sbr._connect()
        sbr._ensure_wiring_registry_table(conn)
        # _ensure_wiring_registry_table() above is a bare CREATE TABLE IF NOT
        # EXISTS -- a no-op against a pre-existing table, so it never widens an
        # already-created wiring_registry's entity_type CHECK constraint to
        # allow 'dispatch_event'. _migrate_wiring_registry_entity_types() is
        # the real migration that rebuilds the table in place when needed
        # (see its own docstring); called directly (not via the broader
        # _migrate_schema(), which also touches the unrelated system_index
        # table) since only wiring_registry's schema matters on this write
        # path. Both calls are idempotent no-ops once the table is current.
        sbr._migrate_wiring_registry_entity_types(conn)
        sbr.register_entity_row(conn, entity)
        conn.commit()
        conn.close()


def record_tick(script_name, status, dispatched_this_tick=0, extra=None):
    """Upserts ONE wiring_registry row (entity_type='cron_job') representing this
    script's own recurring-job entity -- same row updated every tick (stable
    entity_id per script), not a new row per run. relationships carries
    last_run_ts/last_run_status/dispatched_this_tick so the wiring graph always
    reflects the most recent real tick without needing a second, separate table.

    Best-effort: same "fails open" principle run-logged.sh already applies to its
    own instrumentation ("Logging is best-effort, never load-bearing for the job
    it wraps") -- a wiring_registry write failure is printed to stderr and
    swallowed, never allowed to fail the real dispatch tick it is only recording."""
    entity = {
        "entity_id": f"cron_job-{script_name}",
        "entity_type": "cron_job",
        "source_system": "server",
        "path": f"scripts/{script_name}.py",
        "relationships": [{
            "relationship_type": "last_run",
            "last_run_ts": _now_iso(),
            "last_run_status": status,
            "dispatched_this_tick": dispatched_this_tick,
            **(extra or {}),
        }],
        "last_verified_ts": _now_iso(),
        "verification_status": "VERIFIED_MATCH",
        "source_ref": ["dispatch_core.record_tick"],
    }
    try:
        _upsert_wiring_row(entity)
    except Exception as e:
        print(f"WARNING: record_tick wiring_registry write failed (non-fatal): {e}", file=sys.stderr)


def record_dispatch_event(task_id, dispatched_by, source_queue_or_plan, worker_unit, extra=None):
    """Upserts ONE wiring_registry row (entity_type='dispatch_event') per
    actually-dispatched task -- entity_id keyed on task_id, so one real dispatch
    gets one real row, distinct from record_tick()'s single per-script row.
    Best-effort, same as record_tick() above."""
    entity = {
        "entity_id": f"dispatch_event-{task_id}",
        "entity_type": "dispatch_event",
        "source_system": "server",
        "path": None,
        "relationships": [{
            "relationship_type": "dispatched",
            "dispatched_by": dispatched_by,
            "source_queue_or_plan": source_queue_or_plan,
            "worker_unit": worker_unit,
            "ts": _now_iso(),
            **(extra or {}),
        }],
        "last_verified_ts": _now_iso(),
        "verification_status": "VERIFIED_MATCH",
        "source_ref": [dispatched_by],
    }
    try:
        _upsert_wiring_row(entity)
    except Exception as e:
        print(f"WARNING: record_dispatch_event wiring_registry write failed (non-fatal): {e}", file=sys.stderr)
