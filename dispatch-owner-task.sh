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

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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

# 0. Coordination-graph conflict check (UMR-20260801-142246-8d51) -- surfaces,
#    but never blocks, an existing 'claims' relation on an overlapping
#    file_area/topic before this new task is even registered. TITLE is the
#    best real scope signal available at this call site (these titles are
#    already topic/file-scoped slugs, e.g. "crm--announcements",
#    "rebase-pr-618" -- not a literal path, but the same kind of scope
#    description ACTIVE-CLAIMS.yaml's own entries use). No --issue is passed
#    here deliberately -- REPO is not a real issue key anything 'addresses',
#    and check-conflict's issue filter is AND-only (would silently exclude
#    every real match rather than fall back to file_area-only matching), so
#    passing it here would make this check always report zero conflicts.
#    Warning-only by design, same as this script's own existing duplicate-
#    content check is a refusal but this is not -- a real conflict here is a
#    judgment call (maybe genuinely intentional parallel work), not an
#    automatic block.
CONFLICT_JSON=$(python3 superboss-register.py check-conflict --file-area "$TITLE" 2>/dev/null || echo '{"conflict_count":0,"conflicts":[]}')
CONFLICT_COUNT=$(echo "$CONFLICT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('conflict_count', 0))" 2>/dev/null || echo 0)
if [ "$CONFLICT_COUNT" != "0" ]; then
  echo "WARNING: check-conflict found $CONFLICT_COUNT existing claim(s) overlapping '$TITLE' -- review before proceeding:" >&2
  echo "$CONFLICT_JSON" >&2
fi

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

# 4.5. Register this task's own claim in the coordination graph, so the NEXT
#      dispatch's Step 0 check-conflict can actually find it -- this is what
#      keeps the graph live/current going forward instead of only reflecting
#      the one-time ACTIVE-CLAIMS.yaml backfill. Explicit status=open (not
#      just relying on log-relation's own bare-metadata entity creation) so
#      there's always a real status field to flip later -- see the note
#      below on WHY that flip matters.
python3 superboss-register.py log-entity --type task --key "$TASK_IDENTITY" \
  --metadata '{"status":"open"}' >/dev/null 2>&1 || true
python3 superboss-register.py log-relation --src-type task --src-key "$TASK_IDENTITY" \
  --dst-type file_area --dst-key "$TITLE" --type claims --created-by owner >/dev/null 2>&1 || true
# IMPORTANT for whoever closes this task out: once it's genuinely done
# (merged/closed/abandoned), run:
#   python3 superboss-register.py log-entity --type task --key "$TASK_IDENTITY" --metadata '{"status":"merged"}'
# (or "closed"/"abandoned") -- log-entity upserts metadata on an existing
# entity (unlike log-relation, which never touches it), and this is the ONLY
# thing that makes check-conflict's open/closed exclusion mean anything
# instead of every claim staying "open" forever.

echo "DISPATCHED: umr_id=$UMR_ID instruction_id=$INSTRUCTION_ID work_item_id=$WORK_ITEM_ID task_identity=$TASK_IDENTITY"

# 5. Relay into the live interactive tmux session -- same call, no separate
#    raw tmux send-keys step for anyone (or anything) to skip past.
if [ "$RELAY" -eq 1 ]; then
  if tmux has-session -t claude 2>/dev/null; then
    tmux send-keys -t claude -l "[${UMR_ID}] ${PROMPT}"
    sleep 1
    tmux send-keys -t claude Enter
    echo "RELAYED into tmux session 'claude'"
  else
    echo "WARNING: tmux session 'claude' not found -- task is registered (umr_id=$UMR_ID) but NOT yet delivered. Recreate the session and relay manually, or re-run once it exists." >&2
  fi
fi
