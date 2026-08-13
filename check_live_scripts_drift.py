#!/usr/bin/env python3
"""Real live-vs-origin/main drift check for /opt/veridian/scripts.

task-20260813-103224-close-the-merged-to-live-deployment-gap (UMR-20260813-101142-5d24),
SCOPE item 3: "Add a real drift check that reports live-vs-origin/main divergence as a
boolean plus a file list, so any PM tier can verify INTEGRATED as a fact instead of an
assumption."

/opt/veridian/scripts is itself a real git working copy of FChecklist/veridian-scripts
(the actual live deploy target -- see sync-repos.sh and scripts/README-RETIRED.md in
claude-control for how deploy-live-scripts.sh's old copy-based mechanism was retired
2026-08-01 in favor of this checkout being pulled directly). This script answers one
question only, with real evidence, not an assumption: is the live checkout's HEAD the
same commit as origin/main, and if not, exactly which tracked files differ?

Does a real `git fetch origin` first -- never trusts a possibly-stale local
refs/remotes/origin/main. Exit code doubles as the boolean: 0 = in sync, 1 = drift
found, 2 = could not determine (fetch/git failure -- fails closed, never reports
in_sync=true on an error).
"""
import argparse
import json
import subprocess
import sys

DEFAULT_LIVE_DIR = "/opt/veridian/scripts"


def run(argv, cwd, timeout=60):
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def check_drift(live_dir=DEFAULT_LIVE_DIR):
    """Returns (result_dict, exit_code). result_dict always has at least
    'in_sync' and 'checked_at_live_dir' keys; on success also 'live_head',
    'origin_main_head', 'commits_behind', 'commits_ahead', 'changed_files'."""
    result = {"checked_at_live_dir": live_dir, "in_sync": False}

    fetch = run(["git", "fetch", "--quiet", "origin"], cwd=live_dir)
    if fetch.returncode != 0:
        result["error"] = f"git fetch origin failed: {fetch.stderr.strip()}"
        return result, 2

    head = run(["git", "rev-parse", "HEAD"], cwd=live_dir)
    origin_main = run(["git", "rev-parse", "origin/main"], cwd=live_dir)
    if head.returncode != 0 or origin_main.returncode != 0:
        result["error"] = "git rev-parse HEAD / origin/main failed"
        return result, 2

    # 2026-08-13 (task-20260813-103224): real incident this caught live --
    # this checkout can be on a non-main branch (e.g. a worker task checked
    # out its own PR branch directly here). sync-repos.sh's `git pull
    # --ff-only` only fast-forwards the CURRENT branch's own upstream in
    # that case, silently never touching origin/main -- surface the real
    # branch name so that failure mode is never silently invisible here too.
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=live_dir)
    result["current_branch"] = branch.stdout.strip() if branch.returncode == 0 else None
    if result["current_branch"] and result["current_branch"] != "main":
        result["on_main_branch"] = False
    else:
        result["on_main_branch"] = True

    live_head = head.stdout.strip()
    origin_head = origin_main.stdout.strip()
    result["live_head"] = live_head
    result["origin_main_head"] = origin_head

    if live_head == origin_head:
        result["in_sync"] = True
        result["commits_behind"] = 0
        result["commits_ahead"] = 0
        result["changed_files"] = []
        return result, 0

    behind = run(["git", "rev-list", "--count", f"{live_head}..{origin_head}"], cwd=live_dir)
    ahead = run(["git", "rev-list", "--count", f"{origin_head}..{live_head}"], cwd=live_dir)
    result["commits_behind"] = int(behind.stdout.strip()) if behind.returncode == 0 else None
    result["commits_ahead"] = int(ahead.stdout.strip()) if ahead.returncode == 0 else None

    name_status = run(["git", "diff", "--name-status", live_head, origin_head], cwd=live_dir)
    changed = []
    if name_status.returncode == 0:
        for line in name_status.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            changed.append({"status": parts[0], "path": parts[-1]})
    result["changed_files"] = changed
    result["in_sync"] = False
    return result, 1


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live-dir", default=DEFAULT_LIVE_DIR,
                     help=f"real live checkout to check (default: {DEFAULT_LIVE_DIR})")
    args = ap.parse_args()

    result, code = check_drift(args.live_dir)
    print(json.dumps(result, indent=2))
    sys.exit(code)


if __name__ == "__main__":
    main()
