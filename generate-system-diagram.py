#!/usr/bin/env python3
"""
generate-system-diagram.py -- TASK 3 (Owner directive 2026-07-20: "make a
diagram or tree of the whole system"). Software-generated, not hand-drawn:
the diagram's content lives as structured data below (SUBSYSTEMS/EDGES),
and this script renders it to Mermaid -- updating the diagram means editing
the data, not redrawing a picture. Run this after any real architecture
change (new bridge, new major component) to regenerate
ai-os/SYSTEM_DIAGRAM.md.

Reflects the Owner's own framing: "SERVER BASICALLY HAS TWO SUB SYSTEMS -
SUB SYSTEM 1 - SOFTWARE DEVELOPMENT AND AI SYSTEM. SUB SYSTEM 2 - is Actual
SOFTWARE and END USER MANAGEMENT." Every component/edge listed here was
verified to actually exist this session (or in prior sessions' verified
work referenced in MASTER_INDEX.yaml) -- not aspirational.
"""

SUBSYSTEM_1 = {
    "id": "sub1",
    "title": "SUBSYSTEM 1: Software Development & AI System (VERIDIAN-DEV, Hetzner 167.233.220.35)",
    "nodes": [
        ("s1_dispatch", "queue-dispatcher.py + gap_queue.yaml (cron, /10min)"),
        ("s1_worker", "veridian-worker@.service fleet (systemd, autonomous claude -p coding tasks)"),
        ("s1_docworker", "veridian-docworker@.service fleet (real-subscription browser tasks)"),
        ("s1_preflight", "preflight-guard.py (circuit breaker, OpenRouter balance, canary)"),
        ("s1_entrypoint", "worker-entrypoint.sh (checkpoint, quality-gate, hard-stop logic)"),
        ("s1_taskcli", "veridian-task.py (CONTROLLER.yaml state machine, choke point)"),
        ("s1_controller", "CONTROLLER.yaml (live task ledger)"),
        ("s1_register", "superboss-register.sqlite (4-tree: instructions/work_items/actions/system_index)"),
        ("s1_masterindex", "MASTER_INDEX.yaml (single entrypoint, this file)"),
        ("s1_syncsync", "system-sync.py (mirror drift / constitution staleness / unindexed files / balance-resume, cron /6h)"),
        ("s1_healthcheck", "health-check-15min.py (systemd + app-layer failure monitor, cron /15min)"),
        ("s1_attention", "ATTENTION.md (live alert surface)"),
        ("s1_proxy", "anthropic_openrouter_proxy_v2.py (GLM-5.2 cache + budget ceiling)"),
    ],
}

SUBSYSTEM_2 = {
    "id": "sub2",
    "title": "SUBSYSTEM 2: Software & End-User Management (compliance-tracker + PROJEXA, one integrated software, Vercel + Supabase)",
    "nodes": [
        ("s2_app", "Next.js app (compliance-tracker + PROJEXA via /api/v1/projexa/*)"),
        ("s2_aiteam", "AI Team dispatch (/api/ai/team/dispatch, task-execution-engine.ts)"),
        ("s2_motherrouter", "Mother Router (ai_routing_policies, ai_routing_audit_log)"),
        ("s2_resolver", "orchestra-model-resolver.ts + model-tier-eligibility.ts (35 direct callers, not yet migrated)"),
        ("s2_llmclient", "llm-client.ts (callAnthropic/callOpenAICompatible, prompt caching)"),
        ("s2_orchestra", "compliance.orchestra_executions (AI call log, org-scoped)"),
        ("s2_activity", "compliance.activity_log (built, 0 rows -- not yet wired)"),
        ("s2_opsdevtasks", "platform.ops_dev_tasks (NEW 2026-07-20, non-org-scoped)"),
        ("s2_syncroute", "POST /api/internal/ops-task-sync (OPS_SYNC_SECRET bearer auth)"),
        ("s2_supabase", "Supabase Postgres (pcrjmlpuqsbocqfwoxod)"),
    ],
}

