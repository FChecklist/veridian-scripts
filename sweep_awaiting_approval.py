#!/usr/bin/env python3
"""sweep_awaiting_approval.py -- one-time backlog sweep, 2026-07-31.

Per Owner directive (quoted in AGENTS.md Rule 12, compliance-tracker), tasks
already sitting in awaiting_human_approval BEFORE the supervisor-entrypoint.sh
patch (claude-control PR #118) don't get automatically reprocessed just
because the script changed -- that script only runs at supervisor-invocation
time. This sweep applies the SAME new policy retroactively: for each task
currently in awaiting_human_approval, if its own already-recorded Superboss
review verdict was "approve" and its PR is still open/mergeable, merge it now
via the identical tier1 merge pattern (poll mergeStateStatus, gh pr merge,
verify via a fresh gh pr view, never trust a shell exit code alone -- same
real-incident-motivated pattern as supervisor-entrypoint.sh itself).

Explicitly does NOT re-run any review -- reuses the real, already-recorded
review.json verdict. A rejected verdict, a missing review.json, or a PR
that's no longer open/mergeable is skipped and logged, not forced.
"""
import json
import os
import subprocess
import sys
import time

AI_OS = "/opt/veridian/ai-os"
CONTROLLER = f"{AI_OS}/CONTROLLER.yaml"
TASK_MANAGER = "/opt/veridian/scripts/veridian-task.py"


def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def load_controller_tasks():
    import yaml
    with open(CONTROLLER) as f:
        d = yaml.safe_load(f)
    tasks = d.get("tasks", d) if isinstance(d, dict) else d
    if isinstance(tasks, dict):
        tasks = list(tasks.values())
    return tasks


def process_task(t):
    task_id = t.get("id")
    task_dir = f"{AI_OS}/tasks/{task_id}"
    review_path = f"{task_dir}/review.json"
    pr_url_path = f"{task_dir}/pr_url.txt"
    result = {"task_id": task_id, "action": None, "detail": None}

    if not os.path.isfile(review_path):
        result["action"] = "skip"
        result["detail"] = "no review.json on disk"
        return result
    try:
        with open(review_path) as f:
            review = json.load(f)
    except Exception as e:
        result["action"] = "skip"
        result["detail"] = f"unreadable review.json: {e}"
        return result

    verdict = review.get("verdict")
    tier = review.get("tier", "unknown")
    if verdict != "approve":
        result["action"] = "skip"
        result["detail"] = f"verdict={verdict}, not approve -- leaving as-is"
        return result

    if not os.path.isfile(pr_url_path):
        result["action"] = "skip"
        result["detail"] = "no pr_url.txt on disk"
        return result
    with open(pr_url_path) as f:
        pr_url = f.read().strip()
    if not pr_url:
        result["action"] = "skip"
        result["detail"] = "pr_url.txt empty"
        return result

    state = sh(["gh", "pr", "view", pr_url, "--json", "state,mergedAt,mergeStateStatus"])
    if state.returncode != 0:
        result["action"] = "skip"
        result["detail"] = f"gh pr view failed: {state.stderr.strip()[:200]}"
        return result
    try:
        info = json.loads(state.stdout)
    except Exception:
        result["action"] = "skip"
        result["detail"] = "could not parse gh pr view output"
        return result

    if info.get("state") == "MERGED":
        # Already merged (by hand, presumably) -- just fix the stale status.
        sh([sys.executable, TASK_MANAGER, "checkpoint", task_id,
            "--status", "completed",
            "--note", f"Backlog sweep 2026-07-31: PR already MERGED externally, correcting stale status: {pr_url}"])
        result["action"] = "status_corrected_already_merged"
        result["detail"] = pr_url
        return result

    if info.get("state") != "OPEN":
        result["action"] = "skip"
        result["detail"] = f"PR state={info.get('state')}, not OPEN -- leaving as-is"
        return result

    # Poll briefly for a clean merge state, same as supervisor-entrypoint.sh.
    for _ in range(10):
        mss = info.get("mergeStateStatus")
        if mss not in ("BLOCKED", "BEHIND", "UNKNOWN"):
            break
        time.sleep(10)
        state = sh(["gh", "pr", "view", pr_url, "--json", "mergeStateStatus"])
        try:
            info["mergeStateStatus"] = json.loads(state.stdout).get("mergeStateStatus")
        except Exception:
            break

    merge = sh(["gh", "pr", "merge", pr_url, "--merge"])
    verify = sh(["gh", "pr", "view", pr_url, "--json", "state,mergedAt"])
    try:
        vinfo = json.loads(verify.stdout)
    except Exception:
        vinfo = {}

    if vinfo.get("state") == "MERGED" and vinfo.get("mergedAt"):
        sh([sys.executable, TASK_MANAGER, "checkpoint", task_id,
            "--status", "completed",
            "--note", f"Backlog sweep 2026-07-31 (tier={tier}, was held pre-autonomy-directive): merged autonomously per Owner's full-approval directive: {pr_url}"])
        result["action"] = "merged"
        result["detail"] = pr_url
    else:
        sh([sys.executable, TASK_MANAGER, "checkpoint", task_id,
            "--status", "blocked",
            "--note", f"Backlog sweep 2026-07-31: merge attempt FAILED (gh pr merge stderr: {merge.stderr.strip()[:300]}) -- needs manual attention: {pr_url}"])
        result["action"] = "merge_failed"
        result["detail"] = merge.stderr.strip()[:300]

    return result


def main():
    tasks = load_controller_tasks()
    targets = [t for t in tasks if isinstance(t, dict) and t.get("status") == "awaiting_human_approval"]
    print(f"Found {len(targets)} tasks in awaiting_human_approval")
    results = []
    for t in targets:
        r = process_task(t)
        results.append(r)
        print(f"  {r['task_id']}: {r['action']} -- {r['detail']}")
    summary = {}
    for r in results:
        summary[r["action"]] = summary.get(r["action"], 0) + 1
    print("\n=== SUMMARY ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")
    with open(f"{AI_OS}/BACKLOG_SWEEP_RESULT_2026-07-31.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nFull results: {AI_OS}/BACKLOG_SWEEP_RESULT_2026-07-31.json")


if __name__ == "__main__":
    main()
