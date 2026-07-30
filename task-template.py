#!/usr/bin/env python3
"""
Renders a standardized task dict (TASK ID/MODULE/OBJECTIVE/FILES ALLOWED/
FILES FORBIDDEN/DEPENDENCIES/INPUT/OUTPUT/STEPS/CONSTRAINTS/VALIDATION/
DONE CRITERIA -- the Master/Supervisor pilot's standard template) into the
prompt text handed to `veridian-task.py create`. The executor is always
Claude Code CLI (not a raw patch-only model -- see HETZNER-04 controller
entry for why), so the rendered prompt keeps the existing worker
conventions (PROGRESS.md, quality gates) on top of the standard fields,
rather than the original proposal's bare "return patch only" framing,
which assumes a harness this pilot deliberately doesn't build.

Importable (render_task_prompt) and usable as a CLI for one-off checks:
    task-template.py <task_yaml_path>
"""
import sys
import yaml


def render_task_prompt(task: dict) -> str:
    lines = [
        f"TASK ID: {task['id']}",
        f"MODULE: {task['module']}",
        "",
        f"OBJECTIVE: {task['objective']}",
        "",
        "FILES ALLOWED (you may only modify files matching these patterns):",
    ]
    lines += [f"  - {p}" for p in task.get("files_allowed", [])] or ["  (none declared -- confirm scope before writing any code)"]
    lines += ["", "FILES FORBIDDEN (never touch, even if it seems related):"]
    lines += [f"  - {p}" for p in task.get("files_forbidden", [])] or ["  (none beyond the module boundary itself)"]

    deps = task.get("dependencies", [])
    if deps:
        lines += ["", f"DEPENDENCIES: this task depends on {', '.join(deps)} already being MERGED. "
                       "If any dependency is not yet merged into main, stop and report blocked -- do not proceed."]

    lines += [
        "",
        f"INPUT: {task.get('input', '(none specified)')}",
        "",
        f"OUTPUT: {task.get('output', '(none specified)')}",
        "",
        "STEPS:",
    ]
    lines += [f"  {i+1}. {s}" for i, s in enumerate(task.get("steps", []))]

    constraints = task.get("constraints", [])
    if constraints:
        lines += ["", "CONSTRAINTS:"]
        lines += [f"  - {c}" for c in constraints]

    lines += [
        "",
        f"VALIDATION: {task.get('validation', 'Standard quality gates (lint/typecheck/build/test) must pass.')}",
        "",
        f"DONE CRITERIA: {task.get('done_criteria', '(none specified -- use judgment, but state what you consider done in PROGRESS.md)')}",
        "",
        "---",
        "Before writing any code: read the actual current implementation of the relevant "
        "file(s) first -- do not assume this task's description is still accurate, the "
        "codebase may have moved since this task was written. If FILES ALLOWED is too "
        "narrow to actually complete the objective, stop and report this in PROGRESS.md "
        "as a scope-definition problem rather than silently touching forbidden files -- "
        "a supervisor will re-scope it, not you.",
        "Maintain a PROGRESS.md file in the repository root with '## Completed' and "
        "'## Remaining' sections, each a markdown checklist. Update it as you complete "
        "each meaningful step.",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: task-template.py <task_yaml_path>", file=sys.stderr)
        sys.exit(2)
    with open(sys.argv[1]) as f:
        task = yaml.safe_load(f)
    print(render_task_prompt(task))