# (from_node, to_node, label)
EDGES = [
    ("s1_dispatch", "s1_worker", "dispatches"),
    ("s1_worker", "s1_preflight", "runs preflight before invocation"),
    ("s1_worker", "s1_entrypoint", "executes via"),
    ("s1_entrypoint", "s1_taskcli", "checkpoint calls"),
    ("s1_taskcli", "s1_controller", "reads/writes"),
    ("s1_taskcli", "s1_register", "auto-logs to (fail-open)"),
    ("s1_worker", "s1_proxy", "AI calls route through"),
    ("s1_syncsync", "s1_masterindex", "keeps in sync"),
    ("s1_syncsync", "s1_register", "registers new files"),
    ("s1_healthcheck", "s1_attention", "writes anomalies to"),
    ("s1_healthcheck", "s1_controller", "reads task state"),
    ("s2_app", "s2_aiteam", "dispatches AI work via"),
    ("s2_aiteam", "s2_resolver", "resolves model via (task-execution-engine.ts)"),
    ("s2_resolver", "s2_llmclient", "calls"),
    ("s2_aiteam", "s2_orchestra", "logs execution to"),
    ("s2_motherrouter", "s2_resolver", "wraps (new call sites only, 35 legacy sites bypass)"),
    ("s2_opsdevtasks", "s2_supabase", "lives in"),
    ("s2_orchestra", "s2_supabase", "lives in"),
    # The bridges -- the whole point of TASKS 1.1/2/4.
    ("s1_taskcli", "s2_syncroute", "TASK 1.1: POSTs checkpoint state to (new, PR #502, held for sign-off)"),
    ("s2_syncroute", "s2_opsdevtasks", "upserts into"),
    ("s1_healthcheck", "s2_supabase", "TASK 2/4: reads orchestra_executions directly via DATABASE_URL (reused, already-proven connection)"),
]


def render_mermaid():
    lines = ["```mermaid", "flowchart TB"]
    for sub in (SUBSYSTEM_1, SUBSYSTEM_2):
        lines.append(f'  subgraph {sub["id"]} ["{sub["title"]}"]')
        for node_id, label in sub["nodes"]:
            safe_label = label.replace('"', "'")
            lines.append(f'    {node_id}["{safe_label}"]')
        lines.append("  end")
    for src, dst, label in EDGES:
        safe_label = label.replace('"', "'")
        lines.append(f'  {src} -->|"{safe_label}"| {dst}')
    lines.append("```")
    return "\n".join(lines)


def render_doc():
    node_count = len(SUBSYSTEM_1["nodes"]) + len(SUBSYSTEM_2["nodes"])
    return f"""# VERIDIAN System Diagram

Auto-generated by `ai-os/scripts/generate-system-diagram.py` -- {node_count} components,
{len(EDGES)} edges. Every component/edge was verified to actually exist as of the
generation date below (not aspirational). To update: edit the SUBSYSTEMS/EDGES
data in that script and re-run it, do not hand-edit this file's diagram block.

Generated: 2026-07-20

## Two subsystems (Owner's own framing, 2026-07-20 directive)

{render_mermaid()}

## Reading this diagram

- **Subsystem 1** runs entirely on VERIDIAN-DEV (Hetzner, 167.233.220.35) --
  the autonomous coding-task worker fleet, its own SQLite register, and the
  scripts that keep it internally consistent (`system-sync.py`,
  `health-check-15min.py`).
- **Subsystem 2** is the actual product -- compliance-tracker and PROJEXA,
  treated as one integrated software per Owner directive, deployed on
  Vercel with a Supabase Postgres backend.
- **The two bridge edges at the bottom are the newest work** (2026-07-20,
  TASKS 1.1 and 2/4) -- before this pass, these two subsystems had zero
  connection to each other's task/failure state. TASK 1.1's bridge (ops to
  app, write path, via a new API route) is still open for Owner sign-off
  (`PR #502`, Tier2: schema + new secret). TASK 2/4's bridge (app to ops,
  read path) is already live, reusing an existing, already-proven database
  connection rather than new infrastructure.
- **35 unmigrated AI-router call sites** (`s2_resolver`) are a known,
  deliberately-deferred gap -- see
  `ai-os/MASTER_INDEX.yaml registries.ai_router_migration_inventory_2026_07_20`
  for the full reasoning and file list.
"""


if __name__ == "__main__":
    import sys
    out_path = sys.argv[1] if len(sys.argv) > 1 else "ai-os/SYSTEM_DIAGRAM.md"
    with open(out_path, "w") as f:
        f.write(render_doc())
    print(f"Wrote {out_path}")
