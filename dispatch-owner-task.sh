#!/usr/bin/env bash
# dispatch-owner-task.sh -- single front door for dispatching real Owner-directed
# work to the server, whether relayed by a Claude Code CLI laptop session or run
# directly by the Owner via SSH/PowerShell. Chains the existing instruction /
# work-item / UMR registration pipeline (superboss-register.py +
# resource_governor.py), then relays the same UMR-tagged message directly into
# the live interactive tmux session in the SAME call -- there is no separate
# "raw tmux send-keys" step left to accidentally use instead of this script.
# Every call either returns a real umr_id (and relays it), or refuses with a
# clear reason (duplicate content, or resource_governor.py rejection) -- it
# never silently does nothing.
#
# Usage: dispatch-owner-task.sh "<short title>" "<full prompt text>" [tier] [medium] [repo] [--no-relay]
#   tier       - resource_governor.py tier, 0 (highest) .. 4 (lowest); default 2
#   medium     - "claude_code_cli" (default, laptop-relayed) or "ssh_session"
#                (Owner running this directly by hand)
#   repo       - target repo for veridian-task.py create; default compliance-tracker
#   --no-relay - register only, do not deliver into the tmux session (e.g. for
#                pure background-worker dispatch with no interactive session
#                involvement). Omit this and relay happens by default.
#
# UMR-20260806-115423-500d (real narrowing of UMR-20260806-085144-9c63 /
# PR #150, read this before touching the relay block below): a successful
# `tmux send-keys` proves only that keystrokes were written into a pane --
# NEVER that a live process actually read and acted on them. This script's
# own "RELAYED into tmux session..." line is therefore a best-effort
# courtesy notification, not proof of delivery, full stop. It used to also
# write status='dispatched' (relay succeeded) or a real terminal
# status='failed' (tmux session absent) straight onto the umr_id it just
# minted -- but BOTH of those writes independently remove the row from
# resource_governor.py's `next_queued_task()` query (`SELECT * FROM
# umr_tasks WHERE status='queued'`), which is the ONLY function that
# mechanically dispatches a queued veridian_task_create row to a real,
# independent `veridian-worker@*.service` via `_perform_spawn()` --
# confirmed live by reading next_queued_task()/_perform_spawn() directly:
# this mechanical path has zero tmux involvement and works whether or not
# any interactive session exists. So a row whose relay keystrokes landed in
# a dead/wrong/busy pane was silently and PERMANENTLY excluded from the one
# channel that could still have picked it up -- a real dead zone.
#
# The fix: a successful (or absent-session) tmux relay now records a real,
# honest courtesy signal via superboss-register.py's mark-umr-relay-attempted
# CLI subcommand (never a raw SQL write) -- which writes ONLY
# ts_relay_attempted/relay_outcome/relay_detail, and NEVER touches `status`,
# `ts_dispatched`, or `ts_completed`. A row stays exactly status='queued'
# after either branch below, fully eligible for dispatch-tick.py's own real
# mechanical pickup on the very next tick, no matter what the tmux relay
# did or didn't achieve. The ONLY legitimate queued -> other status
# transition is that real mechanical pickup (or a genuine mark-umr-terminal
# call recording real completed work -- see below); a printed RELAYED
# message is never, by itself, that transition.
#
# mark-umr-dispatched (status='dispatched') still exists as its own real
# CLI command for a genuinely different, future use -- a non-interactive
# channel that can positively confirm delivery -- it is simply no longer
# called from this script's own relay branches.
#
# To record real completion once work against a dispatched UMR genuinely
# finishes (worker or interactive session, run this by hand or from your own
# completion hook):
#   python3 superboss-register.py mark-umr-terminal --umr-id UMR-... \
#       --status completed [--reason "what finished"]
#   (--status also accepts failed / killed for other genuine terminal outcomes)
#
# UMR-20260806-112013-088f (structural fix, second half of the above
# UMR-20260806-085144-9c63 finding): the paragraph just above was, until
# this change, the *only* place this requirement was written down -- an
# optional-looking doc comment nothing downstream was ever required to
# read, which is the real, confirmed reason 29+ real dispatched rows sat at
# status='dispatched'/ts_completed=NULL indefinitely even when the
# underlying work had genuinely finished. Every real tmux relay below now
# appends a mandatory, UMR-id-specific completion instruction (naming this
# exact mark-umr-terminal command) directly onto the relayed prompt text
# itself, so it travels with the task instead of living only here. There is
# no systemd unit to hook an ExecStopPost= into for this dispatch path --
# task_kind='veridian_task_create' rows are relayed to a live interactive
# session, never started as a systemd unit (only task_kind='systemctl_action'
# rows are, a separate resource_governor.py path) -- so the relayed-prompt
# instruction is the real mechanism here, not a substitute for a better one.
#
# DISPATCH_TMUX_SESSION - overrides the tmux session name relayed into
#   (default: claude, the real live interactive session). Exists solely as a
#   real testability seam: point it at a disposable session (e.g.
#   `tmux new-session -d -s claude-relay-test-throwaway`) to exercise the
#   relay-succeeds/relay-fails branching end-to-end without ever sending
#   real keystrokes into the real live session.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TMUX_SESSION="${DISPATCH_TMUX_SESSION:-claude}"
LOCKS_DIR="${VERIDIAN_DISPATCH_LOCK_DIR:-/opt/veridian/ai-os/locks}"
mkdir -p "$LOCKS_DIR"
TMUX_RELAY_LOCK="$LOCKS_DIR/dispatch-owner-task-tmux-relay-${TMUX_SESSION}.lock"

