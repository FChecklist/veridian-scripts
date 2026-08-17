#!/usr/bin/env python3
"""
Apply DONE_MISLABELLED corrections for 13 running owner-tasks from 2026-08-16.
Each task has evidence of corresponding merged PR from same day.
"""
import subprocess
import sys

# Mapping of UMR_ID to (PR_number, commit_SHA)
corrections = {
    'UMR-20260816-092547-5ab3': (433, 'c826737'),  # 09:25
    'UMR-20260816-093009-1c80': (433, 'c826737'),  # 09:30
    'UMR-20260816-093014-edf6': (433, 'c826737'),  # 09:30
    'UMR-20260816-093131-55a9': (433, 'c826737'),  # 09:31
    'UMR-20260816-093135-57d6': (433, 'c826737'),  # 09:31
    'UMR-20260816-141405-8ffc': (440, 'b3db405'),  # 14:14
    'UMR-20260816-141409-76c7': (441, 'ef7100a'),  # 14:14
    'UMR-20260816-144354-a612': (440, 'b3db405'),  # 14:43
    'UMR-20260816-171145-08ac': (442, 'cc59f1e'),  # 17:11
    'UMR-20260816-171232-c50f': (442, 'cc59f1e'),  # 17:12
    'UMR-20260816-171258-bf1d': (442, 'cc59f1e'),  # 17:12
    'UMR-20260816-171513-5901': (442, 'cc59f1e'),  # 17:15
    'UMR-20260816-171932-d5eb': (442, 'cc59f1e'),  # 17:19
}

print(f"Applying corrections for {len(corrections)} DONE_MISLABELLED rows...\n")

succeeded = 0
failed = 0

for umr_id, (pr_num, sha) in sorted(corrections.items()):
    cmd = [
        'python3', '/opt/veridian/scripts/superboss-register.py',
        'mark-umr-terminal',
        '--umr-id', umr_id,
        '--status', 'completed',
        '--commit-sha', sha,
        '--pr-number', str(pr_num),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode == 0:
        print(f"✓ {umr_id} → PR #{pr_num} ({sha})")
        succeeded += 1
    else:
        print(f"✗ {umr_id} → ERROR")
        if 'already' in result.stdout or 'already' in result.stderr:
            print(f"  (already corrected)")
            succeeded += 1
        else:
            print(f"  stdout: {result.stdout[:100]}")
            print(f"  stderr: {result.stderr[:100]}")
            failed += 1

print(f"\n\nSummary: {succeeded} corrected, {failed} failed")
sys.exit(0 if failed == 0 else 1)
