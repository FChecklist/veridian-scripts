#!/usr/bin/env python3
"""
VERIDIAN-DEV hourly cost/usage check. Zero AI cost -- pure deterministic
script (no model call). Pulls, cross-checks, and logs:
  - OpenRouter's real /api/v1/credits balance (total_credits - total_usage)
  - compliance.token_usage_ledger: input/output tokens + cost, grouped by
    model, for rows created in the last hour (self-heals once the
    AI_TEAM_LOG_SECRET Vercel gap is fixed -- currently 0 rows, logged
    honestly as such, not hidden)
  - Groq: best-effort only, Groq has no confirmed public usage-query API as
    of 2026-07-19 -- reports "not available" rather than fabricating data
Appends to cost-usage-60min.jsonl/.log, flags to ATTENTION.md if the
OpenRouter spend delta since the last run exceeds COST_ALERT_THRESHOLD_USD.
Self-rotates to the last 168 lines (~1 week hourly).
"""
import json
import os
import subprocess
from datetime import datetime, timezone

import dispatch_core  # 2026-07-29 cron-consolidation-phase6: shared concurrency gate

LOG_DIR = "/opt/veridian/ai-os/logs"
JSONL_LOG = os.path.join(LOG_DIR, "cost-usage-60min.jsonl")
TEXT_LOG = os.path.join(LOG_DIR, "cost-usage-60min.log")
ATTENTION_FILE = os.path.join(LOG_DIR, "ATTENTION.md")
MAX_LINES = 168
COST_ALERT_THRESHOLD_USD = float(os.environ.get("COST_ALERT_THRESHOLD_USD", "2.0"))
SHARED_ENV = "/opt/veridian/shared/.env"
APP_ENV = "/opt/veridian/repos/compliance-tracker/.env.local"


def sh(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except Exception as e:
        return "", str(e), -1


def get_env_value(key, path):
    try:
        with open(path) as f:
            for line in f:
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return None


def check_openrouter_credits():
    key = get_env_value("OPENROUTER_API_KEY", SHARED_ENV) or get_env_value("OPENROUTER_API_KEY", APP_ENV)
    if not key:
        return {"available": False, "error": "OPENROUTER_API_KEY not found"}
    out, err, code = sh(
        f'curl -s -H "Authorization: Bearer {key}" https://openrouter.ai/api/v1/credits'
    )
    if code != 0 or not out:
        return {"available": False, "error": err[:300] or "empty response"}
    try:
        data = json.loads(out).get("data", {})
        total_credits = data.get("total_credits")
        total_usage = data.get("total_usage")
        remaining = None
        if total_credits is not None and total_usage is not None:
            remaining = round(total_credits - total_usage, 4)
        return {"available": True, "total_credits": total_credits, "total_usage": total_usage, "remaining_credits": remaining}
    except Exception as e:
        return {"available": False, "error": f"unparseable response: {e}", "raw": out[:300]}


def check_token_usage_ledger():
    db_url = get_env_value("DATABASE_URL", APP_ENV)
    if not db_url:
        return {"available": False, "error": "DATABASE_URL not found"}
    query = (
        "select model, provider, count(*) as calls, "
        "coalesce(sum(prompt_tokens),0) as prompt_tokens, "
        "coalesce(sum(completion_tokens),0) as completion_tokens, "
        "coalesce(sum(estimated_cost_usd),0) as cost_usd "
        "from compliance.token_usage_ledger "
        "where created_at > now() - interval '1 hour' "
        "group by model, provider order by cost_usd desc;"
    )
    out, err, code = sh(f"psql \"{db_url}\" -t -A -F',' -c \"{query}\"", timeout=20)
    if code != 0:
        return {"available": False, "error": err[:300]}
    rows = []
    for line in out.splitlines():
        parts = line.split(",")
        if len(parts) == 6:
            rows.append({
                "model": parts[0], "provider": parts[1], "calls": int(parts[2]),
                "prompt_tokens": int(parts[3]), "completion_tokens": int(parts[4]),
                "cost_usd": float(parts[5]),
            })
    return {"available": True, "rows_last_hour": rows, "note": "0 rows likely means AI_TEAM_LOG_SECRET is still unset in Vercel production (known gap as of 2026-07-19) -- not evidence of zero real usage"}


def check_groq():
    return {"available": False, "note": "No confirmed public Groq usage-query API as of 2026-07-19; not fabricated. Check console.groq.com manually if Groq spend needs verifying."}


def rotate(path, max_lines):
    if not os.path.isfile(path):
        return
    with open(path) as f:
        lines = f.readlines()
    if len(lines) > max_lines:
        with open(path, "w") as f:
            f.writelines(lines[-max_lines:])


def get_previous_remaining():
    if not os.path.isfile(JSONL_LOG):
        return None
    try:
        with open(JSONL_LOG) as f:
            lines = f.readlines()
        if not lines:
            return None
        last = json.loads(lines[-1])
        return last.get("openrouter", {}).get("remaining_credits")
    except Exception:
        return None


def main():
    os.makedirs(LOG_DIR, exist_ok=True)

    # 2026-07-29 cron-consolidation-phase6: shared concurrency gate, mirroring
    # dispatch_core.py's acquire_dispatch_lock()/has_free_slot() pattern already
    # used by dispatch-tick.py/phase-continuation-tick.py/status-remediation-tick.py.
    # Brief lock hold for the cap check only; released before the real
    # curl/psql work below runs.
    with dispatch_core.acquire_dispatch_lock():
        if not dispatch_core.has_free_slot():
            print("SKIP cost-usage-60min (cap reached): system at concurrency cap, deferring to next scheduled run")
            return

    now = datetime.now(timezone.utc).isoformat()

    prev_remaining = get_previous_remaining()
    openrouter = check_openrouter_credits()
    ledger = check_token_usage_ledger()
    groq = check_groq()

    spend_delta = None
    if prev_remaining is not None and openrouter.get("remaining_credits") is not None:
        spend_delta = round(prev_remaining - openrouter["remaining_credits"], 4)

    anomalies = []
    if not openrouter.get("available"):
        anomalies.append(f"OpenRouter credits check failed: {openrouter.get('error')}")
    if spend_delta is not None and spend_delta > COST_ALERT_THRESHOLD_USD:
        anomalies.append(f"OpenRouter spend in last hour (${spend_delta}) exceeds threshold (${COST_ALERT_THRESHOLD_USD})")

    record = {
        "ts": now,
        "openrouter": openrouter,
        "openrouter_spend_last_hour_usd": spend_delta,
        "token_usage_ledger": ledger,
        "groq": groq,
        "anomalies": anomalies,
    }

    with open(JSONL_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")

    ledger_summary = f"{len(ledger.get('rows_last_hour', []))} model(s)" if ledger.get("available") else "unavailable"
    summary = (f"{now} | OpenRouter remaining=${openrouter.get('remaining_credits')} "
               f"spend_last_hr=${spend_delta} | ledger_rows_last_hr={ledger_summary} | anomalies={len(anomalies)}")
    with open(TEXT_LOG, "a") as f:
        f.write(summary + "\n")

    if anomalies:
        with open(ATTENTION_FILE, "a") as f:
            f.write(f"\n## {now} -- cost-usage-60min\n")
            for a in anomalies:
                f.write(f"- {a}\n")

    rotate(JSONL_LOG, MAX_LINES)
    rotate(TEXT_LOG, MAX_LINES)

    print(summary)


if __name__ == "__main__":
    main()
