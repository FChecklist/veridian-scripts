#!/usr/bin/env python3
"""
Master role (on-demand, not a standing daemon -- see HETZNER-04 controller
entry for why: Claude Code CLI sessions are subscription-plan-authenticated
and concurrent-session limits are unverified, so the pilot keeps Master as
a triggered one-shot task like every other role in this framework, not a
6th always-running process).

Takes a high-level objective, calls `claude -p` to decompose it into
standardized-template tasks scoped to real files in the repo, validates
each task's declared module against file-ownership.yaml (not trusting the
AI's own module label), and appends the validated tasks into the
appropriate ai-os/queues/<module>.yaml file(s) -- new tasks always land as
status: NEW, ready for module-queue-dispatcher.py to pick up.

Master never writes application code and never merges -- this script's
only side effect is queue files.

Usage:
    master-decompose.py "<objective text>" --repo compliance-tracker --modules backend,database
"""
import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import yaml

# worker-entrypoint.sh runs under systemd with EnvironmentFile= supplying
# PATH and CLAUDE_CODE_OAUTH_TOKEN automatically. This script is triggered
# on-demand (SSH/cron), which does NOT inherit that -- load the same shared
# .env directly rather than assuming the caller's shell sourced it (real
# bug hit during pilot testing: `claude` wasn't found / unauthenticated
# when invoked from a bare SSH session).
os.environ["PATH"] = f"{os.path.expanduser('~/.local/bin')}:{os.path.expanduser('~/.local/share/supabase')}:/usr/bin:{os.environ.get('PATH', '')}"
_ENV_FILE = "/opt/veridian/shared/.env"
if os.path.exists(_ENV_FILE):
    with open(_ENV_FILE) as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _k, _, _v = _line.partition("=")
            _k = _k.strip()
            if _k == "ANTHROPIC_API_KEY_DISABLED_PER_OWNER_2026-07-18":
                continue  # deliberately disabled -- never load this one, see feedback_veridian_superboss_dispatch_prompt memory
            os.environ.setdefault(_k, _v.strip())

# 2026-07-19 (Owner directive, GLM-ROUTING-COST-PREVENTION-01): route through
# the local GLM-5.2/OpenRouter proxy (veridian-glm-proxy.service, 127.0.0.1:
# 8787) instead of the real Anthropic API, same as worker-entrypoint.sh /
# supervisor-entrypoint.sh. CLAUDE_CODE_OAUTH_TOKEN is popped (not just left
# unset) so it cannot leak through even though the loop above may have set
# it via os.environ.setdefault() from the shared .env file read above.
os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
# 2026-07-23: removed GLM/OpenRouter proxy override, Owner directive -- real Claude Max subscription auth now used everywhere (see worker-entrypoint.sh same-day fix)
os.environ["ANTHROPIC_API_KEY"] = "proxy-routed-not-a-real-anthropic-key"

QUEUES_DIR = "/opt/veridian/ai-os/queues"
OWNERSHIP_PATH = "/opt/veridian/ai-os/file-ownership.yaml"
PILOT_MODULES = {"backend", "database"}  # only these 2 are active in the pilot; frontend/qa_testing/devops queues exist but are not yet dispatched


def load_ownership_rules():
    with open(OWNERSHIP_PATH) as f:
        return yaml.safe_load(f)["rules"]


def classify(path, rules):
    for rule in rules:
        if fnmatch.fnmatch(path, rule["glob"]):
            return rule["module"]
    return None


def decompose(objective, repo_path, modules):
    prompt = f"""You are the VERIDIAN-DEV Master. Decompose the objective below into a set of
standardized, module-scoped tasks. Do not write any code yourself -- read the real
repo structure first (it's the current directory) so file paths are real, not guessed.

Objective: {objective}

Only decompose work for these modules (others are out of pilot scope right now): {', '.join(modules)}

For EACH task, write exactly one JSON object with these fields:
- id: a short unique slug, e.g. "BACKEND-001"
- module: one of {list(modules)}
- objective: one sentence, specific
- files_allowed: array of real glob patterns scoped to this module (check the actual repo structure -- do not guess paths that don't exist)
- files_forbidden: array of glob patterns this task must never touch (can be empty)
- dependencies: array of other task ids in this same decomposition that must merge first (can be empty)
- input: what this task starts from
- output: what it produces
- steps: array of concrete steps
- constraints: array of hard rules
- validation: how to verify it's correct
- done_criteria: one sentence

Write the full list as a JSON array to a file named master-decomposition.json in the
current directory (repo root). Do not modify any other file. Do not run git commands."""

    result = subprocess.run(
        ["claude", "-p", prompt, "--model", "sonnet", "--effort", "high", "--dangerously-skip-permissions", "--output-format", "json"],
        cwd=repo_path, capture_output=True, text=True, timeout=600,
    )
    out_path = os.path.join(repo_path, "master-decomposition.json")
    if not os.path.exists(out_path):
        print("Master decomposition FAILED -- no output file. stderr:", result.stderr, file=sys.stderr)
        sys.exit(1)
    with open(out_path) as f:
        tasks = json.load(f)
    os.remove(out_path)
    return tasks


