#!/usr/bin/env python3
"""
VERIDIAN security-check: network/security/permission monitoring, items 21
(ssh_login_anomaly_detection), 22 (file_permission_change_tracking) and 24
(security_event_detection) of the 2026-07-23 governance audit.

Reads /var/log/auth.log for SSH login activity and the file_inventory table
(superboss-register.sqlite, extended 2026-07-23 with a `mode` column) for
permission drift. Fail-open throughout, same contract as health-check-15min.py:
a read/parse problem here must never crash the check itself -- it shows up as
an empty result for that section plus an entry in "errors", not a traceback.

Persists a small state file (STATE_FILE) across runs so "new" means "never
seen before", not "seen since the log file itself started" -- same shape as
health-check-15min.py's own JSONL-based previous-cycle reads.

Prints one JSON object to stdout: {new_source_ips, failed_then_success_bursts,
permission_changes} plus a non-schema "errors" diagnostic list. Always exits 0
(a monitoring script that can itself fail non-zero on a transient read hiccup
is worse than one that reports "found nothing, here's why").
"""
import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta

AUTH_LOG = "/var/log/auth.log"
STATE_FILE = "/opt/veridian/ai-os/logs/security-check-state.json"
NOTIFY_SCRIPT = "/opt/veridian/scripts/notify-owner.py"
SCRIPTS = "/opt/veridian/scripts"
SUPERBOSS_REGISTER = os.path.join(SCRIPTS, "superboss-register.py")

WINDOW_MINUTES = 20  # 15-min cron cadence + grace, same reasoning as health-check's STALE_THRESHOLD_MIN
BURST_THRESHOLD = 3  # >=3 failed attempts immediately before a success, same IP

ACCEPTED_RE = re.compile(r"Accepted (\S+) for (?:invalid user )?(\S+) from ([0-9a-fA-F:.]+)")
FAILED_RE = re.compile(r"Failed password for (?:invalid user )?\S+ from ([0-9a-fA-F:.]+)")
TS_RE = re.compile(r"^(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})")

# Real, canonical DB-path resolution -- same lazy-import-cached convention
# reconcile_stale_running_workers.py / worker-exit-status-bridge.py already use:
# never a hardcoded path, always the one real SUPERBOSS_REGISTER_DB override every
# other real caller already honors.
_sbr = None


def _superboss_register():
    global _sbr
    if _sbr is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "superboss_register_security_check", SUPERBOSS_REGISTER)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sbr = _mod
    return _sbr


def _resolve_db_path():
    return _superboss_register().resolve_superboss_db_path()


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception:
        pass


def parse_auth_log_events(now):
    """Returns (events, error). events is a list of (dt, ip, kind) tuples,
    kind in {"accepted", "failed"}, sorted by time. error is None or a short
    diagnostic string (e.g. permission denied)."""
    try:
        with open(AUTH_LOG, errors="ignore") as f:
            lines = f.readlines()
    except PermissionError as e:
        return [], (f"cannot read {AUTH_LOG}: permission denied ({e}) -- current user needs "
                    "membership in the 'adm' group (or root) to read syslog-owned auth.log; "
                    "this is a system permission change and was NOT made automatically")
    except FileNotFoundError as e:
        return [], f"cannot read {AUTH_LOG}: not found ({e})"
    except Exception as e:
        return [], f"cannot read {AUTH_LOG}: {e}"

    events = []
    for line in lines:
        m = TS_RE.match(line)
        if not m:
            continue
        try:
            dt = datetime.strptime(f"{now.year} {m.group(1)}", "%Y %b %d %H:%M:%S")
        except ValueError:
            continue
        if dt > now + timedelta(minutes=5):
            # log line is from December, "now" is January -- syslog timestamps carry no year
            dt = dt.replace(year=dt.year - 1)
        if "Accepted" in line:
            am = ACCEPTED_RE.search(line)
            if am:
                method, user, ip = am.group(1), am.group(2), am.group(3)
                events.append((dt, ip, "accepted", method, user))
        elif "Failed password" in line:
            fm = FAILED_RE.search(line)
            if fm:
                events.append((dt, fm.group(1), "failed", None, None))
    events.sort(key=lambda e: e[0])
    return events, None


