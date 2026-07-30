#!/bin/bash
# Closes the drift found in ai-os/SCRIPTS_LIVE_VS_REPO_DRIFT_AUDIT_2026-07-25.yaml:
# sync-repos.sh only ever did `git pull --ff-only` inside /opt/veridian/repos/<repo>/
# -- nothing copied those merged changes into /opt/veridian/scripts/, the actual path
# every cron job and every direct `python3 /opt/veridian/scripts/X.py` invocation runs.
#
# Mechanism: copy every file `git ls-files` reports as tracked under
# claude-control's scripts/ to the matching path under LIVE_DIR, overwriting
# whatever is there. Anything NOT tracked in git (crontab backups, .bak files,
# cost/credit accounting scripts, the OpenRouter proxy, __pycache__, etc.) is by
# definition absent from `git ls-files` and this script never touches it --
# no rsync --delete, no directory mirroring, no rm. This is the "preserve
# operational-only live files" guarantee: it is structural (derived from what
# git tracks), not a hand-maintained exclude list that can silently go stale.
#
# Before overwriting a live file whose content actually differs from the repo
# version, the previous live content is saved as <file>.bak-predeploy-<ts>,
# consistent with this directory's existing .bak-YYYY-MM-DD-<reason> convention.
# Deletions of tracked-then-removed files are intentionally NOT handled here --
# that is a separate, higher-risk decision this script does not make silently.
set -uo pipefail

REPO_DIR="/opt/veridian/repos/claude-control"
LIVE_DIR="/opt/veridian/scripts"
TS="$(date -u +%Y%m%d-%H%M%S)"
LOG="/opt/veridian/logs/deploy-live-scripts-${TS}.log"
exec > "$LOG" 2>&1

echo "=== deploy-live-scripts $(date -u) ==="

if [ ! -d "$REPO_DIR/.git" ]; then
    echo "MISSING $REPO_DIR (not a git checkout), skip"
    exit 0
fi

cd "$REPO_DIR" || exit 1

DEPLOYED=0
UNCHANGED=0
FAILED=0

while IFS= read -r relfile; do
    src="$REPO_DIR/$relfile"
    dest="$LIVE_DIR/${relfile#scripts/}"

    if [ ! -f "$src" ]; then
        echo "SKIP (tracked but missing from checkout): $relfile"
        continue
    fi

    if [ -f "$dest" ] && cmp -s "$src" "$dest"; then
        UNCHANGED=$((UNCHANGED + 1))
        continue
    fi

    mkdir -p "$(dirname "$dest")" || { echo "FAILED mkdir for $dest"; FAILED=$((FAILED + 1)); continue; }

    if [ -f "$dest" ]; then
        bak="${dest}.bak-predeploy-${TS}"
        cp -p "$dest" "$bak" || { echo "FAILED backup of $dest"; FAILED=$((FAILED + 1)); continue; }
        echo "BACKED UP: $dest -> $bak"
    fi

    if cp -p "$src" "$dest"; then
        echo "DEPLOYED: $relfile -> $dest"
        DEPLOYED=$((DEPLOYED + 1))
    else
        echo "FAILED copy: $relfile -> $dest"
        FAILED=$((FAILED + 1))
    fi
done < <(git ls-files scripts/)

echo "=== done: deployed=$DEPLOYED unchanged=$UNCHANGED failed=$FAILED ==="
find /opt/veridian/logs -name 'deploy-live-scripts-*.log' -mtime +14 -delete

[ "$FAILED" -eq 0 ]
