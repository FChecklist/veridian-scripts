#!/usr/bin/env python3
"""gtm_check_regression_testing.py -- real, re-runnable check for GTM
certification category_index=7 ("regression testing").

Built under UMR-20260806-122546-78d6 (TEST_SCRIPT_BUILD): category 7's
evidence_json already recorded a real result (fresh clone of
compliance-tracker origin/main, `bun test` -> 2512 pass / 0 fail / 223
files) but cited a script_path, gtm_check_regression_testing.py, confirmed
genuinely absent from disk. This script reproduces that exact, real
methodology as a genuine, committed, re-runnable file.

What it does, every time it runs:
  1. `git clone --depth 1 -b main <repo_url> <fresh temp dir>` (never the
     live working checkout -- regression testing must run against a clean,
     untouched clone of the real current origin/main).
  2. `bun install` in that fresh clone.
  3. `bun test` in that fresh clone, parsing bun's own real summary line
     (pass/fail/total counts).
  4. Deletes the temp clone when done, success or failure.

Pass criterion (documented, fixed, not adjustable at call time):
  PASS <=> real `bun test` exit code 0 AND fail_count == 0 AND
           pass_count > 0.
  Any real test failure is a genuine FAIL. "blocked" is reserved for: git
  or bun confirmed absent, the clone itself failing (network/auth), or bun
  test's own summary output not being parseable.

Every real run ends by calling the shared writer gtm_write_category_result.py
(never raw SQL) to record category_index=7's result.

Usage:
  gtm_check_regression_testing.py [--no-write]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
WRITER = os.path.join(SCRIPTS_DIR, "gtm_write_category_result.py")
CATEGORY_INDEX = 7
REPO_URL = "https://github.com/FChecklist/compliance-tracker.git"

# bun's real test-run summary looks like:
#   2512 pass
#   0 fail
#   ... N expect() calls
# Ran 2512 tests across 223 files. [15.20s]
SUMMARY_RE = re.compile(r"^\s*(\d+)\s+pass\s*$", re.MULTILINE)
FAIL_RE = re.compile(r"^\s*(\d+)\s+fail\s*$", re.MULTILINE)
RAN_RE = re.compile(r"Ran (\d+) tests? across (\d+) files?\.\s*\[([\d.]+)m?s\]")


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


def emit(args, result, summary, evidence):
    if args.no_write:
        print(json.dumps({"result": result, "summary": summary, "evidence": evidence}, indent=2))
        return
    call_writer(result, summary, evidence)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-url", default=REPO_URL)
    ap.add_argument("--no-write", action="store_true", help="evaluate only, print JSON result, never call the writer")
    args = ap.parse_args()

    git = shutil.which("git")
    bun = shutil.which("bun")
    if not git or not bun:
        emit(
            args, "blocked",
            f"Required tool(s) confirmed absent: {'git ' if not git else ''}{'bun' if not bun else ''}".strip(),
            {"git_present": bool(git), "bun_present": bool(bun)},
        )
        return

    tmpdir = tempfile.mkdtemp(prefix="gtm-regression-clone-")
    clone_dir = os.path.join(tmpdir, "compliance-tracker")
    try:
        rc_clone, out_clone, err_clone = _sh([git, "clone", "--depth", "1", "-b", "main", args.repo_url, clone_dir], timeout=180)
        if rc_clone != 0:
            emit(
                args, "blocked",
                f"git clone --depth 1 -b main {args.repo_url} failed (exit {rc_clone}).",
                {"repo_url": args.repo_url, "clone_exit_code": rc_clone, "clone_stderr_tail": (err_clone or "")[-2000:]},
            )
            return

        rc_sha, out_sha, _ = _sh([git, "rev-parse", "HEAD"], cwd=clone_dir, timeout=20)
        commit_sha = out_sha.strip() if rc_sha == 0 else None

        rc_install, out_install, err_install = _sh([bun, "install"], cwd=clone_dir, timeout=300)
        if rc_install != 0:
            emit(
                args, "blocked",
                f"bun install failed in fresh clone (exit {rc_install}).",
                {"repo_url": args.repo_url, "commit_sha": commit_sha, "bun_install_exit_code": rc_install, "bun_install_stderr_tail": (err_install or "")[-2000:]},
            )
            return

        t0 = time.time()
        rc_test, out_test, err_test = _sh([bun, "test"], cwd=clone_dir, timeout=600)
        duration = round(time.time() - t0, 1)

        combined = (out_test or "") + "\n" + (err_test or "")
        m_pass = SUMMARY_RE.search(combined)
        m_fail = FAIL_RE.search(combined)
        m_ran = RAN_RE.search(combined)

        if not m_pass or not m_fail:
            emit(
                args, "blocked",
                f"bun test ran (exit {rc_test}) but its real pass/fail summary lines were not parseable from output.",
                {"repo_url": args.repo_url, "commit_sha": commit_sha, "bun_test_exit_code": rc_test, "stdout_tail": (out_test or "")[-3000:], "stderr_tail": (err_test or "")[-3000:]},
            )
            return

        pass_count = int(m_pass.group(1))
        fail_count = int(m_fail.group(1))
        total_tests = int(m_ran.group(1)) if m_ran else pass_count + fail_count
        total_files = int(m_ran.group(2)) if m_ran else None

        result = "pass" if (rc_test == 0 and fail_count == 0 and pass_count > 0) else "fail"

        evidence = {
            "repo_url": args.repo_url,
            "commit_sha": commit_sha,
            "clone_method": "git clone --depth 1 -b main (fresh temp dir, not the live working checkout)",
            "bun_install_exit_code": rc_install,
            "bun_test_exit_code": rc_test,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "total_tests": total_tests,
            "total_files": total_files,
            "duration_seconds": duration,
            "pass_criterion": "bun test exit code 0 AND fail_count == 0 AND pass_count > 0, against a fresh clone of origin/main",
        }
        summary = (
            f"Fresh clone of compliance-tracker origin/main (commit {commit_sha[:12] if commit_sha else '?'}): "
            f"`bun test` -> {pass_count} pass, {fail_count} fail, {total_tests} tests "
            f"across {total_files if total_files is not None else '?'} files in {duration}s (exit {rc_test})."
        )
        emit(args, result, summary, evidence)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _sh(cmd, cwd=None, timeout=300):
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except FileNotFoundError as e:
        return None, "", f"command not found: {e}"
    except subprocess.TimeoutExpired as e:
        return None, e.stdout or "", f"timed out after {timeout}s"


if __name__ == "__main__":
    main()
