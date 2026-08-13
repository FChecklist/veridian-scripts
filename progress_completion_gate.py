#!/usr/bin/env python3
"""Conflict-free per-task progress files + a real completion gate.

REAL DEFECT (UMR-20260813-195922-f548, dispatched off PM-desktop-sentinel
tick 2026-08-13T19:45Z, governing chain UMR-20260806-171945-5767): every
worker rewrote ONE shared PROGRESS.md on its own branch --
worker-entrypoint.sh's own PROGRESS_INSTRUCTION literally told every worker
"maintain PROGRESS.md", no per-task namespacing at all. Two compounding
failures resulted, confirmed live against FChecklist/veridian-scripts open
PRs on 2026-08-13:

  (A) EMPTY FIXES SHIPPED AS REAL ONES. PR #317 ("swap gate vetoes on
      STATIC occupancy, dispatch_core.py") and PR #321 ("pm-sentinel-tick.sh
      positional systemctl show parse") each have exactly one file in their
      diff: PROGRESS.md. Neither dispatch_core.py nor pm-sentinel-tick.sh is
      touched. Both got recorded as real completed work.
  (B) UNIVERSAL MERGE CONFLICTS. Because every branch rewrites overlapping
      regions of the one PROGRESS.md, every long-lived branch that touches
      it conflicts with every OTHER branch that also touched it, regardless
      of whether their real code overlaps at all. Of the 25 parseable open,
      mergeStateStatus=DIRTY PRs on veridian-scripts at the time this was
      written, 17 were PROGRESS.md-only diffs stuck CONFLICTING for exactly
      this reason (gh pr list -R FChecklist/veridian-scripts --state open
      --json number,mergeStateStatus,files).

Correction against the dispatching SPEC's own claim: PR #297 is NOT
explained by this defect -- its diff never touches PROGRESS.md at all
(dispatch-owner-task.sh, pm-sentinel-tick.sh, superboss-register.py,
test_pm_sentinel_tick.py, tests/test_target_identifier_dedup.py); it is
CONFLICTING/DIRTY for a real, unrelated code-conflict reason. Only
#315/#317/#321 are genuinely PROGRESS.md-only.

This module provides the two pieces of real (mechanical, not
prompt-instruction) enforcement worker-entrypoint.sh now calls:

  1. `check-completion` -- if a task's own prompt.txt names a specific
     source/script file as its objective, that file must appear in the
     task's real git diff (committed + staged + unstaged). A diff that only
     touches progress/doc artifacts for a code-named objective is REJECTED
     with an explicit reason and a non-zero exit code -- never silently
     accepted as complete.
  2. `rollup` -- deterministically regenerates a single rolled-up view from
     every progress/<task_id>.md file, sorted by filename. This is
     GENERATED OUTPUT, never a hand-edit target -- no worker branch ever
     writes to it, so it can never be the shared-file merge conflict
     PROGRESS.md was.

Not a duplicate of tests/test_ocid_registry_completion_gate.py -- that gate
checks OCID registry row completeness fields, an unrelated registry-schema
concept, not diff-content-vs-stated-objective.
"""
import argparse
import os
import re
import subprocess
import sys

# Extensions that count as "a specific source file or script" for the
# completion gate. Deliberately excludes .md/.txt/.json/.yaml doc/config
# extensions -- the gate exists to catch "objective named a CODE file, diff
# has no code", not to force every task to touch a file of some kind.
CODE_EXTENSIONS = (
    "py", "sh", "js", "jsx", "ts", "tsx", "go", "rb",
    "java", "c", "h", "cpp", "hpp", "rs", "sql",
)

FILENAME_RE = re.compile(
    r"[A-Za-z0-9_\-./]+\.(?:" + "|".join(CODE_EXTENSIONS) + r")\b"
)

# Progress/doc artifacts the gate must never itself treat as "the named
# objective file", even when they appear in prose right next to a real code
# filename (e.g. "update PROGRESS.md after fixing dispatch_core.py") -- these
# are exactly the artifacts this whole fix exists to stop conflating with
# real code.
_PROGRESS_ARTIFACT_RES = (
    re.compile(r"^PROGRESS\.md$", re.IGNORECASE),
    re.compile(r"^progress/.*\.md$", re.IGNORECASE),
    re.compile(r"^RCA.*\.md$", re.IGNORECASE),
)


def is_progress_artifact(path):
    base = path.rsplit("/", 1)[-1]
    return any(p.match(path) or p.match(base) for p in _PROGRESS_ARTIFACT_RES)


def extract_named_code_files(text):
    """Real source/script filenames referenced in a task's own spec text
    (prompt.txt). Order-preserving de-dup; progress/doc artifacts excluded
    even though their extension (.md) is not in CODE_EXTENSIONS anyway --
    kept explicit so the exclusion is provable, not incidental."""
    seen = []
    for m in FILENAME_RE.finditer(text or ""):
        candidate = m.group(0)
        if is_progress_artifact(candidate):
            continue
        if candidate not in seen:
            seen.append(candidate)
    return seen


