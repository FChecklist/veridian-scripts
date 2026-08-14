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
  # 2026-08-13 (task-20260813-103224, UMR-20260813-101142-5d24): report
  # precisely which live files a real pull changed -- "OK: <sha>" alone told
  # you HEAD moved but never what actually landed on disk, which is exactly
  # the fact a PM tier needs to certify INTEGRATED as real, not assumed.
  #
  # Real incident this also closes: this checkout was found (2026-08-13,
  # same task) sitting on an open, unmerged PR's worker branch (PR #292,
  # a DIFFERENT task) instead of main -- `git pull --ff-only` only
  # fast-forwards the CURRENT branch against ITS OWN upstream, so it kept
  # silently reporting "OK: <sha>" every cycle while never once pulling
  # main, and production ran unreviewed/unmerged code for real real-world
  # time. Loudly flag this instead of silently no-op'ing: this does NOT
  # auto-checkout main itself (a checkout/branch switch on this box has
  # previously overwritten live files still in use by a running systemd
  # unit -- see check_live_scripts_drift.py's own docstring / this task's
  # PROGRESS.md -- that decision needs a real human/Owner call, not a
  # silent auto-switch here), it only makes the drift impossible to miss.
  CUR_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  if [ "$CUR_BRANCH" != "main" ]; then
    echo "WARNING: live checkout is on branch '$CUR_BRANCH', NOT main -- this pull only fast-forwards that branch's own upstream, it does NOT pull origin/main. Run check_live_scripts_drift.py for the real divergence. NOT auto-switching (known hazard: branch switches here have previously overwritten live files a running systemd unit depends on)."
  fi
  PRE_SHA="$(git rev-parse HEAD)"
  git fetch --quiet origin
  if git pull --ff-only --quiet; then
    POST_SHA="$(git rev-parse HEAD)"
    echo "OK: $POST_SHA"
    if [ "$PRE_SHA" != "$POST_SHA" ]; then
      echo "CHANGED FILES ($PRE_SHA..$POST_SHA):"
      git diff --name-status "$PRE_SHA" "$POST_SHA"
    else
      echo "CHANGED FILES: none (already up to date)"
    fi
  else
    echo "FAILED (non-fast-forward or network issue)"
  fi
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
