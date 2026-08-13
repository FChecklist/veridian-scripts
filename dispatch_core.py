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
import json
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

# FIXED CONCURRENCY CAP + RESOURCE-AWARE BACKOFF.
#
# History, kept honest rather than rewritten: UMR-20260801-172407-ae58
# (2026-08-01) replaced this fixed constant with a dynamically COMPUTED
# ceiling that could rise above 5 when the box looked idle. UMR-20260801-
# 190119-ff34 (same day, later) reverted that after real evidence: swap hit
# 100% exhaustion with only 3 of the then-current 5-slot cap actually
# running. That is the key finding -- the box can run out of real headroom
# WITHOUT the slot count ever reaching whatever the ceiling is, fixed or
# dynamic, because per-worker memory/swap use ramps up DURING a worker's own
# run (a build/compile spike), not just at spawn time (see the 2026-07-26
# OOM RCA already documented on MemoryHigh/MemoryMax below). A smarter
# ceiling number does not fix that -- it can only ever gate what a NEW
# dispatch does, and a dynamic ceiling that computes higher than 5 on a
# seemingly-idle box makes the failure mode WORSE, not better, since it
# would permit even more concurrent build-shaped processes than the
# already-proven-risky fixed 5 does today.
#
# Final design: CONCURRENCY_CAP goes back to being a genuinely fixed
# constant (5, the Owner's own already-vetted number, never computed
# upward). has_resource_headroom() is a SEPARATE, independent veto check --
# real /proc/meminfo + load average, re-read fresh on every has_free_slot()
# call -- that can refuse a new dispatch even when the slot count is still
# under 5, if real headroom is already tight. Both must pass. This keeps the
# ceiling itself simple and proven while still directly addressing "even
# below the cap, don't dispatch into tight real headroom" -- the thing a
# fixed-only cap cannot do and a dynamic-ceiling-only design does not
# reliably do either (a dynamic ceiling only refuses new slots once it
# recomputes lower, which still lags a spike already in progress on already-
# running workers; an explicit veto with a real, conservative margin below
# the hard ceiling is the more legible, more conservative version of the
# same idea).
#
# Two thresholds:
#   HARD_CEILING_UTILIZATION_PCT (0.99) -- the Owner's own number, never
#     cross. At/above this on memory OR swap, refuse the dispatch outright.
#   BACKOFF_UTILIZATION_PCT (0.80) -- meaningfully below the hard ceiling.
#     The veto trips at/above THIS, not 0.99, because a single worker's own
#     memory has been directly observed to ramp from near-zero to a 2GB peak
#     + 1GB swap peak DURING its own run (2026-07-26 OOM RCA) -- refusing
#     only at 0.99 would leave zero margin for that kind of spike to be
#     absorbed once a new worker is already running.
#
# PER_WORKER_MEMORY_BUDGET_BYTES (2GB) is not a new number invented for
# this -- it's the exact same MemoryHigh=2G already enforced per-unit in the
# veridian-worker@.service template (2026-07-26 RCA fix, preserved
# unchanged by this change, see that file): the veto also refuses a new
# dispatch if less than one more worker's own budget of real headroom is
# left below the backoff threshold, not just a raw percentage check.
#
# Explicit env override (VERIDIAN_DISPATCH_CONCURRENCY_CAP) still works
# identically for tests/manual overrides -- unaffected by any of this.
CONCURRENCY_CAP = int(os.environ.get("VERIDIAN_DISPATCH_CONCURRENCY_CAP", "5"))

HARD_CEILING_UTILIZATION_PCT = 0.99
BACKOFF_UTILIZATION_PCT = 0.80
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