RELAY=1
ARGS=()
for a in "$@"; do
  if [ "$a" = "--no-relay" ]; then
    RELAY=0
  else
    ARGS+=("$a")
  fi
done
set -- "${ARGS[@]}"

TITLE="${1:?Usage: dispatch-owner-task.sh \"<title>\" \"<prompt>\" [tier] [medium] [repo] [--no-relay]}"
PROMPT="${2:?Usage: dispatch-owner-task.sh \"<title>\" \"<prompt>\" [tier] [medium] [repo] [--no-relay]}"
TIER="${3:-2}"
MEDIUM="${4:-claude_code_cli}"
REPO="${5:-compliance-tracker}"

cd "$SCRIPT_DIR"

# 1. Duplicate check -- don't silently re-dispatch the same ask.
DUP_JSON=$(python3 superboss-register.py check-content-duplicate --text "$PROMPT" --window-hours 6)
DUP_FOUND=$(echo "$DUP_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['content_duplicate_found'])")
if [ "$DUP_FOUND" = "True" ]; then
  echo "$DUP_JSON"
  echo "REFUSED: an identical instruction was already logged within the last 6 hours (see duplicate_instruction_id above). Re-run with a genuinely different prompt if this repeat is intentional." >&2
  exit 1
fi

# 2. Log the raw ask (input side of the Owner<->AI operational dialogue).
INS_JSON=$(python3 superboss-register.py log-instruction --text "$PROMPT" --source owner --medium "$MEDIUM")
INSTRUCTION_ID=$(echo "$INS_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['instruction_id'])")

# 3. Register the real task with resource_governor.py -- this is what actually
#    gets it a UMR ID and puts it under governance (concurrency cap, EMERGENCY_STOP).
TASK_IDENTITY="owner-task-$(date -u +%Y%m%d-%H%M%S)-$$"
SPEC_FILE="$(mktemp)"
python3 -c "
import json, sys
json.dump({
    'task_identity': sys.argv[1],
    'task_kind': 'veridian_task_create',
    'inputs': {'title': sys.argv[2], 'prompt': sys.argv[3], 'repo': sys.argv[4]},
}, open(sys.argv[5], 'w'))
" "$TASK_IDENTITY" "$TITLE" "$PROMPT" "$REPO" "$SPEC_FILE"

SUBMIT_JSON=$(python3 resource_governor.py --submit --spec-file "$SPEC_FILE" --tier "$TIER" --source-trigger owner_dispatch_gateway)
rm -f "$SPEC_FILE"
ACCEPTED=$(echo "$SUBMIT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['accepted'])")
UMR_ID=$(echo "$SUBMIT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['umr_id'])")

if [ "$ACCEPTED" != "True" ]; then
  echo "$SUBMIT_JSON"
  echo "REJECTED by resource_governor.py -- see reason above (umr_id=$UMR_ID recorded the rejection itself)." >&2
  exit 1
fi

# 4. Link instruction -> work item -> the real UMR id (output side).
WORK_JSON=$(python3 superboss-register.py log-work --instruction-id "$INSTRUCTION_ID" --ai-task-id "$UMR_ID" --source owner --medium "$MEDIUM" --status open)
WORK_ITEM_ID=$(echo "$WORK_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['work_item_id'])")

echo "DISPATCHED: umr_id=$UMR_ID instruction_id=$INSTRUCTION_ID work_item_id=$WORK_ITEM_ID task_identity=$TASK_IDENTITY"

