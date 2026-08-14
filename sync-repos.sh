#!/bin/bash
# Pulls latest commits for all mirrored repos. Safe: fast-forward only, never overwrites local changes.
set -uo pipefail
LOG=/opt/veridian/logs/sync-repos-$(date +%Y%m%d-%H%M%S).log
exec > "$LOG" 2>&1
echo "=== repo sync $(date -u) ==="

# --- Critical live-checkout sync (task-20260814-095433-make-both-live-checkouts-auto-sync-after,
# UMR-20260814-095405-2b53) -----------------------------------------------
# claude-control (/opt/veridian/repos/claude-control) and this checkout
# itself (/opt/veridian/scripts, veridian-scripts) are the two checkouts
# every worker/cron/dispatch unit on this box actually EXECUTES CODE FROM
# -- unlike the read-mostly mirrors in the loop below (compliance-tracker,
# projexa, veda-advisors, global-revenue-engine, veridian-brain,
# sumeet-spec), letting either one drift from its remote default branch
# means merged PRs are reviewed+merged but never actually running.
#
# Real evidence gathered 2026-08-14T09:50Z (this task's own SPEC):
# claude-control was 16 commits behind origin/master and
# /opt/veridian/scripts was 6 commits behind origin/main AT THE SAME TIME
# this unit's own paired .timer was enabled+active. Root cause was NOT a
# missing job -- it was (a) a 2-hour cadence that same-day merge volume
# outran, and (b) a real, CORRECT refuse-to-clobber dirty-skip (not a bug)
# that, at the old cadence, then had to wait up to 2h for the next retry
# instead of self-healing within minutes. See the paired .timer
# (systemd/veridian-cron-sync-repos.timer) for the cadence fix; this
# function is the robustness fix -- one shared, tested code path for both
# critical checkouts instead of two slightly different copies of the same
# refuse-to-clobber / wrong-branch / idempotent-pull logic (the
# /opt/veridian/scripts block below used to carry its own bespoke version
# of this same logic, added 2026-08-13 UMR-20260813-101142-5d24; that
# history is preserved here, not deleted).
#
# Args: $1 = absolute checkout path, $2 = expected remote-tracking branch.
# Side effect: sets the global CRITICAL_SYNC_OK to 0 if this checkout is
# NOT left at its remote branch head afterwards (dirty, wrong branch, or a
# failed pull) -- read once after both calls, at the bottom of this script,
# to decide this script's own exit code. That is what makes a drifted
# critical checkout show up as a FAILED run in the register
# (superboss-register.sqlite actions table, via run-logged.sh's own
# job_end log-action call) instead of being buried only in this log file.
CRITICAL_SYNC_OK=1

sync_critical_checkout() {
  local path="$1" branch="$2"
  echo "--- $(basename "$path") (live, $path) ---"
  cd "$path" || { echo "MISSING DIR, skip"; CRITICAL_SYNC_OK=0; return; }

  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "SKIPPED: uncommitted local changes present -- refusing to clobber. Real diff below (report, not silence):"
    git --no-pager diff --stat -- . 2>&1
    git --no-pager diff --cached --stat -- . 2>&1
    CRITICAL_SYNC_OK=0
    return
  fi

  # 2026-08-13 (task-20260813-103224, UMR-20260813-101142-5d24), now applied
  # to BOTH critical checkouts instead of just this one: `git pull
  # --ff-only` only fast-forwards the CURRENT branch against ITS OWN
  # upstream -- if the checkout is sitting on some other (e.g. an open
  # worker) branch, this would keep silently reporting "OK: <sha>" every
  # cycle while never once pulling the real remote default branch. Loudly
  # flag instead of silently no-op'ing. Deliberately NOT auto-checking-out
  # the expected branch here (known hazard: a branch switch on this box has
  # previously overwritten live files a running systemd unit depends on) --
  # that remains a real human/Owner call.
  local cur_branch
  cur_branch="$(git rev-parse --abbrev-ref HEAD)"
  if [ "$cur_branch" != "$branch" ]; then
    echo "WARNING: live checkout is on branch '$cur_branch', NOT '$branch' -- this pull only fast-forwards that branch's own upstream, it does NOT pull origin/$branch. NOT auto-switching (known hazard: branch switches here have previously overwritten live files a running systemd unit depends on)."
    CRITICAL_SYNC_OK=0
    return
  fi

  local pre_sha post_sha behind
  pre_sha="$(git rev-parse HEAD)"
  git fetch --quiet origin
  behind="$(git rev-list --count "HEAD..origin/$branch" 2>/dev/null || echo '?')"
  if [ "$behind" = "0" ]; then
    echo "OK: $pre_sha (already up to date, 0 commits behind origin/$branch -- idempotent no-op)"
    return
  fi

  if git pull --ff-only --quiet; then
    post_sha="$(git rev-parse HEAD)"
    echo "OK: $post_sha (was $behind commit(s) behind origin/$branch)"
    if [ "$pre_sha" != "$post_sha" ]; then
      echo "CHANGED FILES ($pre_sha..$post_sha):"
      git diff --name-status "$pre_sha" "$post_sha"
    fi
  else
    echo "FAILED (non-fast-forward or network issue) -- was $behind commit(s) behind origin/$branch"
    CRITICAL_SYNC_OK=0
  fi
}

for repo in compliance-tracker claude-control projexa veda-advisors global-revenue-engine veridian-brain sumeet-spec; do
  if [ "$repo" = "claude-control" ]; then
    sync_critical_checkout "/opt/veridian/repos/claude-control" "master"
    continue
  fi
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
sync_critical_checkout "/opt/veridian/scripts" "main"

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

# task-20260814-095433: a drifted critical checkout must be visible as a
# real failed run, not just a buried log line -- run-logged.sh (this
# script's own ExecStart wrapper) logs "failed" vs "completed" to the
# register purely off this exit code.
if [ "$CRITICAL_SYNC_OK" -ne 1 ]; then
  echo "CRITICAL: claude-control and/or /opt/veridian/scripts did not end this run at their remote branch head -- see the SKIPPED/WARNING/FAILED line(s) above for which one and why. Exiting non-zero."
  exit 1
fi
