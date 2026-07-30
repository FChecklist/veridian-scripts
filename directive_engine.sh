#!/usr/bin/env bash
# Single merged DIRECTIVE engine. Launch:
#   screen -dmS directive_execution bash /opt/veridian/scripts/directive_engine.sh
# Requires resource_governor_tick (separate screen session) also running --
# that is the actual dispatcher draining what this submits.
set -uo pipefail
cd /opt/veridian
while true; do
  python3 /opt/veridian/scripts/directive_engine.py
  rc=$?
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [DIRECTIVE]: engine.py exited rc=$rc" >> /opt/veridian/ai-os/tasks/directive_status.log
  sleep 60
done
