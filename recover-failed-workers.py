#!/usr/bin/env python3
"""
RCA recovery script, 2026-07-20. Converts veridian-worker@ units stuck in
systemd's permanent 'failed' state (pre-dating the preflight-guard.py
OpenRouter-balance fix) into a clean, accurate 'blocked' state via the NOW-
FIXED preflight guard -- so every affected task carries a correct,
actionable diagnostic instead of a dead, retry-stormed unit with no clear
reason recorded.

Safety: for each failed unit, reads its task's result.json FIRST and only
resets+restarts it if the failure is confirmed to be the same
api_error_status:402 pattern this pass's fix specifically addresses --
never blindly resets a unit whose failure might be a genuinely different,
unrelated bug (those are left alone, printed separately for manual review).

Usage: recover-failed-workers.py [--dry-run]
"""
import json
import subprocess
import sys

DRY_RUN = "--dry-run" in sys.argv


def list_failed_units():
    out = subprocess.run(
        ["systemctl", "--user", "list-units", "--state=failed", "--no-pager", "--no-legend"],
        capture_output=True, text=True,
    ).stdout
    units = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].startswith("veridian-worker@"):
            units.append(parts[1])
    return units


def task_id_from_unit(unit):
    # veridian-worker@task-xyz.service -> task-xyz
    inner = unit.split("@", 1)[1]
    return inner.rsplit(".service", 1)[0]


def is_402_balance_failure(task_id):
    result_path = f"/opt/veridian/ai-os/tasks/{task_id}/result.json"
    try:
        with open(result_path) as f:
            content = f.read()
    except FileNotFoundError:
        return False
    return '"api_error_status":402' in content


def main():
    units = list_failed_units()
    print(f"Found {len(units)} failed veridian-worker@ units.")
    recovered, skipped_other_reason, skipped_no_result = [], [], []

    for unit in units:
        task_id = task_id_from_unit(unit)
        if not is_402_balance_failure(task_id):
            skipped_no_result.append(unit)
            continue
        recovered.append(unit)
        if DRY_RUN:
            print(f"[DRY RUN] would reset-failed + start: {unit}")
            continue
        subprocess.run(["systemctl", "--user", "reset-failed", unit], capture_output=True)
        subprocess.run(["systemctl", "--user", "start", unit], capture_output=True)
        print(f"Reset + restarted: {unit} (task {task_id})")

    print()
    print(f"Recovered (confirmed 402 balance failure, reset+restarted): {len(recovered)}")
    print(f"Skipped (no confirmed 402 pattern -- needs manual review, NOT touched): {len(skipped_no_result)}")
    if skipped_no_result:
        print("Skipped units:")
        for u in skipped_no_result:
            print(f"  {u}")


if __name__ == "__main__":
    main()
