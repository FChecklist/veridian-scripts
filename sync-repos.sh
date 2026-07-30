#!/bin/bash
# Pulls latest commits for all mirrored repos. Safe: fast-forward only, never overwrites local changes.
set -uo pipefail
LOG=/opt/veridian/logs/sync-repos-$(date +%Y%m%d-%H%M%S).log
exec > "$LOG" 2>&1
echo "=== repo sync $(date -u) ==="
for repo in compliance-tracker claude-control projexa veda-advisors global-revenue-engine veridian-brain sumeet-spec; do
  echo "--- $repo ---"
  cd "/opt/veridian/repos/$repo" || { echo "MISSING DIR, skip"; continue; }
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "SKIPPED: uncommitted local changes present"
    continue
  fi
  git fetch --quiet origin
  git pull --ff-only --quiet && echo "OK: $(git rev-parse --short HEAD)" || echo "FAILED (non-fast-forward or network issue)"
done
echo "=== done $(date -u) ==="
find /opt/veridian/logs -name 'sync-repos-*.log' -mtime +14 -delete

# ai-os/SCRIPTS_LIVE_VS_REPO_DRIFT_AUDIT_2026-07-25.yaml: pulling claude-control
# above only updates the /opt/veridian/repos/claude-control checkout -- it never
# touched /opt/veridian/scripts/, the path every cron job and every direct
# `python3 /opt/veridian/scripts/X.py` invocation actually runs. Without this
# call, merged PRs had zero real effect on production behavior. See
# deploy-live-scripts.sh for exactly what it does and does not touch.
echo "--- deploy claude-control/scripts -> live scripts (git-tracked files only) ---"
/opt/veridian/scripts/deploy-live-scripts.sh
echo "deploy-live-scripts.sh exit code: $?"