def has_resource_headroom_detail():
    """Real-time veto, independent of CONCURRENCY_CAP/running_worker_count():
    (ok, detail) where ok is True only if current real memory/swap/load
    headroom is enough to safely absorb one more worker-class (~2-3GB) task,
    checked fresh on every call (no cached/prior-tick snapshot -- every value
    below is read from /proc at call time). `detail` always carries the real
    check name plus the exact real numbers involved (never just True/False),
    so a caller logging a "deferred"/"frozen" outcome can say WHICH real
    metric tripped instead of a generic "no free slot" message -- added
    2026-08-06 (UMR-20260806-101839-688e) after the previous bool-only
    contract made a real load-average veto indistinguishable in the tick log
    from a genuinely exhausted CONCURRENCY_CAP slot count.

    This is what lets has_free_slot() refuse a new dispatch even when the
    slot count is still under the fixed cap -- see the module-level comment
    above for why the cap alone (fixed or dynamic) doesn't catch this."""
    meminfo = _read_meminfo_bytes()
    mem_total = meminfo.get("MemTotal", 0)
    mem_available = meminfo.get("MemAvailable", 0)
    swap_total = meminfo.get("SwapTotal", 0)
    swap_free = meminfo.get("SwapFree", 0)

    mem_used_pct = (1 - (mem_available / mem_total)) if mem_total else 1.0
    swap_used_pct = (1 - (swap_free / swap_total)) if swap_total else 0.0

    # HARD CEILING -- Owner's own number, never cross.
    if mem_used_pct >= HARD_CEILING_UTILIZATION_PCT:
        return False, {"check": "mem_hard_ceiling", "mem_used_pct": mem_used_pct,
                        "threshold_pct": HARD_CEILING_UTILIZATION_PCT}
    if swap_used_pct >= HARD_CEILING_UTILIZATION_PCT:
        return False, {"check": "swap_hard_ceiling", "swap_used_pct": swap_used_pct,
                        "threshold_pct": HARD_CEILING_UTILIZATION_PCT}

    # BACKOFF threshold -- meaningfully below the hard ceiling, tripped
    # first so a build/compile spike on an already-running worker still has
    # real room before 0.99.
    if mem_used_pct >= BACKOFF_UTILIZATION_PCT:
        return False, {"check": "mem_backoff", "mem_used_pct": mem_used_pct,
                        "threshold_pct": BACKOFF_UTILIZATION_PCT}
    if swap_used_pct >= BACKOFF_UTILIZATION_PCT:
        return False, {"check": "swap_backoff", "swap_used_pct": swap_used_pct,
                        "threshold_pct": BACKOFF_UTILIZATION_PCT}

    # Real headroom to the backoff threshold must fit at least one more
    # worker's own memory budget, not just be nonzero.
    mem_used_bytes = mem_total - mem_available
    mem_headroom_bytes = (mem_total * BACKOFF_UTILIZATION_PCT) - mem_used_bytes
    if mem_headroom_bytes < PER_WORKER_MEMORY_BUDGET_BYTES:
        return False, {"check": "mem_headroom_budget", "mem_headroom_bytes": mem_headroom_bytes,
                        "required_bytes": PER_WORKER_MEMORY_BUDGET_BYTES}

    cpu_count = os.cpu_count() or 1
    try:
        load1, _, _ = os.getloadavg()
    except OSError:
        # fail safe: refuse rather than assume idle if unreadable
        return False, {"check": "load1_unreadable"}
    load_threshold = cpu_count * BACKOFF_UTILIZATION_PCT
    if load1 >= load_threshold:
        return False, {"check": "load1_backoff", "load1": load1, "cpu_count": cpu_count,
                        "threshold": load_threshold}

    return True, {"check": "ok"}


