#!/usr/bin/env python3
"""
Queue Manager for VERIDIAN tasks
Provides: list, delete, priority, pause, resume, merge, modify, decrypt
"""

import os
import sys
import yaml
import json
import shutil
import argparse
import subprocess
import time
from datetime import datetime

TASKS_DIR = "/opt/veridian/ai-os/tasks"
TRASH_DIR = "/opt/veridian/ai-os/tasks/.trash"

# Bug fix (reproduced live 2026-08-15): `list --status queued` used to read
# ONLY per-task task.yaml files under TASKS_DIR. A task.yaml is written for
# the first time by the dispatcher when a worker actually starts (see
# resource_governor.py's dispatch_one()) -- it structurally does not exist
# yet for a row still sitting in the real pre-dispatch backlog (umr_tasks,
# status='queued', accessed only via resource_governor.py/
# superboss-register.sqlite). So `list --status queued` always returned an
# empty result while real queued work existed (33+ real tier 0-1 rows at the
# time this was found), giving anyone who trusted it a false "nothing
# queued" impression.
#
# Fixed by delegating to resource_governor.py's own real, already-existing
# queue-management functions (list_queue/stop_task/resume_task/set_priority,
# wired to --list-queue/--stop-task/--resume-task/--set-priority) instead of
# re-reading raw sqlite -- this is the preferred fix over a warning label
# (per the governing SPEC): it makes the real pre-dispatch backlog visible
# AND actionable through this same CLI, with a single source of truth
# (resource_governor.py owns all real umr_tasks reads/writes) rather than a
# second, divergent implementation here.
RESOURCE_GOVERNOR_PY = os.environ.get(
    "RESOURCE_GOVERNOR_PY",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "resource_governor.py"),
)


def fetch_pre_dispatch_queue(status="queued", limit=100):
    """Real, read-only call into resource_governor.py --list-queue -- the
    single source of truth for real pre-dispatch umr_tasks rows. Fails open
    (returns ok=False with a real error string) rather than raising, same
    convention resource_governor.py's own CLI branches use, so a broken/
    unavailable Superboss Register never crashes this tool's task.yaml-based
    view alongside it."""
    if not os.path.exists(RESOURCE_GOVERNOR_PY):
        return {"ok": False, "error": f"resource_governor.py not found at {RESOURCE_GOVERNOR_PY}"}
    try:
        proc = subprocess.run(
            [sys.executable, RESOURCE_GOVERNOR_PY, "--list-queue",
             "--status", status, "--limit", str(limit)],
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"ok": False, "error": f"resource_governor.py --list-queue failed to run: {exc}"}
    if proc.returncode != 0:
        return {"ok": False, "error": (proc.stderr or proc.stdout or "non-zero exit").strip()}
    try:
        result = json.loads(proc.stdout)
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": f"non-JSON output from resource_governor.py --list-queue: {exc}"}
    if not result.get("ok", True):
        return {"ok": False, "error": result.get("error", "unknown resource_governor.py error")}
    return {"ok": True, "queue": result.get("queue", [])}

def ensure_trash():
    os.makedirs(TRASH_DIR, exist_ok=True)

def get_task_path(task_id):
    path = os.path.join(TASKS_DIR, task_id)
    if os.path.isdir(path):
        return path
    return None

def load_task_yaml(task_path):
    yaml_file = os.path.join(task_path, "task.yaml")
    if not os.path.exists(yaml_file):
        return None
    with open(yaml_file, 'r') as f:
        return yaml.safe_load(f) or {}

def save_task_yaml(task_path, data):
    yaml_file = os.path.join(task_path, "task.yaml")
    with open(yaml_file, 'w') as f:
        yaml.dump(data, f, default_flow_style=False)

