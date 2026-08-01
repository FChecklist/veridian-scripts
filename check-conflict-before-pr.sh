#!/usr/bin/env bash
# check-conflict-before-pr.sh -- the second real integration point for the
# coordination graph (UMR-20260801-142246-8d51), replacing a manual
# ACTIVE-CLAIMS.yaml read with a queryable check. Run this before `gh pr
# create` for any real gap/task, same spirit as AGENTS.md Rule 11 in
# compliance-tracker ("read ai-os/boss/ACTIVE-CLAIMS.yaml before selecting a
# gap/task"), but there is no single centralized "PR-open" script in this
# codebase to wire this into automatically (every worker session runs its own
# `gh pr create` directly) -- this is the runnable replacement for that
# manual read, callable from any session/task, not a background hook.
#
# Never blocks -- prints a clear warning and a non-zero exit code so a caller
# CAN choose to treat it as a gate, but nothing forces that; same
# warning-only posture as dispatch-owner-task.sh's own Step 0.
#
# Usage: check-conflict-before-pr.sh "<file_area>" ["<issue-or-task-key>"]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FILE_AREA="${1:?Usage: check-conflict-before-pr.sh \"<file_area>\" [\"<issue-or-task-key>\"]}"
ISSUE="${2:-}"

cd "$SCRIPT_DIR"
if [ -n "$ISSUE" ]; then
  RESULT=$(python3 superboss-register.py check-conflict --file-area "$FILE_AREA" --issue "$ISSUE")
else
  RESULT=$(python3 superboss-register.py check-conflict --file-area "$FILE_AREA")
fi

COUNT=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('conflict_count', 0))")
echo "$RESULT"

if [ "$COUNT" != "0" ]; then
  echo "WARNING: $COUNT existing claim(s) overlap '$FILE_AREA' -- read the conflicts above before opening this PR. This does not block you; it replaces the manual ACTIVE-CLAIMS.yaml read AGENTS.md Rule 11 asks for." >&2
  exit 1
fi
exit 0
