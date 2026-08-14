#!/usr/bin/env bash
# scripts/find-real-pr.sh -- UMR-20260814-092508-8a6b real fix.
#
# Real incident, 2026-08-14: a governance/integration UMR's real deliverable
# PR landed in the veridian-scripts repo, but was dispatched against and
# tracked under claude-control. Because nothing ever automatically checked
# any repo OTHER than the one a UMR was dispatched against, multiple rounds
# of a PM tier wrongly concluded the real claude-control PR was
# fake/orphaned -- wasted significant real investigation time before a human
# caught it by manually checking veridian-scripts by hand.
#
# This is the standalone CLI entrypoint for that fix: a real, deterministic
# cross-repo PR lookup that searches ALL known repos (compliance-tracker,
# claude-control, veridian-scripts, projexa, veda-advisors,
# global-revenue-engine, veridian-brain, sumeet-spec by default -- see
# resource_governor.py's ALL_KNOWN_REPOS), not just one assumed repo, and
# returns EVERY real match found, not just the first. Thin wrapper around
# resource_governor.py's --find-real-pr flag (same "shell script wraps
# `python3 resource_governor.py <flag>`" convention already used by
# resource_governor_tick_loop.sh / dispatch-owner-task.sh -- one real
# implementation in find_real_pr_across_repos(), never a divergent bash
# reimplementation of the same `gh pr list --search` logic).
#
# Usage:
#   scripts/find-real-pr.sh "<query text>"                    # search ALL_KNOWN_REPOS
#   scripts/find-real-pr.sh "<query text>" repo1,repo2,repo3   # search only these repos
#
# Prints JSON: {"query_text": ..., "repos_searched": [...], "count": N,
# "matches": [{"repo": ..., "number": ..., "title": ..., "state": ...,
# "mergedAt": ...}, ...]} -- one entry per real match, across every repo
# searched, on stdout. Exits non-zero only on a real usage error (missing
# query text); a real `gh` failure for one repo fails open per-repo inside
# find_real_pr_across_repos() itself (see its own docstring) and never
# aborts the whole search.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

QUERY_TEXT="${1:-}"
REPOS_ARG="${2:-}"

if [ -z "$QUERY_TEXT" ]; then
  echo "usage: $0 \"<query text>\" [repo1,repo2,...]" >&2
  exit 1
fi

ARGS=(--find-real-pr "$QUERY_TEXT")
if [ -n "$REPOS_ARG" ]; then
  ARGS+=(--find-real-pr-repos "$REPOS_ARG")
fi

exec python3 "$REPO_ROOT/resource_governor.py" "${ARGS[@]}"
