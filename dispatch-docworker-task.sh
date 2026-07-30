#!/bin/bash
# Atomic docworker dispatch, 2026-07-20. Closes a confirmed, RECURRING gap:
# `veridian-task.py create` auto-starts the wrong GLM-routed
# veridian-worker@ unit before a manual `disable` (run as a separate,
# later command) can catch it -- confirmed twice tonight, 3 real restarts
# each time on tasks dispatched just this session, not a one-time fluke.
# This script does create -> stop(not just disable) the wrong unit ->
# fix task.yaml's service field -> enable+start the correct docworker unit
# as ONE sequence, so the gap between "wrong unit might start" and "wrong
# unit is stopped" can't be skipped by a future dispatch forgetting a step.
set -euo pipefail
TITLE="$1"
REPO="$2"
PROMPT_FILE="$3"

# 2026-07-29 Stage-10-gate remediation: this script had zero governance --
# no resource_governor.py involvement of any kind. This is a narrow, manual,
# one-shot utility (not queue-driven, so no natural task_identity to
# cross-check for dedup the way dispatch-tick.py/phase-continuation-tick.py
# now do), so the real, applicable protection here is the EMERGENCY_STOP
# check -- reads resource_governor.py's own real constant rather than
# hardcoding the sentinel path, so it can never drift out of sync with it.
EMERGENCY_STOP_PATH=$(python3 -c "
import importlib.util
spec = importlib.util.spec_from_file_location('rg', '/opt/veridian/scripts/resource_governor.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(mod.EMERGENCY_STOP_PATH)
")
if [ -f "$EMERGENCY_STOP_PATH" ]; then
  echo "ERROR: resource_governor.py EMERGENCY_STOP sentinel present at $EMERGENCY_STOP_PATH -- refusing to dispatch a new docworker task. Clear it via 'resource_governor.py --clear-emergency-stop' only once real metrics justify it, then re-run this script." >&2
  exit 1
fi

CREATE_OUT=$(python3 /opt/veridian/scripts/veridian-task.py create --title "$TITLE" --repo "$REPO" --prompt "$(cat "$PROMPT_FILE")" 2>&1)
echo "$CREATE_OUT"
TASK_ID=$(echo "$CREATE_OUT" | grep '^CREATED:' | sed 's/^CREATED: //')
if [ -z "$TASK_ID" ]; then
  echo "ERROR: could not parse TASK_ID from create output" >&2
  exit 1
fi

# Stop (not just disable) immediately -- disable alone does not kill an
# already-started instance, confirmed the real cause of the recurring gap.
systemctl --user stop "veridian-worker@${TASK_ID}.service" 2>&1 || true
systemctl --user disable "veridian-worker@${TASK_ID}.service" 2>&1 || true

sed -i 's/service: veridian-worker@/service: veridian-docworker@/' "/opt/veridian/ai-os/tasks/${TASK_ID}/task.yaml"

systemctl --user enable "veridian-docworker@${TASK_ID}.service" 2>&1
systemctl --user start "veridian-docworker@${TASK_ID}.service" 2>&1

sleep 2
echo "--- final state ---"
echo "wrong unit (veridian-worker@): $(systemctl --user is-active veridian-worker@${TASK_ID}.service 2>&1)"
echo "correct unit (veridian-docworker@): $(systemctl --user is-active veridian-docworker@${TASK_ID}.service 2>&1)"
echo "TASK_ID=${TASK_ID}"
