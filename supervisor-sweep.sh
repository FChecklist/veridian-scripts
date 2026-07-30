#!/bin/bash
# Safety net: catches any task stuck in pending_review with no review.json,
# meaning its immediate supervisor trigger was missed (crash, systemd hiccup).
# This is also the ONLY mechanism that discovers an `veridian-task.py adopt`ed
# task (a real, existing branch/PR registered outside the normal
# task-gateway.py dispatch flow, e.g. claude-control PR #84) -- adopt leaves
# it status=pending_review with no review.json on purpose, specifically so
# this loop picks it up on its next run.
#
# TASKS_DIR/LOG_DIR are overridable (VERIDIAN_TASKS_DIR/VERIDIAN_SWEEP_LOG_DIR)
# purely so tests/supervisor_sweep_discovery_test.sh can run this REAL script
# against a throwaway fixture instead of the live /opt/veridian/ai-os/tasks —
# production behavior (the defaults) is unchanged.
set -uo pipefail
TASKS_DIR="${VERIDIAN_TASKS_DIR:-/opt/veridian/ai-os/tasks}"
LOG_DIR="${VERIDIAN_SWEEP_LOG_DIR:-/opt/veridian/logs}"
LOG="$LOG_DIR/supervisor-sweep-$(date +%Y%m%d-%H%M%S).log"
exec > "$LOG" 2>&1
echo "=== supervisor sweep $(date -u) ==="

# --- SWEEP-DISCOVERY-BLOCK-START (see tests/supervisor_sweep_discovery_test.sh) ---
for task_dir in "$TASKS_DIR"/*/; do
  task_id=$(basename "$task_dir")
  [ -f "${task_dir}task.yaml" ] || continue
  status=$(python3 -c "import yaml; print(yaml.safe_load(open('${task_dir}task.yaml'))['status'])" 2>/dev/null || echo "")
  if [ "$status" = "pending_review" ] && [ ! -f "${task_dir}review.json" ]; then
    echo "Missed trigger found: $task_id — starting supervisor"
    systemctl --user daemon-reload
    systemctl --user start "veridian-supervisor@${task_id}.service"
  fi
done
# --- SWEEP-DISCOVERY-BLOCK-END ---

echo "=== done $(date -u) ==="
find "$LOG_DIR" -name 'supervisor-sweep-*.log' -mtime +14 -delete
