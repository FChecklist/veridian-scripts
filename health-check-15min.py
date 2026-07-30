#!/usr/bin/env python3
"""
VERIDIAN-DEV 15-minute health check. Zero AI cost -- pure deterministic
script (L0 tier: no model call of any kind). Checks:
  - systemd status of every veridian-worker@*/veridian-supervisor@* unit
  - staleness of every in_progress task's checkpoint vs its unit's state
  - Mother Router / AI router registry reachability (row counts, via psql)
  - server health (disk, memory, load)
  - best-effort Claude-CLI-quota-exhaustion signature scan (no real quota
    API exists in this Claude Code CLI version as of 2026-07-19 -- this is
    a PROXY signal via known failure-message patterns, not a real quota
    check; documented as such, not oversold)
Appends one JSON line to health-15min.jsonl, one human line to
health-15min.log, and any anomaly to ATTENTION.md. Self-rotates: keeps only
the last 700 lines (~1 week at 15-min cadence) of each log.
"""
import fcntl
import hashlib
import time
import json
import os
import re
import subprocess
import sys
import sqlite3
from datetime import datetime, timezone, timedelta

LOG_DIR = "/opt/veridian/ai-os/logs"
LOCK_PATH = "/opt/veridian/ai-os/.health-check-15min.lock"
# Governance item 6: crontab still fires this script every 15 min (mechanically
# enforced -- item 50's check_crontab_unauthorized_change() fails closed on any
# crontab diff without a verified Owner approval citation). Rather than touch
# the schedule, main() below loops internally so each individual check runs on
# a ~1-min cadence, then returns control to cron for the next 15-min tick.
# LOOP_SPAN_SECONDS stays under 15 min (not the full 900s) to leave headroom
# for a slow cycle plus the lock-release/process-exit before the next cron
# tick fires -- the two are independently safe (see LOCK_PATH above) but
# keeping them non-adjacent avoids needlessly relying on that second layer.
# Overridable via env vars so this can be end-to-end tested with a short span
# without touching production cadence.
# 2026-07-29 cron-consolidation-phase6 review: deliberately NOT wired into
# dispatch_core.py's shared worker-spawn lock. Reasoning, documented per that
# task's own guidance to skip the lock on trivially read-only/idempotent
# scripts: every real check here (check_systemd_units, check_tasks) only
# READS systemd/task.yaml state, never starts/stops/restarts a unit -- there
# is no spawn call site to gate. The one real concurrency risk (this script's
# own internal 13-min loop overrunning into the next cron tick) already has
# its own dedicated, pre-existing fcntl.flock(LOCK_NB) self-overlap guard
# (see main() below) that skips a second invocation outright rather than
# queue behind it. Adding the shared worker-spawn lock on top would only
# serialize this read-only monitor against real spawn-gating scripts for no
# safety benefit.
LOOP_SPAN_SECONDS = int(os.environ.get("HEALTH_CHECK_LOOP_SPAN_SECONDS", 13 * 60))
CHECK_INTERVAL_SECONDS = int(os.environ.get("HEALTH_CHECK_INTERVAL_SECONDS", 60))
MAX_ITERATIONS = 20  # backstop -- elapsed-time check above is the real bound
TASKS_DIR = "/opt/veridian/ai-os/tasks"
JSONL_LOG = os.path.join(LOG_DIR, "health-15min.jsonl")
TEXT_LOG = os.path.join(LOG_DIR, "health-15min.log")
ATTENTION_FILE = os.path.join(LOG_DIR, "ATTENTION.md")
NOTIFY_SCRIPT = "/opt/veridian/scripts/notify-owner.py"
MAX_LINES = 700
STALE_THRESHOLD_MIN = 25  # 15-min cadence + 1 grace period
FAILURE_RATE_THRESHOLD = 0.20  # 2026-07-20, constitution-audit gap #3
ANOMALY_ESCALATION_STREAK = 3  # consecutive 15-min cycles (45 min) before escalating
DISK_WARNING_PCT = 75  # governance item 10: distinct WARN tier below the DISK/MEM anomaly threshold (90)
MEM_WARNING_PCT = 75
ENV_FILE = "/opt/veridian/repos/compliance-tracker/.env.local"
CREDIT_LEDGER_PATH = "/opt/veridian/ai-os/memory/credit-ledger.sqlite"

EXHAUSTION_PATTERNS = [
    r"credit balance is too low",
    r"rate.?limit",
    r"\b429\b",
    r"quota exceeded",
    r"insufficient.?quota",
]


