#!/usr/bin/env bash
# dispatch-owner-task.sh -- single front door for dispatching real Owner-directed
# work to the server, whether relayed by a Claude Code CLI laptop session or run
# directly by the Owner via SSH/PowerShell. Chains the existing instruction /
# work-item / UMR registration pipeline (superboss-register.py +
# resource_governor.py) instead of bypassing it with a raw tmux send-keys nudge
# or an ad-hoc SSH command. Every call either returns a real umr_id, or refuses
# with a clear reason (duplicate content, or resource_governor.py rejection) --
# it never silently does nothing.
#
# Usage: dispatch-owner-task.sh "<short title>" "<full prompt text>" [tier] [medium] [repo]
#   tier   - resource_governor.py tier, 0 (highest) .. 4 (lowest); default 2
#   medium - "claude_code_cli" (default, laptop-relayed) or "ssh_session"
#            (Owner running this directly by hand)
#   repo   - target repo for veridian-task.py create; default compliance-tracker

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TITLE="${1:?Usage: dispatch-owner-task.sh \"<title>\" \"<prompt>\" [tier] [medium] [repo]}"
PROMPT="${2:?Usage: dispatch-owner-task.sh \"<title>\" \"<prompt>\" [tier] [medium] [repo]}"
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
