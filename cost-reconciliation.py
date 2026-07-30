#!/usr/bin/env python3
"""
Weekly (or on-demand) reconciliation report. Q6's "keep it working" check --
run this and read it, don't just trust the system silently did the right
thing. Zero API cost: reads only local logs/state.
"""
import json
import sys
from collections import Counter, defaultdict

REAL_COST_LOG = "/opt/veridian/ai-os/logs/glm-proxy-calls.jsonl"
CONTROLLER = "/opt/veridian/ai-os/CONTROLLER.yaml"
CACHE_DEPLOYED_AT = "2026-07-20T05:34:28"  # v2 proxy went live; cache hit rate before this is meaningless (0% by construction)


def load_cost_log():
    rows = []
    try:
        with open(REAL_COST_LOG) as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return rows


def load_tasks():
    import yaml
    try:
        d = yaml.safe_load(open(CONTROLLER))
        return d.get("tasks", [])
    except FileNotFoundError:
        return []


def main():
    rows = load_cost_log()
    total_cost = sum(r.get("real_cost_usd") or 0 for r in rows)
    cache_hits_alltime = sum(1 for r in rows if r.get("cache_hit"))
    real_calls = len(rows) - cache_hits_alltime

    # windowed since the cache actually existed -- blending in the thousands
    # of pre-cache calls (which can only ever be misses) makes the headline
    # number meaningless, not just noisy.
    since_rows = [r for r in rows if r.get("ts", "") >= CACHE_DEPLOYED_AT]
    since_hits = sum(1 for r in since_rows if r.get("cache_hit"))

    tasks = load_tasks()
    status_counts = Counter(t.get("status") for t in tasks)

    print("=== COST RECONCILIATION ===")
    print(f"Total logged cost:      ${total_cost:.4f}")
    print(f"Total calls (all-time): {len(rows)}  ({real_calls} real, {cache_hits_alltime} cache hits)")
    print(f"Calls since cache deployed ({CACHE_DEPLOYED_AT}): {len(since_rows)}")
    if since_rows:
        print(f"Cache hit rate (since deployment, the only fair number): {since_hits}/{len(since_rows)} = {since_hits/len(since_rows)*100:.1f}%")
        print("  Caveat: this window is still small and includes verification testing, not yet a clean")
        print("  steady-state measurement under normal diverse task dispatch.")
    print()
    print("=== TASK STATUS (all-time, CONTROLLER.yaml) ===")
    for status, count in status_counts.most_common():
        print(f"  {status:20s} {count}")
    total = sum(status_counts.values())
    failed = status_counts.get("failed", 0)
    if total:
        print(f"\n  Failure rate: {failed}/{total} = {failed/total*100:.1f}%")
        if failed / total > 0.20:
            print("  ALARM: failure rate above 20% -- investigate before dispatching more work.")

    # failure-signature repeat check across all tasks (needs task dirs, best-effort)
    import os
    repeat_offenders = []
    tasks_dir = "/opt/veridian/ai-os/tasks"
    if os.path.isdir(tasks_dir):
        for tid in os.listdir(tasks_dir):
            sig_file = os.path.join(tasks_dir, tid, ".failure_signatures.json")
            if os.path.exists(sig_file):
                try:
                    sigs = json.load(open(sig_file))
                    if len(sigs) >= 2 and sigs[-1] == sigs[-2]:
                        repeat_offenders.append(tid)
                except Exception:
                    pass
    if repeat_offenders:
        print(f"\n  ALARM: {len(repeat_offenders)} task(s) have 2 identical consecutive failure "
              f"signatures (circuit breaker should have stopped these):")
        for t in repeat_offenders:
            print(f"    - {t}")


if __name__ == "__main__":
    main()