def list_post_dispatch_tasks(status=None):
    """Real, read-only task.yaml scan -- POST-DISPATCH ONLY. A task.yaml is
    only ever written once resource_governor.py's dispatch_one() has already
    started a real worker for a row, so this view structurally cannot see
    anything still sitting in the pre-dispatch backlog (see
    fetch_pre_dispatch_queue() above for that)."""
    tasks = []
    for d in os.listdir(TASKS_DIR):
        if d.startswith('.') or d == '.trash':
            continue
        path = os.path.join(TASKS_DIR, d)
        if not os.path.isdir(path):
            continue
        data = load_task_yaml(path)
        if data is None:
            continue
        if status and data.get('status') != status:
            continue
        ctime = os.path.getctime(path)
        tasks.append({
            'id': d,
            'status': data.get('status', 'unknown'),
            'priority': data.get('priority', 0),
            'paused': data.get('paused', False),
            'created': datetime.fromtimestamp(ctime).isoformat(),
            'repo': data.get('repo', ''),
            'branch': data.get('branch', '')
        })
    tasks.sort(key=lambda t: (t['priority'], t['created']))
    return tasks

def list_tasks(status=None, fmt='table', source='all', limit=100):
    """Real listing across BOTH real queue spaces, clearly labeled by source
    so an empty result from one is never mistaken for an empty queue overall
    (the real bug this fixes -- see fetch_pre_dispatch_queue()'s docstring):
      - 'pre_dispatch'  -- real umr_tasks rows not yet dispatched, from
                           resource_governor.py --list-queue (the single
                           source of truth for that table).
      - 'post_dispatch' -- real per-task task.yaml files under TASKS_DIR,
                           for tasks a worker has already started on.
    """
    pre = {"ok": True, "queue": []}
    if source in ('all', 'pre_dispatch'):
        pre = fetch_pre_dispatch_queue(status=status or 'queued', limit=limit)
    post = []
    if source in ('all', 'post_dispatch'):
        post = list_post_dispatch_tasks(status)

    if fmt == 'json':
        print(json.dumps({
            'pre_dispatch': {
                'source': 'umr_tasks via resource_governor.py --list-queue',
                'ok': pre.get('ok'),
                'error': pre.get('error'),
                'tasks': pre.get('queue', []),
            },
            'post_dispatch': {
                'source': f'task.yaml files under {TASKS_DIR} (post-dispatch only)',
                'tasks': post,
            },
        }, indent=2))
        return

    if source in ('all', 'pre_dispatch'):
        print(f"PRE-DISPATCH BACKLOG (real umr_tasks rows, status={status or 'queued'!r}, "
              "via resource_governor.py --list-queue -- not yet dispatched to a worker)")
        if not pre.get('ok'):
            print(f"  ERROR: {pre.get('error')}")
        elif not pre['queue']:
            print("  (none)")
        else:
            print(f"  {'UMR_ID':<38} {'TASK_IDENTITY':<20} {'TIER':<5} {'STATUS':<10} {'PAUSED':<6} {'SUBMITTED'}")
            print('  ' + '-'*98)
            for row in pre['queue']:
                paused = 'Y' if row.get('paused') else 'N'
                print(f"  {row.get('umr_id', '')[:36]:<38} {str(row.get('task_identity', ''))[:18]:<20} "
                      f"{row.get('tier', ''):<5} {row.get('status', ''):<10} {paused:<6} {row.get('ts_submitted', '')}")
        print()

    if source in ('all', 'post_dispatch'):
        print(f"POST-DISPATCH TASKS (real task.yaml files under {TASKS_DIR} -- "
              "already dispatched to a worker; NOT the pre-dispatch backlog)")
        if not post:
            print("  (none)")
        else:
            print(f"  {'ID':<48} {'STATUS':<15} {'PRIORITY':<8} {'PAUSED':<6} {'CREATED'}")
            print('  ' + '-'*98)
            for t in post:
                paused = 'Y' if t['paused'] else 'N'
                print(f"  {t['id'][:46]:<48} {t['status']:<15} {t['priority']:<8} {paused:<6} {t['created']}")

def cmd_list(args):
    list_tasks(args.status, args.format, args.source, args.limit)

def cmd_delete(args):
    task_id = args.task_id
    path = get_task_path(task_id)
    if not path:
        print(f"Task {task_id} not found.")
        return
    if args.force:
        shutil.rmtree(path)
        print(f"Task {task_id} permanently deleted.")
    else:
        ensure_trash()
        shutil.move(path, os.path.join(TRASH_DIR, task_id))
        print(f"Task {task_id} moved to trash (use --force to delete permanently).")