# 5. Relay into the live interactive tmux session -- same call, no separate
#    raw tmux send-keys step for anyone (or anything) to skip past.
#
# UMR-20260806-094226-8617 (real root cause of the input-line-sticking
# finding): two concurrent dispatch-owner-task.sh invocations targeting the
# same tmux session had no mutual exclusion around their send-keys calls --
# this session has directly observed multiple near-simultaneous duplicate
# dispatches (owner_dispatch_gateway bursts within milliseconds of each
# other) this same day, and without a lock their literal `-l` text sends
# could genuinely interleave in the target pane's input buffer, leaving a
# garbled/unsubmitted line that only a manual Enter would clear -- never
# fixed at the root before this. Real flock on a per-session lock file
# serializes the has-session-check + both send-keys calls as one atomic
# unit across concurrent invocations, same real per-open-file-description
# discipline superboss-register.py's own _write_lock() already documents.
#
# UMR-20260806-112013-088f (structural fix for "nothing ever calls
# mark-umr-terminal", second half of UMR-20260806-085144-9c63 / PR #150):
# every real dispatch through this script is task_kind='veridian_task_create',
# relayed straight into a live interactive tmux session -- but (correction,
# UMR-20260806-115423-500d: the claim this comment used to make here, that
# these rows "never get a backing systemd unit," is false -- confirmed by
# reading resource_governor.py's _perform_spawn() directly, which DOES spawn
# a real `veridian-worker@*.service` for task_kind='veridian_task_create'
# rows exactly as it does for 'systemctl_action' rows) that systemd unit, if
# and when it gets spawned, belongs to resource_governor.py's own mechanical
# pickup, is created by *that* code path, not by this script, and this
# script has already returned long before it would exist -- so there is
# still no real ExecStopPost= hook available for *this script* to attach
# completion-recording to at relay time. The one real structural seam that
# exists here is the relayed text itself: appending a
# mandatory, UMR-id-specific final instruction to every relayed prompt puts
# the exact real command in front of whoever/whatever is doing the work, in
# the same message that carries the task -- not in a header comment several
# dozen lines above that nothing downstream was ever required to read
# (which is the real, confirmed root cause: mark-umr-terminal already
# existed and was already documented there, and 29+ real dispatched rows
# still sat at ts_completed=NULL). This cannot force an interactive session
# to record its own outcome honestly -- no code can -- but it removes "I
# didn't know I was supposed to" as a real, provable failure mode.
COMPLETION_INSTRUCTION="

MANDATORY FINAL STEP for ${UMR_ID} (structural, not optional -- read this even if the rest of this message was skimmed): once the real work above reaches a genuine terminal outcome, record it by running this exact command with this exact UMR id (from the veridian-scripts repo, e.g. /opt/veridian/repos/veridian-scripts):
  python3 superboss-register.py mark-umr-terminal --umr-id ${UMR_ID} --status completed --reason \"<real one-line summary of what finished>\"
Use --status failed or --status killed instead of completed, with a real --reason, if the work genuinely did not finish successfully -- never record a genuine failure as a success. Run this as the actual last action for ${UMR_ID}, after any PR/merge work, not before. Leave it unrun only if the work is genuinely still in progress."
RELAY_TEXT="[${UMR_ID}] ${PROMPT}${COMPLETION_INSTRUCTION}"
if [ "$RELAY" -eq 1 ]; then
  exec 9>"$TMUX_RELAY_LOCK"
  flock -x 9
  if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
    tmux send-keys -t "$TMUX_SESSION" -l "$RELAY_TEXT"
    sleep 1
    tmux send-keys -t "$TMUX_SESSION" Enter
    flock -u 9
    exec 9>&-
    echo "RELAYED into tmux session '$TMUX_SESSION' (best-effort courtesy notification ONLY -- send-keys returning 0 proves the keystrokes were written into the pane, NOT that any live process read or acted on them; this is never proof of delivery)"
    # 6. Real relay attempt recorded as a courtesy signal (UMR-20260806-115423-500d)
    #    -- ts_relay_attempted/relay_outcome/relay_detail ONLY, status/
    #    ts_dispatched/ts_completed are NEVER touched here. The row stays
    #    exactly status='queued', fully eligible for dispatch-tick.py's own
    #    real mechanical pickup (resource_governor.py's next_queued_task())
    #    on the very next tick, regardless of what the tmux relay achieved.
    python3 superboss-register.py mark-umr-relay-attempted --umr-id "$UMR_ID" \
      --outcome sent --detail "tmux session '$TMUX_SESSION'" >/dev/null
    echo "RELAY ATTEMPTED (courtesy only, NOT authoritative): umr_id=$UMR_ID -- row remains status='queued', pollable by dispatch-tick.py's own real mechanical pickup"
  else
    flock -u 9
    exec 9>&-
    echo "WARNING: tmux session '$TMUX_SESSION' not found -- task is registered (umr_id=$UMR_ID) and remains status='queued', pollable by dispatch-tick.py's own real mechanical pickup regardless of this tmux relay's outcome. Recreate the session and relay manually, or re-run once it exists, if you also want the interactive channel to see it." >&2
    # 6. Real relay attempt (absent-session outcome) recorded as the same
    #    non-authoritative courtesy signal as the success branch above --
    #    NEVER a terminal status. Absence of an interactive tmux session
    #    says nothing about whether the real mechanical pickup path can
    #    still do this work; marking the row terminal here would wrongly
    #    exclude it from that entirely independent channel too.
    python3 superboss-register.py mark-umr-relay-attempted --umr-id "$UMR_ID" \
      --outcome session_not_found --detail "tmux session '$TMUX_SESSION' not found at relay time" >/dev/null
    echo "RELAY ATTEMPTED, session absent (courtesy only, NOT authoritative): umr_id=$UMR_ID -- row remains status='queued', pollable by dispatch-tick.py's own real mechanical pickup" >&2
  fi
fi
