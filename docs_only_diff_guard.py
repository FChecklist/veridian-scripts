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

Used as a guard at TWO real choke points: supervisor-entrypoint.sh, right
before the (paid) Superboss AI review call and `gh pr create` -- same
cost-avoidance placement as the pre-existing NO-OP-BRANCH-GUARD-BLOCK and
GITLINK-GUARD-BLOCK immediately above it in that file -- and
dispatch-owner-task.sh's own separate claude_code_cli_headless `gh pr
create` call, a distinct direct-execution path the supervisor never sees.

EXIT CODE CONTRACT (fixed 2026-08-17 after a real audit finding on PR #444,
head 499d1266: a crashed guard was indistinguishable from a genuine
docs-only trip, both exiting 1, which both callers treated identically --
silently refusing a real PR, or in supervisor-entrypoint.sh's case,
actively CLOSING a pre-existing real PR, on nothing more than a broken
`git diff` or a moved quality-gate.sh regex):
    0 -- code-relevant: at least one changed file is a real source/test/
         config/schema change. Callers should proceed to `gh pr create`.
    1 -- docs-only: guard TRIPPED as intended, every changed file matches
         the docs-only allowlist (or there are zero changed files).
         Callers should skip `gh pr create`.
    2 -- GUARD ERROR: the guard itself could not determine an answer (the
         underlying `git diff` failed, quality-gate.sh's allowlist regexes
         have moved/changed shape, or any other unexpected exception).
         This is NOT a docs-only signal. Callers MUST NOT treat this the
         same as exit 1 -- they must fail open (proceed as if code-relevant,
         never close a pre-existing PR) and log loudly, so a broken guard
         degrades to the pre-guard unconditional-PR behavior instead of
         silently swallowing real work.
"""
import argparse
import os
import re
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
QUALITY_GATE = os.path.join(SCRIPTS_DIR, "quality-gate.sh")

EXIT_CODE_RELEVANT = 0
EXIT_DOCS_ONLY = 1
EXIT_GUARD_ERROR = 2


class GuardError(Exception):
    """Raised when the guard itself cannot determine an answer -- distinct
    from a real, intentional docs-only trip. Callers must map this to
    EXIT_GUARD_ERROR (2), never to EXIT_DOCS_ONLY (1)."""


def _docs_only_patterns(quality_gate_path=QUALITY_GATE):
    """Pulls the two exact, live docs-only allowlist regexes out of
    quality-gate.sh itself, in source order. Raises GuardError loudly
    (never guesses a fallback pattern) if that script's own detection
    logic has moved or changed shape -- silently falling back to a stale
    copy here would be exactly the kind of drift Search-Reuse Discipline
    exists to prevent, and silently treating it as "docs-only" would be
    the exact crash-vs-trip conflation the 2026-08-17 audit found."""
    try:
        with open(quality_gate_path) as f:
            src = f.read()
    except OSError as exc:
        raise GuardError(f"could not read {quality_gate_path}: {exc}") from exc
    ext = re.search(r"DOCS_ONLY_EXT_PATTERN='((?:[^'\\]|\\.)*)'", src)
    name = re.search(r"DOCS_ONLY_NAME_PATTERN='((?:[^'\\]|\\.)*)'", src)
    if not (ext and name):
        raise GuardError(
            f"expected DOCS_ONLY_EXT_PATTERN/DOCS_ONLY_NAME_PATTERN single-quoted "
            f"assignments in {quality_gate_path} -- has the detection logic moved "
            f"or changed shape? Refusing to guess a stale fallback."
        )
    return ext.group(1), name.group(1)


def changed_files(workspace, base_ref, head_ref="HEAD"):
    """Real changed-file list for base_ref...head_ref (three-dot: files
    changed on head_ref since it diverged from base_ref), matching the
    exact same diff range every other guard in this codebase (gitlink_guard,
    the Superboss review's own DIFF_STAT) already uses.

    Raises GuardError (never silently returns []) if the underlying `git
    diff` itself fails -- e.g. an unknown base_ref/head_ref, or workspace
    not a git repo -- so a broken diff is never misread as "zero files
    changed" i.e. a false docs-only trip."""
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...{head_ref}"],
        cwd=workspace, capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise GuardError(
            f"git diff --name-only {base_ref}...{head_ref} (cwd={workspace}) "
            f"failed with exit {proc.returncode}: {proc.stderr.strip()}"
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

    try:
        files = changed_files(args.workspace, args.base_ref, args.head_ref)
        code_relevant = is_code_relevant(files)
    except GuardError as exc:
        print(f"docs_only_diff_guard: GUARD ERROR (not a docs-only trip): {exc}", file=sys.stderr)
        return EXIT_GUARD_ERROR
    except Exception as exc:  # pragma: no cover - defense in depth, see audit finding
        print(f"docs_only_diff_guard: unexpected GUARD ERROR (not a docs-only trip): "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_GUARD_ERROR

    for f in files:
        print(f)
    print(
        "code_relevant={} ({} file(s) changed vs {})".format(
            "1" if code_relevant else "0", len(files), args.base_ref
        ),
        file=sys.stderr,
    )
    # Exit 0 ("pass", a PR should be opened) when code-relevant; exit 1
    # ("guard tripped", no PR) when docs/progress-only; exit 2 (see
    # GuardError above) when the guard itself could not determine an
    # answer -- callers must treat 1 and 2 differently, never both as
    # "nonzero means tripped".
    return EXIT_CODE_RELEVANT if code_relevant else EXIT_DOCS_ONLY


if __name__ == "__main__":
    sys.exit(main())