def cmd_priority(args):
    task_id = args.task_id
    path = get_task_path(task_id)
    if not path:
        print(f"Task {task_id} not found.")
        return
    data = load_task_yaml(path)
    if data is None:
        print(f"No task.yaml in {task_id}")
        return
    data['priority'] = args.priority
    save_task_yaml(path, data)
    print(f"Priority of {task_id} set to {args.priority}")

def cmd_pause(args):
    task_id = args.task_id
    path = get_task_path(task_id)
    if not path:
        print(f"Task {task_id} not found.")
        return
    data = load_task_yaml(path)
    if data is None:
        print(f"No task.yaml in {task_id}")
        return
    data['paused'] = True
    save_task_yaml(path, data)
    print(f"Task {task_id} paused.")

def cmd_resume(args):
    task_id = args.task_id
    path = get_task_path(task_id)
    if not path:
        print(f"Task {task_id} not found.")
        return
    data = load_task_yaml(path)
    if data is None:
        print(f"No task.yaml in {task_id}")
        return
    data['paused'] = False
    save_task_yaml(path, data)
    print(f"Task {task_id} resumed.")

def cmd_merge(args):
    src_id = args.source
    tgt_id = args.target
    src_path = get_task_path(src_id)
    tgt_path = get_task_path(tgt_id)
    if not src_path or not tgt_path:
        print("One or both tasks not found.")
        return
    src_data = load_task_yaml(src_path)
    tgt_data = load_task_yaml(tgt_path)
    if src_data is None or tgt_data is None:
        print("Missing task.yaml in one or both.")
        return
    for key in ['repo', 'branch', 'workspace', 'title']:
        if key in src_data and key not in tgt_data:
            tgt_data[key] = src_data[key]
    if src_data.get('priority', 0) < tgt_data.get('priority', 0):
        tgt_data['priority'] = src_data['priority']
    save_task_yaml(tgt_path, tgt_data)
    ensure_trash()
    shutil.move(src_path, os.path.join(TRASH_DIR, src_id))
    print(f"Merged {src_id} into {tgt_id}. Source moved to trash.")

def cmd_modify(args):
    task_id = args.task_id
    path = get_task_path(task_id)
    if not path:
        print(f"Task {task_id} not found.")
        return
    data = load_task_yaml(path)
    if data is None:
        print(f"No task.yaml in {task_id}")
        return
    for kv in args.set:
        if '=' not in kv:
            print(f"Invalid key=value: {kv}")
            continue
        key, val = kv.split('=', 1)
        if val.lower() == 'true':
            val = True
        elif val.lower() == 'false':
            val = False
        elif val.isdigit():
            val = int(val)
        data[key] = val
    save_task_yaml(path, data)
    print(f"Updated {task_id}")

def cmd_decrypt(args):
    print("Decrypt not implemented. Placeholder.")

