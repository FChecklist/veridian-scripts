#!/usr/bin/env bash
set -uo pipefail
while true; do
  python3 /opt/veridian/scripts/resource_governor.py --tick >> /opt/veridian/ai-os/tasks/resource_governor_tick.log 2>&1
  # Stage 3 (2026-07-29): reconciliation pass for the "task exits cleanly but
  # umr_tasks status never reconciles" bug. Scoped internally to rows with a
  # real, stale last_heartbeat only (NULL heartbeats -- e.g. all 5 real
  # in-flight tasks at the moment this deploys -- are always skipped, see
  # resource_governor.py's reconcile_stale_heartbeats() docstring).
  python3 /opt/veridian/scripts/resource_governor.py --reconcile-stale >> /opt/veridian/ai-os/tasks/resource_governor_tick.log 2>&1
  sleep 30
done
