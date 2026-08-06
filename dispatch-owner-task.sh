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
# UMR-20260806-085144-9c63 (prevention side of the owner_dispatch_gateway
# stuck-at-'queued' finding; reconciliation side of already-stale rows is
# PR #147 / UMR-20260806-082646-3aba, out of scope here): once the real tmux
# relay below either succeeds or is confirmed absent, this script now writes
# a real terminal-or-dispatched status back onto the umr_id it just minted,
# via superboss-register.py's mark-umr-dispatched / mark-umr-terminal CLI
# subcommands (never a raw SQL write) -- so a row that really was delivered
# stops sitting at status='queued'/ts_dispatched=NULL forever, and a row
# whose relay genuinely failed is marked status='failed' with a real reason
# instead of being left to silently look identical to "not yet delivered."
#
# To record real completion once work against a dispatched UMR genuinely
# finishes (worker or interactive session, run this by hand or from your own
# completion hook):
#   python3 superboss-register.py mark-umr-terminal --umr-id UMR-... \
#       --status completed [--reason "what finished"]
#   (--status also accepts failed / killed for other genuine terminal outcomes)
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
if [ "$RELAY" -eq 1 ]; then
  if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
    tmux send-keys -t "$TMUX_SESSION" -l "[${UMR_ID}] ${PROMPT}"
    sleep 1
    tmux send-keys -t "$TMUX_SESSION" Enter
    echo "RELAYED into tmux session '$TMUX_SESSION'"
    # 6. Real relay genuinely succeeded -- record it on the umr_tasks row this
    #    same call minted, so it stops sitting at status='queued' forever.
    python3 superboss-register.py mark-umr-dispatched --umr-id "$UMR_ID" >/dev/null
    echo "MARKED DISPATCHED: umr_id=$UMR_ID (ts_dispatched written)"
  else
    echo "WARNING: tmux session '$TMUX_SESSION' not found -- task is registered (umr_id=$UMR_ID) but NOT yet delivered. Recreate the session and relay manually, or re-run once it exists." >&2
    # 6. Real relay genuinely failed -- record a real 'failed' status with a
    #    real reason instead of silently leaving the row at 'queued' forever.
    python3 superboss-register.py mark-umr-terminal --umr-id "$UMR_ID" --status failed \
      --reason "tmux session '$TMUX_SESSION' not found at relay time" >/dev/null
    echo "MARKED FAILED: umr_id=$UMR_ID (relay could not be delivered)" >&2
  fi
fi
