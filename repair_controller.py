#!/usr/bin/env python3
"""
Rebuilds ai-os/CONTROLLER.yaml from scratch by scanning the authoritative
per-task task.yaml files (each written only by its own worker, never
contested) -- safe because the corruption was in the shared aggregate index,
not in the individual task records themselves.
"""
import datetime
import glob
import yaml

AI_OS = "/opt/veridian/ai-os"
CONTROLLER_PATH = f"{AI_OS}/CONTROLLER.yaml"

tasks = []
for path in sorted(glob.glob(f"{AI_OS}/tasks/*/task.yaml")):
    with open(path) as f:
        t = yaml.safe_load(f)
    entry = {
        "id": t["id"],
        "title": t["title"],
        "status": t["status"],
        "repo": t["repo"],
        "branch": t["branch"],
        "created_at": t["created_at"],
        "last_checkpoint_at": t.get("last_checkpoint_at"),
        "service": t["service"],
        "task_dir": t["task_dir"],
        "execution_seconds": t.get("execution_seconds", 0),
        "restart_count": t.get("restart_count", 0),
    }
    tasks.append(entry)

ctrl = {
    "server": "VERIDIAN-DEV",
    "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "task_count": len(tasks),
    "tasks": tasks,
    "operating_instructions": {
        "source": "/opt/veridian/repos/claude-control/SUPERBOSS_DISPATCH_PROMPT.md",
        "master_controller": "/opt/veridian/repos/claude-control/CONTROLLER.yaml (entry SUPERBOSS-PROMPT-01)",
        "summary": "Superboss = one role, two seats (Claude Desktop intake/dispatch, Claude CLI-on-server execution/audit/merge, equal authority). Tiered trust: tier1 (deterministic, risk-tier.py) merges autonomously after AI review via supervisor-entrypoint.sh; tier2 (migrations/auth/permission/payment/billing/RLS/.env/heavy-deletion) holds for human sign-off even if AI-approved. Workers never merge themselves. Read the source file for the full prompt text and mechanics before dispatching or reviewing any task.",
        "last_synced": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    },
}

with open(CONTROLLER_PATH, "w") as f:
    yaml.safe_dump(ctrl, f, sort_keys=False, default_flow_style=False)

print(f"Rebuilt CONTROLLER.yaml from {len(tasks)} task.yaml files:")
for t in tasks:
    print(f"  {t['id']}: {t['status']}")
