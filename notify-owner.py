#!/usr/bin/env python3
"""
Sends a real email to the Owner via the Resend API. Built so the detection
scripts that already find problems (health-check-15min.py) can actually tell
the Owner, instead of only writing to ai-os/logs/ATTENTION.md (a file nobody
actively reads -- see ai-os/logs/ATTENTION.md's repeated identical
"1 systemd unit(s) in failed state" entries from 2026-07-19 that never got a
human response).

Rate-limited to at most one email per distinct issue signature per hour,
tracked in a local JSON state file (STATE_FILE) keyed by a content hash.
Callers can pass an explicit --dedupe-key to control what counts as "the same
issue" (e.g. stripping variable counts out of a message); otherwise the key
is derived from the subject+body.

Owner is explicitly non-technical: callers should keep --body in simple
English, short sentences, no jargon.
"""
import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

ENV_FILE = "/opt/veridian/shared/.env"
STATE_FILE = "/opt/veridian/ai-os/logs/notify-owner-state.json"
OWNER_EMAIL = "raajat.agarwal@gmail.com"
FROM_ADDRESS = "Veridian Alerts <onboarding@resend.dev>"
RATE_LIMIT_HOURS = 1
STATE_MAX_AGE_HOURS = 24


def get_env_value(key, path=ENV_FILE):
    try:
        with open(path) as f:
            for line in f:
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return None


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, STATE_FILE)


def prune_state(state, now):
    cutoff = now - timedelta(hours=STATE_MAX_AGE_HOURS)
    kept = {}
    for k, v in state.items():
        try:
            if datetime.fromisoformat(v) > cutoff:
                kept[k] = v
        except ValueError:
            continue
    return kept


def already_notified_recently(state, key, now):
    last = state.get(key)
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return False
    return (now - last_dt) < timedelta(hours=RATE_LIMIT_HOURS)


def send_email(api_key, subject, body):
    payload = json.dumps(
        {
            "from": FROM_ADDRESS,
            "to": [OWNER_EMAIL],
            "subject": subject,
            "text": body,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "veridian-notify-owner/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description="Send a real Owner-notification email via Resend.")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body", required=True)
    parser.add_argument(
        "--dedupe-key",
        default=None,
        help="Explicit issue signature for rate-limiting; defaults to a hash of subject+body",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Bypass the hourly rate limit (manual test sends only)",
    )
    args = parser.parse_args()

    key = args.dedupe_key or hashlib.sha256((args.subject + "\n" + args.body).encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    state = prune_state(load_state(), now)

    if not args.force and already_notified_recently(state, key, now):
        print(f"SKIPPED (rate-limited, signature already notified within {RATE_LIMIT_HOURS}h): {key[:12]}")
        return 0

    api_key = os.environ.get("RESEND_API_KEY") or get_env_value("RESEND_API_KEY")
    if not api_key:
        print("ERROR: RESEND_API_KEY not found in environment or shared/.env", file=sys.stderr)
        return 1

    try:
        result = send_email(api_key, args.subject, args.body)
    except urllib.error.HTTPError as e:
        print(f"ERROR: Resend API returned HTTP {e.code}: {e.read().decode('utf-8', 'ignore')}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: failed to send email: {e}", file=sys.stderr)
        return 1

    message_id = result.get("id")
    state[key] = now.isoformat()
    save_state(state)
    print(f"SENT: Resend message id={message_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