def sh(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except Exception as e:
        return "", str(e), -1


def get_env_value(key, path=ENV_FILE):
    try:
        with open(path) as f:
            for line in f:
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return None


def check_systemd_units():
    out, _, _ = sh("systemctl --user list-units 'veridian-worker@*' 'veridian-supervisor@*' 'veridian-glm-proxy.service' 'veridian-docworker@*' --all --no-legend --plain 2>/dev/null")
    units = []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 4:
            unit, load, active, sub = parts[0], parts[1], parts[2], parts[3]
            units.append({"unit": unit, "load": load, "active": active, "sub": sub})
    running = sum(1 for u in units if u["active"] == "active")
    failed = [u for u in units if u["active"] == "failed" or u["sub"] == "failed"]
    return {"total": len(units), "running": running, "failed_count": len(failed), "failed_units": [u["unit"] for u in failed]}


def check_tasks():
    """Read every task.yaml's status + last_checkpoint_at without requiring PyYAML."""
    results = {"in_progress": 0, "completed": 0, "failed": 0, "blocked": 0,
               "awaiting_human_approval": 0, "other": 0, "stalled": [],
               "blocked_task_ids": []}
    if not os.path.isdir(TASKS_DIR):
        return results
    now = datetime.now(timezone.utc)
    for task_id in os.listdir(TASKS_DIR):
        yaml_path = os.path.join(TASKS_DIR, task_id, "task.yaml")
        if not os.path.isfile(yaml_path):
            continue
        try:
            with open(yaml_path) as f:
                content = f.read()
        except Exception:
            continue
        status_m = re.search(r"^status:\s*(\S+)", content, re.MULTILINE)
        status = status_m.group(1).strip("'\"") if status_m else "other"
        results[status] = results.get(status, 0) + 1
        if status == "in_progress":
            cp_m = re.search(r"^last_checkpoint_at:\s*'?([0-9T:.+-]+)'?", content, re.MULTILINE)
            if cp_m:
                try:
                    cp_time = datetime.fromisoformat(cp_m.group(1).replace("Z", "+00:00"))
                    age_min = (now - cp_time).total_seconds() / 60
                    if age_min > STALE_THRESHOLD_MIN:
                        results["stalled"].append({"task_id": task_id, "checkpoint_age_min": round(age_min, 1)})
                except Exception:
                    pass
        elif status == "blocked":
            results["blocked_task_ids"].append(task_id)
    return results


def get_prev_blocked_ids():
    """Blocked-task ids as of the previous health-check cycle (read before this
    cycle's record is appended to JSONL_LOG), used to detect a task that is
    still blocked two cycles in a row (30 min) rather than a fresh/transient
    block."""
    if not os.path.isfile(JSONL_LOG):
        return set()
    try:
        with open(JSONL_LOG) as f:
            lines = [l for l in f if l.strip()]
        if not lines:
            return set()
        last = json.loads(lines[-1])
        return set(last.get("tasks", {}).get("blocked_task_ids", []))
    except Exception:
        return set()


def notify_owner(subject, body, dedupe_key):
    """Best-effort call to notify-owner.py. Fail-open: a notification problem
    must never break the health check itself."""
    try:
        subprocess.run(
            [sys.executable, NOTIFY_SCRIPT, "--subject", subject, "--body", body, "--dedupe-key", dedupe_key],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        pass


def get_prev_anomaly_streaks():
    """Consecutive-occurrence count per anomaly signature as of the previous
    cycle, read the same way get_prev_blocked_ids() reads previous state --
    from the last JSONL record, before this cycle's record is appended."""
    if not os.path.isfile(JSONL_LOG):
        return {}
    try:
        with open(JSONL_LOG) as f:
            lines = [l for l in f if l.strip()]
        if not lines:
            return {}
        last = json.loads(lines[-1])
        return last.get("anomaly_streaks", {})
    except Exception:
        return {}


def send_anomaly_notifications(anomalies, prev_streaks):
    """Owner email escalation (2026-07-23): ATTENTION.md is kept exactly as
    before -- this only ADDS a real email so a genuinely new anomaly doesn't
    sit unread. One email per distinct anomaly signature (digits stripped so
    a changing count, e.g. "14 unit(s)" vs "15 unit(s)", stays the same
    signature instead of re-notifying); notify-owner.py itself rate-limits
    each signature to at most one send per hour, so this call is safe to
    make every cycle.

    2026-07-23 addition (items 27/28/56): a first-time anomaly and one that
    has been found on every check for hours used to send the exact same
    plain email -- no severity tiering existed once the hourly rate limit
    kicked in. This counts consecutive cycles per signature (same
    prev-cycle-JSONL-state pattern as get_prev_blocked_ids()/
    get_prev_network_totals() above) and, once a signature has now persisted
    ANOMALY_ESCALATION_STREAK cycles in a row (45 min), switches to an
    escalated subject/body and a distinct dedupe key. The key change is
    deliberate: it makes the escalated email a "new" signature to
    notify-owner.py's own rate limiter, so it sends immediately at the
    escalation boundary rather than waiting out the still-running hourly
    window from the original (non-escalated) send. Reuses the existing
    notify_owner() call -- no new notification path."""
    streaks = {}
    for anomaly in anomalies:
        signature = re.sub(r"\d+", "N", anomaly)
        sig_key = hashlib.sha256(signature.encode("utf-8")).hexdigest()
        streak = prev_streaks.get(sig_key, 0) + 1
        streaks[sig_key] = streak
        escalated = streak >= ANOMALY_ESCALATION_STREAK
        dedupe_key = "anomaly-" + sig_key + ("-escalated" if escalated else "")
        if escalated:
            subject = "Veridian: UNRESOLVED PROBLEM on your server (still happening)"
            body = (
                "Hi Rajat,\n\n"
                f"Your Veridian server has found the same problem on {streak} checks in a "
                "row (at least 45 minutes) without it being fixed.\n\n"
                f"What it found: {anomaly}\n\n"
                "This has been going on a while -- it likely needs your attention soon.\n\n"
                "- Veridian health check"
            )
        else:
            subject = "Veridian: a problem was found on your server"
            body = (
                "Hi Rajat,\n\n"
                "Your Veridian server found a problem during a routine check.\n\n"
                f"What it found: {anomaly}\n\n"
                "You may want to take a look when you get a chance.\n\n"
                "- Veridian health check"
            )
        notify_owner(subject, body, dedupe_key)
    return streaks


BLOCKED_STALE_HOURS = 2


def is_stale_blocked(task_id):
    """True if this blocked task's last checkpoint is old enough (>2h) that
    it's abandoned/archived debris rather than a live problem to page on."""
    try:
        with open(os.path.join(TASKS_DIR, task_id, "task.yaml")) as f:
            content = f.read()
        cp_m = re.search(r"^last_checkpoint_at:\s*'?([0-9T:.+-]+)'?", content, re.MULTILINE)
        if not cp_m:
            return True
        cp_time = datetime.fromisoformat(cp_m.group(1).replace("Z", "+00:00"))
        age_h = (datetime.now(timezone.utc) - cp_time).total_seconds() / 3600
        return age_h > BLOCKED_STALE_HOURS
    except Exception:
        return True


def blocked_task_note(task_id):
    try:
        with open(os.path.join(TASKS_DIR, task_id, "task.yaml")) as f:
            content = f.read()
        checkpoints = re.findall(r"note:\s*'?\"?(.*?)'?\"?\n", content)
        return checkpoints[-1] if checkpoints else ""
    except Exception:
        return ""


def check_mother_router_db():
    db_url = get_env_value("DATABASE_URL")
    if not db_url:
        return {"reachable": False, "error": "DATABASE_URL not found in .env.local"}
    out, err, code = sh(
        f'psql "{db_url}" -t -A -c '
        '"select (select count(*) from platform.ai_model_registry) as models, '
        '(select count(*) from platform.ai_routing_policies) as policies, '
        '(select count(*) from platform.ai_routing_audit_log) as audit_rows;"',
        timeout=20,
    )
    if code != 0:
        return {"reachable": False, "error": err[:300]}
    try:
        models, policies, audit_rows = out.split("|")
        return {"reachable": True, "ai_model_registry_rows": int(models), "ai_routing_policies_rows": int(policies), "ai_routing_audit_log_rows": int(audit_rows)}
    except Exception:
        return {"reachable": True, "raw": out}


def check_app_layer_failures(lookback_minutes=15):
    """TASK 2/4 (Owner directive 2026-07-20: "if task fails (any task, ai
    task, software task), server to know, log it"). Real cross-system gap
    found: this ops server had zero visibility into APP-layer (Vercel/
    Supabase) task failures -- only its own systemd worker fleet
    (check_tasks() above). Reuses the exact same DATABASE_URL psql
    connection check_mother_router_db() above already establishes and
    proves works (verified live before writing this: 'ai_model_registry_rows':
    11, matching the Supabase MCP tool's own count exactly) -- no new
    infrastructure, no new credential, same fail-open contract (returns
    reachable:False on any error rather than raising).

    orchestra_executions.status is the only currently-populated failure
    signal on the app side (activity_log has 0 rows as of 2026-07-20 --
    built but not yet wired to any live call site, a separate, already-
    disclosed gap, not something this check can surface data for that
    doesn't exist).
    """
    db_url = get_env_value("DATABASE_URL")
    if not db_url:
        return {"reachable": False, "error": "DATABASE_URL not found in .env.local"}
    out, err, code = sh(
        f'psql "{db_url}" -t -A -c '
        f'"select count(*) from compliance.orchestra_executions '
        f"where status = 'failed' and created_at >= now() - interval '{lookback_minutes} minutes';\"",
        timeout=20,
    )
    if code != 0:
        return {"reachable": False, "error": err[:300]}
    try:
        return {"reachable": True, "recent_failed_count": int(out.strip()), "lookback_minutes": lookback_minutes}
    except Exception:
        return {"reachable": True, "raw": out}


def check_credit_accountant_health():
    """Owner directive 2026-07-20 credit-governance mechanism
    (registries.credit_spend_governance): the accountant gate in
    credit-accountant.py fails CLOSED on its own errors, by design -- any
    accountant-call timeout/error halts ALL metered AI spend server-wide
    rather than silently letting it through (the correct safe default for
    a gate whose job is preventing waste). But that must never happen
    SILENTLY -- this is the function credit-accountant.py's own docstring
    promises exists, to surface "accountant broken, spend currently
    halted" fast rather than leaving it to be discovered only when a task
    mysteriously can't get any credit approved.

    Looks at the last 15 minutes of the SQLite ledger (this script's own
    cadence) for plan_reviewer/outcome_reviewer == 'claude_cli_failed' --
    credit-accountant.py's own specific signal that the claude -p judgment
    call itself errored/timed out, distinct from a normal PASS/FAIL/REDIRECT
    verdict on a plan's actual merits (which is expected, working-as-
    designed behavior, not an anomaly).
    """
    if not os.path.isfile(CREDIT_LEDGER_PATH):
        return {"reachable": False, "note": "ledger not yet created -- no metered spend proposed yet, not itself an anomaly"}
    try:
        conn = sqlite3.connect(CREDIT_LEDGER_PATH, timeout=5)
        cur = conn.cursor()
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
        cur.execute(
            "SELECT task_id, increment_number, plan_reasoning FROM credit_increments "
            "WHERE plan_reviewed_at >= ? AND plan_reviewer = 'claude_cli_failed'",
            (cutoff,),
        )
        plan_failures = cur.fetchall()
        cur.execute(
            "SELECT task_id, increment_number, outcome_reasoning FROM credit_increments "
            "WHERE outcome_reviewed_at >= ? AND outcome_reviewer = 'claude_cli_failed'",
            (cutoff,),
        )
        report_failures = cur.fetchall()
        conn.close()
        return {
            "reachable": True,
            "recent_propose_accountant_failures": len(plan_failures),
            "recent_report_accountant_failures": len(report_failures),
            "sample": [list(r) for r in (plan_failures + report_failures)[:3]],
        }
    except Exception as e:
        return {"reachable": False, "error": str(e)}


SQLITE_DBS_TO_GUARD = [
    "/opt/veridian/ai-os/memory/superboss-register.sqlite",
    CREDIT_LEDGER_PATH,
]
SQLITE_BACKUP_DIR = "/opt/veridian/backups/sqlite-daily"
SQLITE_BACKUP_RETENTION_DAYS = 14


def check_db_integrity_and_backup():
    """2026-07-23 addition: superboss-register.sqlite was found silently
    corrupted ("database disk image is malformed") for ~17 hours before
    being caught -- every consumer (session_bootstrap.py, this script's
    own credit_health check pattern, file_inventory.py, veridian_self_check.py,
    superboss-register.py check-duplicate/log-work) had been failing that
    whole time with no ATTENTION.md/email escalation, because the failure
    happened before any of those scripts reached their own alerting code
    (which itself depends on writing to the same broken DB). This function
    breaks that chicken-and-egg gap: it never depends on the DB being
    readable to report that the DB is not readable, and it reuses this
    file's own already-working anomalies -> ATTENTION.md -> notify-owner.py
    pipeline instead of building a second one.

    Also takes a once-a-day integrity-verified backup (skipped if today's
    backup already exists) -- no backup of either DB existed anywhere on
    this server before this, confirmed by a full-server find during the
    2026-07-23 incident investigation.
    """
    results = []
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    os.makedirs(SQLITE_BACKUP_DIR, exist_ok=True)
    for db_path in SQLITE_DBS_TO_GUARD:
        name = os.path.basename(db_path)
        if not os.path.isfile(db_path):
            results.append({"db": name, "ok": True, "note": "not yet created"})
            continue
        try:
            # 2026-07-23: retry before declaring real corruption -- a single read-only
            # PRAGMA integrity_check under WAL mode can transiently see an inconsistent
            # snapshot if it lands mid-write from another concurrent process (confirmed:
            # a same-day flagged "malformed" superboss-register.sqlite passed a manual
            # re-check seconds later with no intervention). 3 attempts, 2s apart, before
            # treating it as real.
            verdict = None
            for attempt in range(3):
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
                cur = conn.cursor()
                cur.execute("PRAGMA integrity_check;")
                verdict = cur.fetchone()[0]
                conn.close()
                if verdict == "ok":
                    break
                if attempt < 2:
                    time.sleep(2)
            ok = verdict == "ok"
            results.append({"db": name, "ok": ok, "integrity_check": verdict})
            if ok:
                backup_path = os.path.join(SQLITE_BACKUP_DIR, f"{name}.{today}.bak")
                if not os.path.isfile(backup_path):
                    with open(db_path, "rb") as src, open(backup_path, "wb") as dst:
                        dst.write(src.read())
                cutoff = datetime.now(timezone.utc) - timedelta(days=SQLITE_BACKUP_RETENTION_DAYS)
                for fn in os.listdir(SQLITE_BACKUP_DIR):
                    if not fn.startswith(name + "."):
                        continue
                    fp = os.path.join(SQLITE_BACKUP_DIR, fn)
                    if datetime.fromtimestamp(os.path.getmtime(fp), tz=timezone.utc) < cutoff:
                        os.remove(fp)
        except Exception as e:
            results.append({"db": name, "ok": False, "error": str(e)})
    return results


def read_proc_net_dev():
    """Raw cumulative rx/tx byte counters per interface, straight from the
    kernel (/proc/net/dev). These are monotonically-increasing totals since
    boot, not deltas -- check_network_usage() below diffs them against the
    previous cycle's totals."""
    interfaces = {}
    try:
        with open("/proc/net/dev") as f:
            lines = f.readlines()[2:]  # first 2 lines are header
    except Exception:
        return interfaces
    for line in lines:
        if ":" not in line:
            continue
        iface, rest = line.split(":", 1)
        iface = iface.strip()
        fields = rest.split()
        if len(fields) < 9:
            continue
        try:
            interfaces[iface] = {"rx_bytes": int(fields[0]), "tx_bytes": int(fields[8])}
        except ValueError:
            continue
    return interfaces


def get_prev_network_totals():
    """Previous cycle's raw counters, read the same way get_prev_blocked_ids()
    reads previous tasks state above -- from the last JSONL record, before
    this cycle's record is appended."""
    if not os.path.isfile(JSONL_LOG):
        return {}
    try:
        with open(JSONL_LOG) as f:
            lines = [l for l in f if l.strip()]
        if not lines:
            return {}
        last = json.loads(lines[-1])
        return last.get("network_totals", {})
    except Exception:
        return {}


def check_network_usage():
    """item 20 (network_usage_logging): per-interface rx/tx byte deltas since
    the last 15-min cycle, from /proc/net/dev. Returns (per_interface_list,
    current_totals) -- current_totals is persisted into this cycle's JSONL
    record so the next cycle can compute its own delta, same pattern as
    get_prev_blocked_ids()/tasks.blocked_task_ids."""
    current = read_proc_net_dev()
    prev = get_prev_network_totals()
    per_interface = []
    for iface, counters in current.items():
        p = prev.get(iface)
        if p:
            rx_delta = counters["rx_bytes"] - p["rx_bytes"]
            tx_delta = counters["tx_bytes"] - p["tx_bytes"]
            # counter reset (interface reset/reboot) shows up as a negative
            # delta -- treat it as "delta since reset" rather than negative
            if rx_delta < 0:
                rx_delta = counters["rx_bytes"]
            if tx_delta < 0:
                tx_delta = counters["tx_bytes"]
        else:
            rx_delta = 0
            tx_delta = 0
        per_interface.append({"interface": iface, "rx_bytes_delta": rx_delta, "tx_bytes_delta": tx_delta})
    return per_interface, current


def check_server_health():
    disk_out, _, _ = sh("df -h / | tail -1")
    disk_pct = None
    m = re.search(r"(\d+)%", disk_out)
    if m:
        disk_pct = int(m.group(1))
    mem_out, _, _ = sh("free -m | grep Mem")
    mem_parts = mem_out.split()
    mem_used_pct = None
    if len(mem_parts) >= 3:
        try:
            total, used = int(mem_parts[1]), int(mem_parts[2])
            mem_used_pct = round(100 * used / total, 1) if total else None
        except Exception:
            pass
    load_out, _, _ = sh("uptime")
    return {"disk_pct_used": disk_pct, "mem_pct_used": mem_used_pct, "uptime_raw": load_out}


def scan_claude_exhaustion_signatures():
    """Best-effort proxy for CLI-subscription quota exhaustion -- NOT a real
    quota API (none exists in claude-code 2.1.212). Scans worker.log/result.json
    files touched in the last 15 min for known failure-message patterns."""
    out, _, _ = sh(f"find {TASKS_DIR} -name 'worker.log' -o -name 'result.json' -mmin -16 2>/dev/null")
    hits = []
    for path in out.splitlines():
        try:
            with open(path, errors="ignore") as f:
                content = f.read()[-4000:]
            for pat in EXHAUSTION_PATTERNS:
                if re.search(pat, content, re.IGNORECASE):
                    hits.append({"file": path, "pattern": pat})
                    break
        except Exception:
            continue
    return {"scanned": len(out.splitlines()), "exhaustion_signature_hits": hits}


CLAUDE_CREDENTIALS_PATH = os.path.expanduser("~/.claude/.credentials.json")
REFRESH_TOKEN_WARNING_DAYS = 7  # requires interactive Owner re-auth, cannot self-heal


def check_claude_cli_credentials_health():
    """Governance item 4 (cli_monitoring): real, zero-AI-cost signal this
    file's own header claims does not exist ('no real quota API in claude-code
    2.1.212') -- true for USAGE/quota, but ~/.claude/.credentials.json DOES
    expose real subscription-session facts with no model call needed:
    expiresAt (short-lived access token, auto-refreshed, not itself urgent)
    and refreshTokenExpiresAt (the one that matters -- once THIS expires the
    CLI can no longer self-refresh and every headless worker/supervisor/
    docworker invocation on this server fails until the Owner interactively
    re-authenticates `claude`, since none of those run interactively).
    Answers GOVERNANCE_TASK_PROMPT_2026-07-23.yaml decision_1's own
    known_incompatibility question ('replace [OpenRouter balance check] with
    an equivalent guard...if the CLI or account API exposes that signal') --
    it does, this is that signal. Fails open (returns reachable=False, never
    raises) on any missing/unreadable/malformed credentials file."""
    try:
        with open(CLAUDE_CREDENTIALS_PATH) as f:
            data = json.load(f)
        oauth = data.get("claudeAiOauth", {})
        now_ms = time.time() * 1000
        expires_at = oauth.get("expiresAt")
        refresh_expires_at = oauth.get("refreshTokenExpiresAt")
        refresh_days_left = (refresh_expires_at - now_ms) / 86400000 if refresh_expires_at else None
        return {
            "reachable": True,
            "subscription_type": oauth.get("subscriptionType"),
            "rate_limit_tier": oauth.get("rateLimitTier"),
            "access_token_expired": bool(expires_at and expires_at < now_ms),
            "refresh_token_days_left": round(refresh_days_left, 2) if refresh_days_left is not None else None,
        }
    except Exception as e:
        return {"reachable": False, "error": str(e)}


def rotate(path, max_lines):
    if not os.path.isfile(path):
        return
    with open(path) as f:
        lines = f.readlines()
    if len(lines) > max_lines:
        with open(path, "w") as f:
            f.writelines(lines[-max_lines:])


def run_one_cycle():
    now = datetime.now(timezone.utc).isoformat()

    units = check_systemd_units()
    tasks = check_tasks()
    prev_blocked_ids = get_prev_blocked_ids()  # must read before this cycle's record is appended below
    prev_anomaly_streaks = get_prev_anomaly_streaks()  # same reason
    router = check_mother_router_db()
    app_failures = check_app_layer_failures()
    server = check_server_health()
    claude_signal = scan_claude_exhaustion_signatures()
    claude_cli_credentials = check_claude_cli_credentials_health()
    credit_health = check_credit_accountant_health()
    db_integrity = check_db_integrity_and_backup()
    network, network_totals = check_network_usage()

    anomalies = []
    if units["failed_count"] > 0:
        anomalies.append(f"{units['failed_count']} systemd unit(s) in failed state: {', '.join(units['failed_units'])}")
    if tasks["stalled"]:
        for s in tasks["stalled"]:
            anomalies.append(f"Task {s['task_id']} checkpoint stale ({s['checkpoint_age_min']} min, threshold {STALE_THRESHOLD_MIN})")
    # 2026-07-20 (constitution-audit gap #3, corrected): this check_tasks()
    # call already counted failed/completed/etc, but nothing computed the
    # RATE or alerted on it -- a 71% all-time failure rate produced zero
    # anomalies here before this. Deliberately reuses this same scheduled
    # run + the same ATTENTION_FILE mechanism rather than a new cron job --
    # a separate reconciliation script+cron was drafted first and found to
    # duplicate this file's existing job before being deployed.
    total_known_tasks = sum(v for k, v in tasks.items() if k != "stalled" and isinstance(v, int))
    failed_count = tasks.get("failed", 0)
    if total_known_tasks >= 5:  # don't alarm on a tiny/early sample
        failure_rate = failed_count / total_known_tasks
        if failure_rate > FAILURE_RATE_THRESHOLD:
            anomalies.append(f"Task failure rate {failure_rate*100:.1f}% ({failed_count}/{total_known_tasks}) "
                              f"above {FAILURE_RATE_THRESHOLD*100:.0f}% threshold")
    if not router.get("reachable"):
        anomalies.append(f"Mother Router DB unreachable: {router.get('error')}")
    if app_failures.get("reachable") and app_failures.get("recent_failed_count", 0) > 0:
        anomalies.append(
            f"{app_failures['recent_failed_count']} app-layer (orchestra_executions) "
            f"task(s) failed in the last {app_failures['lookback_minutes']} min"
        )
    warnings = []
    if server.get("disk_pct_used") is not None and server["disk_pct_used"] >= 90:
        anomalies.append(f"Disk usage at {server['disk_pct_used']}%")
    elif server.get("disk_pct_used") is not None and server["disk_pct_used"] >= DISK_WARNING_PCT:
        warnings.append(f"Disk usage at {server['disk_pct_used']}%")
    if server.get("mem_pct_used") is not None and server["mem_pct_used"] >= 90:
        anomalies.append(f"Memory usage at {server['mem_pct_used']}%")
    elif server.get("mem_pct_used") is not None and server["mem_pct_used"] >= MEM_WARNING_PCT:
        warnings.append(f"Memory usage at {server['mem_pct_used']}%")
    if claude_signal["exhaustion_signature_hits"]:
        anomalies.append(f"Possible Claude/API quota exhaustion signature found in {len(claude_signal['exhaustion_signature_hits'])} recent log(s)")
    if not claude_cli_credentials.get("reachable"):
        anomalies.append(f"Claude Code CLI credentials unreadable: {claude_cli_credentials.get('error')} -- headless workers/supervisor/docworker cannot authenticate")
    elif claude_cli_credentials.get("refresh_token_days_left") is not None and claude_cli_credentials["refresh_token_days_left"] < REFRESH_TOKEN_WARNING_DAYS:
        anomalies.append(
            f"HIGH PRIORITY: Claude Code CLI refresh token expires in {claude_cli_credentials['refresh_token_days_left']} day(s) "
            f"-- once expired, the CLI cannot self-refresh and every headless worker/supervisor/docworker invocation will fail "
            f"until the Owner interactively re-authenticates `claude` on this server."
        )
    if not credit_health.get("reachable") and "note" not in credit_health:
        anomalies.append(f"Credit-accountant ledger unreachable: {credit_health.get('error')}")
    credit_failures = credit_health.get("recent_propose_accountant_failures", 0) + credit_health.get("recent_report_accountant_failures", 0)
    if credit_failures > 0:
        anomalies.append(
            f"HIGH PRIORITY: credit-accountant.py itself failed/timed out {credit_failures} time(s) in the last 15min "
            f"-- this is failing CLOSED and BLOCKING ALL METERED AI SPEND server-wide by design (Owner zero-waste "
            f"directive 2026-07-20). Fix the accountant (check CLAUDE_CODE_OAUTH_TOKEN / claude -p reachability), "
            f"do not bypass or disable the gate."
        )
    for db_result in db_integrity:
        if not db_result["ok"]:
            anomalies.append(
                f"HIGH PRIORITY: {db_result['db']} failed PRAGMA integrity_check "
                f"({db_result.get('integrity_check') or db_result.get('error')}) -- this DB is a canonical "
                f"store per STANDING_DIRECTIVE.yaml metadata_store; every consumer will fail until it is "
                f"restored from /opt/veridian/backups/sqlite-daily/ or reinitialized."
            )

    # Sends real Owner emails (escalating subject/body once a signature has
    # persisted ANOMALY_ESCALATION_STREAK cycles in a row) -- must run before
    # anomaly_streaks is persisted into this cycle's record below.
    anomaly_streaks = send_anomaly_notifications(anomalies, prev_anomaly_streaks)

    record = {
        "ts": now,
        "systemd_units": units,
        "tasks": tasks,
        "mother_router": router,
        "app_layer_failures": app_failures,
        "server": server,
        "claude_quota_proxy_signal": claude_signal,
        "claude_cli_credentials": claude_cli_credentials,
        "db_integrity": db_integrity,
        "credit_accountant": credit_health,
        "network": network,
        "network_totals": network_totals,
        "anomalies": anomalies,
        "anomaly_streaks": anomaly_streaks,
        "warnings": warnings,
    }

    with open(JSONL_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")

    summary = (f"{now} | units running={units['running']}/{units['total']} failed={units['failed_count']} | "
               f"tasks in_progress={tasks.get('in_progress', 0)} completed={tasks.get('completed', 0)} "
               f"failed={tasks.get('failed', 0)} stalled={len(tasks['stalled'])} | "
               f"router_reachable={router.get('reachable')} | disk={server.get('disk_pct_used')}% mem={server.get('mem_pct_used')}% | "
               f"anomalies={len(anomalies)} warnings={len(warnings)}")
    with open(TEXT_LOG, "a") as f:
        f.write(summary + "\n")
        for w in warnings:
            f.write(f"WARN: {w}\n")

    if anomalies:
        with open(ATTENTION_FILE, "a") as f:
            f.write(f"\n## {now} -- health-check-15min\n")
            for a in anomalies:
                f.write(f"- {a}\n")

    # Blocked-task escalation (2026-07-23): only notify once a task has been
    # status=blocked for two consecutive health-check cycles (30 min), not on
    # the first (possibly transient) block. notify-owner.py's own per-signature
    # hourly rate limit keeps this to one email per continuous blocked stretch.
    # Also requires a checkpoint within the last 2 hours: the live task pool
    # already had ~229 status=blocked tasks that are days-old abandoned/archived
    # work (confirmed 2026-07-23: 0 of them checkpointed in the last 2h) --
    # without this, first activation would fire ~229 emails for historical
    # debris instead of the live tasks the Owner actually needs to hear about.
    confirmed_blocked = set(tasks.get("blocked_task_ids", [])) & prev_blocked_ids
    confirmed_blocked = {t for t in confirmed_blocked if not is_stale_blocked(t)}
    for task_id in confirmed_blocked:
        note = blocked_task_note(task_id)
        body = (
            "Hi Rajat,\n\n"
            "A task on your Veridian server has been stuck for at least 30 minutes.\n\n"
            f"Task: {task_id}\n"
        )
        if note:
            body += f"Reason given: {note}\n"
        body += "\nIt may need your review.\n\n- Veridian health check"
        notify_owner("Veridian: a task is stuck and needs review", body, "blocked-task-" + task_id)

    rotate(JSONL_LOG, MAX_LINES)
    rotate(TEXT_LOG, MAX_LINES)

    print(summary)
    if anomalies:
        print("ANOMALIES:", anomalies, file=sys.stderr)


def main():
    os.makedirs(LOG_DIR, exist_ok=True)

    lock_f = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # A previous invocation's internal loop is still running (overran into
        # this cron tick). Skip rather than pile up a second concurrent process
        # -- flock is tied to the holding process's open fd, so it releases
        # automatically (crash-safe) if that process dies instead of exiting
        # cleanly; no stale-lock cleanup logic is needed.
        print("health-check-15min: previous cycle still running (lock held) -- skipping this invocation", file=sys.stderr)
        lock_f.close()
        return

    try:
        start = time.monotonic()
        for _iteration in range(MAX_ITERATIONS):
            cycle_start = time.monotonic()
            run_one_cycle()
            elapsed = time.monotonic() - start
            if elapsed >= LOOP_SPAN_SECONDS:
                break
            sleep_for = max(0, CHECK_INTERVAL_SECONDS - (time.monotonic() - cycle_start))
            if elapsed + sleep_for >= LOOP_SPAN_SECONDS:
                break
            time.sleep(sleep_for)
    finally:
        fcntl.flock(lock_f, fcntl.LOCK_UN)
        lock_f.close()


if __name__ == "__main__":
    main()
