#!/usr/bin/env python3
"""
Queue Manager for VERIDIAN tasks
Provides: list, delete, priority, pause, resume, merge, modify, decrypt
"""

import os
import sys
import yaml
import shutil
import argparse
import time
from datetime import datetime

TASKS_DIR = "/opt/veridian/ai-os/tasks"
TRASH_DIR = "/opt/veridian/ai-os/tasks/.trash"

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

def list_tasks(status=None, fmt='table'):
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
    if fmt == 'json':
        import json
        print(json.dumps(tasks, indent=2))
    else:
        print(f"{'ID':<50} {'STATUS':<15} {'PRIORITY':<8} {'PAUSED':<6} {'CREATED'}")
        print('-'*100)
        for t in tasks:
            paused = 'Y' if t['paused'] else 'N'
            print(f"{t['id'][:48]:<50} {t['status']:<15} {t['priority']:<8} {paused:<6} {t['created']}")

def cmd_list(args):
    list_tasks(args.status, args.format)

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

def main():
    parser = argparse.ArgumentParser(description='VERIDIAN Queue Manager')
    subparsers = parser.add_subparsers(dest='command', required=True)

    p_list = subparsers.add_parser('list', help='List tasks')
    p_list.add_argument('--status', help='Filter by status')
    p_list.add_argument('--format', choices=['table', 'json'], default='table')
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

    args = parser.parse_args()
    args.func(args)

if __name__ == '__main__':
    main()