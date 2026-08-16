#!/usr/bin/env python3
"""Deterministic guard: does a real git diff contain at least one genuine
source/test/config/schema change, or is it progress/documentation only
(UMR-20260816-171513-5901, Owner directive 2026-08-16)?

REAL PROBLEM this closes: supervisor-entrypoint.sh's own `gh pr create`
fired unconditionally for any branch with a non-zero AHEAD_COUNT, and
(confirmed live, not assumed) worker Claude sessions themselves also
directly ran `gh pr create` mid-session for tasks whose entire real diff
was a single per-task progress file -- e.g. task-20260815-114035's own
result.json shows a literal `gh pr create --title ...` call, and that
task's supervisor.log then shows supervisor-entrypoint.sh's own later `gh
pr create` attempt failing with "a pull request ... already exists",
falling through to reuse the worker-created PR (FChecklist/compliance-
tracker PR #1291, diff stat: 1 file changed, progress/task-...md only; same
confirmed shape for PRs #1277 and #1290). Real, measured impact as of
2026-08-16: FChecklist/compliance-tracker held 422 open PRs, 414 authored
by this fleet's shared bot identity, 189 with a "docs" title prefix; of a
500-PR sample of open PRs, 115 touched nothing but progress/*.md or other
prose/doc paths, against a near-zero real landing rate.

Deliberately REUSES, rather than reimplements, quality-gate.sh's own real,
already-audit-hardened DOCS_ONLY allowlist (DOCS_ONLY_EXT_PATTERN /
DOCS_ONLY_NAME_PATTERN -- see that file's own PR #305 audit-fail history,
heads b315ae9/a63def8e, for why this is an ALLOWLIST that fails CLOSED to
code-relevant on anything unrecognized, never a blocklist) -- extracted
directly from the live quality-gate.sh source at call time (same technique
tests/test_quality_gate_docs_only.py already uses to keep itself in sync),
so this module can never silently drift from the one other place in this
codebase that already answers "is this diff code-relevant". Per
AGENTS.md's Search-Reuse Discipline (Operating Rule 5): a second,
independently-maintained classification of the same real question is
exactly the duplication that rule exists to prevent.

Used as a guard at ONE real choke point: supervisor-entrypoint.sh, right
before the (paid) Superboss AI review call and `gh pr create` -- same
cost-avoidance placement as the pre-existing NO-OP-BRANCH-GUARD-BLOCK and
GITLINK-GUARD-BLOCK immediately above it in that file.
"""
import argparse
import os
import re
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
QUALITY_GATE = os.path.join(SCRIPTS_DIR, "quality-gate.sh")


def _docs_only_patterns(quality_gate_path=QUALITY_GATE):
    """Pulls the two exact, live docs-only allowlist regexes out of
    quality-gate.sh itself, in source order. Raises loudly (never guesses a
    fallback pattern) if that script's own detection logic has moved or
    changed shape -- silently falling back to a stale copy here would be
    exactly the kind of drift Search-Reuse Discipline exists to prevent."""
    with open(quality_gate_path) as f:
        src = f.read()
    ext = re.search(r"DOCS_ONLY_EXT_PATTERN='((?:[^'\\]|\\.)*)'", src)
    name = re.search(r"DOCS_ONLY_NAME_PATTERN='((?:[^'\\]|\\.)*)'", src)
    if not (ext and name):
        raise RuntimeError(
            f"expected DOCS_ONLY_EXT_PATTERN/DOCS_ONLY_NAME_PATTERN single-quoted "
            f"assignments in {quality_gate_path} -- has the detection logic moved "
            f"or changed shape? Refusing to guess a stale fallback."
        )
    return ext.group(1), name.group(1)


def changed_files(workspace, base_ref, head_ref="HEAD"):
    """Real changed-file list for base_ref...head_ref (three-dot: files
    changed on head_ref since it diverged from base_ref), matching the
    exact same diff range every other guard in this codebase (gitlink_guard,
    the Superboss review's own DIFF_STAT) already uses."""
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...{head_ref}"],
        cwd=workspace, capture_output=True, text=True, check=False,
    )
    return [line for line in proc.stdout.splitlines() if line.strip()]


def is_code_relevant(files, quality_gate_path=QUALITY_GATE):
    """True if AT LEAST ONE changed file is a genuine source/test/config/
    schema change -- i.e. does not match the docs-only allowlist. An empty
    file list is treated as NOT code-relevant (nothing to justify a PR
    for). Reproduces quality-gate.sh's own real `grep -qvE` semantics
    exactly: code-relevant iff at least one line does NOT match either
    allowlist pattern (fails closed)."""
    if not files:
        return False
    ext_pattern, name_pattern = _docs_only_patterns(quality_gate_path)
    combined = "{}|{}".format(ext_pattern, name_pattern)
    joined = "\n".join(files) + "\n"
    result = subprocess.run(["grep", "-qvE", combined], input=joined, text=True)
    return result.returncode == 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("workspace")
    ap.add_argument("base_ref")
    ap.add_argument("--head-ref", default="HEAD")
    args = ap.parse_args(argv)

    files = changed_files(args.workspace, args.base_ref, args.head_ref)
    code_relevant = is_code_relevant(files)

    for f in files:
        print(f)
    print(
        "code_relevant={} ({} file(s) changed vs {})".format(
            "1" if code_relevant else "0", len(files), args.base_ref
        ),
        file=sys.stderr,
    )
    # Exit 0 ("pass", a PR should be opened) when code-relevant; exit 1
    # ("guard tripped", no PR) when docs/progress-only -- same nonzero-means-
    # tripped convention gitlink_guard.py already established.
    return 0 if code_relevant else 1


if __name__ == "__main__":
    sys.exit(main())
