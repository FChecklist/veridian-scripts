#!/usr/bin/env python3
"""gtm_check_regression_testing.py -- real, re-runnable check for GTM
certification category_index=7 ("regression testing").

Defined deterministically, per the dispatching task's own instruction: does
compliance-tracker's real `bun test` suite still pass against a FRESH
`git clone --depth 1 -b main` of origin/main (never the live working
checkout at /opt/veridian/repos/compliance-tracker, which routinely carries
unrelated uncommitted local changes from other in-flight work -- a fresh
clone is what actually proves origin/main itself is regression-clean).

What it does, every real run:
  1. `git clone --depth 1 -b main https://github.com/FChecklist/compliance-tracker.git`
     into a fresh temp directory (removed at the end of the run either way).
  2. Records the real commit SHA that clone landed on
     (`git rev-parse HEAD` inside the clone).
  3. `bun install` in that fresh clone (real dependency resolution, not
     reused from the live checkout's node_modules).
  4. `bun test` in that fresh clone -- bun's own test runner auto-discovers
     every `*.test.ts` file in the tree, no separate `test` package.json
     script exists or is needed.
  5. Parses bun's own real summary line ("N pass\\nM fail\\n...") from
     stdout/stderr -- never a narrative description of "the tests passed".

Pass bar (documented, fixed, not adjustable at call time):
  PASS <=> real `bun test` exit code 0 AND fail count == 0 AND pass count > 0
           (zero collected tests is treated as blocked, not a vacuous pass).
  Any real run with fail count > 0 is a genuine FAIL. "blocked" is reserved
  for: git/bun confirmed absent, the clone or install step itself failing
  (network/registry unavailable), or bun's own summary line failing to
  parse.

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=7's result.

Usage:
  gtm_check_regression_testing.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WRITER = os.path.join(SCRIPTS_DIR, "gtm_write_category_result.py")
CATEGORY_INDEX = 7
REPO_URL = "https://github.com/FChecklist/compliance-tracker.git"

SUMMARY_RE = re.compile(r"^\s*(\d+)\s+pass\s*$", re.MULTILINE)
FAIL_RE = re.compile(r"^\s*(\d+)\s+fail\s*$", re.MULTILINE)
FILES_RE = re.compile(r"Ran (\d+) tests across (\d+) files\.\s*\[([\d.]+)s\]")


def call_writer(result, evidence_summary, evidence):
    cmd = [
        sys.executable, WRITER,
        "--category-index", str(CATEGORY_INDEX),
        "--result", result,
        "--script-path", "gtm_check_regression_testing.py",
        "--evidence-summary", evidence_summary,
        "--evidence-json", json.dumps(evidence),
    ]
    print("Calling writer:", " ".join(cmd), file=sys.stderr)
    p = subprocess.run(cmd, capture_output=True, text=True)
    print(p.stdout)
    if p.returncode != 0:
        print(p.stderr, file=sys.stderr)
        sys.exit(p.returncode)


def main():
    git = shutil.which("git")
    bun = shutil.which("bun")
    if not git:
        call_writer("blocked", "git confirmed absent from PATH; cannot fresh-clone origin/main.", {"missing_tools": ["git"]})
        return
    if not bun:
        call_writer("blocked", "bun confirmed absent from PATH; cannot run the real test suite.", {"missing_tools": ["bun"]})
        return

    tmpdir = tempfile.mkdtemp(prefix="gtm_regression_")
    clone_dir = os.path.join(tmpdir, "compliance-tracker")
    try:
        p = subprocess.run(
            [git, "clone", "--depth", "1", "-b", "main", REPO_URL, clone_dir],
            capture_output=True, text=True, timeout=120,
        )
        if p.returncode != 0:
            call_writer(
                "blocked",
                f"fresh `git clone --depth 1 -b main {REPO_URL}` failed (exit {p.returncode}).",
                {"stdout": p.stdout[-2000:], "stderr": p.stderr[-2000:]},
            )
            return

        rev = subprocess.run([git, "rev-parse", "HEAD"], cwd=clone_dir, capture_output=True, text=True, timeout=20)
        commit_sha = rev.stdout.strip()

        install = subprocess.run(
            [bun, "install", "--silent"], cwd=clone_dir, capture_output=True, text=True, timeout=280,
        )
        if install.returncode != 0:
            call_writer(
                "blocked",
                f"`bun install` in the fresh clone (commit {commit_sha}) failed (exit {install.returncode}).",
                {"commit_sha": commit_sha, "stdout": install.stdout[-3000:], "stderr": install.stderr[-3000:]},
            )
            return

        test = subprocess.run(
            [bun, "test"], cwd=clone_dir, capture_output=True, text=True, timeout=550,
        )
        combined = (test.stdout or "") + "\n" + (test.stderr or "")

        pass_m = SUMMARY_RE.search(combined)
        fail_m = FAIL_RE.search(combined)
        files_m = FILES_RE.search(combined)

        if pass_m is None or fail_m is None:
            call_writer(
                "blocked",
                f"`bun test` (exit {test.returncode}) in the fresh clone (commit {commit_sha}) produced no parseable pass/fail summary line.",
                {
                    "commit_sha": commit_sha,
                    "exit_code": test.returncode,
                    "stdout_tail": (test.stdout or "")[-3000:],
                    "stderr_tail": (test.stderr or "")[-3000:],
                },
            )
            return

        pass_count = int(pass_m.group(1))
        fail_count = int(fail_m.group(1))
        total_tests = int(files_m.group(1)) if files_m else None
        total_files = int(files_m.group(2)) if files_m else None
        duration_s = float(files_m.group(3)) if files_m else None

        if pass_count == 0 and fail_count == 0:
            call_writer(
                "blocked",
                f"`bun test` in the fresh clone (commit {commit_sha}) collected zero real tests.",
                {"commit_sha": commit_sha, "exit_code": test.returncode},
            )
            return

        result = "fail" if (fail_count > 0 or test.returncode != 0) else "pass"
        evidence = {
            "repo_url": REPO_URL,
            "commit_sha": commit_sha,
            "clone_method": "git clone --depth 1 -b main (fresh temp dir, not the live working checkout)",
            "bun_install_exit_code": install.returncode,
            "bun_test_exit_code": test.returncode,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "total_tests": total_tests,
            "total_files": total_files,
            "duration_seconds": duration_s,
            "pass_criterion": "bun test exit code 0 AND fail_count == 0 AND pass_count > 0, against a fresh clone of origin/main",
        }
        summary = (
            f"Fresh clone of compliance-tracker origin/main (commit {commit_sha[:12]}): "
            f"`bun test` -> {pass_count} pass, {fail_count} fail"
            + (f", {total_tests} tests across {total_files} files in {duration_s}s" if total_tests else "")
            + f" (exit {test.returncode})."
        )
        call_writer(result, summary, evidence)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
