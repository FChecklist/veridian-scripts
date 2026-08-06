#!/usr/bin/env python3
"""generate_pm_report_v3.py -- real, pure, deterministic PM report generator.

UMR-20260805-181636-32f2 (parent UMR-20260802-165606-4413, OCID-020).

Replaces the AI-reasoned-every-10-minutes PM report generator that was a
real contributing factor to hitting the Owner's weekly Claude Code usage
limit this session. This script makes ZERO AI/LLM calls of any kind -- every
field below is either a direct read of a real local source (a command, a
file, a SQL query) or a pure deterministic function of those reads. No
narration, no summarization-by-model, no judgment calls beyond fixed
threshold/rule logic documented in this file.

Real sources, one per header/status field:
  - /proc/meminfo directly (chosen over parsing `free -h`'s human-formatted
    columns -- same underlying kernel data, but /proc/meminfo's KEY: VALUE kB
    lines are trivial to parse exactly and do not depend on `free`'s locale/
    column-width formatting) for RAM/swap.
  - `systemctl --user list-timers veridian-cron-dispatch-tick.timer` for
    dispatch-tick cron/timer status.
  - `systemctl --user list-units 'veridian-worker@*' --state=running` for
    the live parallel worker count.
  - the real STUCK_TASKS_HEARTBEAT.json file, same default path
    dispatch-tick.py itself uses ({AI_OS}/STUCK_TASKS_HEARTBEAT.json,
    overridable via VERIDIAN_STUCK_TASKS_HEARTBEAT_PATH -- same env var name
    dispatch-tick.py reads, so a test/deploy override affects both
    consistently).
  - `tmux has-session -t claude` for direct CLI status.
  - resource_governor.py's own EMERGENCY_STOP_PATH constant, imported (not
    hardcoded) from the real script at SCRIPTS/resource_governor.py.
  - `PRAGMA integrity_check` against superboss-register.sqlite, via
    superboss-register.py's own _connect() (read-only query, no write lock
    needed). This database has one real, confirmed-corrupted table
    (file_inventory, held under Hard Rule 8 pending an Owner decision --
    see the real pm_decisions_pending row). A whole-database
    integrity_check WILL fail because of it. That is real, current, expected
    behavior -- db_integrity_ok below reflects it honestly; this script does
    not work around it, patch over it, or touch file_inventory in any way.
  - real SQL against gtm_certification_categories, ocid_canonical_registry
    and umr_tasks (all read-only SELECTs) for the OCID-020 GTM, test-results
    and deterministic-gate sections.

Established real precedent this script reuses rather than re-inventing:
  - The pass/fail/"blocked_or_pending" three-way split for
    gtm_certification_categories.passed (1 / 0 / NULL) is not this script's
    own invention -- it is exactly classify() in the already-real, already-
    merged gtm_check_production_readiness_audit.py (category_index=25,
    commit c9da808). That script treats "blocked" and "pending" as ONE
    combined state (a category that is neither confirmed-passing nor
    confirmed-failing), not two separately-tracked ones -- there is no DB
    column that distinguishes them. This script follows that same, already-
    established real definition instead of inventing a new blocked-vs-
    pending split with no real backing data to justify where the line falls.
  - "deterministic gate" (per this script's own required self-contained
    definition, since MASTER-TRACKER.yaml and OS.yaml do NOT exist anywhere
    on this server -- confirmed via search before writing this docstring):
    the only pre-existing "gate" concept for GTM/OCID-020 readiness found
    anywhere in this codebase is gtm_check_production_readiness_audit.py's
    own category_index=25 synthesis (a fixed P0/P1/P2/P3 severity rubric,
    documented in that script, over the other 24 categories' own already-
    computed passed values). This script's "deterministic gate" section
    reuses that category's own already-computed, already-real result
    (passed / evidence_summary / evidence_json) verbatim, rather than
    re-deriving a second, competing gate definition. It is "deterministic"
    in the sense this script cares about: a pure SQL read of an
    already-computed value, zero AI/LLM judgment applied here or in the
    category-25 script that computed it.

THE ONE PIECE THIS SCRIPT DOES NOT HAVE A REAL SOURCE FOR:
  The Go-To-Market readiness score's real bucket-mapping formula and the
  exact NOT_READY / LIMITED_PILOT / BETA / PRODUCTION threshold rule are
  defined by "Reporting Contract V3" SKILL.md, which normally lives at
  C:\\Users\\Dell\\.claude\\scheduled-tasks\\veridian-server-sentinel\\SKILL.md
  -- a Windows path that does NOT exist anywhere on this Linux server
  (confirmed via a full pruned search before this script was written). This
  script does NOT invent plausible-looking thresholds and present them as if
  copied from that real source. See compute_readiness_bucket() below: a
  clearly-labeled placeholder that always returns a fixed, conservative,
  explicitly-labeled non-answer. The report text also marks this with a
  "PLACEHOLDER" marker next to the recommendation line so it cannot be
  mistaken for a real computed score.

Threshold values chosen BY THIS TASK (not from SKILL.md, which is
unavailable -- see above) for the auto-generated open-issues section. These
are this script's own reasonable interpretation of the parent instruction's
"roughly ten percent" / "roughly twenty five" wording, and are declared here
as plain constants so they are easy to find, review, and revise once the
real SKILL.md becomes available:
  - SWAP_FREE_PCT_WARN_THRESHOLD = 10.0   -- swap free% strictly below this
    opens an issue. Chosen as the literal reading of "roughly ten percent".
  - LOAD_1MIN_WARN_THRESHOLD = 25.0       -- 1-minute load average strictly
    above this opens an issue. Chosen as the literal reading of "roughly
    twenty five" (this is a load-average NUMBER, not a percentage -- e.g. a
    1-minute load average of 25 on any core count is heavy load by any
    normal reading, which is why the 1-minute figure specifically was
    picked over 5/15-minute: it reacts fastest to a real current spike).

No AI/LLM calls anywhere in this file. Every open-issue, delta and status
line below is produced by plain Python conditionals over the real reads
above -- nothing here is a model call, a summarization, or a narrated
judgment.

Usage:
  python3 generate_pm_report_v3.py [--no-db-write] [--json-out PATH]

  --no-db-write   Skip the pm_report_snapshots INSERT and the two report
                  file writes (still runs and prints the full report to
                  stdout). Used by the test suite; also usable for a dry-run
                  on a live box.
"""
import argparse
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Paths (all overridable via env vars, same convention as dispatch-tick.py /
# resource_governor.py, so tests can point every one of these at a temp file
# without touching the live server).
# ---------------------------------------------------------------------------
VERIDIAN_ROOT = os.environ.get("VERIDIAN_ROOT", "/opt/veridian")
AI_OS = os.environ.get("VERIDIAN_AI_OS_DIR", f"{VERIDIAN_ROOT}/ai-os")
SCRIPTS = os.environ.get("VERIDIAN_SCRIPTS_DIR", f"{VERIDIAN_ROOT}/scripts")

SBR_PATH = os.environ.get("VERIDIAN_SUPERBOSS_REGISTER_PY", f"{SCRIPTS}/superboss-register.py")
RESOURCE_GOVERNOR_PATH = os.environ.get(
    "VERIDIAN_RESOURCE_GOVERNOR_PY", f"{SCRIPTS}/resource_governor.py")

STUCK_TASKS_HEARTBEAT_PATH = os.environ.get(
    "VERIDIAN_STUCK_TASKS_HEARTBEAT_PATH", f"{AI_OS}/STUCK_TASKS_HEARTBEAT.json")

PROC_MEMINFO_PATH = os.environ.get("VERIDIAN_PM_REPORT_MEMINFO", "/proc/meminfo")
PROC_LOADAVG_PATH = os.environ.get("VERIDIAN_PM_REPORT_LOADAVG", "/proc/loadavg")

REPORT_LATEST_PATH = os.environ.get(
    "VERIDIAN_PM_REPORT_LATEST", f"{AI_OS}/reports/pm-report-latest.txt")
REPORT_HISTORY_PATH = os.environ.get(
    "VERIDIAN_PM_REPORT_HISTORY", f"{AI_OS}/reports/pm-report-history.log")

TMUX_SESSION_NAME = os.environ.get("VERIDIAN_PM_REPORT_TMUX_SESSION", "claude")
DISPATCH_TICK_TIMER_UNIT = "veridian-cron-dispatch-tick.timer"
WORKER_UNIT_GLOB = "veridian-worker@*"

# ---------------------------------------------------------------------------
# Open-issue thresholds -- this task's own documented interpretation. See
# the module docstring above for why these exact numbers were picked.
# ---------------------------------------------------------------------------
SWAP_FREE_PCT_WARN_THRESHOLD = 10.0
LOAD_1MIN_WARN_THRESHOLD = 25.0

REPORT_FORMAT_VERSION = "pm-report-v3-placeholder-gtm-score"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_module_from_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_cmd(argv, timeout=30):
    """Runs a real subprocess and returns (returncode, stdout, stderr).
    Never raises on a non-zero exit or missing binary -- callers decide what
    a given exit code/output means; this just captures it faithfully."""
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError as e:
        return 127, "", str(e)
    except subprocess.TimeoutExpired as e:
        return 124, e.stdout or "", str(e)


# ---------------------------------------------------------------------------
# Section 1: header/status, real local sources
# ---------------------------------------------------------------------------
def parse_meminfo(text):
    """Pure function: parses /proc/meminfo text (KEY: VALUE kB lines) into
    the fields this report needs. Kept separate from the file read so tests
    can feed synthetic text without a real /proc/meminfo."""
    vals = {}
    for line in text.splitlines():
        m = re.match(r"^(\w+):\s+(\d+)\s*kB\s*$", line)
        if m:
            vals[m.group(1)] = int(m.group(2))
    mem_total_kb = vals.get("MemTotal")
    mem_available_kb = vals.get("MemAvailable")
    swap_total_kb = vals.get("SwapTotal")
    swap_free_kb = vals.get("SwapFree")

    result = {
        "mem_total_mb": (mem_total_kb // 1024) if mem_total_kb is not None else None,
        "mem_available_mb": (mem_available_kb // 1024) if mem_available_kb is not None else None,
        "swap_total_mb": (swap_total_kb // 1024) if swap_total_kb is not None else None,
        "swap_free_mb": (swap_free_kb // 1024) if swap_free_kb is not None else None,
        "swap_free_pct": None,
    }
    if swap_total_kb is not None and swap_free_kb is not None and swap_total_kb > 0:
        result["swap_free_pct"] = round((swap_free_kb / swap_total_kb) * 100.0, 2)
    elif swap_total_kb == 0:
        # No swap configured at all: nothing to run low on. Report 100.0 so
        # the "swap free% < threshold" open-issue rule below never fires on
        # a box that simply has no swap.
        result["swap_free_pct"] = 100.0
    return result


def get_ram_swap():
    try:
        with open(PROC_MEMINFO_PATH) as f:
            text = f.read()
        return parse_meminfo(text)
    except OSError as e:
        return {"error": f"could not read {PROC_MEMINFO_PATH}: {e}"}


def parse_loadavg(text):
    parts = text.split()
    if len(parts) < 3:
        return {"error": f"unexpected /proc/loadavg format: {text!r}"}
    return {
        "load_1min": float(parts[0]),
        "load_5min": float(parts[1]),
        "load_15min": float(parts[2]),
    }


def get_load_average():
    try:
        with open(PROC_LOADAVG_PATH) as f:
            text = f.read()
        return parse_loadavg(text)
    except OSError as e:
        return {"error": f"could not read {PROC_LOADAVG_PATH}: {e}"}


def parse_dispatch_tick_timer_active(stdout, unit_name):
    """Pure function: `systemctl --user list-timers <unit>` prints a table
    with the unit name in it when the timer is loaded/scheduled, and prints
    only the header + "0 timers listed." when it is not. Deterministic
    substring check against the exact unit name -- no fuzzy matching."""
    return unit_name in stdout


def get_dispatch_tick_status():
    code, out, err = run_cmd(["systemctl", "--user", "list-timers", DISPATCH_TICK_TIMER_UNIT])
    active = code == 0 and parse_dispatch_tick_timer_active(out, DISPATCH_TICK_TIMER_UNIT)
    return {
        "dispatch_tick_active": bool(active),
        "raw_exit_code": code,
        "raw_stdout": out.strip(),
        "raw_stderr": err.strip(),
    }


def parse_worker_count(stdout):
    """Pure function: counts real 'loaded units listed' from
    `systemctl --user list-units 'veridian-worker@*' --state=running`
    output. Falls back to counting UNIT lines starting with the worker
    prefix if the summary line's wording ever changes, so a parsing edge
    case degrades to a still-correct count rather than a crash."""
    m = re.search(r"^(\d+) loaded units listed\.", stdout, re.MULTILINE)
    if m:
        return int(m.group(1))
    return len(re.findall(r"^\s*veridian-worker@\S+\.service\s", stdout, re.MULTILINE))


def get_worker_count():
    code, out, err = run_cmd(["systemctl", "--user", "list-units", WORKER_UNIT_GLOB, "--state=running"])
    if code != 0:
        return {"parallel_worker_count": None, "error": err.strip() or f"exit {code}"}
    return {"parallel_worker_count": parse_worker_count(out), "raw_stdout": out.strip()}


def get_stuck_tasks():
    try:
        with open(STUCK_TASKS_HEARTBEAT_PATH) as f:
            doc = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {"stuck_task_count": None, "error": f"could not read/parse {STUCK_TASKS_HEARTBEAT_PATH}: {e}"}
    stuck = doc.get("stuck_tasks")
    return {
        "stuck_task_count": len(stuck) if isinstance(stuck, list) else None,
        "heartbeat_generated_at": doc.get("generated_at"),
        "stuck_task_threshold_minutes": doc.get("stuck_task_threshold_minutes"),
        "real_task_counts": doc.get("real_task_counts"),
    }


def get_tmux_status():
    code, out, err = run_cmd(["tmux", "has-session", "-t", TMUX_SESSION_NAME])
    return {"tmux_session_alive": code == 0, "raw_exit_code": code}


def get_emergency_stop():
    try:
        governor = load_module_from_path("resource_governor", RESOURCE_GOVERNOR_PATH)
        path = governor.EMERGENCY_STOP_PATH
    except Exception as e:
        return {"emergency_stop_present": None, "error": f"could not import {RESOURCE_GOVERNOR_PATH}: {e}"}
    return {"emergency_stop_present": os.path.exists(path), "emergency_stop_path": path}


def get_db_integrity(sbr):
    """PRAGMA integrity_check against the whole DB, via superboss-register.py's
    own _connect(). Read-only -- no _write_lock() needed. Real, expected
    current behavior: this FAILS (returns more than just the single row
    ['ok']) because of the confirmed-corrupted file_inventory table, held
    under Hard Rule 8. This function does not touch file_inventory, does not
    special-case it out of the result, and does not retry/paper over the
    failure -- it reports exactly what PRAGMA integrity_check said."""
    try:
        conn = sbr._connect()
        rows = conn.execute("PRAGMA integrity_check").fetchall()
        conn.close()
    except sqlite3.Error as e:
        return {"db_integrity_ok": False, "integrity_check_rows": [], "error": str(e)}
    values = [r[0] for r in rows]
    ok = values == ["ok"]
    return {"db_integrity_ok": ok, "integrity_check_rows": values}


# ---------------------------------------------------------------------------
# Section 2/3: OCID-020 GTM, test results, deterministic gate
# ---------------------------------------------------------------------------
def classify_passed(passed):
    """Same three-way classification as the real, already-merged
    gtm_check_production_readiness_audit.py's classify() -- reused
    verbatim, not re-derived, per this script's own docstring."""
    if passed == 1:
        return "pass"
    if passed == 0:
        return "fail"
    return "blocked_or_pending"


def get_gtm_section(sbr):
    try:
        conn = sbr._connect()
        rows = conn.execute(
            "SELECT category_index, category_name, ocid_number, passed, "
            "evidence_summary, validated_at FROM gtm_certification_categories "
            "ORDER BY category_index"
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        return {"error": str(e)}

    categories = []
    counts = {"pass": 0, "fail": 0, "blocked_or_pending": 0}
    for r in rows:
        state = classify_passed(r["passed"])
        counts[state] += 1
        categories.append({
            "category_index": r["category_index"],
            "category_name": r["category_name"],
            "ocid_number": r["ocid_number"],
            "passed": r["passed"],
            "state": state,
            "evidence_summary": r["evidence_summary"],
            "validated_at": r["validated_at"],
        })

    gate_row = next((c for c in categories if c["category_index"] == 25), None)

    return {
        "gtm_pass_count": counts["pass"],
        "gtm_fail_count": counts["fail"],
        "gtm_blocked_or_pending_count": counts["blocked_or_pending"],
        "categories": categories,
        "deterministic_gate": {
            "definition": (
                "Reuses gtm_certification_categories.category_index=25's own "
                "already-computed result (gtm_check_production_readiness_audit.py's "
                "P0/P1/P2/P3 severity-rubric synthesis over the other 24 categories). "
                "MASTER-TRACKER.yaml and OS.yaml do not exist on this server "
                "(confirmed via search) so no other established 'deterministic gate' "
                "definition could be found to reuse instead."
            ),
            "category_25_row": gate_row,
            "gate_result": (gate_row["state"] if gate_row else "UNKNOWN (category 25 row not found)"),
        },
    }


def get_ocid_registry_section(sbr):
    try:
        conn = sbr._connect()
        total = conn.execute("SELECT COUNT(*) FROM ocid_canonical_registry").fetchone()[0]
        status_rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM ocid_canonical_registry GROUP BY status ORDER BY status"
        ).fetchall()
        complete = conn.execute(
            "SELECT COUNT(*) FROM ocid_canonical_registry WHERE is_fully_complete = 1"
        ).fetchone()[0]
        conn.close()
    except sqlite3.Error as e:
        return {"error": str(e)}
    return {
        "ocid_canonical_registry_total": total,
        "ocid_canonical_registry_fully_complete": complete,
        "ocid_canonical_registry_by_status": {r["status"]: r["n"] for r in status_rows},
    }


def get_umr_tasks_section(sbr):
    try:
        conn = sbr._connect()
        total = conn.execute("SELECT COUNT(*) FROM umr_tasks").fetchone()[0]
        status_rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM umr_tasks GROUP BY status ORDER BY status"
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        return {"error": str(e)}
    return {
        "umr_tasks_total": total,
        "umr_tasks_by_status": {r["status"]: r["n"] for r in status_rows},
    }


# ---------------------------------------------------------------------------
# Section 4: GTM readiness score placeholder -- SEE MODULE DOCSTRING.
# ---------------------------------------------------------------------------
def compute_readiness_bucket(gtm_section):
    """PLACEHOLDER -- NOT a real computed GTM readiness bucket.

    TODO(SKILL.md section 8, not available on this server): the real
    bucket-mapping formula and the exact NOT_READY / LIMITED_PILOT / BETA /
    PRODUCTION threshold rule live in "Reporting Contract V3" SKILL.md at
    C:\\Users\\Dell\\.claude\\scheduled-tasks\\veridian-server-sentinel\\SKILL.md
    (a Windows path). That file does not exist anywhere on this Linux
    server (confirmed via a full pruned search before this script was
    written). Nobody has copied its real section-8 formula/thresholds onto
    this box. DO NOT replace this function with invented-but-plausible-
    looking numbers/thresholds and present them as if they came from that
    real source -- that would be worse than this honest placeholder.

    This function deliberately ignores gtm_section's real counts (even
    though they are passed in, so a future real implementation has them
    ready to use) and always returns the same fixed, conservative,
    explicitly-labeled non-answer.
    """
    del gtm_section  # unused on purpose -- see docstring
    return {
        "bucket": "NOT_READY -- placeholder, real thresholds pending SKILL.md",
        "is_placeholder": True,
        "reason": (
            "compute_readiness_bucket() has no real source for the bucket-mapping "
            "formula/thresholds on this server (SKILL.md unavailable -- see "
            "module docstring and this function's own TODO). Returns a fixed "
            "conservative placeholder rather than an invented score."
        ),
    }


# ---------------------------------------------------------------------------
# Section 5: implementation summary (real deltas vs prior snapshot row)
# ---------------------------------------------------------------------------
DELTA_FIELDS = [
    "gtm_pass_count", "gtm_fail_count", "gtm_blocked_count", "gtm_pending_count",
    "mem_available_mb", "swap_free_pct", "load_1min", "load_5min", "load_15min",
    "dispatch_tick_active", "parallel_worker_count", "stuck_task_count",
    "tmux_session_alive", "emergency_stop_present", "db_integrity_ok",
    "umr_tasks_total", "ocid_canonical_registry_total",
]


def get_prior_snapshot(sbr):
    try:
        conn = sbr._connect()
        row = conn.execute(
            "SELECT * FROM pm_report_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
    except sqlite3.Error as e:
        return None, str(e)
    if row is None:
        return None, None
    return dict(row), None


def compute_deltas(prior, current):
    """Pure function: real +N / -N / unchanged / new (no prior row) per
    field. No narration -- just arithmetic and string formatting."""
    deltas = {}
    for field in DELTA_FIELDS:
        cur_val = current.get(field)
        if prior is None:
            deltas[field] = "new (no prior pm_report_snapshots row)"
            continue
        prior_val = prior.get(field)
        if prior_val is None or cur_val is None:
            deltas[field] = f"unknown (prior={prior_val!r} current={cur_val!r})"
            continue
        if isinstance(cur_val, bool) or isinstance(prior_val, bool):
            cur_b, prior_b = bool(cur_val), bool(prior_val)
            deltas[field] = "unchanged" if cur_b == prior_b else f"{prior_b} -> {cur_b}"
            continue
        try:
            diff = cur_val - prior_val
        except TypeError:
            deltas[field] = "unchanged" if cur_val == prior_val else f"{prior_val!r} -> {cur_val!r}"
            continue
        if diff == 0:
            deltas[field] = "unchanged"
        elif diff > 0:
            deltas[field] = f"+{diff}" if isinstance(diff, int) else f"+{diff:.2f}"
        else:
            deltas[field] = f"{diff}" if isinstance(diff, int) else f"{diff:.2f}"
    return deltas


# ---------------------------------------------------------------------------
# Section 6: open issues -- pure threshold/rule logic, zero AI judgment
# ---------------------------------------------------------------------------
def build_open_issues(gtm_section, db_integrity, ram_swap, load_avg):
    issues = []

    for cat in gtm_section.get("categories", []):
        if cat["state"] == "fail":
            issues.append({
                "kind": "gtm_category_failed",
                "category_index": cat["category_index"],
                "category_name": cat["category_name"],
                "ocid_number": cat["ocid_number"],
                "root_cause": cat["evidence_summary"],
            })

    if db_integrity.get("db_integrity_ok") is False:
        issues.append({
            "kind": "db_integrity_check_failed",
            "detail": (
                "PRAGMA integrity_check against superboss-register.sqlite did not "
                "return exactly ['ok']. This is the real, expected, current state "
                "(1 confirmed-corrupted table, file_inventory, held under Hard Rule "
                "8 pending an Owner decision -- see PM decisions pending section)."
            ),
            "integrity_check_rows": db_integrity.get("integrity_check_rows")
                or ([db_integrity["error"]] if db_integrity.get("error") else []),
        })

    swap_pct = ram_swap.get("swap_free_pct")
    if isinstance(swap_pct, (int, float)) and swap_pct < SWAP_FREE_PCT_WARN_THRESHOLD:
        issues.append({
            "kind": "swap_free_low",
            "detail": (
                f"swap_free_pct={swap_pct:.2f}% is below the "
                f"{SWAP_FREE_PCT_WARN_THRESHOLD}% threshold (see module docstring "
                "for why this exact number was chosen)."
            ),
        })

    load1 = load_avg.get("load_1min")
    if isinstance(load1, (int, float)) and load1 > LOAD_1MIN_WARN_THRESHOLD:
        issues.append({
            "kind": "load_average_high",
            "detail": (
                f"load_1min={load1} exceeds the {LOAD_1MIN_WARN_THRESHOLD} threshold "
                "(see module docstring for why this exact number was chosen)."
            ),
        })

    return issues


# ---------------------------------------------------------------------------
# Section 7: PM decisions pending -- read-only, verbatim
# ---------------------------------------------------------------------------
def _pm_decisions_pending_has_decision_type(conn):
    """True once pm_decisions_pending carries the Owner standing-mandate
    decision_type column (task-20260806-034817, cites
    UMR-20260805-185000-e94f -- see superboss-register.py's
    _migrate_pm_decisions_pending_owner_proposal_columns() for the write
    side). Checked live via PRAGMA table_info rather than assumed, so this
    read-only script degrades gracefully (never raises) against a DB that
    predates that migration -- same defensive spirit as this script's own
    db_integrity check never assuming a clean database."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(pm_decisions_pending)").fetchall()}
    return "decision_type" in cols


def get_pm_decisions_pending(sbr):
    try:
        conn = sbr._connect()
        # Once decision_type exists, exclude 'owner_proposal' rows -- those
        # surface separately in get_owner_proposals_pending() (Section 8)
        # below, same table, same 'status=open means awaiting a decision'
        # convention, but a distinct real workflow the Owner's standing
        # mandate keeps visually separate in the report. On a DB that
        # predates decision_type, no filter is applied (there are no
        # owner_proposal rows to exclude yet), preserving this function's
        # original behavior exactly.
        type_filter = " AND decision_type = 'pm_decision'" if _pm_decisions_pending_has_decision_type(conn) else ""
        rows = conn.execute(
            "SELECT id, opened_ts, title, detail, options_json, recommended_option, "
            f"related_umr, status FROM pm_decisions_pending WHERE status = 'open'{type_filter} "
            "ORDER BY id"
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        return {"error": str(e)}
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Section 8: Owner/AI child-UMR proposals pending -- read-only, verbatim
#
# Owner standing mandate (task-20260806-034817, cites
# UMR-20260805-185000-e94f): "thinking is by the Project Manager, execution
# is by AI agents" for real novel findings outside already-approved scope.
# Same real table (pm_decisions_pending), same read-only-section pattern as
# Section 7 above -- this is the report-side half of
# superboss-register.py's insert_owner_proposal()/decide_owner_proposal()/
# record_owner_proposal_completion(), so the PM sees real pending proposals
# every real report cycle without a separate real query of their own.
# ---------------------------------------------------------------------------
def get_owner_proposals_pending(sbr):
    try:
        conn = sbr._connect()
        if not _pm_decisions_pending_has_decision_type(conn):
            conn.close()
            return []  # DB predates the Owner-proposal columns -- no real rows can exist yet
        rows = conn.execute(
            "SELECT id, opened_ts, title AS issue, detail AS proposal, related_umr AS child_umr, "
            "status FROM pm_decisions_pending WHERE status = 'open' AND decision_type = 'owner_proposal' "
            "ORDER BY id"
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        return {"error": str(e)}
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Assembly + rendering
# ---------------------------------------------------------------------------
def build_report(sbr):
    ram_swap = get_ram_swap()
    load_avg = get_load_average()
    dispatch = get_dispatch_tick_status()
    workers = get_worker_count()
    stuck = get_stuck_tasks()
    tmux = get_tmux_status()
    estop = get_emergency_stop()
    db_integrity = get_db_integrity(sbr)

    gtm_section = get_gtm_section(sbr)
    ocid_section = get_ocid_registry_section(sbr)
    umr_section = get_umr_tasks_section(sbr)

    readiness = compute_readiness_bucket(gtm_section)

    prior, prior_err = get_prior_snapshot(sbr)

    current_flat = {
        "gtm_pass_count": gtm_section.get("gtm_pass_count"),
        "gtm_fail_count": gtm_section.get("gtm_fail_count"),
        # NOTE: pm_report_snapshots' schema (already-migrated, real) has
        # separate gtm_blocked_count / gtm_pending_count columns, but the
        # only real established source data (gtm_certification_categories)
        # does not distinguish blocked from pending (see module docstring).
        # Both columns are written with the same combined
        # blocked_or_pending count so neither column is silently left NULL,
        # and this collapse is documented here rather than hidden.
        "gtm_blocked_count": gtm_section.get("gtm_blocked_or_pending_count"),
        "gtm_pending_count": gtm_section.get("gtm_blocked_or_pending_count"),
        "mem_available_mb": ram_swap.get("mem_available_mb"),
        "swap_free_pct": ram_swap.get("swap_free_pct"),
        "load_1min": load_avg.get("load_1min"),
        "load_5min": load_avg.get("load_5min"),
        "load_15min": load_avg.get("load_15min"),
        "dispatch_tick_active": dispatch.get("dispatch_tick_active"),
        "parallel_worker_count": workers.get("parallel_worker_count"),
        "stuck_task_count": stuck.get("stuck_task_count"),
        "tmux_session_alive": tmux.get("tmux_session_alive"),
        "emergency_stop_present": estop.get("emergency_stop_present"),
        "db_integrity_ok": db_integrity.get("db_integrity_ok"),
        "umr_tasks_total": umr_section.get("umr_tasks_total"),
        "ocid_canonical_registry_total": ocid_section.get("ocid_canonical_registry_total"),
    }
    deltas = compute_deltas(prior, current_flat)

    open_issues = build_open_issues(gtm_section, db_integrity, ram_swap, load_avg)
    decisions = get_pm_decisions_pending(sbr)
    owner_proposals = get_owner_proposals_pending(sbr)

    report = {
        "report_format_version": REPORT_FORMAT_VERSION,
        "generated_at": _now_iso(),
        "umr": "UMR-20260805-181636-32f2",
        "parent_umr": "UMR-20260802-165606-4413",
        "ocid": "OCID-020",
        "header_status": {
            "ram_swap": ram_swap,
            "load_average": load_avg,
            "dispatch_tick": dispatch,
            "parallel_workers": workers,
            "stuck_tasks": stuck,
            "tmux": tmux,
            "emergency_stop": estop,
            "db_integrity": db_integrity,
        },
        "ocid_020_gtm_section": gtm_section,
        "ocid_canonical_registry_section": ocid_section,
        "umr_tasks_section": umr_section,
        "gtm_readiness": readiness,
        "implementation_summary": {
            "prior_snapshot_found": prior is not None,
            "prior_snapshot_error": prior_err,
            "prior_snapshot_ts": prior.get("ts") if prior else None,
            "deltas": deltas,
        },
        "open_issues": open_issues,
        "pm_decisions_pending": decisions,
        "owner_proposals_pending": owner_proposals,
        "current_flat_fields": current_flat,
        "thresholds": {
            "SWAP_FREE_PCT_WARN_THRESHOLD": SWAP_FREE_PCT_WARN_THRESHOLD,
            "LOAD_1MIN_WARN_THRESHOLD": LOAD_1MIN_WARN_THRESHOLD,
        },
    }
    return report


def render_report_text(report):
    lines = []

    def h(title):
        lines.append("")
        lines.append("=" * 78)
        lines.append(title)
        lines.append("=" * 78)

    lines.append(f"PM REPORT v3 (real, pure, deterministic -- zero AI/LLM calls)")
    lines.append(f"generated_at: {report['generated_at']}")
    lines.append(f"umr: {report['umr']}  parent_umr: {report['parent_umr']}  ocid: {report['ocid']}")

    h("1. HEADER / STATUS")
    hs = report["header_status"]
    rs = hs["ram_swap"]
    la = hs["load_average"]
    lines.append(f"RAM available: {rs.get('mem_available_mb')} MB / total {rs.get('mem_total_mb')} MB")
    lines.append(f"Swap free: {rs.get('swap_free_mb')} MB / total {rs.get('swap_total_mb')} MB "
                  f"({rs.get('swap_free_pct')}%)")
    lines.append(f"Load average: 1m={la.get('load_1min')} 5m={la.get('load_5min')} 15m={la.get('load_15min')}")
    lines.append(f"dispatch-tick timer active: {hs['dispatch_tick'].get('dispatch_tick_active')}")
    lines.append(f"Parallel workers running: {hs['parallel_workers'].get('parallel_worker_count')}")
    lines.append(f"Stuck tasks (STUCK_TASKS_HEARTBEAT.json): {hs['stuck_tasks'].get('stuck_task_count')} "
                  f"(heartbeat generated_at={hs['stuck_tasks'].get('heartbeat_generated_at')})")
    lines.append(f"tmux session 'claude' alive: {hs['tmux'].get('tmux_session_alive')}")
    lines.append(f"EMERGENCY_STOP present: {hs['emergency_stop'].get('emergency_stop_present')} "
                  f"(path={hs['emergency_stop'].get('emergency_stop_path')})")
    lines.append(f"DB integrity_check OK: {hs['db_integrity'].get('db_integrity_ok')} "
                  f"(rows={hs['db_integrity'].get('integrity_check_rows')})")

    h("2. OCID-020 GTM CERTIFICATION SECTION")
    gtm = report["ocid_020_gtm_section"]
    lines.append(f"pass={gtm.get('gtm_pass_count')} fail={gtm.get('gtm_fail_count')} "
                  f"blocked_or_pending={gtm.get('gtm_blocked_or_pending_count')} "
                  f"(blocked/pending is a single combined state in the real source data "
                  f"-- see module docstring)")
    ocid = report["ocid_canonical_registry_section"]
    lines.append(f"ocid_canonical_registry: total={ocid.get('ocid_canonical_registry_total')} "
                  f"fully_complete={ocid.get('ocid_canonical_registry_fully_complete')}")
    lines.append(f"ocid_canonical_registry by status: {ocid.get('ocid_canonical_registry_by_status')}")

    h("3. TEST RESULTS + DETERMINISTIC GATE")
    for cat in gtm.get("categories", []):
        lines.append(f"  [{cat['category_index']:2d}] {cat['category_name']:<28s} "
                      f"state={cat['state']:<18s} validated_at={cat['validated_at']}")
    gate = gtm.get("deterministic_gate", {})
    lines.append("")
    lines.append(f"Deterministic gate result: {gate.get('gate_result')}")
    lines.append(f"Deterministic gate definition: {gate.get('definition')}")

    h("4. GO-TO-MARKET READINESS SCORE + RECOMMENDATION")
    readiness = report["gtm_readiness"]
    marker = " \u26a0 PLACEHOLDER" if readiness.get("is_placeholder") else ""
    lines.append(f"Recommendation: {readiness.get('bucket')}{marker}")
    lines.append(f"Reason: {readiness.get('reason')}")

    h("5. IMPLEMENTATION SUMMARY (deltas since prior report)")
    impl = report["implementation_summary"]
    if not impl["prior_snapshot_found"]:
        extra = f" (error: {impl['prior_snapshot_error']})" if impl.get("prior_snapshot_error") else ""
        lines.append(f"No prior pm_report_snapshots row found -- this is the first real run.{extra}")
    else:
        lines.append(f"Prior snapshot ts: {impl['prior_snapshot_ts']}")
    for field, delta in impl["deltas"].items():
        lines.append(f"  {field:<28s} {delta}")

    h("6. OPEN ISSUES (auto-generated, pure threshold/rule logic)")
    issues = report["open_issues"]
    if not issues:
        lines.append("None.")
    for i, issue in enumerate(issues, 1):
        if issue["kind"] == "gtm_category_failed":
            lines.append(f"  {i}. [GTM FAIL] category {issue['category_index']} "
                          f"'{issue['category_name']}' ({issue['ocid_number']}): {issue['root_cause']}")
        elif issue["kind"] == "db_integrity_check_failed":
            lines.append(f"  {i}. [DB INTEGRITY] {issue['detail']} rows={issue['integrity_check_rows']}")
        elif issue["kind"] == "swap_free_low":
            lines.append(f"  {i}. [SWAP LOW] {issue['detail']}")
        elif issue["kind"] == "load_average_high":
            lines.append(f"  {i}. [LOAD HIGH] {issue['detail']}")
        else:
            lines.append(f"  {i}. [{issue['kind']}] {issue}")

    h("7. PM DECISION REQUIRED (read-only from pm_decisions_pending)")
    decisions = report["pm_decisions_pending"]
    if isinstance(decisions, dict) and "error" in decisions:
        lines.append(f"ERROR reading pm_decisions_pending: {decisions['error']}")
    elif not decisions:
        lines.append("None open.")
    else:
        for d in decisions:
            lines.append(f"  #{d['id']} [{d['opened_ts']}] {d['title']}")
            lines.append(f"      detail: {d['detail']}")
            lines.append(f"      recommended_option: {d['recommended_option']}")
            lines.append(f"      related_umr: {d['related_umr']}")

    h("8. AI PROPOSALS AWAITING PM DECISION (read-only from pm_decisions_pending, "
      "decision_type='owner_proposal')")
    proposals = report["owner_proposals_pending"]
    if isinstance(proposals, dict) and "error" in proposals:
        lines.append(f"ERROR reading owner proposals: {proposals['error']}")
    elif not proposals:
        lines.append("None open.")
    else:
        for pr in proposals:
            lines.append(f"  #{pr['id']} [{pr['opened_ts']}] child_umr={pr['child_umr']}")
            lines.append(f"      issue: {pr['issue']}")
            lines.append(f"      proposed: {pr['proposal']}")

    lines.append("")
    lines.append("=" * 78)
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# DB write + file writes
# ---------------------------------------------------------------------------
def write_snapshot_row(sbr, report):
    fields = report["current_flat_fields"]
    conn = sbr._connect()
    with sbr._write_lock():
        conn.execute(
            """
            INSERT INTO pm_report_snapshots (
                ts, gtm_pass_count, gtm_fail_count, gtm_blocked_count, gtm_pending_count,
                mem_available_mb, swap_free_pct, load_1min, load_5min, load_15min,
                dispatch_tick_active, parallel_worker_count, stuck_task_count,
                tmux_session_alive, emergency_stop_present, db_integrity_ok,
                umr_tasks_total, ocid_canonical_registry_total, report_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report["generated_at"],
                fields["gtm_pass_count"], fields["gtm_fail_count"],
                fields["gtm_blocked_count"], fields["gtm_pending_count"],
                fields["mem_available_mb"], fields["swap_free_pct"],
                fields["load_1min"], fields["load_5min"], fields["load_15min"],
                int(bool(fields["dispatch_tick_active"])) if fields["dispatch_tick_active"] is not None else None,
                fields["parallel_worker_count"], fields["stuck_task_count"],
                int(bool(fields["tmux_session_alive"])) if fields["tmux_session_alive"] is not None else None,
                int(bool(fields["emergency_stop_present"])) if fields["emergency_stop_present"] is not None else None,
                int(bool(fields["db_integrity_ok"])) if fields["db_integrity_ok"] is not None else None,
                fields["umr_tasks_total"], fields["ocid_canonical_registry_total"],
                json.dumps(report),
            ),
        )
        conn.commit()
    conn.close()


def write_report_files(report_text):
    os.makedirs(os.path.dirname(REPORT_LATEST_PATH), exist_ok=True)
    with open(REPORT_LATEST_PATH, "w") as f:
        f.write(report_text)
    os.makedirs(os.path.dirname(REPORT_HISTORY_PATH), exist_ok=True)
    with open(REPORT_HISTORY_PATH, "a") as f:
        f.write(report_text)
        f.write("\n")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-db-write", action="store_true",
                         help="Skip pm_report_snapshots INSERT and report file writes (still prints report).")
    parser.add_argument("--json-out", default=None, help="Also write the full report as JSON to this path.")
    args = parser.parse_args(argv)

    sbr = load_module_from_path("superboss_register", SBR_PATH)

    report = build_report(sbr)
    text = render_report_text(report)
    print(text)

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(report, f, indent=2, default=str)

    if not args.no_db_write:
        write_report_files(text)
        write_snapshot_row(sbr, report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
