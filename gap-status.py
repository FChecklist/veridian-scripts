#!/usr/bin/env python3
"""Quick X/Y status check for the gap queue, plus anything needing a decision."""
import yaml

with open("/opt/veridian/ai-os/gap_queue.yaml") as f:
    doc = yaml.safe_load(f)

by_status = {}
for item in doc["queue"]:
    by_status.setdefault(item["status"], []).append(item)

total = len(doc["queue"])
completed = len(by_status.get("completed", []))
print(f"{completed}/{total} groups completed ({doc.get('total_findings', 'unknown')} total findings across all groups)")

for status in ("awaiting_human_approval", "stuck_needs_human", "needs_retry", "dispatch_failed", "skipped_possible_duplicate"):
    items = by_status.get(status, [])
    if items:
        print(f"\n{status} ({len(items)}):")
        for it in items:
            print(f"  {it['id']}")
