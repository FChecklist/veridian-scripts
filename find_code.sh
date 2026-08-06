#!/usr/bin/env bash
# find_code.sh -- pruned recursive code search helper.
#
# WHY THIS EXISTS (UMR-20260806-100604-4591): an unfiltered recursive `grep -rl`
# invoked directly over /opt/veridian/scripts and /opt/veridian/ai-os (PID
# 2769369, `grep -rl PHASE-4-BUILD-WORKFLOW /opt/veridian/scripts
# /opt/veridian/ai-os`) ran 17+ minutes in real D-state disk saturation because
# it descended into node_modules/.git/__pycache__/etc trees and
# ai-os/tasks/*/workspace (~1416 task directories, 334+ node_modules trees,
# ~246GB total) instead of pruning them. The same class of problem was
# already independently discovered and documented in
# /opt/veridian/ai-os/MASTER_INDEX.yaml's own exclusion_rules block (an
# earlier "unscoped find incident"): `-not -path` still descends into and
# stats every excluded directory -- only real `-prune` skips the subtree.
# This script is the reusable, registered (capability_registry:
# pruned_code_search) wrapper of that fix, defaulting to the narrower,
# usually-correct scope (/opt/veridian/scripts) instead of the whole server.
#
# USAGE:
#   find_code.sh <pattern> [scope_dir]
#
#   pattern    -- an extended-regex pattern passed to grep -lE (lists
#                 filenames containing a match, same convention
#                 MASTER_INDEX.yaml's own correct_find_pattern example uses)
#   scope_dir  -- optional search root. Defaults to /opt/veridian/scripts.
#                 Pass an explicit broader scope (e.g. /opt/veridian/ai-os or
#                 /opt/veridian) only when you specifically need it -- the
#                 same prune list below is always applied regardless of scope.
#
# Always-pruned (genuine `find -prune`, never descended into or stat'd beyond
# the path-check itself):
#   - directories named node_modules, .git, .venv, __pycache__, dist, build,
#     anywhere in the tree
#   - ai-os/tasks/*/workspace (per-task worktree copies -- confirmed by
#     MASTER_INDEX.yaml's own root_cause_confirmed to be ~9.8 MILLION files
#     under /opt/veridian/ai-os/tasks alone; NOT the same as
#     /opt/veridian/workspace, which holds real, non-scratch content and is
#     deliberately NOT pruned wholesale)
#   - /opt/veridian/workspace/claude-cli-work and
#     /opt/veridian/workspace/main-e2e-check specifically (the two scratch
#     subdirs MASTER_INDEX.yaml's exclusion_rules documents, not all of
#     /opt/veridian/workspace)
#
# Exit codes: 0 if at least one match found, 1 if none found, 2 on usage error.

set -euo pipefail

# Basename prunes (match this directory name anywhere in the tree).
PRUNE_BASENAMES=(node_modules .git .venv __pycache__ dist build)

# Path-suffix prunes (real find -path patterns, not bare basenames -- these
# exist to prune specific known-huge scratch subtrees without also pruning
# unrelated real content that happens to share a basename, e.g.
# /opt/veridian/workspace itself). Reused verbatim from
# /opt/veridian/ai-os/MASTER_INDEX.yaml's own exclusion_rules.prune_paths,
# not reinvented.
PRUNE_PATH_PATTERNS=(
  "*/ai-os/tasks/*/workspace"
  "*/workspace/claude-cli-work"
  "*/workspace/main-e2e-check"
)

usage() {
  echo "usage: $(basename "$0") <pattern> [scope_dir]" >&2
  echo "  pattern    extended-regex pattern for grep -lE" >&2
  echo "  scope_dir  search root (default: /opt/veridian/scripts)" >&2
  exit 2
}

if [[ $# -lt 1 || $# -gt 2 || -z "${1:-}" ]]; then
  usage
fi

pattern="$1"
scope_dir="${2:-/opt/veridian/scripts}"

if [[ ! -d "$scope_dir" ]]; then
  echo "find_code.sh: scope_dir does not exist or is not a directory: $scope_dir" >&2
  exit 2
fi

# Reject a malformed regex up front, against real input (/dev/null), so a
# bad pattern is a real, visible usage error (exit 2) instead of being
# silently indistinguishable from a genuine no-match (exit 1) once it's
# buried inside the find | xargs pipeline below (real finding from this
# script's own independent review, UMR-20260806-100604-4591). grep's own
# exit status distinguishes "ran fine, no match" (1) from "real error, e.g.
# malformed regex" (>1) -- only the latter is a usage error here.
set +e
regex_check_err="$(grep -E -- "$pattern" /dev/null 2>&1 >/dev/null)"
regex_check_rc=$?
set -e
if [[ $regex_check_rc -gt 1 ]]; then
  echo "find_code.sh: invalid pattern: $regex_check_err" >&2
  exit 2
fi

prune_expr=()
first=1
for name in "${PRUNE_BASENAMES[@]}"; do
  [[ $first -eq 0 ]] && prune_expr+=(-o)
  prune_expr+=(-name "$name")
  first=0
done
for pat in "${PRUNE_PATH_PATTERNS[@]}"; do
  [[ $first -eq 0 ]] && prune_expr+=(-o)
  prune_expr+=(-path "$pat")
  first=0
done

# Real -prune (not -not-path, which still descends into and stats every
# excluded directory -- see MASTER_INDEX.yaml's own root_cause_confirmed).
# NUL-delimited throughout so filenames with spaces/newlines are handled
# safely. `--` before $pattern (real finding from this script's own
# independent review) so a pattern starting with `-` (e.g. `-v`, `-f`) is
# never misparsed as a grep flag.
set +e
matches="$(find "$scope_dir" \( "${prune_expr[@]}" \) -prune -o -type f -print0 2>/dev/null \
  | xargs -0 -r grep -IlE -- "$pattern" 2>/dev/null)"
set -e

if [[ -z "$matches" ]]; then
  exit 1
fi

printf '%s\n' "$matches"
exit 0
