#!/bin/bash
# Shared "claude usage-limit survives the 5-hour window" wrapper (2026-08-01,
# Owner directive: the supervisor should detect a Claude Code usage-limit hit
# -- interactive tmux session OR a headless `claude -p` worker -- and
# automatically resume once the limit window clears, with no manual restart.
#
# Honest status check before writing this (grepped worker.log/supervisor.log
# across every task dir on this box for "usage limit|rate limit|5-hour|resets
# at|resets in|rate.limited" on 2026-08-01): ZERO prior handling existed
# anywhere in worker-entrypoint.sh / supervisor-entrypoint.sh /
# doc-worker-entrypoint.sh. The only pre-existing hits were unrelated
# (credit-accountant's own "Budget limit reached" text and notify-owner.py's
# internal per-signature send-rate limiter) -- confirmed genuinely unrelated,
# not reused here. There is also no live-logged example of this box's own
# `claude`/`claude -p` actually hitting the real 5-hour usage limit to copy
# the exact message text from, so the detection below is deliberately BROAD
# across the documented/known message shapes (an embedded unix-epoch form and
# a human clock-time "resets at HH:MM" form) rather than a single guessed
# string. Every match is logged with the tag USAGE_LIMIT_RETRY specifically so
# the first real production hit is easy to grep for and the patterns below
# can be tightened against real text if they ever need it.
#
# Scope: purely additive resilience around the EXISTING `claude`/`claude -p`
# call sites. Does not touch, weaken, or bypass any existing guardrail --
# preflight-guard.py's hard/transient stops, resource_governor.py's caps, the
# lifetime-invocation cap, the circuit breaker's failure-signature recording,
# the PR/CI/audit-gate pipeline, or scope-check.py. From every caller's point
# of view this is a drop-in replacement for a single `claude ...` invocation:
# same output file, same exit code semantics, same everything downstream --
# it may just internally take longer (it sleeps through the usage-limit
# window) before returning.

# Safety cap on any single wait, in case the resume time cannot be parsed at
# all -- never sleep past a real 5-hour window plus generous margin either
# way. Configurable per-caller via env override, same convention as the
# existing *_BUDGET_CAP_USD / MAX_LIFETIME_INVOCATIONS knobs in this repo.
CLAUDE_USAGE_LIMIT_MAX_WAIT_SECONDS="${CLAUDE_USAGE_LIMIT_MAX_WAIT_SECONDS:-21600}"  # 6h (5h limit + margin)
# Fallback wait when a usage-limit hit is detected but no resume time could be
# parsed out of the message at all -- retry in 30 minutes rather than give up
# immediately. Deliberately short relative to the cap above: worst case this
# just retries a few times inside the same window until the real reset text
# (if this box ever surfaces one containing a specific time) can be parsed.
CLAUDE_USAGE_LIMIT_FALLBACK_WAIT_SECONDS="${CLAUDE_USAGE_LIMIT_FALLBACK_WAIT_SECONDS:-1800}"

# --- detection: is this text a genuine Claude Code usage-limit hit? ---------
# Reads combined stdout+stderr text on stdin. Intentionally does NOT match
# generic words like "limit" or "rate" alone (this repo already uses those
# words for unrelated things -- credit-accountant budget caps, notify-owner's
# own send-rate limiter -- a loose match would misfire on those and start
# sleeping for hours over an unrelated, already-handled condition).
claude_text_signals_usage_limit() {
  grep -qiE \
    "claude ai usage limit|usage limit reached|5-hour limit reached|five-hour limit reached|you.ve hit your (claude|usage) limit|upgrade to (pro|max) or try again"
}

# --- parse the resume time the CLI itself reported --------------------------
# Reads the same combined text on stdin. Prints a unix epoch to resume at, or
# nothing (caller must fall back to CLAUDE_USAGE_LIMIT_FALLBACK_WAIT_SECONDS).
# Two forms handled, checked in order of confidence:
#   1. An embedded unix epoch, e.g. "...usage limit reached|1735689600" (the
#      historical Claude Code / Anthropic API rate-limit message shape).
#   2. A human clock time near the words reset/try again/available again,
#      e.g. "resets at 3:00pm", "try again at 15:00", "available again 9pm"
#      -- resolved against THIS machine's own local clock/timezone (the CLI
#      and this wrapper run on the same box, so they share a timezone; if the
#      parsed hour:minute is <= now, it must mean tomorrow, not today).
claude_usage_limit_parse_resume_epoch() {
  python3 -c '
import re, sys
from datetime import datetime, timedelta

text = sys.stdin.read()

m = re.search(r"usage limit reached\|(\d{10,13})", text, re.IGNORECASE)
if m:
    ts = int(m.group(1))
    if ts > 10**12:
        ts //= 1000
    print(ts)
    sys.exit(0)

m = re.search(
    r"(?:resets?|reset|try again|available again)\D{0,10}(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?",
    text, re.IGNORECASE)
if m:
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = (m.group(3) or "").lower().replace(".", "")
    if ampm == "pm" and hour != 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        now = datetime.now()
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        print(int(candidate.timestamp()))
        sys.exit(0)
' 2>/dev/null
}

