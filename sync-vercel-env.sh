#!/bin/bash
# Refreshes .env.local for the 3 linked Vercel projects with current production env vars.
set -uo pipefail
LOG=/opt/veridian/logs/sync-vercel-env-$(date +%Y%m%d-%H%M%S).log
exec > "$LOG" 2>&1

# 2026-07-29 cron-consolidation-phase6: shared concurrency gate (see
# sync-repos.sh for the full rationale -- same pattern, brief lock hold).
_DISPATCH_LOCK_PATH="/opt/veridian/ai-os/locks/worker-spawn.lock"
mkdir -p "$(dirname "$_DISPATCH_LOCK_PATH")"
exec 200>"$_DISPATCH_LOCK_PATH"
flock -x 200
python3 -c "
import sys
sys.path.insert(0, '/opt/veridian/scripts')
import dispatch_core
sys.exit(0 if dispatch_core.has_free_slot() else 1)
"
_CAP_OK=$?
flock -u 200
if [ "$_CAP_OK" -ne 0 ]; then
    echo "SKIP sync-vercel-env (cap reached): system at concurrency cap, deferring to next scheduled run"
    exit 0
fi

echo "=== vercel env sync $(date -u) ==="
VT=$(grep '^VERCEL_ACCESS_TOKEN=' /opt/veridian/shared/.env | cut -d= -f2)
SCOPE="meet-track-s-projects"
for repo in compliance-tracker projexa veda-advisors; do
  echo "--- $repo ---"
  cd "/opt/veridian/repos/$repo" || { echo "MISSING DIR, skip"; continue; }
  vercel env pull --token "$VT" --scope "$SCOPE" --yes .env.local 2>&1 | tail -5
done
echo "=== done $(date -u) ==="
find /opt/veridian/logs -name 'sync-vercel-env-*.log' -mtime +14 -delete
