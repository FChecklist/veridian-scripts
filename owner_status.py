#!/usr/bin/env python3
import os
import sqlite3
import subprocess
import argparse

SCRIPTS = "/opt/veridian/scripts"
SUPERBOSS_REGISTER = os.path.join(SCRIPTS, "superboss-register.py")

_sbr = None


def _superboss_register():
    global _sbr
    if _sbr is None:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "superboss_register_owner_status", SUPERBOSS_REGISTER)
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _sbr = _mod
    return _sbr


def _resolve_db_path():
    return _superboss_register().resolve_superboss_db_path()


def real_unit_state(unit_name):
    if not unit_name:
        return None
    try:
        out = subprocess.run(
            ['systemctl', '--user', 'is-active', unit_name],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip()
    except Exception:
        return 'unknown'


def main():
    ap = argparse.ArgumentParser(description='Reconciled Owner task status table')
    ap.add_argument('--hours', type=int, default=48)
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--verify-live', action='store_true')
    args = ap.parse_args()

    con = sqlite3.connect(_resolve_db_path())
    cur = con.cursor()
    if args.all:
        cur.execute(
            "SELECT umr_id, task_identity, status, unit_name, ts_submitted "
            "FROM umr_tasks ORDER BY ts_submitted"
        )
    else:
        cur.execute(
            "SELECT umr_id, task_identity, status, unit_name, ts_submitted "
            "FROM umr_tasks WHERE ts_submitted >= datetime('now', ?) ORDER BY ts_submitted",
            (f'-{args.hours} hours',),
        )
    rows = cur.fetchall()

    print(f"{'TASK_ID':<65} {'STATUS':<12} {'CHECK'}")
    print('-' * 100)
    mismatches = 0
    for umr_id, identity, status, unit_name, ts in rows:
        check = ''
        if args.verify_live and status in ('running', 'dispatched', 'queued') and unit_name:
            live = real_unit_state(unit_name)
            if status == 'running' and live != 'active':
                check = f'MISMATCH (systemd={live}, DB=running)'
                mismatches += 1
        display_id = identity if identity else umr_id
        print(f"{display_id:<65} {status:<12} {check}")

    if args.verify_live:
        print('-' * 100)
        print(f"{mismatches} real DB/systemd mismatch(es) found -- not auto-corrected, review before trusting.")


if __name__ == '__main__':
    main()