def has_resource_headroom():
    """True only if current real memory/swap/load headroom is enough to
    safely absorb one more worker-class task. Thin bool wrapper around
    has_resource_headroom_detail() -- kept for the existing call sites/tests
    that only ever needed the boolean; use the _detail() variant when the
    real reason matters (e.g. tick-log diagnostics)."""
    ok, _ = has_resource_headroom_detail()
    return ok

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
    CONCURRENCY_CAP before spawning anything else."""
    total = 0
    for unit_glob in _UNIT_GLOBS:
        r = _run(["systemctl", "--user", "list-units", unit_glob, "--state=running", "--no-legend"])
        total += len([line for line in r.stdout.splitlines() if line.strip()])
    return total


def has_free_slot_detail(cap=None):
    """(ok, detail) real-reason variant of has_free_slot() -- see that
    function's docstring for the two conditions checked. `detail` names
    which of the two independent gates actually failed
    ("cap_exhausted" vs whatever has_resource_headroom_detail() reports),
    with the real numbers involved, so a "deferred" tick-log entry can say
    the true reason instead of always blaming "no free concurrency slot"
    even when the real cap has slots free and a resource veto is what
    actually blocked it (added 2026-08-06, UMR-20260806-101839-688e --
    this exact confusion cost real diagnostic time: running_worker_count()
    was 0/5 while every real tick logged "no free concurrency slot")."""
    cap = CONCURRENCY_CAP if cap is None else cap
    running = running_worker_count()
    if running >= cap:
        return False, {"check": "cap_exhausted", "running_worker_count": running, "cap": cap}
    ok, headroom_detail = has_resource_headroom_detail()
    if not ok:
        return False, headroom_detail
    return True, {"check": "ok", "running_worker_count": running, "cap": cap}


def has_free_slot(cap=None):
    """True if a real spawn is currently allowed. Two independent conditions
    must BOTH pass: the fixed CONCURRENCY_CAP slot count, and
    has_resource_headroom()'s real-time memory/swap/load veto -- the fixed
    cap alone does not catch a build/compile spike ramping on an already-
    running worker well before the slot count reaches its ceiling (see the
    module-level comment above CONCURRENCY_CAP for the real incident this
    caught). Callers must check this WHILE holding acquire_dispatch_lock(),
    never before/after -- checking outside the lock reintroduces the exact
    TOCTOU race this module exists to close. Pass an explicit cap only to
    override CONCURRENCY_CAP for tests -- has_resource_headroom() is not
    overridable, it always reads real host state.

    Thin bool wrapper around has_free_slot_detail() -- kept for the many
    existing `if not dispatch_core.has_free_slot():` call sites; use the
    _detail() variant when the real reason matters."""
    ok, _ = has_free_slot_detail(cap=cap)
    return ok


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


# ---------------------------------------------------------------------------
# Real-time journal instrumentation for every dispatch_one() decision
# (UMR-20260813-120054-4e66, addendum to UMR-20260806-171945-5767 /
# UMR-20260813-100854-e8a1: "restore the stalled dispatch pipeline").
#
# Real gap this closes: resource_governor.py's dispatch_one() already
# computes a real, detailed blocking reason every tick (has_free_slot_detail()
# -- itself the UMR-20260806-101839-688e fix for "cap_exhausted" vs a real
# resource-headroom veto being indistinguishable in the tick log) -- but that
# detail only ever reached /opt/veridian/ai-os/tasks/resource_governor_tick.log,
# a single, ever-growing flat file, appended to by
# resource_governor_tick_loop.sh's own `>> "$LOG" 2>&1` redirect. Two real,
# live-confirmed consequences (2026-08-13, this UMR's own evidence-gathering
# run): (1) `journalctl --user` on veridian-cron-dispatch-tick.service (the
# unit the standard PM sentinel protocol checks first) shows nothing useful
# about queued-row dispatch at all -- that unit's own dispatch-tick.py never
# calls dispatch_one()/touches umr_tasks queued rows; the real dispatcher is
# resource_governor.py's dispatch_one(), driven by the always-on
# veridian-governor-tick.service loop instead. (2) EVEN journalctl on that
# real unit is silent for this, because the tick loop's own subprocess
# redirect keeps every real per-tick decision out of the journal entirely.
# This is the same class of blind spot UMR-20260806-101839-688e already
# fixed once for the DATA (has_free_slot_detail()'s real check name) -- this
# closes the remaining VISIBILITY gap (the data existed, journalctl still
# could not show it).
# ---------------------------------------------------------------------------

# Small, closed, real vocabulary -- one category per real gate this
# codebase actually has, distinct from resource_governor.dispatch_one()'s
# own larger Rule-2 "action" vocabulary (classify_dispatch_outcome()) so a
# human grepping journalctl never needs to know every individual `action`
# string, just which of these real mechanisms is currently blocking
# dispatch. Every caller of log_dispatch_decision() gets the SAME mapping
# for the SAME real gate -- never invented ad hoc per call site.
_CAP_EXHAUSTED_CHECK = "cap_exhausted"
_RESOURCE_HEADROOM_CHECKS = {
    "mem_backoff", "swap_backoff", "mem_hard_ceiling", "swap_hard_ceiling",
    "mem_headroom_budget", "load1_backoff", "load1_unreadable",
}


def classify_blocking_category(result):
    """Real, deterministic, total mapping from a dispatch_one()-shaped
    result dict to ONE of a small, closed set of real blocking categories:

      cap_exhausted            -- dispatch_core's own fixed CONCURRENCY_CAP
                                   slot count is genuinely full (real,
                                   live systemd unit count -- see
                                   running_worker_count()).
      resource_headroom_veto   -- has_resource_headroom_detail()'s own real,
                                   independent memory/swap/load veto tripped
                                   (mem_backoff/swap_backoff/mem_hard_ceiling/
                                   swap_hard_ceiling/mem_headroom_budget/
                                   load1_backoff/load1_unreadable -- the
                                   exact ambiguity UMR-20260806-101839-688e
                                   already fixed inside has_free_slot_detail()
                                   itself; this is what finally makes that
                                   fix visible outside a flat log file).
      resource_threshold_gate  -- resource_governor.py's own separate,
                                   coarser 4-metric (cpu/ram/disk_io/network)
                                   EMERGENCY_STOP-adjacent gate
                                   (action in {"frozen", "emergency_stopped"}).
      stop_work_gate           -- the real issue #980 standing stop-work-
                                   order gate (action ==
                                   "blocked_stop_work_order").
      dedup_rejection          -- any of the real duplicate-dispatch guards
                                   (rejected_duplicate_pr /
                                   rejected_duplicate_reuse_verdict /
                                   superseded_by_ocid_evidence).
      superboss_unavailable    -- the Superboss Register DB itself was
                                   unreachable this tick.
      dispatched                -- a real spawn happened.
      queue_empty               -- next_queued_task() found nothing queued.
      would_dispatch             -- a dry-run pick (dispatch_one(dry_run=True)).
      other                     -- any action not enumerated above -- kept
                                   open so a future new real action can never
                                   raise here, only fall into this real,
                                   honestly-labeled catch-all (see this
                                   function's own test for the contract).

    Pure function, no I/O -- reads only `action` and, for a "deferred"
    action, `slot_detail`."""
    action = (result or {}).get("action")
    if action == "deferred":
        slot_detail = (result or {}).get("slot_detail") or {}
        check = slot_detail.get("check")
        if check == _CAP_EXHAUSTED_CHECK:
            return "cap_exhausted"
        # Any real headroom-veto check (enumerated or not -- has_free_slot_detail()
        # only ever returns "cap_exhausted" or a headroom_detail dict for
        # "deferred", so an unrecognized check name here is still, by
        # construction, that same real gate) is reported as one category.
        return "resource_headroom_veto"
    if action in ("frozen", "emergency_stopped"):
        return "resource_threshold_gate"
    if action == "blocked_stop_work_order":
        return "stop_work_gate"
    if action in ("rejected_duplicate_pr", "rejected_duplicate_reuse_verdict", "superseded_by_ocid_evidence"):
        return "dedup_rejection"
    if action == "superboss_unavailable":
        return "superboss_unavailable"
    if action == "dispatched":
        return "dispatched"
    if action == "idle":
        return "queue_empty"
    if action == "would_dispatch":
        return "would_dispatch"
    return "other"


def log_dispatch_decision(result, tag="veridian-dispatch-decision"):
    """Real, best-effort systemd-journal line for EVERY real dispatch_one()
    outcome -- written on every tick regardless of whether anything was
    actually dispatched this tick, so `journalctl --user -t
    veridian-dispatch-decision` shows the real, current blocking reason
    directly, without ever needing to grep resource_governor_tick.log (a
    single, ever-growing flat file) by hand. Piped through the real
    `systemd-cat` binary (verified live against this exact box's own
    journald, 2026-08-13) rather than a raw `logger` call, since
    systemd-cat's whole job is "write stdin to the journal, tagged" with no
    syslog-forwarding assumptions.

    Best-effort / fail-open, same "logging is never load-bearing for the
    real work it wraps" convention as record_tick()/record_dispatch_event()
    above -- a missing systemd-cat binary, a timeout, or any other real
    failure here is printed to stderr and swallowed, never allowed to break
    the real dispatch tick this only observes."""
    category = classify_blocking_category(result)
    payload = {
        "blocking_category": category,
        "action": (result or {}).get("action"),
        "umr_id": (result or {}).get("umr_id"),
        "detail": (result or {}).get("detail"),
        "slot_detail": (result or {}).get("slot_detail"),
        "ts": _now_iso(),
    }
    line = json.dumps(payload, default=str)
    try:
        subprocess.run(
            ["systemd-cat", "-t", tag, "--priority=info"],
            input=line, text=True, timeout=5, capture_output=True,
        )
    except Exception as e:
        print(f"WARNING: log_dispatch_decision journal write failed (non-fatal): {e}", file=sys.stderr)
