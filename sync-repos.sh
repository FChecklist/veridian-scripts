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

# 2026-08-02 (PM decision UMR-20260802-083104-5987, MASTER_INDEX.yaml
# divergence investigation UMR-20260802-080051-6e48): /opt/veridian/ai-os is
# itself a real git working copy of FChecklist/veridian-ai-os (created
# 2026-07-30 as an initial snapshot) but was never added to this sync loop --
# a structural gap, not a transient failure, confirmed by direct read of this
# script before this fix. Real consequence: MASTER_INDEX.yaml (this
# directory's own stated "read this first" entrypoint) drifted ~3 days
# out of sync with its own git history, with no automated mechanism to
# either notice or correct it. Same fast-forward-only + dirty-skip pattern
# as every repo above and as veridian-scripts directly above this block --
# this only PULLS (picks up anything already merged upstream); it does not
# push local edits. Local edits to files under /opt/veridian/ai-os still need
# a real commit+push by whoever makes them (same discipline already expected
# for /opt/veridian/scripts), same as any other tracked repo -- this fix
# closes "silently drifts forever with nothing pulling it back into sync",
# not "nobody has to commit their own edits."
echo "--- veridian-ai-os (live, /opt/veridian/ai-os) ---"
cd /opt/veridian/ai-os
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "SKIPPED: uncommitted local changes present"
else
  git fetch --quiet origin
  git pull --ff-only --quiet && echo "OK: $(git rev-parse --short HEAD)" || echo "FAILED (non-fast-forward or network issue)"
fi

echo "=== done $(date -u) ==="
find /opt/veridian/logs -name 'sync-repos-*.log' -mtime +14 -delete