def log_login(user, ip, method):
    """Best-effort, fail-open -- same contract as notify_owner() below.
    Governance item 11 (veridian_built_login_logging): logs a real successful
    SSH login into superboss-register.py's own actions table via its
    log-login CLI (reuses that script's tested DB/locking code, no second
    sqlite writer here)."""
    try:
        import subprocess
        subprocess.run(
            [sys.executable, SUPERBOSS_REGISTER, "log-login", "--user", user, "--source-ip", ip, "--method", method],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        pass


def check_ssh_activity(state, now):
    events, error = parse_auth_log_events(now)
    errors = [error] if error else []
    window_start = now - timedelta(minutes=WINDOW_MINUTES)
    seen_ips = set(state.get("seen_ssh_ips", []))
    logged_logins = set(state.get("logged_login_keys", []))

    new_source_ips = []
    bursts = []
    failed_streak = {}
    all_accepted_ips = set()

    for dt, ip, kind, method, user in events:
        if kind == "failed":
            failed_streak[ip] = failed_streak.get(ip, 0) + 1
            continue
        all_accepted_ips.add(ip)
        prior_failed = failed_streak.get(ip, 0)
        # governance item 11: log every real successful login line this same
        # parser finds, exactly once per line (dt+ip+user+method is unique
        # per real auth.log line) -- not windowed like new_source_ips/bursts
        # below, since a login that happened outside the last WINDOW_MINUTES
        # is still a real login worth recording, just not worth re-alerting on.
        login_key = f"{dt.isoformat()}|{ip}|{user}|{method}"
        if login_key not in logged_logins:
            log_login(user, ip, method)
            logged_logins.add(login_key)
        if dt >= window_start:
            if ip not in seen_ips and ip not in new_source_ips:
                new_source_ips.append(ip)
            if prior_failed >= BURST_THRESHOLD:
                bursts.append({"ip": ip, "count": prior_failed})
        failed_streak[ip] = 0

    state["seen_ssh_ips"] = sorted(seen_ips | all_accepted_ips)
    state["logged_login_keys"] = sorted(logged_logins)
    return new_source_ips, bursts, errors


def get_current_modes():
    try:
        db = _resolve_db_path()
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        cur = conn.cursor()
        cur.execute("SELECT path, mode FROM file_inventory WHERE mode IS NOT NULL")
        rows = cur.fetchall()
        conn.close()
        return {r[0]: r[1] for r in rows}, None
    except sqlite3.OperationalError as e:
        return {}, f"file_inventory.mode not queryable yet: {e}"
    except Exception as e:
        return {}, f"could not read file_inventory: {e}"


def check_permission_changes(state):
    current_modes, error = get_current_modes()
    errors = [error] if error else []
    prev_modes = state.get("file_modes", {})
    changes = []
    for path, mode in current_modes.items():
        old = prev_modes.get(path)
        if old is not None and old != mode:
            changes.append({"path": path, "old_mode": old, "new_mode": mode})
    state["file_modes"] = current_modes
    return changes, errors


def notify_owner(subject, body, dedupe_key):
    """Best-effort, fail-open -- same contract as health-check-15min.py's
    notify_owner()."""
    try:
        import subprocess
        subprocess.run(
            [sys.executable, NOTIFY_SCRIPT, "--subject", subject, "--body", body, "--dedupe-key", dedupe_key],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        pass


def main():
    now = datetime.now()
    state = load_state()

    new_source_ips, bursts, ssh_errors = check_ssh_activity(state, now)
    permission_changes, perm_errors = check_permission_changes(state)
    errors = ssh_errors + perm_errors

    save_state(state)

    result = {
        "new_source_ips": new_source_ips,
        "failed_then_success_bursts": bursts,
        "permission_changes": permission_changes,
        "errors": errors,
    }

    for ip in new_source_ips:
        notify_owner(
            "Veridian: a new IP address logged in over SSH",
            f"Hi Rajat,\n\nA source IP that has never logged in before succeeded over SSH: {ip}\n\n"
            "If that wasn't you or someone you expect, you may want to look into it.\n\n- Veridian security check",
            "security-new-ip-" + ip,
        )
    for b in bursts:
        notify_owner(
            "Veridian: repeated failed SSH logins before a success",
            f"Hi Rajat,\n\n{b['ip']} had {b['count']} failed SSH login attempts immediately before "
            "succeeding. This can be a sign of a guessed or compromised password.\n\n- Veridian security check",
            "security-burst-" + b["ip"],
        )
    for pc in permission_changes:
        path_hash = hashlib.sha256(pc["path"].encode("utf-8")).hexdigest()[:16]
        notify_owner(
            "Veridian: a file's permissions changed",
            f"Hi Rajat,\n\n{pc['path']} changed permissions from {pc['old_mode']} to {pc['new_mode']}.\n\n"
            "If you didn't make this change, you may want to look into it.\n\n- Veridian security check",
            "security-permchange-" + path_hash,
        )

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
