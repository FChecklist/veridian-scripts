#!/usr/bin/env python3
"""
Automates SYNC rule #14 from SUPERBOSS_DISPATCH_PROMPT.md: whenever a server
task reaches a terminal state, record a summary entry in the master
CONTROLLER.yaml (claude-control repo) and push it. Idempotent via task.yaml's
`master_synced_at` marker — never double-records a task.

Deliberately does NOT parse+rewrite the whole master CONTROLLER.yaml (its own
header explicitly forbids wholesale rewrites and has hand-written comments
that wouldn't round-trip through a YAML dump) — appends raw text, same
convention as manual entries.
"""
import datetime
import glob
import subprocess
import sys
import yaml

import dispatch_core  # 2026-07-29 cron-consolidation-phase6: shared concurrency gate

AI_OS_TASKS = "/opt/veridian/ai-os/tasks"
CONTROL_REPO = "/opt/veridian/repos/claude-control"
CONTROLLER_PATH = f"{CONTROL_REPO}/CONTROLLER.yaml"
TERMINAL = {"completed", "blocked", "failed", "awaiting_human_approval"}

STATUS_MAP = {
    "completed": "done",
    "blocked": "blocked",
    "failed": "failed",
    "awaiting_human_approval": "in-progress",
}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def today():
    return datetime.date.today().isoformat()


def run(cmd, cwd=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    print(f"$ {' '.join(cmd)}\n{r.stdout}{r.stderr}")
    if check and r.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(cmd)}")
    return r


def format_entry(entry):
    text = yaml.safe_dump([entry], default_flow_style=False, sort_keys=False, allow_unicode=True)
    lines = text.rstrip("\n").split("\n")
    return "\n" + "\n".join("  " + line for line in lines) + "\n"


def main():
    task_files = sorted(glob.glob(f"{AI_OS_TASKS}/*/task.yaml"))
    pending = []
    for path in task_files:
        with open(path) as f:
            t = yaml.safe_load(f)
        if t.get("status") in TERMINAL and not t.get("master_synced_at"):
            pending.append((path, t))

    if not pending:
        print("Nothing to sync.")
        return

    # 2026-07-29 cron-consolidation-phase6: shared concurrency gate, mirroring
    # dispatch_core.py's acquire_dispatch_lock()/has_free_slot() pattern already
    # used by dispatch-tick.py/phase-continuation-tick.py/status-remediation-tick.py.
    # This job does real git network I/O (fetch/pull/push) against a shared repo
    # checkout -- exactly the kind of job that must not pile on top of an
    # already-loaded box. Brief lock hold for the cap check only; released
    # before the real git work below runs.
    with dispatch_core.acquire_dispatch_lock():
        if not dispatch_core.has_free_slot():
            print("SKIP sync-controller-back (cap reached): system at concurrency cap, deferring to next scheduled run")
            return

    run(["git", "fetch", "origin"], cwd=CONTROL_REPO)
    run(["git", "checkout", "master"], cwd=CONTROL_REPO)
    run(["git", "pull", "--rebase", "origin", "master"], cwd=CONTROL_REPO)

    appended = ""
    for path, t in pending:
        pr_url = ""
        try:
            with open(path.replace("task.yaml", "pr_url.txt")) as f:
                pr_url = f.read().strip()
        except FileNotFoundError:
            pass

        last_note = ""
        if t.get("checkpoints"):
            last_note = t["checkpoints"][-1].get("note", "")

        summary = f"Server-worker task '{t['title']}' on {t['repo']} reached status={t['status']}."
        if last_note:
            summary += f" {last_note}"

        where = f"/opt/veridian/ai-os/tasks/{t['id']}/ on VERIDIAN-DEV server (task.yaml, worker.log, review.json)"
        if pr_url:
            where += f"; PR: {pr_url}"

        entry = {
            "id": f"WORKER-{t['id'].replace('task-', '')}",
            "project": "veridian",
            "type": "worker-task",
            "status": STATUS_MAP.get(t["status"], t["status"]),
            "summary": summary,
            "where": where,
            "last_touched": today(),
        }
        appended += format_entry(entry)

        t["master_synced_at"] = now()
        with open(path, "w") as f:
            yaml.safe_dump(t, f, sort_keys=False, default_flow_style=False)

    with open(CONTROLLER_PATH, "a") as f:
        f.write(appended)

    run(["git", "add", "CONTROLLER.yaml"], cwd=CONTROL_REPO)
    commit = run(
        ["git", "commit", "-m", f"Auto-sync {len(pending)} server task(s) to master controller"],
        cwd=CONTROL_REPO, check=False,
    )
    if commit.returncode != 0:
        print("Nothing to commit.")
        return
    run(["git", "pull", "--rebase", "origin", "master"], cwd=CONTROL_REPO)
    run(["git", "push", "origin", "master"], cwd=CONTROL_REPO)
    print(f"Synced {len(pending)} task(s) to master CONTROLLER.yaml.")


if __name__ == "__main__":
    main()
