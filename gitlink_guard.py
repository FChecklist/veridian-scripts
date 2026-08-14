#!/usr/bin/env python3
"""Guard against silently shipping a bare git submodule gitlink (mode
160000) that isn't a genuine, pre-existing, intentional submodule of the
target repo (UMR-20260813-235552-dc9a).

REAL INCIDENT this closes: workers dispatched with a workspace checked out
from the WRONG repo (e.g. `claude-control` for a task whose real target was
`veridian-scripts`) improvised a nested `git clone` of the correct repo
inside their own workspace (observed directory names `veridian-scripts-work`,
`veridian-scripts-clean`) to do the real fix for real, and in some cases even
pushed a real PR against the correct repo from inside that nested checkout.
But worker-entrypoint.sh's own safety-net `git add -A && git commit && git
push` checkpoint commits (run unconditionally at multiple points, best-effort
so a hard-stop path never loses partial work) then picked up that nested
`.git` directory as a bare gitlink and pushed it to the WRONG repo's branch.
supervisor-entrypoint.sh's `gh pr create` had no check for this, so it opened
a real PR containing nothing but that one gitlink entry (diff stat: 1 file
changed, 1 insertion(+)) -- looks like delivered work, contains none.
Confirmed real and reproduced exactly: FChecklist/claude-control PRs #146,
#170, #191.

This module is the deterministic check used at two choke points:
  1. worker-entrypoint.sh, right after every `git add -A` and before the
     commit -- the earliest possible point, so the poisonous entry never
     even gets committed (see `find_illegitimate_gitlinks_staged`).
  2. supervisor-entrypoint.sh, right before `gh pr create` -- a last-resort
     net that catches anything that reached a pushed branch some other way,
     e.g. a worker's own manual `git push` from inside its agentic session
     (see `find_illegitimate_gitlinks_in_range`).
"""
import argparse
import subprocess
import sys


def _run(args, cwd):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)


def _gitmodules_paths(workspace, ref):
    """Paths declared in .gitmodules at `ref`, or empty set if none exists."""
    proc = _run(["git", "show", f"{ref}:.gitmodules"], workspace)
    if proc.returncode != 0:
        return set()
    paths = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("path = "):
            paths.add(line[len("path = "):].strip())
    return paths


def _is_preexisting_submodule(workspace, ref, path):
    """True only if `path` is already a real mode-160000 gitlink in `ref`'s
    own tree AND is declared in `ref`'s own .gitmodules -- i.e. genuinely a
    submodule this repo already had before the change under review, not one
    newly introduced by it."""
    if path not in _gitmodules_paths(workspace, ref):
        return False
    proc = _run(["git", "ls-tree", ref, "--", path], workspace)
    if proc.returncode != 0:
        return False
    for line in proc.stdout.splitlines():
        # ls-tree format: "<mode> <type> <object>\t<file>" -- mode is field 0.
        parts = line.split(None, 3)
        if len(parts) >= 1 and parts[0] == "160000":
            return True
    return False


def _scan_raw_diff(raw_output, workspace, base_ref):
    violations = []
    for line in raw_output.splitlines():
        if not line.startswith(":"):
            continue
        # ":<old_mode> <new_mode> <old_sha> <new_sha> <status>\t<path>"
        try:
            meta, path = line.split("\t", 1)
        except ValueError:
            continue
        fields = meta[1:].split()
        if len(fields) < 5:
            continue
        old_mode, new_mode, status = fields[0], fields[1], fields[4]
        path = path.split("\t")[0]
        if new_mode != "160000":
            continue
        if status.startswith("D"):
            continue  # a gitlink being *removed* is never a fake-PR risk
        if old_mode == "160000" and _is_preexisting_submodule(workspace, base_ref, path):
            continue  # genuinely pre-existing declared submodule, just moved commits
        violations.append(path)
    return violations


def find_illegitimate_gitlinks_staged(workspace, base_ref="HEAD"):
    """Newly-staged gitlinks (mode 160000) in the index right now, relative
    to `base_ref`, that are not a pre-existing declared submodule. Meant to
    run right after `git add -A`, before `git commit`."""
    proc = _run(["git", "diff", "--cached", "--raw", "--no-renames", base_ref], workspace)
    return _scan_raw_diff(proc.stdout, workspace, base_ref)


def find_illegitimate_gitlinks_in_range(workspace, base_ref, head_ref="HEAD"):
    """Gitlinks (mode 160000) introduced anywhere across base_ref...head_ref
    that are not a pre-existing declared submodule at `base_ref`. Meant to
    run right before `gh pr create`."""
    proc = _run(
        ["git", "diff", "--raw", "--no-renames", f"{base_ref}...{head_ref}"], workspace
    )
    return _scan_raw_diff(proc.stdout, workspace, base_ref)


GUARD_EXPLANATION = (
    "GITLINK GUARD: refusing to proceed -- '{path}' is a bare git submodule "
    "gitlink (mode 160000) that is not a genuine, pre-existing, declared "
    "submodule of this repo. This is the exact failure mode from "
    "UMR-20260813-235552-dc9a: a nested checkout of a DIFFERENT repo got "
    "swept into `git add -A` and would ship as an empty PR containing only "
    "this gitlink. Fix: never nest a checkout of another repo inside this "
    "workspace -- if this task's real target is a different repo, its "
    "dispatch `repo:` field is wrong; fix the dispatch, don't work around it "
    "here."
)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("workspace")
    ap.add_argument("base_ref")
    ap.add_argument("--head-ref", default="HEAD")
    ap.add_argument(
        "--staged",
        action="store_true",
        help="check the current index (post `git add`, pre-commit) instead of a committed range",
    )
    args = ap.parse_args(argv)

    if args.staged:
        violations = find_illegitimate_gitlinks_staged(args.workspace, args.base_ref)
    else:
        violations = find_illegitimate_gitlinks_in_range(
            args.workspace, args.base_ref, args.head_ref
        )

    for path in violations:
        print(GUARD_EXPLANATION.format(path=path), file=sys.stderr)
        print(path)

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