def _git(workspace, *args):
    return subprocess.run(
        ["git", "-C", workspace, *args], capture_output=True, text=True
    )


def git_diff_files(workspace, default_branch):
    """Real changed-file set for this branch: committed-since-merge-base,
    PLUS staged, PLUS unstaged. This gate can run before the worker's own
    final `git add -A && git commit` (see worker-entrypoint.sh's
    COMPLETION-GATE-BLOCK, which runs before that commit), so committed-only
    would miss real in-progress work; and it must also see fully-committed
    branches if invoked standalone (e.g. from a test or a resume), so
    committed history is not skipped either."""
    out = set()
    mb = _git(workspace, "merge-base", f"origin/{default_branch}", "HEAD")
    merge_base = mb.stdout.strip() if mb.returncode == 0 else f"origin/{default_branch}"
    for args in (
        ("diff", "--name-only", merge_base, "HEAD"),
        ("diff", "--name-only", "HEAD"),
        ("diff", "--name-only", "--cached"),
    ):
        res = _git(workspace, *args)
        if res.returncode == 0:
            out.update(f for f in res.stdout.splitlines() if f)
    return out


def check_completion(task_dir, workspace, default_branch):
    """Returns (ok, reason).

    ok=True whenever there is nothing real to gate on (objective names no
    code file) OR at least one named file is really present in the diff.

    ok=False -- a REAL rejection, never downgraded to a success status --
    only when the objective names >=1 code file, the diff is non-empty, and
    NONE of the named files are in it (i.e. a doc/progress-only diff for a
    code-named objective).
    """
    prompt_path = os.path.join(task_dir, "prompt.txt")
    try:
        with open(prompt_path) as f:
            spec_text = f.read()
    except FileNotFoundError:
        return True, "no prompt.txt found -- nothing to gate on"

    named = extract_named_code_files(spec_text)
    if not named:
        return True, "objective names no specific source/script file -- gate does not apply"

    diff_files = git_diff_files(workspace, default_branch)
    if not diff_files:
        return True, "empty diff -- handled by the separate no-op path, not this gate"

    diff_basenames = {f.rsplit("/", 1)[-1] for f in diff_files}
    matched = [
        n for n in named
        if n in diff_files or n.rsplit("/", 1)[-1] in diff_basenames
    ]
    if matched:
        return True, f"objective-named file(s) present in diff: {matched}"

    non_progress = sorted(f for f in diff_files if not is_progress_artifact(f))
    reason = (
        f"objective named {named} but the diff touches no code -- "
        f"diff only contains: {sorted(diff_files)}"
    )
    if non_progress:
        reason += f" (non-progress files present but none of them match: {non_progress})"
    return False, reason


def cmd_check_completion(args):
    ok, reason = check_completion(args.task_dir, args.workspace, args.default_branch)
    print(reason)
    return 0 if ok else 1


def cmd_rollup(args):
    """Deterministic, generated-only rollup -- never a merge target. Reads
    every progress/*.md file in the workspace, in filename-sorted order,
    and concatenates them under an explicit generated-file banner. Safe to
    run from any number of concurrent branches: it only READS progress/*.md
    (each worker's own file) and WRITES a single output path that no worker
    branch itself commits to as part of its own progress protocol."""
    progress_dir = os.path.join(args.workspace, "progress")
    lines = [
        "<!-- GENERATED by progress_completion_gate.py rollup -- do not hand-edit. -->",
        "<!-- Source of truth is progress/<task_id>.md, one file per task. -->",
        "",
        "# Progress rollup",
        "",
    ]
    if os.path.isdir(progress_dir):
        for name in sorted(os.listdir(progress_dir)):
            if not name.endswith(".md"):
                continue
            with open(os.path.join(progress_dir, name)) as f:
                body = f.read().rstrip()
            lines.append(f"## {name}")
            lines.append("")
            lines.append(body)
            lines.append("")
    output = "\n".join(lines) + "\n"
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
    else:
        sys.stdout.write(output)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser(
        "check-completion",
        help="reject a doc/progress-only diff for a code-named objective",
    )
    p_check.add_argument("--task-dir", required=True)
    p_check.add_argument("--workspace", required=True)
    p_check.add_argument("--default-branch", required=True)
    p_check.set_defaults(func=cmd_check_completion)

    p_roll = sub.add_parser(
        "rollup", help="regenerate the deterministic progress rollup view"
    )
    p_roll.add_argument("--workspace", required=True)
    p_roll.add_argument("--output", help="write here instead of stdout")
    p_roll.set_defaults(func=cmd_rollup)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