def _run_resource_governor(flag_args):
    """Shared delegation helper for the *-pending commands below -- makes
    real pre-dispatch umr_tasks rows actionable (not just visible) through
    this same CLI, per the governing SPEC's preferred fix, by calling
    straight into resource_governor.py's own real stop_task/resume_task/
    set_priority rather than reimplementing a second writer against
    umr_tasks."""
    if not os.path.exists(RESOURCE_GOVERNOR_PY):
        print(json.dumps({"ok": False, "error": f"resource_governor.py not found at {RESOURCE_GOVERNOR_PY}"}))
        return False
    try:
        proc = subprocess.run(
            [sys.executable, RESOURCE_GOVERNOR_PY] + flag_args,
            capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(json.dumps({"ok": False, "error": f"resource_governor.py call failed to run: {exc}"}))
        return False
    print(proc.stdout.strip())
    if proc.stderr.strip():
        print(proc.stderr.strip(), file=sys.stderr)
    return proc.returncode == 0

def cmd_stop_pending(args):
    _run_resource_governor(["--stop-task", "--umr-id", args.umr_id])

def cmd_resume_pending(args):
    _run_resource_governor(["--resume-task", "--umr-id", args.umr_id])

def cmd_priority_pending(args):
    _run_resource_governor(["--set-priority", "--umr-id", args.umr_id, "--tier", str(args.tier)])

def main():
    parser = argparse.ArgumentParser(description='VERIDIAN Queue Manager')
    subparsers = parser.add_subparsers(dest='command', required=True)

    p_list = subparsers.add_parser('list', help='List tasks (real pre-dispatch umr_tasks backlog '
                                                 'AND post-dispatch task.yaml files, clearly labeled)')
    p_list.add_argument('--status', help="Filter by status (interpreted independently against each "
                                          "source's own real status vocabulary; default 'queued' for "
                                          "the pre-dispatch source)")
    p_list.add_argument('--format', choices=['table', 'json'], default='table')
    p_list.add_argument('--source', choices=['all', 'pre_dispatch', 'post_dispatch'], default='all',
                         help="'pre_dispatch' = real umr_tasks backlog via resource_governor.py "
                              "(not yet dispatched); 'post_dispatch' = task.yaml files (already "
                              "dispatched); default 'all' shows both, clearly labeled")
    p_list.add_argument('--limit', type=int, default=100, help='Max pre-dispatch rows to fetch')
    p_list.set_defaults(func=cmd_list)

    p_del = subparsers.add_parser('delete', help='Delete task (move to trash)')
    p_del.add_argument('task_id')
    p_del.add_argument('--force', action='store_true', help='Permanently delete')
    p_del.set_defaults(func=cmd_delete)

    p_pri = subparsers.add_parser('priority', help='Set task priority (lower=higher)')
    p_pri.add_argument('task_id')
    p_pri.add_argument('priority', type=int)
    p_pri.set_defaults(func=cmd_priority)

    p_pause = subparsers.add_parser('pause', help='Pause a task')
    p_pause.add_argument('task_id')
    p_pause.set_defaults(func=cmd_pause)

    p_resume = subparsers.add_parser('resume', help='Resume a paused task')
    p_resume.add_argument('task_id')
    p_resume.set_defaults(func=cmd_resume)

    p_merge = subparsers.add_parser('merge', help='Merge source into target (deletes source)')
    p_merge.add_argument('source')
    p_merge.add_argument('target')
    p_merge.set_defaults(func=cmd_merge)

    p_mod = subparsers.add_parser('modify', help='Modify task fields')
    p_mod.add_argument('task_id')
    p_mod.add_argument('--set', action='append', required=True, help='key=value pairs')
    p_mod.set_defaults(func=cmd_modify)

    p_dec = subparsers.add_parser('decrypt', help='Decrypt task content (placeholder)')
    p_dec.add_argument('task_id')
    p_dec.set_defaults(func=cmd_decrypt)

    # Pre-dispatch (umr_tasks) actions -- separate command names + a real
    # --umr-id (not task_id) from the task.yaml-based pause/resume/priority
    # above, since a pre-dispatch row lives in a different real ID
    # namespace and has no task.yaml yet. Each delegates straight to
    # resource_governor.py's own real functions (single source of truth for
    # umr_tasks writes), rather than reimplementing them here.
    p_stop_pending = subparsers.add_parser(
        'stop-pending', help='Pause a real pre-dispatch umr_tasks row (via resource_governor.py --stop-task)')
    p_stop_pending.add_argument('umr_id')
    p_stop_pending.set_defaults(func=cmd_stop_pending)

    p_resume_pending = subparsers.add_parser(
        'resume-pending', help='Un-pause a real pre-dispatch umr_tasks row (via resource_governor.py --resume-task)')
    p_resume_pending.add_argument('umr_id')
    p_resume_pending.set_defaults(func=cmd_resume_pending)

    p_priority_pending = subparsers.add_parser(
        'priority-pending', help='Set tier of a real pre-dispatch umr_tasks row (via resource_governor.py --set-priority)')
    p_priority_pending.add_argument('umr_id')
    p_priority_pending.add_argument('tier', type=int, help='0 (highest) .. 4 (lowest)')
    p_priority_pending.set_defaults(func=cmd_priority_pending)

    args = parser.parse_args()
    args.func(args)

if __name__ == '__main__':
    main()