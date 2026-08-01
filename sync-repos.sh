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

# 2026-08-01: retired the claude-control/scripts -> live-scripts copy mechanism
# (deploy-live-scripts.sh). Root cause this closes: /opt/veridian/scripts is
# itself a real git working copy of FChecklist/veridian-scripts, but every
# cycle deploy-live-scripts.sh unconditionally overwrote same-named tracked
# files here with claude-control's older scripts/ subdirectory content --
# silently discarding real fixes merged into veridian-scripts (confirmed:
# the 2026-07-27 worker-boot-activation OOM fix and dispatch-tick.py's
# resume_interrupted_workers_tick never actually reached production because
# of this, despite being merged hours/days earlier). Pulling /opt/veridian/scripts
# directly here, the same fast-forward-only + dirty-skip pattern as every repo
# above, removes the two-repo drift entirely. claude-control's own scripts/
# subdirectory is retired -- see its README-RETIRED.md.
echo "--- veridian-scripts (live, /opt/veridian/scripts) ---"
cd /opt/veridian/scripts
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "SKIPPED: uncommitted local changes present"
else
  git fetch --quiet origin
  git pull --ff-only --quiet && echo "OK: $(git rev-parse --short HEAD)" || echo "FAILED (non-fast-forward or network issue)"
fi

echo "=== done $(date -u) ==="
find /opt/veridian/logs -name 'sync-repos-*.log' -mtime +14 -delete