def validate_and_route(tasks, rules, modules):
    by_module = {}
    rejected = []
    for t in tasks:
        module = t.get("module")
        if module not in modules:
            rejected.append((t.get("id", "?"), f"module '{module}' not in pilot scope {modules}"))
            continue
        # Cross-check every declared files_allowed pattern actually maps to the
        # declared module per the deterministic ownership map -- don't trust the
        # AI's own module label at face value.
        bad_files = []
        for pat in t.get("files_allowed", []):
            owner = classify(pat, rules)
            if owner and owner != module and owner != "master_reviewed":
                bad_files.append(f"{pat} -> owned by {owner}, not {module}")
        if bad_files:
            rejected.append((t["id"], f"files_allowed mismatch: {bad_files}"))
            continue
        t["status"] = "NEW"
        t["task_id"] = None
        by_module.setdefault(module, []).append(t)
    return by_module, rejected


def append_to_queues(by_module):
    os.makedirs(QUEUES_DIR, exist_ok=True)
    for module, tasks in by_module.items():
        path = f"{QUEUES_DIR}/{module}.yaml"
        if os.path.exists(path):
            with open(path) as f:
                doc = yaml.safe_load(f) or {"module": module, "queue": []}
        else:
            doc = {"module": module, "queue": []}
        existing_ids = {t["id"] for t in doc["queue"]}
        new_tasks = [t for t in tasks if t["id"] not in existing_ids]
        doc["queue"].extend(new_tasks)
        tmp = f"{path}.tmp"
        with open(tmp, "w") as f:
            yaml.safe_dump(doc, f, sort_keys=False, default_flow_style=False, allow_unicode=True)
        os.replace(tmp, path)
        print(f"{module}: appended {len(new_tasks)} task(s) to {path} ({len(tasks) - len(new_tasks)} skipped as already-present)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("objective")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--modules", default="backend,database")
    args = ap.parse_args()

    modules = set(args.modules.split(","))
    unknown = modules - PILOT_MODULES
    if unknown:
        print(f"ERROR: {unknown} not in pilot scope ({PILOT_MODULES}). Frontend/qa_testing/devops queues exist "
              f"but are not yet activated for dispatch -- expand PILOT_MODULES once the pilot proves out.", file=sys.stderr)
        sys.exit(1)

    repo_path = f"/opt/veridian/repos/{args.repo}"

    # --- Pre-flight guard (2026-07-20, constitution-audit gap #7): confirmed
    # ZERO protection existed here before this -- no disk/mem check, no proxy
    # health check, no canary call before spending a real claude -p call
    # through the GLM proxy. Reuses preflight-guard.py, passing repo_path for
    # both task_dir/workspace args since Master has no per-task directory of
    # its own (it's a bare on-demand CLI, not a systemd-dispatched task) --
    # the circuit-breaker/tight-schema checks inside it read
    # <task_dir>/.failure_signatures.json and <task_dir>/prompt.txt, neither
    # of which exist under a repo path, so they no-op harmlessly (same
    # degraded-but-safe behavior already accepted for supervisor-entrypoint.sh).
    # Disk/mem/proxy-health/canary checks are fully real.
    guard = subprocess.run(
        ["python3", "/opt/veridian/scripts/preflight-guard.py", repo_path, repo_path, "http://127.0.0.1:8787"],
        capture_output=True, text=True,
    )
    if guard.returncode != 0:
        try:
            reason = json.loads(guard.stdout or guard.stderr).get("reason", "unknown")
            detail = json.loads(guard.stdout or guard.stderr).get("detail", "")
        except (json.JSONDecodeError, ValueError):
            reason, detail = "unknown", (guard.stdout + guard.stderr).strip()
        print(f"Master pre-flight REJECTED ({reason}): {detail}", file=sys.stderr)
        sys.exit(1)

    rules = load_ownership_rules()
    tasks = decompose(args.objective, repo_path, modules)
    by_module, rejected = validate_and_route(tasks, rules, modules)

    if rejected:
        print("REJECTED (Master's own output failed deterministic validation, not written to any queue):")
        for tid, reason in rejected:
            print(f"  {tid}: {reason}")

    append_to_queues(by_module)


if __name__ == "__main__":
    main()
