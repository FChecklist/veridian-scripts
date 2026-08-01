#!/bin/bash
# Usage-limit auto-resume for the INTERACTIVE Claude Code tmux session
# (2026-08-01, same Owner directive as claude-usage-limit-retry.sh's header --
# see that file for the full detection rationale, this script reuses its
# exact detection/parsing logic, not a second copy of it).
#
# Reality check done before writing this (2026-08-01): the interactive
# session this Owner directive describes is `tmux attach -t claude` /
# `claude --continue --permission-mode bypassPermissions` (confirmed from
# `history`, not from a guess) -- it is currently NOT systemd-managed at all
# (no matching unit anywhere in ~/.config/systemd/user/), and as of this
# writing no `claude` tmux session is even running (`tmux ls` -> no server
# running). This script therefore does NOT start or own the session's
# lifecycle -- it only watches an EXISTING "claude" tmux session, if and when
# one exists, and sends the resume keystroke once the usage-limit window it
# detects has cleared. It is intentionally idempotent and safe to invoke
# repeatedly on a short interval (a few seconds of work, no long-lived sleep)
# so it fits a periodic-check model rather than needing its own daemon.
#
# Wiring note (deliberately NOT done by this PR): the live
# ~/.config/systemd/user/README.md documents a hard STANDING RULE -- this box
# runs a CLOSED SET of exactly 18 periodic systemd --user timer/service
# pairs, and a new periodic need that fits an existing cadence must be added
# as a new ExecStart step inside that existing unit, not a new 19th unit;
# a genuinely new unit requires an explicit Owner decision. This script's
# natural fit is a 2nd ExecStart line inside the existing
# veridian-cron-health-check-15min.service (already covers "systemd units,
# task staleness" on a 15-minute cadence). That unit file lives only on the
# server (not tracked in this git repo, confirmed by `git ls-files` in
# claude-control), so wiring it in is a follow-up the Owner/an operator with
# direct server access should do deliberately, not something this PR does
# silently to a live, untracked, already-audited config file. This script is
# committed here, ready to be called from that ExecStart line (or run by hand
# meanwhile: `bash /opt/veridian/scripts/claude-tmux-usage-limit-check.sh`).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./claude-usage-limit-retry.sh
source "$SCRIPT_DIR/claude-usage-limit-retry.sh"

TMUX_SESSION="${CLAUDE_TMUX_SESSION_NAME:-claude}"
STATE_DIR="/opt/veridian/ai-os/locks"
STATE_FILE="$STATE_DIR/claude-tmux-usage-limit-resume-epoch.txt"
LOG_FILE="/opt/veridian/ai-os/logs/claude-tmux-usage-limit-watchdog.log"
mkdir -p "$STATE_DIR" "$(dirname "$LOG_FILE")" 2>/dev/null || true

log() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $1" >> "$LOG_FILE"
}

if ! tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
  # No interactive session running right now -- nothing to watch. Clear any
  # stale wait-state left over from a session that has since gone away
  # (e.g. Owner killed and restarted it manually) so a brand-new session
  # never inherits a resume time that belonged to a previous one.
  if [ -f "$STATE_FILE" ]; then
    log "No tmux session '$TMUX_SESSION' found; clearing stale wait-state from $STATE_FILE."
    rm -f "$STATE_FILE"
  fi
  exit 0
fi

# Last ~200 lines of the pane is plenty to catch the usage-limit banner
# without re-scanning the session's entire scrollback on every run.
PANE_TEXT="$(tmux capture-pane -t "$TMUX_SESSION" -p -S -200 2>/dev/null || true)"

if ! echo "$PANE_TEXT" | claude_text_signals_usage_limit; then
  # Pane looks normal. If we were previously waiting (state file exists) but
  # the banner is gone -- most likely the Owner already resumed it by hand,
  # or a previous run of this script already sent the resume keystroke and
  # the CLI is back to normal -- clear the state so we don't act on stale data.
  if [ -f "$STATE_FILE" ]; then
    log "Usage-limit banner no longer present in session '$TMUX_SESSION' (resumed already, by hand or by this script); clearing wait-state."
    rm -f "$STATE_FILE"
  fi
  exit 0
fi

if [ ! -f "$STATE_FILE" ]; then
  # First time we've seen the banner for this occurrence: parse the resume
  # time and record it. Do NOT resume immediately even if resume_epoch ends
  # up <= now (the CLI just told us there's a live limit, so a same-run
  # resume risks hitting it again with no delay at all) -- always wait at
  # least until the NEXT scheduled check.
  RESUME_EPOCH="$(echo "$PANE_TEXT" | claude_usage_limit_parse_resume_epoch)"
  NOW_EPOCH="$(date +%s)"
  if [ -n "$RESUME_EPOCH" ] && [ "$RESUME_EPOCH" -gt "$NOW_EPOCH" ] 2>/dev/null; then
    WAIT_SECONDS=$((RESUME_EPOCH - NOW_EPOCH))
    if [ "$WAIT_SECONDS" -gt "$CLAUDE_USAGE_LIMIT_MAX_WAIT_SECONDS" ]; then
      RESUME_EPOCH=$((NOW_EPOCH + CLAUDE_USAGE_LIMIT_MAX_WAIT_SECONDS))
    fi
  else
    RESUME_EPOCH=$((NOW_EPOCH + CLAUDE_USAGE_LIMIT_FALLBACK_WAIT_SECONDS))
  fi
  echo "$RESUME_EPOCH" > "$STATE_FILE"
  log "Detected Claude usage-limit banner in tmux session '$TMUX_SESSION'. Recorded resume time $(date -d "@$RESUME_EPOCH" 2>/dev/null || echo "$RESUME_EPOCH"). Will send the resume keystroke automatically once this check runs again at/after that time -- no manual restart needed."
  exit 0
fi

RESUME_EPOCH="$(cat "$STATE_FILE" 2>/dev/null || echo 0)"
NOW_EPOCH="$(date +%s)"
if [ "$NOW_EPOCH" -ge "$RESUME_EPOCH" ] 2>/dev/null; then
  log "Resume time reached ($(date -d "@$RESUME_EPOCH" 2>/dev/null || echo "$RESUME_EPOCH")) for tmux session '$TMUX_SESSION' -- sending the resume command automatically."
  tmux send-keys -t "$TMUX_SESSION" "claude --continue --permission-mode bypassPermissions" Enter
  rm -f "$STATE_FILE"
  log "Resume command sent. Will re-detect a fresh banner on the next check if the limit is hit again."
else
  log "Still waiting on tmux session '$TMUX_SESSION' -- resume scheduled for $(date -d "@$RESUME_EPOCH" 2>/dev/null || echo "$RESUME_EPOCH"), $((RESUME_EPOCH - NOW_EPOCH))s remaining."
fi
