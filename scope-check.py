#!/usr/bin/env python3
"""
Enforces the Master/Supervisor pilot's "no collision" rule: every file a
worker's diff touches must belong to the task's declared module (per
file-ownership.yaml) or be explicitly listed in the task's own
files_allowed override. Deterministic, path-glob based -- same trust-
boundary posture as risk-tier.py (the AI reviewer cannot talk its way
around this, only supervisor-entrypoint.sh's caller decides what to do
with a violation).

Usage:
    scope-check.py <workspace_path> <base_ref> <module> [files_allowed_csv]

Exit 0 + prints "SCOPE_OK" if every changed file matches the module (or an
explicit files_allowed override). Exit 1 + prints "SCOPE_VIOLATION:" followed
by one violating file per line otherwise.
"""
import fnmatch
import subprocess
import sys
import yaml

OWNERSHIP_PATH = "/opt/veridian/ai-os/file-ownership.yaml"


def load_rules():
    with open(OWNERSHIP_PATH) as f:
        doc = yaml.safe_load(f)
    return doc["rules"]


def classify(path, rules):
    for rule in rules:
        if fnmatch.fnmatch(path, rule["glob"]):
            return rule["module"]
    return None  # unassigned -- always a violation unless explicitly allowed


def changed_files(workspace, base_ref):
    r = subprocess.run(
        ["git", "-C", workspace, "diff", "--name-only", f"{base_ref}...HEAD"],
        capture_output=True, text=True, check=True,
    )
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]


def main():
    if len(sys.argv) < 4:
        print("Usage: scope-check.py <workspace> <base_ref> <module> [files_allowed_csv]", file=sys.stderr)
        sys.exit(2)

    workspace, base_ref, module = sys.argv[1], sys.argv[2], sys.argv[3]
    files_allowed = [p.strip() for p in sys.argv[4].split(",")] if len(sys.argv) > 4 and sys.argv[4] else []

    rules = load_rules()
    files = changed_files(workspace, base_ref)

    violations = []
    for path in files:
        # Explicit per-task allowlist overrides the module map (a task can be
        # granted a narrow, deliberate cross-module exception -- e.g. a
        # backend task adding one additive schema column -- without loosening
        # the module map itself).
        if any(fnmatch.fnmatch(path, pat) for pat in files_allowed):
            continue
        owner = classify(path, rules)
        if owner != module:
            violations.append(f"{path} (owned by: {owner or 'UNASSIGNED'}, task's module: {module})")

    if violations:
        print("SCOPE_VIOLATION:")
        for v in violations:
            print(f"  {v}")
        sys.exit(1)

    print("SCOPE_OK")
    sys.exit(0)


if __name__ == "__main__":
    main()