# --- the wrapper itself ------------------------------------------------------
# Usage: run_claude_usage_limit_retry OUT_FILE LOG_FILE [TIMEOUT_SECONDS] -- <claude args...>
#   OUT_FILE           stdout capture path (same as callers already use)
#   LOG_FILE           stderr append path (same as callers already use)
#   TIMEOUT_SECONDS    optional -- if given, the underlying claude call is run
#                      as `timeout TIMEOUT_SECONDS claude ...` (matches
#                      doc-worker-entrypoint.sh's existing MAX_WALL_SECONDS
#                      wall-clock cap). Applied PER ATTEMPT only -- the
#                      usage-limit sleep between attempts happens BETWEEN
#                      separate timeout invocations, so a multi-hour wait is
#                      never itself counted against that per-attempt cap.
#   --                 literal separator
#   <claude args...>   everything claude itself should receive (without the
#                      leading `claude` binary name)
#
# Returns the same exit code the (possibly-retried) claude invocation itself
# produced, with OUT_FILE holding that final invocation's output -- every
# existing downstream check (EXIT_CODE, is_error parsing, budget-cap parsing,
# etc.) in worker-entrypoint.sh / supervisor-entrypoint.sh /
# doc-worker-entrypoint.sh works completely unchanged against it.
#
# Retries the identical invocation at most ONCE after a genuine usage-limit
# hit (not an unbounded loop): if the retry hits the limit again too, this
# returns normally with whatever exit code that produced, so the caller's own
# existing failure handling (failure-signature recording, circuit breaker,
# lifetime-invocation cap, checkpoint --status blocked/failed) still applies
# exactly as before -- this wrapper only absorbs the ONE common, recoverable
# case (a single 5-hour window boundary crossed mid-task), it does not try to
# replace the existing loop-prevention machinery for a task that is
# genuinely, repeatedly hitting the limit.
run_claude_usage_limit_retry() {
  local out_file="$1"; shift
  local log_file="$1"; shift
  local timeout_seconds=""
  if [ "$1" != "--" ]; then
    timeout_seconds="$1"; shift
  fi
  shift  # consume the literal --
  local claude_args=("$@")

  local attempt=1
  local exit_code
  while :; do
    if [ -n "$timeout_seconds" ]; then
      timeout "$timeout_seconds" claude "${claude_args[@]}" > "$out_file" 2>>"$log_file"
    else
      claude "${claude_args[@]}" > "$out_file" 2>>"$log_file"
    fi
    exit_code=$?

    local combined
    combined="$(tail -c 4000 "$log_file" 2>/dev/null)
$(cat "$out_file" 2>/dev/null)"

    if ! echo "$combined" | claude_text_signals_usage_limit; then
      return "$exit_code"
    fi

    if [ "$attempt" -ge 2 ]; then
      echo "[USAGE_LIMIT_RETRY] Hit the Claude usage limit again on retry attempt $attempt (exit $exit_code) -- not retrying a 2nd time, handing off to this task's normal failure handling (circuit breaker / lifetime-invocation cap / checkpoint) instead of looping." >> "$log_file"
      return "$exit_code"
    fi

    local resume_epoch
    resume_epoch=$(echo "$combined" | claude_usage_limit_parse_resume_epoch)
    local now_epoch
    now_epoch=$(date +%s)
    local wait_seconds
    if [ -n "$resume_epoch" ] && [ "$resume_epoch" -gt "$now_epoch" ] 2>/dev/null; then
      wait_seconds=$((resume_epoch - now_epoch))
    else
      resume_epoch=""
      wait_seconds="$CLAUDE_USAGE_LIMIT_FALLBACK_WAIT_SECONDS"
    fi
    if [ "$wait_seconds" -gt "$CLAUDE_USAGE_LIMIT_MAX_WAIT_SECONDS" ]; then
      wait_seconds="$CLAUDE_USAGE_LIMIT_MAX_WAIT_SECONDS"
    fi

    local resume_human
    resume_human=$(date -d "@$((now_epoch + wait_seconds))" 2>/dev/null || echo "unknown")
    if [ -n "$resume_epoch" ]; then
      echo "[USAGE_LIMIT_RETRY] Detected a Claude Code usage-limit hit (attempt $attempt, exit $exit_code). Parsed resume time from the CLI's own message: $(date -d "@$resume_epoch" 2>/dev/null || echo "$resume_epoch"). Sleeping ${wait_seconds}s, will auto-resume at $resume_human." >> "$log_file"
    else
      echo "[USAGE_LIMIT_RETRY] Detected a Claude Code usage-limit hit (attempt $attempt, exit $exit_code), but could not parse a specific resume time out of the message -- using the ${CLAUDE_USAGE_LIMIT_FALLBACK_WAIT_SECONDS}s fallback wait instead. Sleeping ${wait_seconds}s, will retry at approximately $resume_human. Full text logged above/around this line for debugging." >> "$log_file"
    fi
    # Make the wait visible to dashboards/watchdogs, and refresh the
    # last-checkpoint timestamp so veridian-task-watchdog.py's stall detector
    # does not misdiagnose a legitimate, expected multi-hour wait as a stall
    # (same reasoning worker-entrypoint.sh's own periodic-checkpoint loop
    # already documents for why it keeps ticking through long phases).
    # Best-effort only -- TASK_ID/veridian-task.py may not be in scope for
    # every future caller of this shared function, and a logging failure here
    # must never block the actual wait/retry.
    if [ -n "${TASK_ID:-}" ]; then
      python3 /opt/veridian/scripts/veridian-task.py checkpoint "$TASK_ID" --status in_progress --note "USAGE_LIMIT_RETRY: Claude usage limit hit, auto-resuming at $resume_human (no manual restart needed)" >> "$log_file" 2>&1 || true
    fi

    sleep "$wait_seconds"
    echo "[USAGE_LIMIT_RETRY] Wait complete, automatically re-invoking claude (attempt $((attempt + 1)) of 2)." >> "$log_file"
    attempt=$((attempt + 1))
  done
}
